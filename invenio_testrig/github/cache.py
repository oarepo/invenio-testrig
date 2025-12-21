# -*- coding: utf-8 -*-
#
# Copyright (C) 2026 CESNET z.s.p.o.
#
# invenio-testrig is free software; you can redistribute it and/or
# modify it under the terms of the MIT License; see LICENSE file for more
# details.
"""Git repository caching layer to minimize GitHub API calls.

This module provides a caching mechanism for git repository data using
local clones instead of API calls to avoid rate limiting. It caches
repository metadata, branch information, and pull request details.
"""

import json
import logging
import multiprocessing
import shutil
import subprocess
from pathlib import Path
from typing import Any

import semver

from invenio_testrig.github.types import PullRequestInfo
from invenio_testrig.types import Progress
from invenio_testrig.utils import call_executable_quietly

log = logging.getLogger(__name__)


class GitCache:
    """
    As github API calls are rate limited (at most 5000 per hour for authenticated requests),
    normal git operations (such as clone) are used to have the local state of the repository.
    These are not capped by the API rate limits.

    API calls (via the gh client) are used just to resolve PRs as they are not
    available via normal git operations.

    The cache will be stored in a temporary directory and will be cleared on initialization.
    It takes care of common operations:
    - Cloning repositories
    - Fetching branch commits
    - Getting default branch name
    - Fetching PR information
    - Getting names of branches and tags sorted by most recently updated
    """

    def __init__(self, cache_dir: Path):
        """Initialize the GitCache with a cache directory.

        :param cache_dir: Path to the directory where cached repository data will be stored
        """
        self._cache_dir = cache_dir.resolve()
        self._pr_cache: dict[tuple[str, str, int], PullRequestInfo] = {}

    @property
    def cache_dir(self) -> Path:
        """Get the path to the cache directory."""
        return self._cache_dir

    def clear_cache(self):
        """Clear the cache by removing all cached repositories.

        Removes the entire cache directory and clears in-memory PR cache.
        """
        log.info("Clearing git cache at %s", self._cache_dir)
        if self._cache_dir.exists():
            shutil.rmtree(self._cache_dir)
        self._pr_cache.clear()

    def get_repository_path(self, org: str, repo: str) -> Path:
        """Get the local cache path for the specified repository.

        :param org: GitHub organization or user name
        :param repo: Repository name

        :return: Path to the cached repository directory
        """
        return self._clone_repo(org, repo)

    def get_pr_info(self, org: str, repo: str, pr: int) -> PullRequestInfo:
        """Get cached PR information for the given reference.

        :param org: GitHub organization or user name
        :param repo: Repository name
        :param pr: Pull request number

        :return: PullRequestInfo containing source repository and commit details
        """
        if (org, repo, pr) not in self._pr_cache:
            self._prepare_pr(org, repo, pr)

        return self._pr_cache[(org, repo, pr)]

    def virtual_pr_info(
        self, org: str, repo: str, branch: str, base: str
    ) -> PullRequestInfo:
        """Generate a virtual PR info for a non-PR reference by comparing the branch with the base.

        :param org: GitHub organization or user name
        :param repo: Repository name
        :param branch: Branch name to compare
        :param base: Base branch name to compare against

        :return: PullRequestInfo with commits between base and branch (oldest to newest)
        """
        cache_path = self._clone_repo(org, repo)

        # Resolve base and branch to commit SHAs (handles branches, tags, and commits)
        base_commit = self.get_branch_commit(org, repo, branch=base)
        branch_commit = self.get_branch_commit(org, repo, branch=branch)

        # Get the list of commits between base and branch
        output, _ = call_executable_quietly(
            ["git", "rev-list", f"{base_commit}..{branch_commit}"],
            cwd=cache_path,
            always_quiet=True,  # Don't print errors if this fails
        )
        commits = output.strip().split("\n") if output.strip() else []
        return PullRequestInfo(
            source_org=org,
            source_repo=repo,
            source_branch=branch,
            commits=list(reversed(commits)),  # reverse to have oldest to newest
        )

    def get_branch_commit(self, org: str, repo: str, branch: str | None = None) -> str:
        """Get the latest commit SHA for the specified branch.

        :param org: GitHub organization or user name
        :param repo: Repository name
        :param branch: Branch name, tag name, or commit SHA. If None, uses HEAD

        :return: The commit SHA referenced by the branch/tag/commit

        :raises ValueError: If the branch/tag/commit cannot be resolved
        """
        cache_path = self._clone_repo(org, repo)
        ref = branch or "HEAD"

        # Try direct git rev-parse strategies first (fast path)
        commit = self._try_rev_parse_strategies(cache_path, ref)
        if commit:
            return commit

        # Fall back to parsing for-each-ref output (slower path)
        commit = self._try_for_each_ref_strategy(cache_path, ref)
        if commit:
            return commit

        # Special error message for HEAD resolution failure
        if branch is None:
            raise ValueError(f"Could not resolve default branch for {org}/{repo}")

        raise ValueError(f"Could not resolve ref '{ref}' for {org}/{repo}")

    def _try_rev_parse_strategies(self, cache_path: Path, ref: str) -> str | None:
        """Try to resolve reference using git rev-parse with multiple patterns.

        The ^{commit} suffix ensures we get a commit object, not an annotated tag object.

        :param cache_path: Path to the cached repository
        :param ref: Reference to resolve (branch, tag, or commit)

        :return: Commit SHA if successful, None otherwise
        """
        remotes = self._get_remote_names(cache_path)
        ref_patterns = self._build_rev_parse_patterns(ref, remotes)

        for pattern in ref_patterns:
            try:
                output, _ = call_executable_quietly(
                    ["git", "rev-parse", pattern],
                    cwd=cache_path,
                )
                return output.strip()
            except subprocess.CalledProcessError:
                continue

        return None

    def _get_remote_names(self, cache_path: Path) -> list[str]:
        """Get list of remote names from the repository.

        :param cache_path: Path to the cached repository

        :return: List of remote names, or empty list if retrieval fails
        """
        try:
            remotes_output, _ = call_executable_quietly(
                ["git", "remote"],
                cwd=cache_path,
            )
            return remotes_output.strip().split("\n") if remotes_output.strip() else []
        except subprocess.CalledProcessError:
            return []

    def _build_rev_parse_patterns(self, ref: str, remotes: list[str]) -> list[str]:
        """Build list of git reference patterns to try with rev-parse.

        :param ref: Reference to resolve
        :param remotes: List of remote names

        :return: List of reference patterns to try
        """
        patterns = [
            f"{ref}^{{commit}}",  # Direct ref (local branch, commit ID)
            f"refs/tags/{ref}^{{commit}}",  # Tag (annotated or lightweight)
            f"refs/heads/{ref}^{{commit}}",  # Explicit local branch ref
        ]

        # Add remote branch patterns for all remotes
        for remote in remotes:
            patterns.append(f"{remote}/{ref}^{{commit}}")

        return patterns

    def _try_for_each_ref_strategy(self, cache_path: Path, ref: str) -> str | None:
        """Try to resolve reference by parsing for-each-ref output.

        This is a slower fallback strategy when rev-parse doesn't work.

        :param cache_path: Path to the cached repository
        :param ref: Reference to resolve

        :return: Commit SHA if successful, None otherwise
        """
        try:
            output, _ = call_executable_quietly(
                [
                    "git",
                    "for-each-ref",
                    "--format=%(refname:short) %(objectname) %(*objectname)",
                    "refs/heads/",
                    "refs/tags/",
                    "refs/remotes/",
                ],
                cwd=cache_path,
            )

            remotes = self._get_remote_names(cache_path)
            return self._parse_for_each_ref_output(output, ref, remotes)
        except subprocess.CalledProcessError:
            return None

    def _parse_for_each_ref_output(
        self, output: str, ref: str, remotes: list[str]
    ) -> str | None:
        """Parse for-each-ref output to find matching reference.

        :param output: Output from git for-each-ref command
        :param ref: Reference to find
        :param remotes: List of remote names for matching remote branches

        :return: Commit SHA if found, None otherwise
        """
        for line in output.strip().split("\n"):
            parts = line.split()
            if len(parts) < 2:
                continue

            name = parts[0]
            # For annotated tags, %(*objectname) gives the dereferenced commit
            # For everything else, it's empty and %(objectname) is used
            commit = parts[2] if len(parts) > 2 and parts[2] else parts[1]

            # Match against the short name directly
            if name == ref:
                return commit

            # Try matching with any remote prefix stripped (e.g., "origin/main" matches "main")
            for remote in remotes:
                if name == f"{remote}/{ref}":
                    return commit

        return None

    def get_default_branch(self, org: str, repo: str) -> str:
        """Get the default branch name for the specified repository.

        :param org: GitHub organization or user name
        :param repo: Repository name

        :return: The default branch name (e.g., "main" or "master")
        """
        self.get_branch_commit(org, repo)  # Ensure cache is populated
        cache_path = self._clone_repo(org, repo)
        output, _ = call_executable_quietly(
            ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
            cwd=cache_path,
        )
        return output.strip().split("/")[-1]

    def get_branches(self, org: str, repo: str) -> list[str]:
        """Get branch names from the repository, sorted by most recently updated.

        :param org: GitHub organization or user name
        :param repo: Repository name

        :return: List of branch names sorted by commit date (most recent first)
        """
        self.get_branch_commit(org, repo)  # Ensure cache is populated
        cache_path = self._clone_repo(org, repo)
        output, _ = call_executable_quietly(
            [
                "git",
                "for-each-ref",
                "--sort=-committerdate",
                "--format=%(refname:short)",
                "refs/remotes/origin/",
            ],
            cwd=cache_path,
        )
        branches = output.strip().split("\n")
        return [
            branch.replace("origin/", "")
            for branch in branches
            if branch.startswith("origin/")
        ]

    def cache_repositories(
        self, repositories: list[tuple[str, str]], progress: Progress
    ) -> None:
        """Cache multiple repositories in parallel using multiprocessing.

        :param repositories: List of (org, repo) tuples to cache
        :param progress: Progress callback for status updates
        """
        if not repositories:
            return

        progress.info(
            f"Caching {len(repositories)} repositories in parallel...", icon="📦"
        )

        # Prepare arguments for the worker pool
        # Pass cache_dir path instead of GitCache instance to avoid pickling issues
        clone_args = [
            (self._cache_dir, org, repo, progress) for org, repo in repositories
        ]

        # Use multiprocessing to clone repositories in parallel
        # Use cpu_count for number of workers, but cap at reasonable limit
        num_workers = min(multiprocessing.cpu_count(), len(repositories), 8)

        with multiprocessing.Pool(processes=num_workers) as pool:
            pool.starmap(_clone_repo_worker, clone_args)

        progress.info(f"Finished caching {len(repositories)} repositories", icon="✅")

    def _prepare_pr(self, org: str, repo: str, pr: int) -> None:
        """Prepare the local cache for the given PR reference.

        Fetches PR information from GitHub API using the gh CLI tool and caches it.
        Note: This call uses the API in the background to resolve PR details.

        :param org: GitHub organization or user name
        :param repo: Repository name
        :param pr: Pull request number
        """
        # use the gh client to fetch information about the PR
        output, _ = call_executable_quietly(
            [
                "gh",
                "pr",
                "view",
                str(pr),
                "--repo",
                f"{org}/{repo}",
                "--json",
                "commits,headRepository,headRefName,headRepositoryOwner",
            ]
        )
        pr_info: Any = json.loads(output)
        head_org = pr_info["headRepositoryOwner"]["login"]
        head_repo = pr_info["headRepository"]["name"]
        head_branch = pr_info["headRefName"]
        commits = [commit["oid"] for commit in pr_info["commits"]]

        self._pr_cache[(org, repo, pr)] = PullRequestInfo(
            source_org=head_org,
            source_repo=head_repo,
            source_branch=head_branch,
            commits=commits,
        )

    def _clone_repo(self, org: str, repo: str) -> Path:
        """Clone the specified repository into the cache.

        If the repository is already cached, returns the existing cache path.

        :param org: GitHub organization or user name
        :param repo: Repository name

        :return: Path to the cached repository directory
        """
        repo_cache_path = self._cache_dir / org / repo
        if repo_cache_path.exists():
            return repo_cache_path

        repo_cache_path.parent.mkdir(parents=True, exist_ok=True)

        call_executable_quietly(
            [
                "gh",
                "repo",
                "clone",
                f"{org}/{repo}",
                str(repo_cache_path),
            ]
        )
        call_executable_quietly(
            [
                "git",
                "fetch",
                "--all",
            ],
            cwd=repo_cache_path,
        )

        return repo_cache_path

    def get_last_version_on_or_before_commit(
        self, org: str, repo: str, commit: str
    ) -> str | None:
        """Get the latest tag version that is reachable from the given commit.

        If the commit itself has a version tag, returns that tag.
        Otherwise, returns the latest version tag before the commit.

        :param org: GitHub organization or user name
        :param repo: Repository name
        :param commit: Commit SHA to find the version for

        :return: Version string (without 'v' prefix) or None if no version tag found
        """
        cache_path = self._clone_repo(org, repo)
        try:
            # First, check if the commit itself has a version tag
            output, _ = call_executable_quietly(
                [
                    "git",
                    "tag",
                    "--list",
                    "--points-at",
                    commit,
                ],
                cwd=cache_path,
                always_quiet=True,  # Don't print errors if this fails
            )
            tags_on_commit = output.strip().split("\n")
            tags_on_commit = [tag for tag in tags_on_commit if tag.startswith("v")]
            if tags_on_commit:
                # Sort version tags using semver and return the highest version
                def parse_version(tag: str) -> semver.Version:
                    try:
                        return semver.Version.parse(tag[1:])  # remove 'v' prefix
                    except ValueError:
                        # If parsing fails, return a very low version
                        return semver.Version(0, 0, 0)

                tags_on_commit.sort(key=parse_version, reverse=True)
                return tags_on_commit[0][1:]  # remove leading 'v' from tag

            # If no tag on the commit, find the last version before it
            output, _ = call_executable_quietly(
                [
                    "git",
                    "tag",
                    "--list",
                    "--sort=-v:refname",
                    f"--merged={commit}^",
                ],
                cwd=cache_path,
                always_quiet=True,  # Don't print errors if this fails
            )
            ret = output.strip().split("\n")
            ret = [tag for tag in ret if tag.startswith("v")]  # filter out empty lines
            ret = [tag[1:] for tag in ret]  # remove leading 'v' from tags
            if ret:
                return ret[0]
            return None
        except subprocess.CalledProcessError:
            return None

    def get_commits_from_version(
        self, org: str, repo: str, commit: str, previous_version: str | None = None
    ) -> list[str]:
        """Get the list of commits on the branch that are not in the given version.

        If the previous version is not given, return all commits reachable from the given commit.
        Otherwise, return only the commits between the previous version and the given commit,
        in chronological order (oldest first).

        If commit and the previous version are the same, return an empty list.
        If the previous version is not an ancestor of the commit, return an empty list.

        :param org: GitHub organization or user name
        :param repo: Repository name
        :param commit: Target commit SHA
        :param previous_version: Previous version tag (with or without 'v' prefix). If None, returns all commits from the target commit

        :return: List of commit SHAs in chronological order (oldest first)
        """
        cache_path = self._clone_repo(org, repo)
        try:
            output, _ = call_executable_quietly(
                [
                    "git",
                    "rev-list",
                    "--reverse",
                    f"{previous_version}..{commit}" if previous_version else commit,
                ],
                cwd=cache_path,
                always_quiet=True,  # Don't print errors if this fails
            )
            return output.strip().split("\n") if output.strip() else []
        except subprocess.CalledProcessError:
            return []


def _clone_repo_worker(
    cache_dir: Path, org: str, repo: str, progress: Progress
) -> None:
    """Worker function for parallel repository cloning.

    Reconstructs a GitCache from the cache_dir path to avoid pickling
    the full GitCache instance across process boundaries.

    :param cache_dir: Path to the cache directory
    :param org: GitHub organization
    :param repo: GitHub repository
    :param progress: Progress reporter
    """
    progress.info(f"Caching repository {org}/{repo}...", icon="📦")
    cache = GitCache(cache_dir)
    cache._clone_repo(org, repo)
