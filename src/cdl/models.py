"""Plain dataclasses shared by logic, store, cli and ui. No pandas, no I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from . import constants


@dataclass(frozen=True)
class CheckRequest:
    """One proposed deal, as typed by the trader (already validated)."""

    username: str
    counterparty: str
    product: str
    tenor: str
    pair_or_currency: str
    direction: str
    notional_usd: float


@dataclass(frozen=True)
class FfrLookup:
    """The FFR weight actually used, and where it came from."""

    weight: float
    table_name: str
    source_label: str
    time_period: str
    weight_column: str = ""
    currency_class: str | None = None
    filter_description: str = ""

    @property
    def weight_percent(self) -> float:
        return self.weight * 100.0


@dataclass(frozen=True)
class BucketSurface:
    """One time period of one counterparty/product limit.

    `available` is the ladder result, not a plain subtraction: the limit system is
    cumulative, so a period is limited by its own headroom AND by every shorter
    period (see build_surface in logic/availability.py).
    """

    bucket: str
    slot: int
    limit: float
    occupied: float
    holds_usage: float
    reverse_cumulative: float
    available: float

    @property
    def own_headroom(self) -> float:
        """This period's own limit less the cash risk from here to the longest period."""
        return self.limit - self.reverse_cumulative


@dataclass(frozen=True)
class Surface:
    """The limit surface for one counterparty and product."""

    counterparty: str
    product: str
    limit_type: str
    deal_limit: float
    utilisation: float
    holds_usage: float
    buckets: tuple[BucketSurface, ...] = ()

    @property
    def available(self) -> float:
        return self.deal_limit - self.utilisation - self.holds_usage

    def bucket(self, name: str) -> BucketSurface | None:
        for item in self.buckets:
            if item.bucket == name:
                return item
        return None


@dataclass(frozen=True)
class ChainNode:
    """One node of the ownership chain. Display only - never decides Y/N."""

    counterparty: str
    parent: str | None
    depth: int
    is_submitted: bool
    surface: Surface | None = None
    agreement_text: str = ""


@dataclass(frozen=True)
class Hold:
    """A temporary hold (soft reservation), not a booking."""

    id: int
    check_id: int | None
    created_at: datetime
    expires_at: datetime
    released_at: datetime | None
    status: str
    username: str
    counterparty: str
    product: str
    tenor: str
    affected_bucket: str
    pair_or_currency: str
    notional_usd: float
    usage: float

    def minutes_remaining(self, now: datetime) -> float:
        return max(0.0, (self.expires_at - now).total_seconds() / 60.0)


@dataclass(frozen=True)
class CheckRecord:
    """One row of today's history."""

    id: int
    created_at: datetime
    username: str
    counterparty: str
    parent_counterparty: str | None
    product: str
    tenor: str
    affected_bucket: str
    pair_or_currency: str
    direction: str
    notional_usd: float
    usage: float
    ffr_table: str
    ffr_weight: float
    decision: str
    message: str


@dataclass(frozen=True)
class DecisionOutcome:
    """What the pure availability computation decided, ready to be written down.

    Produced inside the store transaction (§11) so that the holds it was computed
    against cannot change between the computation and the insert.
    """

    decision: str
    message: str
    usage: float
    affected_bucket: str
    ffr_table: str
    ffr_weight: float
    parent_counterparty: str | None = None
    surface: Surface | None = None
    active_holds: tuple[Hold, ...] = ()


@dataclass(frozen=True)
class CommittedDecision:
    """The identifiers written by one decision transaction."""

    outcome: DecisionOutcome
    check_id: int | None = None
    hold_id: int | None = None


@dataclass(frozen=True)
class CheckResult:
    """Everything the UI, the CLI and the report need to show one decision."""

    request: CheckRequest
    decision: str
    message: str
    sources: dict[str, str] = field(default_factory=dict)
    ffr: FfrLookup | None = None
    usage: float = 0.0
    affected_bucket: str = ""
    surface: Surface | None = None
    chain: tuple[ChainNode, ...] = ()
    active_holds: tuple[Hold, ...] = ()
    check_id: int | None = None
    hold_id: int | None = None
    failed_table: str | None = None
    failed_source: str | None = None

    @property
    def is_yes(self) -> bool:
        return self.decision == constants.DECISION_YES

    @property
    def is_error(self) -> bool:
        return self.decision == constants.DECISION_ERROR

    @property
    def deal_available_before(self) -> float:
        return self.surface.available if self.surface else 0.0

    @property
    def deal_available_after(self) -> float:
        return self.deal_available_before - self.usage

    @property
    def bucket_surface(self) -> BucketSurface | None:
        if self.surface is None or not self.affected_bucket:
            return None
        return self.surface.bucket(self.affected_bucket)

    @property
    def bucket_available_before(self) -> float:
        bucket = self.bucket_surface
        return bucket.available if bucket else 0.0

    @property
    def bucket_available_after(self) -> float:
        return self.bucket_available_before - self.usage
