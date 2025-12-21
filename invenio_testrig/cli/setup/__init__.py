# -*- coding: utf-8 -*-
#
# Copyright (C) 2026 CESNET z.s.p.o.
#
# invenio-testrig is free software; you can redistribute it and/or
# modify it under the terms of the MIT License; see LICENSE file for more
# details.
"""Configuration setup and package preparation command.

Provides the setup command that initializes the testing environment by loading
configuration, resolving repositories, determining dependency graphs, and
preparing patched repositories for testing.
"""

from invenio_testrig.cli.setup.command import setup_cmd

__all__ = ["setup_cmd"]
