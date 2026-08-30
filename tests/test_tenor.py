"""§22: tenor aliases, the full grid, bucket boundaries, clear error for unknown input."""

from __future__ import annotations

import pytest

from cdl import constants
from cdl.logic.tenor import UnknownTenorError, bucket_for, normalise_tenor


def test_grid_has_89_values_in_the_published_order() -> None:
    assert len(constants.TENOR_GRID) == 89
    assert constants.TENOR_GRID[0] == "Spot"
    assert constants.TENOR_GRID[1:4] == ("1 week", "2 weeks", "3 weeks")
    assert constants.TENOR_GRID[4] == "1 months"
    assert constants.TENOR_GRID[63] == "60 months"
    assert constants.TENOR_GRID[64] == "6 years"
    assert constants.TENOR_GRID[-1] == "30 years"


@pytest.mark.parametrize("label", constants.TENOR_GRID)
def test_every_grid_value_normalises_to_itself(label: str) -> None:
    assert normalise_tenor(label) == label


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("spot", "Spot"),
        ("SPOT", "Spot"),
        ("  Spot  ", "Spot"),
        ("1W", "1 week"),
        ("2w", "2 weeks"),
        ("3W", "3 weeks"),
        ("1M", "1 months"),
        ("3m", "3 months"),
        ("6M", "6 months"),
        ("1Y", "12 months"),
        ("2Y", "24 months"),
        ("5Y", "60 months"),
        ("10Y", "10 years"),
        ("30y", "30 years"),
        ("18 MONTHS", "18 months"),
        ("7  years", "7 years"),
        ("12M", "12 months"),
        ("7Y", "7 years"),
    ],
)
def test_aliases_and_whitespace(raw: str, expected: str) -> None:
    assert normalise_tenor(raw) == expected


@pytest.mark.parametrize(
    ("tenor", "bucket"),
    [
        ("Spot", "Spot-1M"),
        ("1 week", "Spot-1M"),
        ("3 weeks", "Spot-1M"),
        ("1 months", "Spot-1M"),
        ("2 months", "1M-3M"),
        ("3 months", "1M-3M"),
        ("4 months", "3M-6M"),
        ("6 months", "3M-6M"),
        ("7 months", "6M-1Y"),
        ("12 months", "6M-1Y"),
        ("13 months", "1Y+"),
        ("60 months", "1Y+"),
        ("6 years", "1Y+"),
        ("30 years", "1Y+"),
    ],
)
def test_bucket_boundaries(tenor: str, bucket: str) -> None:
    assert bucket_for(tenor) == bucket


def test_every_grid_value_lands_in_exactly_one_known_bucket() -> None:
    assert {bucket_for(label) for label in constants.TENOR_GRID} <= set(constants.BUCKETS)


@pytest.mark.parametrize("raw", ["", "   ", "4 fortnights", "1 quarter", "31 years", "0M", "banana"])
def test_unknown_tenor_lists_valid_examples(raw: str) -> None:
    with pytest.raises(UnknownTenorError) as error:
        normalise_tenor(raw)
    message = str(error.value)
    assert "Spot" in message and "months" in message
    assert message.lower() != "unknown"
