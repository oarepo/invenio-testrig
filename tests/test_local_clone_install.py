# -*- coding: utf-8 -*-
#
# Copyright (C) 2026 CESNET z.s.p.o.
#
# invenio-testrig is free software; you can redistribute it and/or
# modify it under the terms of the MIT License; see LICENSE file for more
# details.
"""Tests for installing local clones without modifying other installed packages."""

import importlib.metadata
import json
import subprocess
import zipfile
from pathlib import Path

import pytest

from invenio_testrig.progress import SilentProgress
from invenio_testrig.python_api import DependencyResolutionError, PythonAPI

# Minimal self-contained PEP 517 backend, so that local clones can be built
# offline without downloading setuptools or any other build backend.
_BACKEND = '''
import zipfile
from pathlib import Path

NAME, VERSION, REQUIRES = {name!r}, {version!r}, {requires!r}


def _build(wheel_directory):
    module = NAME.replace("-", "_")
    filename = f"{{module}}-{{VERSION}}-py3-none-any.whl"
    dist_info = f"{{module}}-{{VERSION}}.dist-info"
    metadata = f"Metadata-Version: 2.1\\nName: {{NAME}}\\nVersion: {{VERSION}}\\n"
    metadata += "".join(f"Requires-Dist: {{r}}\\n" for r in REQUIRES)
    with zipfile.ZipFile(Path(wheel_directory) / filename, "w") as zf:
        zf.writestr(f"{{module}}/__init__.py", "")
        zf.writestr(f"{{dist_info}}/METADATA", metadata)
        zf.writestr(
            f"{{dist_info}}/WHEEL",
            "Wheel-Version: 1.0\\nGenerator: test\\nRoot-Is-Purelib: true\\n"
            "Tag: py3-none-any\\n",
        )
        zf.writestr(f"{{dist_info}}/RECORD", "")
    return filename


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    return _build(wheel_directory)


def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
    return _build(wheel_directory)
'''


def _make_wheel(directory: Path, name: str, version: str, requires=()) -> Path:
    """Create a minimal pure-python wheel."""
    module = name.replace("-", "_")
    dist_info = f"{module}-{version}.dist-info"
    wheel = directory / f"{module}-{version}-py3-none-any.whl"
    metadata = f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
    metadata += "".join(f"Requires-Dist: {r}\n" for r in requires)
    with zipfile.ZipFile(wheel, "w") as zf:
        zf.writestr(f"{module}/__init__.py", "")
        zf.writestr(f"{dist_info}/METADATA", metadata)
        zf.writestr(
            f"{dist_info}/WHEEL",
            "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\n"
            "Tag: py3-none-any\n",
        )
        zf.writestr(f"{dist_info}/RECORD", "")
    return wheel


class Env:
    """A venv with a fake project, local clones and a local find-links 'index'."""

    def __init__(self, tmp_path: Path):
        self.root = tmp_path
        self.index = tmp_path / "index"
        self.wheels = tmp_path / "wheels"
        self.packages = tmp_path / "cloned" / "packages"
        self.patched = tmp_path / "cloned" / "patched"
        self.project = tmp_path / "project"
        for d in (self.index, self.wheels, self.packages, self.patched, self.project):
            d.mkdir(parents=True)
        self.api = PythonAPI(
            {"UV_NO_INDEX": "1", "UV_FIND_LINKS": str(self.index), "UV_OFFLINE": "1"}
        )
        self._uv("venv", "-q", str(self.project / ".venv"))

    def _uv(self, *args):
        subprocess.run(
            ["uv", *args],
            check=True,
            cwd=self.project,
            env=self.api.prepare_venv_environment(self.project),
        )

    def publish(self, name, version, requires=()):
        """Make a distribution available on the 'index'."""
        _make_wheel(self.index, name, version, requires)

    def preinstall(self, name, version, requires=()):
        """Install a distribution directly, without dependencies."""
        wheel = _make_wheel(self.wheels, name, version, requires)
        self._uv("pip", "install", "-q", "--no-deps", str(wheel))

    def make_project(self, directory: Path, name, version, requires=()) -> Path:
        directory.mkdir(parents=True)
        (directory / "pyproject.toml").write_text(
            f'[project]\nname = "{name}"\nversion = "{version}"\n'
            f"dependencies = {json.dumps(list(requires))}\n"
            "[build-system]\nrequires = []\n"
            'build-backend = "_testrig_backend"\nbackend-path = ["."]\n'
        )
        (directory / "_testrig_backend.py").write_text(
            _BACKEND.format(name=name, version=version, requires=list(requires))
        )
        return directory

    def clone(self, name, version, requires=(), patched=False):
        """Create a local clone of a package."""
        root = self.patched if patched else self.packages
        return self.make_project(root / name, name, version, requires)

    def installed(self) -> dict[str, tuple[str, str | None]]:
        return self.api._installed_distributions(self.project)

    def versions(self) -> dict[str, str]:
        return {name: version for name, (version, _) in self.installed().items()}

    def run(self):
        self.progress = RecordingProgress()
        return self.api.install_patched_dependencies(
            project_path=self.project,
            packages_root=self.packages,
            patched_packages_root=self.patched,
            install_patched_dependencies=True,
            progress=self.progress,
        )


class RecordingProgress(SilentProgress):
    def __init__(self):
        self.warnings: list[str] = []

    def warning(self, message: str, icon: str | None = None) -> None:
        self.warnings.append(message)


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def test_no_clones_is_noop(env):
    env.preinstall("fake-a", "1.0")
    before = env.installed()
    assert env.run() == []
    assert env.installed() == before


