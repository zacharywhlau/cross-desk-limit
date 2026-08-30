"""§22: doctor exits non-zero when a table is set to api but the connector is missing."""

from __future__ import annotations

from pathlib import Path

import pytest

from cdl import cli
from cdl.config import Settings


@pytest.fixture(autouse=True)
def _temp_store(settings: Settings) -> Settings:
    """The `settings` fixture pins db_path and dev_cache inside tmp_path via env vars."""
    return settings


def test_doctor_passes_in_mock_mode(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["doctor"]) == 0
    output = capsys.readouterr().out
    assert "ALL PASS" in output
    assert "[FAIL]" not in output


def test_doctor_fails_when_a_table_is_api_but_the_connector_is_missing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CDL_TREATS_TTCPIPP", "api")
    monkeypatch.setenv("CDL_TREATS_URL", "http://placeholder")
    monkeypatch.setenv("CDL_TREATS_LIBRARY", "PLACEHOLDER")
    assert cli.main(["doctor"]) == 1
    output = capsys.readouterr().out
    assert "company connector pasted" in output
    assert "NOT pasted" in output


def test_doctor_never_prints_the_url(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CDL_TREATS_URL", "http://secret-endpoint.example")
    monkeypatch.setenv("CDL_TREATS_LIBRARY", "SECRETLIB")
    cli.main(["doctor"])
    output = capsys.readouterr().out
    assert "secret-endpoint" not in output
    assert "SECRETLIB" not in output


def test_extract_shows_sql_rows_and_columns(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["extract", "--table", "CKSBLMP", "--rows", "2"]) == 0
    output = capsys.readouterr().out
    assert "SELECT * FROM <LIBRARY>.CKSBLMP" in output
    assert "rows   : 16" in output
    assert "CFSLMT" in output


def test_extract_save_cache_writes_the_gitignored_folder(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["extract", "--save-cache"]) == 0
    capsys.readouterr()
    cached = sorted(path.name for path in Path(settings.paths.dev_cache).glob("*.csv"))
    assert cached == ["CKOVLMP.csv", "CKSBLMP.csv", "TTCPIPP.csv"]


def test_check_writes_report_html(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    report = tmp_path / "report.html"
    exit_code = cli.main([
        "check", "--user", "edmund", "--cpty", "ABCDEFG", "--product", "FX",
        "--tenor", "1 months", "--pair", "USDHKD", "--notional", "500000",
        "--report", str(report),
    ])
    assert exit_code == 0
    output = capsys.readouterr().out
    assert "DECISION        : Y" in output
    assert "509,000" in output
    assert report.is_file()
    html = report.read_text(encoding="utf-8")
    assert "Spot-1M" in html and "ABCDGRP" in html and "reference only" in html


def test_check_no_hold_leaves_no_claim(capsys: pytest.CaptureFixture[str]) -> None:
    args = [
        "check", "--user", "edmund", "--cpty", "ABCDEFG", "--tenor", "1M",
        "--pair", "USDHKD", "--notional", "500000", "--no-hold",
    ]
    assert cli.main(args) == 0
    assert "hold id" not in capsys.readouterr().out


def test_check_rejects_a_bad_counterparty_length(
    capsys: pytest.CaptureFixture[str]
) -> None:
    args = [
        "check", "--user", "edmund", "--cpty", "ABCDE", "--tenor", "1M",
        "--pair", "USDHKD", "--notional", "500000",
    ]
    assert cli.main(args) == 1
    assert "exactly 4" in capsys.readouterr().out


def test_peers_and_history_and_release(capsys: pytest.CaptureFixture[str]) -> None:
    cli.main([
        "check", "--user", "edmund", "--cpty", "ABCDEFG", "--tenor", "1M",
        "--pair", "USDHKD", "--notional", "500000", "--report", "/dev/null",
    ])
    capsys.readouterr()

    assert cli.main(["peers", "--cpty", "ABCDEFG"]) == 0
    peers_output = capsys.readouterr().out
    assert "edmund" in peers_output and "min left" in peers_output

    assert cli.main(["history"]) == 0
    assert "Y" in capsys.readouterr().out

    assert cli.main(["release", "--hold-id", "1", "--user", "olivia"]) == 1
    assert "only the creating username" in capsys.readouterr().out

    assert cli.main(["release", "--hold-id", "1", "--user", "edmund"]) == 0
    assert "released" in capsys.readouterr().out


def test_unknown_command_exits_non_zero() -> None:
    with pytest.raises(SystemExit) as error:
        cli.main(["nonsense"])
    assert error.value.code != 0
