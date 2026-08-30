"""Deal and bucket availability, including this tool's own active holds.

    available = limit - utilisation - sum(usage of active holds on that
                counterparty + product)
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
    """The CKSBLMP row for one counterparty and the product's limit type."""
    wanted_cpty = str(counterparty).strip().upper()
    wanted_type = limit_type_for(product).upper()
    for row in rows:
        cpty = str(row.get(constants.COL_LIMIT_COUNTERPARTY, "") or "").strip().upper()
        code = str(row.get(constants.COL_LIMIT_TYPE, "") or "").strip().upper()
        if cpty == wanted_cpty and code == wanted_type:
            return row
    return None


def holds_usage(holds: Iterable[Hold], bucket: str | None = None) -> float:
    """Total usage of the given holds, optionally restricted to one bucket."""
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
    """Read one CKSBLMP row into a Surface, subtracting this tool's active holds.

    PROVISIONAL: utilisation is the sum of the occupied buckets CFS001..CFS00n, and a
    per-bucket limit is read from CFSL00n when that column exists, otherwise the deal
    limit is used for the bucket as well (§20: it is not confirmed whether per-bucket
    limits exist at all).
    """
    canonical = normalise_product(product)
    row = limit_row_for(limit_rows, counterparty, canonical)
    if row is None:
        raise LimitDataError(
            f"no {constants.TABLE_LIMITS} row for {counterparty} with limit type "
            f"{limit_type_for(canonical)} (product {canonical})"
        )
    deal_limit = numbers.to_float(row.get(constants.COL_LIMIT_AMOUNT))
    buckets: list[BucketSurface] = []
    utilisation = 0.0
    for name in constants.BUCKETS:
        index = constants.BUCKET_INDEX[name]
        occupied = numbers.to_float(row.get(f"{constants.COL_OCCUPIED_PREFIX}{index}"))
        raw_bucket_limit = row.get(f"{constants.COL_BUCKET_LIMIT_PREFIX}{index}")
        bucket_limit = (
            numbers.to_float(raw_bucket_limit)
            if str(raw_bucket_limit or "").strip() != ""
            else deal_limit
        )
        utilisation += occupied
        buckets.append(
            BucketSurface(
                bucket=name,
                limit=bucket_limit,
                occupied=occupied,
                holds_usage=holds_usage(holds, name),
            )
        )
    return Surface(
        counterparty=str(counterparty).strip().upper(),
        product=canonical,
        limit_type=limit_type_for(canonical),
        deal_limit=deal_limit,
        utilisation=utilisation,
        holds_usage=holds_usage(holds),
        buckets=tuple(buckets),
    )


def fits(surface: Surface, bucket: str, usage: float) -> tuple[bool, str]:
    """Rule §4.2: the usage must fit BOTH the deal limit AND the affected bucket."""
    bucket_surface = surface.bucket(bucket)
    if bucket_surface is None:
        raise LimitDataError(
            f"tenor bucket {bucket!r} is not one of {', '.join(constants.BUCKETS)}"
        )
    failures: list[str] = []
    if usage > surface.available:
        failures.append(
            f"the deal limit (available {numbers.millions(surface.available)})"
        )
    if usage > bucket_surface.available:
        failures.append(
            f"tenor bucket {bucket} (available {numbers.millions(bucket_surface.available)})"
        )
    if failures:
        return False, (
            f"Insufficient limit: usage {numbers.millions(usage)} exceeds "
            + " and ".join(failures)
            + ". Hard reject - no override and no partial hold."
        )
    return True, (
        f"Usage {numbers.millions(usage)} fits the deal limit "
        f"(available {numbers.millions(surface.available)}) and bucket {bucket} "
        f"(available {numbers.millions(bucket_surface.available)})."
    )
