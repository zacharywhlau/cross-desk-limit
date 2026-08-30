"""The orchestrator: CheckRequest -> CheckResult. No UI, no SQL strings.

The decision is taken on the SUBMITTED counterparty only. Parent and ultimate-parent
figures are collected as reference information and never decide Y/N.

When a holds gateway is supplied (the sqlite3 store), the availability computation and
the two inserts happen inside ONE transaction, so two traders cannot spend the same
last capacity. The gateway is passed in rather than imported, keeping the dependency
direction logic -> treats only.
"""

from __future__ import annotations

from typing import Callable, Protocol, Sequence

from .. import constants
from ..config import Settings, load_settings
from ..logging_setup import get_logger
from ..models import (
    ChainNode,
    CheckRequest,
    CheckResult,
    CommittedDecision,
    DecisionOutcome,
    Hold,
)
from ..treats import source as source_module
from ..treats import sql as sql_builder
from ..treats.tabular import Record
from . import availability, numbers
from .calculators import ProductError, normalise_product, usage_for
from .counterparty import (
    CounterpartyError,
    find_row,
    parent_chain,
    parent_of,
    validate_counterparty,
)
from .ffr import FfrError, lookup_ffr
from .tenor import UnknownTenorError, bucket_for, normalise_tenor

_logger = get_logger("logic.check")


class HoldsGateway(Protocol):
    """The part of the store the orchestrator needs (structural typing only)."""

    def commit_decision(
        self,
        request: CheckRequest,
        compute: Callable[[Sequence[Hold]], DecisionOutcome],
        *,
        create_hold: bool = True,
    ) -> CommittedDecision:
        ...


class ValidationError(ValueError):
    """The submitted deal is not acceptable. Raised before any remote call."""


def validate_request(
    username: str,
    counterparty: str,
    product: str,
    tenor: str,
    pair_or_currency: str,
    direction: str,
    notional_usd: float | str,
) -> CheckRequest:
    """§5: validate and normalise every field before anything is fetched."""
    name = str(username or "").strip()
    if not name:
        raise ValidationError("username is required (the name you typed at login)")

    try:
        acronym = validate_counterparty(counterparty)
    except CounterpartyError as error:
        raise ValidationError(str(error)) from error

    try:
        canonical_product = normalise_product(product)
    except ProductError as error:
        raise ValidationError(str(error)) from error

    try:
        canonical_tenor = normalise_tenor(tenor)
    except UnknownTenorError as error:
        raise ValidationError(str(error)) from error

    pair = str(pair_or_currency or "").strip().upper()
    if not pair:
        raise ValidationError("pair or currency is required, e.g. USDHKD or HKD")

    side = str(direction or "").strip().lower()
    if side not in constants.DIRECTIONS:
        raise ValidationError(
            f"direction must be one of {', '.join(constants.DIRECTIONS)}; got {direction!r}"
        )

    notional = numbers.to_float(notional_usd, default=float("nan"))
    if notional != notional or notional <= 0:  # NaN or non-positive
        raise ValidationError(
            f"notional_usd must be a positive number; got {notional_usd!r}"
        )

    return CheckRequest(
        username=name,
        counterparty=acronym,
        product=canonical_product,
        tenor=canonical_tenor,
        pair_or_currency=pair,
        direction=side,
        notional_usd=notional,
    )


def _row_fetcher(
    table: str,
    column: str,
    settings: Settings,
) -> Callable[[str], list[Record]]:
    """Fetch rows of one table for one acronym, remembering what was already read."""
    seen: dict[str, list[Record]] = {}

    def fetch(acronym: str) -> list[Record]:
        key = str(acronym).strip().upper()
        if key not in seen:
            where = sql_builder.equals_clause(column, key)
            seen[key] = source_module.fetch_table(table, settings, where=where).rows
        return seen[key]

    return fetch


def _reference_nodes(
    chain: Sequence[str],
    request: CheckRequest,
    fetch_limits: Callable[[str], list[Record]],
    fetch_agreement: Callable[[str], list[Record]],
    ttcpipp_rows_for: Callable[[str], list[Record]],
    holds: Sequence[Hold],
) -> tuple[tuple[ChainNode, ...], list[str]]:
    """Chain nodes with their (display-only) figures. Failures degrade, never decide."""
    nodes: list[ChainNode] = []
    notes: list[str] = []
    for depth, acronym in enumerate(chain):
        is_submitted = depth == 0
        parent = parent_of(find_row(ttcpipp_rows_for(acronym), acronym))
        surface = None
        try:
            surface = availability.build_surface(
                acronym,
                request.product,
                fetch_limits(acronym),
                holds if is_submitted else (),
            )
        except Exception as error:  # reference figures only
            if is_submitted:
                raise
            notes.append(f"{acronym}: {error}")
        agreement = ""
        try:
            for row in fetch_agreement(acronym):
                text = str(row.get(constants.COL_AGREEMENT_TEXT, "") or "").strip()
                if text:
                    agreement = text
                    break
        except Exception as error:  # display text only
            notes.append(f"{acronym} agreement text: {error}")
        nodes.append(
            ChainNode(
                counterparty=acronym,
                parent=parent,
                depth=depth,
                is_submitted=is_submitted,
                surface=surface,
                agreement_text=agreement,
            )
        )
    return tuple(nodes), notes


