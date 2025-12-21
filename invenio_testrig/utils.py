# -*- coding: utf-8 -*-
#
# Copyright (C) 2026 CESNET z.s.p.o.
#
# invenio-testrig is free software; you can redistribute it and/or
# modify it under the terms of the MIT License; see LICENSE file for more
# details.
"""Common utility functions and decorators.

This module provides shared utilities including the ExtensibleMixin class.
decorator for creating dataclasses that accept unknown keyword arguments,
and subprocess execution helpers.
"""

import logging
import subprocess
from dataclasses import dataclass, field, fields
from typing import Any

log = logging.getLogger(__name__)


@dataclass(init=False)
class ExtensibleMixin:
    """A mixin that adds an _extra field to store unknown kwargs."""

    _extra: dict[str, Any] = field(default_factory=dict)

    def __init__(self, **kwargs: Any):
        super().__init__()  # call dataclass __init__ to initialize fields with defaults

        _extra = kwargs.pop("_extra", {})
        known_fields = {f.name for f in fields(self) if f.init}
        for k, v in kwargs.items():
            if k in known_fields:
                setattr(self, k, v)
            else:
                _extra[k] = v
        self._extra = _extra


def extra_data(instance: Any) -> dict[str, Any]:
    """Get the extra data stored in an instance created by ExtensibleMixin.

    :param instance: Instance created with ExtensibleMixin

    :return: Dictionary of extra keyword arguments that were passed during initialization
    """
    return getattr(instance, "_extra", {})


def call_executable_quietly(
    cmd: list[str], always_quiet=False, **kwargs: Any
) -> tuple[str, str]:
    """Call an executable command quietly, capturing stdout and stderr.

    If the command fails (non-zero exit code), print the captured output and raise an exception.

    :param cmd: The command to execute as a list of strings
    :param always_quiet: If True, suppress error output even on command failure
    :param kwargs: Additional keyword arguments passed to :func:`subprocess.run`

    :return: A tuple of (stdout, stderr) captured from the command execution

    :raises subprocess.CalledProcessError: If the command exits with a non-zero status
    """

    log.info("%s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True, **kwargs)

    if result.returncode != 0:
        if not always_quiet:
            log.error("Command failed: %s", " ".join(cmd))
            log.error("CWD: %s", kwargs.get("cwd", "."))
            log.error("%s", result.stdout)
            log.error("%s", result.stderr)

        raise subprocess.CalledProcessError(
            result.returncode, cmd, output=result.stdout, stderr=result.stderr
        )

    return result.stdout, result.stderr
