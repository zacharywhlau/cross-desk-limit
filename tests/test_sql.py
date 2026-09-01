"""The SQL and payload builders: every real read must be narrowed and bounded."""

from __future__ import annotations

import pytest

from cdl.treats.sql import (
    SqlBuildError,
    build_payload,
    build_select,
    equals_clause,
    in_clause,
    qualified_name,
)


def test_select_is_the_library_table_form() -> None:
    assert build_select("MYLIB", "CKSBLMP") == "SELECT * FROM MYLIB.CKSBLMP"
    assert qualified_name("MYLIB", "CKSBLMP") == "MYLIB.CKSBLMP"


def test_equals_clause() -> None:
    assert equals_clause("CFCPTY", "ABCDEFG") == "CFCPTY='ABCDEFG'"
    statement = build_select("MYLIB", "CKSBLMP", equals_clause("CFCPTY", "ABCDEFG"))
    assert statement == "SELECT * FROM MYLIB.CKSBLMP WHERE CFCPTY='ABCDEFG'"


def test_in_clause_covers_a_whole_chain_in_one_query() -> None:
    assert in_clause("CFCPTY", ["ABCDEFG", "ABCDGRP"]) == "CFCPTY IN ('ABCDEFG','ABCDGRP')"


def test_in_clause_upper_cases_and_drops_duplicates() -> None:
    assert in_clause("CICPTY", ["abcd", "ABCD", "efghijk"]) == "CICPTY IN ('ABCD','EFGHIJK')"


def test_in_clause_needs_at_least_one_value() -> None:
    with pytest.raises(SqlBuildError):
        in_clause("CFCPTY", [])


@pytest.mark.parametrize("value", ["ABCD'; DROP TABLE", 'ABCD"', "ABCD;--"])
def test_a_value_that_could_break_out_of_the_literal_is_refused(value: str) -> None:
    with pytest.raises(SqlBuildError):
        equals_clause("CFCPTY", value)
    with pytest.raises(SqlBuildError):
        in_clause("CFCPTY", [value])


@pytest.mark.parametrize("column", ["1CFCPTY", "CF CPTY", "CFCPTY;"])
def test_a_bad_column_name_is_refused(column: str) -> None:
    with pytest.raises(SqlBuildError):
        equals_clause(column, "ABCD")


def test_payload_has_no_paging_by_default() -> None:
    payload = build_payload("MYLIB", "CKSBLMP", "SELECT * FROM MYLIB.CKSBLMP")
    assert payload == {
        "startRow": None,
        "endRow": None,
        "libandfile": [{"library": "MYLIB", "file": "CKSBLMP"}],
        "fullSQL": "SELECT * FROM MYLIB.CKSBLMP",
    }


def test_payload_can_bound_the_read() -> None:
    payload = build_payload(
        "MYLIB", "CKSBLMP", "SELECT * FROM MYLIB.CKSBLMP", start_row=1, end_row=50)
    assert payload["startRow"] == 1
    assert payload["endRow"] == 50
