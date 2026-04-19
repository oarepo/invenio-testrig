# -*- coding: utf-8 -*-
#
# Copyright (C) 2026 CESNET z.s.p.o.
#
# invenio-testrig is free software; you can redistribute it and/or
# modify it under the terms of the MIT License; see LICENSE file for more
# details.
"""Shared utilities for CLI commands."""

import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from invenio_testrig.cli.utils.log_processing import process_warnings
from invenio_testrig.config import TestedPackageInfo
from invenio_testrig.progress import Progress
from invenio_testrig.report import ExecutionStatus, save_execution_status


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


@contextmanager
def test_run_context(
    paths: TestArtifactPaths,
    package_info: TestedPackageInfo,
    dependencies: list[TestedPackageInfo],
    timeout_cmd: list[str] | str,
    timeout_minutes: int | None,
    label: str,
    progress: Progress,
):
    """Context manager for the boilerplate surrounding every test run.

    Eliminates the repeated try/except/process_warnings/save_execution_status
    pattern that every test runner (package, repo, e2e) needs. The caller puts
    only the actual test-running logic inside the ``with`` block; this context
    manager handles outcome recording unconditionally.

    On normal exit: saves ``"success"`` status and processes warnings.
    On :exc:`subprocess.CalledProcessError`: logs the failure, saves
    ``"failed"`` status, processes warnings, and re-raises.
    On :exc:`subprocess.TimeoutExpired`: logs the timeout, saves ``"failed"``
    status, processes warnings, and raises a ``CalledProcessError(-1)`` so
    callers see a uniform exception type.

    :param paths: Artifact file paths for this run variant.
    :param package_info: Package metadata written into the status file.
    :param dependencies: Patched dependency list written into the status file.
    :param timeout_cmd: Command to embed in the synthetic ``CalledProcessError``
                        raised on timeout (for upstream error context).
    :param timeout_minutes: Timeout value used in the human-readable log message.
    :param label: Short description used in error messages,
                  e.g. ``"repository"`` or ``"package 'foo'"``.
    :param progress: Progress reporter.
    """
    try:
        yield
    except subprocess.CalledProcessError as e:
        progress.error(f"Tests failed for {label} with exit code {e.returncode}")
        progress.info(f"Check the output log at: {paths.log_file}", icon="💡")
        process_warnings(paths.log_file, paths.warnings_file, paths.simplified_log_file)
        save_execution_status(
            paths.status_file,
            ExecutionStatus(status="failed", package=package_info, dependencies=dependencies),
        )
        raise
    except subprocess.TimeoutExpired:
        progress.error(f"Tests timed out for {label} after {timeout_minutes} minutes")
        progress.info(f"Check the output log at: {paths.log_file}", icon="💡")
        process_warnings(paths.log_file, paths.warnings_file, paths.simplified_log_file)
        save_execution_status(
            paths.status_file,
            ExecutionStatus(status="failed", package=package_info, dependencies=dependencies),
        )
        raise subprocess.CalledProcessError(returncode=-1, cmd=timeout_cmd)
    else:
        process_warnings(paths.log_file, paths.warnings_file, paths.simplified_log_file)
        save_execution_status(
            paths.status_file,
            ExecutionStatus(status="success", package=package_info, dependencies=dependencies),
        )


__all__ = [
    "process_warnings",
    "TestArtifactPaths",
    "test_artifact_paths",
    "test_run_context",
]
