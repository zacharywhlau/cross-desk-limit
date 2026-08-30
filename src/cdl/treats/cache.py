"""dev_cache: real-shaped rows on disk for the weekend / endpoint-down workflow.

Written by `extract --save-cache`, read when a table is set to `cache`. The directory
is gitignored and must never be committed, never be written to the shared network
folder and never be used in production. Writes outside the configured dev_cache path
are refused.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from ..config import Settings
from ..logging_setup import get_logger
from .tabular import Record, TabularError, read_table_file, write_csv

READ_SUFFIXES = (".csv", ".xlsx", ".xlsm", ".xls")

_logger = get_logger("treats.cache")


class CachePathError(RuntimeError):
    """A cache file would be written outside the configured dev_cache directory."""


def cache_dir(settings: Settings) -> Path:
    return Path(settings.paths.dev_cache)


def find_cache_file(table: str, settings: Settings) -> Path | None:
    """First existing dev_cache/<TABLE>.<ext>, CSV preferred."""
    directory = cache_dir(settings)
    for suffix in READ_SUFFIXES:
        candidate = directory / f"{table}{suffix}"
        if candidate.is_file():
            return candidate
    return None


def fetch(table: str, settings: Settings) -> tuple[list[Record], Path]:
    """Load one table from dev_cache. Returns (rows, path)."""
    path = find_cache_file(table, settings)
    if path is None:
        raise TabularError(
            f"no cache file for {table} in {cache_dir(settings)}; create one with "
            "`run_check extract --save-cache` while the endpoint works, or save a "
            f"manual export as {table}.csv"
        )
    rows = read_table_file(path)
    _logger.info("fetch source=cache table=%s rows=%d file=%s", table, len(rows), path.name)
    return rows, path


def target_path(table: str, settings: Settings) -> Path:
    """Where a cache file for `table` may be written."""
    directory = cache_dir(settings).resolve()
    path = (directory / f"{table}.csv").resolve()
    if path.parent != directory:
        raise CachePathError(
            f"refusing to write {path}: cache files must stay inside {directory}"
        )
    return path


def save(table: str, rows: Sequence[Record], settings: Settings) -> Path:
    """Write one table to dev_cache/<TABLE>.csv."""
    path = target_path(table, settings)
    written = write_csv(path, rows)
    _logger.info("cache write table=%s rows=%d file=%s", table, len(rows), written)
    return written
