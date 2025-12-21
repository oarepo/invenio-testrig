# -*- coding: utf-8 -*-
#
# Copyright (C) 2026 CESNET z.s.p.o.
#
# invenio-testrig is free software; you can redistribute it and/or
# modify it under the terms of the MIT License; see LICENSE file for more
# details.
"""Type definitions for GitHub-related structures.

Defines data classes for git references (GitReference), version constraints
(VersionConstraint), pull request information (PullRequestInfo), and patch
configurations (Patch), along with their Marshmallow schemas for serialization.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

import semver
from marshmallow_dataclass import class_schema

log = logging.getLogger(__name__)


@dataclass
class VersionConstraint:
    """Version constraint with operator and version string."""

    operator: str
    version: str

    def applies_to(self, version_str: str) -> bool:
        """Check if this version constraint applies to a given version.

        :param version_str: Version string to check (e.g., '1.2.3')

        :return: True if the constraint is satisfied, False otherwise

        :raises ValueError: If the operator is not supported
        :raises ValueError: If version strings cannot be parsed as semantic versions
        """
        self_version = semver.Version.parse(self.version)
        tested_version = semver.Version.parse(version_str)

        if self.operator == "==":
            return tested_version == self_version
        elif self.operator == ">=":
            return tested_version >= self_version
        elif self.operator == "<=":
            return tested_version <= self_version
        elif self.operator == ">":
            return tested_version > self_version
        elif self.operator == "<":
            return tested_version < self_version
        elif self.operator == "!=":
            return tested_version != self_version
        raise ValueError(f"Unsupported operator {self.operator} in version constraint")


@dataclass
class PullRequestInfo:
    """Pull request information."""

    source_org: str
    source_repo: str
    source_branch: str
    commits: list[str]
    """Commits included in the PR, as a list of commit SHAs. 
    
       The order is oldest to newest.
    """


@dataclass
class GitReference:
    """Parsed git reference structure.

    Used for both git references with package metadata (with base, versions, pr_info)
    and simple git repository references (where base and pr_info may be None).
    The package field is always populated, defaulting to the repo name if not explicitly specified.
    """

    org: str
    """GitHub organization or user name."""

    repo: str
    """GitHub repository name."""

    package: str
    """Python package name that corresponds to this git repository."""

    branch: str | None = None
    """Branch name, if specified."""

    pr: int | None = None
    """Pull request number, if specified."""

    base: str | None = None
    """Base of the branch, if specified. 
    Commits between base and branch are considered for patch applicability."""

    pr_info: PullRequestInfo | None = None
    """Detailed information about the pull request, if this reference corresponds to a PR."""

    commit: str | None = None
    """Specific commit SHA, if specified. This is resolved from a branch or PR reference 
    to point to the exact commit being tested."""

    actual_version: str | None = None
    """The actual version of the package at the specified commit. 
    This is resolved from the git reference as the latest vx.y.z tag reachable from the commit."""

    commits_from_version: list[str] | None = None
    """List of commit SHAs from the latest version tag to the specified commit, 
    in order from oldest to newest."""

    def __str__(self) -> str:
        """Return a string representation of the git reference.

        :return: String in format 'org/repo[@branch][#pr][[base]]'
        """
        ref = f"{self.org}/{self.repo}"
        if self.branch:
            ref += f"@{self.branch}"
        if self.pr is not None:
            ref += f"#{self.pr}"
        if self.base:
            ref += f"[{self.base}]"
        return ref

    def to_dict(self) -> dict[str, Any]:
        """Convert the GitReference to a dictionary.

        :return: Dictionary representation with all GitReference fields
        """
        return GitReferenceSchema().dump(self)

    @property
    def github_url(self) -> str:
        """Construct the GitHub URL for this reference.

        :return: Full GitHub URL pointing to the repository, branch, or pull request
        """
        url = f"https://github.com/{self.org}/{self.repo}"
        if self.branch:
            url += f"/tree/{self.branch}"
        elif self.pr is not None:
            url += f"/pull/{self.pr}"
        return url


@dataclass
class Patch(GitReference):
    """Patch information, extending GitReference with patch-specific behaviour."""

    versions: list[VersionConstraint] = field(default_factory=list)

    def applies_to(self, another: GitReference) -> bool:
        """Check if this patch applies to another reference.

        A patch applies if it targets the same package and all version constraints
        are satisfied by the target reference's actual version.

        :param another: GitReference to check applicability against

        :return: True if the patch should be applied to the reference, False otherwise
        """
        if self.package != another.package:
            return False

        # If no version constraints, it applies to any reference with the same package
        if not self.versions:
            return True

        # If the other reference doesn't have an actual version, applicability cannot be determined
        if not another.actual_version:
            log.error(
                f"Cannot determine if patch {self} applies to {another} because the other reference does not have an actual version resolved."
            )
            return False
        # Check if any of the version constraints match the other reference's actual version
        for constraint in self.versions:
            if not constraint.applies_to(another.actual_version):
                return False
        return True

    def to_dict(self) -> dict[str, Any]:
        """Convert the Patch to a dictionary.

        :return: Dictionary representation with all Patch fields including version constraints
        """
        return PatchSchema().dump(self)


GitReferenceSchema = class_schema(GitReference)
PatchSchema = class_schema(Patch)
