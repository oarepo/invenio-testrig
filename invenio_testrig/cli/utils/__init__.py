# -*- coding: utf-8 -*-
#
# Copyright (C) 2026 CESNET z.s.p.o.
#
# invenio-testrig is free software; you can redistribute it and/or
# modify it under the terms of the MIT License; see LICENSE file for more
# details.
"""Shared utilities for CLI commands."""

from dataclasses import dataclass
from pathlib import Path

from invenio_testrig.cli.utils.log_processing import process_warnings


@dataclass
class TestArtifactPaths:
    """File paths for the artifacts produced by a single test run variant.

    Centralises the file-naming convention so that writers (test runners) and
    readers (merge, report) all derive the same paths from the same logic.
    Create an instance with :func:`test_artifact_paths`.
    """

    log_file: Path
    status_file: Path
    freeze_file: Path
    warnings_file: Path
    simplified_log_file: Path


def test_artifact_paths(log_dir: Path, variant: str) -> TestArtifactPaths:
    """Return the standard artifact file paths for one test run variant.

    :param log_dir: Directory that holds artifacts for this package or run
                    (e.g. ``workdir/artifacts/<package_name>``).
    :param variant: ``"patched"`` or ``"original"``.
    """
    return TestArtifactPaths(
        log_file=log_dir / f"{variant}_log.log",
        status_file=log_dir / f"{variant}_status.json",
        freeze_file=log_dir / f"{variant}_freeze.txt",
        warnings_file=log_dir / f"{variant}_warnings.json",
        simplified_log_file=log_dir / f"{variant}_simplified_log.log",
    )


__all__ = ["process_warnings", "TestArtifactPaths", "test_artifact_paths"]
