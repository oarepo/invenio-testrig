# -*- coding: utf-8 -*-
#
# Copyright (C) 2026 CESNET z.s.p.o.
#
# invenio-testrig is free software; you can redistribute it and/or
# modify it under the terms of the MIT License; see LICENSE file for more
# details.
"""Python package management and installation using uv.

This module provides a wrapper around the uv package manager for creating
virtual environments, installing packages, and managing Python dependencies.
It handles various project configurations including modern pyproject.toml
and legacy setup.py based projects.
"""

import configparser
import json
import logging
import os
import shutil
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, overload

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from invenio_testrig.progress import Progress
from invenio_testrig.utils import call_executable_quietly

log = logging.getLogger(__name__)


@dataclass
class _LocalProjectInfo:
    """Parsed dependency metadata for a locally available package.

    Holds just enough information to walk the dependency graph and determine
    which other local packages a given package needs — without installing
    anything or querying PyPI.
    """

    name: str                            # PEP 503 normalized
    raw_deps: list[str]                  # base dependency specifier strings
    optional_deps: dict[str, list[str]]  # extra name -> list of dep specifiers


def _parse_lock_file_dependencies(lock_path: Path) -> "dict[str, str] | None":
    """Parse a uv.lock TOML file and return a package→version mapping.

    Returns None if the file cannot be parsed so callers can fall back to pip list.
    """
    try:
        with open(lock_path, "rb") as f:
            lock_data = tomllib.load(f)
        dependencies: dict[str, str] = {}
        for package in lock_data.get("package", []):
            name = package.get("name")
            if not name:
                continue
            name = name.lower()
            source = package.get("source")
            if source and isinstance(source, dict) and "git" in source:
                dependencies[name] = source["git"]
            else:
                version = package.get("version")
                if version:
                    dependencies[name] = version
        return dependencies
    except Exception:
        log.exception("Failed to parse uv.lock file")
        return None


