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
import tempfile
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from invenio_testrig.cli.base import (
    with_config,
    with_debug,
    with_progress,
    with_verbose,
)
from invenio_testrig.cli.utils import (
    TestArtifactPaths,
    test_artifact_paths,
    test_run_context,
)
from invenio_testrig.config import Config, TestedPackageInfo
from invenio_testrig.progress import Progress
from invenio_testrig.python_api import PythonAPI
from invenio_testrig.report import ExecutionStatus, save_execution_status

# region CLI Command


@click.command("test")
@with_progress
@with_config
@click.argument("package_name", required=False)
@click.option(
    "--apply-patches",
    "apply_patches",
    is_flag=True,
    help="Reinstall dependencies from local patches",
)
@click.option(
    "--all",
    "all_packages",
    is_flag=True,
    help="Test all packages in config.tested_packages",
)
@click.option(
    "--part",
    "part",
    type=str,
    help="Test a specific part of the package (for slow tests)",
)
@with_verbose
@with_debug
def cmd_test(
    config: Config,
    package_name: str | None,
    apply_patches: bool,
    all_packages: bool,
    part: str | None,
    progress: Progress,
):
    """2/ Test the package locally - be sure to call setup first."""
    if part and part.lower() == "none":
        part = None

    if part:
        part_number = int(part)
    else:
        part_number = None

    # Validation: both --all and package_name cannot be used together
    if all_packages and package_name:
        progress.error("Cannot specify both --all and a package name")
        raise click.Abort()

    # Validation: at least one must be provided
    if not all_packages and not package_name:
        progress.error("Must specify either a package name or --all")
        raise click.Abort()

    # Test all packages
    if all_packages:
        _run_test_all_packages(config, apply_patches, progress)
    else:
        # Test single package
        assert package_name is not None  # This is guaranteed by validation above
        _run_test_package(config, package_name, apply_patches, part_number, progress)


# endregion


# region Test Orchestration


def _run_test_all_packages(config: Config, apply_patches: bool, progress: Progress):
    """Test all packages in config.tested_packages.

    :param config: Configuration object containing paths and settings
    :param apply_patches: Whether to install dependencies from the patched directory
    """
    results = {}
    has_failures = False

    console = Console()
    total_packages = len(config.runtime.tested_packages)

    progress.start(f"Testing {total_packages} packages", icon="🚀")

    for idx, package_name in enumerate(config.runtime.tested_packages.keys(), 1):
        progress.start(
            f"[{idx}/{total_packages}] Testing package '{package_name}'", icon="📦"
        )

        try:
            _run_test_package(config, package_name, apply_patches, None, progress)
            results[package_name] = "✅ PASSED"
            progress.success(f"Package '{package_name}' tests passed")
        except click.Abort, subprocess.CalledProcessError, ValueError:
            results[package_name] = "❌ FAILED"
            has_failures = True
            progress.error(f"Package '{package_name}' tests failed")
        except Exception as e:
            results[package_name] = f"❌ ERROR: {str(e)}"
            has_failures = True
            progress.error(f"Package '{package_name}' error: {str(e)}")

    # Print summary table
    console.print("\n")
    table = Table(
        title="Test Results Summary", show_header=True, header_style="bold magenta"
    )
    table.add_column("Package", style="cyan", no_wrap=True)
    table.add_column("Status", style="green")

    for package_name, status in results.items():
        if "PASSED" in status:
            table.add_row(package_name, f"[green]{status}[/green]")
        else:
            table.add_row(package_name, f"[red]{status}[/red]")

    console.print(table)

    # Summary statistics
    passed = sum(1 for s in results.values() if "PASSED" in s)
    failed = total_packages - passed
    console.print(
        f"\n[bold]Summary:[/bold] {passed} passed, {failed} failed out of {total_packages} total"
    )

    if has_failures:
        raise SystemExit(1)


def _run_test_package(
    config: Config,
    package_name: str,
    apply_patches: bool,
    part_number: int | None,
    progress: Progress,
):
    log_dir = config.workdir_path("artifacts") / package_name
    package_name = package_name.lower()

    if package_name not in config.runtime.tested_packages:
        progress.error(f"Package '{package_name}' not found in tested_packages")
        raise click.Abort()

    # Prepare log directory
    log_dir.mkdir(parents=True, exist_ok=True)

    # Install the package
    progress.text(f"::group::📦 Installing package '{package_name}'")
    try:
        package_dir, package_config, library_patches, patched = (
            _install_package_for_testing(
                config,
                package_name,
                apply_patches,
                progress,
            )
        )
    except ValueError as e:
        progress.text("::endgroup::")
        progress.error(str(e))
        raise click.Abort()

    progress.text("::endgroup::")

    # Print test configuration summary
    _print_patch_summary(
        config, package_dir, package_name, package_config, library_patches, progress
    )

    # Disable codestyle checks if requested
    if config.user.disable_codestyle_checks:
        progress.info(
            "Disabling codestyle checks (black, isort, pydocstyle)",
            icon="🔧",
        )
        _disable_codestyle_checks(package_dir)

    # Run the tests
    progress.text(f"::group::🚀 Running tests for package '{package_name}'")
    try:
        _run_tests(
            config,
            package_dir,
            package_name,
            package_config,
            library_patches,
            apply_patches,
            patched,
            part_number,
            progress,
        )
        progress.text("::endgroup::")

    except subprocess.CalledProcessError:
        progress.text("::endgroup::")
        progress.text("::error::Tests failed")
        raise


