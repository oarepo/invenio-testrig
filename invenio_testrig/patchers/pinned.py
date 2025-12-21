# -*- coding: utf-8 -*-
#
# Copyright (C) 2026 CESNET z.s.p.o.
#
# invenio-testrig is free software; you can redistribute it and/or
# modify it under the terms of the MIT License; see LICENSE file for more
# details.
"""Pinned version patching strategies.

This module provides patchers that use exact pinned commits for both
unpatched and patched versions, with either overwrite or rebase modes.
"""

from invenio_testrig.github.types import GitReference
from invenio_testrig.patchers.base import Patcher
from invenio_testrig.types import TestedPackageInfo


class PinnedRebasePatcher(Patcher):
    """Patcher for pinned repositories with rebase.

    For packages with patches:
    - Uses the pinned branch for the unpatched version
    - Expects one or more patches per package, applied on top of the pinned branch for the patched version
    """

    def _build_unpatched_reference(
        self, package_name: str, package_info: TestedPackageInfo
    ) -> GitReference:
        """Build GitReference for the unpatched version of the dependency.

        :param package_name: Name of the package
        :param package_info: Information about the tested package

        :return: GitReference pointing to the exact commit specified in the configuration
        """
        return GitReference(
            org=package_info.reference.org,
            repo=package_info.reference.repo,
            package=package_name,
            commit=package_info.reference.commit,
        )
