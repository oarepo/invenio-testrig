# -*- coding: utf-8 -*-
#
# Copyright (C) 2026 CESNET z.s.p.o.
#
# invenio-testrig is free software; you can redistribute it and/or
# modify it under the terms of the MIT License; see LICENSE file for more
# details.
"""Setup CLI command: orchestrates config init, dependency collection, filtering, patching, and cloning."""

from pathlib import Path
from typing import Literal

import click

from invenio_testrig.cli.base import set_verbose, step_error_handler, with_progress
from invenio_testrig.cli.setup.config import initialize_config
from invenio_testrig.cli.setup.dependencies import (
    collect_dependencies,
    filter_packages,
    select_patches,
)
from invenio_testrig.cli.setup.repository import clone_repositories
from invenio_testrig.github import GitApi, GitCache
from invenio_testrig.progress import Progress


@click.command("setup")
@with_progress
@click.option(
    "--config",
    "config_yaml_path_or_url",
    default=None,
    help="Path or URL to a YAML configuration file",
)
@click.option(
    "--workdir",
    type=click.Path(path_type=Path, resolve_path=True),
    default=Path("workdir"),
)
@click.option("--verbose", is_flag=True, help="Enable verbose output")
@click.option("--debug", is_flag=True, help="Enable debug mode with full traceback")
@click.option(
    "--python",
    "python_version",
    default="python3",
    help="Python version to use for testing",
)
@click.option(
    "--uv",
    "uv_executable",
    default="uv",
    help="Path to uv executable",
)
@click.option(
    "--disable-codestyle-checks",
    "disable_codestyle_checks",
    is_flag=True,
    help="Disable codestyle checks (black, isort, pydocstyle) in test configuration",
)
@click.option("--patch-mode", help="Patch mode to use for patching packages")
@click.option(
    "--test-scope",
    "test_scope",
    type=click.Choice(["affected", "all"]),
    default="affected",
    help="Test scope: 'affected' (only packages with patches), 'all' (all packages)",
)
@click.option(
    "--test-mode",
    "test_mode",
    type=click.Choice(["stop-on-success", "run-all"]),
    default="stop-on-success",
    help="Test mode: 'stop-on-success' (test original only on failure), 'run-all' (always test both versions)",
)
@click.option(
    "--repository",
    "repository_git",
    default=None,
    help="Override seed_repository.git configuration (e.g., 'org/repo@branch' or GitHub URL)",
)
@click.option(
    "--e2e",
    "repository_e2e",
    default=None,
    help="Override seed_repository.e2e configuration (e.g., 'org/repo@branch' or GitHub URL)",
)
@click.option(
    "--name",
    "name",
)
@click.option(
    "--ignore-uv-lock",
    "ignore_uv_lock",
    is_flag=True,
    help="Ignore uv.lock file during dependency collection",
)
@click.option(
    "--no-slow-test-splitting","invenio-requests>=15.0.0,<16.0.0",
    "no_slow_test_splitting",
    is_flag=True,
    help="Disable splitting of slow tests into multiple parts",
)
@click.option(
    "--editable/--installed",
    "use_editable_installation",
    default=True,
    help="Install packages in editable mode (--editable) or as built packages (--installed)",
)
@click.argument("patches", nargs=-1)
def setup_cmd(
    config_yaml_path_or_url: str | None,
    workdir: Path,
    python_version: str,
    uv_executable: str,
    disable_codestyle_checks: bool,
    test_scope: Literal["affected", "all"],
    test_mode: Literal["stop-on-success", "run-all"],
    repository_git: str | None,
    repository_e2e: str | None,
    name: str | None,
    debug: bool,
    verbose: bool,
    patch_mode: Literal["upstream", "pinned"] | None,
    patches: tuple[str, ...],
    ignore_uv_lock: bool,
    no_slow_test_splitting: bool,
    use_editable_installation: bool,
    progress: Progress,
):
    """1/ Complete local setup: init, collect, filter, select-patches, and clone.

    This command combines the following steps:
    1. Initialize configuration (init)
    2. Collect dependencies (collect)
    3. Filter packages (filter)
    4. Select patches (select-patches)
    5. Clone repositories (clone)

    This is a convenience command that runs all preparation steps before testing.

    Example: invenio-testrig setup --config config.yaml org/package#pr_number
    """

    if verbose:
        set_verbose()

    # Print command-line options summary for debugging
    progress.text("::group::🔍 Setup Command Options Summary")
    progress.text(f"  config_yaml_path_or_url: {config_yaml_path_or_url}")
    progress.text(f"  workdir: {workdir}")
    progress.text(f"  python_version: {python_version}")
    progress.text(f"  uv_executable: {uv_executable}")
    progress.text(f"  disable_codestyle_checks: {disable_codestyle_checks}")
    progress.text(f"  test_scope: {test_scope}")
    progress.text(f"  test_mode: {test_mode}")
    progress.text(f"  repository_git: {repository_git}")
    progress.text(f"  repository_e2e: {repository_e2e}")
    progress.text(f"  name: {name}")
    progress.text(f"  debug: {debug}")
    progress.text(f"  verbose: {verbose}")
    progress.text(f"  patch_mode: {patch_mode}")
    progress.text(f"  patches: {patches}")
    progress.text(f"  ignore_uv_lock: {ignore_uv_lock}")
    progress.text(f"  no_slow_test_splitting: {no_slow_test_splitting}")
    progress.text(
        f"  enable_slow_test_splitting (computed): {not no_slow_test_splitting}"
    )
    progress.text(f"  use_editable_installation: {use_editable_installation}")
    progress.text("::endgroup::")

    # Step 1: Initialize
    progress.start("Step 1/5: Initializing configuration", icon="🔧")
    Path(workdir).mkdir(parents=True, exist_ok=True)
    config = initialize_config(
        config_yaml_path_or_url,
        workdir,
        repository_git,
        repository_e2e,
        progress,
    )
    config.user.python_version = python_version
    config.user.uv_executable = uv_executable
    config.user.disable_codestyle_checks = disable_codestyle_checks
    config.user.test_scope = test_scope
    config.user.test_mode = test_mode
    config.user.verbose = verbose
    config.user.debug = debug
    config.user.slow_test_splitting = not no_slow_test_splitting
    config.runtime.use_editable_installation = use_editable_installation

    # Override seed_repository configurations if provided
    api = GitApi(
        GitCache(workdir / "git_cache", extra_env=config.user.env), extra_env=config.user.env
    )
    if name:
        config.user.name = name
    if patch_mode:
        config.user.patch_mode = patch_mode
    if patches:
        config.user.patches = [api.parse_patch(patch) for patch in patches]

    # Write user config once; write runtime state (started_at) alongside it.
    config.save()

    # Step 2: Collect dependencies
    progress.start("Step 2/5: Collecting dependencies", icon="📦")
    with step_error_handler(debug, progress, "Error collecting dependencies"):
        collect_dependencies(
            config,
            config.user.uv_executable,
            config.user.python_version,
            ignore_uv_lock,
            progress,
        )
    config.save_runtime()

    # Step 3: Filter packages
    progress.start("Step 3/5: Filtering packages", icon="🔍")
    with step_error_handler(debug, progress, "Error filtering packages"):
        filter_packages(
            config, progress, enable_slow_test_splitting=not no_slow_test_splitting
        )
    config.save_runtime()

    # Step 4: Select patches
    progress.start("Step 4/5: Selecting patches", icon="🏷️")
    with step_error_handler(debug, progress, "Error selecting patches"):
        select_patches(config, progress)
    config.save_runtime()

    # Step 5: Clone repositories
    progress.start("Step 5/5: Cloning repositories", icon="📥")
    clone_path = config.workdir_path("cloned_repos")
    with step_error_handler(debug, progress, "Error cloning repositories"):
        try:
            clone_repositories(config, clone_path, progress)
        except ValueError as e:
            progress.error(str(e))
            raise click.Abort()
    # clone_repositories calls config.save_runtime() internally for updated references

    progress.success("Setup complete! Ready for testing.", icon="🎉")
