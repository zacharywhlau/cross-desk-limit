"""SQL and payload builders. The library name always comes from config."""

from __future__ import annotations

import re
from typing import Any, Sequence

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$#.]*$")
_LITERAL = re.compile(r"^[A-Za-z0-9_ .:/+-]*$")


class SqlBuildError(ValueError):
    """A table, column or literal is not safe to put into a statement."""


def _identifier(kind: str, value: str) -> str:
    text = str(value or "").strip()
    if not _IDENTIFIER.match(text):
        raise SqlBuildError(f"{kind} {value!r} is not a valid identifier")
    return text


def _literal(value: str) -> str:
    text = str(value if value is not None else "").strip()
    if not _LITERAL.match(text):
        raise SqlBuildError(f"value {value!r} contains characters that are not allowed")
    return text


def qualified_name(library: str, table: str) -> str:
    """``library.table`` - the form the endpoint expects."""
    return f"{_identifier('library', library)}.{_identifier('table', table)}"


def build_select(library: str, table: str, where: str | None = None) -> str:
    """``SELECT * FROM library.table [WHERE ...]``."""
    statement = f"SELECT * FROM {qualified_name(library, table)}"
    if where:
        statement = f"{statement} WHERE {where.strip()}"
    return statement


def equals_clause(column: str, value: str) -> str:
    """``COLUMN='VALUE'`` with both sides checked."""
    return f"{_identifier('column', column)}='{_literal(value)}'"


def in_clause(column: str, values: Sequence[str]) -> str:
    """``COLUMN IN ('A','B')`` - one query for a whole counterparty chain.

    Callers never hand-write a predicate: the endpoint caps a result set, so every
    real read has to be narrowed, and a hand-built string is where the mistakes live.
    """
    if not values:
        raise SqlBuildError(f"an IN clause on {column} needs at least one value")
    unique: list[str] = []
    for value in values:
        literal = _literal(value).upper()
        if literal not in unique:
            unique.append(literal)
    joined = ",".join(f"'{literal}'" for literal in unique)
    return f"{_identifier('column', column)} IN ({joined})"


def build_payload(
    library: str,
    table: str,
    statement: str,
    *,
    start_row: int | None = None,
    end_row: int | None = None,
) -> dict[str, Any]:
    """The payload accepted by the company connector.

    `start_row` / `end_row` stay None for a normal read (no paging). They are set when
    a command only wants a bounded sample, such as `doctor` probing a table.
    """
    return {
        "startRow": start_row,
        "endRow": end_row,
        "libandfile": [{"library": _identifier("library", library), "file":
                        _identifier("table", table)}],
        "fullSQL": statement,
    }
