# -*- coding: utf-8 -*-
#
# Copyright (C) 2026 CESNET z.s.p.o.
#
# invenio-testrig is free software; you can redistribute it and/or
# modify it under the terms of the MIT License; see LICENSE file for more
# details.
"""Test execution command implementation.

Provides the test command that runs package tests for patched and/or unpatched
versions, manages virtual environments, captures test output, and saves
execution status for report generation.
"""

import shutil
import subprocess
from pathlib import Path

import click

from invenio_testrig.cli.base import (
    with_config,
    with_debug,
    with_progress,
    with_verbose,
)
from invenio_testrig.cli.utils import test_artifact_paths, test_run_context
from invenio_testrig.config import Config, TestedPackageInfo
from invenio_testrig.progress import Progress
from invenio_testrig.python_api import PythonAPI
from invenio_testrig.report import ExecutionStatus, save_execution_status

# region CLI Command


@click.group("repo")
def cmd_repo():
    """3/ Repository test commands."""


@cmd_repo.command("test")
@click.option("--ignore-uv-lock", is_flag=True, default=False)
@click.option("--apply-patches", is_flag=True, default=False)
@with_progress
@with_config
@with_verbose
@with_debug
def cmd_repo_test(
    config: Config, progress: Progress, ignore_uv_lock: bool, apply_patches: bool
):
    """Run tests for the given repository."""
    # Setup log and status files using "repo" as the package name
    log_dir = config.workdir_path("artifacts") / "repo"
    log_dir.mkdir(parents=True, exist_ok=True)

    variant = "patched" if apply_patches else "original"
    paths = test_artifact_paths(log_dir, variant)

    repo_info = config.user.seed_repository.as_tested_package_info()

    if not config.user.seed_repository.test:
        progress.warning("No tests configured for the seed repository.")
        save_execution_status(
            paths.status_file,
            ExecutionStatus(
                status="skipped",
                package=repo_info,
                dependencies=[],
            ),
        )
        return

    clone_path = config.workdir_path("cloned_repos")
    test_repository_path = config.workdir / "tested-repo"

    # Track which packages have patches applied
    patched_dependencies = []

    install_repository(
        config, clone_path, test_repository_path, ignore_uv_lock=ignore_uv_lock
    )
    if apply_patches:
        patched_dependencies = apply_package_patches(
            config,
            clone_path / "packages",
            clone_path / "patched",
            test_repository_path,
            progress,
        )

    with test_run_context(
        paths=paths,
        package_info=repo_info,
        dependencies=patched_dependencies,
        timeout_cmd=config.user.seed_repository.test,
        timeout_minutes=config.user.test_timeout,
        label="repository",
        progress=progress,
    ):
        run_repository_tests(config, test_repository_path, paths.log_file)

        # Save the actual uv pip freeze into the artifacts
        python_api = PythonAPI(config.user.env)
        python_api.run_in_venv(
            test_repository_path,
            ["uv", "pip", "freeze"],
            capture_to_file=paths.freeze_file,
            tee_output=False,  # don't print the freeze output to the console
        )

    progress.success("Repository tests completed successfully")


def install_repository(
    config, clone_path, tested_repo_path: Path, ignore_uv_lock
) -> None:
    """Install the seed repository into the given path.

    :param path: Path to install the repository into.
    :param config: Configuration object.
    :param ignore_uv_lock: Whether to ignore the UV lock file.
    """
    shutil.copytree(clone_path / "repo", tested_repo_path)
    python_api = PythonAPI(config.user.env)
    python_api.install_project(tested_repo_path, ignore_uv_lock=ignore_uv_lock)


def apply_package_patches(
    config: Config,
    packages_path: Path,
    patched_path: Path,
    tested_repo_path: Path,
    progress: Progress,
) -> list[TestedPackageInfo]:
    """Apply package patches to the given repository.

    :param config: Configuration object.
    :param packages_path: Path to the packages directory.
    :param patched_path: Path to the patched packages directory.
    :param tested_repo_path: Path to the repository.
    :param progress: Progress object for reporting progress.

    :return: List of patched package information
    """
    python_api = PythonAPI(config.user.env)
    python_api.install_patched_dependencies(
        project_path=tested_repo_path,
        packages_root=packages_path,
        patched_packages_root=patched_path,
        install_patched_dependencies=True,
        progress=progress,
    )

    # Collect information about patched packages
    patched_packages = []
    for package_name, package_config in config.runtime.tested_packages.items():
        if package_config.patches:
            patched_packages.append(package_config)

    return patched_packages


def run_repository_tests(
    config: Config, tested_repo_path: Path, log_file: Path
) -> None:
    """Run tests for the given repository.

    :param config: Configuration object.
    :param tested_repo_path: Path to the repository.
    :param log_file: Path to the log file for capturing output.

    :raises subprocess.CalledProcessError: If the tests fail
    :raises subprocess.TimeoutExpired: If the tests exceed the timeout
    """
    test_command = config.user.seed_repository.test
    assert test_command is not None

    python_api = PythonAPI(config.user.env)
    python_api.run_in_venv(
        project_path=tested_repo_path,
        command=test_command,
        capture_to_file=log_file,
        tee_output=True,
        timeout=config.user.test_timeout * 60 if config.user.test_timeout else None,
    )
