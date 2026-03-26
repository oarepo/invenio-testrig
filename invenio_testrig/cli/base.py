# -*- coding: utf-8 -*-
#
# Copyright (C) 2026 CESNET z.s.p.o.
#
# invenio-testrig is free software; you can redistribute it and/or
# modify it under the terms of the MIT License; see LICENSE file for more
# details.
"""Base CLI utilities and decorators.

Provides reusable decorators for CLI commands including configuration loading,
progress reporting, debug mode handling, and verbose logging setup.

Decorator stacking order
~~~~~~~~~~~~~~~~~~~~~~~~

The decorators below have implicit dependencies on kwargs injected by other
decorators. When composing them on a CLI command, they **must** be applied in
exactly this order (outermost decorator listed first)::

    @click.command(...)
    @with_config       # (optional) loads Config from workdir; requires ``progress``
    @click.option(...)  # any click options/arguments go here
    @with_verbose      # (optional) reads ``config.verbose``; requires ``config``
    @with_debug        # (optional) reads ``config.debug``; requires ``config`` and ``progress``
    @with_progress     # injects ``progress`` — should be the innermost decorator
    def my_command(config, progress, ...):
        ...

Because Python applies decorators bottom-up, ``@with_progress`` runs first
and injects ``progress``, then ``@with_debug`` and ``@with_verbose`` can
read ``config`` (injected by ``@with_config``), and ``@with_config`` itself
can consume the ``progress`` kwarg that ``@with_progress`` already provided.

Commands that do not need ``config`` (e.g. ``github_cmd``) only use
``@with_progress``.
"""

import contextlib
import functools
import logging
from pathlib import Path
from typing import Any, Callable, Generator

import click

from invenio_testrig.config import Config
from invenio_testrig.progress import Progress


@contextlib.contextmanager
def step_error_handler(
    debug: bool, progress: Progress, error_prefix: str = "Error"
) -> Generator[None, None, None]:
    """Context manager that converts unhandled exceptions into user-friendly errors.

    In debug mode the exception propagates unchanged so the full traceback is
    visible. Otherwise a formatted message is printed via *progress* and
    ``click.Abort`` is raised.

    :param debug: When ``True``, re-raise exceptions instead of formatting them.
    :param progress: Progress reporter used to display the error message.
    :param error_prefix: Human-readable prefix prepended to the error message.
    """
    try:
        yield
    except Exception as e:
        if debug:
            raise
        progress.error(f"{error_prefix}: {e}")
        raise click.Abort()


def with_debug(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator that adds debug option and handles exceptions.

    Requires ``config`` and ``progress`` kwargs (injected by ``@with_config``
    and ``@with_progress`` respectively). Must be stacked **after**
    ``@with_config`` and **before** ``@with_progress``.

    :param func: Function to wrap

    :return: Wrapped function that handles exceptions based on debug mode
    """

    @functools.wraps(func)
    def wrapper(*args: Any, config: Config, progress: Progress, **kwargs: Any) -> Any:
        with step_error_handler(config.debug, progress):
            return func(*args, config=config, progress=progress, **kwargs)

    return wrapper


def set_verbose():
    """Configure logging for verbose output."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


def with_verbose(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator that adds verbose option and configures logging.

    Requires ``config`` kwarg (injected by ``@with_config``). Must be
    stacked **after** ``@with_config`` and **before** ``@with_progress``.

    :param func: Function to wrap

    :return: Wrapped function that configures logging if verbose mode is enabled
    """

    @functools.wraps(func)
    def wrapper(*args: Any, config: Config, **kwargs: Any) -> Any:
        if config.verbose:
            set_verbose()
        return func(*args, config=config, **kwargs)

    return wrapper


def with_config(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator that loads config and passes it as config parameter.

    Requires ``progress`` kwarg (injected by ``@with_progress``). Must be
    the **outermost** project decorator (right after ``@click.command``).

    The config is supposed to always be loaded from a file named ``config.json``
    in the specified working directory. The setup_cmd creates this directory
    and places the config file there, so this is a safe assumption for all commands
    that run after setup.

    :param func: Function to wrap

    :return: Wrapped function with config argument added from workdir/config.json
    """

    @functools.wraps(func)
    def wrapper(*args: Any, progress: Progress, **kwargs: Any) -> Any:
        # Try to get config path from either config_json_path or config_path
        workdir = kwargs.pop("workdir", None)

        if workdir is None:
            progress.error("workdir is required but was not provided")
            raise click.Abort()

        return func(
            *args,
            config=Config.load(workdir / "config.json"),
            progress=progress,
            **kwargs,
        )

    click_arg = click.argument(
        "workdir",
        type=click.Path(exists=True, path_type=Path, resolve_path=True),
    )

    return click_arg(wrapper)


class ClickProgress(Progress):
    """Click-based progress reporter with emoticons and colors."""

    def start(self, message: str, icon: str | None = None) -> None:
        """Report the start of a new step in the testing process."""
        icon = icon or "🔄"
        click.secho(f"{icon} {message}", fg="cyan", bold=True)

    def info(self, message: str, icon: str | None = None) -> None:
        """Report informational messages about the testing process."""
        icon = icon or "ℹ️"
        click.secho(f"{icon}  {message}", fg="blue")

    def success(self, message: str, icon: str | None = None) -> None:
        """Report successful completion of a step in the testing process."""
        icon = icon or "✅"
        click.secho(f"{icon} {message}", fg="green", bold=True)

    def warning(self, message: str, icon: str | None = None) -> None:
        """Report a warning during the testing process."""
        icon = icon or "⚠️"
        click.secho(f"{icon}  {message}", fg="yellow", bold=True, err=True)

    def error(self, message: str, icon: str | None = None) -> None:
        """Report an error during the testing process."""
        icon = icon or "❌"
        click.secho(f"{icon} {message}", fg="red", bold=True, err=True)

    def text(
        self,
        message: str,
        fg: str | None = None,
        bold: bool = False,
        dim: bool = False,
    ) -> None:
        """Output styled text without semantic meaning."""
        click.secho(message, fg=fg, bold=bold, dim=dim)


def with_progress(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator that adds a ClickProgress instance and passes it as progress parameter.

    Has no dependencies on other decorators. Should be the **innermost**
    (bottom-most) project decorator so that ``progress`` is available to
    all outer decorators.

    :param func: Function to wrap

    :return: Wrapped function with progress argument added
    """

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        progress = ClickProgress()
        return func(*args, progress=progress, **kwargs)

    return wrapper
