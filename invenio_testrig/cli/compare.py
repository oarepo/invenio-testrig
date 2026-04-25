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

import click
import httpx
from rich.console import Console
from rich.table import Table

from invenio_testrig.config import Config


class ReportHelper:
    def __init__(self, report_url: str):
        if report_url.endswith("index.html"):
            report_url = report_url[:-10]
        if not report_url.endswith("/"):
            report_url += "/"
        self.report_url = report_url

    def fetch_json_config(self) -> dict:
        with httpx.Client() as client:
            response = client.get(self.report_url + "config.json")
            response.raise_for_status()
            return response.json()

    def fetch_tests_status(self) -> tuple[dict, dict]:
        with httpx.Client() as client:
            response = client.get(self.report_url + "data.json")
            response.raise_for_status()
            original_statuses = {}
            patched_statuses = {}
            for pkg in response.json()["packages"]:
                original = pkg.get("original")
                patched = pkg.get("patched")

                if original:
                    original_statuses[original["package"]["reference"]["package"]] = (
                        original["status"]
                    )
                if patched:
                    patched_statuses[patched["package"]["reference"]["package"]] = (
                        patched["status"]
                    )

            return original_statuses, patched_statuses


@click.command("compare", hidden=True)
@click.argument("first_report_url")
@click.argument("second_report_url")
def compare_cmd(first_report_url: str, second_report_url: str):
    console = Console()

    console.print(f"First version: {first_report_url}")
    console.print(f"Second version: {second_report_url}")

    first_report = ReportHelper(first_report_url)
    second_report = ReportHelper(second_report_url)

    first_config = Config.load_from_dict(first_report.fetch_json_config())
    second_config = Config.load_from_dict(second_report.fetch_json_config())

    first_packages = set(first_config.runtime.tested_packages.keys())
    second_packages = set(second_config.runtime.tested_packages.keys())

    # Compare packages that are in both reports and get differences
    differences = []  # pkg_name, first_version, second_version, first_commit, second_commit
    for pkg in first_packages | second_packages:
        first_package = first_config.runtime.tested_packages.get(pkg)
        second_package = second_config.runtime.tested_packages.get(pkg)

        if (
            first_package is None
            or second_package is None
            or first_package.reference.commit != second_package.reference.commit
        ):
            differences.append(
                (
                    pkg,
                    first_package.reference.actual_version
                    if first_package is not None
                    else None,
                    second_package.reference.actual_version
                    if second_package is not None
                    else None,
                    first_package.reference.commit
                    if first_package is not None
                    else None,
                    second_package.reference.commit
                    if second_package is not None
                    else None,
                )
            )

    if differences:
        # generate rich table with the differences

        tbl = Table(
            title="Package Differences",
            show_header=True,
            header_style="bold magenta",
        )
        tbl.add_column("Package", style="cyan", no_wrap=True)
        tbl.add_column("First Version", style="green")
        tbl.add_column("Second Version", style="green")
        tbl.add_column("First Commit", style="yellow")
        tbl.add_column("Second Commit", style="yellow")

        for pkg, first_version, second_version, first_commit, second_commit in sorted(
            differences
        ):
            tbl.add_row(
                pkg,
                first_version or "-",
                second_version or "-",
                first_commit[:12] if first_commit else "-",
                second_commit[:12] if second_commit else "-",
            )
        console.print(tbl)
    else:
        console.print("[green]No package reference differences found.[/green]")

    first_original_statuses, first_patched_statuses = first_report.fetch_tests_status()
    second_original_statuses, second_patched_statuses = (
        second_report.fetch_tests_status()
    )

    original_status_table_data = make_status_diff_data(
        first_original_statuses, second_original_statuses
    )
    patched_status_table_data = make_status_diff_data(
        first_patched_statuses, second_patched_statuses
    )

    if original_status_table_data:
        print_status_table(original_status_table_data, "Original Packages")
    else:
        console.print(
            "[green]No differences found in test statuses of original packages.[/green]"
        )

    if patched_status_table_data:
        print_status_table(patched_status_table_data, "Patched Packages")
    else:
        console.print(
            "[green]No differences found in test statuses of patched packages.[/green]"
        )


def make_status_diff_data(first_statuses, second_statuses):
    ret = []
    for pkg in set(first_statuses.keys()) & set(second_statuses.keys()):
        first_status = first_statuses.get(pkg)
        second_status = second_statuses.get(pkg)

        transformed_first_status = (
            "success" if first_status == "skipped" else first_status
        )
        transformed_second_status = (
            "success" if second_status == "skipped" else second_status
        )

        if transformed_first_status != transformed_second_status:
            ret.append((pkg, first_status, second_status))
    return ret


def print_status_table(status_table_data, test_type):
    console = Console()
    table = Table(
        title=f"Status Differences for {test_type}",
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("Package", style="cyan", no_wrap=True)
    table.add_column("First Status", style="yellow")
    table.add_column("Second Status", style="yellow")

    for pkg, first_status, second_status in sorted(status_table_data):
        first_style = "green" if str(first_status).upper() == "PASSED" else "red"
        second_style = "green" if str(second_status).upper() == "PASSED" else "red"

        table.add_row(
            pkg,
            f"[{first_style}]{first_status}[/{first_style}]",
            f"[{second_style}]{second_status}[/{second_style}]",
        )

    console.print(table)
