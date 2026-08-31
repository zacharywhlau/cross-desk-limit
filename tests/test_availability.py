"""§22: active holds reduce availability; expired holds do not.

Plus the rule the limit system actually applies: availability is a ladder, so a deal
consumes headroom in its own period and in every shorter one.
"""

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


def make_hold(usage: float, *, bucket: str = "SPT-1M", minutes: int = 60,
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


def limit_row(total: float, limits: dict[str, float], risk: dict[str, float]) -> dict[str, str]:
    """One synthetic CKSBLMP row, written the way an export would write it."""
    row = {
        constants.COL_LIMIT_COUNTERPARTY: "ABCDEFG",
        constants.COL_LIMIT_TYPE: "FX 01",
        constants.COL_LIMIT_AMOUNT: f"{total:.0f}",
    }
    for name in constants.BUCKETS:
        slot = constants.BUCKET_INDEX[name]
        row[constants.occupied_column(slot)] = f"{risk.get(name, 0.0):.0f}"
        row[constants.slot_limit_column(slot)] = f"{limits.get(name, total):.0f}"
    return row


@pytest.fixture
def limit_rows(settings: Settings) -> list[dict[str, str]]:
    return mock.fetch(constants.TABLE_LIMITS, settings)


def test_surface_reads_the_reference_row(limit_rows: list[dict[str, str]]) -> None:
    surface = build_surface("ABCDEFG", "FX", limit_rows)
    assert surface.limit_type == "FX 01"
    assert surface.deal_limit == pytest.approx(20_000_000)
    assert surface.utilisation == pytest.approx(3_500_000)
    assert surface.available == pytest.approx(16_500_000)
    assert [bucket.bucket for bucket in surface.buckets] == list(constants.BUCKETS)
    assert [bucket.slot for bucket in surface.buckets] == list(range(1, 15))
    assert surface.bucket("SPT-1M").available == pytest.approx(16_500_000)
    # Beyond five years the mock counterparty has no FX limit at all.
    assert surface.bucket("15Y+").available == 0.0


def test_availability_is_the_reverse_cumulative_ladder() -> None:
    """A deal in one period consumes every shorter period, so available is a running
    minimum of (period limit - cash risk from that period onwards)."""
    row = limit_row(
        total=60_000_000,
        limits={
            "CALL": 60_000_000, "TDY": 60_000_000, "TOM": 60_000_000,
            "SPT": 60_000_000, "SPT-1M": 60_000_000,
            "1M-3M": 30_000_000, "3M-6M": 30_000_000, "6M-1Y": 30_000_000,
            "1Y-3Y": 12_000_000, "3Y-5Y": 12_000_000,
            "5Y-7Y": 0.0, "7Y-10Y": 0.0, "10Y-15Y": 0.0, "15Y+": 0.0,
        },
        risk={"SPT": 100_000, "1M-3M": 2_800_000, "3M-6M": 200_000, "1Y-3Y": 600_000},
    )
    surface = build_surface("ABCDEFG", "FX", [row])
    available = {bucket.bucket: bucket.available for bucket in surface.buckets}
    reverse = {bucket.bucket: bucket.reverse_cumulative for bucket in surface.buckets}

    total_risk = 3_700_000
    assert reverse["CALL"] == pytest.approx(total_risk)
    assert reverse["SPT-1M"] == pytest.approx(total_risk - 100_000)
    assert reverse["1Y-3Y"] == pytest.approx(600_000)

    # The short end carries the whole book of risk.
    assert available["CALL"] == pytest.approx(60_000_000 - total_risk)
    # SPT-1M has 56.4mm of its own headroom, but CALL carries the SPT risk as well and
    # is therefore tighter, so the running minimum wins.
    assert surface.bucket("SPT-1M").own_headroom == pytest.approx(60_000_000 - 3_600_000)
    assert available["SPT-1M"] == pytest.approx(60_000_000 - total_risk)
    # 1M-3M has a smaller limit, and from here on it caps everything longer.
    assert available["1M-3M"] == pytest.approx(30_000_000 - 3_600_000)
    assert available["3M-6M"] == pytest.approx(26_400_000)
    assert available["6M-1Y"] == pytest.approx(26_400_000)
    # A tighter limit further out binds again.
    assert available["1Y-3Y"] == pytest.approx(12_000_000 - 600_000)
    assert available["3Y-5Y"] == pytest.approx(11_400_000)
    # No limit at all beyond seven years, and zero is the floor.
    assert available["5Y-7Y"] == 0.0
    assert available["15Y+"] == 0.0


def test_a_used_up_short_period_blocks_every_longer_one() -> None:
    """The F7 rule: 'where a limit is fully utilised in a given period all longer
    dated limits will become unavailable'."""
    row = limit_row(
        total=10_000_000,
        limits={name: 10_000_000.0 for name in constants.BUCKETS},
        risk={"SPT": 10_000_000},
    )
    surface = build_surface("ABCDEFG", "FX", [row])
    assert all(bucket.available == 0.0 for bucket in surface.buckets)
    # The long end still has its own headroom, but the ladder overrides it.
    assert surface.bucket("15Y+").own_headroom == pytest.approx(10_000_000)


def test_period_limit_falls_back_to_the_total_when_the_column_is_missing() -> None:
    row = {
        constants.COL_LIMIT_COUNTERPARTY: "ABCDEFG",
        constants.COL_LIMIT_TYPE: "FX 01",
        constants.COL_LIMIT_AMOUNT: "5000000",
        constants.occupied_column(5): "1000000",
    }
    surface = build_surface("ABCDEFG", "FX", [row])
    assert all(bucket.limit == pytest.approx(5_000_000) for bucket in surface.buckets)
    assert surface.bucket("SPT-1M").available == pytest.approx(4_000_000)
    # The long end has no risk of its own, but the used-up short end caps it.
    assert surface.bucket("15Y+").own_headroom == pytest.approx(5_000_000)
    assert surface.bucket("15Y+").available == pytest.approx(4_000_000)


def test_active_holds_reduce_availability(limit_rows: list[dict[str, str]]) -> None:
    holds = [make_hold(1_000_000), make_hold(500_000, bucket="1M-3M")]
    surface = build_surface("ABCDEFG", "FX", limit_rows, holds)
    assert surface.holds_usage == pytest.approx(1_500_000)
    assert surface.available == pytest.approx(15_000_000)
    # A hold in SPT-1M is invisible to the shorter periods' own headroom but not to
    # the ladder, which sees the whole book from each period onwards.
    assert surface.bucket("SPT-1M").available == pytest.approx(16_500_000 - 1_500_000)
    assert surface.bucket("1M-3M").available == pytest.approx(9_700_000 - 500_000)
    assert surface.bucket("3M-6M").available == pytest.approx(9_200_000)


def test_a_hold_far_out_reduces_the_short_end_as_well() -> None:
    """The F7 rule again, from the other side: 'the 1-3y exposure reduces availability
    in all periods from 1-3y to TDY'."""
    row = limit_row(
        total=10_000_000,
        limits={name: 10_000_000.0 for name in constants.BUCKETS},
        risk={},
    )
    surface = build_surface("ABCDEFG", "FX", [row], [make_hold(4_000_000, bucket="10Y-15Y")])
    assert surface.bucket("SPT").available == pytest.approx(6_000_000)
    assert surface.bucket("10Y-15Y").available == pytest.approx(6_000_000)
    # 15Y+ carries no risk of its own, but it cannot exceed what the shorter periods
    # still allow, so it is capped at the same figure.
    assert surface.bucket("15Y+").own_headroom == pytest.approx(10_000_000)
    assert surface.bucket("15Y+").available == pytest.approx(6_000_000)


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
                "SPT-1M",
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


def test_fits_requires_both_the_total_limit_and_the_period(
    limit_rows: list[dict[str, str]]
) -> None:
    surface = build_surface("ABCDEFG", "FX", limit_rows)
    allowed, message = fits(surface, "SPT-1M", 509_000)
    assert allowed and "fits" in message

    allowed, message = fits(surface, "1Y-3Y", 6_000_000)
    assert not allowed
    assert "period 1Y-3Y" in message
    assert "total limit" not in message

    allowed, message = fits(surface, "SPT-1M", 30_000_000)
    assert not allowed
    assert "the total limit" in message and "period SPT-1M" in message


def test_a_period_with_no_limit_rejects_everything(limit_rows: list[dict[str, str]]) -> None:
    surface = build_surface("ABCDEFG", "FX", limit_rows)
    allowed, message = fits(surface, "15Y+", 1.0)
    assert not allowed
    assert "period 15Y+" in message


def test_exhausted_counterparty_is_rejected(limit_rows: list[dict[str, str]]) -> None:
    surface = build_surface("EFGHIJK", "FX", limit_rows)
    allowed, message = fits(surface, "SPT-1M", 509_000)
    assert not allowed
    assert "Hard reject" in message


def test_missing_limit_row_names_the_table_and_the_limit_type(
    limit_rows: list[dict[str, str]]
) -> None:
    with pytest.raises(LimitDataError) as error:
        build_surface("WXYZ", "FX", limit_rows)
    assert constants.TABLE_LIMITS in str(error.value)
    assert "FX 01" in str(error.value)


def test_limit_row_lookup_matches_counterparty_and_limit_type(
    limit_rows: list[dict[str, str]]
) -> None:
    row = limit_row_for(limit_rows, "abcdefg", "Gold")
    assert row is not None
    assert row[constants.COL_LIMIT_TYPE] == "GD 01"


def test_limit_row_lookup_tolerates_a_code_without_the_space() -> None:
    rows = [{
        constants.COL_LIMIT_COUNTERPARTY: "ABCD ",
        constants.COL_LIMIT_TYPE: "FX01",
        constants.COL_LIMIT_AMOUNT: "1000",
    }]
    assert limit_row_for(rows, "ABCD", "FX") is rows[0]
