# -*- coding: utf-8 -*-
#
# Copyright (C) 2026 CESNET z.s.p.o.
#
# invenio-testrig is free software; you can redistribute it and/or
# modify it under the terms of the MIT License; see LICENSE file for more
# details.
"""Configuration schema definitions and management.

This module defines the configuration schema for invenio-testrig split into
two distinct layers:

* :class:`UserConfig` — everything the user authors: repository settings,
  GitHub organization patterns, patch configurations, and execution options.
  Serialized to ``workdir/config.json``.

* :class:`RuntimeState` — data accumulated by the setup sub-steps:
  resolved dependency versions and tested-package metadata.
  Serialized to ``workdir/runtime_config.json``.

* :class:`Config` — a thin aggregate that holds both and exposes all fields
  as properties so every existing ``config.X`` call site continues to work
  without change.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Self, cast

import marshmallow as ma
from marshmallow_dataclass import class_schema

from invenio_testrig.utils import ExtensibleMixin

from .types import GitReference, Patch


def _normalize_list(lst: list | None) -> list[str]:
    """Return a lowercased copy of lst, or an empty list if lst is None."""
    return [item.lower() for item in (lst or [])]


@dataclass(init=False)
class Repository(ExtensibleMixin):
    """Repository configuration.

    Additional information can be added here for hooks, thus ExtensibleMixin is used.
    """

    git: GitReference
    """Git reference to the seed repository/repository to test."""

    e2e: GitReference | None
    """Git reference to the E2E package that will be used for testing."""

    install: list[str] | None
    """Installation command to run instead of `pip install` for the initial installation of the repository."""

    test: list[str] | None
    """Test command to run on the installed repository."""

    def as_tested_package_info(self) -> "TestedPackageInfo":
        """Create a TestedPackageInfo that represents this seed repository.

        Several subsystems (test runners, report generators) need to record the
        seed repository as if it were a tested package so they can write status
        files and render it uniformly alongside regular packages. This factory
        centralises that construction so the field mapping is defined once.
        """
        return TestedPackageInfo(
            reference=self.git,
            github_entry=Github(
                org=self.git.org,
                install=self.install or [],
                test=self.test or [],
            ),
        )


@dataclass(init=False)
class Github(ExtensibleMixin):
    """GitHub organization configuration.

    Additional information can be added here for hooks, thus ExtensibleMixin is used.
    """

    org: str
    """Organization name on GitHub to match against when resolving repositories."""

    test: list[str]
    """The test command to run to tested packages that match include directive.

    It is a single command, arguments should be passed as a list of strings.
    """

    install: list[str] = field(default_factory=list)
    """Installation command to run instead of `pip install` for the initial installation of the repository.

    It is a single command, arguments should be passed as a list of strings.
    """

    include: list[str] = field(default_factory=list)
    """List of package regexes to include in the testing process. If empty, all packages are included."""

    exclude: list[str] = field(default_factory=list)
    """List of package regexes to exclude from the testing process. If empty, no packages are excluded."""

    package_map: dict[str, str] = field(default_factory=dict)
    """Map of package names to github repositories.

    If package is not in the package map, it is supposed that the repository name of the package
    is the same as the package name.
    """

    extras: list[str] = field(default_factory=list)
    """List of extras to install for tested packages that match include directive."""

    freeze: list[str] = field(default_factory=list)
    """List of version constraints to apply when resolving dependencies for tested packages that match include directive.

    If specified, these packages will be reinstalled with the specified version constraints before running the tests.
    """

    slow_packages: dict[str, list[str]] = field(default_factory=dict)
    """List of packages that are slow to test.

    Testrig will try to run these packages first and in parallel with the rest of the packages,
    so that we can get results faster.
    """

    default_branch: str | None = None
    """Default branch to use for the repositories inside the organization. If not set,
    defaults to the default branch of the repository.

    For upstream mode, this is the branch that will be used as the base branch for testing."""

    def __post_init__(self):
        """Normalize package lists to lowercase for case-insensitive matching.

        Ensures all package names in include, exclude, and freeze lists are
        lowercase to enable consistent case-insensitive package matching.
        """
        self.include = _normalize_list(self.include)
        self.exclude = _normalize_list(self.exclude)
        self.freeze = _normalize_list(self.freeze)
        self.test = list(self.test)
        self.install = list(self.install or [])
        self.extras = list(self.extras or [])
        self.package_map = self.package_map or {}


@dataclass
class TestedPackageInfo:
    """Information about a package that is being tested, derived from the github configuration.

    This one is generated automatically based on the github configuration
    and the dependencies, so it is not extensible.
    """

    reference: GitReference
    github_entry: Github
    patches: list[Patch] = field(default_factory=list)
    unpatched_reference: GitReference | None = None
    patched_reference: GitReference | None = None

    @property
    def package(self) -> str:
        return self.reference.package


# ---------------------------------------------------------------------------
# User-authored configuration layer
# ---------------------------------------------------------------------------


@dataclass(init=False)
class UserConfig(ExtensibleMixin):
    """Everything the user authors — present in YAML and stable after setup begins.

    This class captures the full user intent: which repositories to test,
    which patches to apply, and how to run the tests.  It is written to
    ``workdir/config.json`` exactly once (after CLI overrides are applied) and
    never modified again during a run.
    """

    github: list[Github]
    """GitHub organization configurations defining which packages to test."""

    seed_repository: Repository
    """The InvenioRDM repository whose dependency graph is being tested."""

    patches: list[Patch] = field(default_factory=list)
    """Patches (PRs or branches) to apply during testing."""

    patch_mode: Literal["upstream", "pinned"] = "upstream"
    """Patching strategy: ``upstream`` (latest versions) or ``pinned`` (lock-file versions)."""

    name: str | None = None
    """Optional human-readable name for this test configuration run."""

    env: dict[str, str] = field(default_factory=dict)
    """Environment variables injected into every installation and test subprocess."""

    python_version: str = "python3"
    """Python interpreter to use when creating virtual environments."""

    uv_executable: str = "uv"
    """Path or name of the ``uv`` executable."""

    disable_codestyle_checks: bool = False
    """Remove ``--black``/``--isort``/``--pydocstyle`` flags from test configs."""

    verbose: bool = False
    """Enable verbose logging output."""

    debug: bool = False
    """Re-raise exceptions with full tracebacks instead of user-friendly messages."""

    test_mode: Literal["stop-on-success", "run-all"] = "stop-on-success"
    """Controls when package testing (patched and unpatched) stops.

    * ``stop-on-success``: skip the unpatched run if the patched version passes.
    * ``run-all``: always run both variants.
    """

    test_scope: Literal["affected", "all"] = "affected"
    """Controls which packages are tested.

    * ``affected``: only packages touched by patches or their dependents.
    * ``all``: every package in the dependency graph.
    """

    test_timeout: int = 90
    """Per-package test timeout in minutes. 0 means no timeout."""

    slow_test_splitting: bool = True
    """Split slow-test packages across multiple parallel matrix jobs."""


UserConfigSchema = class_schema(UserConfig)


# ---------------------------------------------------------------------------
# Runtime state layer
# ---------------------------------------------------------------------------


@dataclass
class RuntimeState:
    """Data accumulated by the setup sub-steps — never user-authored.

    Written to ``workdir/runtime_config.json`` and updated after each setup
    phase (collect → filter → select-patches → clone).
    """

    started_at: str | None = None
    """ISO-format UTC timestamp set when the setup run begins."""

    use_editable_installation: bool = True
    """Whether to install packages in editable mode (default: True)."""

    packages: dict[str, str] = field(default_factory=dict)
    """All dependencies of the seed repository mapped to their resolved versions.

    Populated by the ``collect`` step.
    """

    tested_packages: dict[str, TestedPackageInfo] = field(default_factory=dict)
    """Subset of ``packages`` that match the GitHub include/exclude filters.

    Populated by the ``filter`` step and enriched by ``select-patches`` and ``clone``.
    """


RuntimeStateSchema = class_schema(RuntimeState)


# ---------------------------------------------------------------------------
# Aggregate Config class
# ---------------------------------------------------------------------------


class Config:
    """Aggregate that combines :class:`UserConfig` and :class:`RuntimeState`.

    User configuration is accessed via ``config.user``;
    runtime state is accessed via ``config.runtime``.

    I/O is split into :meth:`save_user` (writes ``config.json``) and
    :meth:`save_runtime` (writes ``runtime_config.json``).  :meth:`save`
    writes both for convenience.
    """

    def __init__(
        self,
        user: UserConfig,
        runtime: RuntimeState | None = None,
        workdir: Path | None = None,
    ) -> None:
        self.user = user
        self.runtime = runtime if runtime is not None else RuntimeState()
        self._workdir = workdir

    # ------------------------------------------------------------------
    # Path resolution
    # ------------------------------------------------------------------

    @property
    def workdir(self) -> Path:
        """Absolute path to the working directory.

        :raises ValueError: If the workdir has not been set.
        """
        if self._workdir is None:
            raise ValueError("workdir is not set on this Config instance.")
        return self._workdir.resolve()

    @property
    def config_path(self) -> Path | None:
        """Path to ``config.json`` inside the working directory, or ``None``."""
        if self._workdir is None:
            return None
        return self._workdir / "config.json"

    @config_path.setter
    def config_path(self, value: Path) -> None:
        """Backwards-compat setter: derive workdir from a config.json path."""
        self._workdir = Path(value).parent

    def workdir_path(self, subdir: str | None = None) -> Path:
        """Return the workdir or a named subdirectory within it.

        :param subdir: Optional subdirectory name.
        """
        if subdir:
            return self.workdir / subdir
        return self.workdir

    # ------------------------------------------------------------------
    # I/O
    # ------------------------------------------------------------------

    @classmethod
    def load(cls: type[Self], workdir: Path) -> Self:
        """Load configuration from a working directory.

        Reads ``workdir/config.json`` (UserConfig) and, if present,
        ``workdir/runtime_config.json`` (RuntimeState).

        :param workdir: Path to the working directory produced by ``setup``.

        :return: Fully populated :class:`Config` instance.

        :raises FileNotFoundError: If ``config.json`` does not exist.
        :raises ValueError: If ``config.json`` is not a JSON object.
        """
        workdir = Path(workdir)
        user_path = workdir / "config.json"

        with open(user_path) as fh:
            raw = json.load(fh)

        if not isinstance(raw, dict):
            raise ValueError(
                f"Expected config.json to contain a JSON object, got {type(raw)}"
            )

        # Backwards compat: old config.json files contain runtime fields too.
        # Peel them off so they don't confuse UserConfigSchema.
        runtime_keys = {"started_at", "packages", "tested_packages"}
        runtime_data = {k: v for k, v in raw.items() if k in runtime_keys}
        user_data = {k: v for k, v in raw.items() if k not in runtime_keys}

        user = cast(UserConfig, UserConfigSchema().load(user_data, unknown=ma.INCLUDE))

        runtime_path = workdir / "runtime_config.json"
        if runtime_path.exists():
            with open(runtime_path) as fh:
                runtime = cast(RuntimeState, RuntimeStateSchema().load(json.load(fh)))
        elif runtime_data:
            # Old-style single-file workdir: runtime fields were in config.json.
            runtime = cast(RuntimeState, RuntimeStateSchema().load(runtime_data))
        else:
            runtime = RuntimeState()

        return cls(user=user, runtime=runtime, workdir=workdir)

    @classmethod
    def load_from_dict(cls: type[Self], data: dict) -> Self:
        """Create a Config from a flat dictionary (e.g., fetched from a report).

        Handles both the old combined format (all fields in one dict) and the
        new split format (user fields only).

        :param data: Raw dictionary containing config fields.

        :return: :class:`Config` instance with no workdir set.
        """
        runtime_keys = {"started_at", "packages", "tested_packages"}
        runtime_data = {k: v for k, v in data.items() if k in runtime_keys}
        user_data = {k: v for k, v in data.items() if k not in runtime_keys}

        user = cast(UserConfig, UserConfigSchema().load(user_data, unknown=ma.INCLUDE))
        runtime = cast(RuntimeState, RuntimeStateSchema().load(runtime_data))
        return cls(user=user, runtime=runtime)

    def save_user(self) -> None:
        """Write :class:`UserConfig` to ``workdir/config.json``.

        Call this once after all CLI overrides have been applied.  The file
        should not need to change again during a run.

        :raises AssertionError: If workdir is not set.
        """
        assert self._workdir is not None, "workdir must be set before saving"
        path = self._workdir / "config.json"
        path.write_text(
            json.dumps(UserConfigSchema().dump(self.user), indent=2, sort_keys=True)
        )

    def save_runtime(self) -> None:
        """Write :class:`RuntimeState` to ``workdir/runtime_config.json``.

        Call this after each setup sub-step that populates runtime state
        (collect, filter, select-patches, clone).

        :raises AssertionError: If workdir is not set.
        """
        assert self._workdir is not None, "workdir must be set before saving"
        path = self._workdir / "runtime_config.json"
        path.write_text(
            json.dumps(RuntimeStateSchema().dump(self.runtime), indent=2, sort_keys=True)
        )

    def save(self, file: str | Path | None = None) -> None:
        """Write both :class:`UserConfig` and :class:`RuntimeState` to disk.

        Accepts an optional *file* argument for backwards compatibility with
        callers that previously passed ``workdir / "config.json"`` explicitly.
        When given, the workdir is derived from the file's parent directory.

        :param file: Optional path ending in ``config.json``.  If omitted,
            uses the workdir already set on this instance.
        """
        if file is not None:
            self._workdir = Path(file).parent
        self.save_user()
        self.save_runtime()


# Deprecated alias — kept for any external hook libraries that import it.
# New code should use UserConfigSchema or RuntimeStateSchema directly.
ConfigSchema = UserConfigSchema
