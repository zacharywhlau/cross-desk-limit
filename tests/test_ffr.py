"""§22: percent parsing, FX currency derivation, class mapping, quarter-column fallback."""

from __future__ import annotations

import csv
import logging
from pathlib import Path

import pytest

from cdl import constants
from cdl.config import Settings
from cdl.logic.ffr import (
    FfrError,
    classifying_currency,
    currency_class,
    lookup_ffr,
    resolve_ffr_selection,
    select_weight_column,
)
from cdl.logic.numbers import parse_percent
from conftest import settings_with_mock_dir


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1%", 0.01),
        ("2.5%", 0.025),
        (" 1.8% ", 0.018),
        ("0.9%", 0.009),
        (0.01, 0.01),
        (1, 0.01),
        (2.5, 0.025),
        ("0.018", 0.018),
        ("18", 0.18),
    ],
)
def test_percent_parsing(raw: object, expected: float) -> None:
    assert parse_percent(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw", ["", None, "abc", "%"])
def test_unusable_weight_is_an_error(raw: object) -> None:
    with pytest.raises(ValueError):
        parse_percent(raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("HKD", "HKD"),
        ("hkd", "HKD"),
        ("USDHKD", "HKD"),
        ("EURUSD", "EUR"),
        ("usdjpy", "JPY"),
        ("EURHKD", "HKD"),
        ("EUR/HKD", "HKD"),
    ],
)
def test_fx_currency_derivation(raw: str, expected: str) -> None:
    assert classifying_currency(raw) == expected


@pytest.mark.parametrize("raw", ["US", "USDHK", "USDHKDX", ""])
def test_bad_pair_is_an_error(raw: str) -> None:
    with pytest.raises(FfrError):
        classifying_currency(raw)


@pytest.mark.parametrize(
    ("currency", "expected"),
    [("HKD", "Low"), ("CAD", "Normal"), ("KRW", "Medium"), ("TRY", "High")],
)
def test_class_mapping(currency: str, expected: str) -> None:
    assert currency_class(currency) == expected


def test_unlisted_currency_falls_back_to_the_most_volatile_class() -> None:
    assert currency_class("XYZ") == constants.DEFAULT_CURRENCY_CLASS == "High"


def test_selection_is_a_file_per_class_in_mock_mode(settings: Settings) -> None:
    assert resolve_ffr_selection("FX", "Low", settings)[0] == "FFR_FX_LOW"
    assert resolve_ffr_selection("Gold", None, settings)[0] == "FFR_GOLD"
    assert resolve_ffr_selection("IRS", None, settings)[0] == "FFR_IRS"
    assert resolve_ffr_selection("Equity swaps", None, settings)[0] == "FFR_EQ_SWAP"


def test_selection_is_the_configured_table_in_api_mode(settings: Settings) -> None:
    from dataclasses import replace

    api_settings = replace(settings, ffr=replace(settings.ffr, source="api"))
    table, description = resolve_ffr_selection("FX", "Low", api_settings)
    assert table == "CKBLOTP"
    assert "PROVISIONAL" in description


def test_reference_case_weight(settings: Settings) -> None:
    lookup = lookup_ffr("FX", "USDHKD", "1 months", settings)
    assert lookup.weight == pytest.approx(0.018)
    assert lookup.table_name == "FFR_FX_LOW"
    assert lookup.currency_class == "Low"
    assert lookup.weight_column == "2025Q2"
    assert lookup.time_period == "1 months"


@pytest.mark.parametrize("product", constants.PRODUCTS)
def test_every_product_resolves_a_weight(product: str, settings: Settings) -> None:
    currency = "USDHKD" if product == "FX" else constants.NON_FX_CURRENCY[product]
    lookup = lookup_ffr(product, currency, "1 months", settings)
    assert lookup.weight > 0


def test_longer_tenor_consumes_more_headroom(settings: Settings) -> None:
    short = lookup_ffr("FX", "USDHKD", "1 months", settings).weight
    long = lookup_ffr("FX", "USDHKD", "10 years", settings).weight
    assert long > short


def test_more_volatile_class_has_a_higher_weight(settings: Settings) -> None:
    low = lookup_ffr("FX", "USDHKD", "1 months", settings).weight
    high = lookup_ffr("FX", "USDTRY", "1 months", settings).weight
    assert high > low


def _write_grid(directory: Path, name: str, columns: list[str]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / f"{name}.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Time Period", *columns])
        writer.writerow(["Spot", *[f"{0.5 + index}%" for index in range(len(columns))]])
        writer.writerow(["1 months", *[f"{1.0 + index}%" for index in range(len(columns))]])


def test_missing_quarter_column_falls_back_and_logs(
    settings: Settings,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _write_grid(tmp_path / "grids", "FFR_FX_LOW", ["2024Q4", "2025Q1"])
    local = settings_with_mock_dir(settings, tmp_path / "grids")
    with caplog.at_level(logging.WARNING, logger="cdl.logic.ffr"):
        lookup = lookup_ffr("FX", "USDHKD", "1 months", local)
    assert settings.ffr.weight_column == "2025Q2"
    assert lookup.weight_column == "2025Q1"
    assert lookup.weight == pytest.approx(0.02)
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "2025Q2" in logged and "2025Q1" in logged


def test_fallback_picks_the_highest_sorting_quarter() -> None:
    chosen = select_weight_column(
        ["Time Period", "2024Q4", "2025Q1", "2025Q3", "note"], "2025Q2", where="test"
    )
    assert chosen == "2025Q3"


def test_no_quarter_column_at_all_is_an_error() -> None:
    with pytest.raises(FfrError):
        select_weight_column(["Time Period", "weight"], "2025Q2", where="test")


def test_api_mode_reads_the_configured_table_through_the_connector(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The FFR api path uses the same connector, payload and SQL builder as any table."""
    pd = pytest.importorskip("pandas")
    from dataclasses import replace

    from cdl.treats import api

    api_settings = replace(
        settings,
        treats=replace(settings.treats, url="http://<URL>", library="MYLIB"),
        ffr=replace(settings.ffr, source="api"),
    )
    seen: dict[str, object] = {}

    def fake_connector(url: str, payload: dict[str, object]):
        seen["url"] = url
        seen["payload"] = payload
        return pd.DataFrame(
            [
                {"Time Period": "Spot", "2025Q1": "0.9%", "2025Q2": "1%"},
                {"Time Period": "1 months", "2025Q1": "2.4%", "2025Q2": "2.5%"},
            ]
        )

    monkeypatch.setattr(api, "query_to_dataframe", fake_connector)
    lookup = lookup_ffr("FX", "USDHKD", "1 months", api_settings)

    assert lookup.weight == pytest.approx(0.025)
    assert lookup.table_name == "CKBLOTP"
    assert lookup.source_label.startswith("api:")
    payload = seen["payload"]
    assert isinstance(payload, dict)
    assert payload["libandfile"] == [{"library": "MYLIB", "file": "CKBLOTP"}]
    assert payload["fullSQL"] == "SELECT * FROM MYLIB.CKBLOTP"
    assert payload["startRow"] is None and payload["endRow"] is None


def test_missing_time_period_is_an_error(settings: Settings, tmp_path: Path) -> None:
    _write_grid(tmp_path / "grids", "FFR_FX_LOW", ["2025Q2"])
    local = settings_with_mock_dir(settings, tmp_path / "grids")
    with pytest.raises(FfrError) as error:
        lookup_ffr("FX", "USDHKD", "3 months", local)
    assert "3 months" in str(error.value)
