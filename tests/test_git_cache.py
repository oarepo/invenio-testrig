# -*- coding: utf-8 -*-
#
# Copyright (C) 2026 CESNET z.s.p.o.
#
# invenio-testrig is free software; you can redistribute it and/or
# modify it under the terms of the MIT License; see LICENSE file for more
# details.
"""
Github cache tests.
"""

import tempfile
from pathlib import Path
from typing import Generator

import pytest

from invenio_testrig.github.cache import GitCache
from invenio_testrig.types import SilentProgress


@pytest.fixture(scope="module")
def git() -> Generator[GitCache, None, None]:
    with tempfile.TemporaryDirectory() as tmpdir:
        yield GitCache(cache_dir=Path(tmpdir), extra_env={})


def test_get_repository_path(git: GitCache):
    pth = git.get_repository_path("inveniosoftware", "invenio-records")
    assert pth.exists()
    assert pth.is_dir()


def test_get_pr_commits(git: GitCache):
    pr_info = git.get_pr_info("inveniosoftware", "invenio-records", 327)
    assert pr_info.source_branch == "lazy_gettext"
    assert pr_info.source_org == "mesemus"
    assert pr_info.commits == [
        "27b82097dcfff3bb82383ec20c61ef77f9d63fe7",
        "ebda4bc84a4db709708c4ae100e6856d737a2dcb",
    ]


def test_virtual_pr_info(git: GitCache):
    pr_info = git.virtual_pr_info(
        "inveniosoftware", "invenio-rdm-records", branch="maint-v23.x", base="v23.0.0"
    )
    assert pr_info.source_branch == "maint-v23.x"
    assert pr_info.source_org == "inveniosoftware"
    assert "5e2bdec470950924eb1f24ddc7c80c8d20b3c1ca" in pr_info.commits
    assert "435de740bbf92686d018edd1f7ba90d270a9cba2" in pr_info.commits
    assert len(pr_info.commits) >= 25


def test_get_branch_commit(git: GitCache):
    commit = git.get_branch_commit(
        "inveniosoftware", "invenio-rdm-records", "maint-v23.x"
    )
    pr_info = git.virtual_pr_info(
        "inveniosoftware", "invenio-rdm-records", branch="maint-v23.x", base="v23.0.0"
    )
    assert commit == pr_info.commits[-1]


def test_get_default_branch(git: GitCache):
    branch = git.get_default_branch("inveniosoftware", "invenio-rdm-records")
    assert branch == "master"


def test_get_branches(git: GitCache):
    branches = git.get_branches("inveniosoftware", "invenio-rdm-records")
    assert isinstance(branches, list)
    assert "maint-v23.x" in branches


def test_cache_repositories(git: GitCache):
    git.cache_repositories(
        [
            ("inveniosoftware", "invenio-requests"),
            ("inveniosoftware", "invenio-communities"),
        ],
        SilentProgress(),
    )
    assert (git._cache_dir / "inveniosoftware" / "invenio-requests").exists()
    assert (git._cache_dir / "inveniosoftware" / "invenio-communities").exists()


def test_get_last_version_on_or_before_commit(git: GitCache):
    version = git.get_last_version_on_or_before_commit(
        "inveniosoftware",
        "invenio-rdm-records",
        "1cd121a5d366616c3824bd490e6dcc27b079a644",
    )
    assert version == "25.0.0"


def test_get_commits_from_version(git: GitCache):
    commits = git.get_commits_from_version(
        "inveniosoftware",
        "invenio-rdm-records",
        "1cd121a5d366616c3824bd490e6dcc27b079a644",
        "v25.0.0",
    )
    assert "5052c102b3acf2cbdcda21f9d15efc89a194ca22" in commits
    assert "1cd121a5d366616c3824bd490e6dcc27b079a644" in commits


def test_get_tag_for_version(git: GitCache):
    tag = git.get_tag_for_version(
        "inveniosoftware",
        "invenio-app-rdm",
        "v14.0.0b7.dev1",
    )
    assert tag == "v14.0.0b7.dev1"
    tag = git.get_tag_for_version(
        "inveniosoftware",
        "invenio-app-rdm",
        "14.0.0b7.dev1",
    )
    assert tag == "v14.0.0b7.dev1"
    tag = git.get_tag_for_version(
        "inveniosoftware",
        "invenio-app-rdm",
        "v14.0.0.b7.dev1",
    )
    assert tag == "v14.0.0b7.dev1"
    tag = git.get_tag_for_version(
        "inveniosoftware",
        "invenio-app-rdm",
        "14.0.0.b7.dev1",
    )
    assert tag == "v14.0.0b7.dev1"
