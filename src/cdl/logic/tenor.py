"""Tenor grid, alias normalisation and the tenor bucket map."""

from __future__ import annotations

import re

from .. import constants

_COMPACT = re.compile(r"^(\d+)\s*(W|WEEK|WEEKS|M|MONTH|MONTHS|Y|YEAR|YEARS)$")

_UPPER_TO_LABEL = {label.upper(): label for label in constants.TENOR_GRID}

VALID_EXAMPLES = (
    "Spot, 1 week, 3 weeks, 1 months, 18 months, 60 months, 6 years, 30 years "
    "(aliases: spot, 1W, 1M, 3M, 6M, 1Y, 2Y, 5Y, 10Y, 30Y)"
)


class UnknownTenorError(ValueError):
    """The tenor is not one of the 89 grid values or a known alias."""


def normalise_tenor(raw: str) -> str:
    """Map typed input onto one of the 89 grid labels. Case and space tolerant."""
    text = " ".join(str(raw or "").split())
    if not text:
        raise UnknownTenorError(f"tenor is required; valid examples: {VALID_EXAMPLES}")
    upper = text.upper()
    if upper in constants.TENOR_ALIASES:
        return constants.TENOR_ALIASES[upper]
    if upper in _UPPER_TO_LABEL:
        return _UPPER_TO_LABEL[upper]
    match = _COMPACT.match(upper)
    if match:
        count, unit = int(match.group(1)), match.group(2)[0]
        if unit == "W":
            candidate = f"{count} week" if count == 1 else f"{count} weeks"
        elif unit == "M":
            candidate = f"{count} months"
        else:
            candidate = f"{count} years" if count >= 6 else f"{count * 12} months"
        if candidate.upper() in _UPPER_TO_LABEL:
            return _UPPER_TO_LABEL[candidate.upper()]
    raise UnknownTenorError(
        f"unknown tenor {text!r}; valid examples: {VALID_EXAMPLES}"
    )


def months_of(tenor: str) -> float:
    """Approximate maturity in months for a grid label (used by the bucket map)."""
    label = normalise_tenor(tenor)
    if label == "Spot":
        return 0.0
    count_text, unit = label.split(" ", 1)
    count = int(count_text)
    if unit.startswith("week"):
        return count * 7.0 / 30.0
    if unit.startswith("month"):
        return float(count)
    return count * 12.0


#: PROVISIONAL upper bound in months for each period of the ladder, in slot order.
#: CALL, TDY and TOM carry no bound because no FFR tenor maps onto them; a deal never
#: lands there, but their figures are still read and displayed.
_BUCKET_UPPER_BOUND_MONTHS: tuple[tuple[str, float], ...] = (
    ("SPT", 0.0),
    ("SPT-1M", 1.0),
    ("1M-3M", 3.0),
    ("3M-6M", 6.0),
    ("6M-1Y", 12.0),
    ("1Y-3Y", 36.0),
    ("3Y-5Y", 60.0),
    ("5Y-7Y", 84.0),
    ("7Y-10Y", 120.0),
    ("10Y-15Y", 180.0),
)

#: Anything longer than the last bound above.
_LONGEST_BUCKET: str = "15Y+"


def bucket_for(tenor: str) -> str:
    """PROVISIONAL map from a tenor onto one of the 14 periods - one function to edit.

    A deal falls in the first period whose upper bound covers its maturity: Spot -> SPT,
    weeks and 1 months -> SPT-1M, 2-3 months -> 1M-3M, and so on up to 15Y+.
    """
    months = months_of(tenor)
    for name, upper in _BUCKET_UPPER_BOUND_MONTHS:
        if months <= upper:
            return name
    return _LONGEST_BUCKET


def bucket_index(bucket: str) -> int:
    """The CFSO / CFSL slot number (1..14) that belongs to a period."""
    try:
        return constants.BUCKET_INDEX[bucket]
    except KeyError as error:
        raise ValueError(
            f"unknown tenor bucket {bucket!r}; valid: {', '.join(constants.BUCKETS)}"
        ) from error
