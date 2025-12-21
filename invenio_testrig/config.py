# -*- coding: utf-8 -*-
#
# Copyright (C) 2026 CESNET z.s.p.o.
#
# invenio-testrig is free software; you can redistribute it and/or
# modify it under the terms of the MIT License; see LICENSE file for more
# details.
"""Configuration schema definitions and management.

This module defines the complete configuration schema for invenio-testrig,
including repository settings, GitHub organization patterns, patch configurations,
and execution status tracking. It also provides functions for loading and saving
configuration files.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Self, cast

import marshmallow as ma
from marshmallow_dataclass import class_schema

from invenio_testrig.github.types import GitReference, Patch
from invenio_testrig.types import ExecutionStatus, TestedPackageInfo
from invenio_testrig.utils import ExtensibleMixin


def _normalize_list(lst: list | None) -> list[str]:
    """Return a lowercased copy of lst, or an empty list if lst is None."""
    return [item.lower() for item in (lst or [])]


@dataclass(init=False)
class Repository(ExtensibleMixin):
    """Repository configuration.

    Additional information can be added here for hooks, thus ExtensibleMixin is used.
    """

    git: GitReference
    e2e: GitReference | None


@dataclass(init=False)
class Github(ExtensibleMixin):
    """GitHub organization configuration.

    Additional information can be added here for hooks, thus ExtensibleMixin is used.
    """

    org: str
    """Organization name on GitHub to match against when resolving repositories."""

    test: list[str]
    """The test command to run to tested packages that match include directive."""

    include: list[str] | None = field(default_factory=list)
    """List of package regexes to include in the testing process. If empty, all packages are included."""

    exclude: list[str] | None = field(default_factory=list)
    """List of package regexes to exclude from the testing process. If empty, no packages are excluded."""

    extras: list[str] | None = field(default_factory=list)
    """List of extras to install for tested packages that match include directive."""

    freeze: list[str] | None = field(default_factory=list)
    """List of version constraints to apply when resolving dependencies for tested packages that match include directive.
    
    If specified, these packages will be reinstalled with the specified version constraints before running the tests. 
    """

    def __post_init__(self):
        """Normalize package lists to lowercase for case-insensitive matching.

        Ensures all package names in include, exclude, and freeze lists are
        lowercase to enable consistent case-insensitive package matching.
        """
        self.include = _normalize_list(self.include)
        self.exclude = _normalize_list(self.exclude)
        self.freeze = _normalize_list(self.freeze)
        self.test = list(self.test)
        self.extras = list(self.extras or [])


@dataclass(init=False)
class Config(ExtensibleMixin):
    """Main configuration structure for invenio-testrig.

    This is kept extensible to allow custom fields for hooks.
    """

    github: list[Github]
    seed_repository: Repository
    name: str | None = None
    """Optional name for this test configuration run."""
    started_at: str | None = None
    """ISO datetime when the configuration was initialized."""
    patches: list[Patch] = field(default_factory=list)
    patch_mode: Literal["upstream", "pinned"] = "upstream"

    # runtime information - these are populated during execution and not
    # expected to be set by users, but kept here for simplicity and extensibility
    packages: dict[str, str] = field(
        default_factory=dict
    )  # package name to version mapping for dependencies
    tested_packages: dict[str, TestedPackageInfo] = field(
        default_factory=dict
    )  # package name to tested package info mapping

    # Execution options - again,  not setup in the config file but can be set programmatically or via hooks
    python_version: str = "python3"
    """Python version to use for testing."""

    uv_executable: str = "uv"
    """Path to uv executable."""

    disable_codestyle_checks: bool = False
    """Whether to disable codestyle checks (--black, --isort, --pydocstyle) in test configuration."""

    debug: bool = False
    """Whether to enable debug mode with full traceback on errors."""

    verbose: bool = False
    """Whether to enable verbose output with additional logging information."""

    test_mode: Literal["stop-on-success", "run-all"] = "stop-on-success"
    """This setting controls when package testing (patched and unpatched version) should stop.

    The options are:

    * ``stop-on-success``: Test the patched version first. If it fails, also test
      the unpatched version. If it succeeds, skip testing the unpatched version.
    * ``run-all``: Run tests for both the patched and unpatched version
      regardless of the test results.
    """

    test_scope: Literal["affected", "all"] = "affected"
    """This setting controls which packages should be tested.

    The options are:

    * ``affected``: Only test packages affected by the patches
      (i.e. packages that have patches or depend on packages with patches).
    * ``all``: Test all packages regardless of whether they are affected by the patches.
    """

    test_timeout: int = 90
    """Timeout for each test execution in minutes. 0 means no timeout."""

    @property
    def workdir(self) -> Path:
        """Get the working directory path.

        :return: Path to the working directory (parent of config file)

        :raises ValueError: If config_path is not set
        """
        # workdir is always the parent directory of the config file
        config_path = self.config_path
        if config_path is None:
            raise ValueError("Config path is not set. Cannot determine workdir.")
        return config_path.parent.resolve()

    def workdir_path(self, subdir: str | None = None) -> Path:
        """Get the Path object for the working directory or a subdirectory within it.

        :param subdir: Optional subdirectory name within the working directory

        :return: Path to the working directory or specified subdirectory
        """
        if subdir:
            return self.workdir / subdir
        return self.workdir

    @property
    def config_path(self) -> Path | None:
        """Path to the config JSON file, if loaded from a file."""
        return getattr(self, "_config_path", None)

    @config_path.setter
    def config_path(self, value: Path) -> None:
        """Set the path to the config JSON file.

        :param value: Path to the configuration file
        """
        self._config_path = value

    @classmethod
    def load(cls: type[Self], file: str | Path) -> Self:
        """Load configuration from a JSON file.

        :param file: Path to the JSON configuration file
        :type file: str or Path

        :return: Loaded :class:`Config` instance with :attr:`config_path` set

        :raises ValueError: If the file doesn't contain a valid JSON object
        :raises FileNotFoundError: If the file doesn't exist
        """
        path = Path(file)

        with open(path, "r") as stream:
            raw_data = json.load(stream)
            schema = ConfigSchema()

            if isinstance(raw_data, dict):
                ret = cast(Self, schema.load(raw_data, unknown=ma.INCLUDE))
                ret.config_path = path
                return ret
            else:
                raise ValueError(
                    f"Expected config file to contain a JSON object, got {type(raw_data)}"
                )

    def save(self, file: str | Path | None = None) -> None:
        """Save configuration to a JSON file.

        :param file: Optional path to save the configuration to. If None, uses config_path

        :raises ValueError: If no file path is specified and config_path is not set
        """
        if file:
            self.config_path = Path(file)
        elif not self.config_path:
            raise ValueError("No file path specified for saving configuration.")
        formatted_config = json.dumps(
            ConfigSchema().dump(self), indent=2, sort_keys=True
        )
        assert self.config_path is not None  # guaranteed by the check above
        self.config_path.write_text(formatted_config)


ConfigSchema = class_schema(Config)


ExecutionStatusSchema = class_schema(ExecutionStatus)


def load_execution_status(file: str | Path) -> ExecutionStatus:
    """Load execution status from JSON file.

    :param file: Path to the JSON execution status file

    :return: Loaded ExecutionStatus instance

    :raises ValueError: If the file doesn't contain a valid JSON object
    :raises FileNotFoundError: If the file doesn't exist
    """
    path = Path(file)
    with open(path, "r") as stream:
        raw_data = json.load(stream)
        schema = ExecutionStatusSchema()
        if isinstance(raw_data, dict):
            return cast(ExecutionStatus, schema.load(raw_data))
        else:
            raise ValueError(
                f"Expected execution status file to contain a JSON object, got {type(raw_data)}"
            )


def save_execution_status(file: str | Path, status: ExecutionStatus) -> None:
    """Save execution status to JSON file.

    :param file: Path where the execution status should be saved
    :param status: ExecutionStatus instance to save
    """
    path = Path(file)
    formatted_status = json.dumps(
        ExecutionStatusSchema().dump(status), indent=2, sort_keys=True
    )
    path.write_text(formatted_status)