# endregion


# region Package Installation


def _install_package_for_testing(
    config: Config,
    package_name: str,
    apply_patches: bool,
    progress: Progress,
) -> tuple[Path, TestedPackageInfo, list[TestedPackageInfo], bool]:
    """Install a package for testing.

    :param config: Config object
    :param package_name: Name of the package to test
    :param apply_patches: Whether to install dependencies from the patched directory
    :param progress: Progress reporter for status updates

    :return: Tuple of (package_config, library_patches, has_patches)

    :raises ValueError: If the package is not found in tested_packages
    """
    python_api = PythonAPI(config.user.env, "uv", config.user.python_version)

    package_name = package_name.lower()
    variant = "patched" if apply_patches else "original"

    if package_name not in config.runtime.tested_packages:
        raise ValueError(f"Package '{package_name}' not found in tested_packages")

    package_config = config.runtime.tested_packages[package_name]

    working_dir = _testing_directory(config, package_name, apply_patches)
    if working_dir.exists():
        progress.info(
            f"Working directory {working_dir} already exists, removing it before installation",
            icon="⚠️",
        )
        shutil.rmtree(working_dir)

    dependencies = python_api.install_with_patches(
        repositories_root=config.workdir_path("cloned_repos"),
        package_name=package_name,
        target_dir=working_dir,
        install_patched_dependencies=apply_patches,
        extras=package_config.github_entry.extras,
        freeze=package_config.github_entry.freeze,
        progress=progress,
    )

    progress.success(
        f"Successfully installed package '{package_name}' in {working_dir}"
    )

    library_patches = [
        config.runtime.tested_packages[x]
        for x in dependencies
        if x in config.runtime.tested_packages
    ]

    patched = bool(config.runtime.tested_packages[package_name].patches) or any(
        bool(config.runtime.tested_packages[x].patches)
        for x in dependencies
        if x in config.runtime.tested_packages
    )

    # save the actual uv pip freeze into the logs if logging
    log_dir = config.workdir_path("artifacts") / package_name
    paths = test_artifact_paths(log_dir, variant)
    python_api.run_in_venv(
        working_dir,
        ["uv", "pip", "freeze"],
        capture_to_file=paths.freeze_file,
        tee_output=False,  # don't print the freeze output to the console
    )

    return working_dir, package_config, library_patches, patched


# endregion


# region Test Execution


