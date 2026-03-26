# -*- coding: utf-8 -*-
#
# Copyright (C) 2026 CESNET z.s.p.o.
#
# invenio-testrig is free software; you can redistribute it and/or
# modify it under the terms of the MIT License; see LICENSE file for more
# details.
"""Reports index generation and CLI command."""

import contextlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import click

from invenio_testrig.cli.base import with_progress
from invenio_testrig.cli.report.report_utils import get_jinja_template
from invenio_testrig.progress import Progress


def generate_reports_index(
    reports_directory: Path,
    output_file: Path,
    progress: Progress,
) -> None:
    """Generate an index page listing all reports in the reports directory.

    :param reports_directory: Path to the directory containing report subdirectories
    :param output_file: Path where the index HTML file should be saved
    :param progress: Progress reporter for status updates
    """
    progress.start("Generating reports index", icon="📑")

    if not reports_directory.exists():
        progress.error(f"Reports directory does not exist: {reports_directory}")
        return

    # Find all directories in the reports directory that have an index.html
    report_dirs: list[dict[str, Any]] = []
    for item in reports_directory.iterdir():
        if item.is_dir():
            # Check if index.html exists in the directory
            index_file = item / "index.html"
            if not index_file.exists():
                continue

            data_json_file = item / "data.json"
            report_data = None
            if not data_json_file.exists():
                continue

            try:
                with data_json_file.open("r") as f:
                    report_data = json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                progress.warning(
                    f"Failed to load report data from {data_json_file}: {e}"
                )
                continue

            # Get the last modification time from data.json
            last_updated = report_data.get("last_updated")
            mtime: float = 0.0
            mtime_formatted = "unknown"
            if last_updated:
                # Parse ISO format and convert to timestamp for sorting
                with contextlib.suppress(ValueError, AttributeError):
                    last_updated_dt = datetime.fromisoformat(
                        last_updated.replace(" UTC", "+00:00")
                    )
                    mtime = last_updated_dt.timestamp()
                    mtime_formatted = last_updated

            # Add report directory info
            report_dirs.append(
                {
                    "name": item.name,
                    "path": item.name,  # Relative path from the index
                    "mtime": mtime,
                    "mtime_formatted": mtime_formatted,
                    "ok_count": report_data.get("ok_count", 0),
                    "errors_count": report_data.get("regression_count", 0)
                    + report_data.get("still_failing_count", 0),
                }
            )
    # Sort by modification time, most recent first
    report_dirs.sort(key=lambda x: float(x["mtime"]), reverse=True)

    progress.info(f"Found {len(report_dirs)} report directories")

    jinja_context = {
        "last_updated": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "reports": report_dirs,
    }

    template = get_jinja_template("reports_index.html")
    report_content = template.render(**jinja_context)

    # Save the report
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(report_content)

    progress.success(f"Reports index written to: {output_file}")


@click.command("reports-index", hidden=True)
@with_progress
@click.argument(
    "reports_directory", type=click.Path(exists=True, path_type=Path, resolve_path=True)
)
@click.argument("output_file", type=click.Path(path_type=Path, resolve_path=True))
def reports_index_cmd(
    reports_directory: Path,
    output_file: Path,
    progress: Progress,
):
    """8/ Generate an index page listing all reports in a directory.

    Scans the reports directory for subdirectories and creates an index.html
    file listing all available reports, sorted by last modification time.
    """
    progress.start(
        f"Generating reports index from {reports_directory}",
        icon="📑",
    )

    generate_reports_index(
        reports_directory=reports_directory,
        output_file=output_file,
        progress=progress,
    )
