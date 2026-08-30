"""Shared fixtures. Every test runs against the mock CSVs and a temporary database."""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

import pytest

from cdl.config import Settings, load_settings, project_root
from cdl.models import CheckRequest
from cdl.store.db import HoldsStore

#: The reference case from §22 that must produce a sensible Y on mock data.
REFERENCE_REQUEST = CheckRequest(
    username="edmund",
    counterparty="ABCDEFG",
    product="FX",
    tenor="1 months",
    pair_or_currency="USDHKD",
    direction="buy",
    notional_usd=500_000.0,
)

#: Counterparty whose mock limits are nearly exhausted, so N is easy to demonstrate.
EXHAUSTED_COUNTERPARTY = "EFGHIJK"


@pytest.fixture(autouse=True)
def _no_local_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin every test to config.example.ini so a local config.ini cannot leak in."""
    monkeypatch.setenv("CDL_CONFIG", str(project_root() / "config.example.ini"))


@pytest.fixture(autouse=True)
def _propagate_logs() -> None:
    """caplog needs the `cdl` logger to propagate; setup_logging turns that off."""
    logger = logging.getLogger("cdl")
    previous = logger.propagate
    logger.propagate = True
    yield
    logger.propagate = previous


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Mock sources everywhere, with the database and cache inside tmp_path."""
    monkeypatch.setenv("CDL_STORE_DB_PATH", str(tmp_path / "cross_desk_limit.db"))
    monkeypatch.setenv("CDL_PATHS_DEV_CACHE", str(tmp_path / "dev_cache"))
    return load_settings()


@pytest.fixture
def store(settings: Settings) -> HoldsStore:
    holds = HoldsStore(settings)
    holds.initialise()
    return holds


@pytest.fixture
def mock_dir() -> Path:
    return project_root() / "data" / "mock_treats"


def settings_with_mock_dir(settings: Settings, directory: Path) -> Settings:
    """A copy of `settings` reading its mock tables from `directory`."""
    return replace(settings, paths=replace(settings.paths, mock_treats=directory))
