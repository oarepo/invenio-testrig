# -*- coding: utf-8 -*-
#
# Copyright (C) 2026 CESNET z.s.p.o.
#
# invenio-testrig is free software; you can redistribute it and/or
# modify it under the terms of the MIT License; see LICENSE file for more
# details.
"""Configuration initialization: YAML loading and config resolution."""

from datetime import UTC, datetime
from importlib import resources as importlib_resources
from pathlib import Path
from typing import cast

import marshmallow as ma
import yaml

from invenio_testrig.config import Config, ConfigSchema
from invenio_testrig.github import GitApi, GitCache, GitReferenceSchema
from invenio_testrig.hooks import run_hook
from invenio_testrig.types import Progress


def _load_yaml_config_data(config_yaml_path_or_url: str | None) -> dict:
    """Load raw YAML config data from a file path, URL, data URL, or built-in default.

    Args:
        config_yaml_path_or_url: One of:
            - None: Use built-in default config
            - File path: Load from local file
            - HTTP(S) URL: Fetch from web
            - Data URL: Inline YAML data (base64 or URL-encoded, optionally xz-compressed)

    Returns:
        Parsed YAML data as a dictionary

    Examples:
        # Data URL with base64 encoding
        data:text/yaml;base64,Z2l0aHViOgogIC0gcGFja2FnZTogdGVzdA==

        # Data URL with URL encoding
        data:text/yaml,github%3A%0A%20%20-%20package%3A%20test

        # Data URL with xz compression (base64 encoded)
        data:application/x-xz;base64,<base64-encoded-xz-compressed-yaml>

    Raises:
        ValueError: If data URL format is invalid
    """
    if config_yaml_path_or_url is None:
        return yaml.safe_load(
            importlib_resources.read_text("invenio_testrig", "default_config.yaml")
        )
    if config_yaml_path_or_url.startswith("data:"):
        import base64
        import lzma
        import urllib.parse

        # Parse data URL: data:[<mediatype>][;base64],<data>
        # Remove 'data:' prefix
        data_part = config_yaml_path_or_url[5:]

        # Split at comma to separate metadata from data
        if "," not in data_part:
            raise ValueError("Invalid data URL format: missing comma separator")

        metadata, encoded_data = data_part.split(",", 1)

        # Decode the data
        if ";base64" in metadata:
            decoded_bytes = base64.b64decode(encoded_data)
        else:
            # URL-encoded data
            decoded_bytes = urllib.parse.unquote(encoded_data).encode("utf-8")

        # Check if data is xz-compressed
        if "application/x-xz" in metadata:
            # Decompress xz data
            decoded_data = lzma.decompress(decoded_bytes).decode("utf-8")
        else:
            # Not compressed, just decode bytes to string
            if isinstance(decoded_bytes, bytes):
                decoded_data = decoded_bytes.decode("utf-8")
            else:
                decoded_data = decoded_bytes

        return yaml.safe_load(decoded_data)
    if config_yaml_path_or_url.startswith(
        "http://"
    ) or config_yaml_path_or_url.startswith("https://"):
        import httpx

        response = httpx.get(config_yaml_path_or_url)
        response.raise_for_status()
        return yaml.safe_load(response.text)
    with open(config_yaml_path_or_url, "r") as f:
        return yaml.safe_load(f)


def initialize_config(
    config_yaml_path_or_url: str | None,
    workdir: Path,
    repository_git: str | None,
    repository_e2e_git: str | None,
    progress: Progress,
) -> Config:
    """Initialize workflow by preparing configuration.

    Resolves all git references in the YAML configuration and outputs JSON.

    :param config_yaml_path_or_url: Path or URL to the input YAML configuration file
    :param workdir: Path to the working directory
    :param repository_git: Override for ``seed_repository.git``
    :param repository_e2e_git: Override for ``seed_repository.e2e``
    :param progress: Progress reporter for status updates

    :return: Initialized :class:`~invenio_testrig.config.Config` instance with all
        git references resolved and ``started_at`` timestamp set
    """
    # Read the yaml config file
    schema = GitReferenceSchema()
    config_data = _load_yaml_config_data(config_yaml_path_or_url)
    extra_env = config_data.get("env", {})
    git_api = GitApi(
        GitCache(workdir / "git_cache", extra_env=extra_env),
        extra_env=extra_env,
    )

    # resolve all references before loading
    config_data["patches"] = [
        schema.dump(git_api.parse_patch(x)) for x in (config_data.get("patches") or [])
    ]
    seed_repository = config_data.get("seed_repository", {})

    if repository_git:
        seed_repository["git"] = repository_git
    if repository_e2e_git:
        seed_repository["e2e"] = repository_e2e_git

    if "git" in seed_repository and seed_repository["git"]:
        seed_repository["git"] = schema.dump(
            git_api.parse_reference(seed_repository["git"])
        )
    if "e2e" in seed_repository and seed_repository["e2e"]:
        seed_repository["e2e"] = schema.dump(
            git_api.parse_reference(seed_repository["e2e"])
        )
    config_data["hooks"] = config_data.get("hooks", {}) or {}

    config = cast(Config, ConfigSchema().load(config_data, unknown=ma.INCLUDE))

    config.started_at = datetime.now(UTC).isoformat()

    # Run after-config-preprocessing hook if it exists
    run_hook(
        config,
        "after_config_preprocessing",
    )
    progress.success("Configuration prepared")

    return config
