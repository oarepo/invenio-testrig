# -*- coding: utf-8 -*-
#
# Copyright (C) 2026 CESNET z.s.p.o.
#
# invenio-testrig is free software; you can redistribute it and/or
# modify it under the terms of the MIT License; see LICENSE file for more
# details.
"""Hook execution system for running custom Python functions at workflow stages.

Hooks allow users to inject custom Python code at specific points during the
testrig workflow, enabling configuration modifications and custom processing.
"""

import importlib.metadata as importlib_metadata
import logging
from typing import Any, Callable, Generator, cast

from invenio_testrig.config import Config

logger = logging.getLogger(__name__)


def _iter_hooks(
    hook_name: str,
) -> Generator[tuple[str, Callable[..., None]], None, None]:
    """Iterate over hooks from the entrypoints.

    Hooks are named as ``invenio_testrig.hooks.<hook_name>``.

    :param hook_name: Name of the hook to load

    :return: ``(entry_point_name, callable)`` tuple for each matching entry point.
    :rtype: Generator[tuple[str, Callable[..., None]], None, None]
    """
    for ep in importlib_metadata.entry_points(group="invenio_testrig.hooks"):
        if ep.name == hook_name:
            yield ep.name, cast(Callable[..., None], ep.load())


def run_hook(
    config: Config,
    hook_name: str,
    **extra_parameters: Any,
) -> None:
    """Run a hook by name.

    A hook is a Python function registered via entry points that is called
    at a specific point during the testing process. The hook function receives
    the config object and any extra parameters.

    :param config: Configuration object
    :param hook_name: Name of the hook to execute
    :param extra_parameters: Additional keyword arguments to pass to the hook function
    """
    for hook_ep_name, hook_func in _iter_hooks(hook_name):
        logger.info("Running hook %s", hook_ep_name)

        # Call the hook function with config, config_path, and other parameters
        hook_func(
            config=config,
            **extra_parameters,
        )
