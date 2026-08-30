"""§22: 4 and 7 accepted; 5, 6, 8 and empty rejected; plus the chain walk."""

from __future__ import annotations

import pytest

from cdl.logic.counterparty import (
    CounterpartyError,
    chain_from_rows,
    parent_chain,
    validate_counterparty,
)

ROWS = [
    {"XJCPAC": "ABCDEFG", "XJPRAC": "ABCDGRP"},
    {"XJCPAC": "ABCDGRP", "XJPRAC": ""},
    {"XJCPAC": "ABCD", "XJPRAC": ""},
]


@pytest.mark.parametrize("raw", ["ABCD", "ABCDEFG", "abcd", " abcdefg ", "AB12", "A1B2C3D"])
def test_four_or_seven_accepted(raw: str) -> None:
    assert validate_counterparty(raw) == raw.strip().upper()


@pytest.mark.parametrize("raw", ["", "   ", "ABC", "ABCDE", "ABCDEF", "ABCDEFGH", "AB-CD"])
def test_other_lengths_and_shapes_rejected(raw: str) -> None:
    with pytest.raises(CounterpartyError):
        validate_counterparty(raw)


def test_rejection_message_names_the_rule() -> None:
    with pytest.raises(CounterpartyError) as error:
        validate_counterparty("ABCDE")
    assert "exactly 4" in str(error.value) and "exactly 7" in str(error.value)


def test_chain_walk_reaches_the_ultimate_parent() -> None:
    assert chain_from_rows("ABCDEFG", ROWS) == ["ABCDEFG", "ABCDGRP"]


def test_counterparty_without_a_parent_is_a_single_node_chain() -> None:
    assert chain_from_rows("ABCD", ROWS) == ["ABCD"]


def test_missing_counterparty_is_an_error() -> None:
    with pytest.raises(CounterpartyError):
        chain_from_rows("ZZZZ", ROWS)


def test_chain_walk_stops_on_a_cycle() -> None:
    cyclic = [
        {"XJCPAC": "ABCD", "XJPRAC": "WXYZ"},
        {"XJCPAC": "WXYZ", "XJPRAC": "ABCD"},
    ]
    assert chain_from_rows("ABCD", cyclic) == ["ABCD", "WXYZ"]


def test_chain_walk_respects_max_depth() -> None:
    def fetch_row(acronym: str) -> dict[str, str]:
        return {"XJCPAC": acronym, "XJPRAC": acronym[:3] + str((int(acronym[3]) + 1) % 10)}

    chain = parent_chain("ABC0", fetch_row, max_depth=3)
    assert len(chain) == 3
