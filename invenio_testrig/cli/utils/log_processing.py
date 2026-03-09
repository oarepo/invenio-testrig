# -*- coding: utf-8 -*-
#
# Copyright (C) 2026 CESNET z.s.p.o.
#
# invenio-testrig is free software; you can redistribute it and/or
# modify it under the terms of the MIT License; see LICENSE file for more
# details.
"""Log processing utilities for test output.

Provides functions to extract warnings from test logs and create simplified
versions by filtering out noise like warnings, docker output, etc.
"""

import json
import re
from collections import defaultdict
from pathlib import Path

# Regular expression patterns for filtering
_WARNING_TYPE_RE = re.compile(
    r"DeprecationWarning|PendingDeprecationWarning|ResourceWarning|"
    r"UserWarning|FutureWarning|ImportWarning|RuntimeWarning"
)

_DOCKER_PULL_RE = re.compile(
    r"^\s*[0-9a-f]{12}\s+(?:Waiting|Pulling|Downloading|Verifying|Download|Extracting|Pull complete)"
)


def _filter_log_line(line: str) -> str | None:
    """Filter/clean one log line; returns None if the line should be dropped.

    :param line: Raw log line to filter
    :return: Cleaned line or None if the line should be removed
    """
    if line.strip().startswith("::warning file="):
        return None
    line = re.sub(r"::warning file=.*", "", line)
    if _WARNING_TYPE_RE.search(line):
        return None
    if re.match(r"^\s*warnings summary", line):
        return None
    if re.match(r"^\s*--.*warnings", line):
        return None
    if re.search(r"[0-9]+ warnings?$", line):
        return None
    if _DOCKER_PULL_RE.match(line):
        return None
    if re.match(r"^[=\-]{10,}$", line.strip()):
        return None
    line = re.sub(r"^\.+", "", line)
    line = re.sub(r"\.+$", "", line)
    return line if line.strip() else None


def process_warnings(
    input_log_path: Path, warnings_json_path: Path, output_log_path: Path
) -> None:
    """Extract warnings from log file and create a filtered version without warnings.

    This function:
    1. Extracts Python warnings from the log file (e.g., DeprecationWarning, RuntimeWarning)
    2. Normalizes and counts occurrences of each warning
    3. Saves warnings to a JSON file
    4. Creates a filtered log file with warnings and other noise removed

    :param input_log_path: Path to the input log file
    :param warnings_json_path: Path where warnings JSON should be saved
    :param output_log_path: Path where filtered log (without warnings) should be saved
    """
    if not input_log_path.exists():
        # If input log doesn't exist, create empty output files
        warnings_json_path.write_text("{}")
        output_log_path.write_text("")
        return

    # Extract warnings
    warnings: dict[str, int] = defaultdict(int)
    warning_pattern = re.compile(r"(\w+Warning:.*?)$")

    with input_log_path.open("r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
        for line in content.splitlines():
            match = warning_pattern.search(line)
            if match:
                warning_text = match.group(1).strip()
                # Normalize by replacing memory addresses with [id]
                warning_text = re.sub(r"0x[0-9a-fA-F]+", "[id]", warning_text)
                warnings[warning_text] += 1

    # Save warnings to JSON
    warnings_json_path.parent.mkdir(parents=True, exist_ok=True)
    warnings_json_path.write_text(json.dumps(dict(warnings), indent=2))

    # Create filtered log without warnings
    output_log_path.parent.mkdir(parents=True, exist_ok=True)

    with input_log_path.open("r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    filtered_lines = []
    for raw_line in lines:
        cleaned = _filter_log_line(raw_line)
        if cleaned is not None:
            filtered_lines.append(cleaned)

    # Write filtered output
    output_log_path.write_text("".join(filtered_lines))
