# -*- coding: utf-8 -*-
#
# Copyright (C) 2026 CESNET z.s.p.o.
#
# invenio-testrig is free software; you can redistribute it and/or
# modify it under the terms of the MIT License; see LICENSE file for more
# details.
"""Warnings collection and warnings report generation."""

import json
from collections import defaultdict
from pathlib import Path

from invenio_testrig.config import Config
from invenio_testrig.types import Progress


def collect_warnings(
    config: Config,
    artifacts_path: Path,
    progress: Progress,
) -> dict[str, dict[str, tuple[int, str]]]:
    """Collect warnings from test runs, loading patched warnings for patched packages
    and original warnings for unpatched packages.

    :param config: Configuration object to determine which packages are patched
    :param artifacts_path: Path to the artifacts directory containing package subdirectories
    :param progress: Progress reporter for status updates

    :return: Dict mapping warning text to package data (count and artifact link).
        Structure: ``{warning_text: {package_name: (count, artifact_link)}}``
    """
    warnings_data: dict[str, dict[str, tuple[int, str]]] = defaultdict(dict)

    if not artifacts_path.exists():
        return {}

    # Iterate through each tested package
    for package_name, package_info in config.tested_packages.items():
        package_dir = artifacts_path / package_name
        if not package_dir.exists():
            continue

        # Determine which warnings file to load:
        # - If package has patches, load warnings from patched tests only
        # - If package has no patches, load warnings from original tests
        for log_type in ["patched", "original"]:
            warnings_file = package_dir / f"warnings_{log_type}.json"
            if not warnings_file.exists():
                continue

        if not warnings_file.exists():
            continue

        try:
            # Construct link to the simplified log file
            artifact_link = f"artifacts/{package_name}/{log_type}_log.log"

            with warnings_file.open("r") as f:
                warnings_json = json.load(f)

            if isinstance(warnings_json, dict):
                for warning_text, count in warnings_json.items():
                    warnings_data[warning_text][package_name] = (
                        count,
                        artifact_link,
                    )

        except (json.JSONDecodeError, IOError) as e:
            progress.error(f"Failed to process {warnings_file}: {e}")
            continue

    return dict(warnings_data)


def calculate_total_occurrences(package_data: dict[str, tuple[int, str]]) -> int:
    """Calculate total occurrences of a warning across all packages.

    :param package_data: Dict mapping package names to (count, artifact_link) tuples

    :return: Total count across all packages
    """
    return sum(count for count, _ in package_data.values())


def create_warnings_report(
    config: Config,
    artifacts_path: Path,
    report_output_path: Path,
    progress: Progress,
) -> None:
    """Collect all warnings and create a warnings report in HTML format.

    :param config: Configuration object
    :param artifacts_path: Path to the artifacts directory
    :param report_output_path: Path where the report should be saved
    :param progress: Progress reporter for status updates
    """
    from invenio_testrig.cli.report.report_utils import (
        build_base_jinja_context,
        get_jinja_template,
        to_serializable,
    )

    progress.info("Generating warnings report")

    # Collect warnings from all packages (merged from patched and unpatched)
    warnings_data = collect_warnings(config, artifacts_path, progress)

    # Calculate statistics
    total_unique_warnings = len(warnings_data)
    total_packages_with_warnings: set[str] = set()
    total_warning_occurrences = 0

    for warning_text, package_data in warnings_data.items():
        total_packages_with_warnings.update(package_data.keys())
        total_warning_occurrences += calculate_total_occurrences(package_data)

    progress.info(
        f"Found {total_unique_warnings} unique warning(s) from {len(total_packages_with_warnings)} package(s)"
    )

    # Sort warnings by total count (descending) then by text (ascending)
    sorted_warnings = sorted(
        warnings_data.items(),
        key=lambda x: (-calculate_total_occurrences(x[1]), x[0]),
    )

    # Prepare warnings list for template
    warnings_list = []
    for warning_text, package_data in sorted_warnings:
        total_count = calculate_total_occurrences(package_data)
        # Sort packages by count descending, build list with name, count, and link
        package_list = [
            {
                "name": pkg_name,
                "count": count,
                "link": artifact_link,
            }
            for pkg_name, (count, artifact_link) in sorted(
                package_data.items(), key=lambda x: (-x[1][0], x[0])
            )
        ]

        warnings_list.append(
            {
                "text": warning_text,
                "total_count": total_count,
                "packages": package_list,
            }
        )

    jinja_context = {
        **build_base_jinja_context(config),
        "total_unique_warnings": total_unique_warnings,
        "total_packages_with_warnings": len(total_packages_with_warnings),
        "total_warning_occurrences": total_warning_occurrences,
        "warnings": warnings_list,
    }

    template = get_jinja_template("warnings.html")
    report_content = template.render(**jinja_context)

    # Save the report
    report_output_path.mkdir(parents=True, exist_ok=True)
    warnings_report_file = report_output_path / "warnings.html"
    warnings_report_file.write_text(report_content)

    with (report_output_path / "warnings.json").open("w") as f:
        json.dump(
            to_serializable(jinja_context),
            f,
            default=str,
            indent=2,
        )

    progress.success(f"Warnings report written to: {warnings_report_file}")
