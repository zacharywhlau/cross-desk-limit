"""Availability across the 14 time periods of the limit system.

The limit system is cumulative, not a set of independent buckets. A deal booked in
one period consumes headroom in that period AND in every shorter one, which is why a
fully used short period blocks the longer ones too. For period i, shortest first:

    reverse_cum[i] = sum(cash risk of period j for j >= i)    # our holds included
    available[i]   = max(0, min(limit[i] - reverse_cum[i], available[i - 1]))

A deal therefore only has to fit `available` of its own period; that figure already
carries every shorter period with it. The deal-level (total) limit is checked as well,
because §4.2 asks for both.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from .. import constants
from ..models import BucketSurface, Hold, Surface
from ..treats.tabular import Record
from . import numbers
from .calculators import limit_type_for, normalise_product


class LimitDataError(RuntimeError):
    """The limit table has no usable row for this counterparty and product."""


def limit_row_for(rows: Iterable[Record], counterparty: str, product: str) -> Record | None:
    """The CKSBLMP row for one counterparty and the product's limit type.

    Both sides are compared on `constants.code_key`, so "FX 01" also matches an export
    that writes "FX01" or pads the code.
    """
    wanted_cpty = constants.code_key(counterparty)
    wanted_type = constants.code_key(limit_type_for(product))
    for row in rows:
        cpty = constants.code_key(row.get(constants.COL_LIMIT_COUNTERPARTY, ""))
        code = constants.code_key(row.get(constants.COL_LIMIT_TYPE, ""))
        if cpty == wanted_cpty and code == wanted_type:
            return row
    return None


def holds_usage(holds: Iterable[Hold], bucket: str | None = None) -> float:
    """Total usage of the given holds, optionally restricted to one period."""
    return sum(
        hold.usage for hold in holds
        if bucket is None or hold.affected_bucket == bucket
    )


def build_surface(
    counterparty: str,
    product: str,
    limit_rows: Sequence[Record],
    holds: Sequence[Hold] = (),
) -> Surface:
    """Read one CKSBLMP row into the limit ladder, including this tool's own holds.

    PROVISIONAL: the total limit is CFSLTT, a period limit is CFSL{slot:02d} (falling
    back to the total when that column is absent) and the cash risk of a period is
    CFSO{slot:02d}. The period boundaries live in logic/tenor.py.
    """
    canonical = normalise_product(product)
    row = limit_row_for(limit_rows, counterparty, canonical)
    if row is None:
        raise LimitDataError(
            f"no {constants.TABLE_LIMITS} row for {counterparty} with limit type "
            f"{limit_type_for(canonical)} (product {canonical})"
        )
    total_limit = numbers.to_float(row.get(constants.COL_LIMIT_AMOUNT))

    limits: dict[str, float] = {}
    risk: dict[str, float] = {}
    own_holds: dict[str, float] = {}
    for name in constants.BUCKETS:
        slot = constants.BUCKET_INDEX[name]
        occupied = numbers.to_float(row.get(constants.occupied_column(slot)))
        raw_limit = row.get(constants.slot_limit_column(slot))
        limits[name] = (
            numbers.to_float(raw_limit)
            if str(raw_limit or "").strip() != ""
            else total_limit
        )
        risk[name] = occupied
        own_holds[name] = holds_usage(holds, name)

    reverse_cumulative: dict[str, float] = {}
    running_total = 0.0
    for name in reversed(constants.BUCKETS):
        running_total += risk[name] + own_holds[name]
        reverse_cumulative[name] = running_total

    buckets: list[BucketSurface] = []
    running_available: float | None = None
    for name in constants.BUCKETS:
        headroom = limits[name] - reverse_cumulative[name]
        running_available = (
            headroom if running_available is None else min(headroom, running_available)
        )
        buckets.append(
            BucketSurface(
                bucket=name,
                slot=constants.BUCKET_INDEX[name],
                limit=limits[name],
                occupied=risk[name],
                holds_usage=own_holds[name],
                reverse_cumulative=reverse_cumulative[name],
                available=max(0.0, running_available),
            )
        )

    return Surface(
        counterparty=str(counterparty).strip().upper(),
        product=canonical,
        limit_type=limit_type_for(canonical),
        deal_limit=total_limit,
        utilisation=sum(risk.values()),
        holds_usage=holds_usage(holds),
        buckets=tuple(buckets),
    )


def fits(surface: Surface, bucket: str, usage: float) -> tuple[bool, str]:
    """Rule §4.2: the usage must fit the total limit AND the affected period.

    The period figure is the ladder result, so passing it also means the deal fits
    every shorter period.
    """
    bucket_surface = surface.bucket(bucket)
    if bucket_surface is None:
        raise LimitDataError(
            f"time period {bucket!r} is not one of {', '.join(constants.BUCKETS)}"
        )
    failures: list[str] = []
    if usage > surface.available:
        failures.append(
            f"the total limit (available {numbers.millions(surface.available)})"
        )
    if usage > bucket_surface.available:
        failures.append(
            f"period {bucket} (available {numbers.millions(bucket_surface.available)}, "
            f"limit {numbers.millions(bucket_surface.limit)} less cash risk "
            f"{numbers.millions(bucket_surface.reverse_cumulative)} from {bucket} onwards)"
        )
    if failures:
        return False, (
            f"Insufficient limit: usage {numbers.millions(usage)} exceeds "
            + " and ".join(failures)
            + ". Hard reject - no override and no partial hold."
        )
    return True, (
        f"Usage {numbers.millions(usage)} fits period {bucket} "
        f"(available {numbers.millions(bucket_surface.available)}) and the total limit "
        f"(available {numbers.millions(surface.available)})."
    )
