"""Tolerant parsing and display of the numbers that come out of a SQL export."""

from __future__ import annotations

from typing import Any


def to_float(value: Any, default: float = 0.0) -> float:
    """Parse an exported amount. Blank, None and unparsable values give `default`."""
    if value is None:
        return default
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("_", "")
    if not text:
        return default
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1].strip()
    try:
        number = float(text)
    except ValueError:
        return default
    return -number if negative else number


def parse_percent(value: Any) -> float:
    """FFR cell -> fraction: '1%' -> 0.01, '2.5%' -> 0.025, 0.01 -> 0.01, 1 -> 0.01.

    A bare number of 1 or more is read as a percentage, because that is how the
    published grid states it; anything below 1 is already a fraction.
    """
    if value is None:
        raise ValueError("FFR weight is empty")
    text = str(value).strip()
    if not text:
        raise ValueError("FFR weight is empty")
    if text.endswith("%"):
        body = text[:-1].strip()
        try:
            return float(body) / 100.0
        except ValueError as error:
            raise ValueError(f"FFR weight {value!r} is not a percentage") from error
    try:
        number = float(text.replace(",", ""))
    except ValueError as error:
        raise ValueError(f"FFR weight {value!r} is not a number") from error
    return number / 100.0 if abs(number) >= 1.0 else number


def millions(amount: float) -> str:
    """15,991,000 -> '15.99mm' (the desk's reading unit)."""
    return f"{amount / 1_000_000.0:,.2f}mm"


def percent(fraction: float, places: int = 3) -> str:
    """0.018 -> '1.8%'."""
    text = f"{fraction * 100.0:.{places}f}".rstrip("0").rstrip(".")
    return f"{text or '0'}%"


def amount(value: float) -> str:
    """Plain grouped amount, used in the numbered trace and the CLI."""
    return f"{value:,.0f}"