class PythonAPI:
    """Python package management API using uv.

    Provides methods for creating virtual environments, installing packages,
    managing dependencies, and running commands within virtual environments.
    """

    def __init__(
        self,
        extra_env: dict[str, str],
        uv_executable: str = "uv",
        python_version: str = "python3.14",
    ) -> None:
        """Initialize the PythonAPI with uv executable and Python version.

        :param uv_executable: Path to the uv executable (default: "uv")
        :param python_version: Python version to use (default: "python3")
        """
        if extra_env is None:
            raise ValueError("extra_env must be provided")
        self.extra_env = extra_env
        self.uv_executable = uv_executable
        self.python_version = python_version

    def install_project(
        self,
        project_path: Path,
        extras: list[str] | None = None,
        ignore_uv_lock: bool = False,
    ) -> None:
        """
        Create a virtual environment in the project directory and install the package.

        Strategy:
        1. If uv.lock exists, run `uv sync --locked` - it is a repository
        2. If pyproject.toml exists with [project] table, run `uv sync` - a new library
        3. Otherwise, run `uv venv` + `uv pip install -e .` - an older library without correct pyproject.toml

        :param project_path: Path to the directory containing the package to install
        :param extras: Optional list of extras to install with the package
        :param ignore_uv_lock: If True, remove uv.lock file before installation

        :raises subprocess.CalledProcessError: If venv creation or package installation fails
        """
        project_path = project_path.resolve()
        lock_path = project_path / "uv.lock"

        if lock_path.exists() and ignore_uv_lock:
            log.warning(
                "uv.lock file exists but will be removed due to ignore_uv_lock=True."
            )
            lock_path.unlink()

        # Try installation strategies in order of preference
        if lock_path.exists():
            self._install_with_locked_sync(project_path)
        elif not self._try_install_with_sync(project_path, extras):
            self._install_with_venv_pip(project_path, extras)

    def _install_with_locked_sync(self, project_path: Path) -> None:
        """Install using uv sync --locked for projects with uv.lock.

        :param project_path: Path to the project directory
        """
        call_executable_quietly(
            [
                self.uv_executable,
                "sync",
                "--locked",
                "--python",
                self.python_version,
            ],
            cwd=project_path,
            env=self.prepare_venv_environment(project_path),
        )

    def _try_install_with_sync(
        self, project_path: Path, extras: list[str] | None
    ) -> bool:
        """Try to install using uv sync if pyproject.toml has [project] table.

        :param project_path: Path to the project directory
        :param extras: Optional list of extras to install

        :return: True if installation was successful, False to fall back to other strategy
        """
        pyproject_path = project_path / "pyproject.toml"
        if not pyproject_path.exists():
            return False

        try:
            with open(pyproject_path, "rb") as f:
                pyproject_data = tomllib.load(f)

            if "project" not in pyproject_data:
                return False

            sync_extras = self._filter_available_extras(
                extras, pyproject_data.get("project", {})
            )

            call_executable_quietly(
                [
                    self.uv_executable,
                    "sync",
                    "--python",
                    self.python_version,
                    *sync_extras,
                ],
                cwd=project_path,
                env=self.prepare_venv_environment(project_path),
            )
            return True
        except tomllib.TOMLDecodeError:
            log.exception("Failed to parse pyproject.toml")
            return False
        except subprocess.CalledProcessError:
            log.exception("uv sync failed")
            return False
        except OSError:
            log.exception("Failed to read pyproject.toml")
            return False

    def _filter_available_extras(
        self, extras: list[str] | None, project_data: dict
    ) -> list[str]:
        """Filter extras list to only include those available in the project.

        :param extras: Requested extras to install
        :param project_data: Project section from pyproject.toml

        :return: List of command-line arguments for uv sync (--extra name pairs)
        """
        if not extras:
            return []

        available_extras = set()
        if "optional-dependencies" in project_data:
            available_extras = set(project_data["optional-dependencies"].keys())

        sync_extras = []
        for extra in extras:
            if extra in available_extras:
                sync_extras.extend(["--extra", extra])
            else:
                log.warning(
                    f"Extra '{extra}' not found in project's optional-dependencies, skipping"
                )

        return sync_extras

    def _install_with_venv_pip(
        self, project_path: Path, extras: list[str] | None
    ) -> None:
        """Install using uv venv + uv pip install for legacy projects.

        This strategy is used for older projects without proper pyproject.toml.
        No extras filtering is needed as uv pip ignores non-existent extras.

        :param project_path: Path to the project directory
        :param extras: Optional list of extras to install
        """
        # Create venv with a clean environment to avoid conflicts
        clean_env = os.environ.copy()
        clean_env.pop("VIRTUAL_ENV", None)
        clean_env.pop("PYTHONHOME", None)

        call_executable_quietly(
            [
                self.uv_executable,
                "venv",
                "--python",
                self.python_version,
            ],
            cwd=project_path,
            env=clean_env | self.extra_env,
        )

        # Install the project with extras
        extras_specification = f"[{','.join(extras)}]" if extras else ""
        pip_install_spec = f".{extras_specification}"
        call_executable_quietly(
            [self.uv_executable, "pip", "install", "-e", pip_install_spec],
            cwd=project_path,
            env=self.prepare_venv_environment(project_path),
        )

    def get_dependencies(
        self,
        project_path: Path,
        ignore_uv_lock: bool = False,
    ) -> dict[str, str]:
        """
        Get dependencies from uv.lock file or installed packages.

        Strategy:
        1. If no lock file exists, run install_directory first
        2. If lock file exists, extract dependencies from it
        3. If no lock file (even after install), use uv pip list

        :param project_path: Path to the directory containing the package
        :param ignore_uv_lock: If True, remove uv.lock and skip using it for dependency resolution

        :return: Dictionary mapping package names to their version strings. Versions can be standard version numbers or git references.

        :raises subprocess.CalledProcessError: If commands fail
        """
        project_path = project_path.resolve()
        lock_path = project_path / "uv.lock"

        if ignore_uv_lock or not lock_path.exists():
            self.install_project(project_path, ignore_uv_lock=ignore_uv_lock)

        if lock_path.exists():
            result = _parse_lock_file_dependencies(lock_path)
            if result is not None:
                return result

        return self.get_installed_dependencies(project_path)

    def get_installed_dependencies(self, project_path: Path) -> dict[str, str]:
        """Get installed dependencies using uv pip list.

        :param project_path: Path to the project directory with virtual environment

        :return: Dictionary mapping package names to their installed versions
        """
        stdout, _ = call_executable_quietly(
            [self.uv_executable, "pip", "list", "--format", "json"],
            cwd=project_path,
            env=self.prepare_venv_environment(project_path),
        )

        # Parse JSON output
        packages_list = json.loads(stdout)

        # Convert to dict of package -> version
        dependencies = {pkg["name"]: pkg["version"] for pkg in packages_list}

        return dependencies

    def prepare_venv_environment(self, project_path: Path) -> dict[str, str]:
        """Prepare environment variables for commands executed inside the venv.

        This clears any active virtualenv settings and points to the project's venv.

        :param project_path: Path to the project directory containing .venv

        :return: Dictionary of environment variables configured for the virtual environment
        """
        venv_path = project_path / ".venv"
        env = os.environ.copy()

        # Clear any existing virtualenv variables to avoid conflicts
        env.pop("VIRTUAL_ENV", None)
        env.pop("PYTHONHOME", None)

        # Set the new virtualenv
        env["VIRTUAL_ENV"] = str(venv_path)
        bin_dir = venv_path / "bin"

        # Prepend venv bin directory to PATH, removing any old venv paths
        path_value = env.get("PATH", "")
        # Filter out paths from other virtualenvs
        path_parts = path_value.split(os.pathsep) if path_value else []
        filtered_paths = [
            p for p in path_parts if "/.venv/" not in p and "/venv/" not in p
        ]
        env["PATH"] = os.pathsep.join([str(bin_dir)] + filtered_paths)
        env.update(self.extra_env)

        return env

    def install_patched_dependencies(
        self,
        *,
        project_path: Path,
        packages_root: Path,
        patched_packages_root: Path,
        install_patched_dependencies: bool,
        progress: Progress,
    ) -> list[str]:
        """Reinstall dependencies with local patches when available.

        :param project_path: Path to the project directory
        :param packages_root: Root directory containing unpatched package clones
        :param patched_packages_root: Root directory containing patched package clones
        :param install_patched_dependencies: Whether to install patched versions when available
        :param progress: Progress reporter for status messages

        :return: List of installed patched dependency package names
        """
        all_dependencies = self.get_installed_dependencies(project_path)
        patched_dependencies: list[str] = []

        paths_to_install: list[Path] = []
        for library_package in all_dependencies.keys():
            library_path = None
            patched = False

            if (
                install_patched_dependencies
                and (patched_packages_root / library_package).exists()
            ):
                progress.info(f"Installing patched dependency '{library_package}'")
                library_path = patched_packages_root / library_package
                patched = True
            elif (packages_root / library_package).exists():
                progress.info(
                    f"Installing dependency '{library_package}' from local clone"
                )
                library_path = packages_root / library_package

            # If installed from a local library, get patch info
            if library_path:
                paths_to_install.append(library_path)
                if patched:
                    patched_dependencies.append(library_package)

        self.install_external_libraries(project_path, *paths_to_install)

        return patched_dependencies

    def install_external_libraries(
        self, project_path: Path, *library_paths: Path
    ) -> None:
        """Install project directories using uv pip install.

        :param project_path: Path to the project where libraries should be installed
        :param library_paths: Paths to library directories to install
        """

        args = [self.uv_executable, "pip", "install"]
        args.extend(["--no-deps", "--force-reinstall"])
        args.extend([str(library_path) for library_path in library_paths])

        call_executable_quietly(
            args,
            cwd=project_path,
            env=self.prepare_venv_environment(project_path),
        )

    def _read_local_project_info(self, path: Path) -> _LocalProjectInfo:
        """Read a local package's name and declared dependencies without installing it.

        Needed so the dependency walker can traverse the local package graph
        purely from metadata, before any venv exists. Supports both modern
        pyproject.toml (with a [project] table) and legacy setup.cfg layouts,
        matching the range of packages found in InvenioRDM repositories.

        :raises FileNotFoundError: If neither pyproject.toml nor setup.cfg is present.
        """
        if (path / "pyproject.toml").exists():
            with open(path / "pyproject.toml", "rb") as f:
                data = tomllib.load(f)
            if "project" in data:
                return _LocalProjectInfo(
                    name=canonicalize_name(data["project"]["name"]),
                    raw_deps=list(data["project"].get("dependencies", [])),
                    optional_deps={
                        extra: list(deps)
                        for extra, deps in data["project"].get("optional-dependencies", {}).items()
                    },
                )

        if (path / "setup.cfg").exists():
            cfg = configparser.ConfigParser()
            cfg.read(path / "setup.cfg")
            raw_deps = [
                d.strip()
                for d in cfg.get("options", "install_requires", fallback="").splitlines()
                if d.strip()
            ]
            optional_deps: dict[str, list[str]] = {}
            if cfg.has_section("options.extras_require"):
                for extra, raw in cfg["options.extras_require"].items():
                    optional_deps[extra] = [d.strip() for d in raw.splitlines() if d.strip()]
            return _LocalProjectInfo(
                name=canonicalize_name(cfg["metadata"]["name"]),
                raw_deps=raw_deps,
                optional_deps=optional_deps,
            )

        raise FileNotFoundError(f"No pyproject.toml or setup.cfg found in {path}")

    def _resolve_local_paths(
        self,
        project_path: Path,
        packages_root: Path,
        patched_packages_root: Path,
        install_patched: bool,
        extras: list[str] | None,
    ) -> tuple[list[Path], set[str]]:
        """Identify which locally available packages must be pre-installed for a given package.

        When a package under test depends on another package whose required version
        does not exist on PyPI (e.g. a pre-release or an internally patched build),
        ``uv pip install -e .`` fails because PyPI cannot satisfy the constraint.
        Pre-installing only the relevant local packages with ``--no-deps`` plants
        the correct versions into the venv before PyPI resolution runs, making the
        constraint satisfiable without preventing uv from fetching other deps normally.

        Only packages reachable through the declared dependency tree of
        ``project_path`` are included, so unrelated packages that happen to live in
        the local repository cannot contaminate the test environment.

        :param project_path: The package being installed (its own deps seed the walk).
        :param packages_root: Directory of unpatched local package clones.
        :param patched_packages_root: Directory of patched local package clones (takes priority).
        :param install_patched: Whether to include patched packages in the candidate set.
        :param extras: Extras requested for the main package; used to follow optional deps.

        :return: A tuple of ``(ordered_paths, patched_names)`` where ``ordered_paths``
                 lists local dependency directories in topological order (dependencies
                 before dependents, main package excluded) and ``patched_names`` is the
                 set of package names sourced from ``patched_packages_root``.
        """
        _local_map: dict[str, Path] = {}
        patched_names_all: set[str] = set()

        for entry in packages_root.iterdir() if packages_root.exists() else []:
            if entry.is_dir():
                _local_map[canonicalize_name(entry.name)] = entry

        if install_patched and patched_packages_root.exists():
            for entry in patched_packages_root.iterdir():
                if entry.is_dir():
                    key = canonicalize_name(entry.name)
                    _local_map[key] = entry
                    patched_names_all.add(key)

        extras_map: dict[str, set[str]] = {}
        order: list[Path] = []

        def _visit(path: Path, with_extras: set[str]) -> None:
            try:
                info = self._read_local_project_info(path)
            except (FileNotFoundError, KeyError) as e:
                log.warning("Could not read metadata for %s: %s", path, e)
                return

            already = extras_map.get(info.name, set())
            new_extras = with_extras - already
            first_visit = info.name not in extras_map
            extras_map[info.name] = already | with_extras

            if first_visit:
                for spec in info.raw_deps:
                    req = Requirement(spec)
                    dep_name = canonicalize_name(req.name)
                    if dep_name in _local_map:
                        _visit(_local_map[dep_name], set(req.extras))
                order.append(path)

            for extra in new_extras:
                for spec in info.optional_deps.get(extra, []):
                    req = Requirement(spec)
                    dep_name = canonicalize_name(req.name)
                    if dep_name in _local_map:
                        _visit(_local_map[dep_name], set(req.extras))

        try:
            main_info = self._read_local_project_info(project_path)
        except (FileNotFoundError, KeyError):
            log.warning("Could not read metadata for %s; skipping local resolution", project_path)
            return [], set()

        seed_extras = set(extras) if extras else set()
        for spec in main_info.raw_deps:
            req = Requirement(spec)
            dep_name = canonicalize_name(req.name)
            if dep_name in _local_map:
                _visit(_local_map[dep_name], set(req.extras))

        for extra in seed_extras:
            for spec in main_info.optional_deps.get(extra, []):
                req = Requirement(spec)
                dep_name = canonicalize_name(req.name)
                if dep_name in _local_map:
                    _visit(_local_map[dep_name], set(req.extras))

        return order, patched_names_all & {canonicalize_name(p.name) for p in order}

    def _install_with_local_preinstall(
        self,
        project_path: Path,
        local_paths: list[Path],
        extras: list[str] | None,
    ) -> None:
        """Install a package after seeding its venv with pre-resolved local dependencies.

        Solves the problem where ``uv pip install -e .`` fails because a dependency
        is pinned to a version that does not exist on PyPI (e.g. a patched or
        pre-release build only available locally). By installing the local packages
        first with ``--no-deps``, their versions are already present in the venv when
        uv resolves the main package, so PyPI constraints are satisfied without
        blocking the rest of the dependency fetch.

        :param project_path: The package to install in editable mode.
        :param local_paths: Local dependency paths to pre-install (in topological order).
        :param extras: Extras to activate when installing the main package.
        """
        clean_env = os.environ.copy()
        clean_env.pop("VIRTUAL_ENV", None)
        clean_env.pop("PYTHONHOME", None)

        call_executable_quietly(
            [
                self.uv_executable,
                "venv",
                "--python",
                self.python_version,
            ],
            cwd=project_path,
            env=clean_env | self.extra_env,
        )

        if local_paths:
            self.install_external_libraries(project_path, *local_paths)

        extras_specification = f"[{','.join(extras)}]" if extras else ""
        pip_install_spec = f".{extras_specification}"
        call_executable_quietly(
            [self.uv_executable, "pip", "install", "-e", pip_install_spec],
            cwd=project_path,
            env=self.prepare_venv_environment(project_path),
        )

    def install_with_patches(
        self,
        repositories_root: Path,
        package_name: str,
        target_dir: Path,
        install_patched_dependencies: bool,
        progress: Progress,
        *,
        extras: list[str] | None = None,
        freeze: list[str] | None = None,
    ) -> list[str]:
        """Install a package with patches applied.

        :param repositories_root: Root directory containing both patched and regular packages
        :param package_name: Name of the package to install
        :param target_dir: Directory where the package should be installed
        :param install_patched_dependencies: Whether to install patched dependencies
        :param progress: Progress reporter for status messages
        :param extras: Optional list of extras to install with the package
        :param freeze: Optional list of dependencies to freeze after installation (e.g. ["package==1.2.3"])

        :return: List of installed, patched dependency names

        :raises FileExistsError: If target_dir already exists
        :raises FileNotFoundError: If package is not found in any package directory

        Strategy:
        1. Check if patched version of the package exists in patched_packages_root
        2. If not, check if the package exists in packages_root
        3. Copy the source code to the target directory
        4. Install the package in the target directory
        5. Install patched dependencies if any
        6. Apply the freeze list if provided
        """

        if target_dir.exists():
            raise FileExistsError(f"Target directory '{target_dir}' already exists")

        # get the source path for the package, prioritizing patched version
        # if should test patched dependencies, and raise an error if the package is
        # not found in either location
        candidates = [repositories_root / "packages" / package_name]
        if install_patched_dependencies:
            candidates.insert(0, repositories_root / "patched" / package_name)
        try:
            source_path = next(path for path in candidates if path.exists())
        except StopIteration:
            raise FileNotFoundError(
                f"Package '{package_name}' not found in patched or regular packages directories"
            )

        progress.info(
            f"Copying package '{package_name}' from {source_path} to {target_dir}"
        )

        shutil.copytree(source_path, target_dir)

        progress.info(f"Installing package '{package_name}' in {target_dir}")

        if (target_dir / "uv.lock").exists():
            self.install_project(target_dir, extras=extras)
        else:
            local_dep_paths, patched_from_walk = self._resolve_local_paths(
                project_path=target_dir,
                packages_root=repositories_root / "packages",
                patched_packages_root=repositories_root / "patched",
                install_patched=install_patched_dependencies,
                extras=extras,
            )
            progress.info(
                f"Pre-installing {len(local_dep_paths)} local dep(s) for "
                f"'{package_name}': {[p.name for p in local_dep_paths]}"
            )
            self._install_with_local_preinstall(target_dir, local_dep_paths, extras)

        # install patched dependencies if any
        dependencies = self.install_patched_dependencies(
            project_path=target_dir,
            packages_root=repositories_root / "packages",
            patched_packages_root=repositories_root / "patched",
            install_patched_dependencies=install_patched_dependencies,
            progress=progress,
        )

        if freeze:
            progress.info(
                f"Applying freeze list for package '{package_name}': {freeze}"
            )
            stdout, stderr = call_executable_quietly(
                [
                    self.uv_executable,
                    "pip",
                    "install",
                    "--force-reinstall",
                    "-U",
                    *freeze,
                ],
                cwd=target_dir,
                env=self.prepare_venv_environment(target_dir),
            )
            log.info("Freeze output: stdout=%s stderr=%s", stdout, stderr)
            progress.success(f"Freeze list applied for package '{package_name}'")

        return dependencies

    @overload
    def run_in_venv(
        self,
        project_path: Path,
        command: list[str],
        capture_to_file: Path | None = None,
        tee_output: bool = True,
        check_output: Literal[False] = False,
        timeout: float | None = None,
        cwd: Path | None = None,
    ) -> None: ...

    @overload
    def run_in_venv(
        self,
        project_path: Path,
        command: list[str],
        capture_to_file: Path | None = None,
        tee_output: bool = True,
        check_output: Literal[True] = True,
        timeout: float | None = None,
        cwd: Path | None = None,
    ) -> str: ...

    def run_in_venv(
        self,
        project_path: Path,
        command: list[str],
        capture_to_file: Path | None = None,
        tee_output: bool = True,
        check_output: bool = False,
        timeout: float | None = None,
        cwd: Path | None = None,
    ) -> None | str:
        """Run a command inside the virtual environment.

        :param project_path: Path to the project with virtual environment
        :param command: Command and arguments to execute
        :param capture_to_file: Optional file path to capture command output
        :param tee_output: If True and capture_to_file is set, output to both file and stdout/stderr
        :param timeout: Optional timeout in seconds. None means no timeout.

        :raises subprocess.CalledProcessError: If the command fails
        :raises subprocess.TimeoutExpired: If the command exceeds the timeout
        """
        environment = self.prepare_venv_environment(project_path)
        cwd = cwd or project_path
        if check_output:
            print(f"Checking output of command {command} in directory {cwd}")
            return subprocess.check_output(
                command,
                cwd=cwd,
                env=environment,
                text=True,
            )

        if capture_to_file is None:
            print(f"Running command {command} in directory {cwd}")
            subprocess.run(
                command,
                cwd=cwd,
                env=environment,
                check=True,
                timeout=timeout,
            )
        elif tee_output:
            import shlex

            # Escape each command argument and join them
            escaped_command = " ".join(shlex.quote(arg) for arg in command)

            # Use bash with tee to capture output to file and print to stdout/stderr
            bash_command = f"set -o pipefail; {escaped_command} 2>&1 | tee {shlex.quote(str(capture_to_file))}"
            print(f"Running bash command: {bash_command} in directory {cwd}")
            subprocess.run(
                ["bash", "-c", bash_command],
                cwd=cwd,
                env=environment,
                check=True,
                timeout=timeout,
            )
        else:
            with open(capture_to_file, "w") as f:
                subprocess.run(
                    command,
                    cwd=cwd,
                    env=environment,
                    stdout=f,
                    stderr=subprocess.STDOUT,
                    check=True,
                    timeout=timeout,
                )
