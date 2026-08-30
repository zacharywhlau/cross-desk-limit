"""Mock tables: data/mock_treats/<TABLE>.csv, real column names, synthetic values."""

from __future__ import annotations

from pathlib import Path

from ..config import Settings, project_root
from .tabular import Record, TabularError, read_csv

DEFAULT_MOCK_DIR = project_root() / "data" / "mock_treats"


def mock_dir(settings: Settings | None = None) -> Path:
    return settings.paths.mock_treats if settings is not None else DEFAULT_MOCK_DIR


def table_path(table: str, settings: Settings | None = None) -> Path:
    return mock_dir(settings) / f"{table}.csv"


def fetch(table: str, settings: Settings | None = None) -> list[Record]:
    """Load one mock table. Raises TabularError when the file is missing."""
    path = table_path(table, settings)
    if not path.is_file():
        raise TabularError(
            f"mock table {table} not found at {path}; expected one CSV file per table"
        )
    return read_csv(path)