def test_new_dependency_of_clone_is_installed(env):
    # the released fake-lib is installed; its master clone adds a new dependency
    env.preinstall("fake-lib", "2.0", ["fake-pinned"])
    env.preinstall("fake-pinned", "1.0")
    env.clone("fake-lib", "2.0", ["fake-pinned", "fake-new>=1"])
    # the index has a newer fake-pinned that the resolver would otherwise prefer
    env.publish("fake-pinned", "1.0")
    env.publish("fake-pinned", "3.0")
    env.publish("fake-new", "1.5", ["fake-pinned>=1", "fake-transitive"])
    env.publish("fake-transitive", "0.1")

    env.run()

    installed = env.installed()
    assert env.versions() == {
        "fake-lib": "2.0",
        "fake-new": "1.5",
        "fake-pinned": "1.0",
        "fake-transitive": "0.1",
    }
    # fake-lib was really replaced by the clone
    assert str(env.packages / "fake-lib") in json.loads(installed["fake-lib"][1])["url"]


def test_patched_clone_has_priority(env):
    env.preinstall("fake-lib", "1.0")
    env.clone("fake-lib", "1.0")
    env.clone("fake-lib", "1.0", patched=True)

    assert env.run() == ["fake-lib"]
    assert str(env.patched / "fake-lib") in json.loads(env.installed()["fake-lib"][1])["url"]


def test_clone_with_different_version(env):
    # an unreleased master usually carries a version that is not on the index
    env.preinstall("fake-root", "1.0", ["fake-lib>=1"])
    env.preinstall("fake-lib", "1.0")
    env.clone("fake-lib", "1.1.dev0")

    env.run()

    assert env.versions()["fake-lib"] == "1.1.dev0"
    assert env.versions()["fake-root"] == "1.0"


def test_upgrade_required_by_clone_fails_and_changes_nothing(env):
    env.preinstall("fake-lib", "1.0")
    env.preinstall("fake-pinned", "1.0")
    env.clone("fake-lib", "1.1", ["fake-pinned>=2"])
    env.publish("fake-pinned", "2.0")
    before = env.installed()

    with pytest.raises(DependencyResolutionError):
        env.run()

    assert env.installed() == before


def test_new_dependency_requiring_upgrade_fails_and_changes_nothing(env):
    env.preinstall("fake-lib", "1.0")
    env.preinstall("fake-pinned", "1.0")
    env.clone("fake-lib", "1.0", ["fake-new"])
    env.publish("fake-new", "1.0", ["fake-pinned>=2"])
    env.publish("fake-pinned", "2.0")
    before = env.installed()

    with pytest.raises(DependencyResolutionError):
        env.run()

    assert env.installed() == before


def test_editable_install_is_kept(env):
    editable = env.make_project(env.root / "editable", "fake-editable", "1.0")
    env._uv("pip", "install", "-q", "-e", str(editable))
    env.preinstall("fake-lib", "1.0", ["fake-editable"])
    env.clone("fake-lib", "1.0", ["fake-editable", "fake-new"])
    env.publish("fake-editable", "1.0")  # same version on the index must not win
    env.publish("fake-new", "1.0")
    before = env.installed()["fake-editable"]

    env.run()

    assert env.installed()["fake-editable"] == before
    assert json.loads(before[1])["dir_info"]["editable"] is True


# --- release train: a clone bumps a version that dependents do not allow yet ---


def test_release_train_dependent_not_cloned(env):
    env.preinstall("fake-dependent", "1.0", ["fake-lib>=1,<2"])
    env.preinstall("fake-lib", "1.0")
    env.clone("fake-lib", "2.0", ["fake-new"])
    env.publish("fake-new", "1.0")

    env.run()

    assert env.versions() == {
        "fake-dependent": "1.0",
        "fake-lib": "2.0",
        "fake-new": "1.0",
    }
    assert any(
        "fake-dependent" in w and "fake-lib" in w for w in env.progress.warnings
    )


def test_release_train_dependent_is_also_cloned(env):
    # e.g. invenio-base bumped to a new major while invenio-accounts master
    # still pins the old one in its base dependencies
    env.preinstall("fake-dependent", "1.0", ["fake-lib>=1,<2"])
    env.preinstall("fake-lib", "1.0")
    env.preinstall("fake-pinned", "1.0")
    env.clone("fake-lib", "2.0", ["fake-new", "fake-pinned"])
    env.clone("fake-dependent", "1.1.dev0", ["fake-lib>=1,<2"])
    env.publish("fake-new", "1.0")
    env.publish("fake-pinned", "9.0")

    env.run()

    assert env.versions() == {
        "fake-dependent": "1.1.dev0",
        "fake-lib": "2.0",
        "fake-new": "1.0",
        "fake-pinned": "1.0",  # still pinned, although 9.0 is available
    }
    assert any(
        "fake-dependent" in w and "fake-lib" in w for w in env.progress.warnings
    )


def test_release_train_clone_still_cannot_upgrade_non_clones(env):
    env.preinstall("fake-lib", "1.0")
    env.preinstall("fake-pinned", "1.0")
    env.clone("fake-lib", "2.0", ["fake-pinned>=2"])
    env.publish("fake-pinned", "2.0")
    before = env.installed()

    with pytest.raises(DependencyResolutionError):
        env.run()

    assert env.installed() == before


def test_consistent_venv_produces_no_warnings(env):
    env.preinstall("fake-dependent", "1.0", ["fake-lib>=1"])
    env.preinstall("fake-lib", "1.0")
    env.clone("fake-lib", "2.0")

    env.run()

    assert env.progress.warnings == []
