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

import json
import re
import shutil
import subprocess
from collections import defaultdict
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
from invenio_testrig.config import (
    Config,
    save_execution_status,
)
from invenio_testrig.python_api import PythonAPI
from invenio_testrig.types import ExecutionStatus, Progress, TestedPackageInfo

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
@with_verbose
@with_debug
def cmd_test(
    config: Config,
    package_name: str | None,
    apply_patches: bool,
    all_packages: bool,
    progress: Progress,
):
    """2/ Test the package locally - be sure to call setup first."""

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
        _run_test_package(config, package_name, apply_patches, progress)


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
    total_packages = len(config.tested_packages)

    progress.start(f"Testing {total_packages} packages", icon="🚀")

    for idx, package_name in enumerate(config.tested_packages.keys(), 1):
        progress.start(
            f"[{idx}/{total_packages}] Testing package '{package_name}'", icon="📦"
        )

        try:
            _run_test_package(config, package_name, apply_patches, progress)
            results[package_name] = "✅ PASSED"
            progress.success(f"Package '{package_name}' tests passed")
        except (click.Abort, subprocess.CalledProcessError, ValueError):
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
    progress: Progress,
):
    log_dir = config.workdir_path("artifacts") / package_name
    package_name = package_name.lower()

    if package_name not in config.tested_packages:
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
    if config.disable_codestyle_checks:
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
    python_api = PythonAPI("uv", config.python_version)

    package_name = package_name.lower()

    if package_name not in config.tested_packages:
        raise ValueError(f"Package '{package_name}' not found in tested_packages")

    package_config = config.tested_packages[package_name]

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
        extras=package_config.extras,
        freeze=package_config.freeze,
        progress=progress,
    )

    progress.success(
        f"Successfully installed package '{package_name}' in {working_dir}"
    )

    library_patches = [
        config.tested_packages[x] for x in dependencies if x in config.tested_packages
    ]

    patched = bool(config.tested_packages[package_name].patches) or any(
        bool(config.tested_packages[x].patches)
        for x in dependencies
        if x in config.tested_packages
    )

    # save the actual uv pip freeze into the logs if logging
    log_dir = config.workdir_path("artifacts") / package_name
    freeze_file = log_dir / f"{'patched' if apply_patches else 'original'}_freeze.txt"
    python_api.run_in_venv(
        working_dir,
        ["uv", "pip", "freeze"],
        capture_to_file=freeze_file,
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
    :param progress: Progress reporter for status updates

    :return: Test status ("success", "failed", or "skipped")

    :raises subprocess.CalledProcessError: If the tests fail
    """
    log_dir = config.workdir_path("artifacts") / package_name
    log_file = log_dir / f"{'patched' if apply_patches else 'original'}_log.log"
    status_file = log_dir / f"{'patched' if apply_patches else 'original'}_status.json"
    warnings_file = (
        log_dir / f"warnings_{'patched' if apply_patches else 'original'}.json"
    )
    simplified_log_file = (
        log_dir / f"{'patched' if apply_patches else 'original'}_simplified_log.log"
    )

    if apply_patches and not patched:
        # skip the test execution if patches were requested but not applied
        progress.warning(
            f"No patches applied for package '{package_name}', skipping test execution"
        )
        status = "skipped"
        save_execution_status(
            status_file,
            ExecutionStatus(
                status=status, package=package_config, dependencies=library_patches
            ),
        )
        return status

    api = PythonAPI(
        uv_executable=config.uv_executable, python_version=config.python_version
    )

    progress.start(
        f"Running tests for package '{package_name}' with command: "
        f"{package_config.test}",
        icon="🚀",
    )

    try:
        api.run_in_venv(
            working_dir,
            package_config.test,
            log_file,
            timeout=config.test_timeout * 60,
        )
        progress.success(f"Tests completed successfully for package '{package_name}'")

        # Process warnings from log file
        _process_warnings(log_file, warnings_file, simplified_log_file)

        status = "success"
        save_execution_status(
            status_file,
            ExecutionStatus(
                status=status, package=package_config, dependencies=library_patches
            ),
        )
        return status

    except subprocess.CalledProcessError as e:
        progress.error(
            f"Tests failed for package '{package_name}' with exit code {e.returncode}"
        )
        if log_file:
            progress.info(f"Check the output log at: {log_file}", icon="💡")

        # Process warnings from log file even on failure
        _process_warnings(log_file, warnings_file, simplified_log_file)

        save_execution_status(
            status_file,
            ExecutionStatus(
                status="failed", package=package_config, dependencies=library_patches
            ),
        )
        raise

    except subprocess.TimeoutExpired:
        timeout_minutes = config.test_timeout
        progress.error(
            f"Tests timed out for package '{package_name}' after {timeout_minutes} minutes"
        )
        if log_file:
            progress.info(f"Check the output log at: {log_file}", icon="💡")

        # Process warnings from log file even on timeout
        _process_warnings(log_file, warnings_file, simplified_log_file)

        save_execution_status(
            status_file,
            ExecutionStatus(
                status="failed", package=package_config, dependencies=library_patches
            ),
        )
        raise subprocess.CalledProcessError(returncode=-1, cmd=package_config.test)


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


# region Output Processing

_WARNING_TYPE_RE = re.compile(
    r"DeprecationWarning|PendingDeprecationWarning|ResourceWarning|"
    r"UserWarning|FutureWarning|ImportWarning|RuntimeWarning"
)
_DOCKER_PULL_RE = re.compile(
    r"^\s*[0-9a-f]{12}\s+(?:Waiting|Pulling|Downloading|Verifying|Download|Extracting|Pull complete)"
)


def _filter_log_line(line: str) -> str | None:
    """Filter/clean one log line; returns None if the line should be dropped."""
    if line.strip().startswith("::warning file="):
        return None
    line = re.sub(r"::warning file=.*", "", line)
    if _WARNING_TYPE_RE.search(line):
        return None
    if re.match(r"^\s*warnings summary", line):
        return None
    if re.match(r"^\s*--.*warnings", line):
        return None
    if re.search(r"[0-9]+ warnings?$", line):
        return None
    if _DOCKER_PULL_RE.match(line):
        return None
    if re.match(r"^[=\-]{10,}$", line.strip()):
        return None
    line = re.sub(r"^\.+", "", line)
    line = re.sub(r"\.+$", "", line)
    return line if line.strip() else None


def _process_warnings(
    input_log_path: Path, warnings_json_path: Path, output_log_path: Path
) -> None:
    """Extract warnings from log file and create a filtered version without warnings.

    This function:
    1. Extracts Python warnings from the log file (e.g., DeprecationWarning, RuntimeWarning)
    2. Normalizes and counts occurrences of each warning
    3. Saves warnings to a JSON file
    4. Creates a filtered log file with warnings and other noise removed

    :param input_log_path: Path to the input log file
    :param warnings_json_path: Path where warnings JSON should be saved
    :param output_log_path: Path where filtered log (without warnings) should be saved
    """
    if not input_log_path.exists():
        # If input log doesn't exist, create empty output files
        warnings_json_path.write_text("{}")
        output_log_path.write_text("")
        return

    # Extract warnings
    warnings: dict[str, int] = defaultdict(int)
    warning_pattern = re.compile(r"(\w+Warning:.*?)$")

    with input_log_path.open("r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
        for line in content.splitlines():
            match = warning_pattern.search(line)
            if match:
                warning_text = match.group(1).strip()
                # Normalize by replacing memory addresses with [id]
                warning_text = re.sub(r"0x[0-9a-fA-F]+", "[id]", warning_text)
                warnings[warning_text] += 1

    # Save warnings to JSON
    warnings_json_path.parent.mkdir(parents=True, exist_ok=True)
    warnings_json_path.write_text(json.dumps(dict(warnings), indent=2))

    # Create filtered log without warnings
    output_log_path.parent.mkdir(parents=True, exist_ok=True)

    with input_log_path.open("r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    filtered_lines = []
    for raw_line in lines:
        cleaned = _filter_log_line(raw_line)
        if cleaned is not None:
            filtered_lines.append(cleaned)

    # Write filtered output
    output_log_path.write_text("".join(filtered_lines))


# endregion


# region Utility Functions


def _testing_directory(config: Config, package_name: str, apply_patches: bool) -> Path:
    """Get the testing directory path for a package.

    :param config: Config object
    :param package_name: Name of the package
    :param apply_patches: Whether patches are applied

    :return: Path to the testing directory
    """
    return (
        config.workdir_path("tests")
        / package_name
        / ("patched" if apply_patches else "original")
    )


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

    progress.text(f"Patch mode: {config.patch_mode}", fg="cyan")
    _print_package_patches(package_name, package_config, progress)
    if library_patches:
        _print_library_dependencies(library_patches, progress)
    _print_config_file(package_dir, progress)

    progress.text("=" * 80 + "\n", fg="blue")
    progress.text("::endgroup::")


# endregion
