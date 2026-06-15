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

import os
import shutil
import subprocess
import time
from pathlib import Path

import click
import psutil

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

from .repo_test import cmd_repo

# region CLI Command


@cmd_repo.command("e2e")
@click.option("--ignore-uv-lock", is_flag=True, default=False)
@click.option("--apply-patches", is_flag=True, default=False)
@click.option("--thorough-ui", is_flag=True, default=False)
@click.option("--smoketest-ui", is_flag=True, default=False)
@with_progress
@with_config
@with_verbose
@with_debug
def cmd_repo_test(
    config: Config,
    progress: Progress,
    ignore_uv_lock: bool,
    apply_patches: bool,
    thorough_ui: bool,
    smoketest_ui: bool,
):
    """Run tests for the given repository."""
    progress.info("Will run E2E tests for the repository")
    progress.info(f"Apply patches: {apply_patches}")
    progress.info(f"Thorough UI: {thorough_ui}")
    progress.info(f"Smoketest UI: {smoketest_ui}")
    progress.info(f"Ignore UV lock: {ignore_uv_lock}")

    # Setup log and status files using "e2e" as the package name
    log_dir = config.workdir_path("artifacts") / "e2e"
    log_dir.mkdir(parents=True, exist_ok=True)

    variant = "patched" if apply_patches else "original"
    paths = test_artifact_paths(log_dir, variant)
    server_log_file = log_dir / f"{variant}_server_log.log"

    repo_info = config.user.seed_repository.as_tested_package_info()

    if not config.user.seed_repository.e2e:
        progress.warning("No e2e configured for the seed repository.")
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
    parent_directory = config.workdir / "e2e"
    test_repository_path = parent_directory / "repo"
    test_e2e_path = parent_directory / "invenio-e2e"
    shutil.copytree(clone_path / "invenio-e2e", test_e2e_path)

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

    # pnpm workspace

    # 1. Create pnpm-workspace.yaml in parent directory
    workspace_yaml = parent_directory / "pnpm-workspace.yaml"
    workspace_yaml.write_text("packages:\n  - repo/e2e\n  - invenio-e2e\n")

    # 2. Copy e2e directory from invenio-e2e into the repository
    shutil.copytree(test_e2e_path / "e2e", test_repository_path / "e2e")

    # 3. Install dependencies
    subprocess.run(
        ["pnpm", "install"],
        cwd=test_repository_path / "e2e",
        check=True,
        env=os.environ | config.user.env,
    )
    runner_handle = None
    server_log_fh = None
    try:
        with test_run_context(
            paths=paths,
            package_info=repo_info,
            dependencies=patched_dependencies,
            timeout_cmd="npx playwright test",
            timeout_minutes=config.user.test_timeout,
            label="repository",
            progress=progress,
        ):
            python_api = PythonAPI(config.user.env)

            python_api.run_in_venv(
                test_repository_path,
                ["invenio-cli", "install"],
            )

            progress.info(".invenio:")
            progress.info((test_repository_path / ".invenio").read_text())

            python_api.run_in_venv(
                test_repository_path,
                ["invenio-cli", "services", "setup"],
            )

            python_api.run_in_venv(
                test_repository_path,
                ["docker", "ps"],
            )

            python_api.run_in_venv(
                test_repository_path,
                [
                    "invenio",
                    "roles",
                    "create",
                    "admin",
                ],
            )
            python_api.run_in_venv(
                test_repository_path,
                [
                    "invenio",
                    "access",
                    "allow",
                    "administration-access",
                    "role",
                    "admin",
                ],
            )
            python_api.run_in_venv(
                test_repository_path,
                [
                    "invenio",
                    "users",
                    "create",
                    "admin@demo.org",
                    "--password",
                    "123456",
                    "--active",
                    "--confirm",
                ],
            )
            python_api.run_in_venv(
                test_repository_path,
                [
                    "invenio",
                    "roles",
                    "add",
                    "admin@demo.org",
                    "admin",
                ],
            )
            python_api.run_in_venv(
                test_repository_path,
                [
                    "invenio",
                    "access",
                    "allow",
                    "superuser-access",
                    "user",
                    "admin@demo.org",
                ],
            )

            # create an s3 bucket for the test data
            create_s3_location(progress)

            # run the invenio-cli run in a background process
            #
            _venv = PythonAPI._venv_path(test_repository_path)
            server_log_fh = open(server_log_file, "w")
            runner_handle = subprocess.Popen(
                [str(_venv / "bin" / "invenio-cli"), "run", "all", "--no-debug"],
                cwd=test_repository_path,
                env={
                    **os.environ,
                    **config.user.env,
                    "VIRTUAL_ENV": str(_venv),
                    "INVENIO_RATELIMIT_ENABLED": "False",
                    "INVENIO_RECORDS_RESOURCES_FILES_ALLOWED_DOMAINS": '["inveniordm.docs.cern.ch"]',
                    "FLASK_DEBUG": "False",
                },
                stdout=server_log_fh,
                stderr=server_log_fh,
            )
            #
            # we need to wait cca 1 minute for the output to be ready
            time.sleep(60)

            for locale in ["en", "de", "cs"]:
                python_api.run_in_venv(
                    test_repository_path,
                    [
                        "pnpm",
                        "run",
                        "build-translations",
                        "--",
                        "-l",
                        locale,
                    ],
                    cwd=test_repository_path / "e2e",
                )

            # for now, remove the ui tests, only run api tests
            # ui_spec = test_repository_path / "e2e" / "tests" / "invenio.spec.ts"
            # if ui_spec.exists():
            #     ui_spec.unlink()

            subprocess.run(
                ["npx", "playwright", "install"],
                cwd=test_repository_path / "e2e",
                check=True,
                env=os.environ | config.user.env,
            )

            playwright_opts = ["--max-failures", "10", "--retries", "1"]

            if not thorough_ui:
                # always run API tests
                playwright_grep = [
                    "@api",
                ]
                if smoketest_ui:
                    playwright_grep.append("@smoke")
                playwright_opts.extend(["--grep", "|".join(playwright_grep)])

            progress.info(f"Running Playwright tests with {' '.join(playwright_opts)}")

            subprocess.run(
                ["npx", "playwright", "test", *playwright_opts],
                cwd=test_repository_path / "e2e",
                check=True,
                env={
                    **os.environ,
                    **config.user.env,
                    "INVENIO_USER_EMAIL": os.environ.get(
                        "INVENIO_USER_EMAIL", "user@demo.org"
                    ),
                    "INVENIO_USER_PASSWORD": os.environ.get(
                        "INVENIO_USER_PASSWORD", "123456"
                    ),
                    # "CI": "1",
                },
                timeout=config.user.test_timeout * 60
                if config.user.test_timeout
                else None,
            )

            python_api.run_in_venv(
                test_repository_path,
                ["uv", "pip", "freeze"],
                capture_to_file=paths.freeze_file,
                tee_output=False,  # don't print the freeze output to the console
            )

        progress.success("Repository tests completed successfully")

    finally:
        if runner_handle is not None:
            try:
                proc = psutil.Process(runner_handle.pid)
                children = proc.children(recursive=True)
            except psutil.NoSuchProcess:
                children = []

            runner_handle.terminate()
            for child in children:
                try:
                    child.terminate()
                except psutil.NoSuchProcess:
                    pass

            runner_handle.wait()
            psutil.wait_procs(children, timeout=10)

        if server_log_fh is not None:
            server_log_fh.close()

        # Copy playwright report to artifacts regardless of test outcome —
        # Playwright always writes a report, even on failure.
        playwright_report_src = test_repository_path / "e2e" / "playwright-report"
        if playwright_report_src.exists():
            playwright_report_dst = log_dir / f"{variant}_playwright-report"
            if playwright_report_dst.exists():
                shutil.rmtree(playwright_report_dst)
            shutil.copytree(playwright_report_src, playwright_report_dst)


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


def create_s3_location(progress: Progress):
    import boto3
    from botocore.client import Config

    s3 = boto3.client(
        "s3",
        endpoint_url="http://127.0.0.1:9000",
        aws_access_key_id=os.environ.get("INVENIO_S3_ACCESS_KEY_ID", "CHANGE_ME"),
        aws_secret_access_key=os.environ.get(
            "INVENIO_S3_SECRET_ACCESS_KEY", "CHANGE_ME"
        ),
        config=Config(signature_version="s3v4"),
    )

    s3.create_bucket(Bucket="default")
    progress.info("Bucket 'default' created.")