def _run_tests(
    config: Config,
    working_dir: Path,
    package_name: str,
    package_config: TestedPackageInfo,
    library_patches: list[TestedPackageInfo],
    apply_patches: bool,
    patched: bool,
    part_number: int | None,
    progress: Progress,
) -> str:
    """Run tests for an installed package.

    :param config: Config object
    :param working_dir: Directory where the package is installed (either patched or original)
    :param package_name: Name of the package to test
    :param package_config: Package configuration
    :param library_patches: List of dependency packages with configuration
    :param apply_patches: Whether patches were applied
    :param patched: Whether any patches were applied to package or dependencies
    :param part_number: Part number of the test to run (None for all parts)
    :param progress: Progress reporter for status updates

    :return: Test status ("success", "failed", or "skipped")

    :raises subprocess.CalledProcessError: If the tests fail
    """
    log_dir = config.workdir_path("artifacts") / package_name
    variant = "patched" if apply_patches else "original"
    paths = test_artifact_paths(log_dir, variant)

    if apply_patches and not patched:
        # skip the test execution if patches were requested but not applied
        progress.warning(
            f"No patches applied for package '{package_name}', skipping test execution"
        )
        status = "skipped"
        save_execution_status(
            paths.status_file,
            ExecutionStatus(
                status=status, package=package_config, dependencies=library_patches
            ),
        )
        return status

    api = PythonAPI(
        config.user.env,
        uv_executable=config.user.uv_executable,
        python_version=config.user.python_version,
    )

    progress.start(
        f"Running tests for package '{package_name}' with command: "
        f"{package_config.github_entry.test}",
        icon="🚀",
    )

    def make_slow_split(info: TestedPackageInfo) -> list[str]:
        if config.user.slow_test_splitting:
            return info.github_entry.slow_packages.get(info.package, [])
        return []

    filtered_tests = []
    if part_number is not None:
        # we support this only for pytest just now ...
        tests = api.run_in_venv(
            working_dir, ["pytest", "--co", "-q"], check_output=True
        )
        split_points = make_slow_split(config.runtime.tested_packages[package_name])
        assert split_points is not None
        split_start = split_points[part_number - 1] if part_number > 0 else None
        split_end = (
            split_points[part_number] if part_number < len(split_points) else None
        )
        splitting = split_start is None
        seen_tests = set()
        for line in tests.splitlines():
            line = line.strip()
            if not splitting:
                if split_start and line.startswith(split_start):
                    splitting = True
                else:
                    continue

            if split_end and line.startswith(split_end):
                break

            # Remove :: suffix (e.g., ::ISORT, ::BLACK, ::test_name) to get just the file path
            test_path = line.split("::")[0] if "::" in line else line

            # Only include Python test files that start with test_
            if (
                test_path
                and test_path.startswith("tests/")
                and test_path.endswith(".py")
            ):
                filename = Path(test_path).name
                if filename.startswith("test_") and test_path not in seen_tests:
                    seen_tests.add(test_path)
                    filtered_tests.append(test_path)
        if not filtered_tests:
            raise Exception(
                f"Wrong part number for package '{package_name}', no tests found between '{split_start}' and '{split_end}'"
            )

    with test_run_context(
        paths=paths,
        package_info=package_config,
        dependencies=library_patches,
        timeout_cmd=package_config.github_entry.test,
        timeout_minutes=config.user.test_timeout,
        label=f"package '{package_name}'",
        progress=progress,
    ):
        test_command = package_config.github_entry.test
        if filtered_tests:
            # Write filtered tests to a file and use @ syntax to avoid long command lines
            selected_tests_file = log_dir / "selected_tests.txt"
            selected_tests_file.write_text("\n".join(filtered_tests))
            test_command = package_config.github_entry.test + [
                f"@{selected_tests_file}"
            ]
            progress.info(
                f"Running {len(filtered_tests)} filtered tests from {selected_tests_file}"
            )
            progress.info(selected_tests_file.read_text())

        api.run_in_venv(
            working_dir,
            test_command,
            paths.log_file,
            timeout=config.user.test_timeout * 60,
        )

        # temporarily disabled till alembic PRs are merged
        # test_alembic_migrations(config, working_dir, api, paths, progress)
        progress.success(f"Tests completed successfully for package '{package_name}'")

    return "success"


def test_alembic_migrations(
    config: Config,
    working_dir: Path,
    api: PythonAPI,
    paths: TestArtifactPaths,
    progress: Progress,
) -> None:
    # check if there is "invenio" command in the virtualenv

    test_shell_script = """#!/usr/bin/env bash
set -euo pipefail

echo
echo "Testing alembic migrations..."

# check if there is an "invenio" command in path
if ! command -v invenio &> /dev/null; then
    echo "No 'invenio' command found in path, skipping alembic migrations test"
    exit 0
fi

if ! command -v docker-services-cli &> /dev/null; then
    echo "No 'docker-services-cli' command found in path, skipping alembic migrations test"
    exit 0
fi

# some packages (invenio-s3) reference invenio-app and invenio-accounts in tests,
# but they do not have a postgres dependency. This will fail in the invenio-accounts
# as it needs psycopg2 inside the cli commands.
if ! uv pip list | grep -q psycopg2; then
    echo "No 'psycopg2' package found in virtualenv, skipping alembic migrations test"
    exit 0
fi

# try to call invenio shell just to make sure we boot up
if ! invenio shell -c "print('all ok')" ; then
    echo "Failed to call invenio shell !"
    echo ""
    echo "This is normally caused by missing dependencies in cli.py from other packages,"
    echo "that depend on extras in normal runtime but those extras are not installed"
    echo "in this test environment. We ignore this failure for the time being."
    exit 0
fi

function cleanup {
  eval "$(docker-services-cli down --env)"
}
trap cleanup EXIT
eval "$(docker-services-cli up --db postgresql --env)"
export INVENIO_SQLALCHEMY_DATABASE_URI=$SQLALCHEMY_DATABASE_URI
invenio db destroy --yes-i-know
invenio db init
invenio alembic upgrade heads

cat <<EOF >/tmp/compare_alembic_migrations.py
import pprint
import sys
from flask import current_app

ext = current_app.extensions['invenio-db']
difference = ext.alembic.compare_metadata()
if len(difference) > 0:
    print('Difference found in alembic migrations and sqlalchemy state:')
    pprint.pprint(difference)
    sys.exit(1)
else:
    print('No difference found in alembic migrations and sqlalchemy state')
EOF

invenio shell /tmp/compare_alembic_migrations.py

# downgrade and upgrade
invenio alembic downgrade dbdbc1b19cf2
invenio alembic upgrade heads

"""
    with tempfile.NamedTemporaryFile(suffix=".sh", mode="wt") as f:
        f.write(test_shell_script)
        f.flush()
        api.run_in_venv(
            working_dir,
            ["bash", str(f.name)],
            paths.log_file,
            timeout=config.user.test_timeout * 60,
        )


