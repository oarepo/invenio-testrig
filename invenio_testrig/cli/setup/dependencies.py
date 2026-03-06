# -*- coding: utf-8 -*-
#
# Copyright (C) 2026 CESNET z.s.p.o.
#
# invenio-testrig is free software; you can redistribute it and/or
# modify it under the terms of the MIT License; see LICENSE file for more
# details.
"""Dependency collection, package filtering, and patch selection."""

import os
import re
import tempfile
from pathlib import Path

from invenio_testrig.config import Config, Github
from invenio_testrig.errors import PatchApplicationError
from invenio_testrig.github import GitApi, GitCache, GitReference
from invenio_testrig.hooks import run_hook
from invenio_testrig.python_api import PythonAPI
from invenio_testrig.types import Progress, TestedPackageInfo

# region Dependency Collection


def collect_dependencies(
    config: Config,
    uv_executable: str,
    python_version: str,
    ignore_uv_lock: bool,
    progress: Progress,
) -> None:
    """Collect dependencies/libraries for the repository.

    Clones the repository, installs it (if uv.lock is not found),
    and collects dependencies. Updates the config JSON with a "packages" key
    containing all detected dependencies and their versions.

    :param config: Config object
    :param uv_executable: Path to uv executable
    :param python_version: Python version to use
    :param ignore_uv_lock: Whether to ignore uv.lock file
    :param progress: Progress reporter for status updates
    """
    git_ref = config.seed_repository.git
    git_api = GitApi(GitCache(config.workdir_path("git_cache")))

    # Clone the repository to a temporary directory
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir) / "repo"
        progress.start("Cloning invenio repository...", icon="🔄")
        git_api.clone_git_reference(git_ref, repo_path)

        run_hook(
            config,
            "after_invenio_repo_clone",
            repository_path=repo_path,
        )
        # Install and get dependencies
        progress.start(
            "Collecting dependencies (might take a while as the repository might be installed)...",
            icon="📦",
        )
        python_api = PythonAPI(uv_executable, python_version)
        dependencies = python_api.get_dependencies(
            repo_path, ignore_uv_lock=ignore_uv_lock
        )

    # Add dependencies to the config
    config.packages = dependencies

    run_hook(
        config,
        "after_dependencies_collected",
    )

    progress.success(
        f"Collected {len(dependencies)} dependencies and updated {config.config_path}"
    )


# endregion


# region Package Filtering


def _resolve_package_reference(
    git_api: GitApi,
    github_entry: Github,
    package_name: str,
    version: str,
) -> GitReference:
    """Build and resolve a GitReference for the given package and version string."""
    if version.startswith("https://"):
        reference = git_api.parse_reference(version)
    else:
        reference = GitReference(
            org=github_entry.org or "",
            repo=package_name,
            package=package_name,
            branch=f"v{version}",
        )
    return git_api.resolve_reference(reference)


def _find_git_repository_config(config: Config, package_name: str) -> Github | None:
    """Find the matching GitHub configuration entry for a package.

    :param config: Configuration object
    :param package_name: Name of the package to find configuration for

    :return: Matching Github configuration entry or None if no match found
    """
    for github_entry in config.github or []:
        exclude_patterns = github_entry.exclude or []

        # Check if package matches any include pattern
        if not any(
            re.match(pattern, package_name, re.IGNORECASE)
            for pattern in github_entry.include or []
        ):
            continue

        # Check if package matches any exclude pattern
        if any(
            re.match(pattern, package_name, re.IGNORECASE)
            for pattern in exclude_patterns
        ):
            continue
        return github_entry
    return None


