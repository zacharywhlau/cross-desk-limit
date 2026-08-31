"""Command line: doctor | extract | check | peers | history | release.

Everything the UI can do must also work here, so the operator can test on the
corporate network before opening a window. `ui.report` is imported for presentation
only - no business logic lives in either place.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Sequence

from . import constants
from .config import ConfigError, Settings, load_settings
from .logging_setup import get_logger, log_startup, setup_logging
from .logic import numbers
from .logic.check import ValidationError, run_check, validate_request
from .logic.ffr import lookup_ffr
from .store.db import HoldsStore, StoreError
from .treats import api, cache
from .treats import source as source_module
from .treats import sql as sql_builder
from .treats.tabular import columns_of
from .ui.report import DEFAULT_REPORT_NAME, text_report, write_html_report

_logger = get_logger("cli")

EXIT_OK = 0
EXIT_FAIL = 1

#: How many rows a `doctor` probe or an unfiltered `extract` may ask the endpoint for.
DOCTOR_PROBE_ROWS = 50
EXTRACT_SAMPLE_ROWS = 200


def _print(line: str = "") -> None:
    print(line)


def _counterparty_where(table: str, counterparty: str | None) -> str | None:
    """`COLUMN='CPTY'` for the table, or None when there is nothing to narrow by."""
    column = constants.COUNTERPARTY_COLUMN_BY_TABLE.get(table)
    if not column or not counterparty:
        return None
    return sql_builder.equals_clause(column, counterparty)


def _settings(args: argparse.Namespace) -> Settings:
    settings = load_settings(getattr(args, "config", None))
    log_startup(settings)
    return settings


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------
def command_doctor(args: argparse.Namespace) -> int:
    settings = _settings(args)
    failures = 0

    def check(name: str, ok: bool, detail: str) -> None:
        nonlocal failures
        if not ok:
            failures += 1
        _print(f"[{'PASS' if ok else 'FAIL'}] {name:<34} {detail}")

    def warn(name: str, detail: str) -> None:
        _print(f"[WARN] {name:<34} {detail}")

    _print("cross-desk-limit doctor")
    _print("")
    if settings.config_path is not None:
        check("config file found", True, str(settings.config_path))
    else:
        warn("config file found",
             "no config.ini; running on the built-in defaults (every source mock). "
             "Copy config.example.ini to config.ini before using the real sources.")
    for table, mode in settings.source_summary().items():
        check(f"source {table}", True, mode)
    pasted = api.connector_is_pasted()
    api_tables = [table for table, mode in settings.source_summary().items()
                  if mode == constants.SOURCE_API]
    check(
        "company connector pasted",
        pasted or not api_tables,
        "pasted" if pasted else (
            "NOT pasted - required because "
            f"{', '.join(api_tables)} {'is' if len(api_tables) == 1 else 'are'} set to api. "
            "Replace the body of query_to_dataframe in src/cdl/treats/api.py, including "
            "the raise NotImplementedError line (see the PASTE POINT banner in that file)"
            if api_tables else "not pasted, but no table is set to api"
        ),
    )
    if settings.quoted_values:
        warn("config values unquoted",
             "quotes were stripped from " + ", ".join(settings.quoted_values)
             + " - an ini file takes the value literally, so remove the quotes")
    if api_tables and not settings.treats.probe_counterparty:
        warn("probe counterparty set",
             "[treats] probe_counterparty is empty, so the probes below read a "
             f"{DOCTOR_PROBE_ROWS} row sample instead of one counterparty")
    for table in api_tables:
        if not pasted:
            check(f"query {table}", False, "skipped: connector not pasted")
            continue
        where = _counterparty_where(table, settings.treats.probe_counterparty)
        try:
            # Bounded on purpose: the endpoint caps a result set, so a probe must never
            # ask for a whole table.
            fetched = source_module.fetch_table(
                table, settings, where=where, end_row=DOCTOR_PROBE_ROWS)
            scope = "one counterparty" if where else f"first {DOCTOR_PROBE_ROWS} rows"
            check(f"query {table}", True,
                  f"{fetched.row_count} rows from {fetched.detail} ({scope})")
        except Exception as error:
            check(f"query {table}", False, f"{type(error).__name__}: {error}")
    for table, mode in settings.source_summary().items():
        if mode in (constants.SOURCE_MOCK, constants.SOURCE_CACHE) and table != settings.ffr.table:
            try:
                fetched = source_module.fetch_table(table, settings)
                check(f"read {table}", True, f"{fetched.row_count} rows from {fetched.detail}")
            except Exception as error:
                check(f"read {table}", False, f"{type(error).__name__}: {error}")

    try:
        lookup = lookup_ffr(constants.PRODUCT_FX, "USDHKD", "1 months", settings)
        detail = (
            f"{lookup.table_name} column {lookup.weight_column} "
            f"weight {numbers.percent(lookup.weight)}"
        )
        ok = lookup.weight_column == settings.ffr.weight_column
        check("ffr weight_column present", ok,
              detail if ok else f"{detail} (configured {settings.ffr.weight_column} missing)")
    except Exception as error:
        check("ffr weight_column present", False, f"{type(error).__name__}: {error}")

    store = HoldsStore(settings)
    try:
        store.initialise()
        store.history_today(limit=1)
        check("db_path writable", True,
              f"{store.db_path} (journal_mode={store.journal_mode})")
    except StoreError as error:
        check("db_path writable", False, str(error))

    _print("")
    _print(f"{'FAILED' if failures else 'ALL PASS'} - {failures} problem(s)")
    return EXIT_FAIL if failures else EXIT_OK


# ---------------------------------------------------------------------------
# extract
# ---------------------------------------------------------------------------
def command_extract(args: argparse.Namespace) -> int:
    settings = _settings(args)
    tables = [args.table] if args.table else list(settings.source_summary())
    failures = 0
    for table in tables:
        mode = source_module.effective_source(table, settings)
        _print("")
        _print(f"--- {table} (source={mode})")
        if table == settings.ffr.table and mode != constants.SOURCE_API:
            _print("    the FFR grid is not one table in this mode; "
                   "it is one file per product/class (see docs/PLAN.md §7)")
            continue
        counterparty = args.cpty or settings.treats.probe_counterparty
        where = _counterparty_where(table, counterparty)
        # An api read is bounded unless it is narrowed to one counterparty, because the
        # endpoint caps a result set and a capped read looks like a complete one.
        end_row = args.limit
        if end_row is None and mode == constants.SOURCE_API and where is None:
            end_row = EXTRACT_SAMPLE_ROWS
        _print(f"    SQL: {source_module.statement_for(table, settings, where)}")
        if end_row is not None:
            _print(f"    bounded to the first {end_row} rows")
        try:
            fetched = source_module.fetch_table(
                table, settings, where=where, end_row=end_row)
        except Exception as error:
            failures += 1
            _print(f"    FAILED: {type(error).__name__}: {error}")
            continue
        columns = columns_of(fetched.rows)
        _print(f"    detail : {fetched.detail}")
        _print(f"    rows   : {fetched.row_count}  ({fetched.elapsed_ms:.0f} ms)")
        _print(f"    columns: {', '.join(columns) if columns else '(none)'}")
        for row in fetched.rows[: args.rows]:
            _print("    " + " | ".join(f"{key}={row.get(key, '')}" for key in columns[:8]))
        if args.save_cache:
            try:
                path = cache.save(table, fetched.rows, settings)
                _print(f"    cached : {path}")
                if where is not None or end_row is not None:
                    _print("    NOTE   : this cache file holds a filtered or bounded "
                           "sample, not the whole table")
            except Exception as error:
                failures += 1
                _print(f"    cache FAILED: {type(error).__name__}: {error}")
    _print("")
    return EXIT_FAIL if failures else EXIT_OK


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------
def command_check(args: argparse.Namespace) -> int:
    settings = _settings(args)
    try:
        request = validate_request(
            username=args.user,
            counterparty=args.cpty,
            product=args.product,
            tenor=args.tenor,
            pair_or_currency=args.pair,
            direction=args.direction,
            notional_usd=args.notional,
        )
    except ValidationError as error:
        _print(f"ERROR: {error}")
        return EXIT_FAIL

    store = HoldsStore(settings)
    result = run_check(request, settings, store, create_hold=not args.no_hold)
    if result.is_error:
        try:
            store.record_error(request, result.message,
                               affected_bucket=result.affected_bucket)
        except StoreError as error:
            _logger.warning("could not write the ERROR outcome to history: %s", error)

    try:
        peers = store.peers(request.counterparty, request.product)
        history = store.history_today()
    except StoreError as error:
        _logger.warning("holds/history unavailable: %s", error)
        peers, history = [], []

    _print(text_report(result, peers=peers, history=history))
    report_path = write_html_report(
        result, args.report or DEFAULT_REPORT_NAME, peers=peers, history=history)
    _print(f"report written to {report_path}")
    return EXIT_OK if not result.is_error else EXIT_FAIL


# ---------------------------------------------------------------------------
# peers / history / release
# ---------------------------------------------------------------------------
def command_peers(args: argparse.Namespace) -> int:
    settings = _settings(args)
    store = HoldsStore(settings)
    now = datetime.now()
    try:
        peers = store.peers(args.cpty.strip().upper(), args.product, now)
    except StoreError as error:
        _print(f"ERROR: {error}")
        return EXIT_FAIL
    if not peers:
        _print(f"no active holds on {args.cpty.upper()} {args.product}")
        return EXIT_OK
    _print(f"active holds on {args.cpty.upper()} {args.product}:")
    for hold, minutes in peers:
        _print(
            f"  hold {hold.id:<4} {hold.username:<12} {hold.tenor:<10} "
            f"bucket={hold.affected_bucket:<8} notional={numbers.millions(hold.notional_usd)} "
            f"usage={numbers.millions(hold.usage)} {minutes:.0f} min left"
        )
    return EXIT_OK


def command_history(args: argparse.Namespace) -> int:
    settings = _settings(args)
    store = HoldsStore(settings)
    try:
        records = store.history_today(limit=args.limit)
    except StoreError as error:
        _print(f"ERROR: {error}")
        return EXIT_FAIL
    if not records:
        _print("no checks recorded today")
        return EXIT_OK
    _print("today's checks:")
    for record in records:
        _print(
            f"  {record.created_at:%H:%M:%S} {record.decision:<5} {record.username:<12} "
            f"{record.counterparty:<8} {record.product:<13} {record.tenor:<10} "
            f"usage={numbers.millions(record.usage)}  {record.message[:60]}"
        )
    return EXIT_OK


def command_release(args: argparse.Namespace) -> int:
    settings = _settings(args)
    store = HoldsStore(settings)
    try:
        hold = store.release(args.hold_id, args.user)
    except StoreError as error:
        _print(f"ERROR: {error}")
        return EXIT_FAIL
    _print(f"hold {hold.id} on {hold.counterparty} {hold.product} released by {args.user}")
    return EXIT_OK


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cdl",
        description="cross-desk-limit: counterparty limit check and temporary hold.",
    )
    parser.add_argument("--config", help="path to config.ini (default: ./config.ini)")
    parser.add_argument("--verbose", action="store_true", help="debug level logging")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor", help="PASS/FAIL per item, non-zero on failure")
    doctor.set_defaults(func=command_doctor)

    extract = subparsers.add_parser("extract", help="show what each source returns")
    extract.add_argument("--table", help="one table only")
    extract.add_argument("--cpty", help="narrow the query to one counterparty "
                                        "(default: [treats] probe_counterparty)")
    extract.add_argument("--limit", type=int,
                         help="ask the endpoint for at most this many rows "
                              f"(default {EXTRACT_SAMPLE_ROWS} for an unnarrowed api read)")
    extract.add_argument("--rows", type=int, default=3, help="how many first rows to print")
    extract.add_argument("--save-cache", action="store_true",
                         help="write each table to dev_cache/<TABLE>.csv")
    extract.set_defaults(func=command_extract)

    check = subparsers.add_parser("check", help="full decision and breakdown")
    check.add_argument("--user", required=True, help="username typed at login")
    check.add_argument("--cpty", required=True, help="counterparty acronym (4 or 7 characters)")
    check.add_argument("--product", default=constants.PRODUCT_FX, choices=list(constants.PRODUCTS))
    check.add_argument("--tenor", required=True, help='e.g. "1 months" or 1M')
    check.add_argument("--pair", required=True, help="pair or currency, e.g. USDHKD")
    check.add_argument("--direction", default="buy", choices=list(constants.DIRECTIONS))
    check.add_argument("--notional", required=True, help="notional in USD")
    check.add_argument("--no-hold", action="store_true", help="do not write a hold on Y")
    check.add_argument("--report", help=f"report path (default {DEFAULT_REPORT_NAME})")
    check.set_defaults(func=command_check)

    peers = subparsers.add_parser("peers", help="who currently holds capacity")
    peers.add_argument("--cpty", required=True)
    peers.add_argument("--product", default=constants.PRODUCT_FX, choices=list(constants.PRODUCTS))
    peers.set_defaults(func=command_peers)

    history = subparsers.add_parser("history", help="today's checks")
    history.add_argument("--limit", type=int, default=50)
    history.set_defaults(func=command_history)

    release = subparsers.add_parser("release", help="release one of your own holds")
    release.add_argument("--hold-id", type=int, required=True, dest="hold_id")
    release.add_argument("--user", required=True)
    release.set_defaults(func=command_release)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    verbose = bool(getattr(args, "verbose", False))
    setup_logging(
        logging.DEBUG if verbose else logging.INFO,
        console_level=logging.DEBUG if verbose else logging.WARNING,
    )
    try:
        return int(args.func(args))
    except ConfigError as error:
        _print(f"ERROR: configuration problem: {error}")
        return EXIT_FAIL
    except KeyboardInterrupt:  # pragma: no cover
        _print("interrupted")
        return EXIT_FAIL


if __name__ == "__main__":  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    sys.exit(main())