def _disable_codestyle_checks(package_path: Path) -> None:
    """Remove codestyle check flags from package configuration files.

    This function removes --black, --isort, and --pydocstyle flags from
    setup.cfg and pyproject.toml files in the specified package directory.

    :param package_path: Path to the package directory
    """
    flags_to_remove = ["--black ", "--isort ", "--pydocstyle "]

    def fix_file(file_path: Path) -> None:
        """Remove codestyle flags from a single file."""
        if not file_path.exists():
            return
        content = file_path.read_text()
        original_content = content

        for flag in flags_to_remove:
            content = content.replace(flag, "")

        if content != original_content:
            file_path.write_text(content)

    fix_file(package_path / "setup.cfg")
    fix_file(package_path / "pyproject.toml")


# endregion


# region Utility Functions


def _testing_directory(config: Config, package_name: str, apply_patches: bool) -> Path:
    """Get the testing directory path for a package.

    :param config: Config object
    :param package_name: Name of the package
    :param apply_patches: Whether patches are applied

    :return: Path to the testing directory
    """
    variant = "patched" if apply_patches else "original"
    return config.workdir_path("tests") / package_name / variant


def _print_package_patches(
    package_name: str, package_config: TestedPackageInfo, progress: Progress
) -> None:
    """Print the package name and its applied patches."""
    progress.text(f"\n📦 Package: {package_name}", fg="green", bold=True)
    if package_config.patches:
        for patch in package_config.patches:
            progress.text(f"  Patch: {str(patch)}", fg="yellow")
    else:
        progress.text("  No patches applied", fg="white", dim=True)


def _print_library_dependencies(
    library_patches: list[TestedPackageInfo], progress: Progress
) -> None:
    """Print patched and unpatched library dependencies."""
    patched_libs = [lib for lib in library_patches if lib.patches]
    unpatched_libs = [lib for lib in library_patches if not lib.patches]

    if patched_libs:
        progress.text(
            f"\n📚 Patched dependencies ({len(patched_libs)}):",
            fg="green",
            bold=True,
        )
        for lib_info in patched_libs:
            progress.text(f"  {lib_info.reference.package}:", fg="cyan")
            for patch in lib_info.patches:
                progress.text(f"    Patch: {str(patch)}", fg="yellow")

    if unpatched_libs:
        progress.text(
            f"\n📚 Locally installed dependencies without patches ({len(unpatched_libs)}):",
            fg="white",
            dim=True,
        )
        for lib_info in unpatched_libs:
            progress.text(f"  {lib_info.reference.package}", fg="white", dim=True)


def _print_config_file(package_dir: Path, progress: Progress) -> None:
    """Print setup.cfg or pyproject.toml contents if present."""
    for candidate in ["setup.cfg", "pyproject.toml"]:
        candidate_path = package_dir / candidate
        if candidate_path.exists():
            progress.text(f"\n⚙️  Configuration file: {candidate_path.name}", fg="cyan")
            with open(candidate_path) as f:
                for line in f:
                    progress.text(f"  {line.rstrip()}", fg="white")
            return
    progress.text("\n⚙️  No setup.cfg or pyproject.toml found", fg="white", dim=True)


def _print_patch_summary(
    config: Config,
    package_dir: Path,
    package_name: str,
    package_config: TestedPackageInfo,
    library_patches: list[TestedPackageInfo],
    progress: Progress,
) -> None:
    """Print a summary of package configuration and applied patches.

    :param config: Configuration object containing patch_mode information
    :param package_name: Name of the package being tested
    :param package_config: Configuration for the tested package
    :param library_patches: List of dependency packages with configuration
    :param progress: Progress reporter for styled output
    """
    progress.text("::group::📋 Test Configuration Summary")
    progress.text("\n" + "=" * 80, fg="blue")
    progress.text("📋 Test Configuration Summary", fg="blue", bold=True)
    progress.text("=" * 80, fg="blue")

    progress.text(f"Patch mode: {config.user.patch_mode}", fg="cyan")
    _print_package_patches(package_name, package_config, progress)
    if library_patches:
        _print_library_dependencies(library_patches, progress)
    _print_config_file(package_dir, progress)

    progress.text("=" * 80 + "\n", fg="blue")
    progress.text("::endgroup::")


# endregion
