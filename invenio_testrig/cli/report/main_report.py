# -*- coding: utf-8 -*-
#
# Copyright (C) 2026 CESNET z.s.p.o.
#
# invenio-testrig is free software; you can redistribute it and/or
# modify it under the terms of the MIT License; see LICENSE file for more
# details.
"""Report CLI command and report generation logic."""

import json
import shutil
from pathlib import Path
from typing import Any

import click

from invenio_testrig.cli.base import (
    with_config,
    with_debug,
    with_progress,
    with_verbose,
)
from invenio_testrig.cli.report.data import load_test_artifacts
from invenio_testrig.cli.report.report_utils import (
    build_base_jinja_context,
    get_jinja_template,
    to_serializable,
)
from invenio_testrig.config import Config
from invenio_testrig.types import Progress, ReportPackageData

# region Report Stats & Partitioning


def _compute_report_status(completed: bool, has_errors: bool) -> tuple[str, str, str]:
    """Return (status_label, css_class, icon) for the report header badge."""
    if completed and has_errors:
        return "Complete with Errors", "complete-errors", "❌"
    if completed:
        return "Complete", "complete", "✅"
    if has_errors:
        return "In Progress with Errors", "in-progress-errors", "⚠️"
    return "In Progress", "in-progress", "🔄"


_INACTIVE = ("skipped", "pending")


def _is_regression(p: ReportPackageData) -> bool:
    return p.patched.status == "failed" and p.original.status == "success"


def _is_still_failing(p: ReportPackageData) -> bool:
    return p.original.status == "failed" and p.patched.status != "success"


def _is_patched_active(p: ReportPackageData) -> bool:
    return p.patched.status not in _INACTIVE or p.original.status not in _INACTIVE


def _is_both_inactive(p: ReportPackageData) -> bool:
    return p.patched.status in _INACTIVE and p.original.status in _INACTIVE


def _is_ok(p: ReportPackageData) -> bool:
    return p.patched.status == "success" or (
        p.original.status == "success" and p.patched.status in _INACTIVE
    )


def _count_report_stats(
    test_results: list[ReportPackageData],
) -> tuple[int, int, int, int, int]:
    """Return (regression, still_failing, patched_active, both_inactive, ok) counts."""
    regression, still_failing, patched, unpatched, ok = 0, 0, 0, 0, 0
    for p in test_results:
        if _is_regression(p):
            regression += 1
        if _is_still_failing(p):
            still_failing += 1
        if _is_patched_active(p):
            patched += 1
        if _is_both_inactive(p):
            unpatched += 1
        if _is_ok(p):
            ok += 1
    return regression, still_failing, patched, unpatched, ok


def _partition_packages(
    test_results: list[ReportPackageData],
) -> tuple[list[ReportPackageData], list[ReportPackageData], list[ReportPackageData]]:
    """Split packages into (without_patches, patched, with_patched_deps)."""
    without_patches: list[ReportPackageData] = []
    patched_packages: list[ReportPackageData] = []
    with_patched_deps: list[ReportPackageData] = []
    for p in test_results:
        ps, os = p.patched.status, p.original.status
        if ps in _INACTIVE and os not in ("pending",):
            without_patches.append(p)
        if ps not in _INACTIVE and p.info.patches:
            patched_packages.append(p)
        if ps not in _INACTIVE and not p.info.patches:
            with_patched_deps.append(p)
    return without_patches, patched_packages, with_patched_deps


def _build_report_counts(test_results: list[ReportPackageData]) -> dict[str, Any]:
    """Compute all numerical counts and package sub-lists for the report context."""
    regression, still_failing, patched, unpatched, ok = _count_report_stats(
        test_results
    )
    packages_without_patches, patched_packages, with_patched_deps = _partition_packages(
        test_results
    )
    return {
        "regression_count": regression,
        "still_failing_count": still_failing,
        "total_packages_count": len(test_results),
        "patched_packages_count": patched,
        "unpatched_packages_count": unpatched,
        "ok_count": ok,
        "packages_without_patches": packages_without_patches,
        "patched_packages": patched_packages,
        "packages_with_patched_dependencies": with_patched_deps,
    }


# endregion


# region Report Generation


