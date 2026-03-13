# -*- coding: utf-8 -*-
#
# Copyright (C) 2026 CESNET z.s.p.o.
#
# invenio-testrig is free software; you can redistribute it and/or
# modify it under the terms of the MIT License; see LICENSE file for more
# details.
"""GitHub workflow automation command implementation.

Provides the github command that initiates remote testing inside a GitHub repository
by creating/updating a test repository, dispatching a workflow run with optional patches,
and opening the workflow page in a browser.
"""

import difflib
import hashlib
import json
import re
import shutil
import subprocess
import time
import webbrowser
from importlib.resources import files
from pathlib import Path

import click

from invenio_testrig.cli.base import with_progress
from invenio_testrig.types import Progress
from invenio_testrig.utils import call_executable_quietly

# region CLI Command


@click.command("github")
@with_progress
@click.option(
    "--target",
    help="Target repository name (e.g., 'org/repo'). If not provided, forks to your account as 'invenio-testrig'",
)
@click.option(
    "--name",
    help="Test name (used in reports)",
)
@click.option(
    "--python-version",
    default="3.14.2",
    help="Python version to use for testing",
)
@click.option(
    "--disable-codestyle-checks",
    is_flag=True,
    help="Disable codestyle checks (black/isort) during tests",
)
@click.option(
    "--patch-mode",
    type=click.Choice(["upstream", "pinned"]),
    default="upstream",
    help="Test upstream or pinned versions",
)
@click.option(
    "--test-scope",
    type=click.Choice(["affected", "all"]),
    default="all",
    help="Test scope: 'affected' (only packages affected by patches), 'all'",
)
@click.option(
    "--test-mode",
    type=click.Choice(["stop-on-success", "run-all"]),
    default="stop-on-success",
    help="Test selection for patched packages",
)
@click.argument("patches", nargs=-1)
def github_cmd(
    target: str | None,
    name: str | None,
    python_version: str,
    disable_codestyle_checks: bool,
    patch_mode: str,
    test_scope: str,
    test_mode: str,
    patches: tuple[str, ...],
    progress: Progress,
):
    """Setup remote testing inside GitHub repository with optional patches.

    Creates or updates a fork of inveniosoftware/invenio-testrig and sets up
    the gh-pages branch. Optionally dispatches a workflow run with the provided patches.

    Examples:

    invenio-testrig github

    invenio-testrig github --target myorg/my-testrig

    invenio-testrig github inveniosoftware/invenio-records-resources#123

    invenio-testrig github org/package#456 org/another#789

    invenio-testrig github --patch-mode upstream --test-scope all org/package#123

    This function:
    1. Creates a repository if it doesn't exist (with testrig client files and gh-pages branch)
    2. Dispatches the testrig.yml workflow with the provided patches
    3. Opens a browser window with the workflow run
    """

    username = _get_current_github_username()
    target_repo = _determine_target_repository(target, username, progress)
    repo_exists = _check_repository_exists(target_repo, progress)

    if not repo_exists:
        _create_repository(target_repo, username, progress)
    else:
        # Repository exists - check if workflow needs updating
        _update_existing_repository_workflow(target_repo, progress)

    workflow_url = _dispatch_workflow(
        target_repo,
        list(patches),
        name,
        python_version,
        disable_codestyle_checks,
        patch_mode,
        test_scope,
        test_mode,
        progress,
    )

    _open_workflow_in_browser(workflow_url, progress)

    progress.success("GitHub repository setup complete!")
    progress.info(f"Repository: https://github.com/{target_repo}")


# endregion


# region Repository Setup


def _get_latest_testrig_version() -> tuple[int, int, int]:
    """Find the latest testrig workflow version.

    Searches for testrig-X.Y.Z.yml files and returns the highest version.

    :return: Tuple of (major, minor, patch) version numbers
    """
    testrig_client_path = files("invenio_testrig.testrig_client")
    workflows_path = testrig_client_path / ".github" / "workflows"

    versions = []
    for item in workflows_path.iterdir():
        if item.name.startswith("testrig-") and item.name.endswith(".yml"):
            # Extract version from filename: testrig-X.Y.Z.yml
            version_str = item.name[8:-4]  # Remove "testrig-" and ".yml"
            try:
                parts = version_str.split(".")
                if len(parts) == 3:
                    version = tuple(int(p) for p in parts)
                    versions.append(version)
            except ValueError:
                continue

    if not versions:
        raise RuntimeError("No versioned testrig workflow files found")

    return max(versions)


