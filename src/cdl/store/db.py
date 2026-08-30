"""Shared holds and history in one SQLite file. stdlib sqlite3, used directly.

Correctness notes that matter on a shared network folder:
  - WAL needs shared memory and is unsafe over SMB, so a network path uses the
    rollback journal (journal_mode=DELETE). WAL is used only on a local disk.
  - busy_timeout is always set, and transactions stay short.
  - One decision is ONE transaction opened with BEGIN IMMEDIATE: expire stale holds,
    re-read the active holds, compute availability, insert history, insert the hold on
    Y, commit. That is what stops two traders spending the same last capacity.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Iterator, Sequence

from .. import constants
from ..config import Settings, StoreSettings
from ..logging_setup import get_logger
from ..models import (
    CheckRecord,
    CheckRequest,
    CommittedDecision,
    DecisionOutcome,
    Hold,
)

_logger = get_logger("store")

TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS limit_checks (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at          TEXT    NOT NULL,
        username            TEXT    NOT NULL,
        counterparty        TEXT    NOT NULL,
        parent_counterparty TEXT,
        product             TEXT    NOT NULL,
        tenor               TEXT    NOT NULL,
        affected_bucket     TEXT    NOT NULL,
        pair_or_currency    TEXT    NOT NULL,
        direction           TEXT    NOT NULL,
        notional_usd        REAL    NOT NULL,
        usage               REAL    NOT NULL,
        ffr_table           TEXT    NOT NULL,
        ffr_weight          REAL    NOT NULL,
        decision            TEXT    NOT NULL,
        message             TEXT    NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS temporary_holds (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        check_id         INTEGER,
        created_at       TEXT    NOT NULL,
        expires_at       TEXT    NOT NULL,
        released_at      TEXT,
        status           TEXT    NOT NULL,
        username         TEXT    NOT NULL,
        counterparty     TEXT    NOT NULL,
        product          TEXT    NOT NULL,
        tenor            TEXT    NOT NULL,
        affected_bucket  TEXT    NOT NULL,
        pair_or_currency TEXT    NOT NULL,
        notional_usd     REAL    NOT NULL,
        usage            REAL    NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_holds_active
        ON temporary_holds (counterparty, product, status)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_checks_created
        ON limit_checks (created_at)
    """,
)


class StoreError(RuntimeError):
    """The holds/history database could not be used."""


class HoldOwnershipError(StoreError):
    """Only the creating username may release a hold."""


def format_time(value: datetime) -> str:
    return value.strftime(TIME_FORMAT)


def parse_time(value: str | None) -> datetime | None:
    if value is None or str(value).strip() == "":
        return None
    return datetime.strptime(str(value).strip()[:19], TIME_FORMAT)


def _rollback(connection: sqlite3.Connection) -> None:
    """Undo an open transaction; never mask the original failure."""
    try:
        connection.execute("ROLLBACK")
    except sqlite3.Error as error:  # pragma: no cover - only on a broken connection
        _logger.warning("rollback failed: %s", error)


def is_network_path(db_path: Path, flag: str = "auto") -> bool:
    """UNC path (``\\\\server\\share``) or an explicit configured flag."""
    if flag == "true":
        return True
    if flag == "false":
        return False
    text = str(db_path)
    return text.startswith("\\\\") or text.startswith("//")


