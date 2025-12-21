# -*- coding: utf-8 -*-
#
# Copyright (C) 2026 CESNET z.s.p.o.
#
# invenio-testrig is free software; you can redistribute it and/or
# modify it under the terms of the MIT License; see LICENSE file for more
# details.
"""Custom exception classes for invenio-testrig."""

from pathlib import Path
from typing import Any


class PatchApplicationError(Exception):
    """Exception raised when a patch cannot be applied to a repository.

    This exception wraps git errors that occur during patch application,
    providing additional context about which patch failed and to which repository.

    :ivar message: Human-readable error message
    :ivar patch_reference: The GitReference (patch) that failed to apply
    :ivar repository_path: Path to the repository where the patch failed
    """

    def __init__(
        self,
        message: str,
        patch_reference: Any = None,
        repository_path: Path | None = None,
    ):
        """Initialize PatchApplicationError with context information.

        :param message: Human-readable error message
        :param patch_reference: The GitReference (patch) that failed to apply
        :param repository_path: Path to the repository where the patch failed
        """
        super().__init__(message)
        self.message = message
        self.patch_reference = patch_reference
        self.repository_path = repository_path

    def __str__(self):
        """Return a formatted error message with all context.

        :return: Multi-line string with error message, patch reference, and repository path
        """
        parts = [self.message]
        if self.patch_reference:
            parts.append(f"Patch: {self.patch_reference}")
        if self.repository_path:
            parts.append(f"Repository: {self.repository_path}")
        return "\n".join(parts)