def _get_testrig_version_md5s() -> dict[tuple[int, int, int], str]:
    """Get MD5 hashes for all versioned testrig workflow files.

    :return: Dictionary mapping version tuples to MD5 hashes
    """
    testrig_client_path = files("invenio_testrig.testrig_client")
    workflows_path = testrig_client_path / ".github" / "workflows"

    version_md5s = {}
    for item in workflows_path.iterdir():
        if item.name.startswith("testrig-") and item.name.endswith(".yml"):
            version_str = item.name[8:-4]
            try:
                parts = version_str.split(".")
                if len(parts) == 3:
                    version = tuple(int(p) for p in parts)
                    content = item.read_text()
                    md5 = hashlib.md5(content.encode("utf-8")).hexdigest()
                    version_md5s[version] = md5
            except ValueError:
                continue

    return version_md5s


def _get_file_md5(file_path: Path) -> str:
    """Calculate MD5 hash of a file.

    :param file_path: Path to the file
    :return: MD5 hash as hex string
    """
    content = file_path.read_text()
    return hashlib.md5(content.encode("utf-8")).hexdigest()


def _update_workflow_file(temp_dir: Path, progress: Progress) -> None:
    """Update or create testrig.yml workflow file with version management.

    For new repositories: Copy the latest version as testrig.yml
    For existing repositories: Check MD5 and update if it matches a known version,
                              otherwise show diff and warning

    :param temp_dir: Temporary directory with cloned repository
    :param progress: Progress reporter for status updates
    """
    workflows_dir = temp_dir / ".github" / "workflows"
    target_file = workflows_dir / "testrig.yml"

    # Get latest version
    latest_version = _get_latest_testrig_version()
    version_str = f"{latest_version[0]}.{latest_version[1]}.{latest_version[2]}"

    # Get path to latest versioned file
    testrig_client_path = files("invenio_testrig.testrig_client")
    source_file = (
        testrig_client_path / ".github" / "workflows" / f"testrig-{version_str}.yml"
    )
    latest_content = source_file.read_text()

    # Check if target file exists
    if not target_file.exists():
        # New repository - just copy the latest version
        workflows_dir.mkdir(parents=True, exist_ok=True)
        target_file.write_text(latest_content)
        progress.info(f"Created testrig.yml (version {version_str})")
        return

    # Existing repository - check if we should update
    current_md5 = _get_file_md5(target_file)
    version_md5s = _get_testrig_version_md5s()

    # Check if current file matches any known version
    matching_version = None
    for version, md5 in version_md5s.items():
        if md5 == current_md5:
            matching_version = version
            break

    if matching_version:
        # File matches a known version
        if matching_version == latest_version:
            progress.info(f"testrig.yml is already at latest version {version_str}")
        else:
            # Update to latest version
            old_version_str = (
                f"{matching_version[0]}.{matching_version[1]}.{matching_version[2]}"
            )
            target_file.write_text(latest_content)
            progress.success(
                f"Updated testrig.yml from {old_version_str} to {version_str}"
            )
    else:
        # File doesn't match any known version - user has customized it
        progress.warning(
            "testrig.yml has been customized (doesn't match any known version)"
        )
        progress.warning("Please review the diff below and update manually if needed:")
        progress.text("")

        # Show diff
        current_content = target_file.read_text()
        current_lines = current_content.splitlines(keepends=True)
        latest_lines = latest_content.splitlines(keepends=True)

        diff = difflib.unified_diff(
            current_lines,
            latest_lines,
            fromfile="testrig.yml (current/customized)",
            tofile=f"testrig.yml (latest version {version_str})",
            lineterm="",
        )

        for line in diff:
            line = line.rstrip()
            if line.startswith("+"):
                progress.text(line, fg="green")
            elif line.startswith("-"):
                progress.text(line, fg="red")
            elif line.startswith("@@"):
                progress.text(line, fg="cyan")
            else:
                progress.text(line, dim=True)

        progress.text("")
        progress.warning("testrig.yml was NOT updated automatically")
        progress.info(
            f"To update manually, replace .github/workflows/testrig.yml with version {version_str}"
        )


def _get_current_github_username() -> str:
    """Get the current GitHub username using the gh CLI."""
    stdout, _ = call_executable_quietly(["gh", "api", "user", "--jq", ".login"])
    return stdout.strip()


