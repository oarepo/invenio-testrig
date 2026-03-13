# -*- coding: utf-8 -*-
#
# Copyright (C) 2026 CESNET z.s.p.o.
#
# invenio-testrig is free software; you can redistribute it and/or
# modify it under the terms of the MIT License; see LICENSE file for more
# details.
"""Main CLI entry point and command group definition.

Defines the top-level CLI group and registers all subcommands including setup,
test, report, github, matrix, and cache management.
"""

import click

from invenio_testrig.cli.github import github_cmd  # noqa
from invenio_testrig.cli.matrix import matrix_cmd  # noqa
from invenio_testrig.cli.merge import cmd_merge_test_artifacts  # noqa
from invenio_testrig.cli.repo_test import cmd_repo
from invenio_testrig.cli.report import (  # noqa
    archive_report_cmd,
    report_cmd,
    reports_index_cmd,
)
from invenio_testrig.cli.setup import setup_cmd  # noqa
from invenio_testrig.cli.test import cmd_test  # noqa


class InsertionOrderGroup(click.Group):
    def list_commands(self, ctx):
        return list(self.commands.keys())  # no sorting


@click.group(cls=InsertionOrderGroup)
def cli():
    """Workflow commands for testing invenio packages."""
    pass


cli.add_command(github_cmd)
cli.add_command(setup_cmd)
cli.add_command(cmd_test)
cli.add_command(cmd_repo)
cli.add_command(cmd_merge_test_artifacts)
cli.add_command(report_cmd)
cli.add_command(reports_index_cmd)
cli.add_command(archive_report_cmd)
cli.add_command(matrix_cmd)

if __name__ == "__main__":
    cli()
