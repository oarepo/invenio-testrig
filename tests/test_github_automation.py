# -*- coding: utf-8 -*-
#
# Copyright (C) 2026 CESNET z.s.p.o.
#
# invenio-testrig is free software; you can redistribute it and/or
# modify it under the terms of the MIT License; see LICENSE file for more
# details.
"""Tests for using the ``github`` command from automation (--non-interactive, --name)."""

import json
import subprocess
from importlib.resources import files

import pytest
from click.testing import CliRunner

from invenio_testrig.cli import github
from invenio_testrig.cli.base import ClickProgress
from invenio_testrig.cli.main import cli

WORKFLOWS = files("invenio_testrig.testrig_client") / ".github" / "workflows"


class FakeGh:
    """Stand-in for ``call_executable_quietly``: records commands, answers ``gh`` calls."""

    def __init__(self, runs=None, fail_dispatch=False, runs_appear_after=0):
        self.commands: list[list[str]] = []
        self.runs = runs if runs is not None else []
        self.fail_dispatch = fail_dispatch
        self.runs_appear_after = runs_appear_after
        self.list_calls = 0

    def __call__(self, cmd, always_quiet=False, **kwargs):
        self.commands.append(cmd)
        if cmd[:3] == ["gh", "api", "user"]:
            return "someone\n", ""
        if cmd[:3] == ["gh", "repo", "view"]:
            return "", ""
        if cmd[:3] == ["gh", "workflow", "run"]:
            if self.fail_dispatch:
                raise subprocess.CalledProcessError(1, cmd, "", "HTTP 403")
            return "", ""
        if cmd[:3] == ["gh", "run", "list"]:
            self.list_calls += 1
            runs = self.runs if self.list_calls > self.runs_appear_after else []
            return json.dumps(runs), ""
        raise AssertionError(f"unexpected command {cmd}")


@pytest.fixture
def fake_gh(monkeypatch):
    def install(**kwargs):
        fake = FakeGh(**kwargs)
        monkeypatch.setattr(github, "call_executable_quietly", fake)
        monkeypatch.setattr(github.time, "sleep", lambda seconds: None)
        # The workflow file update clones the repo; it's covered by its own tests.
        monkeypatch.setattr(
            github, "_update_existing_repository_workflow", lambda *args, **kwargs: None
        )
        return fake

    return install


@pytest.fixture
def no_browser(monkeypatch):
    opened = []
    monkeypatch.setattr(github.webbrowser, "open", opened.append)
    return opened


def test_latest_workflow_template_sets_the_run_name():
    latest = ".".join(str(part) for part in github._get_latest_testrig_version())
    content = (WORKFLOWS / f"testrig-{latest}.yml").read_text()

    assert "run-name: ${{ inputs.name || 'Run on testrig' }}" in content


def test_previous_template_is_still_a_known_version_so_it_gets_updated():
    # Repositories still on 1.5.0 must be recognised (not "customized") and updated.
    assert (1, 5, 0) in github._get_testrig_version_md5s()
    assert github._get_latest_testrig_version() > (1, 5, 0)


def test_non_interactive_prints_the_run_identified_by_name(fake_gh, no_browser):
    url = "https://github.com/org/runner/actions/runs/42"
    fake = fake_gh(
        runs=[
            {"databaseId": 41, "url": url[:-2] + "41", "displayTitle": "another-run"},
            {"databaseId": 42, "url": url, "displayTitle": "my-unique-id"},
        ],
        runs_appear_after=2,  # GitHub creates the run asynchronously
    )

    result = CliRunner().invoke(
        cli,
        [
            "github",
            "--target",
            "org/runner",
            "--name",
            "my-unique-id",
            "--non-interactive",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "run-id: 42" in result.output.splitlines()
    assert f"run-url: {url}" in result.output.splitlines()
    assert no_browser == []
    dispatch = next(
        cmd for cmd in fake.commands if cmd[:3] == ["gh", "workflow", "run"]
    )
    assert "name=my-unique-id" in dispatch
    assert fake.list_calls == 3


def test_non_interactive_never_reports_someone_elses_run(fake_gh, no_browser):
    fake_gh(runs=[{"databaseId": 7, "url": "https://x/7", "displayTitle": "other"}])

    result = CliRunner().invoke(
        cli, ["github", "--target", "org/runner", "--name", "mine", "--non-interactive"]
    )

    assert result.exit_code == 0, result.output
    assert "run-id:" not in result.output
    assert no_browser == []


def test_failed_dispatch_exits_with_an_error(fake_gh, no_browser):
    fake_gh(fail_dispatch=True)

    result = CliRunner().invoke(
        cli, ["github", "--target", "org/runner", "--name", "x"]
    )

    assert result.exit_code == 1
    assert no_browser == []


def test_interactive_use_without_name_still_opens_the_latest_run(fake_gh, no_browser):
    url = "https://github.com/org/runner/actions/runs/5"
    fake_gh(runs=[{"databaseId": 5, "url": url}])

    result = CliRunner().invoke(cli, ["github", "--target", "org/runner"])

    assert result.exit_code == 0, result.output
    assert no_browser == [url]
    assert "run-id:" not in result.output


def test_strict_update_fails_when_the_workflow_file_is_customized(
    monkeypatch, tmp_path
):
    def fake_clone(cmd, always_quiet=False, **kwargs):
        if cmd[:3] == ["gh", "repo", "clone"]:
            workflows = (
                tmp_path
                / ".tmp_invenio_testrig_client_update"
                / ".github"
                / "workflows"
            )
            workflows.mkdir(parents=True)
            (workflows / "testrig.yml").write_text("name: customized by hand\n")
        return "", ""

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(github, "call_executable_quietly", fake_clone)

    with pytest.raises(SystemExit) as excinfo:
        github._update_existing_repository_workflow(
            "org/runner", _progress(), strict=True
        )
    assert excinfo.value.code == 1


def test_strict_update_fails_when_clone_fails(monkeypatch, tmp_path):
    def failing(cmd, always_quiet=False, **kwargs):
        raise subprocess.CalledProcessError(128, cmd)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(github, "call_executable_quietly", failing)

    with pytest.raises(SystemExit) as excinfo:
        github._update_existing_repository_workflow(
            "org/runner", _progress(), strict=True
        )
    assert excinfo.value.code == 1

    # Without --non-interactive it stays a warning, as before.
    github._update_existing_repository_workflow("org/runner", _progress(), strict=False)


def _progress():
    return ClickProgress()
