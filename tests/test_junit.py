# -*- coding: utf-8 -*-
#
# Copyright (C) 2026 CESNET z.s.p.o.
#
# invenio-testrig is free software; you can redistribute it and/or
# modify it under the terms of the MIT License; see LICENSE file for more
# details.
"""Tests for the junit report artifacts."""

from pathlib import Path

from invenio_testrig.cli.merge import _merge_junit_files
from invenio_testrig.cli.utils import junit_env
from invenio_testrig.cli.utils import test_artifact_paths as artifact_paths
from invenio_testrig.progress import SilentProgress

PART_0 = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" errors="0" failures="0" skipped="0" tests="1" time="1.0">
    <testcase classname="tests.test_a" name="test_a" time="1.0" />
  </testsuite>
</testsuites>
"""

PART_1 = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" errors="0" failures="1" skipped="0" tests="1" time="2.0">
    <testcase classname="tests.test_b" name="test_b" time="2.0">
      <failure message="boom">traceback</failure>
    </testcase>
  </testsuite>
</testsuites>
"""


def test_test_artifact_paths_contains_junit_file():
    paths = artifact_paths(Path("/tmp/artifacts/some-package"), "patched")

    assert paths.junit_file == Path("/tmp/artifacts/some-package/patched_junit.xml")
    assert paths.junit_file.parent == paths.log_file.parent


def test_junit_env_sets_pytest_addopts():
    env = junit_env(Path("/tmp/artifacts/pkg/patched_junit.xml"))

    assert env == {"PYTEST_ADDOPTS": "--junit-xml=/tmp/artifacts/pkg/patched_junit.xml"}


def test_junit_env_keeps_configured_pytest_addopts():
    env = junit_env(
        Path("/tmp/artifacts/pkg/patched_junit.xml"),
        {"PYTEST_ADDOPTS": "--color=yes"},
    )

    assert env == {
        "PYTEST_ADDOPTS": "--color=yes --junit-xml=/tmp/artifacts/pkg/patched_junit.xml"
    }


def test_junit_env_quotes_paths_with_spaces():
    env = junit_env(Path("/tmp/my artifacts/pkg/patched_junit.xml"))

    assert env["PYTEST_ADDOPTS"].endswith(
        "--junit-xml='/tmp/my artifacts/pkg/patched_junit.xml'"
    )


def test_merge_junit_files(tmp_path: Path):
    part_0 = tmp_path / "part-0" / "patched_junit.xml"
    part_1 = tmp_path / "part-1" / "patched_junit.xml"
    for part_file, content in ((part_0, PART_0), (part_1, PART_1)):
        part_file.parent.mkdir(parents=True)
        part_file.write_text(content)

    merged = tmp_path / "merged" / "patched_junit.xml"
    merged.parent.mkdir()
    _merge_junit_files([part_0, part_1], merged, "some-package", SilentProgress())

    content = merged.read_text()
    assert content.count("<testsuite ") == 2
    assert "tests.test_a" in content
    assert "tests.test_b" in content
    assert "<failure" in content


def test_merge_junit_files_without_parts_removes_stale_report(tmp_path: Path):
    merged = tmp_path / "patched_junit.xml"
    merged.write_text(PART_0)

    _merge_junit_files([], merged, "some-package", SilentProgress())

    assert not merged.exists()


def test_merge_junit_files_skips_broken_reports(tmp_path: Path):
    broken = tmp_path / "part-0" / "patched_junit.xml"
    broken.parent.mkdir(parents=True)
    broken.write_text("<testsuites>")
    good = tmp_path / "part-1" / "patched_junit.xml"
    good.parent.mkdir(parents=True)
    good.write_text(PART_1)

    merged = tmp_path / "patched_junit.xml"
    _merge_junit_files([broken, good], merged, "some-package", SilentProgress())

    content = merged.read_text()
    assert content.count("<testsuite ") == 1
    assert "tests.test_b" in content
