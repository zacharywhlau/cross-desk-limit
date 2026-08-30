"""FFR weight resolution. The target table is CKBLOTP (name from config).

The grid is the same shape whatever the source: each ROW is a maturity in a column
called "Time Period", each further COLUMN is one published quarterly snapshot
("2025Q1", "2025Q2", ...). The cell where they meet is the weight in force. Which
quarter column to read is config ffr.weight_column, so a new quarter is a config
change rather than a code change.
"""

from __future__ import annotations

import re
from pathlib import Path

from .. import constants
from ..config import Settings, load_settings
from ..logging_setup import get_logger
from ..models import FfrLookup
from ..treats import source as source_module
from ..treats.tabular import Record, TabularError, columns_of, read_xlsx
from . import numbers
from .calculators import normalise_product
from .tenor import normalise_tenor

_logger = get_logger("logic.ffr")

_QUARTER = re.compile(constants.QUARTER_COLUMN_PATTERN)


class FfrError(RuntimeError):
    """The FFR weight could not be resolved."""


def classifying_currency(pair_or_currency: str) -> str:
    """PROVISIONAL: 3 letters is the currency itself; for a 6-letter pair take the
    non-USD side (USDHKD -> HKD, EURUSD -> EUR); if neither side is USD take the
    quote currency."""
    text = str(pair_or_currency or "").strip().upper().replace("/", "")
    if len(text) == 3 and text.isalpha():
        return text
    if len(text) == 6 and text.isalpha():
        base, quote = text[:3], text[3:]
        if base == "USD":
            return quote
        if quote == "USD":
            return base
        return quote
    raise FfrError(
        f"pair_or_currency {pair_or_currency!r} must be 3 letters (HKD) or 6 letters (USDHKD)"
    )


def currency_class(currency: str) -> str:
    """PROVISIONAL sample lists live in constants.py; unknown -> most volatile class."""
    return constants.CURRENCY_CLASS_BY_CURRENCY.get(
        str(currency).strip().upper(), constants.DEFAULT_CURRENCY_CLASS
    )


def resolve_ffr_selection(
    product: str,
    ccy_class: str | None,
    settings: Settings | None = None,
) -> tuple[str, str]:
    """Which rows hold the weights: (table_or_file, filter_description).

    In mock and excel mode this is a per-class file / sheet name. In api mode it is
    the configured FFR table plus a row-selection rule.
    """
    settings = settings or load_settings()
    canonical = normalise_product(product)
    if settings.ffr.source == constants.SOURCE_API:
        # PROVISIONAL: the CKBLOTP column layout and the rule that picks the rows for
        # a product / currency class are NOT confirmed.
        # TODO(operator): confirm with the owning team (a) which column identifies the
        # product, (b) which column identifies the FX volatility class, and (c) whether
        # one row set covers all products. Until then the filter below is applied only
        # when those columns actually exist in the returned rows, and the assumption is
        # logged on every lookup.
        if canonical == constants.PRODUCT_FX:
            description = (
                f"{constants.COL_FFR_PRODUCT}='{canonical}' and "
                f"{constants.COL_FFR_CLASS}='{ccy_class}' (PROVISIONAL, applied only "
                "if those columns are present)"
            )
        else:
            description = (
                f"{constants.COL_FFR_PRODUCT}='{canonical}' (PROVISIONAL, applied only "
                "if that column is present)"
            )
        return settings.ffr.table, description

    if canonical == constants.PRODUCT_FX:
        if ccy_class not in constants.FFR_FX_SELECTION:
            raise FfrError(
                f"unknown FX currency class {ccy_class!r}; valid: "
                f"{', '.join(constants.CURRENCY_CLASSES)}"
            )
        name = constants.FFR_FX_SELECTION[ccy_class]
        return name, f"FX currency class {ccy_class}"
    name = constants.FFR_PRODUCT_SELECTION[canonical]
    return name, f"product {canonical}"


def select_weight_column(columns: list[str], configured: str, *, where: str) -> str:
    """The configured quarter column, or the highest-sorting 20\\d\\dQ[1-4] fallback.

    The substituted column is logged, so a silently stale weight is impossible.
    """
    if configured in columns:
        return configured
    quarters = sorted(column for column in columns if _QUARTER.match(str(column)))
    if not quarters:
        raise FfrError(
            f"no quarter column (e.g. 2025Q2) found in {where}; columns are: "
            f"{', '.join(str(column) for column in columns) or '(none)'}"
        )
    chosen = quarters[-1]
    _logger.warning(
        "ffr weight column %s missing in %s; using %s instead", configured, where, chosen
    )
    return chosen


