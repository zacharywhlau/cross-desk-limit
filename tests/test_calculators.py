"""§22: all four products return notional * (1 + weight)."""

from __future__ import annotations

import pytest

from cdl import constants
from cdl.logic.calculators import (
    USAGE_CALCULATORS,
    ProductError,
    equity_swap_usage,
    fx_usage,
    gold_usage,
    irs_usage,
    limit_type_for,
    normalise_product,
    usage_for,
)


def test_registry_covers_every_product_exactly_once() -> None:
    assert set(USAGE_CALCULATORS) == set(constants.PRODUCTS)
    assert list(USAGE_CALCULATORS.values()) == [
        fx_usage, gold_usage, irs_usage, equity_swap_usage
    ]


@pytest.mark.parametrize("product", constants.PRODUCTS)
@pytest.mark.parametrize(
    ("notional", "weight"),
    [(500_000, 0.018), (1_000_000, 0.0), (250_000, 0.5), (1, 0.077)],
)
def test_default_shared_formula(product: str, notional: float, weight: float) -> None:
    assert usage_for(product, notional, weight) == pytest.approx(notional * (1 + weight))


def test_reference_case_usage() -> None:
    assert usage_for("FX", 500_000, 0.018) == pytest.approx(509_000.0)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("fx", "FX"),
        ("GOLD", "Gold"),
        ("gold", "Gold"),
        ("irs", "IRS"),
        ("equity swaps", "Equity swaps"),
        ("EQ_SWAP", "Equity swaps"),
    ],
)
def test_product_normalisation(raw: str, expected: str) -> None:
    assert normalise_product(raw) == expected


def test_unknown_product_lists_the_four() -> None:
    with pytest.raises(ProductError) as error:
        normalise_product("crude oil")
    for product in constants.PRODUCTS:
        assert product in str(error.value)


def test_fx_limit_type_is_the_confirmed_code() -> None:
    assert limit_type_for("FX") == "FX01"
    assert {limit_type_for(product) for product in constants.PRODUCTS} == {
        "FX01", "GD01", "IR01", "EQ01"
    }
