"""Config reading, including the quoting trap that cost the operator debugging time."""

from __future__ import annotations

from pathlib import Path

import pytest

from cdl.config import ConfigError, load_settings, looks_quoted, unquote

CONFIG_WITH_QUOTES = """
[treats]
url = "http://endpoint.example/query"
library = 'MYLIB'
ttcpipp = "api"
cksblmp = mock
ckovlmp = mock
max_rows = "20000"
probe_counterparty = "ABCDEFG"

[ffr]
source = mock
table = "CKBLOTP"
weight_column = "2025Q2"

[store]
db_path = "./data/holds.db"
hold_ttl_minutes = "45"
"""


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('"http://x"', "http://x"),
        ("'http://x'", "http://x"),
        ('  "http://x"  ', "http://x"),
        ("http://x", "http://x"),
        ('"quoted twice"', "quoted twice"),
        ('""', ""),
        ("", ""),
        ('a "quoted" word', 'a "quoted" word'),
    ],
)
def test_unquote(raw: str, expected: str) -> None:
    assert unquote(raw) == expected


def test_quoted_values_are_read_as_if_they_were_unquoted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An ini file is not Python: quotes would otherwise become part of the value."""
    config = tmp_path / "config.ini"
    config.write_text(CONFIG_WITH_QUOTES, encoding="utf-8")
    monkeypatch.setenv("CDL_CONFIG", str(config))

    settings = load_settings()
    assert settings.treats.url == "http://endpoint.example/query"
    assert settings.treats.library == "MYLIB"
    assert settings.treats.ttcpipp == "api"
    assert settings.treats.max_rows == 20000
    assert settings.treats.probe_counterparty == "ABCDEFG"
    assert settings.ffr.table == "CKBLOTP"
    assert settings.ffr.weight_column == "2025Q2"
    assert settings.store.hold_ttl_minutes == 45
    assert settings.store.db_path.name == "holds.db"


def test_quoted_values_are_reported_so_the_file_gets_cleaned_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config.ini"
    config.write_text(CONFIG_WITH_QUOTES, encoding="utf-8")
    monkeypatch.setenv("CDL_CONFIG", str(config))

    settings = load_settings()
    assert "[treats] url" in settings.quoted_values
    assert "[treats] library" in settings.quoted_values
    assert "[treats] cksblmp" not in settings.quoted_values


def test_no_quotes_means_nothing_to_report(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = load_settings()  # config.example.ini, pinned by the autouse fixture
    assert settings.quoted_values == ()


def test_looks_quoted() -> None:
    assert looks_quoted('"x"') is True
    assert looks_quoted("'x'") is True
    assert looks_quoted("x") is False


def test_environment_override_wins_and_is_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CDL_FFR_WEIGHT_COLUMN", "2025Q3")
    settings = load_settings()
    assert settings.ffr.weight_column == "2025Q3"
    assert "CDL_FFR_WEIGHT_COLUMN" in settings.overrides


def test_environment_override_is_also_unquoted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CDL_TREATS_LIBRARY", '"MYLIB"')
    assert load_settings().treats.library == "MYLIB"


def test_max_rows_must_be_a_positive_whole_number(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CDL_TREATS_MAX_ROWS", "not a number")
    with pytest.raises(ConfigError) as error:
        load_settings()
    assert "max_rows" in str(error.value)


def test_config_example_matches_the_defaults_the_code_expects() -> None:
    settings = load_settings()
    assert settings.config_path is not None
    assert settings.config_path.name == "config.example.ini"
    assert settings.treats.max_rows == 20000
    assert settings.ffr.table == "CKBLOTP"
