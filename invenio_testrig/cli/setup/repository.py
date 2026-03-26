# -*- coding: utf-8 -*-
#
# Copyright (C) 2026 CESNET z.s.p.o.
#
# invenio-testrig is free software; you can redistribute it and/or
# modify it under the terms of the MIT License; see LICENSE file for more
# details.
"""Repository cloning and patching."""

import json
from pathlib import Path

from invenio_testrig.cli.setup.dependencies import report_patch_error
from invenio_testrig.config import Config
from invenio_testrig.errors import PatchApplicationError
from invenio_testrig.github import GitApi, GitCache
from invenio_testrig.hooks import run_hook
from invenio_testrig.patchers import patchers_by_mode
from invenio_testrig.types import Progress


def _save_commits(commits: list[tuple[str, str]], output_json_path: Path) -> None:
    """Save commit log to JSON file.

    :param commits: List of tuples containing commit hash and commit message
    :param output_json_path: Path where to save the JSON file
    """
    with open(output_json_path, "w") as f:
        json.dump(
            [
                {"hash": commit_hash, "message": message}
                for commit_hash, message in commits
            ],
            f,
            indent=2,
        )


def clone_repositories(
    config: Config,
    clone_path: Path,
    progress: Progress,
) -> None:
    """Clone packages from configuration.

    Clone seed_repository.git and seed_repository.e2e (if configured) to the output directory.
    Then clone all packages specified in "tested_packages" into the packages/ subdirectory.
    If a package has patches, also clone it into the patched/ subdirectory and apply patches.
    The patching behavior depends on the patch_mode specified in the config (upstream, or pinned).

    Layout of the output directory::

        clone_path/
        ├── repo/          # Cloned seed_repository.git
        ├── invenio-e2e/   # Cloned seed_repository.e2e (if configured)
        ├── packages/      # Cloned dependencies without patches
        │     └── <pkg>/   # Cloned dependency repository with pinned version
        └── patched/       # Cloned dependencies with patches applied
              └── <pkg>/   # Cloned dependency repository with patches applied


    :param config: Config object
    :param clone_path: Path where repositories will be cloned
    :param progress: Progress reporter for status updates

    :raises ValueError: If the clone_path already exists or if the patch_mode is unsupported
    """
    # Check if output directory exists
    if clone_path.exists():
        raise ValueError(f"Output directory {clone_path} already exists")

    git_api = GitApi(
        GitCache(config.workdir_path("git_cache"), extra_env=config.env),
        extra_env=config.env,
    )

    # Read the config JSON
    run_hook(
        config,
        "before_cloning_packages",
        clone_path=clone_path,
    )

    # Create output directory
    clone_path.mkdir(parents=True, exist_ok=False)

    # Clone seed_repository.git
    repo_git = config.seed_repository.git
    repo_dir = clone_path / "repo"
    progress.start(f"Cloning {repo_git.org}/{repo_git.repo} to {repo_dir}", icon="🔄")
    git_api.clone_git_reference(repo_git, repo_dir)

    run_hook(
        config,
        "after_cloning_repository",
        repository_path=repo_dir,
        clone_path=clone_path,
    )

    # Clone seed_repository.e2e if it exists
    if config.seed_repository.e2e:
        e2e_ref = config.seed_repository.e2e
        e2e_dir = clone_path / "invenio-e2e"
        progress.start(f"Cloning {e2e_ref.org}/{e2e_ref.repo} to {e2e_dir}", icon="🔄")
        git_api.clone_git_reference(e2e_ref, e2e_dir)

        run_hook(
            config,
            "after_cloning_e2e_repository",
            e2e_repository_path=e2e_dir,
            clone_path=clone_path,
        )

    # Clone dependencies using appropriate patcher mode
    tested_packages = config.tested_packages
    mode = config.patch_mode
    patcher_cls = patchers_by_mode.get(mode)

    if patcher_cls is None:
        raise ValueError(f"Unsupported patch_mode '{mode}'")

    if tested_packages:
        packages_dir = clone_path / "packages"
        packages_dir.mkdir(parents=True, exist_ok=True)
        patched_packages_dir = clone_path / "patched"
        patched_packages_dir.mkdir(parents=True, exist_ok=True)
        top_commits_dir = clone_path / "top_commits"
        top_commits_dir.mkdir(parents=True, exist_ok=True)

        patcher = patcher_cls(config, packages_dir, patched_packages_dir, progress)

        for tested_package_name in tested_packages.keys():
            progress.info(
                f"Cloning dependency {tested_package_name} using '{mode}' mode",
                icon="📦",
            )
            try:
                (
                    (unpatched_reference, unpatched_commits),
                    (
                        patched_reference,
                        patched_commits,
                    ),
                ) = patcher.clone_and_patch_package(tested_package_name)
                tested_packages[
                    tested_package_name
                ].unpatched_reference = unpatched_reference
                tested_packages[
                    tested_package_name
                ].patched_reference = patched_reference

                # Save commit logs to JSON files
                _save_commits(
                    unpatched_commits,
                    top_commits_dir / f"{tested_package_name}_unpatched.json",
                )

                if patched_commits is not None:
                    _save_commits(
                        patched_commits,
                        top_commits_dir / f"{tested_package_name}_patched.json",
                    )

            except PatchApplicationError as e:
                report_patch_error(e, tested_package_name, progress)
                raise

            run_hook(
                config,
                "after_cloning_dependency",
                clone_path=clone_path,
                package_name=tested_package_name,
                package_clone_path=packages_dir / tested_package_name,
                patched_package_clone_path=patched_packages_dir / tested_package_name,
            )

    run_hook(
        config,
        "after_cloning_packages",
        clone_path=clone_path,
    )
    config.save()  # Save the config with updated tested_packages info

    progress.success(f"Successfully cloned repositories to {clone_path}")
