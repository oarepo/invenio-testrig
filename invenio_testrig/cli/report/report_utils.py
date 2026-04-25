# -*- coding: utf-8 -*-
#
# Copyright (C) 2026 CESNET z.s.p.o.
#
# invenio-testrig is free software; you can redistribute it and/or
# modify it under the terms of the MIT License; see LICENSE file for more
# details.
"""Shared report utilities: templates, context helpers, and serialization."""

from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from invenio_testrig.config import Config

# region Constants

PATCH_MODE_LABELS: dict[str, str] = {
    "upstream": "Upstream baseline (patches rebased on upstream, unpatched are upstream)",
    "pinned": "Pinned baseline (patches rebased on pinned version, unpatched are pinned version)",
}

TEST_MODE_LABELS: dict[str, str] = {
    "stop-on-success": "Test patched first, retry unpatched on failure",
    "run-all": "Test both patched and unpatched versions",
}

TEST_SCOPE_LABELS: dict[str, str] = {
    "affected": "Affected packages only",
    "all": "All packages",
}

# endregion


# region Template & Context Helpers


def format_started_at(started_at: str | None) -> str | None:
    """Format a started_at ISO timestamp to a human-readable string."""
    if not started_at:
        return None
    try:
        return datetime.fromisoformat(started_at).strftime("%Y-%m-%d %H:%M:%S UTC")
    except (ValueError, AttributeError):
        return started_at


def get_jinja_template(template_name: str):
    """Load a Jinja2 template from the bundled templates directory."""
    from jinja2 import Environment, FileSystemLoader

    env = Environment(
        loader=FileSystemLoader(searchpath=Path(__file__).parent / "templates")
    )
    return env.get_template(template_name)


def build_base_jinja_context(config: Config) -> dict[str, Any]:
    """Build common Jinja2 context fields shared across all report templates."""
    return {
        "config_name": config.user.name,
        "patch_mode": PATCH_MODE_LABELS.get(config.user.patch_mode, config.user.patch_mode),
        "test_mode": TEST_MODE_LABELS.get(config.user.test_mode, config.user.test_mode),
        "test_scope": TEST_SCOPE_LABELS.get(config.user.test_scope, config.user.test_scope),
        "started_at": format_started_at(config.runtime.started_at),
        "python_version": config.user.python_version,
        "last_updated": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC"),
    }


def to_serializable(obj: Any) -> Any:
    """Convert Python objects to JSON-serializable format.

    :param obj: Object to convert

    :return: JSON-serializable version of the object
    """
    if is_dataclass(obj):
        return {k: to_serializable(v) for k, v in asdict(obj).items()}

    elif isinstance(obj, datetime):
        return obj.isoformat()

    elif isinstance(obj, dict):
        return {to_serializable(k): to_serializable(v) for k, v in obj.items()}

    elif isinstance(obj, (list, tuple, set)):
        return [to_serializable(v) for v in obj]

    else:
        return obj


# endregion