def _rows_for_selection(
    selection: str,
    settings: Settings,
) -> tuple[list[Record], str]:
    """Rows of the FFR grid from the configured source, plus a source label."""
    mode = settings.ffr.source
    if mode == constants.SOURCE_MOCK:
        fetched = source_module.fetch_table(selection, settings, source=constants.SOURCE_MOCK)
        return fetched.rows, f"mock:{fetched.detail}"
    if mode == constants.SOURCE_API:
        fetched = source_module.fetch_table(selection, settings, source=constants.SOURCE_API)
        return fetched.rows, f"api:{fetched.detail}"
    if mode == constants.SOURCE_EXCEL:
        # LAST RESORT: one workbook, one sheet per product/class, refreshed manually
        # each quarter. Documented as the fallback we do not want.
        if not settings.ffr.excel_path:
            raise FfrError("[ffr] excel_path is not set, but ffr.source = excel")
        path = Path(settings.ffr.excel_path)
        try:
            rows = read_xlsx(path, sheet=selection)
        except TabularError as error:
            raise FfrError(str(error)) from error
        return rows, f"excel:{path.name}#{selection}"
    raise FfrError(f"unsupported ffr.source {mode!r}")


def _apply_provisional_api_filter(
    rows: list[Record],
    product: str,
    ccy_class: str | None,
) -> list[Record]:
    """PROVISIONAL: narrow CKBLOTP rows when the product/class columns exist."""
    columns = columns_of(rows)
    filtered = rows
    if constants.COL_FFR_PRODUCT in columns:
        filtered = [
            row for row in filtered
            if str(row.get(constants.COL_FFR_PRODUCT, "") or "").strip().upper()
            == product.upper()
        ]
    if ccy_class and constants.COL_FFR_CLASS in columns:
        filtered = [
            row for row in filtered
            if str(row.get(constants.COL_FFR_CLASS, "") or "").strip().upper()
            == ccy_class.upper()
        ]
    if filtered is rows or not filtered:
        _logger.warning(
            "ffr api rows not narrowed by product/class (PROVISIONAL selection rule "
            "unconfirmed): product=%s class=%s columns=%s",
            product, ccy_class, ",".join(str(column) for column in columns[:8]),
        )
        return rows
    return filtered


def lookup_ffr(
    product: str,
    pair_or_currency: str,
    tenor: str,
    settings: Settings | None = None,
) -> FfrLookup:
    """The one public entry point: resolve the weight for this deal."""
    settings = settings or load_settings()
    canonical = normalise_product(product)
    time_period = normalise_tenor(tenor)

    ccy_class: str | None = None
    if canonical == constants.PRODUCT_FX:
        ccy_class = currency_class(classifying_currency(pair_or_currency))
    selection, filter_description = resolve_ffr_selection(canonical, ccy_class, settings)

    rows, source_label = _rows_for_selection(selection, settings)
    if not rows:
        raise FfrError(f"FFR grid {selection} returned no rows ({source_label})")
    if settings.ffr.source == constants.SOURCE_API:
        rows = _apply_provisional_api_filter(rows, canonical, ccy_class)

    column = select_weight_column(
        columns_of(rows), settings.ffr.weight_column, where=f"{selection} ({source_label})"
    )
    for row in rows:
        label = str(row.get(constants.COL_FFR_TIME_PERIOD, "") or "").strip()
        if label == time_period:
            try:
                weight = numbers.parse_percent(row.get(column))
            except ValueError as error:
                raise FfrError(
                    f"FFR weight for {time_period} in {selection} column {column} "
                    f"is unusable: {error}"
                ) from error
            _logger.info(
                "ffr lookup product=%s selection=%s filter=%s period=%s column=%s weight=%s",
                canonical, selection, filter_description, time_period, column,
                numbers.percent(weight),
            )
            return FfrLookup(
                weight=weight,
                table_name=selection,
                source_label=source_label,
                time_period=time_period,
                weight_column=column,
                currency_class=ccy_class,
                filter_description=filter_description,
            )
    raise FfrError(
        f"time period {time_period!r} not found in FFR grid {selection} "
        f"(column {constants.COL_FFR_TIME_PERIOD}, source {source_label})"
    )
