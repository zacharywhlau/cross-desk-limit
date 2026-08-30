"""§22: TTL expiry, own-release refusal, two-user stacking, exhaustion, concurrency."""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from cdl import constants
from cdl.config import Settings, StoreSettings
from cdl.models import CheckRequest, DecisionOutcome, Hold
from cdl.store.db import HoldOwnershipError, HoldsStore, StoreError, is_network_path

NOW = datetime(2026, 1, 5, 10, 0, 0)


def request_for(username: str, notional: float = 500_000.0) -> CheckRequest:
    return CheckRequest(
        username=username,
        counterparty="ABCDEFG",
        product="FX",
        tenor="1 months",
        pair_or_currency="USDHKD",
        direction="buy",
        notional_usd=notional,
    )


def capacity_compute(capacity: float):
    """A stand-in for the availability computation: decide against the live holds."""

    def compute(active) -> DecisionOutcome:
        used = sum(hold.usage for hold in active)
        usage = 400_000.0
        allowed = usage <= capacity - used
        return DecisionOutcome(
            decision=constants.DECISION_YES if allowed else constants.DECISION_NO,
            message="fits" if allowed else "Insufficient limit",
            usage=usage,
            affected_bucket="Spot-1M",
            ffr_table="FFR_FX_LOW",
            ffr_weight=0.018,
            parent_counterparty="ABCDGRP",
            active_holds=tuple(active),
        )

    return compute


def test_schema_and_journal_mode_on_a_local_disk(store: HoldsStore) -> None:
    assert store.journal_mode == "WAL"
    with store.connect() as connection:
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert {"limit_checks", "temporary_holds"} <= tables


@pytest.mark.parametrize(
    ("path", "flag", "expected"),
    [
        (r"\\server\share\holds.db", "auto", True),
        ("/home/desk/holds.db", "auto", False),
        ("C:/desk/holds.db", "auto", False),
        ("/home/desk/holds.db", "true", True),
        (r"\\server\share\holds.db", "false", False),
    ],
)
def test_network_path_detection(path: str, flag: str, expected: bool) -> None:
    assert is_network_path(Path(path), flag) is expected


def test_network_path_uses_the_rollback_journal(settings: Settings) -> None:
    store_settings = StoreSettings(
        db_path=Path(r"\\server\share\cross_desk_limit.db"),
        hold_ttl_minutes=60,
        busy_timeout_ms=15000,
        network_path="auto",
    )
    store = HoldsStore(store_settings)
    assert store.on_network_path is True
    assert store.journal_mode == "DELETE"


def test_yes_writes_history_and_a_hold(store: HoldsStore) -> None:
    committed = store.commit_decision(request_for("edmund"), capacity_compute(1_000_000))
    assert committed.outcome.decision == constants.DECISION_YES
    assert committed.check_id is not None and committed.hold_id is not None
    assert len(store.active_holds("ABCDEFG", "FX")) == 1
    assert len(store.history_today()) == 1


def test_no_writes_history_but_no_hold(store: HoldsStore) -> None:
    committed = store.commit_decision(request_for("edmund"), capacity_compute(100_000))
    assert committed.outcome.decision == constants.DECISION_NO
    assert committed.hold_id is None
    assert store.active_holds("ABCDEFG", "FX") == []
    assert len(store.history_today()) == 1


def test_no_hold_flag_skips_the_hold_on_yes(store: HoldsStore) -> None:
    committed = store.commit_decision(
        request_for("edmund"), capacity_compute(1_000_000), create_hold=False)
    assert committed.outcome.decision == constants.DECISION_YES
    assert committed.hold_id is None
    assert store.active_holds("ABCDEFG", "FX") == []


def test_two_users_stack_holds_until_capacity_is_exhausted(store: HoldsStore) -> None:
    compute = capacity_compute(1_000_000)
    first = store.commit_decision(request_for("edmund"), compute)
    second = store.commit_decision(request_for("olivia"), compute)
    third = store.commit_decision(request_for("peter"), compute)

    assert [first.outcome.decision, second.outcome.decision, third.outcome.decision] == [
        constants.DECISION_YES, constants.DECISION_YES, constants.DECISION_NO
    ]
    holds = store.active_holds("ABCDEFG", "FX")
    assert [hold.username for hold in holds] == ["edmund", "olivia"]
    assert sum(hold.usage for hold in holds) == pytest.approx(800_000)


def test_ttl_expiry_frees_capacity(settings: Settings) -> None:
    clock = {"now": NOW}
    store = HoldsStore(settings, clock=lambda: clock["now"])
    store.commit_decision(request_for("edmund"), capacity_compute(1_000_000))
    assert len(store.active_holds("ABCDEFG", "FX")) == 1

    clock["now"] = NOW + timedelta(minutes=settings.store.hold_ttl_minutes + 1)
    assert store.expire_stale() == 1
    assert store.active_holds("ABCDEFG", "FX") == []
    with store.connect() as connection:
        status = connection.execute(
            "SELECT status FROM temporary_holds WHERE id = 1").fetchone()[0]
    assert status == constants.HOLD_EXPIRED


