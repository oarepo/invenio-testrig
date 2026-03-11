# -*- coding: utf-8 -*-
#
# Copyright (C) 2026 CESNET z.s.p.o.
#
# invenio-testrig is free software; you can redistribute it and/or
# modify it under the terms of the MIT License; see LICENSE file for more
# details.
"""Base patcher class with common functionality for cloning and patching.

This module defines the abstract Patcher base class that provides shared
logic for cloning repositories and applying patches. Subclasses implement
specific strategies for handling unpatched and patched versions.
"""

import shutil
from pathlib import Path

import black

from invenio_testrig.config import Config
from invenio_testrig.github.api import GitApi, GitCache
from invenio_testrig.github.types import GitReference
from invenio_testrig.types import Progress, TestedPackageInfo

# Constants
MAX_COMMITS_TO_DISPLAY = 50


class Patcher:
    """Base class for applying patches to repository clones.

    Provides common functionality for cloning repositories with and without
    patches, and applying patches according to different strategies.
    Subclasses must implement specific patching strategies.
    """

    def __init__(
        self, config: Config, unpatched_dir: Path, patched_dir: Path, progress: Progress
    ):
        """Initialize the Patcher with configuration and directory paths.

        :param config: Configuration object containing patch and package information
        :param unpatched_dir: Directory where unpatched repositories will be cloned
        :param patched_dir: Directory where patched repositories will be cloned
        :param progress: Progress reporter for outputting status messages
        """
        self.config = config
        self.git_api = GitApi(GitCache(config.workdir_path("git_cache")))
        self.unpatched_dir = unpatched_dir
        self.patched_dir = patched_dir
        self.progress = progress

    def clone_and_patch_package(
        self, package: str
    ) -> tuple[
        tuple[GitReference, list[tuple[str, str]]],
        tuple[GitReference | None, list[tuple[str, str]] | None],
    ]:
        """Clone the package and apply patches if configured.

        :param package: Name of the package to clone

        :return: Tuple of ((unpatched_reference, unpatched_commits), (patched_reference, patched_commits)) where patched_reference can be None if no patches exist
        """

        name, info = self._get_tested_package(package)

        unpatched_reference = self._build_unpatched_reference(name, info)
        unpatched_reference = self.git_api.resolve_reference(unpatched_reference)
        self.progress.info(f"Cloning unpatched: {str(unpatched_reference)}")
        unpatched_reference_path = self._clone_package(
            unpatched_reference, self.unpatched_dir
        )
        unpatched_commits = self.git_api.get_last_commits(
            unpatched_reference_path, MAX_COMMITS_TO_DISPLAY
        )
        self._show_commit_log("unpatched", unpatched_commits)

        patched_reference = self._build_patched_reference(name, info)
        patched_reference_path = None
        if patched_reference:
            patched_reference = self.git_api.resolve_reference(patched_reference)
            self.progress.info(f"Cloning patched: {str(patched_reference)}")
            patched_reference_path = self._clone_package(
                patched_reference, self.patched_dir
            )
            patched_commits = self.git_api.get_last_commits(
                patched_reference_path, MAX_COMMITS_TO_DISPLAY
            )
            self._show_commit_log("before patch", patched_commits)
            self._apply_patches(patched_reference_path, name, info, patched_reference)
            self._add_patch_info(
                patched_reference_path,
                patch_mode=self.config.patch_mode,
                reference=patched_reference,
                applied_patches=info.patches or [],
            )
            patched_commits = self.git_api.get_last_commits(
                patched_reference_path, MAX_COMMITS_TO_DISPLAY
            )
            self._show_commit_log("after patch", patched_commits)

        # remove the .git directory after cloning
        self._post_process_clone(unpatched_reference_path)
        self._post_process_clone(patched_reference_path)

        return (
            (unpatched_reference, unpatched_commits),
            (patched_reference, patched_commits) if patched_reference else (None, None),
        )

    def _build_unpatched_reference(
        self, package_name: str, package_info: TestedPackageInfo
    ) -> GitReference:
        """Build GitReference for the unpatched version of the dependency.

        :param package_name: Name of the package
        :param package_info: Information about the tested package

        :return: GitReference for the unpatched version

        :raises NotImplementedError: This method must be implemented by subclasses
        """
        raise NotImplementedError(
            "Subclasses must implement the _build_unpatched_reference method"
        )

    def _build_patched_reference(
        self, package_name: str, package_info: TestedPackageInfo
    ) -> GitReference | None:
        """Build GitReference for the patched version of the dependency.

        The default implementation returns the same reference as the unpatched
        version when patches exist, or None when there are no patches.
        Subclasses may override this to use a different base for patching.

        :param package_name: Name of the package
        :param package_info: Information about the tested package

        :return: GitReference for the patched version, or None if no patches exist
        """
        if not package_info.patches:
            return None
        return self._build_unpatched_reference(package_name, package_info)

    def _apply_patches(
        self,
        patched_reference_path: Path,
        package_name: str,
        package_info: TestedPackageInfo,
        reference: GitReference,
    ) -> None:
        """Apply patches to the target directory. The patches are applied in order.

        The default implementation cherry-picks all PR commits from each patch
        onto the cloned repository. Subclasses may override this for custom
        patching strategies.

        :param patched_reference_path: Path to the cloned repository
        :param package_name: Name of the package
        :param package_info: Information about the tested package
        :param reference: GitReference of the patched version
        """
        for patch in package_info.patches:
            self.progress.info(f" ... applying patch {str(patch)}")
            if patch.pr_info:
                self.git_api.apply_pr_commits(patched_reference_path, patch)
            else:
                self.git_api.apply_branch(patched_reference_path, patch)

    def _post_process_clone(self, path: Path | None) -> None:
        """Remove .git directory and fix test scripts after cloning.

        :param path: Path to the cloned repository, or None (no-op)
        """
        if path is None:
            return
        self._remove_git_directory(path)
        self._fix_check_manifest(path)
        self._fix_run_sphinx(path)

    def _show_commit_log(self, message, commits: list[tuple[str, str]]) -> None:
        """Get and display the last N commits from a repository.

        :param commits: List of tuples containing commit hash and commit message
        """
        if commits:
            self.progress.info(f"\nLast {len(commits)} {message} commits:")
            for commit_hash, commit_message in commits:
                self.progress.info(f"  {commit_hash} - {commit_message}")
            self.progress.info("")  # Empty line for readability

    def _remove_git_directory(self, path: Path) -> None:
        """Remove .git directory from a repository.

        :param path: Path to the repository directory
        """
        git_directory = path / ".git"
        if git_directory.exists():
            shutil.rmtree(git_directory)

    def _fix_check_manifest(self, path: Path) -> None:
        """Remove check-manifest commands from run-tests.sh script.

        The check_manifest command fails when there are untracked files (like
        removed .git directories), so it is removed from test scripts.

        :param path: Path to the repository directory
        """
        self._remove_from_runtest_sh(path, "check_manifest")

    def _fix_run_sphinx(self, path: Path) -> None:
        """Remove run-sphinx commands from run-tests.sh script.

        The run-sphinx command currently fails here inside tests, so it is
        removed from test scripts.

        The sphinx.cmd.build lines are removed from run-tests.sh

        :param path: Path to the repository directory
        """
        self._remove_from_runtest_sh(path, "sphinx.cmd.build")

    def _remove_from_runtest_sh(self, path: Path, search_string: str) -> None:
        """Remove lines containing search_string from run-tests.sh script.

        :param path: Path to the repository directory
        :param search_string: String to search for in the script, lines containing this string will be removed
        """
        run_tests_script = path / "run-tests.sh"
        if run_tests_script.exists():
            content = run_tests_script.read_text()
            if search_string in content:
                new_content = "\n".join(
                    line for line in content.splitlines() if search_string not in line
                )
                run_tests_script.write_text(new_content)

    def _get_tested_package(self, package: str) -> tuple[str, TestedPackageInfo]:
        """Return tested package info matching package name (case-insensitive).

        :param package: Package name to look up

        :return: Tuple of (package_name, TestedPackageInfo)

        :raises ValueError: If the package is not found in configuration
        """
        tested_packages = self.config.tested_packages

        for name, info in tested_packages.items():
            if name == package:
                return name, info

        raise ValueError(f"Tested package '{package}' not found in configuration")

    def _clone_package(self, reference: GitReference, destination: Path) -> Path:
        """Clone the tested package repository and return the target directory.

        :param reference: GitReference to clone
        :param destination: Root directory where the package should be cloned

        :return: Path to the cloned package directory
        """
        package_dir = destination / reference.package
        if package_dir.exists():
            shutil.rmtree(package_dir)
        self.git_api.clone_git_reference(reference, package_dir)
        return package_dir

    def _add_patch_info(
        self,
        target_dir: Path,
        patch_mode: str,
        reference: GitReference,
        applied_patches: list[GitReference],
    ) -> None:
        """Add patch info file to the target directory.

        Creates a patch_info.py file in all Python packages within the target directory
        containing information about the patch mode, reference, and applied patches.

        :param target_dir: Path to the repository directory
        :param patch_mode: The patch mode used (e.g., "upstream", "pinned")
        :param reference: GitReference for the cloned repository
        :param applied_patches: List of GitReferences for patches that were applied
        """
        content = self._generate_patch_info_content(
            patch_mode, reference, applied_patches
        )

        # Write to all Python packages
        for package_dir in self._find_python_packages(target_dir):
            self._write_and_format_patch_info(package_dir / "patch_info.py", content)

        # Also write to top level
        self._write_and_format_patch_info(target_dir / "patch_info.py", content)

    def _generate_patch_info_content(
        self,
        patch_mode: str,
        reference: GitReference,
        applied_patches: list[GitReference],
    ) -> str:
        """Generate the Python code content for patch_info.py.

        :param patch_mode: The patch mode used
        :param reference: GitReference for the cloned repository
        :param applied_patches: List of applied patches

        :return: Python source code as a string
        """
        lines = [
            '"""Clone information for this package."""',
            "",
            f'patch_mode = "{patch_mode}"',
            "",
            f"reference = {reference.to_dict()}",
            "applied_patches = [",
        ]

        for patch in applied_patches:
            # Indent the representation
            indented = "    " + repr(patch.to_dict()).replace("\n", "\n    ")
            lines.append(f"{indented},")

        lines.extend(
            [
                "]",
                "if __name__ == '__main__':",
                "    import json",
                "    print(json.dumps({",
                "        'patch_mode': patch_mode,",
                "        'reference': reference,",
                "        'applied_patches': applied_patches,",
                "    }, indent=2))",
            ]
        )

        return "\n".join(lines) + "\n"

    def _find_python_packages(self, target_dir: Path) -> list[Path]:
        """Find all Python package directories (excluding test directories).

        :param target_dir: Root directory to search

        :return: List of package directories containing __init__.py
        """
        packages = []
        for init_file in target_dir.glob("*/__init__.py"):
            package_dir = init_file.parent
            # Skip test directories
            if package_dir.name not in ("test", "tests"):
                packages.append(package_dir)
        return packages

    def _write_and_format_patch_info(self, file_path: Path, content: str) -> None:
        """Write patch_info.py file and format it with black.

        :param file_path: Path to the patch_info.py file to create
        :param content: Python source code to write
        """
        file_path.write_text(content)
        black.format_file_in_place(
            file_path,
            fast=False,
            mode=black.Mode(),
            write_back=black.WriteBack.YES,
        )
