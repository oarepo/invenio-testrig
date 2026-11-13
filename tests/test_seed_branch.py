# -*- coding: utf-8 -*-
#
# Copyright (C) 2026 CESNET z.s.p.o.
#
# invenio-testrig is free software; you can redistribute it and/or
# modify it under the terms of the MIT License; see LICENSE file for more
# details.
"""Tests for the seed repository selection of the ``github`` command."""

import pytest
from click.testing import CliRunner

from invenio_testrig.cli.github import DEFAULT_SEED_REPOSITORY
from invenio_testrig.cli.github import _seed_repository_with_branch as with_branch
from invenio_testrig.cli.main import cli


def test_default_seed_repository():
    assert DEFAULT_SEED_REPOSITORY == "oarepo/inveniordm-reference-repo@master"


@pytest.mark.parametrize(
    "repository",
    [
        "oarepo/inveniordm-reference-repo",
        "oarepo/inveniordm-reference-repo@master",
        "https://github.com/oarepo/inveniordm-reference-repo",
        "https://github.com/oarepo/inveniordm-reference-repo/tree/main",
    ],
)
def test_seed_branch_is_used(repository: str):
    assert with_branch(repository, "develop") == (
        "oarepo/inveniordm-reference-repo@develop"
    )


def test_seed_branch_without_seed_repository_fails():
    """--seed-branch needs a seed repository, which a config file hides."""
    result = CliRunner().invoke(
        cli, ["github", "--config-file", "my-config.yaml", "--seed-branch", "develop"]
    )

    assert result.exit_code == 2
    assert "--seed-branch needs a seed repository" in result.output
