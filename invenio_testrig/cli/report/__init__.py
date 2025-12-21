# -*- coding: utf-8 -*-
#
# Copyright (C) 2026 CESNET z.s.p.o.
#
# invenio-testrig is free software; you can redistribute it and/or
# modify it under the terms of the MIT License; see LICENSE file for more
# details.
"""Report generation functionality for test results.

This package generates HTML reports from test execution artifacts,
showing test results for both patched and unpatched versions of packages.
"""

from invenio_testrig.cli.report.archive import archive_report_cmd
from invenio_testrig.cli.report.main_report import report_cmd
from invenio_testrig.cli.report.report_index import reports_index_cmd

__all__ = ["report_cmd", "reports_index_cmd", "archive_report_cmd"]
