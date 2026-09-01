"""§22: mock CSV loads with the real column names; cache round-trip write-then-read."""

from __future__ import annotations

from pathlib import Path

import pytest

from cdl import constants
from cdl.config import Settings
from cdl.treats import cache, mock
from cdl.treats.cache import CachePathError
from cdl.treats.tabular import TabularError, columns_of, read_csv, write_csv


def test_every_mock_table_exists_as_one_csv(mock_dir: Path) -> None:
    for table in constants.MOCK_TABLES:
        assert (mock_dir / f"{table}.csv").is_file(), table


def test_counterparty_master_uses_the_real_column_names(settings: Settings) -> None:
    rows = mock.fetch(constants.TABLE_COUNTERPARTY, settings)
    assert columns_of(rows) == [constants.COL_CPTY_ACRONYM, constants.COL_CPTY_PARENT]
    assert {row[constants.COL_CPTY_ACRONYM] for row in rows} >= {
        "ABCDEFG", "ABCDGRP", "ABCD", "EFGHIJK"
    }


def test_limit_table_uses_the_real_column_names(settings: Settings) -> None:
    columns = columns_of(mock.fetch(constants.TABLE_LIMITS, settings))
    assert columns[:3] == ["CFCPTY", "CFSLMT", "CFSLTT"]
    assert columns[:3] == [
        constants.COL_LIMIT_COUNTERPARTY,
        constants.COL_LIMIT_TYPE,
        constants.COL_LIMIT_AMOUNT,
    ]
    # One occupied and one limit column per period, zero padded to two digits.
    for slot in range(1, 15):
        assert constants.occupied_column(slot) in columns
        assert constants.slot_limit_column(slot) in columns
    assert "CFSO01" in columns and "CFSO14" in columns
    assert "CFSL01" in columns and "CFSL14" in columns


def test_agreement_table_uses_the_real_column_names(settings: Settings) -> None:
    columns = columns_of(mock.fetch(constants.TABLE_AGREEMENT, settings))
    assert columns == ["CICPTY", "CIRFMG"]


def test_limit_type_codes_are_written_as_the_desk_screen_shows_them(
    settings: Settings
) -> None:
    codes = {
        row[constants.COL_LIMIT_TYPE]
        for row in mock.fetch(constants.TABLE_LIMITS, settings)
    }
    assert codes == {"FX 01", "GD 01", "IR 01", "EQ 01"}


def test_ffr_grid_is_a_time_period_by_quarter_grid(settings: Settings) -> None:
    rows = mock.fetch("FFR_FX_LOW", settings)
    columns = columns_of(rows)
    assert columns[0] == constants.COL_FFR_TIME_PERIOD
    assert columns[1:] == ["2025Q1", "2025Q2", "2025Q3"]
    assert len(rows) == len(constants.TENOR_GRID)
    assert [row[constants.COL_FFR_TIME_PERIOD] for row in rows] == list(constants.TENOR_GRID)


def test_mock_values_are_obviously_synthetic(settings: Settings) -> None:
    texts = " ".join(
        str(value)
        for row in mock.fetch(constants.TABLE_AGREEMENT, settings)
        for value in row.values()
    )
    assert "SYNTHETIC MOCK TEXT" in texts


def test_missing_mock_table_names_the_expected_path(settings: Settings) -> None:
    with pytest.raises(TabularError) as error:
        mock.fetch("NOSUCHTABLE", settings)
    assert "NOSUCHTABLE" in str(error.value)


def test_cache_round_trip(settings: Settings) -> None:
    rows = mock.fetch(constants.TABLE_LIMITS, settings)
    path = cache.save(constants.TABLE_LIMITS, rows, settings)
    assert path.parent == settings.paths.dev_cache
    loaded, loaded_path = cache.fetch(constants.TABLE_LIMITS, settings)
    assert loaded_path == path
    assert loaded == rows
    assert columns_of(loaded) == columns_of(rows)


def test_cache_refuses_to_write_outside_dev_cache(settings: Settings) -> None:
    with pytest.raises(CachePathError):
        cache.target_path("../escaped", settings)


def test_missing_cache_file_explains_how_to_create_one(settings: Settings) -> None:
    with pytest.raises(TabularError) as error:
        cache.fetch(constants.TABLE_LIMITS, settings)
    assert "--save-cache" in str(error.value)


def test_reader_strips_headers_and_skips_blank_lines(tmp_path: Path) -> None:
    path = tmp_path / "T.csv"
    path.write_text(" XJCPAC , XJPRAC \nABCD , \n\n", encoding="utf-8")
    rows = read_csv(path)
    assert rows == [{"XJCPAC": "ABCD", "XJPRAC": ""}]


def test_writer_keeps_the_export_column_order(tmp_path: Path) -> None:
    rows = [{"B": "2", "A": "1"}, {"A": "3", "B": "4"}]
    written = write_csv(tmp_path / "out.csv", rows)
    assert written.read_text(encoding="utf-8").splitlines()[0] == "B,A"


def test_dataframe_conversion_happens_at_the_boundary() -> None:
    pd = pytest.importorskip("pandas")
    from cdl.treats.tabular import dataframe_to_records

    frame = pd.DataFrame([{"CFCPAC": "ABCD", "CFSLMT": 1000}])
    records = dataframe_to_records(frame)
    assert records == [{"CFCPAC": "ABCD", "CFSLMT": 1000}]


def test_pandas_is_not_imported_outside_the_treats_package() -> None:
    """The pandas boundary rule of §8, enforced by reading the source."""
    from cdl.config import project_root

    offenders = []
    for path in (project_root() / "src" / "cdl").rglob("*.py"):
        if path.parent.name == "treats":
            continue
        text = path.read_text(encoding="utf-8")
        if "import pandas" in text:
            offenders.append(str(path.relative_to(project_root())))
    assert offenders == []


def test_forbidden_frameworks_are_absent() -> None:
    from cdl.config import project_root

    root = project_root()
    forbidden = ("fastapi", "streamlit", "sqlalchemy", "pydantic")
    offenders: list[str] = []
    for path in list((root / "src").rglob("*.py")) + list((root / "prototype").rglob("*.py")):
        text = path.read_text(encoding="utf-8").lower()
        for name in forbidden:
            if f"import {name}" in text or f"from {name}" in text:
                offenders.append(f"{path.name}:{name}")
    assert offenders == []
