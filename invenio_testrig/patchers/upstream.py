# -*- coding: utf-8 -*-
#
# Copyright (C) 2026 CESNET z.s.p.o.
#
# invenio-testrig is free software; you can redistribute it and/or
# modify it under the terms of the MIT License; see LICENSE file for more
# details.
"""Upstream branch patching strategies.

This module provides patchers that use the upstream default branch for
unpatched versions and apply patches on top, with either overwrite or rebase modes.
"""

from invenio_testrig.config import TestedPackageInfo
from invenio_testrig.patchers.base import Patcher
from invenio_testrig.types import GitReference


class UpstreamRebasePatcher(Patcher):
    """Patcher for upstream repositories with rebase.

    For packages with patches:
    - Uses the upstream branch (normally master) for the unpatched version
    - Expects one or more patches per package, and applies them on top of the upstream branch for the patched version
    """

    def _build_unpatched_reference(
        self, package_name: str, package_info: TestedPackageInfo
    ) -> GitReference:
        """Build GitReference for the unpatched version of the dependency.

        :param package_name: Name of the package
        :param package_info: Information about the tested package

        :return: GitReference pointing to the default branch (no specific commit)
        """
        return GitReference(
            org=package_info.reference.org,
            repo=package_info.reference.repo,
            package=package_name,
            branch=package_info.github_entry.default_branch,
        )
