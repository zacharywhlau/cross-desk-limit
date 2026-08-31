"""M1 - standalone counterparty limit check (single user, very early prototype).

ONE file. Imports nothing from src/. Standard library only, plus pandas on the real
path (the company connector returns a pandas DataFrame).

It answers one question: for one counterparty and one proposed deal, what does the
limit data say? Deliberately NOT included: holds, other traders' history, shared
database, UI, threading, packages, plugins.

Usage on the corporate network (TTCPIPP/CKSBLMP/CKOVLMP live, FFR from mock files):

    python prototype\\check_limit.py --cpty ABCDEFG --product FX --tenor "1 months" ^
        --pair USDHKD --notional 500000

Add --mock to run everything from the mock CSVs (this is how it is developed and
tested off the corporate network).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import traceback

# ===========================================================================
# 1. CONFIG - everything environment specific lives in this block.
#    Fill these in on the operator's PC. Do not commit real values.
# ===========================================================================

URL = "PASTE_ENDPOINT_URL_HERE"  # internal endpoint; never printed, never committed
LIBRARY = "PASTE_LIBRARY_NAME_HERE"  # schema/library that holds the tables below

TABLE_COUNTERPARTY = "TTCPIPP"  # counterparty master
TABLE_LIMITS = "CKSBLMP"  # limits and occupied amounts
TABLE_AGREEMENT = "CKOVLMP"  # legal/ISDA agreement text
TABLE_FFR = "CKBLOTP"  # FFR weighting table (real path not used by M1)

# Folder holding the mock CSV exports, including the FFR grids.
MOCK_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "mock_treats")

# Which quarterly snapshot column of the FFR grid to read.
FFR_WEIGHT_COLUMN = "2025Q2"

# Per-table mode for the operator's first run: "real" or "mock".
MODES = {
    TABLE_COUNTERPARTY: "real",
    TABLE_LIMITS: "real",
    TABLE_AGREEMENT: "real",
    "FFR": "mock",
}

# Default deal inputs (overridable on the command line).
DEFAULT_CPTY = "ABCDEFG"
DEFAULT_PRODUCT = "FX"
DEFAULT_TENOR = "1 months"
DEFAULT_PAIR = "USDHKD"
DEFAULT_DIRECTION = "buy"  # collected and stored, NOT used in the formula
DEFAULT_NOTIONAL = 500000.0

REPORT_PATH = "prototype_report.txt"

# --- field names ------------------------------------------------------------
# PROVISIONAL: names marked below are not yet confirmed by the owning team.
COL_CPTY_ACRONYM = "XJCPAC"
COL_CPTY_PARENT = "XJPRAC"
COL_LIMIT_CPTY = "CFCPTY"
COL_LIMIT_TYPE = "CFSLMT"  # limit type code, e.g. "FX 01"
COL_LIMIT_AMOUNT = "CFSLTT"  # total (deal level) approved limit
COL_AGREEMENT_CPTY = "CICPTY"
COL_AGREEMENT_TEXT = "CIRFMG"

# PROVISIONAL: only "FX 01" is confirmed.
LIMIT_TYPE_BY_PRODUCT = {
    "FX": "FX 01", "Gold": "GD 01", "IRS": "IR 01", "Equity swaps": "EQ 01",
}

# PROVISIONAL sample lists; the official mapping is still to be supplied.
CURRENCY_CLASS = {
    "HKD": "Low", "EUR": "Low", "JPY": "Low", "GBP": "Low", "CHF": "Low",
    "CAD": "Normal", "AUD": "Normal", "NZD": "Normal", "SGD": "Normal", "CNH": "Normal",
    "SEK": "Medium", "NOK": "Medium", "KRW": "Medium", "THB": "Medium", "TWD": "Medium",
    "ZAR": "High", "TRY": "High", "BRL": "High", "MXN": "High", "IDR": "High",
}
DEFAULT_CURRENCY_CLASS = "High"  # PROVISIONAL: unknown currency treated as most volatile

FFR_MOCK_FILE = {
    ("FX", "Low"): "FFR_FX_LOW",
    ("FX", "Normal"): "FFR_FX_NORMAL",
    ("FX", "Medium"): "FFR_FX_MEDIUM",
    ("FX", "High"): "FFR_FX_HIGH",
    ("Gold", None): "FFR_GOLD",
    ("IRS", None): "FFR_IRS",
    ("Equity swaps", None): "FFR_EQ_SWAP",
}

# The 14 time periods of the limit system, shortest first. Period n is held in
# CFSO{n:02d} (cash risk already outstanding) and CFSL{n:02d} (approved limit).
BUCKETS = [
    "CALL", "TDY", "TOM", "SPT", "SPT-1M", "1M-3M", "3M-6M", "6M-1Y",
    "1Y-3Y", "3Y-5Y", "5Y-7Y", "7Y-10Y", "10Y-15Y", "15Y+",
]

# PROVISIONAL upper bound in months per period. CALL, TDY and TOM have no bound: no
# tenor on the FFR grid maps onto them, so a deal never lands there.
BUCKET_UPPER_BOUND_MONTHS = [
    ("SPT", 0.0), ("SPT-1M", 1.0), ("1M-3M", 3.0), ("3M-6M", 6.0), ("6M-1Y", 12.0),
    ("1Y-3Y", 36.0), ("3Y-5Y", 60.0), ("5Y-7Y", 84.0), ("7Y-10Y", 120.0),
    ("10Y-15Y", 180.0),
]
LONGEST_BUCKET = "15Y+"


# ===========================================================================
# 2. THE PASTE POINT
# ===========================================================================

def query_to_dataframe(url, payload):
    """PASTE THE COMPANY IMPLEMENTATION HERE."""
    raise NotImplementedError("Paste the company connector, then re-run.")


# ===========================================================================
# 3. SMALL HELPERS - each prints what it does
# ===========================================================================

TRACE = []


def say(line=""):
    """Print a line and keep it for prototype_report.txt."""
    print(line)
    TRACE.append(line)


def money(value):
    return "{:,.0f}".format(value)


def build_sql(table, where=None):
    """SELECT * FROM <LIBRARY>.<TABLE> [WHERE ...] - library.table form."""
    sql = "SELECT * FROM {library}.{table}".format(library=LIBRARY, table=table)
    if where:
        sql += " WHERE " + where
    return sql


def mask(text):
    """Keep the library name out of the printed trace and the report file."""
    return str(text).replace(LIBRARY, "<LIBRARY>")


def build_payload(table, sql):
    return {
        "startRow": None,
        "endRow": None,
        "libandfile": [{"library": LIBRARY, "file": table}],
        "fullSQL": sql,
    }


def read_mock_csv(name):
    path = os.path.join(MOCK_DIR, name + ".csv")
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def fetch(table, where=None, mode=None):
    """Return list[dict] for one table, honouring the per-table mode."""
    mode = mode or MODES.get(table, "mock")
    sql = build_sql(table, where)
    if mode == "mock":
        rows = read_mock_csv(table)
        if where:
            rows = filter_rows(rows, where)
        return rows, mode, sql
    frame = query_to_dataframe(url=URL, payload=build_payload(table, sql))
    rows = [
        {str(key).strip(): value for key, value in record.items()}
        for record in frame.to_dict(orient="records")
    ]
    return rows, mode, sql


def equals_clause(column, value):
    """COLUMN='VALUE' - build the predicate, never hand-write it."""
    return "{0}='{1}'".format(column, str(value).strip().upper())


def in_clause(column, values):
    """COLUMN IN ('A','B') - one query for the whole counterparty chain."""
    joined = ",".join("'{0}'".format(str(value).strip().upper()) for value in values)
    return "{0} IN ({1})".format(column, joined)


def filter_rows(rows, where):
    """Apply the `COL='VALUE'` / `COL IN (...)` predicates of this script to mock rows."""
    match = re.match(r"^\s*(\w+)\s*=\s*'([^']*)'\s*$", where)
    if match:
        column, wanted = match.group(1), match.group(2).strip().upper()
        return [row for row in rows if code_key(row.get(column)) == code_key(wanted)]
    match = re.match(r"^\s*(\w+)\s+IN\s*\((.*)\)\s*$", where, re.IGNORECASE)
    if match:
        column = match.group(1)
        wanted = {code_key(item.strip().strip("'")) for item in match.group(2).split(",")}
        return [row for row in rows if code_key(row.get(column)) in wanted]
    return rows


def parse_percent(value):
    """'1%' -> 0.01, '2.5%' -> 0.025, 0.01 -> 0.01, 1 -> 0.01."""
    if value is None:
        raise ValueError("empty FFR weight")
    text = str(value).strip()
    if not text:
        raise ValueError("empty FFR weight")
    if text.endswith("%"):
        return float(text[:-1].strip()) / 100.0
    number = float(text)
    return number / 100.0 if abs(number) >= 1.0 else number


def to_amount(value):
    text = str(value if value is not None else "").strip().replace(",", "")
    if not text:
        return 0.0
    return float(text)


TENOR_GRID = (
    ["Spot", "1 week", "2 weeks", "3 weeks"]
    + ["{0} months".format(n) for n in range(1, 61)]
    + ["{0} years".format(n) for n in range(6, 31)]
)

TENOR_ALIASES = {
    "SPOT": "Spot", "1W": "1 week", "2W": "2 weeks", "3W": "3 weeks",
    "1M": "1 months", "3M": "3 months", "6M": "6 months", "1Y": "12 months",
    "2Y": "24 months", "5Y": "60 months", "10Y": "10 years", "30Y": "30 years",
}


def normalise_tenor(raw):
    """Map a typed tenor onto one of the 89 grid labels."""
    text = " ".join(str(raw or "").split())
    if not text:
        raise ValueError("tenor is required; valid examples: Spot, 1 week, 1 months, 10 years")
    upper = text.upper()
    if upper in TENOR_ALIASES:
        return TENOR_ALIASES[upper]
    for label in TENOR_GRID:
        if label.upper() == upper:
            return label
    match = re.match(r"^(\d+)\s*(W|WEEK|WEEKS|M|MONTH|MONTHS|Y|YEAR|YEARS)$", upper)
    if match:
        count, unit = int(match.group(1)), match.group(2)[0]
        candidate = {
            "W": "{0} week".format(count) if count == 1 else "{0} weeks".format(count),
            "M": "{0} months".format(count),
            "Y": "{0} years".format(count) if count >= 6 else "{0} months".format(count * 12),
        }[unit]
        if candidate in TENOR_GRID:
            return candidate
    raise ValueError(
        "unknown tenor {0!r}; valid examples: Spot, 1 week, 3 weeks, 1 months, "
        "18 months, 60 months, 6 years, 30 years (aliases: 1W, 1M, 6M, 1Y, 10Y, 30Y)".format(text)
    )


def months_of(tenor):
    """Approximate maturity in months for a grid label."""
    label = normalise_tenor(tenor)
    if label == "Spot":
        return 0.0
    count, unit = label.split(" ", 1)
    count = int(count)
    if unit.startswith("week"):
        return count * 7.0 / 30.0
    if unit.startswith("month"):
        return float(count)
    return count * 12.0


def bucket_for(tenor):
    """PROVISIONAL period map - a deal falls in the first period that covers it."""
    months = months_of(tenor)
    for name, upper in BUCKET_UPPER_BOUND_MONTHS:
        if months <= upper:
            return name
    return LONGEST_BUCKET


def code_key(value):
    """Comparison key for a code column: no whitespace, upper case ("FX 01" == "FX01")."""
    return "".join(str(value if value is not None else "").split()).upper()


def occupied_column(slot):
    """Cash risk already outstanding in one period: CFSO01 .. CFSO14."""
    return "CFSO{0:02d}".format(slot)


def slot_limit_column(slot):
    """Approved limit for one period: CFSL01 .. CFSL14."""
    return "CFSL{0:02d}".format(slot)


def classifying_currency(pair_or_currency):
    """PROVISIONAL: 3 letters is the currency; for a pair take the non-USD side."""
    text = str(pair_or_currency or "").strip().upper()
    if len(text) == 3:
        return text
    if len(text) == 6:
        base, quote = text[:3], text[3:]
        if base == "USD":
            return quote
        if quote == "USD":
            return base
        return quote
    raise ValueError("pair_or_currency must be 3 or 6 letters, e.g. HKD or USDHKD")


def ffr_weight_from_mock(product, pair_or_currency, tenor):
    """Read one weight from the mock FFR grid: row = Time Period, column = quarter."""
    if product == "FX":
        currency = classifying_currency(pair_or_currency)
        ccy_class = CURRENCY_CLASS.get(currency, DEFAULT_CURRENCY_CLASS)
        file_name = FFR_MOCK_FILE[("FX", ccy_class)]
        class_label = "{0}({1})".format(ccy_class, currency)
    else:
        file_name = FFR_MOCK_FILE[(product, None)]
        class_label = "n/a"
    rows = read_mock_csv(file_name)
    if not rows:
        raise ValueError("FFR mock file {0}.csv is empty".format(file_name))
    columns = list(rows[0].keys())
    column = FFR_WEIGHT_COLUMN
    if column not in columns:
        quarters = sorted(c for c in columns if re.match(r"^20\d\dQ[1-4]$", str(c)))
        if not quarters:
            raise ValueError("no quarter column found in {0}.csv".format(file_name))
        column = quarters[-1]
        say("      WARNING: column {0} missing; using {1} instead".format(FFR_WEIGHT_COLUMN, column))
    label = normalise_tenor(tenor)
    for row in rows:
        if str(row.get("Time Period", "")).strip() == label:
            return parse_percent(row[column]), file_name, class_label, column, label
    raise ValueError("time period {0!r} not found in {1}.csv".format(label, file_name))


def usage_for(product, notional_usd, ffr_weight):
    """Default shared formula - may diverge per product."""
    if product not in LIMIT_TYPE_BY_PRODUCT:
        raise ValueError("unknown product {0!r}; valid: {1}".format(
            product, ", ".join(sorted(LIMIT_TYPE_BY_PRODUCT))))
    return notional_usd * (1.0 + ffr_weight)


def limit_row_for(rows, counterparty, product):
    code = code_key(LIMIT_TYPE_BY_PRODUCT[product])
    for row in rows:
        same_cpty = code_key(row.get(COL_LIMIT_CPTY)) == code_key(counterparty)
        same_type = code_key(row.get(COL_LIMIT_TYPE)) == code
        if same_cpty and same_type:
            return row
    return None


def surface_from_row(row):
    """Read one CKSBLMP row into the limit ladder.

    The limit system is cumulative: a deal booked in one period also consumes every
    shorter period, so the availability of period i is

        reverse_cum[i] = sum(cash risk of period j for j >= i)
        available[i]   = max(0, min(limit[i] - reverse_cum[i], available[i - 1]))

    which is why a fully used short period blocks every longer one as well.
    """
    total_limit = to_amount(row.get(COL_LIMIT_AMOUNT))
    occupied = {}
    limits = {}
    for slot, bucket in enumerate(BUCKETS, start=1):
        occupied[bucket] = to_amount(row.get(occupied_column(slot)))
        raw = row.get(slot_limit_column(slot))
        limits[bucket] = to_amount(raw) if str(raw or "").strip() else total_limit
    reverse_cum = {}
    total = 0.0
    for bucket in reversed(BUCKETS):
        total += occupied[bucket]
        reverse_cum[bucket] = total
    available = {}
    running = None
    for bucket in BUCKETS:
        headroom = limits[bucket] - reverse_cum[bucket]
        running = headroom if running is None else min(headroom, running)
        available[bucket] = max(0.0, running)
    return total_limit, sum(occupied.values()), occupied, limits, reverse_cum, available


def validate_counterparty(raw):
    text = str(raw or "").strip().upper()
    if not text.isalnum() or len(text) not in (4, 7):
        raise ValueError(
            "counterparty must be uppercase alphanumeric and exactly 4 or 7 characters "
            "(got {0!r}, length {1})".format(text, len(text))
        )
    return text


# ===========================================================================
# 4. MAIN - the numbered trace
# ===========================================================================

def main(argv=None):
    parser = argparse.ArgumentParser(description="Standalone counterparty limit check (M1).")
    parser.add_argument("--cpty", default=DEFAULT_CPTY)
    parser.add_argument("--product", default=DEFAULT_PRODUCT,
                        choices=sorted(LIMIT_TYPE_BY_PRODUCT))
    parser.add_argument("--tenor", default=DEFAULT_TENOR)
    parser.add_argument("--pair", default=DEFAULT_PAIR)
    parser.add_argument("--direction", default=DEFAULT_DIRECTION, choices=["buy", "sell"])
    parser.add_argument("--notional", type=float, default=DEFAULT_NOTIONAL)
    parser.add_argument("--mock", action="store_true", help="run every source from the mock CSVs")
    parser.add_argument("--json", action="store_true",
                        help="also print one machine-readable line (used by the parity test)")
    args = parser.parse_args(argv)

    if args.mock:
        for key in MODES:
            MODES[key] = "mock"

    step = 0
    table = "-"
    mode = "-"
    sql = "-"
    try:
        # --- [1/6] input validation, before any remote call --------------------
        step = 1
        cpty = validate_counterparty(args.cpty)
        if args.notional <= 0:
            raise ValueError("notional_usd must be a positive number")
        tenor = normalise_tenor(args.tenor)
        bucket = bucket_for(tenor)
        say("[1/6] counterparty {0} (length {1}) OK".format(cpty, len(cpty)))
        say("      product={0} tenor={1} pair={2} direction={3} notional={4}".format(
            args.product, tenor, args.pair, args.direction, money(args.notional)))

        # --- [2/6] counterparty master and the parent chain -------------------
        step = 2
        table = TABLE_COUNTERPARTY
        chain = [cpty]
        parent = None
        seen = {cpty}
        current = cpty
        rows_first = 0
        for depth in range(10):
            where = equals_clause(COL_CPTY_ACRONYM, current)
            rows, mode, sql = fetch(TABLE_COUNTERPARTY, where=where)
            if depth == 0:
                rows_first = len(rows)
                say("[2/6] {0}  source={1}  SQL: {2}".format(
                    TABLE_COUNTERPARTY, mode, mask(sql)))
                if not rows:
                    raise LookupError("counterparty {0} not found in {1}".format(
                        cpty, TABLE_COUNTERPARTY))
            if not rows:
                break
            next_parent = str(rows[0].get(COL_CPTY_PARENT, "") or "").strip().upper()
            if depth == 0:
                parent = next_parent or None
            if not next_parent or next_parent in seen:
                break
            chain.append(next_parent)
            seen.add(next_parent)
            current = next_parent
        say("      rows={0}  parent={1}  chain={2}".format(
            rows_first, parent or "(none)", " > ".join(chain)))

        # --- [3/6] limits and occupied amounts --------------------------------
        # The WHERE matters: the endpoint caps a result set, so never read the whole
        # table. One IN (...) covers the submitted counterparty and its parents.
        step = 3
        table = TABLE_LIMITS
        where = in_clause(COL_LIMIT_CPTY, chain)
        limit_rows, mode, sql = fetch(TABLE_LIMITS, where=where)
        say("[3/6] {0}  source={1}  SQL: {2}".format(TABLE_LIMITS, mode, mask(sql)))
        row = limit_row_for(limit_rows, cpty, args.product)
        if row is None:
            raise LookupError("no {0} row for {1} with limit type {2}".format(
                TABLE_LIMITS, cpty, LIMIT_TYPE_BY_PRODUCT[args.product]))
        deal_limit, utilisation, occupied, bucket_limits, reverse_cum, available = (
            surface_from_row(row))
        say("      rows={0}  limit_type={1}  total limit={2}  total cash risk={3}".format(
            len(limit_rows), LIMIT_TYPE_BY_PRODUCT[args.product],
            money(deal_limit), money(utilisation)))

        # --- [4/6] FFR weight (mock in M1) ------------------------------------
        step = 4
        table = TABLE_FFR
        if MODES.get("FFR", "mock") != "mock":
            raise NotImplementedError(
                "this prototype reads the FFR weight from the mock grid only; "
                "the api path lives in the package (see docs/PLAN.md §7)")
        weight, ffr_file, class_label, column, period = ffr_weight_from_mock(
            args.product, args.pair, tenor)
        say("[4/6] FFR      source=mock  file={0}.csv  class={1}  period={2}".format(
            ffr_file, class_label, period))
        say("      column={0}  weight={1:g}%".format(column, round(weight * 100, 4)))

        # --- [5/6] usage -------------------------------------------------------
        step = 5
        table = "-"
        usage = usage_for(args.product, args.notional, weight)
        say("[5/6] usage = {0} * (1 + {1:g}) = {2}   bucket={3}".format(
            money(args.notional), round(weight, 6), money(usage), bucket))

        # --- [6/6] decision ----------------------------------------------------
        # The ladder already carries the "shorter periods too" rule, so the deal only
        # has to fit the availability of its own period.
        step = 6
        bucket_available = available[bucket]
        deal_available = deal_limit - utilisation
        decision = "Y" if usage <= bucket_available else "N"
        say("[6/6] DECISION: {0}   period {1} available before={2} after={3}".format(
            decision, bucket, money(bucket_available), money(bucket_available - usage)))
        say("                    period limit={0}  cash risk from {1} onwards={2}".format(
            money(bucket_limits[bucket]), bucket, money(reverse_cum[bucket])))
        say("                    total limit={0}  total available={1}".format(
            money(deal_limit), money(deal_available)))
        for name in BUCKETS:
            marker = " <- this deal" if name == bucket else ""
            say("      {0:<9} limit={1:>15}  cash risk>={2:>15}  available={3:>15}{4}".format(
                name, money(bucket_limits[name]), money(reverse_cum[name]),
                money(available[name]), marker))
        if decision == "N":
            say("      REJECTED: usage {0} exceeds period {1} availability {2}".format(
                money(usage), bucket, money(bucket_available)))

        # --- reference only: parent figures and agreement text -----------------
        table = TABLE_AGREEMENT
        where = in_clause(COL_AGREEMENT_CPTY, chain)
        agreement_rows, agr_mode, sql = fetch(TABLE_AGREEMENT, where=where)
        say("      {0}  source={1}  SQL: {2}".format(TABLE_AGREEMENT, agr_mode, mask(sql)))
        for node in chain[1:]:
            node_row = limit_row_for(limit_rows, node, args.product)
            if node_row is None:
                say("      parent {0} (reference only): no {1} row".format(node, TABLE_LIMITS))
                continue
            node_limit, node_util = surface_from_row(node_row)[:2]
            say("      parent {0} (reference only): limit={1}  cash risk={2}".format(
                node, money(node_limit), money(node_util)))
        for node in chain:
            for agreement in agreement_rows:
                if code_key(agreement.get(COL_AGREEMENT_CPTY)) == code_key(node):
                    say("      agreement {0} (source={1}): {2}".format(
                        node, agr_mode, agreement.get(COL_AGREEMENT_TEXT, "")))

        write_report()
        if args.json:
            print(json.dumps({
                "decision": decision,
                "usage": round(usage, 6),
                "bucket": bucket,
                "ffr_weight": round(weight, 10),
                "available_before": bucket_available,
                "bucket_available_before": bucket_available,
                "deal_available_before": deal_available,
                "reverse_cumulative": reverse_cum[bucket],
            }, sort_keys=True))
        return 0
    except Exception as error:  # noqa: BLE001 - the trace is the product here
        say("")
        say("FAILED at step [{0}/6]".format(step))
        say("  table   : {0}".format(table))
        say("  mode    : {0}".format(mode))
        say("  SQL     : {0}".format(mask(sql)))
        say("  error   : {0}: {1}".format(type(error).__name__, error))
        say("")
        say(traceback.format_exc().rstrip())
        write_report()
        return 1


def write_report():
    try:
        with open(REPORT_PATH, "w", encoding="utf-8") as handle:
            handle.write("\n".join(TRACE) + "\n")
        print("(trace written to {0})".format(REPORT_PATH))
    except OSError as error:
        print("(could not write {0}: {1})".format(REPORT_PATH, error))


if __name__ == "__main__":
    sys.exit(main())
