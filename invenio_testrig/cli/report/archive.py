# -*- coding: utf-8 -*-
#
# Copyright (C) 2026 CESNET z.s.p.o.
#
# invenio-testrig is free software; you can redistribute it and/or
# modify it under the terms of the MIT License; see LICENSE file for more
# details.
"""Report archiving CLI command."""

import shutil
import zipfile
from pathlib import Path

import click

from invenio_testrig.cli.base import with_progress
from invenio_testrig.progress import Progress


@click.command("archive-report", hidden=True)
@with_progress
@click.argument(
    "report_directory", type=click.Path(exists=True, path_type=Path, resolve_path=True)
)
def archive_report_cmd(
    report_directory: Path,
    progress: Progress,
):
    """9/ Archive a report directory by zipping its contents.

    Creates a report.zip file containing all files from the report directory,
    then removes all files except the zip and data.json (if present).

    This is useful for reducing storage space for older reports while keeping
    them accessible in archived form.
    """
    if not report_directory.is_dir():
        progress.error(f"Not a directory: {report_directory}")
        raise click.Abort()

    progress.start(f"Archiving report directory: {report_directory}", icon="📦")

    # Check if already archived
    zip_file = report_directory / "report.zip"
    if zip_file.exists():
        # Check if there are other files besides the zip and data.json
        files = list(report_directory.iterdir())
        non_archive_files = [
            f for f in files if f.name not in ("report.zip", "data.json")
        ]
        if not non_archive_files:
            progress.warning("Report is already archived")
            return

    progress.info("Creating report.zip...")
    _create_zip_from_directory(report_directory, zip_file, progress)

    progress.info("Removing unarchived files...")
    removed_count = _remove_non_archive_files(report_directory)
    files_after = len(list(report_directory.iterdir()))

    progress.success(
        f"Report archived successfully. Removed {removed_count} items, kept {files_after} item(s)"
    )
    progress.info(f"Archive: {zip_file}")
    progress.info(f"Archive size: {zip_file.stat().st_size / 1024 / 1024:.2f} MB")


def _create_zip_from_directory(
    report_directory: Path, zip_file: Path, progress: Progress
) -> None:
    """Write every file in report_directory into zip_file (skip the zip itself)."""
    with zipfile.ZipFile(zip_file, "w", zipfile.ZIP_DEFLATED) as zipf:
        for item in report_directory.rglob("*"):
            if item.is_file() and item != zip_file:
                arcname = item.relative_to(report_directory)
                zipf.write(item, arcname)
                progress.info(f"  Added: {arcname}")


def _remove_non_archive_files(report_directory: Path) -> int:
    """Delete everything except report.zip and data.json. Returns number of items removed."""
    files_before = len(list(report_directory.iterdir()))
    for item in report_directory.iterdir():
        if item.name in ("report.zip", "data.json"):
            continue
        if item.is_file():
            item.unlink()
        elif item.is_dir():
            shutil.rmtree(item)
    return files_before - len(list(report_directory.iterdir()))
