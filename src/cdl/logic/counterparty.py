"""Counterparty validation and the ownership chain walk.

The confirmed rule for this tool: decide on the SUBMITTED counterparty only. Parent
figures are reference information and never decide Y/N.
"""

from __future__ import annotations

from typing import Callable, Iterable, Sequence

from .. import constants
from ..treats.tabular import Record


class CounterpartyError(ValueError):
    """The counterparty is not acceptable, or was not found."""


def validate_counterparty(raw: str) -> str:
    """Uppercase alphanumeric, length EXACTLY 4 or EXACTLY 7. Checked before any call."""
    text = str(raw or "").strip().upper()
    if not text:
        raise CounterpartyError(
            "counterparty is required: 4 or 7 characters, e.g. ABCD or ABCDEFG"
        )
    if not text.isalnum() or not text.isascii():
        raise CounterpartyError(
            f"counterparty {text!r} must be alphanumeric (letters and digits only)"
        )
    if len(text) not in (4, 7):
        raise CounterpartyError(
            f"counterparty {text!r} has length {len(text)}; it must be exactly 4 or "
            "exactly 7 characters, e.g. ABCD or ABCDEFG"
        )
    return text


def find_row(rows: Iterable[Record], acronym: str) -> Record | None:
    """The TTCPIPP row for one acronym."""
    wanted = str(acronym).strip().upper()
    for row in rows:
        if str(row.get(constants.COL_CPTY_ACRONYM, "") or "").strip().upper() == wanted:
            return row
    return None


def parent_of(row: Record | None) -> str | None:
    """XJPRAC of one row, or None when it is an ultimate parent."""
    if row is None:
        return None
    parent = str(row.get(constants.COL_CPTY_PARENT, "") or "").strip().upper()
    return parent or None


def parent_chain(
    counterparty: str,
    fetch_row: Callable[[str], Record | None],
    *,
    max_depth: int = constants.MAX_CHAIN_DEPTH,
) -> list[str]:
    """Follow XJPRAC repeatedly, submitted counterparty first. PROVISIONAL rule.

    Stops at an empty parent, at a cycle, or at `max_depth` nodes.
    """
    start = validate_counterparty(counterparty)
    first = fetch_row(start)
    if first is None:
        raise CounterpartyError(
            f"counterparty {start} was not found in {constants.TABLE_COUNTERPARTY}"
        )
    chain = [start]
    seen = {start}
    row = first
    while len(chain) < max_depth:
        parent = parent_of(row)
        if parent is None or parent in seen:
            break
        chain.append(parent)
        seen.add(parent)
        row = fetch_row(parent)
        if row is None:
            break
    return chain


def chain_from_rows(counterparty: str, rows: Sequence[Record]) -> list[str]:
    """Chain walk over an already fetched TTCPIPP export."""
    return parent_chain(counterparty, lambda acronym: find_row(rows, acronym))
