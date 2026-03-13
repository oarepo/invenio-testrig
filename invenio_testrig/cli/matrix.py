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
    """Generate GitHub Actions test matrices for tested packages.

    Reads the tested packages from config and writes two JSON matrices to the
    GitHub Actions output file for workflow matrix strategy:
    - matrix_tested_packages: packages with fast tests (slow=False)
    - matrix_slow_tested_packages: packages with slow tests (slow=True)

    Example: invenio-testrig matrix config.json $GITHUB_OUTPUT
    """
    tested_packages = config.tested_packages

    # Split packages into fast and slow
    fast_packages = [
        package for package, info in tested_packages.items() if not info.slow
    ]
    slow_packages = [package for package, info in tested_packages.items() if info.slow]

    with open(github_output_file, "a") as f:
        f.write("\n")
        f.write(f"matrix_tested_packages={json.dumps(fast_packages)}\n")
        f.write(f"matrix_slow_tested_packages={json.dumps(slow_packages)}\n")

    progress.success(
        f"Generated test matrices: {len(fast_packages)} fast packages, "
        f"{len(slow_packages)} slow packages, written to {github_output_file}"
    )
