"""Reference data that is NOT a table: names, grids and mappings.

Everything the owning team has not confirmed is marked PROVISIONAL. A provisional
value stays here (or in one small function) so the operator can edit it in one place.
No environment specific value belongs in this module - those come from config.
"""

from __future__ import annotations

from typing import Final

# ---------------------------------------------------------------------------
# Table names
# ---------------------------------------------------------------------------
TABLE_COUNTERPARTY: Final[str] = "TTCPIPP"
TABLE_LIMITS: Final[str] = "CKSBLMP"
TABLE_AGREEMENT: Final[str] = "CKOVLMP"
TABLE_FFR_DEFAULT: Final[str] = "CKBLOTP"  # overridable via config ffr.table

#: Tables whose source is resolved per table by config (ffr.table has its own switch).
CONFIGURED_TABLES: Final[tuple[str, ...]] = (
    TABLE_COUNTERPARTY,
    TABLE_LIMITS,
    TABLE_AGREEMENT,
)

#: config key in [treats] for each table.
TABLE_CONFIG_KEY: Final[dict[str, str]] = {
    TABLE_COUNTERPARTY: "ttcpipp",
    TABLE_LIMITS: "cksblmp",
    TABLE_AGREEMENT: "ckovlmp",
}

# ---------------------------------------------------------------------------
# Column names
# ---------------------------------------------------------------------------
COL_CPTY_ACRONYM: Final[str] = "XJCPAC"
COL_CPTY_PARENT: Final[str] = "XJPRAC"

COL_LIMIT_COUNTERPARTY: Final[str] = "CFCPAC"  # PROVISIONAL: placeholder name
COL_LIMIT_TYPE: Final[str] = "CFSLTT"
COL_LIMIT_AMOUNT: Final[str] = "CFSLMT"
COL_OCCUPIED_PREFIX: Final[str] = "CFS00"  # PROVISIONAL: 01 is the first tenor bucket
COL_BUCKET_LIMIT_PREFIX: Final[str] = "CFSL00"  # PROVISIONAL: per-bucket limits may not exist

COL_AGREEMENT_COUNTERPARTY: Final[str] = "CICPAC"  # PROVISIONAL: key column not confirmed
COL_AGREEMENT_TEXT: Final[str] = "CIRFMG"

COL_FFR_TIME_PERIOD: Final[str] = "Time Period"
#: PROVISIONAL: if CKBLOTP carries product / currency-class columns they are expected
#: to be named like this; used only when the columns are actually present.
COL_FFR_PRODUCT: Final[str] = "CBPROD"  # PROVISIONAL
COL_FFR_CLASS: Final[str] = "CBCLAS"  # PROVISIONAL

#: Pattern a quarterly snapshot column must match, e.g. "2025Q2".
QUARTER_COLUMN_PATTERN: Final[str] = r"^20\d\dQ[1-4]$"

# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------
PRODUCT_FX: Final[str] = "FX"
PRODUCT_GOLD: Final[str] = "Gold"
PRODUCT_IRS: Final[str] = "IRS"
PRODUCT_EQUITY_SWAP: Final[str] = "Equity swaps"

PRODUCTS: Final[tuple[str, ...]] = (
    PRODUCT_FX,
    PRODUCT_GOLD,
    PRODUCT_IRS,
    PRODUCT_EQUITY_SWAP,
)

PRODUCT_ALIASES: Final[dict[str, str]] = {
    "FX": PRODUCT_FX,
    "GOLD": PRODUCT_GOLD,
    "XAU": PRODUCT_GOLD,
    "IRS": PRODUCT_IRS,
    "EQUITY SWAPS": PRODUCT_EQUITY_SWAP,
    "EQUITY SWAP": PRODUCT_EQUITY_SWAP,
    "EQ_SWAP": PRODUCT_EQUITY_SWAP,
    "EQSWAP": PRODUCT_EQUITY_SWAP,
}

#: CFSLTT limit-type code per product.
#: PROVISIONAL: only FX01 is confirmed; the other three are placeholders.
LIMIT_TYPE_BY_PRODUCT: Final[dict[str, str]] = {
    PRODUCT_FX: "FX01",
    PRODUCT_GOLD: "GD01",  # PROVISIONAL
    PRODUCT_IRS: "IR01",  # PROVISIONAL
    PRODUCT_EQUITY_SWAP: "EQ01",  # PROVISIONAL
}

DIRECTIONS: Final[tuple[str, ...]] = ("buy", "sell")

# ---------------------------------------------------------------------------
# Tenors - the FFR "Time Period" grid, 89 values
# ---------------------------------------------------------------------------
TENOR_GRID: Final[tuple[str, ...]] = tuple(
    ["Spot", "1 week", "2 weeks", "3 weeks"]
    + [f"{n} months" for n in range(1, 61)]
    + [f"{n} years" for n in range(6, 31)]
)

TENOR_ALIASES: Final[dict[str, str]] = {
    "SPOT": "Spot",
    "1W": "1 week",
    "2W": "2 weeks",
    "3W": "3 weeks",
    "1M": "1 months",
    "3M": "3 months",
    "6M": "6 months",
    "1Y": "12 months",
    "2Y": "24 months",
    "5Y": "60 months",
    "10Y": "10 years",
    "30Y": "30 years",
}

#: PROVISIONAL bucket names and boundaries (see bucket_for in logic/tenor.py).
BUCKETS: Final[tuple[str, ...]] = ("Spot-1M", "1M-3M", "3M-6M", "6M-1Y", "1Y+")

