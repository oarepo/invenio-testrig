# -*- coding: utf-8 -*-
#
# Copyright (C) 2026 CESNET z.s.p.o.
#
# invenio-testrig is free software; you can redistribute it and/or
# modify it under the terms of the MIT License; see LICENSE file for more
# details.
"""GitHub Actions matrix generation command.

Provides the matrix command that generates a JSON matrix of tested packages
for GitHub Actions workflow matrix strategy, enabling parallel test execution.
"""

import json
from pathlib import Path

import click

from invenio_testrig.cli.base import (
    with_config,
    with_debug,
    with_progress,
    with_verbose,
)
from invenio_testrig.config import Config
from invenio_testrig.types import Progress


@click.command("matrix", hidden=True)
@with_progress
@with_config
@click.argument(
    "github_output_file", type=click.Path(path_type=Path, resolve_path=True)
)
@with_verbose
@with_debug
def matrix_cmd(config: Config, github_output_file: Path, progress: Progress):
    """Generate GitHub Actions test matrix for tested packages.

    Reads the tested packages from config and writes a JSON matrix to the
    GitHub Actions output file for workflow matrix strategy.

    Example: invenio-testrig matrix config.json $GITHUB_OUTPUT
    """
    tested_packages = config.tested_packages
    matrix = [package for package in tested_packages.keys()]
    with open(github_output_file, "a") as f:
        f.write("\n")
        f.write(f"matrix_tested_packages={json.dumps(matrix)}\n")
    progress.success(
        f"Generated test matrix for {len(tested_packages)} packages and "
        f"written to {github_output_file}"
    )
