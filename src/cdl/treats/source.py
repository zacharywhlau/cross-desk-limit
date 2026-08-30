"""Per-table source resolution: mock | api | cache -> list[dict].

One function every caller uses, so a table can be switched to the real source on its
own, and so every fetch is logged the same way.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from .. import constants
from ..config import Settings
from ..logging_setup import get_logger, mask_library
from . import api, cache, mock
from . import sql as sql_builder
from .tabular import Record

_logger = get_logger("treats.source")

_SIMPLE_PREDICATE = re.compile(r"^\s*(\w+)\s*=\s*'([^']*)'\s*$")


class SourceError(RuntimeError):
    """A table could not be read from its configured source."""


@dataclass(frozen=True)
class TableFetch:
    """The rows of one table plus where they came from."""

    table: str
    source: str
    rows: list[Record] = field(default_factory=list)
    detail: str = ""
    statement: str | None = None
    elapsed_ms: float = 0.0

    @property
    def row_count(self) -> int:
        return len(self.rows)


def effective_source(table: str, settings: Settings) -> str:
    """The configured source for one table, including the FFR table."""
    if table == settings.ffr.table:
        return settings.ffr.source
    return settings.treats.source_for(table)


def apply_where(rows: list[Record], where: str) -> list[Record]:
    """Apply the simple ``COLUMN='VALUE'`` predicate locally (mock and cache)."""
    match = _SIMPLE_PREDICATE.match(where)
    if not match:
        return rows
    column, wanted = match.group(1), match.group(2).strip().upper()
    return [row for row in rows if str(row.get(column, "") or "").strip().upper() == wanted]


def fetch_table(
    table: str,
    settings: Settings,
    where: str | None = None,
    *,
    source: str | None = None,
) -> TableFetch:
    """Read one table from its configured source. Raises SourceError on failure."""
    mode = source or effective_source(table, settings)
    started = time.monotonic()
    try:
        if mode == constants.SOURCE_MOCK:
            rows = mock.fetch(table, settings)
            detail = str(mock.table_path(table, settings).name)
            statement = None
            if where:
                rows = apply_where(rows, where)
        elif mode == constants.SOURCE_CACHE:
            rows, path = cache.fetch(table, settings)
            detail = path.name
            statement = None
            if where:
                rows = apply_where(rows, where)
        elif mode == constants.SOURCE_API:
            rows, statement = api.fetch(table, settings, where=where)
            detail = sql_builder.qualified_name(settings.treats.library, table)
            detail = mask_library(detail, settings.treats.library)
        else:
            raise SourceError(f"table {table} has an unsupported source {mode!r}")
    except SourceError:
        raise
    except Exception as error:
        raise SourceError(
            f"could not read {table} in {mode} mode: {type(error).__name__}: {error}"
        ) from error
    elapsed_ms = (time.monotonic() - started) * 1000.0
    if mode != constants.SOURCE_API:
        _logger.info(
            "fetch source=%s table=%s rows=%d elapsed_ms=%.1f detail=%s",
            mode, table, len(rows), elapsed_ms, detail,
        )
    return TableFetch(
        table=table,
        source=mode,
        rows=rows,
        detail=detail,
        statement=statement,
        elapsed_ms=elapsed_ms,
    )


def statement_for(table: str, settings: Settings, where: str | None = None) -> str:
    """The SQL that api mode would send, with the library name masked (for display)."""
    if settings.treats.library:
        statement = sql_builder.build_select(settings.treats.library, table, where)
        return mask_library(statement, settings.treats.library)
    statement = f"SELECT * FROM <LIBRARY>.{table}"
    return f"{statement} WHERE {where}" if where else statement
