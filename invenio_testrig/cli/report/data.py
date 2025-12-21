# -*- coding: utf-8 -*-
#
# Copyright (C) 2026 CESNET z.s.p.o.
#
# invenio-testrig is free software; you can redistribute it and/or
# modify it under the terms of the MIT License; see LICENSE file for more
# details.
"""Test artifact data loading."""

from pathlib import Path

from invenio_testrig.config import Config, load_execution_status
from invenio_testrig.types import ExecutionStatus, Progress, ReportPackageData


def load_test_artifacts(
    config: Config, artefacts_path: Path, progress: Progress
) -> list[ReportPackageData]:
    """Load test artifacts from the artifacts directory.

    Scans the artifacts directory for test execution status files and loads
    them into ReportPackageData structures for report generation.

    :param config: Configuration containing tested packages information
    :param artefacts_path: Path to the directory containing test artifacts
    :param progress: Progress reporter for status updates

    :return: List of :class:`~invenio_testrig.types.ReportPackageData` with test
        execution status for each package, sorted by result priority
    """

    package_data: dict[str, ReportPackageData] = {
        package_name: ReportPackageData(
            info=package_info,
            artefact_dir=f"artifacts/{package_name}",
            patched=ExecutionStatus(status="pending", package=package_info),
            original=ExecutionStatus(status="pending", package=package_info),
        )
        for package_name, package_info in config.tested_packages.items()
    }

    if not artefacts_path.exists():
        progress.warning(f"Artifacts path does not exist: {artefacts_path}")
        return sorted(
            package_data.values(), key=lambda p: (4, p.info.reference.package)
        )

    dir_items = list(artefacts_path.iterdir())

    for package_dir in dir_items:
        package_name = package_dir.name

        if package_name not in package_data:
            progress.warning(
                f"Found artefacts for package '{package_name}' which is not in the config, skipping"
            )
            continue
        if package_dir.is_dir():
            status_files = list(package_dir.glob("*_status.json"))
            # the directory is present, thus the package is not pending
            package_data[package_name].patched.status = "skipped"
            package_data[package_name].original.status = "skipped"

            # now overwrite with actual status if status files are present
            for status_file in status_files:
                loaded_status = load_execution_status(status_file)
                match status_file.stem:
                    case "patched_status":
                        package_data[package_name].patched = loaded_status
                    case "original_status":
                        package_data[package_name].original = loaded_status
                    case _:
                        progress.warning(
                            f"Found unexpected status file '{status_file.name}' for package '{package_name}', skipping"
                        )

    # Sort results: failed first, then success, then skipped; within each group, sort by package name
    def sort_key(pkg: ReportPackageData) -> tuple[int, str]:
        """Generate sort key: (priority, package_name)."""
        patched_status = pkg.patched.status
        if patched_status == "failed":
            priority = 0  # Failed packages first
        elif patched_status == "success":
            priority = 1  # Success packages second
        else:  # skipped or any other status
            original_status = pkg.original.status
            if original_status == "failed":
                priority = 2  # Failed packages first (even if patched was skipped)
            elif original_status == "success":
                priority = 3  # Success packages second (even if patched was skipped)
            else:
                priority = 4  # Skipped/other packages last
        return (priority, pkg.info.reference.package)

    return sorted(package_data.values(), key=sort_key)
