"""§22: active holds reduce availability; expired holds do not."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from cdl import constants
from cdl.config import Settings
from cdl.logic.availability import LimitDataError, build_surface, fits, limit_row_for
from cdl.models import Hold
from cdl.store.db import HoldsStore
from cdl.treats import mock

NOW = datetime(2026, 1, 5, 10, 0, 0)


def make_hold(usage: float, *, bucket: str = "Spot-1M", minutes: int = 60,
              username: str = "edmund", status: str = constants.HOLD_ACTIVE) -> Hold:
    return Hold(
        id=1,
        check_id=1,
        created_at=NOW,
        expires_at=NOW + timedelta(minutes=minutes),
        released_at=None,
        status=status,
        username=username,
        counterparty="ABCDEFG",
        product="FX",
        tenor="1 months",
        affected_bucket=bucket,
        pair_or_currency="USDHKD",
        notional_usd=usage / 1.018,
        usage=usage,
    )


@pytest.fixture
def limit_rows(settings: Settings) -> list[dict[str, str]]:
    return mock.fetch(constants.TABLE_LIMITS, settings)


def test_surface_reads_the_reference_row(limit_rows: list[dict[str, str]]) -> None:
    surface = build_surface("ABCDEFG", "FX", limit_rows)
    assert surface.limit_type == "FX01"
    assert surface.deal_limit == pytest.approx(20_000_000)
    assert surface.utilisation == pytest.approx(3_500_000)
    assert surface.available == pytest.approx(16_500_000)
    assert [bucket.bucket for bucket in surface.buckets] == list(constants.BUCKETS)
    assert surface.bucket("Spot-1M").available == pytest.approx(6_800_000)


def test_active_holds_reduce_availability(limit_rows: list[dict[str, str]]) -> None:
    holds = [make_hold(1_000_000), make_hold(500_000, bucket="1M-3M")]
    surface = build_surface("ABCDEFG", "FX", limit_rows, holds)
    assert surface.holds_usage == pytest.approx(1_500_000)
    assert surface.available == pytest.approx(15_000_000)
    assert surface.bucket("Spot-1M").available == pytest.approx(5_800_000)
    assert surface.bucket("1M-3M").available == pytest.approx(4_700_000)
    assert surface.bucket("3M-6M").available == pytest.approx(4_400_000)


def test_expired_holds_do_not_reduce_availability(
    store: HoldsStore, limit_rows: list[dict[str, str]]
) -> None:
    with store.connect() as connection:
        connection.execute(
            "INSERT INTO temporary_holds (check_id, created_at, expires_at, released_at, "
            "status, username, counterparty, product, tenor, affected_bucket, "
            "pair_or_currency, notional_usd, usage) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                None,
                "2026-01-05 08:00:00",
                "2026-01-05 09:00:00",
                None,
                constants.HOLD_ACTIVE,
                "olivia",
                "ABCDEFG",
                "FX",
                "1 months",
                "Spot-1M",
                "USDHKD",
                1_000_000.0,
                1_018_000.0,
            ),
        )
    active = store.active_holds("ABCDEFG", "FX", NOW)
    assert active == []
    surface = build_surface("ABCDEFG", "FX", limit_rows, active)
    assert surface.holds_usage == 0.0
    assert surface.available == pytest.approx(16_500_000)


def test_fits_requires_both_the_deal_limit_and_the_bucket(
    limit_rows: list[dict[str, str]]
) -> None:
    surface = build_surface("ABCDEFG", "FX", limit_rows)
    allowed, message = fits(surface, "Spot-1M", 509_000)
    assert allowed and "fits" in message

    allowed, message = fits(surface, "Spot-1M", 7_000_000)
    assert not allowed
    assert "tenor bucket Spot-1M" in message
    assert "deal limit" not in message

    allowed, message = fits(surface, "Spot-1M", 30_000_000)
    assert not allowed
    assert "deal limit" in message and "tenor bucket Spot-1M" in message


def test_exhausted_counterparty_is_rejected(limit_rows: list[dict[str, str]]) -> None:
    surface = build_surface("EFGHIJK", "FX", limit_rows)
    allowed, message = fits(surface, "Spot-1M", 509_000)
    assert not allowed
    assert "Hard reject" in message


def test_missing_limit_row_names_the_table_and_the_limit_type(
    limit_rows: list[dict[str, str]]
) -> None:
    with pytest.raises(LimitDataError) as error:
        build_surface("WXYZ", "FX", limit_rows)
    assert constants.TABLE_LIMITS in str(error.value)
    assert "FX01" in str(error.value)


def test_limit_row_lookup_matches_counterparty_and_limit_type(
    limit_rows: list[dict[str, str]]
) -> None:
    row = limit_row_for(limit_rows, "abcdefg", "Gold")
    assert row is not None
    assert row[constants.COL_LIMIT_TYPE] == "GD01"
