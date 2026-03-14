# -*- coding: utf-8 -*-
#
# Copyright (C) 2026 CESNET z.s.p.o.
#
# invenio-testrig is free software; you can redistribute it and/or
# modify it under the terms of the MIT License; see LICENSE file for more
# details.
"""Shared type definitions and protocols for invenio-testrig.

Defines core data structures used throughout the testing framework including
TestedPackageInfo for package metadata, ExecutionStatus for test results,
ReportPackageData for report generation, and the Progress protocol for
status reporting during test execution.
"""

from dataclasses import dataclass, field
from typing import Protocol

from invenio_testrig.github.types import GitReference


@dataclass
class TestedPackageInfo:
    """Information about a package that is being tested, derived from the github configuration.

    This one is generated automatically based on the github configuration
    and the dependencies, so it is not extensible.
    """

    reference: GitReference
    test: list[str]
    extras: list[str]
    freeze: list[str]
    patches: list[GitReference] = field(default_factory=list)
    unpatched_reference: GitReference | None = None
    patched_reference: GitReference | None = None
    slow_split: list[str] | None = None


@dataclass
class ExecutionStatus:
    """Execution status for a tested package."""

    status: str  # e.g. "passed", "failed", "skipped" or "pending"
    package: TestedPackageInfo
    dependencies: list[TestedPackageInfo] = field(default_factory=list)

    @property
    def package_name(self) -> str:
        """Return the package name of the base reference.

        :return: Package name from the reference
        """
        return self.package.reference.package


@dataclass
class ReportPackageData:
    """Data structure for package test results in reports."""

    info: TestedPackageInfo
    artefact_dir: str  # directory name relative to the report where the artefacts for this package are stored (e.g. logs, status files, etc.)
    patched: ExecutionStatus
    original: ExecutionStatus


class Progress(Protocol):
    """Protocol for reporting progress of the testing process."""

    def start(self, message: str, icon: str | None = None) -> None:
        """Report the start of a new step in the testing process.

        :param message: Status message to display
        :param icon: Optional icon/emoji to display with the message
        """
        ...

    def info(self, message: str, icon: str | None = None) -> None:
        """Report informational messages about the testing process.

        :param message: Informational message to display
        :param icon: Optional icon/emoji to display with the message
        """
        ...

    def success(self, message: str, icon: str | None = None) -> None:
        """Report successful completion of a step in the testing process.

        :param message: Success message to display
        :param icon: Optional icon/emoji to display with the message
        """
        ...

    def warning(self, message: str, icon: str | None = None) -> None:
        """Report a warning during the testing process.

        :param message: Warning message to display
        :param icon: Optional icon/emoji to display with the message
        """
        ...

    def error(self, message: str, icon: str | None = None) -> None:
        """Report an error during the testing process.

        :param message: Error message to display
        :param icon: Optional icon/emoji to display with the message
        """
        ...

    def text(
        self,
        message: str,
        fg: str | None = None,
        bold: bool = False,
        dim: bool = False,
    ) -> None:
        """Output styled text without semantic meaning.

        Use for structured summaries, tables, and other formatted output
        that doesn't fit the start/info/success/warning/error categories.

        :param message: Text to display
        :param fg: Foreground color name (e.g. "green", "cyan", "yellow")
        :param bold: Whether to render in bold
        :param dim: Whether to render dimmed
        """
        ...
