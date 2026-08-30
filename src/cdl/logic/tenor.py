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


def bucket_for(tenor: str) -> str:
    """PROVISIONAL bucket map - one function, easy to edit.

    Spot, all weeks and 1 months -> Spot-1M; 2-3 months -> 1M-3M;
    4-6 months -> 3M-6M; 7-12 months -> 6M-1Y; 13+ months and all years -> 1Y+.
    """
    label = normalise_tenor(tenor)
    if label == "Spot" or label.endswith(("week", "weeks")) or label == "1 months":
        return "Spot-1M"
    if label.endswith("months"):
        months = int(label.split(" ", 1)[0])
        if months <= 3:
            return "1M-3M"
        if months <= 6:
            return "3M-6M"
        if months <= 12:
            return "6M-1Y"
        return "1Y+"
    return "1Y+"


def bucket_index(bucket: str) -> int:
    """PROVISIONAL: the CFS0xx / CFSL0xx index that belongs to a bucket."""
    try:
        return constants.BUCKET_INDEX[bucket]
    except KeyError as error:
        raise ValueError(
            f"unknown tenor bucket {bucket!r}; valid: {', '.join(constants.BUCKETS)}"
        ) from error