def _determine_target_repository(target: str | None, username: str, progress: Progress):
    """Determine the target repository for forking.

    :param target: Target repository name in format 'org/repo' or None
    :param username: GitHub username of the current user
    :param progress: Progress reporter for status updates

    :return: Target repository name in format 'org/repo'

    :raises SystemExit: If target is None and GitHub username cannot be determined
    """
    if target:
        if "/" not in target:
            target = f"{target}/invenio-testrig-client"
        progress.info(f"Using target repository: {target}")
        return target

    # Put the testrig into the user's namespace by default
    try:
        target_repo = f"{username}/invenio-testrig-client"
        progress.info(f"Will use default target: {target_repo}")
        return target_repo
    except subprocess.CalledProcessError:
        progress.error(
            "Failed to get GitHub username. Are you logged in to gh? "
            "Run 'gh auth login' first."
        )
        raise SystemExit(1)


def _check_repository_exists(target_repo: str, progress: Progress) -> bool:
    """Check if the target repository exists.

    :param target_repo: Repository name in format 'org/repo'
    :param progress: Progress reporter for status updates

    :return: True if repository exists, False otherwise
    """
    progress.start("Checking if target repository exists", icon="🔍")
    try:
        call_executable_quietly(["gh", "repo", "view", target_repo], always_quiet=True)
        progress.info(f"Repository {target_repo} already exists")
        return True
    except subprocess.CalledProcessError:
        progress.info(f"Repository {target_repo} does not exist")
        return False


def _create_repository(target_repo: str, username: str, progress: Progress) -> None:
    """Create a new empty repository with testrig client files and gh-pages branch.

    :param target_repo: Repository name in format 'org/repo'
    :param username: GitHub username of the current user
    :param progress: Progress reporter for status updates

    :raises SystemExit: If repository creation fails
    """
    progress.start(f"Creating repository {target_repo}", icon="🏗️")
    try:
        # Create empty repository
        call_executable_quietly(
            [
                "gh",
                "repo",
                "create",
                target_repo,
                "--public",
            ]
        )
        progress.success(f"Created repository {target_repo}")

        # Add testrig.yml workflow file
        progress.start("Adding testrig client files", icon="📝")

        # Create a temporary directory for cloning
        temp_dir = Path.cwd() / ".tmp_invenio_testrig_client"
        if temp_dir.exists():
            shutil.rmtree(temp_dir)

        try:
            # Clone the repository
            call_executable_quietly(["gh", "repo", "clone", target_repo, str(temp_dir)])

            # Copy all files from testrig_client to the repository (except versioned workflows)
            testrig_client_path = files("invenio_testrig.testrig_client")

            def copy_resources(source_path, dest_path: Path):
                """Recursively copy all files from importlib.resources to destination."""
                if source_path.is_file():
                    # Skip versioned testrig workflow files (testrig-X.Y.Z.yml)
                    if source_path.name.startswith(
                        "testrig-"
                    ) and source_path.name.endswith(".yml"):
                        if re.match(r"testrig-\d+\.\d+\.\d+\.yml", source_path.name):
                            return

                    # It's a file, copy it
                    dest_path.parent.mkdir(parents=True, exist_ok=True)
                    dest_path.write_text(source_path.read_text())
                else:
                    # It's a directory, iterate through its contents
                    try:
                        for item in source_path.iterdir():
                            item_name = item.name
                            if item_name.startswith(
                                "__pycache__"
                            ) or item_name.endswith(".pyc"):
                                continue
                            copy_resources(item, dest_path / item_name)
                    except NotADirectoryError, AttributeError:
                        pass

            copy_resources(testrig_client_path, temp_dir)

            # Handle testrig.yml workflow file with version management
            _update_workflow_file(temp_dir, progress)

            # Commit and push
            call_executable_quietly(["git", "add", "."], cwd=temp_dir)
            call_executable_quietly(
                ["git", "commit", "-m", "Add testrig client files"], cwd=temp_dir
            )
            call_executable_quietly(["git", "push", "origin", "HEAD"], cwd=temp_dir)

            progress.success("Added testrig client files")

            # Create gh-pages branch
            progress.start("Creating gh-pages branch", icon="📄")
            call_executable_quietly(
                ["git", "checkout", "--orphan", "gh-pages"], cwd=temp_dir
            )
            call_executable_quietly(["git", "rm", "-rf", "."], cwd=temp_dir)
            call_executable_quietly(
                ["git", "commit", "--allow-empty", "-m", "Initialize gh-pages branch"],
                cwd=temp_dir,
            )
            call_executable_quietly(["git", "push", "origin", "gh-pages"], cwd=temp_dir)
            progress.success("Created gh-pages branch")

        finally:
            # Cleanup
            if temp_dir.exists():
                shutil.rmtree(temp_dir)

    except subprocess.CalledProcessError as e:
        progress.error(f"Failed to create repository: {e}")
        raise SystemExit(1)


