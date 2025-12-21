# -*- coding: utf-8 -*-
#
# Copyright (C) 2026 CESNET z.s.p.o.
#
# invenio-testrig is free software; you can redistribute it and/or
# modify it under the terms of the MIT License; see LICENSE file for more
# details.
"""Patcher implementations for applying patches to repositories.

This module provides different patching strategies for managing repository
versions with and without patches, including pinned and upstream modes.
"""

from invenio_testrig.patchers.base import Patcher
from invenio_testrig.patchers.pinned import PinnedRebasePatcher
from invenio_testrig.patchers.upstream import UpstreamRebasePatcher

patchers_by_mode: dict[str, type[Patcher]] = {
    "pinned": PinnedRebasePatcher,
    "upstream": UpstreamRebasePatcher,
}

__all__ = [
    "Patcher",
    "UpstreamRebasePatcher",
    "PinnedRebasePatcher",
    "patchers_by_mode",
]
