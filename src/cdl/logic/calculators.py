"""Per-product usage functions plus a registry.

All four currently implement the default shared formula. Each product has its own
function so one product's formula can be replaced later without touching the others.
"""

from __future__ import annotations

from typing import Callable, Final

from .. import constants

UsageFunction = Callable[[float, float], float]


class ProductError(ValueError):
    """The product is not one of the four this tool supports."""


def _default_shared_formula(notional_usd: float, ffr_weight: float) -> float:
    """usage = notional_usd * (1 + ffr_weight)."""
    return float(notional_usd) * (1.0 + float(ffr_weight))


def fx_usage(notional_usd: float, ffr_weight: float) -> float:
    """FX. Default shared formula - may diverge per product."""
    return _default_shared_formula(notional_usd, ffr_weight)


def gold_usage(notional_usd: float, ffr_weight: float) -> float:
    """Gold. Default shared formula - may diverge per product."""
    return _default_shared_formula(notional_usd, ffr_weight)


def irs_usage(notional_usd: float, ffr_weight: float) -> float:
    """IRS. Default shared formula - may diverge per product."""
    return _default_shared_formula(notional_usd, ffr_weight)


def equity_swap_usage(notional_usd: float, ffr_weight: float) -> float:
    """Equity swaps. Default shared formula - may diverge per product."""
    return _default_shared_formula(notional_usd, ffr_weight)


USAGE_CALCULATORS: Final[dict[str, UsageFunction]] = {
    constants.PRODUCT_FX: fx_usage,
    constants.PRODUCT_GOLD: gold_usage,
    constants.PRODUCT_IRS: irs_usage,
    constants.PRODUCT_EQUITY_SWAP: equity_swap_usage,
}


def normalise_product(raw: str) -> str:
    """Map typed input onto one of the four product names."""
    text = " ".join(str(raw or "").split())
    if not text:
        raise ProductError(f"product is required; valid: {', '.join(constants.PRODUCTS)}")
    for product in constants.PRODUCTS:
        if product.upper() == text.upper():
            return product
    alias = constants.PRODUCT_ALIASES.get(text.upper())
    if alias is not None:
        return alias
    raise ProductError(
        f"unknown product {text!r}; valid: {', '.join(constants.PRODUCTS)}"
    )


def usage_for(product: str, notional_usd: float, ffr_weight: float) -> float:
    """Dispatch to the product's own usage function."""
    return USAGE_CALCULATORS[normalise_product(product)](notional_usd, ffr_weight)


def limit_type_for(product: str) -> str:
    """CFSLTT code for a product. PROVISIONAL for everything except FX01."""
    return constants.LIMIT_TYPE_BY_PRODUCT[normalise_product(product)]