def _update_existing_repository_workflow(target_repo: str, progress: Progress) -> None:
    """Update workflow file in existing repository if needed.

    :param target_repo: Repository name in format 'org/repo'
    :param progress: Progress reporter for status updates
    """
    progress.start("Checking workflow file version", icon="🔍")

    temp_dir = Path.cwd() / ".tmp_invenio_testrig_client_update"
    if temp_dir.exists():
        shutil.rmtree(temp_dir)

    try:
        # Clone the repository
        call_executable_quietly(["gh", "repo", "clone", target_repo, str(temp_dir)])

        # Update workflow file
        _update_workflow_file(temp_dir, progress)

        # Check if there are changes to commit
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=temp_dir,
            capture_output=True,
            text=True,
        )

        if result.stdout.strip():
            # There are changes, commit and push
            call_executable_quietly(
                ["git", "add", ".github/workflows/testrig.yml"], cwd=temp_dir
            )
            call_executable_quietly(
                ["git", "commit", "-m", "Update testrig.yml workflow"], cwd=temp_dir
            )
            call_executable_quietly(["git", "push", "origin", "HEAD"], cwd=temp_dir)
            progress.success("Workflow file updated in repository")

    except subprocess.CalledProcessError:
        progress.warning("Could not update workflow file in existing repository")
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)


# endregion


# region Workflow Management


def _dispatch_workflow(
    target_repo: str,
    patches: list[str],
    name: str | None,
    python_version: str,
    disable_codestyle_checks: bool,
    patch_mode: str,
    test_scope: str,
    test_mode: str,
    progress: Progress,
) -> str | None:
    """Dispatch workflow or return workflow page URL.

    :param target_repo: Repository name in format 'org/repo'
    :param patches: List of patch references to test
    :param name: Test name (used in reports)
    :param python_version: Python version to use for testing
    :param disable_codestyle_checks: Disable codestyle checks during tests
    :param patch_mode: Test upstream or pinned versions
    :param test_scope: Test scope ('affected' or 'all')
    :param test_mode: Test selection for patched packages
    :param progress: Progress reporter for status updates

    :return: Workflow URL if available, None otherwise
    """
    workflow_url = None

    if not patches:
        progress.info(
            "No patches provided. You can start the workflow manually from the Actions tab."
        )
        return f"https://github.com/{target_repo}/actions/workflows/testrig.yml"

    progress.start("Dispatching workflow with patches", icon="🚀")
    try:
        # Build workflow dispatch command
        patches_str = " ".join(patches)
        workflow_cmd = [
            "gh",
            "workflow",
            "run",
            "testrig.yml",
            "--repo",
            target_repo,
            "-f",
            f"patches={patches_str}",
            "-f",
            f"python-version={python_version}",
            "-f",
            f"disable-codestyle-checks={str(disable_codestyle_checks).lower()}",
            "-f",
            f"patch-mode={patch_mode}",
            "-f",
            f"test-scope={test_scope}",
            "-f",
            f"test-mode={test_mode}",
        ]

        # Add name if provided
        if name:
            workflow_cmd.extend(["-f", f"name={name}"])

        # Dispatch the workflow
        call_executable_quietly(workflow_cmd)

        # Wait a moment for the workflow to be created
        time.sleep(2)

        # Get the latest workflow run
        stdout, _ = call_executable_quietly(
            [
                "gh",
                "run",
                "list",
                "--repo",
                target_repo,
                "--workflow=testrig.yml",
                "--limit",
                "1",
                "--json",
                "databaseId,url",
            ]
        )

        runs = json.loads(stdout)
        if runs:
            workflow_url = runs[0]["url"]
            progress.success("Workflow dispatched successfully")
            progress.info(f"Workflow URL: {workflow_url}")

    except subprocess.CalledProcessError:
        progress.warning(
            "Failed to dispatch workflow. You can start it manually from the Actions tab."
        )
        workflow_url = f"https://github.com/{target_repo}/actions/workflows/testrig.yml"

    return workflow_url


def _open_workflow_in_browser(workflow_url: str | None, progress: Progress) -> None:
    """Open workflow URL in browser.

    :param workflow_url: Workflow URL to open
    :param progress: Progress reporter for status updates
    """
    if not workflow_url:
        return

    progress.info("Opening browser with workflow page...")
    try:
        webbrowser.open(workflow_url)
    except Exception as e:
        progress.warning(f"Failed to open browser: {e}")
        progress.info(f"Please visit: {workflow_url}")


# endregion
