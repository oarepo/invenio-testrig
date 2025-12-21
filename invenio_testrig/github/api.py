# -*- coding: utf-8 -*-
#
# Copyright (C) 2026 CESNET z.s.p.o.
#
# invenio-testrig is free software; you can redistribute it and/or
# modify it under the terms of the MIT License; see LICENSE file for more
# details.
"""GitHub API client wrapper for git operations.

This module provides a high-level interface for interacting with GitHub
repositories, including resolving references, fetching commits, managing
branches and tags, and handling pull requests.

Note that this module tries not to use GitHub's REST API directly for git operations,
but instead relies on caching and local git commands. This is because using the REST API
is rate limited to 1000-5000 requests per hour, which can be easily exceeded when
testing multiple packages with multiple references several times.
"""

import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, cast

from invenio_testrig.errors import PatchApplicationError
from invenio_testrig.github.cache import GitCache
from invenio_testrig.github.types import (
    GitReference,
    GitReferenceSchema,
    Patch,
    PullRequestInfo,
)
from invenio_testrig.utils import call_executable_quietly

log = logging.getLogger(__name__)


class GitApi:
    """High-level GitHub API client for git operations.

    Provides methods for resolving git references, cloning repositories,
    applying patches, and managing branches, tags, and pull requests.
    """

    def __init__(self, cache: GitCache):
        """Initialize GitApi with a cache instance.

        :param cache: GitCache instance for caching repository data
        """
        self._cache = cache

    def parse_reference(
        self, reference: str | GitReference | dict[str, Any]
    ) -> GitReference:
        """Parse a git reference string into a GitReference structure.

        Supported formats:
        - org/package@branch
        - org/package#pr_number
        - org/package@branch[base]
        - org/package[@branch|#pr]
        - package_name: org/package...
        - https://github.com/org/repo
        - https://github.com/org/repo/tree/branch-name
        - https://github.com/org/repo/pull/123

        Also pip-installed github references:
        - https://github.com/inveniosoftware/invenio-records-resources?branch=fix-read-many#c6b973a14802e2a7f73100ab4e32cb0c36bd4672
        - https://github.com/inveniosoftware/invenio-swh?rev=v0.13.4#828a3a415cf8e725c369939832b61281c44aec40
        In these two cases, fragments (sha commit) are not parsed because pip can use their obsolete version.


        :param reference: Git reference string to parse

        :return: Parsed GitReference structure

        :raises ValidationError: If the reference string is invalid
        """
        # Import here to avoid circular imports
        from invenio_testrig.github.ref_parser import parse_string_reference

        if isinstance(reference, str):
            parsed_reference = parse_string_reference(reference)
        elif isinstance(reference, dict):
            parsed_reference = cast(
                GitReference, GitReferenceSchema().load(cast(dict[str, Any], reference))
            )
        else:
            parsed_reference = reference

        return self.resolve_reference(parsed_reference)

    def parse_patch(self, patch: str | GitReference | dict[str, Any]) -> Patch:
        """Parse a patch reference string into a Patch structure.

        Patch reference is the same as GitReference, but might have an extra versions
        (version-range) after the reference. The versions always start with '>', '<' or '='
        and are separated by commas if there are multiple.
        """
        from invenio_testrig.github.ref_parser import parse_version_constraints

        versions_part = None
        if isinstance(patch, str):
            # Split the reference and the version constraints
            matches = re.match(r"(.*?)([><=!].*)$", patch)
            if matches:
                patch, versions_part = matches.groups()

        reference = self.parse_reference(patch)

        # Parse version constraints if present
        versions = []
        if versions_part:
            versions = parse_version_constraints(versions_part)

        return Patch(
            org=reference.org,
            repo=reference.repo,
            package=reference.package,
            branch=reference.branch,
            pr=reference.pr,
            base=reference.base,
            actual_version=reference.actual_version,
            pr_info=reference.pr_info,
            commit=reference.commit,
            versions=versions,
        )

    def get_commit(self, org: str, repo: str, branch_or_tag_or_commit: str) -> str:
        """Resolve any git reference to a commit SHA.

        The GitHub commits API accepts branch names, tag names, or direct commit
        SHAs. Using it ensures all reference types can be resolved uniformly.

        :param org: GitHub organization or user name
        :param repo: Repository name
        :param branch_or_tag_or_commit: Branch name, tag name, or commit SHA

        :return: The commit SHA referenced by the input
        """
        return self._cache.get_branch_commit(org, repo, branch_or_tag_or_commit)

    def resolve_pr(self, org: str, repo: str, pr_number: int) -> PullRequestInfo:
        """
        Resolve pull request information including source details and commits.

        :param org: GitHub organization or user name
        :param repo: Repository name
        :param pr_number: Pull request number

        :return: Dictionary containing source_org, source_repo, source_branch, and list of commit SHAs
        """
        return self._cache.get_pr_info(org, repo, pr_number)

    def get_default_branch(self, org: str, repo: str) -> str:
        """
        Get the default branch name for a repository.

        :param org: GitHub organization or user name
        :param repo: Repository name

        :return: The default branch name (e.g., "main", "master")
        """
        return self._cache.get_default_branch(org, repo)

    def get_branches(self, org: str, repo: str) -> list[str]:
        """
        Get branch names from the repository, sorted by most recently updated.

        :param org: GitHub organization or user name
        :param repo: Repository name

        :return: List of branch names sorted by most recent update
        """
        return self._cache.get_branches(org, repo)

    def _fill_pr_info(self, git_ref: GitReference) -> None:
        """Ensure git_ref.pr_info is populated from a PR number or a base-branch comparison."""
        if git_ref.pr is not None and git_ref.pr_info is None:
            git_ref.pr_info = self.resolve_pr(git_ref.org, git_ref.repo, git_ref.pr)
        if (
            git_ref.pr_info is None
            and git_ref.base is not None
            and git_ref.branch is not None
        ):
            git_ref.pr_info = self._cache.virtual_pr_info(
                git_ref.org,
                git_ref.repo,
                git_ref.branch,
                git_ref.base,
            )

    def resolve_reference(self, git_ref: GitReference) -> GitReference:
        """
        Resolve git reference details using GitHub API.

        Fills in missing commit SHA, PR info, and other details for a GitReference.
        If no branch or PR is specified, uses the default branch.

        :param git_ref: GitReference with at least org and repo populated

        :return: GitReference with commit SHA and other details filled in
        """
        self._fill_pr_info(git_ref)

        if git_ref.pr_info is not None and not git_ref.commit:
            git_ref.commit = (
                git_ref.pr_info.commits[-1] if git_ref.pr_info.commits else None
            )

        if not git_ref.commit:
            git_ref.commit = self.get_commit(
                git_ref.org,
                git_ref.repo,
                git_ref.branch or self.get_default_branch(git_ref.org, git_ref.repo),
            )

        git_ref.actual_version = self.get_last_version_before_commit(git_ref)
        git_ref.commits_from_version = self.get_commits_from_version(git_ref)

        return git_ref

    def clone_git_reference(
        self, reference: GitReference, output_directory: Path
    ) -> None:
        """
        Clone a git repository and checkout the specified commit or branch.

        If pr_info is present, clones from the PR's source repository (fork),
        otherwise clones from the original repository.

        :param reference: GitReference containing org, repo, branch, and commit information
        :param output_directory: Path where the repository should be cloned
        """
        if reference.pr_info:
            org = reference.pr_info.source_org
            repo = reference.pr_info.source_repo
        else:
            org = reference.org
            repo = reference.repo

        cache_path = self._cache.get_repository_path(org, repo)

        # use git to clone the repository from cache to the output directory
        shutil.copytree(cache_path, output_directory)

        # checkout the specified commit or branch
        checkout_target = reference.commit or reference.branch
        if checkout_target:
            call_executable_quietly(
                ["git", "checkout", checkout_target],
                cwd=output_directory,
            )

    def apply_pr_commits(self, directory: Path, reference: GitReference) -> None:
        """
        Apply commits from a pull request reference to an existing repository.

        Cherry-picks all commits from the PR into the current branch of the
        repository at the specified directory. If the PR is from a fork,
        fetches the commits from the fork first.

        :param directory: Path to the git repository where commits should be applied
        :param reference: GitReference that must contain PR information

        :raises ValueError: If the reference is not a PR (no pr_info)
        :raises subprocess.CalledProcessError: If git operations fail
        """
        self._validate_pr_reference(reference)
        assert reference.pr_info is not None  # Already validated

        commits = reference.pr_info.commits
        if not commits:
            return  # Nothing to apply

        remote_name, remote_url = self._setup_fork_remote(directory, reference)
        self._fetch_from_remote(directory, remote_name, remote_url, reference)
        self._cherry_pick_commits(directory, commits, reference)
        self._cleanup_remote(directory, remote_name)

    def _validate_pr_reference(self, reference: GitReference) -> None:
        """Validate that reference contains PR information.

        :param reference: GitReference to validate

        :raises ValueError: If the reference is not a PR
        """
        if not reference.pr_info:
            raise ValueError(
                f"Reference must be a pull request with pr_info, got {reference}"
            )

    def _setup_fork_remote(
        self, directory: Path, reference: GitReference
    ) -> tuple[str, str]:
        """Add fork as a git remote if it doesn't exist.

        :param directory: Path to the git repository
        :param reference: GitReference containing PR information

        :return: Tuple of ``(remote_name, remote_url)``

        :raises ValueError: If remote name cannot be determined
        """
        assert (
            reference.pr_info is not None
        )  # Already validated by _validate_pr_reference
        pr_info = reference.pr_info
        source_org = pr_info.source_org
        source_repo = pr_info.source_repo

        # Determine a unique remote name based on PR number or commit SHA
        if reference.pr:
            remote_identifier = str(reference.pr)
        elif pr_info.commits:
            remote_identifier = pr_info.commits[-1][:7]
        else:
            raise ValueError(
                f"Cannot determine remote name for PR {reference}, {reference.pr_info}"
            )

        remote_name = f"pr-{remote_identifier}-fork"
        remote_url = f"https://github.com/{source_org}/{source_repo}.git"

        # Try to add the remote (ignore if it already exists)
        try:
            call_executable_quietly(
                ["git", "remote", "add", remote_name, remote_url],
                cwd=directory,
            )
        except subprocess.CalledProcessError:
            # Remote might already exist, which is fine
            log.debug(f"Remote {remote_name} already exists or failed to add")

        return remote_name, remote_url

    def _fetch_from_remote(
        self,
        directory: Path,
        remote_name: str,
        remote_url: str,
        reference: GitReference,
    ) -> None:
        """Fetch commits from the fork remote.

        :param directory: Path to the git repository
        :param remote_name: Name of the remote to fetch from
        :param remote_url: URL of the remote repository
        :param reference: GitReference for error reporting

        :raises PatchApplicationError: If fetch fails
        """
        try:
            call_executable_quietly(
                ["git", "fetch", remote_name],
                cwd=directory,
            )
        except subprocess.CalledProcessError as e:
            error_msg = f"Failed to fetch from {remote_url}"
            raise PatchApplicationError(
                error_msg,
                patch_reference=reference,
                repository_path=directory,
            ) from e

    def _cherry_pick_commits(
        self, directory: Path, commits: list[str], reference: GitReference
    ) -> None:
        """Cherry-pick commits one by one.

        :param directory: Path to the git repository
        :param commits: List of commit SHAs to cherry-pick
        :param reference: GitReference for error reporting

        :raises PatchApplicationError: If cherry-pick fails
        """
        log.info(
            f"Applying {len(commits)} commits from PR #{reference} to {directory}..."
        )
        log.info("Commits to apply:")
        log.info(f"    {' '.join(commits)}")

        for commit_sha in commits:
            try:
                call_executable_quietly(
                    [
                        "git",
                        "cherry-pick",
                        "--allow-empty",
                        "--allow-empty-message",
                        "--empty=drop",
                        commit_sha,
                    ],
                    cwd=directory,
                )
            except subprocess.CalledProcessError as e:
                error_msg = f"Failed to cherry-pick commit {commit_sha}"
                raise PatchApplicationError(
                    error_msg,
                    patch_reference=reference,
                    repository_path=directory,
                ) from e

    def _cleanup_remote(self, directory: Path, remote_name: str) -> None:
        """Remove the temporary remote.

        :param directory: Path to the git repository
        :param remote_name: Name of the remote to remove
        """
        try:
            call_executable_quietly(
                ["git", "remote", "remove", remote_name],
                cwd=directory,
            )
        except subprocess.CalledProcessError:
            # If cleanup fails, it's not critical
            log.debug(f"Failed to remove remote {remote_name}, continuing anyway")

    def get_last_version_before_commit(self, ref: GitReference) -> str | None:
        """
        Get the last version tag that is an ancestor of the specified commit.

        This method returns the most recent version tag that is on the commit or
        before the specified commit in the commit history. If the latest release version
        is the same as the current commit, it will return the version on the commit.

        :param ref: GitReference containing repository and commit information
        :return: The last version tag before the specified commit, or None if no such version exists.
        """
        if ref.commit is None:
            raise ValueError(
                "GitReference must have a commit to find the last version before it."
            )
        return self._cache.get_last_version_on_or_before_commit(
            ref.org, ref.repo, ref.commit
        )

    def get_commits_from_version(self, ref: GitReference) -> list[str]:
        """
        Get a list of commit SHAs from the latest release version to the current commit.

        If a latest release version is found, returns the list of commits that are
        reachable from the current commit but not from the latest release tag.
        If no release version is present, returns an empty list.

        :param ref: GitReference containing repository and commit information
        :return: List of commit SHAs from the latest release version to the current commit,
            excluding the *actual_version* commit. Returns an empty list if the actual
            version equals the current commit. If no release version is found,
            returns all commits reachable from the current commit.
        """
        if ref.commit is None:
            raise ValueError(
                "GitReference must have a commit to find commits from version."
            )
        return self._cache.get_commits_from_version(
            ref.org,
            ref.repo,
            ref.commit,
            f"v{ref.actual_version}" if ref.actual_version else None,
        )

    def get_last_commits(
        self, repository_path: Path, number_of_commits: int
    ) -> list[tuple[str, str]]:
        """Get the last N commits from a repository.

        :param repository_path: Path to the repository directory
        :param number_of_commits: Number of recent commits to retrieve

        :return: List of ``(commit_hash, commit_message)`` tuples
        """
        try:
            result = subprocess.run(
                [
                    "git",
                    "log",
                    f"-{number_of_commits}",
                    "--pretty=format:%h|%s",
                ],
                cwd=repository_path,
                capture_output=True,
                text=True,
                check=True,
            )
            if not result.stdout:
                return []

            commits = []
            for line in result.stdout.splitlines():
                if "|" in line:
                    hash_part, message = line.split("|", 1)
                    commits.append((hash_part.strip(), message.strip()))
            return commits
        except subprocess.CalledProcessError:
            return []