def run_check(
    request: CheckRequest,
    settings: Settings | None = None,
    store: HoldsGateway | None = None,
    *,
    create_hold: bool = True,
) -> CheckResult:
    """Take one decision. Returns Y, N or ERROR - never raises for a data failure."""
    settings = settings or load_settings()
    sources = {
        constants.TABLE_COUNTERPARTY: source_module.effective_source(
            constants.TABLE_COUNTERPARTY, settings),
        constants.TABLE_LIMITS: source_module.effective_source(
            constants.TABLE_LIMITS, settings),
        constants.TABLE_AGREEMENT: source_module.effective_source(
            constants.TABLE_AGREEMENT, settings),
        settings.ffr.table: settings.ffr.source,
    }
    table = "-"
    try:
        bucket = bucket_for(request.tenor)

        table = constants.TABLE_COUNTERPARTY
        fetch_cpty = _row_fetcher(
            constants.TABLE_COUNTERPARTY, constants.COL_CPTY_ACRONYM, settings)
        chain = parent_chain(
            request.counterparty,
            lambda acronym: find_row(fetch_cpty(acronym), acronym),
        )

        table = settings.ffr.table
        ffr = lookup_ffr(request.product, request.pair_or_currency, request.tenor, settings)
        usage = usage_for(request.product, request.notional_usd, ffr.weight)

        table = constants.TABLE_LIMITS
        fetch_limits = _row_fetcher(
            constants.TABLE_LIMITS, constants.COL_LIMIT_COUNTERPARTY, settings)
        limit_rows = fetch_limits(request.counterparty)
        # Fail here rather than inside the transaction if the row is missing.
        availability.build_surface(request.counterparty, request.product, limit_rows)

        table = constants.TABLE_AGREEMENT
        fetch_agreement = _row_fetcher(
            constants.TABLE_AGREEMENT, constants.COL_AGREEMENT_COUNTERPARTY, settings)

        parent = chain[1] if len(chain) > 1 else None
        notes: list[str] = []

        def compute(active: Sequence[Hold]) -> DecisionOutcome:
            surface = availability.build_surface(
                request.counterparty, request.product, limit_rows, active)
            allowed, message = availability.fits(surface, bucket, usage)
            return DecisionOutcome(
                decision=constants.DECISION_YES if allowed else constants.DECISION_NO,
                message=message,
                usage=usage,
                affected_bucket=bucket,
                ffr_table=ffr.table_name,
                ffr_weight=ffr.weight,
                parent_counterparty=parent,
                surface=surface,
                active_holds=tuple(active),
            )

        table = constants.TABLE_LIMITS
        if store is None:
            committed = CommittedDecision(outcome=compute(()))
        else:
            committed = store.commit_decision(request, compute, create_hold=create_hold)
        outcome = committed.outcome

        table = constants.TABLE_AGREEMENT
        nodes, reference_notes = _reference_nodes(
            chain, request, fetch_limits, fetch_agreement,
            fetch_cpty, outcome.active_holds,
        )
        notes.extend(reference_notes)

        message = outcome.message
        if notes:
            message = f"{message} Reference data unavailable: {'; '.join(notes)}"

        _logger.info(
            "check user=%s cpty=%s product=%s tenor=%s bucket=%s notional=%s usage=%s "
            "ffr=%s/%s weight=%s decision=%s hold=%s",
            request.username, request.counterparty, request.product, request.tenor,
            bucket, numbers.amount(request.notional_usd), numbers.amount(outcome.usage),
            ffr.table_name, ffr.weight_column, numbers.percent(ffr.weight),
            outcome.decision, committed.hold_id,
        )
        return CheckResult(
            request=request,
            decision=outcome.decision,
            message=message,
            sources=sources,
            ffr=ffr,
            usage=outcome.usage,
            affected_bucket=bucket,
            surface=outcome.surface,
            chain=nodes,
            active_holds=outcome.active_holds,
            check_id=committed.check_id,
            hold_id=committed.hold_id,
        )
    except (ValidationError, UnknownTenorError, ProductError, CounterpartyError) as error:
        _logger.warning("check rejected input: %s", error)
        return CheckResult(
            request=request,
            decision=constants.DECISION_ERROR,
            message=str(error),
            sources=sources,
        )
    except Exception as error:
        mode = sources.get(table, "-")
        message = (
            f"Cannot decide: reading {table} in {mode} mode failed - "
            f"{type(error).__name__}: {error}"
        )
        if isinstance(error, FfrError):
            message = (
                f"Cannot decide: the FFR weight could not be resolved from {table} "
                f"in {mode} mode - {error}"
            )
        _logger.error("check failed table=%s mode=%s error=%s", table, mode, error)
        return CheckResult(
            request=request,
            decision=constants.DECISION_ERROR,
            message=message,
            sources=sources,
            failed_table=table,
            failed_source=mode,
        )
