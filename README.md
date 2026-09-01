# cross-desk-limit

An internal trading-desk prototype: counterparty limit check and temporary hold.

A trader types a proposed deal and presses Submit. The tool answers a green **Y** or a
red **N**, shows the numbers behind the decision, the counterparty's parent chain and
agreement text as reference, the teammates who currently hold capacity, and today's
check history. On Y it records a **temporary hold** in a shared database so a
teammate who looks at the same residual capacity minutes later sees the claim instead
of double-spending it.

A hold is an application-level soft reservation, **not a booking**. The limit system
is read-only for this tool; booking happens elsewhere, by other teams.

- `REQUIREMENTS.md` - §1 to §12 of the operator's brief (the source of truth).
- `docs/PLAN.md` - the complete brief, including milestones, unconfirmed items,
  scope boundaries, the test list and the acceptance checks.
- `docs/COMPANY_SETUP.md` - **read this on the corporate PC**: first run, config,
  row caps, the `dev_cache` workflow, delivery and troubleshooting.
- `docs/HOW_IT_WORKS.md` - the decision explained: FFR weight, usage, the
  availability ladder with worked numbers, holds, and what is still provisional.
- `docs/DEMO.md` - a ten minute demo for the desk, with `scripts/demo.sh` / `.bat`.

No employer name, endpoint URL, library/schema name, credential or real counterparty
value is committed anywhere. Every environment-specific value lives in `config.ini`,
which is gitignored.

## Quick start (development, everything mock)

```bash
python3 -m pytest                     # 380 tests, all mock, no network

export PYTHONPATH=src
python3 -m cdl.cli doctor             # PASS/FAIL per item
python3 -m cdl.cli extract --table CKSBLMP --rows 3
python3 -m cdl.cli check --user edmund --cpty ABCDEFG --product FX \
    --tenor "1 months" --pair USDHKD --notional 500000
python3 -m cdl.ui.app                 # the tkinter window
```

The standalone prototype, which imports nothing from `src/`:

```bash
python3 prototype/check_limit.py --mock --cpty ABCDEFG --product FX \
    --tenor "1 months" --pair USDHKD --notional 500000
```

Reference case on mock data: `edmund / ABCDEFG / FX / "1 months" / USDHKD / 500000`
-> period `SPT-1M`, FX class `Low`, weight `1.8%`, usage `509,000`, decision **Y**.
`EFGHIJK` is nearly exhausted, so the same deal there is a clean **N**, and no mock
counterparty has an FX limit beyond five years, so a `10Y` deal is rejected too.

The desk demo, which runs all of that in sequence:

```bash
scripts/demo.sh                       # see docs/DEMO.md
```

## How a decision is made

```
usage = notional_usd * (1 + ffr_weight)             # per product, currently shared

# The limit system is cumulative: a deal consumes its own time period and every
# shorter one, so availability is a ladder over the 14 periods, shortest first.
reverse_cum[i] = sum(cash risk of period j for j >= i)      # our active holds included
available[i]   = max(0, min(limit[i] - reverse_cum[i], available[i - 1]))

decision = Y only if usage fits BOTH the affected period's availability AND the total
           limit, for the SUBMITTED counterparty
```

The 14 periods are CALL, TDY, TOM, SPT, SPT-1M, 1M-3M, 3M-6M, 6M-1Y, 1Y-3Y, 3Y-5Y,
5Y-7Y, 7Y-10Y, 10Y-15Y, 15Y+, held in `CFSO01..CFSO14` (cash risk) and
`CFSL01..CFSL14` (limit). `docs/HOW_IT_WORKS.md` works through the arithmetic.

Parent and ultimate-parent figures are display-only and never decide Y/N. Insufficient
limit is a hard reject: no override and no partial hold. If a required source fails the
decision is `ERROR` naming the table and the source mode - never a Y or N from partial
data, which is also why a result set that arrives at the endpoint's row cap is refused.

## Layout

```
prototype/check_limit.py    M1: one standalone file, numbered trace, company paste point
src/cdl/
  config.py                 config.ini + environment overrides -> one Settings dataclass
  logging_setup.py          console + logs/cdl.log, library name masked
  constants.py              every table/field name, the 89-value tenor grid, the maps
  models.py                 CheckRequest, CheckResult, Surface, ChainNode, FfrLookup, Hold
  treats/                   the ONLY place pandas may be imported
    api.py                  company connector paste point + the api fetch path
    sql.py                  "SELECT * FROM {library}.{table}" and the payload
    tabular.py              CSV / XLSX / DataFrame -> list[dict]
    mock.py                 data/mock_treats/<TABLE>.csv
    cache.py                dev_cache/<TABLE>.csv (gitignored, never production)
    source.py               per-table mock | api | cache resolution, logged
  logic/                    plain Python, no pandas, no SQL strings
    counterparty.py         4-or-7 validation, parent chain walk
    tenor.py                grid, aliases, tenor -> one of the 14 periods
    ffr.py                  lookup_ffr, resolve_ffr_selection, quarter column fallback
    calculators.py          fx / gold / irs / equity_swap usage + registry
    availability.py         the cumulative availability ladder, holds included
    check.py                orchestrator: CheckRequest -> CheckResult
  store/db.py               sqlite3 schema, journal mode, TTL, own-release, history
  ui/app.py                 tkinter window (seven sections)
  ui/report.py              breakdown as text and as report.html
  cli.py                    doctor | extract | check | peers | history | release
data/mock_treats/*.csv      one CSV per table, real column names, synthetic values
scripts/                    run_app, run_check, build_portable
tests/                      pytest, all deterministic, all mock
```

Dependency rule: `ui` and `cli` may import `logic` and `store`; `logic` may import
`treats` and `constants`; the tkinter window is imported by nothing;
`prototype/` imports nothing from `src/`. Runtime dependencies are pandas (data
boundary) and openpyxl (Excel FFR / `.xlsx` cache only) - everything else is stdlib.

## Mock data

`data/mock_treats/` holds one CSV per table using the real column names, so a file can
be replaced by a sanitised real export with no code change. Values are obviously
synthetic: `ABCDEFG -> ABCDGRP` ownership, a 4-character `ABCD`, a nearly exhausted
`EFGHIJK`, rows for all four products with a limit ladder that narrows as maturity
grows, and FFR grids whose weights rise with maturity (`0.9%` at Spot to about `18%` at
30 years for the Low class) and with the currency class, published as three quarterly
columns `2025Q1`, `2025Q2`, `2025Q3`.

## Notes

`programming_note.md` collects the Python and command-line notes for this project
(sqlite3 over SMB, `BEGIN IMMEDIATE`, the pandas boundary, tkinter layout, pytest).