def test_expired_hold_lets_the_next_trader_through(settings: Settings) -> None:
    clock = {"now": NOW}
    store = HoldsStore(settings, clock=lambda: clock["now"])
    compute = capacity_compute(500_000)
    assert store.commit_decision(request_for("edmund"), compute).outcome.decision == "Y"
    assert store.commit_decision(request_for("olivia"), compute).outcome.decision == "N"

    clock["now"] = NOW + timedelta(minutes=61)
    assert store.commit_decision(request_for("olivia"), compute).outcome.decision == "Y"


def test_only_the_creating_username_may_release(store: HoldsStore) -> None:
    committed = store.commit_decision(request_for("edmund"), capacity_compute(1_000_000))
    assert committed.hold_id is not None
    with pytest.raises(HoldOwnershipError) as error:
        store.release(committed.hold_id, "olivia")
    assert "edmund" in str(error.value)
    assert len(store.active_holds("ABCDEFG", "FX")) == 1


def test_release_frees_capacity_immediately(store: HoldsStore) -> None:
    compute = capacity_compute(500_000)
    committed = store.commit_decision(request_for("edmund"), compute)
    assert store.commit_decision(request_for("olivia"), compute).outcome.decision == "N"
    assert committed.hold_id is not None
    released = store.release(committed.hold_id, "edmund")
    assert released.status == constants.HOLD_RELEASED
    assert released.released_at is not None
    assert store.commit_decision(request_for("olivia"), compute).outcome.decision == "Y"


def test_releasing_twice_is_refused(store: HoldsStore) -> None:
    committed = store.commit_decision(request_for("edmund"), capacity_compute(1_000_000))
    assert committed.hold_id is not None
    store.release(committed.hold_id, "edmund")
    with pytest.raises(StoreError):
        store.release(committed.hold_id, "edmund")


def test_releasing_an_unknown_hold_is_refused(store: HoldsStore) -> None:
    with pytest.raises(StoreError):
        store.release(999, "edmund")


def test_peers_report_minutes_remaining(settings: Settings) -> None:
    clock = {"now": NOW}
    store = HoldsStore(settings, clock=lambda: clock["now"])
    store.commit_decision(request_for("edmund"), capacity_compute(1_000_000))
    clock["now"] = NOW + timedelta(minutes=20)
    peers = store.peers("ABCDEFG", "FX")
    assert len(peers) == 1
    hold, minutes = peers[0]
    assert hold.username == "edmund"
    assert minutes == pytest.approx(40.0)


def test_history_today_ignores_yesterday(store: HoldsStore) -> None:
    store.commit_decision(request_for("edmund"), capacity_compute(1_000_000))
    with store.connect() as connection:
        connection.execute(
            "UPDATE limit_checks SET created_at = ? WHERE id = 1",
            ((datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),),
        )
    assert store.history_today() == []


def test_error_outcomes_are_recorded(store: HoldsStore) -> None:
    check_id = store.record_error(request_for("edmund"), "CKSBLMP failed in api mode")
    assert check_id > 0
    record = store.history_today()[0]
    assert record.decision == constants.DECISION_ERROR
    assert "CKSBLMP" in record.message


def test_unreachable_db_path_gives_a_plain_message(settings: Settings, tmp_path: Path) -> None:
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("", encoding="utf-8")
    store_settings = StoreSettings(
        db_path=blocker / "holds.db",
        hold_ttl_minutes=60,
        busy_timeout_ms=1000,
        network_path="false",
    )
    with pytest.raises(StoreError) as error:
        HoldsStore(store_settings).initialise()
    assert str(blocker) in str(error.value)


def test_concurrent_traders_cannot_spend_the_same_capacity(settings: Settings) -> None:
    """Five threads, one temp database, capacity for exactly two holds of 400k."""
    store = HoldsStore(settings)
    store.initialise()
    compute = capacity_compute(1_000_000)
    decisions: list[str] = []
    errors: list[BaseException] = []
    lock = threading.Lock()
    start = threading.Barrier(5)

    def worker(index: int) -> None:
        try:
            start.wait(timeout=10)
            committed = store.commit_decision(request_for(f"trader{index}"), compute)
            with lock:
                decisions.append(committed.outcome.decision)
        except BaseException as error:  # noqa: BLE001 - reported below
            with lock:
                errors.append(error)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == []
    assert decisions.count(constants.DECISION_YES) == 2
    assert decisions.count(constants.DECISION_NO) == 3
    holds = store.active_holds("ABCDEFG", "FX")
    assert len(holds) == 2
    assert sum(hold.usage for hold in holds) == pytest.approx(800_000)


def test_busy_timeout_is_applied(store: HoldsStore) -> None:
    with store.connect() as connection:
        timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
    assert int(timeout) == store.busy_timeout_ms


def test_hold_minutes_remaining_never_negative() -> None:
    hold = Hold(
        id=1, check_id=None, created_at=NOW, expires_at=NOW, released_at=None,
        status=constants.HOLD_ACTIVE, username="edmund", counterparty="ABCDEFG",
        product="FX", tenor="1 months", affected_bucket="Spot-1M",
        pair_or_currency="USDHKD", notional_usd=1.0, usage=1.0,
    )
    assert hold.minutes_remaining(NOW + timedelta(hours=5)) == 0.0


def test_sqlite3_is_used_directly() -> None:
    """No ORM: the store talks to sqlite3 and nothing else."""
    from cdl.store import db

    assert db.sqlite3 is sqlite3
