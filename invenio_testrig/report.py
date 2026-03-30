import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from marshmallow_dataclass import class_schema

from .config import TestedPackageInfo


@dataclass
class ExecutionStatus:
    """Execution status for a tested package."""

    status: str  # e.g. "passed", "failed", "skipped" or "pending"
    package: TestedPackageInfo
    dependencies: list[TestedPackageInfo] = field(default_factory=list)

    @property
    def package_name(self) -> str:
        """Return the package name of the base reference.

        :return: Package name from the reference
        """
        return self.package.reference.package


ExecutionStatusSchema = class_schema(ExecutionStatus)


def load_execution_status(file: str | Path) -> ExecutionStatus:
    """Load execution status from JSON file.

    :param file: Path to the JSON execution status file

    :return: Loaded ExecutionStatus instance

    :raises ValueError: If the file doesn't contain a valid JSON object
    :raises FileNotFoundError: If the file doesn't exist
    """
    path = Path(file)
    with open(path, "r") as stream:
        raw_data = json.load(stream)
        schema = ExecutionStatusSchema()
        if isinstance(raw_data, dict):
            return cast(ExecutionStatus, schema.load(raw_data))
        else:
            raise ValueError(
                f"Expected execution status file to contain a JSON object, got {type(raw_data)}"
            )


def save_execution_status(file: str | Path, status: ExecutionStatus) -> None:
    """Save execution status to JSON file.

    :param file: Path where the execution status should be saved
    :param status: ExecutionStatus instance to save
    """
    path = Path(file)
    formatted_status = json.dumps(
        ExecutionStatusSchema().dump(status), indent=2, sort_keys=True
    )
    path.write_text(formatted_status)


@dataclass
class ReportPackageData:
    """Data structure for package test results in reports."""

    info: TestedPackageInfo
    artefact_dir: str  # directory name relative to the report where the artefacts for this package are stored (e.g. logs, status files, etc.)
    patched: ExecutionStatus
    original: ExecutionStatus