def generate_report(
    config: Config,
    completed: bool,
    test_results: list[ReportPackageData],
    report_output_path: Path,
    progress: Progress,
) -> None:
    """Generate a report based on the execution status of the tested packages."""
    # Filter out packages that are still pending (both patched and original are pending)
    test_results = [
        p
        for p in test_results
        if not (p.patched.status == "pending" and p.original.status == "pending")
    ]

    counts = _build_report_counts(test_results)
    has_errors = (counts["regression_count"] + counts["still_failing_count"]) > 0
    status, status_class, status_icon = _compute_report_status(completed, has_errors)

    jinja_context: dict[str, Any] = {
        **build_base_jinja_context(config),
        "status": status,
        "status_class": status_class,
        "status_icon": status_icon,
        "completed": completed,
        "has_errors": has_errors,
        "packages": test_results,
        **counts,
    }

    template = get_jinja_template("report.html")
    report_content = template.render(**jinja_context)

    report_output_path.mkdir(parents=True, exist_ok=True)
    report_file = report_output_path / "index.html"
    report_file.write_text(report_content)

    with (report_output_path / "data.json").open("w") as f:
        json.dump(
            to_serializable(jinja_context),
            f,
            default=str,
            indent=2,
        )


# endregion


# region CLI Command


@click.command("report")
@with_progress
@with_config
@click.argument(
    "report_output_path", type=click.Path(path_type=Path, resolve_path=True)
)
@click.option(
    "--completed",
    is_flag=True,
    help="Whether to mark the report as completed (e.g. all tests finished)",
)
@with_verbose
@with_debug
def report_cmd(
    config: Config,
    report_output_path: Path,
    completed: bool,
    progress: Progress,
):
    """3/ Generate a test report based on the test artefacts.

    Reads the test status files and logs from the artefacts directory and creates
    a report summarizing the test results, applied patches, etc.

    The report format is always a HTML file saved to report_output_path.
    """
    artefacts_path = config.workdir_path("artifacts")

    progress.start(
        f"Generating test report based on artefacts in {artefacts_path} and configuration in {config.config_path}",
        icon="📊",
    )

    # Debug: Check if artifacts directory exists and what's in it
    progress.info(f"Checking artifacts directory: {artefacts_path}")
    if not artefacts_path.exists():
        progress.error(f"Artifacts directory does not exist: {artefacts_path}")
    else:
        progress.info(f"Artifacts directory exists: {artefacts_path}")
        items = list(artefacts_path.iterdir())
        progress.info(f"Found {len(items)} items in artifacts directory")
        for item in items:
            if item.is_dir():
                progress.info(f"  - Directory: {item.name}")
                status_files = list(item.glob("*_status.json"))
                progress.info(
                    f"    Found {len(status_files)} status files: {[f.name for f in status_files]}"
                )
            else:
                progress.info(f"  - File: {item.name}")

    # Debug: Check tested packages in config
    progress.info(f"Config contains {len(config.tested_packages)} tested packages")
    for pkg_name in list(config.tested_packages.keys())[:5]:  # Show first 5
        progress.info(f"  - {pkg_name}")
    if len(config.tested_packages) > 5:
        progress.info(f"  ... and {len(config.tested_packages) - 5} more")

    test_result_data = load_test_artifacts(config, artefacts_path, progress=progress)

    # Debug: Check loaded test results
    progress.info(f"Loaded {len(test_result_data)} test result entries")
    for pkg in test_result_data[:5]:  # Show first 5
        progress.info(
            f"  - {pkg.info.reference.package}: patched={pkg.patched.status}, original={pkg.original.status}"
        )
    if len(test_result_data) > 5:
        progress.info(f"  ... and {len(test_result_data) - 5} more")

    generate_report(
        config,
        completed,
        test_result_data,
        report_output_path=report_output_path,
        progress=progress,
    )

    # Generate warnings report
    from invenio_testrig.cli.report.warnings import create_warnings_report

    artifacts_path = config.workdir_path("artifacts")
    create_warnings_report(config, artifacts_path, report_output_path, progress)

    # copy the config.json into the report directory to be preserved
    # in machine-readable format alongside the report
    if config.config_path is not None:
        # never is in reality, but the type checker doesn't know that
        shutil.copy(config.config_path, report_output_path / "config.json")


# endregion
