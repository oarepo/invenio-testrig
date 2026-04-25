# -*- coding: utf-8 -*-
#
# Copyright (C) 2026 CESNET z.s.p.o.
#
# invenio-testrig is free software; you can redistribute it and/or
# modify it under the terms of the MIT License; see LICENSE file for more
# details.
"""Command for merging split test artifacts.

This module provides functionality to merge test results from packages that were
split into multiple parts for parallel execution.
"""

import json
from pathlib import Path

import click

from invenio_testrig.cli.base import with_config, with_progress
from invenio_testrig.cli.utils import test_artifact_paths
from invenio_testrig.config import Config, TestedPackageInfo
from invenio_testrig.progress import Progress
from invenio_testrig.report import ExecutionStatusSchema, save_execution_status


@click.command("merge-test-artifacts", hidden=True)
@with_progress
@with_config
def cmd_merge_test_artifacts(config: Config, progress: Progress):
    """Merge test artifacts from split package tests.

    This command merges test results from packages that were split into multiple
    parts for parallel execution. It combines logs and determines the overall
    status based on all parts.
    """
    workdir_path = config.workdir

    def make_slow_split(info: TestedPackageInfo) -> list[str]:
        if config.user.slow_test_splitting:
            return info.github_entry.slow_packages.get(info.package, [])
        return []

    # Find packages that need merging
    packages_to_merge = [
        pkg_name
        for pkg_name, pkg_info in config.runtime.tested_packages.items()
        if make_slow_split(pkg_info)
    ]

    if not packages_to_merge:
        progress.info("No packages require merging", icon="ℹ️")
        return

    progress.info(
        f"Found {len(packages_to_merge)} package(s) to merge: {', '.join(packages_to_merge)}",
        icon="📦",
    )

    artifacts_dir = workdir_path / "artifacts"

    for package_name in packages_to_merge:
        progress.start(f"Merging artifacts for package '{package_name}'", icon="🔀")

        # Determine number of parts
        slow_split = make_slow_split(config.runtime.tested_packages[package_name])
        assert slow_split is not None
        num_parts = len(slow_split) + 1

        # Find all part directories
        part_dirs = []
        for part_num in range(num_parts):
            part_dir = artifacts_dir / f"{package_name}-{part_num}"
            if part_dir.exists():
                part_dirs.append((part_num, part_dir))
            else:
                progress.warning(
                    f"Missing artifacts for part {part_num} of '{package_name}'"
                )

        if not part_dirs:
            progress.error(f"No artifact parts found for package '{package_name}'")
            continue

        # Create merged directory
        merged_dir = artifacts_dir / package_name
        merged_dir.mkdir(exist_ok=True)

        # Merge for both patched and original
        for test_type in ["patched", "original"]:
            _merge_test_type(package_name, part_dirs, merged_dir, test_type, progress)

        progress.success(f"Merged artifacts for package '{package_name}'")


def _merge_test_type(
    package_name: str,
    part_dirs: list[tuple[int, Path]],
    merged_dir: Path,
    test_type: str,
    progress: Progress,
):
    """Merge artifacts for a specific test type (patched or original).

    :param package_name: Name of the package
    :param part_dirs: List of (part_number, part_directory) tuples
    :param merged_dir: Directory where merged results should be saved
    :param test_type: Type of test ('patched' or 'original')
    :param progress: Progress reporter
    """
    paths = test_artifact_paths(merged_dir, test_type)

    # Merge logs
    merged_logs = []
    merged_simplified_logs = []
    merged_warnings = []
    merged_status = None
    overall_status = "success"

    for part_num, part_dir in sorted(part_dirs):
        part_paths = test_artifact_paths(part_dir, test_type)

        # Collect logs
        if part_paths.log_file.exists():
            merged_logs.append(f"=== Part {part_num} ===\n")
            merged_logs.append(part_paths.log_file.read_text())
            merged_logs.append("\n")

        # Collect simplified logs
        if part_paths.simplified_log_file.exists():
            merged_simplified_logs.append(f"=== Part {part_num} ===\n")
            merged_simplified_logs.append(part_paths.simplified_log_file.read_text())
            merged_simplified_logs.append("\n")

        # Collect warnings
        if part_paths.warnings_file.exists():
            try:
                part_warnings_data = json.loads(part_paths.warnings_file.read_text())
                merged_warnings.extend(part_warnings_data)
            except json.JSONDecodeError, TypeError:
                progress.warning(
                    f"Failed to parse warnings from part {part_num} of '{package_name}'"
                )

        # Collect status
        if part_paths.status_file.exists():
            try:
                part_status_data = json.loads(part_paths.status_file.read_text())
                status_obj = ExecutionStatusSchema().load(part_status_data)

                # Use first part's status as template
                if merged_status is None:
                    merged_status = status_obj

                # Determine overall status - if any part failed, overall fails
                if status_obj.status == "failed":
                    overall_status = "failed"
                elif status_obj.status == "skipped" and overall_status == "success":
                    overall_status = "skipped"

            except (json.JSONDecodeError, TypeError) as e:
                progress.warning(
                    f"Failed to parse status from part {part_num} of '{package_name}': {e}"
                )
                overall_status = "failed"

    # Write merged logs
    if merged_logs:
        paths.log_file.write_text("".join(merged_logs))

    if merged_simplified_logs:
        paths.simplified_log_file.write_text("".join(merged_simplified_logs))

    # Write merged warnings
    if merged_warnings:
        paths.warnings_file.write_text(json.dumps(merged_warnings, indent=2))

    # Write merged status
    if merged_status:
        merged_status.status = overall_status
        save_execution_status(paths.status_file, merged_status)
    else:
        progress.warning(
            f"No status files found for {test_type} tests of '{package_name}'"
        )
