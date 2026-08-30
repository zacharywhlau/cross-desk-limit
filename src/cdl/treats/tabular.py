"""One shared tabular reader/writer: CSV, XLSX and DataFrame -> list[dict].

This module is the ONLY pandas import site inside src/. pandas is imported lazily so
CSV work (mock and cache) keeps working when pandas is not installed. Values from CSV
arrive as strings, exactly as a SQL export would; use `numbers.to_float` in the logic
layer rather than trusting a source to type things.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Iterable, Sequence

Record = dict[str, Any]


class TabularError(RuntimeError):
    """A tabular file could not be read or written."""


def _clean_key(key: Any) -> str:
    return str(key).strip()


def _clean_value(value: Any) -> Any:
    return value.strip() if isinstance(value, str) else value


def normalise_records(rows: Iterable[Record]) -> list[Record]:
    """Strip keys and string values; drop unnamed columns from ragged exports."""
    cleaned: list[Record] = []
    for row in rows:
        item = {
            _clean_key(key): _clean_value(value)
            for key, value in row.items()
            if _clean_key(key) != ""
        }
        if any(str(value or "").strip() != "" for value in item.values()):
            cleaned.append(item)
    return cleaned


def read_csv(path: str | Path) -> list[Record]:
    """Read a CSV export into list[dict] (stdlib only)."""
    file_path = Path(path)
    try:
        with file_path.open("r", encoding="utf-8-sig", newline="") as handle:
            return normalise_records(dict(row) for row in csv.DictReader(handle))
    except FileNotFoundError as error:
        raise TabularError(f"file not found: {file_path}") from error
    except OSError as error:
        raise TabularError(f"could not read {file_path}: {error}") from error


def read_xlsx(path: str | Path, sheet: str | int = 0) -> list[Record]:
    """Read one sheet of a workbook. Requires pandas and openpyxl."""
    file_path = Path(path)
    try:
        import pandas as pd
    except ImportError as error:  # pragma: no cover - depends on the environment
        raise TabularError(
            f"reading {file_path.name} needs pandas and openpyxl; install them or use CSV"
        ) from error
    try:
        frame = pd.read_excel(file_path, sheet_name=sheet, dtype=str)
    except FileNotFoundError as error:
        raise TabularError(f"file not found: {file_path}") from error
    except Exception as error:  # pragma: no cover - pandas/openpyxl error surface
        raise TabularError(f"could not read {file_path}: {error}") from error
    return dataframe_to_records(frame)


def read_table_file(path: str | Path, sheet: str | int = 0) -> list[Record]:
    """Read .csv or .xlsx by extension."""
    file_path = Path(path)
    if file_path.suffix.lower() in (".xlsx", ".xlsm", ".xls"):
        return read_xlsx(file_path, sheet=sheet)
    return read_csv(file_path)


def dataframe_to_records(frame: Any) -> list[Record]:
    """Convert a pandas DataFrame to list[dict] immediately at the boundary."""
    if not hasattr(frame, "to_dict"):
        raise TabularError(
            "the connector returned "
            f"{type(frame).__name__}, expected a pandas DataFrame"
        )
    frame = frame.where(frame.notna(), None) if hasattr(frame, "notna") else frame
    return normalise_records(frame.to_dict(orient="records"))


def columns_of(rows: Sequence[Record]) -> list[str]:
    """Column order as seen in the export, union over rows."""
    seen: dict[str, None] = {}
    for row in rows:
        for key in row:
            seen.setdefault(key, None)
    return list(seen)


def write_csv(path: str | Path, rows: Sequence[Record]) -> Path:
    """Write list[dict] back out as a CSV export."""
    file_path = Path(path)
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        columns = columns_of(rows)
        with file_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({column: row.get(column, "") for column in columns})
    except OSError as error:
        raise TabularError(f"could not write {file_path}: {error}") from error
    return file_path