def filter_packages(
    config: Config,
    progress: Progress,
) -> None:
    """Filter dependencies based on GitHub include/exclude patterns.

    Reads packages and filters entries based on github.include and
    github.exclude patterns inside the config file. Creates a new
    "tested_packages" key with matching entries. For each matched package,
    get the branch name and potential commit.

    The version might be:
    - semver version (e.g. 1.2.3). The branch name is v<version> (e.g. v1.2.3)
    - full github url (e.g.https://github.com/inveniosoftware/invenio-swh?rev=v0.13.4#<hash>
      or https://github.com/inveniosoftware/invenio-records-resources?branch=fix-read-many#<hash>)

    :param config: Config object
    :param progress: Progress reporter for status updates

    :raises ValueError: If no packages exist in config
    """
    git_cache = GitCache(config.workdir_path("git_cache"))
    git_api = GitApi(git_cache)

    run_hook(
        config,
        "before_filtering_packages",
    )

    # Check if packages exists
    if not config.packages:
        raise ValueError("No packages in config")

    packages_map = config.packages

    # Filter dependencies based on github patterns
    tested_packages: dict[str, TestedPackageInfo] = {}

    github_entries: dict[str, Github] = {}

    for package_name, version in packages_map.items():
        # Check each github config entry
        github_entry = _find_git_repository_config(config, package_name)
        if github_entry:
            github_entries[package_name] = github_entry

    git_cache.cache_repositories(
        [
            (
                github_entry.org,
                package_name,
            )
            for package_name, github_entry in github_entries.items()
        ],
        progress,
    )

    for package_name, version in packages_map.items():
        # Check each github config entry
        if package_name not in github_entries:
            continue
        github_entry = github_entries[package_name]
        progress.info(
            f"Adding package {package_name} to a set of tested packages ...", icon="🔍"
        )

        reference = _resolve_package_reference(
            git_api, github_entry, package_name, version
        )
        progress.info(
            f" ... resolved reference: {str(reference)}, commit {reference.commit}"
        )

        # Package matches this github config
        tested_packages[package_name] = TestedPackageInfo(
            reference=reference,
            test=github_entry.test,
            extras=github_entry.extras or [],
            freeze=github_entry.freeze or [],
        )

    for patch in config.patches:
        if patch.package in tested_packages:
            continue
        github_entry = _find_git_repository_config(config, patch.package)
        if not github_entry:
            raise ValueError(
                f"Patch {patch} applies to package {patch.package} which is not included "
                "in the tested packages and does not match any GitHub configuration entry."
            )
        tested_packages[package_name] = TestedPackageInfo(
            reference=patch,
            test=github_entry.test,
            extras=github_entry.extras or [],
            freeze=github_entry.freeze or [],
        )

    # Add tested packages to the config
    config.tested_packages = tested_packages

    run_hook(
        config,
        "after_filtering_packages",
    )

    progress.success(
        f"Filtered {len(tested_packages)} packages from {len(packages_map)} "
        f"total dependencies and updated {config.config_path}"
    )


# endregion


# region Patch Selection


def select_patches(
    config: Config,
    progress: Progress,
) -> None:
    """Select patches for the filtered out packages.

    Reads tested_packages and for each package, checks if there are any patches
    that match the package name. If there are, adds them to the config under a new
    "patches" key for each package. This will be used in the cloning step to determine
    which packages need to be cloned with patches applied.

    :param config: Config object
    :param progress: Progress reporter for status updates
    """
    run_hook(
        config,
        "before_selecting_patches",
    )

    # Check if patches exists
    if not config.patches:
        progress.warning("No patches in config, will skip patch selection")
        return

    applied_patches_count = 0
    applied_packages_count = 0
    for tested_package_name, tested_package_info in config.tested_packages.items():
        matching_patches = [
            patch
            for patch in config.patches
            if patch.applies_to(tested_package_info.reference)
        ]

        run_hook(
            config,
            "selecting_package_patch",
            package_name=tested_package_name,
            package_info=tested_package_info,
            matching_patches=matching_patches,
        )
        tested_package_info.patches = matching_patches
        if matching_patches:
            applied_packages_count += 1
            applied_patches_count += len(matching_patches)
            progress.info(
                f"Selected {', '.join(str(patch) for patch in matching_patches)} for package {tested_package_name}",
                icon="📌",
            )

    run_hook(
        config,
        "after_selecting_patches",
    )

    progress.success(
        f"Selected {applied_patches_count} patches to apply to {applied_packages_count} packages"
    )


def report_patch_error(
    e: PatchApplicationError,
    tested_package_name: str,
    progress: Progress,
) -> None:
    """Print GitHub Actions annotations and write step summary for a patch failure."""
    original_error = e.__cause__ if hasattr(e, "__cause__") else None
    error_stderr = getattr(original_error, "stderr", None)

    error_title = f"Failed to apply patch to {tested_package_name}"
    progress.text(f"::error title={error_title}::{e.message}")

    progress.text("::group::Patch Application Error Details")
    progress.text(f"Package: {tested_package_name}")
    if e.patch_reference:
        progress.text(f"Patch: {e.patch_reference}")
    if e.repository_path:
        progress.text(f"Repository: {e.repository_path}")
    if error_stderr:
        progress.text(f"Git error output:\n{error_stderr}")
    progress.text("::endgroup::")

    github_step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if github_step_summary:
        with open(github_step_summary, "a") as f:
            f.write("### ❌ Patch Application Failed\n\n")
            f.write(f"**Package:** `{tested_package_name}`\n\n")
            if e.patch_reference:
                f.write(f"**Patch:** `{e.patch_reference}`\n\n")
            f.write(f"**Error:** {e.message}\n\n")
            if error_stderr:
                f.write("<details>\n")
                f.write("<summary>Git Error Output</summary>\n\n")
                f.write(f"```\n{error_stderr}\n```\n\n")
                f.write("</details>\n\n")


# endregion