class HoldsStore:
    """Holds and history. One instance per process is enough; it holds no connection."""

    def __init__(
        self,
        settings: StoreSettings | Settings,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        store = settings.store if isinstance(settings, Settings) else settings
        self._settings = store
        self._clock = clock or datetime.now
        self.db_path = Path(store.db_path)
        self.hold_ttl_minutes = store.hold_ttl_minutes
        self.busy_timeout_ms = store.busy_timeout_ms
        self.on_network_path = is_network_path(self.db_path, store.network_path)
        self._initialised = False

    # -- connection -------------------------------------------------------
    @property
    def journal_mode(self) -> str:
        """DELETE on a network share (WAL is unsafe over SMB), WAL on a local disk."""
        return "DELETE" if self.on_network_path else "WAL"

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Open a short-lived autocommit connection with the tuning applied."""
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise StoreError(
                f"cannot reach the folder for db_path {self.db_path}: {error}"
            ) from error
        try:
            connection = sqlite3.connect(
                str(self.db_path),
                timeout=self.busy_timeout_ms / 1000.0,
                isolation_level=None,
            )
        except sqlite3.Error as error:
            raise StoreError(
                f"cannot open the holds database at {self.db_path}: {error}"
            ) from error
        connection.row_factory = sqlite3.Row
        try:
            connection.execute(f"PRAGMA busy_timeout = {int(self.busy_timeout_ms)}")
            connection.execute(f"PRAGMA journal_mode = {self.journal_mode}")
            connection.execute("PRAGMA synchronous = FULL")
            yield connection
        except sqlite3.OperationalError as error:
            raise StoreError(
                f"the holds database at {self.db_path} is busy or unavailable "
                f"({error}); wait a moment and try again"
            ) from error
        except sqlite3.Error as error:
            raise StoreError(f"holds database error at {self.db_path}: {error}") from error
        finally:
            connection.close()

    def initialise(self) -> None:
        """Create the two tables on first use."""
        if self._initialised:
            return
        with self.connect() as connection:
            for statement in SCHEMA:
                connection.execute(statement)
        _logger.info(
            "store ready db_path=%s journal_mode=%s ttl_minutes=%d",
            self.db_path, self.journal_mode, self.hold_ttl_minutes,
        )
        self._initialised = True

    def now(self) -> datetime:
        return self._clock().replace(microsecond=0)

    # -- holds ------------------------------------------------------------
    def expire_stale(self, now: datetime | None = None) -> int:
        """Mark every active hold whose expiry has passed as expired."""
        self.initialise()
        moment = now or self.now()
        with self.connect() as connection:
            return self._expire_stale(connection, moment)

    @staticmethod
    def _expire_stale(connection: sqlite3.Connection, now: datetime) -> int:
        cursor = connection.execute(
            "UPDATE temporary_holds SET status = ? "
            "WHERE status = ? AND expires_at <= ?",
            (constants.HOLD_EXPIRED, constants.HOLD_ACTIVE, format_time(now)),
        )
        if cursor.rowcount:
            _logger.info("expired %d stale hold(s)", cursor.rowcount)
        return int(cursor.rowcount or 0)

    @staticmethod
    def _hold_from_row(row: sqlite3.Row) -> Hold:
        created = parse_time(row["created_at"])
        expires = parse_time(row["expires_at"])
        assert created is not None and expires is not None
        return Hold(
            id=int(row["id"]),
            check_id=int(row["check_id"]) if row["check_id"] is not None else None,
            created_at=created,
            expires_at=expires,
            released_at=parse_time(row["released_at"]),
            status=str(row["status"]),
            username=str(row["username"]),
            counterparty=str(row["counterparty"]),
            product=str(row["product"]),
            tenor=str(row["tenor"]),
            affected_bucket=str(row["affected_bucket"]),
            pair_or_currency=str(row["pair_or_currency"]),
            notional_usd=float(row["notional_usd"]),
            usage=float(row["usage"]),
        )

    @classmethod
    def _active_holds(
        cls,
        connection: sqlite3.Connection,
        counterparty: str,
        product: str,
        now: datetime,
    ) -> list[Hold]:
        rows = connection.execute(
            "SELECT * FROM temporary_holds "
            "WHERE counterparty = ? AND product = ? AND status = ? AND expires_at > ? "
            "ORDER BY created_at, id",
            (counterparty.upper(), product, constants.HOLD_ACTIVE, format_time(now)),
        ).fetchall()
        return [cls._hold_from_row(row) for row in rows]

    def active_holds(
        self,
        counterparty: str,
        product: str,
        now: datetime | None = None,
    ) -> list[Hold]:
        """Active, unexpired holds on one counterparty and product."""
        self.initialise()
        moment = now or self.now()
        with self.connect() as connection:
            self._expire_stale(connection, moment)
            return self._active_holds(connection, counterparty, product, moment)

    def peers(
        self,
        counterparty: str,
        product: str,
        now: datetime | None = None,
    ) -> list[tuple[Hold, float]]:
        """Teammates holding capacity, each with the minutes remaining on the hold."""
        moment = now or self.now()
        return [(hold, hold.minutes_remaining(moment))
                for hold in self.active_holds(counterparty, product, moment)]

    def release(self, hold_id: int, username: str, now: datetime | None = None) -> Hold:
        """Release one hold. Only the creating username may do it."""
        self.initialise()
        moment = now or self.now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM temporary_holds WHERE id = ?", (int(hold_id),)
                ).fetchone()
                if row is None:
                    raise StoreError(f"hold {hold_id} does not exist")
                hold = self._hold_from_row(row)
                if hold.username.strip().lower() != str(username).strip().lower():
                    raise HoldOwnershipError(
                        f"hold {hold_id} belongs to {hold.username}; only the creating "
                        "username may release it"
                    )
                if hold.status != constants.HOLD_ACTIVE:
                    raise StoreError(f"hold {hold_id} is already {hold.status}")
                connection.execute(
                    "UPDATE temporary_holds SET status = ?, released_at = ? WHERE id = ?",
                    (constants.HOLD_RELEASED, format_time(moment), int(hold_id)),
                )
                connection.execute("COMMIT")
            except Exception:
                _rollback(connection)
                raise
            _logger.info("hold %d released by %s", hold_id, username)
            released = connection.execute(
                "SELECT * FROM temporary_holds WHERE id = ?", (int(hold_id),)
            ).fetchone()
            return self._hold_from_row(released)

    # -- decisions --------------------------------------------------------
    def commit_decision(
        self,
        request: CheckRequest,
        compute: Callable[[Sequence[Hold]], DecisionOutcome],
        *,
        create_hold: bool = True,
    ) -> CommittedDecision:
        """One decision, one transaction (§11). `compute` must stay pure and quick."""
        self.initialise()
        moment = self.now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._expire_stale(connection, moment)
                active = self._active_holds(
                    connection, request.counterparty, request.product, moment)
                outcome = compute(active)
                check_id = self._insert_check(connection, request, outcome, moment)
                hold_id: int | None = None
                if outcome.decision == constants.DECISION_YES and create_hold:
                    hold_id = self._insert_hold(
                        connection, request, outcome, moment, check_id)
                connection.execute("COMMIT")
            except Exception:
                _rollback(connection)
                raise
        _logger.info(
            "decision written check_id=%s hold_id=%s decision=%s user=%s cpty=%s",
            check_id, hold_id, outcome.decision, request.username, request.counterparty,
        )
        return CommittedDecision(outcome=outcome, check_id=check_id, hold_id=hold_id)

    def record_error(
        self,
        request: CheckRequest,
        message: str,
        *,
        affected_bucket: str = "",
        ffr_table: str = "",
    ) -> int:
        """Write an ERROR outcome to history so a failure is visible to the desk."""
        self.initialise()
        moment = self.now()
        outcome = DecisionOutcome(
            decision=constants.DECISION_ERROR,
            message=message,
            usage=0.0,
            affected_bucket=affected_bucket,
            ffr_table=ffr_table,
            ffr_weight=0.0,
        )
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                check_id = self._insert_check(connection, request, outcome, moment)
                connection.execute("COMMIT")
            except Exception:
                _rollback(connection)
                raise
        return check_id

    @staticmethod
    def _insert_check(
        connection: sqlite3.Connection,
        request: CheckRequest,
        outcome: DecisionOutcome,
        now: datetime,
    ) -> int:
        cursor = connection.execute(
            "INSERT INTO limit_checks (created_at, username, counterparty, "
            "parent_counterparty, product, tenor, affected_bucket, pair_or_currency, "
            "direction, notional_usd, usage, ffr_table, ffr_weight, decision, message) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                format_time(now),
                request.username,
                request.counterparty,
                outcome.parent_counterparty,
                request.product,
                request.tenor,
                outcome.affected_bucket,
                request.pair_or_currency,
                request.direction,
                float(request.notional_usd),
                float(outcome.usage),
                outcome.ffr_table,
                float(outcome.ffr_weight),
                outcome.decision,
                outcome.message,
            ),
        )
        return int(cursor.lastrowid or 0)

    def _insert_hold(
        self,
        connection: sqlite3.Connection,
        request: CheckRequest,
        outcome: DecisionOutcome,
        now: datetime,
        check_id: int,
    ) -> int:
        expires_at = now + timedelta(minutes=self.hold_ttl_minutes)
        cursor = connection.execute(
            "INSERT INTO temporary_holds (check_id, created_at, expires_at, released_at, "
            "status, username, counterparty, product, tenor, affected_bucket, "
            "pair_or_currency, notional_usd, usage) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                check_id,
                format_time(now),
                format_time(expires_at),
                None,
                constants.HOLD_ACTIVE,
                request.username,
                request.counterparty,
                request.product,
                request.tenor,
                outcome.affected_bucket,
                request.pair_or_currency,
                float(request.notional_usd),
                float(outcome.usage),
            ),
        )
        return int(cursor.lastrowid or 0)

    # -- history ----------------------------------------------------------
    def history_today(self, limit: int = 50, now: datetime | None = None) -> list[CheckRecord]:
        """Today's checks, newest first."""
        self.initialise()
        moment = now or self.now()
        start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM limit_checks WHERE created_at >= ? "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (format_time(start), int(limit)),
            ).fetchall()
        return [self._record_from_row(row) for row in rows]

    @staticmethod
    def _record_from_row(row: sqlite3.Row) -> CheckRecord:
        created = parse_time(row["created_at"])
        assert created is not None
        return CheckRecord(
            id=int(row["id"]),
            created_at=created,
            username=str(row["username"]),
            counterparty=str(row["counterparty"]),
            parent_counterparty=(
                str(row["parent_counterparty"])
                if row["parent_counterparty"] is not None else None
            ),
            product=str(row["product"]),
            tenor=str(row["tenor"]),
            affected_bucket=str(row["affected_bucket"]),
            pair_or_currency=str(row["pair_or_currency"]),
            direction=str(row["direction"]),
            notional_usd=float(row["notional_usd"]),
            usage=float(row["usage"]),
            ffr_table=str(row["ffr_table"]),
            ffr_weight=float(row["ffr_weight"]),
            decision=str(row["decision"]),
            message=str(row["message"]),
        )
