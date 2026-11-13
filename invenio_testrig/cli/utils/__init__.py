# -*- coding: utf-8 -*-
#
# Copyright (C) 2026 CESNET z.s.p.o.
#
# invenio-testrig is free software; you can redistribute it and/or
# modify it under the terms of the MIT License; see LICENSE file for more
# details.
"""Shared utilities for CLI commands."""

import shlex
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

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
    junit_file: Path


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
        junit_file=log_dir / f"{variant}_junit.xml",
    )


def junit_env(
    junit_file: Path, base_env: dict[str, str] | None = None
) -> dict[str, str]:
    """Return environment that makes pytest write a JUnit XML report.

    pytest appends ``PYTEST_ADDOPTS`` to its command line, so this works for any
    test command that ends up invoking pytest (``./run-tests.sh``, ``./run.sh
    test``, ...) without modifying the tested repository. Options already set in
    the configuration are preserved.

    Note that pytest overwrites the file on every session, so if the test command
    runs pytest more than once, only the last session is reported.

    :param junit_file: Path of the JUnit XML file to write.
    :param base_env: Environment from the configuration, which may already
                     contain ``PYTEST_ADDOPTS``.
    """
    existing = (base_env or {}).get("PYTEST_ADDOPTS", "").strip()
    addopts = f"{existing} --junit-xml={shlex.quote(str(junit_file))}".strip()
    return {"PYTEST_ADDOPTS": addopts}


JUNIT_MAX_VALUE_LENGTH = 10_000
"""Longest attribute value / text kept in a JUnit report, longer ones are truncated."""


def _truncate(value: str | None, limit: int) -> str | None:
    if value is None or len(value) <= limit:
        return value
    return f"{value[:limit]}... [truncated {len(value) - limit} characters]"


def shrink_junit_file(
    junit_file: Path, limit: int = JUNIT_MAX_VALUE_LENGTH
) -> bool:
    """Truncate oversized attribute values and texts in a JUnit XML report.

    pytest embeds the parameters of parametrized tests in the test name. When a
    test is parametrized with large ``bytes`` (e.g. invenio-s3 uses tens of MB
    of ``os.urandom`` data), the report grows to hundreds of MB, which can not be
    pushed to GitHub (100 MB file limit) and is useless anyway.

    :param junit_file: The report to shrink in place. Missing files are ignored.
    :param limit: Maximum length of any attribute value or text.
    :return: True if the file was modified.
    """
    if not junit_file.exists():
        return False
    try:
        tree = ElementTree.parse(junit_file)
    except ElementTree.ParseError:
        return False

    changed = False
    for element in tree.iter():
        for key, value in element.attrib.items():
            truncated = _truncate(value, limit)
            if truncated is not value:
                element.set(key, truncated)
                changed = True
        for attr in ("text", "tail"):
            value = getattr(element, attr)
            truncated = _truncate(value, limit)
            if truncated is not value:
                setattr(element, attr, truncated)
                changed = True

    if changed:
        tree.write(junit_file, encoding="utf-8", xml_declaration=True)
    return changed


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
    "junit_env",
    "shrink_junit_file",
    "TestArtifactPaths",
    "test_artifact_paths",
    "test_run_context",
]