#: PROVISIONAL: CFS0xx / CFSL0xx index per bucket; 01 is assumed to be the first bucket.
BUCKET_INDEX: Final[dict[str, int]] = {name: index for index, name in enumerate(BUCKETS, start=1)}

# ---------------------------------------------------------------------------
# FX currency classes
# ---------------------------------------------------------------------------
CURRENCY_CLASS_LOW: Final[str] = "Low"
CURRENCY_CLASS_NORMAL: Final[str] = "Normal"
CURRENCY_CLASS_MEDIUM: Final[str] = "Medium"
CURRENCY_CLASS_HIGH: Final[str] = "High"

CURRENCY_CLASSES: Final[tuple[str, ...]] = (
    CURRENCY_CLASS_LOW,
    CURRENCY_CLASS_NORMAL,
    CURRENCY_CLASS_MEDIUM,
    CURRENCY_CLASS_HIGH,
)

#: PROVISIONAL sample lists. The official currency -> class lists are still to be
#: supplied by the owning team; edit this one mapping when they are.
CURRENCY_CLASS_BY_CURRENCY: Final[dict[str, str]] = {
    "HKD": CURRENCY_CLASS_LOW,
    "EUR": CURRENCY_CLASS_LOW,
    "JPY": CURRENCY_CLASS_LOW,
    "GBP": CURRENCY_CLASS_LOW,
    "CHF": CURRENCY_CLASS_LOW,
    "CAD": CURRENCY_CLASS_NORMAL,
    "AUD": CURRENCY_CLASS_NORMAL,
    "NZD": CURRENCY_CLASS_NORMAL,
    "SGD": CURRENCY_CLASS_NORMAL,
    "CNH": CURRENCY_CLASS_NORMAL,
    "SEK": CURRENCY_CLASS_MEDIUM,
    "NOK": CURRENCY_CLASS_MEDIUM,
    "KRW": CURRENCY_CLASS_MEDIUM,
    "THB": CURRENCY_CLASS_MEDIUM,
    "TWD": CURRENCY_CLASS_MEDIUM,
    "ZAR": CURRENCY_CLASS_HIGH,
    "TRY": CURRENCY_CLASS_HIGH,
    "BRL": CURRENCY_CLASS_HIGH,
    "MXN": CURRENCY_CLASS_HIGH,
    "IDR": CURRENCY_CLASS_HIGH,
}

#: PROVISIONAL: an unlisted currency is treated as the most volatile class, which is
#: the conservative choice (higher weight -> more limit consumed).
DEFAULT_CURRENCY_CLASS: Final[str] = CURRENCY_CLASS_HIGH

#: Common pairs offered by the UI. Not exhaustive; free text is accepted.
FX_PAIRS: Final[tuple[str, ...]] = (
    "USDHKD",
    "EURUSD",
    "USDJPY",
    "GBPUSD",
    "USDCHF",
    "AUDUSD",
    "USDSGD",
    "USDCNH",
    "USDKRW",
    "USDZAR",
    "USDTRY",
    "EURHKD",
)

#: Currency shown for the non-FX products (Gold is quoted against USD).
NON_FX_CURRENCY: Final[dict[str, str]] = {
    PRODUCT_GOLD: "XAU",
    PRODUCT_IRS: "USD",
    PRODUCT_EQUITY_SWAP: "USD",
}

# ---------------------------------------------------------------------------
# FFR selection: which rows hold the weights
# ---------------------------------------------------------------------------
#: Mock/excel selection per FX currency class.
FFR_FX_SELECTION: Final[dict[str, str]] = {
    CURRENCY_CLASS_LOW: "FFR_FX_LOW",
    CURRENCY_CLASS_NORMAL: "FFR_FX_NORMAL",
    CURRENCY_CLASS_MEDIUM: "FFR_FX_MEDIUM",
    CURRENCY_CLASS_HIGH: "FFR_FX_HIGH",
}

#: Mock/excel selection for the non-FX products.
FFR_PRODUCT_SELECTION: Final[dict[str, str]] = {
    PRODUCT_GOLD: "FFR_GOLD",
    PRODUCT_IRS: "FFR_IRS",
    PRODUCT_EQUITY_SWAP: "FFR_EQ_SWAP",
}

#: Every mock table file that ships with the repository.
MOCK_TABLES: Final[tuple[str, ...]] = (
    TABLE_COUNTERPARTY,
    TABLE_LIMITS,
    TABLE_AGREEMENT,
    *FFR_FX_SELECTION.values(),
    *FFR_PRODUCT_SELECTION.values(),
)

# ---------------------------------------------------------------------------
# Source modes
# ---------------------------------------------------------------------------
SOURCE_MOCK: Final[str] = "mock"
SOURCE_API: Final[str] = "api"
SOURCE_CACHE: Final[str] = "cache"
SOURCE_EXCEL: Final[str] = "excel"

TABLE_SOURCES: Final[tuple[str, ...]] = (SOURCE_MOCK, SOURCE_API, SOURCE_CACHE)
FFR_SOURCES: Final[tuple[str, ...]] = (SOURCE_API, SOURCE_MOCK, SOURCE_EXCEL)

# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------
DECISION_YES: Final[str] = "Y"
DECISION_NO: Final[str] = "N"
DECISION_ERROR: Final[str] = "ERROR"

HOLD_ACTIVE: Final[str] = "active"
HOLD_RELEASED: Final[str] = "released"
HOLD_EXPIRED: Final[str] = "expired"

MAX_CHAIN_DEPTH: Final[int] = 10
