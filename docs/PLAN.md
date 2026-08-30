

PROJECT: cross-desk-limit
An internal trading-desk prototype: counterparty limit check and temporary hold.
This message is the ONLY source of truth. Do NOT clone, read or depend on any other repository.

CONFIDENTIALITY RULES (apply to every file, commit message and log line):
- Never write the employer's name, any internal hostname, URL, library/schema name, credential,
  or real counterparty/limit value into this repository.
- Every environment-specific value (endpoint URL, library name, network path) is a config
  placeholder that the operator fills in locally, in a gitignored file.
- Where an example is needed, use <LIBRARY>, <URL>, ABCD / ABCDEFG style placeholders.

§0. FIRST TASK, BEFORE ANY CODE
- Create REQUIREMENTS.md containing §1 through §12 of this message, verbatim.
- Create docs/PLAN.md containing this entire message, verbatim.
- Create .cursor/rules/cross-desk-limit.mdc (alwaysApply: true): always read REQUIREMENTS.md and
  docs/PLAN.md before changing this project; rules marked PROVISIONAL may change.
- Branch cursor/build-all-milestones-0ed9, commit those files as the FIRST commit, push.
- Only then start M1. This guarantees no context is lost if the session ends.

§1. BACKGROUND — THE DATA SOURCE AND WHAT THE WORDS MEAN
Read this before designing anything; the domain drives the design.

The desk's credit/limit system is referred to here as the limit system. For this tool it is
READ-ONLY: we look up counterparties, their approved limits and current utilisation. We never write
to it and never book a trade. Booking happens elsewhere, by other teams.

Access pattern: an internal HTTP endpoint accepts a SQL statement plus the library/file
(schema/table) it targets and returns tabular rows. A company-provided Python helper wraps it and
returns a pandas DataFrame:

    df = query_to_dataframe(url=URL, payload=payload)

    payload = {
        "startRow": None,                                        # no paging
        "endRow": None,
        "libandfile": [{"library": <LIBRARY>, "file": "CKSBLMP"}],
        "fullSQL": "SELECT * FROM <LIBRARY>.CKSBLMP WHERE ...",  # library.table form
    }

<LIBRARY> and <URL> come from config and are NEVER hardcoded or committed. The body of
query_to_dataframe is company code that exists only on the operator's machine; in this repository it
is a placeholder that raises NotImplementedError. Everything else must work against mock data so
development can happen off the corporate network.

Tables used (field names marked PROVISIONAL are not yet confirmed by the owning team):
  TTCPIPP — counterparty master. XJCPAC = counterparty acronym, XJPRAC = parent acronym.
            Following XJPRAC repeatedly yields the ownership chain up to the ultimate parent.
  CKSBLMP — limits and occupied amounts. CFSLTT = limit type code (FX uses "FX01"),
            CFSLMT = approved limit amount, CFS001..CFS00n = amount already occupied per tenor
            bucket (PROVISIONAL: 01 is the first bucket), counterparty column CFCPAC (PROVISIONAL).
  CKOVLMP — legal/ISDA agreement text. CIRFMG = the message displayed as-is.
  CKBLOTP — the FFR weighting table. This is the table the earlier version of this project targeted
            for FFR. Its exact columns and how a product / currency class selects the right rows are
            NOT yet confirmed (see §7 and §20) — the operator is investigating. Treat the table name
            as config, and isolate the selection rule in one function.

Vocabulary:
  Notional — face amount of the proposed deal, in USD, as typed by the trader.
  FFR weight — a risk factor that scales notional into limit consumption. It depends on product and
            tenor, and for FX also on how volatile the currency is: a 1-month major-pair deal
            consumes far less headroom than a 10-year exotic deal of the same size.
  Usage — the weighted amount that actually consumes limit: notional * (1 + ffr_weight).
  Deal limit — approved capacity for that counterparty and product.
  Tenor bucket — a maturity band (e.g. Spot-1M, 1M-3M) with its own sub-limit/occupied figure.
            One deal lands in exactly one bucket.
  Occupied / utilisation — capacity already consumed according to the limit system.
  Available — limit minus utilisation minus this tool's own active holds.
  Temporary hold — an application-level soft reservation created when a check passes. It is NOT a
            booking. It exists so that when two traders look at the same residual capacity minutes
            apart, the second sees the first's claim instead of double-spending it (overbooking).
            Holds expire automatically so forgotten ones do not freeze capacity.
  Parent chain — a counterparty may be a subsidiary. The confirmed rule for THIS tool: decide on the
            submitted counterparty only; show parent figures as reference information.

§2. WHAT THE TOOL DOES
A trader types a proposed deal and presses Submit. The tool returns a green Y or red N, the numbers
behind the decision, the counterparty's parent chain and agreement text as reference, the teammates
who currently hold capacity on that counterparty, and today's check history. On Y it records a hold
in a shared database so teammates see the claim.

§3. PRODUCTS
Build all four now (all data is mock during development, so there is no reason to stage them):
FX, Gold, IRS, Equity swaps. Each has its own usage function and its own FFR resolution, but all
start from the same default formula in §4. No product may be unreachable from the UI.

§4. HARD BUSINESS RULES (do not change without operator instruction)
1. DEFAULT FORMULA FOR ALL PRODUCTS: usage = notional_usd * (1 + ffr_weight)
   Each product implements this in its own function so one product's formula can be replaced later
   without touching the others. Mark them "default shared formula - may diverge per product".
2. Decision is Y only if usage fits BOTH the deal limit AND the affected tenor bucket, for the
   SUBMITTED counterparty. One deal affects exactly one bucket.
3. available = limit - utilisation - sum(usage of active holds on that counterparty+product)
4. Parent and ultimate-parent figures are DISPLAY-ONLY. They never decide Y/N.
5. Insufficient limit = hard reject (red N). No override, no partial hold.
6. On Y create a hold; on N create none. Both outcomes are written to history.
7. If any required source fails: decision = ERROR with a plain-language message naming the table and
   the source mode. Never show Y or N from partial or stale data.
8. Holds are stackable, expire after hold_ttl_minutes (default 60), and only the creating username
   may release one. Expired and released holds free capacity immediately.
9. Identity is the username typed at login. No password, no directory lookup. English UI only.

§5. INPUT AND VALIDATION
Fields: username, counterparty, product, tenor, pair_or_currency, direction (buy|sell), notional_usd.
- counterparty: uppercase alphanumeric, length EXACTLY 4 OR EXACTLY 7. Reject other lengths with a
  clear message BEFORE any remote call.
- product: one of the four in §3.
- notional_usd: positive number.
- direction: collected and stored but NOT used in the formula (no netting yet).
- tenor: see §6.

§6. TENORS, BUCKETS, CALCULATORS
Accepted tenor labels (the FFR "Time Period" grid, 89 values):
  Spot; 1 week; 2 weeks; 3 weeks; 1 months .. 60 months; 6 years .. 30 years.
Accept and normalise compact aliases: spot->Spot, 1W->1 week, 2W->2 weeks, 3W->3 weeks,
1M->1 months, 3M->3 months, 6M->6 months, 1Y->12 months, 2Y->24 months, 5Y->60 months,
10Y->10 years, 30Y->30 years. Case-insensitive, whitespace-tolerant.
An unknown tenor raises an error whose message lists valid examples (never a bare "unknown").

PROVISIONAL bucket map (one function, easy to edit):
  Spot, all weeks, 1 months -> "Spot-1M"
  2-3 months   -> "1M-3M"
  4-6 months   -> "3M-6M"
  7-12 months  -> "6M-1Y"
  13+ months and all years -> "1Y+"

Calculators: one function per product plus a dict registry:
  fx_usage, gold_usage, irs_usage, equity_swap_usage — all currently notional * (1 + weight),
  each marked as the shared default.

§7. FFR WEIGHT — TABLE CKBLOTP IS THE TARGET
One public function: lookup_ffr(product, pair_or_currency, tenor) -> FfrLookup
  FfrLookup: weight (float, e.g. 0.018), table_name, source_label, time_period.

Source priority (config ffr.source):
  api   — THE GOAL. Reads the FFR table (CKBLOTP, name from config) through the SAME connector,
          payload and SQL builder as every other table. Implement this path for real, not as a stub.
  mock  — Cursor development, and the interim setting on the operator's PC while the CKBLOTP column
          layout and row-selection rule are being confirmed. Same column names as the real table.
  excel — LAST RESORT ONLY, for the case where the weights turn out not to be queryable: one
          workbook, one sheet per product/class, refreshed manually each quarter. Implement it, but
          document it as the fallback we do not want.

FX resolution flow:
  1. Classifying currency: a 3-letter input is the currency; for a 6-letter pair take the non-USD
     side (e.g. USDHKD->HKD, EURUSD->EUR); if neither side is USD take the quote currency.
     PROVISIONAL.
  2. Currency -> class Low | Normal | Medium | High. PROVISIONAL sample lists in constants.py.
  3. Class -> the rows to read. In mock mode this is a per-class file name
     (FFR_FX_LOW / FFR_FX_NORMAL / FFR_FX_MEDIUM / FFR_FX_HIGH). In api mode it is CKBLOTP plus a
     selection rule that is NOT yet confirmed. Put both behaviours in ONE function,
     resolve_ffr_selection(product, ccy_class) -> (table_or_file, filter_description), and mark the
     api branch PROVISIONAL with a TODO naming what the operator must confirm.
Non-FX: product -> its own selection (mock files FFR_GOLD / FFR_IRS / FFR_EQ_SWAP).

TABLE SHAPE, and what weight_column means (identical for api, mock and excel):
The FFR table is a grid. Each ROW is a maturity, labelled in a column called "Time Period"
(values exactly like the tenor grid in §6: "Spot", "1 week", "1 months", "10 years", ...).
Each additional COLUMN is one published quarterly snapshot of the weights, named like a quarter:
"2025Q1", "2025Q2", "2025Q3". The cell where the row and the quarter column meet is the weight in
force for that maturity in that quarter.

    Time Period | 2025Q1 | 2025Q2
    Spot        | 0.9%   | 1%
    1 months    | 2.4%   | 2.5%
    12 months   | 7.8%   | 8%

The tool must therefore know WHICH quarter column to read. That is config ffr.weight_column
(default "2025Q2"). When a new quarter is published, the operator changes that one value instead of
touching code. If the configured column is missing, fall back to the highest-sorting column matching
20\d\dQ[1-4] and LOG which column was actually used, so a silently stale weight is impossible.
Cell values may be "1%", "2.5%", 0.01 or 1; parse to a fraction (1% -> 0.01).

§8. DATA SOURCES AND FILE FORMATS — MOCK LOOKS LIKE A REAL EXPORT
Every table reaches the logic layer as list[dict] with the SAME keys, whatever the source. One
shared tabular reader serves mock and cache, so there is only one parsing path to debug.

  Mock lives in data/mock_treats/ as CSV, ONE FILE PER TABLE, named after the table, using the REAL
  column names:
      TTCPIPP.csv, CKSBLMP.csv, CKOVLMP.csv,
      FFR_FX_LOW.csv, FFR_FX_NORMAL.csv, FFR_FX_MEDIUM.csv, FFR_FX_HIGH.csv,
      FFR_GOLD.csv, FFR_IRS.csv, FFR_EQ_SWAP.csv
  CSV rather than JSON because that is what a SQL export looks like, it diffs cleanly in Git, it
  needs no extra dependency, and a mock file can be replaced by a sanitised real export with no code
  change. Mock values must be obviously synthetic.

  Reference data that is NOT a table lives in src/cdl/constants.py, not in data/: the product list,
  pair and currency lists, the 89-value tenor grid, currency->class mapping, class->selection
  mapping, and every PROVISIONAL field name.

  Cache lives in dev_cache/ (gitignored), same table-named files. The tool writes CSV there and also
  READS .xlsx when pandas and openpyxl are available, so manual downloads work.

Per-table source resolution (config, so tables can be switched to real one at a time):
    treats.ttcpipp = mock | api | cache
    treats.cksblmp = mock | api | cache
    treats.ckovlmp = mock | api | cache
    ffr.source     = api | mock | excel      (CKBLOTP; see §7)

pandas boundary rule: pandas may be imported ONLY inside src/cdl/treats/ and in the standalone
prototype script. Convert immediately to list[dict]. All logic, storage and UI code is plain Python
and testable without pandas.

§9. dev_cache WORKFLOW (weekends / endpoint down) — DOCUMENT IN docs/COMPANY_SETUP.md
Purpose: work on real-shaped data when the endpoint is unavailable, without storing desk data in Git
or on the shared network folder.
  Create (when the endpoint works):   scripts\run_check.bat extract --save-cache
    Runs the configured api sources once, writes dev_cache\<TABLE>.csv per table, and prints each
    file path, row count and column list.
  Use (when it is down): set the affected tables to cache in config.ini, then run as usual.
    Startup logs which tables came from cache.
  Manual export: save it as dev_cache\<TABLE>.csv (or .xlsx) with the original headers; no code
    change needed.
  Rules: dev_cache/ is gitignored and must never be committed; never written to the shared network
    folder; deleted when finished; never used in production. The tool refuses to write cache files
    outside the configured dev_cache path.

§10. M1 — THE STANDALONE SINGLE-USER SCRIPT (build this first, keep it independent)
File: prototype/check_limit.py — ONE file, no imports from src/, only pandas and the standard
library. This is the operator's first test on the corporate network and must stay simple enough to
debug by reading it top to bottom.

Scope: SINGLE USER, very early prototype. It answers one question: for one counterparty and one
proposed deal, what does the limit data say? Deliberately NOT included: holds, other traders'
history, shared database, UI, threading, packages, plugins.

Structure, in this order inside the file:
  1. A CONFIG block at the very top: URL and LIBRARY placeholders, the four table names, the mock
     FFR folder path, ffr weight_column, and default deal inputs. All in one place.
  2. The paste point:
         def query_to_dataframe(url, payload):
             """PASTE THE COMPANY IMPLEMENTATION HERE."""
             raise NotImplementedError("Paste the company connector, then re-run.")
  3. Small helpers, each printing what it does: build_sql(), fetch(table) honouring per-table mode
     (real | mock), parse_percent(), normalise_tenor(), bucket_for(), ffr_weight_from_mock().
  4. main(): validate the counterparty length (4 or 7), fetch the counterparty row and walk the
     parent chain, fetch limits, read the FFR weight from mock, compute usage, compute deal and
     bucket availability, print the verdict.

Default modes for the operator's first run: TTCPIPP, CKSBLMP and CKOVLMP = real; FFR = mock.
A --mock flag runs everything from the mock CSVs so it can be developed and tested in Cursor.

Command line:
    python prototype\check_limit.py --cpty ABCDEFG --product FX --tenor "1 months" ^
        --pair USDHKD --notional 500000 [--mock]

Output: a numbered, readable trace, for example
    [1/6] counterparty ABCDEFG (length 7) OK
    [2/6] TTCPIPP  source=real  SQL: SELECT * FROM <LIBRARY>.TTCPIPP WHERE XJCPAC='ABCDEFG'
          rows=1  parent=ABCDGRP  chain=ABCDEFG > ABCDGRP
    [3/6] CKSBLMP  source=real  rows=5  limit_type=FX01  limit=20,000,000  utilisation=3,500,000
    [4/6] FFR      source=mock  file=FFR_FX_LOW.csv  class=Low(HKD)  period=1 months
          column=2025Q2  weight=1.8%
    [5/6] usage = 500,000 * (1 + 0.018) = 509,000   bucket=Spot-1M
    [6/6] DECISION: Y   deal available before=16,500,000 after=15,991,000
                        bucket Spot-1M before=... after=...
          parent ABCDGRP (reference only): limit=... utilisation=...
It also writes prototype_report.txt with the same trace so it can be shown to a desk user for logic
sign-off. On failure it prints which numbered step failed, the source mode, the table, the SQL it
attempted and the exception, then exits non-zero. It never prints the URL or any credential.

§11. SHARED HOLDS AND HISTORY (M3; no server, no ORM)
Use the stdlib sqlite3 module directly. Path from config:
    [store] db_path = ./data/cross_desk_limit.db     # local default; operator sets a network path
Tables, created on first use:
  limit_checks(id, created_at, username, counterparty, parent_counterparty, product, tenor,
               affected_bucket, pair_or_currency, direction, notional_usd, usage, ffr_table,
               ffr_weight, decision, message)
  temporary_holds(id, check_id, created_at, expires_at, released_at, status, username,
                  counterparty, product, tenor, affected_bucket, pair_or_currency,
                  notional_usd, usage)
Network-share correctness (get this right):
  - Detect a network path (UNC "\\\\..." or a configured flag). On a network path use
    journal_mode=DELETE, NOT WAL — WAL needs shared memory and is unsafe over SMB. Use WAL only when
    the database file is on a local disk.
  - Always set busy_timeout (default 15000 ms); keep transactions short.
  - Each decision runs in ONE transaction opened with BEGIN IMMEDIATE: expire stale holds, re-read
    active holds, compute availability, insert history, insert the hold on Y, commit. This is what
    stops two traders spending the same last capacity.
  - Wrap sqlite3.OperationalError in a plain message naming db_path and suggesting a retry; log it.
Operations: expire_stale(now), active_holds(counterparty, product), release(hold_id, username)
(refuse when the username differs), history_today(), peers with minutes remaining.

§12. USER INTERFACE (M4; tkinter, minimal but clear)
One process, no server, no port. Single window, sections top to bottom:
  1. Login: username entry, remembered for the session.
  2. Input: counterparty, product (all four), tenor, pair/currency, direction, notional; Submit.
  3. Decision: large green Y or red N, or ERROR text; message; FFR table/class/weight; notional;
     usage; affected bucket; and the source mode used for each table.
  4. Breakdown: deal limit before/after, this request's usage, one row per tenor bucket.
  5. Counterparty chain: one row per node (acronym, parent, limit, utilisation, holds, available)
     labelled "reference only", with the agreement text per node.
  6. Traders who have asked: active holds with username, notional, usage, minutes remaining;
     Release enabled only on the logged-in user's own rows.
  7. Today's history: recent checks with their decision.
Amounts in millions with two decimals (15.99mm); weights as percentages. Readability over styling.
No business logic in UI files.

§13. ARCHITECTURE AND FILE LAYOUT (keep it this flat)
    cross-desk-limit/
      README.md, REQUIREMENTS.md, config.example.ini, .gitignore
      docs/PLAN.md, docs/COMPANY_SETUP.md
      prototype/check_limit.py        # M1, standalone, no imports from src/
      src/cdl/
        config.py           # configparser + env override; one Settings dataclass
        logging_setup.py    # console + logs/cdl.log
        constants.py        # ALL table/field names, tenor grid, product & pair lists,
                            # currency->class and class->selection maps; PROVISIONAL marked
        models.py           # dataclasses: CheckRequest, CheckResult, Surface, ChainNode, FfrLookup
        treats/
          api.py            # query_to_dataframe placeholder (company paste point)
          sql.py            # "SELECT * FROM {library}.{table}" builders; library from config
          tabular.py        # ONLY pandas import site in src/; CSV/XLSX/DataFrame -> list[dict]
          mock.py           # loads data/mock_treats/<TABLE>.csv
          cache.py          # loads/writes dev_cache/<TABLE>.csv (.xlsx read if pandas available)
          source.py         # per-table source resolution -> list[dict]; logs source per table
        logic/
          counterparty.py   # 4-or-7 validation, parent chain walk (PROVISIONAL)
          tenor.py          # grid, normalisation, bucket map (PROVISIONAL)
          ffr.py            # lookup_ffr, resolve_ffr_selection, source switch, percent parsing
          calculators.py    # per-product usage functions + registry
          availability.py   # deal and bucket availability including holds
          check.py          # orchestrator: CheckRequest -> CheckResult (no UI, no SQL strings)
        store/db.py         # sqlite3 schema, tuning, holds, history, TTL, release
        ui/app.py           # tkinter window only
        ui/report.py        # breakdown as text and as report.html
        cli.py              # doctor | extract | check | peers | history | release
      scripts/  run_app.bat run_app.sh run_check.bat run_check.sh build_portable.bat
      data/mock_treats/*.csv
      tests/
Dependency rule: ui and cli may import logic and store; logic may import treats and constants;
nothing imports ui; prototype/ imports nothing from src/. Type-annotate public functions.
Forbidden: FastAPI, Streamlit, SQLAlchemy, pydantic, any HTTP server, any background scheduler.

§14. CLI FOR THE PACKAGE (M2) AND LOGGING
Three commands must work without the UI, mirroring the prototype but package-based:
  doctor  — PASS/FAIL per item: config found; effective source of every table; whether the connector
            is pasted; each api table queried successfully; db_path reachable and writable;
            ffr.weight_column present. Non-zero exit on failure. Never prints credentials.
  extract — per table: source mode, library.table, the exact SQL sent, row count, column names,
            first rows. Supports --table and --save-cache.
  check   — full decision and breakdown to console plus report.html; --no-hold to skip writing.
Logging: logs/cdl.log plus console. At startup log the config path and every table's effective
source. Per fetch log source mode, library.table, row count, elapsed ms, and the SQL when api. Per
check log the FFR selection, quarter column and resolved weight. On failure log table, mode and
operation. Never log credentials or the endpoint URL.

§15. CONFIG (config.example.ini, copied to config.ini which is gitignored)
    [treats]
    url = PASTE_ENDPOINT_URL_HERE
    library = PASTE_LIBRARY_NAME_HERE
    ttcpipp = mock
    cksblmp = mock
    ckovlmp = mock
    [ffr]
    source = mock            # target is api; excel is last resort
    table = CKBLOTP          # FFR table; column layout still being confirmed
    weight_column = 2025Q2   # which quarterly snapshot column to read (see §7)
    excel_path =
    [store]
    db_path = ./data/cross_desk_limit.db
    hold_ttl_minutes = 60
    busy_timeout_ms = 15000
    [paths]
    dev_cache = ./dev_cache
Any value may be overridden by an environment variable (e.g. CDL_STORE_DB_PATH).

§16. DATA MODES AND RETENTION
  Cursor development: every source mock; never contact a corporate endpoint.
  Operator PC now:    ttcpipp/cksblmp/ckovlmp = api; ffr.source = mock until CKBLOTP is confirmed,
                      then ffr.source = api.
  Endpoint down:      affected tables switch to cache (§9).
  Production:         live query per check; the tool writes no extract to disk.
Never commit real values. Never put extracts on the shared network folder; that folder holds only
the SQLite holds/history database.

§17. MILESTONES — build ALL of these with mock before the operator tests on the corporate network
  M1 standalone prototype: prototype/check_limit.py per §10.
     Done when: --mock produces the full numbered trace and a correct Y and N on mock data, and the
     real-mode path is present with the paste point clearly marked.
  M2 package core: config, logging, constants, models, treats sources (mock, api placeholder,
     cache), logic (counterparty, tenor, ffr, calculators, availability, check), cli with
     doctor/extract/check, report.html, and the parity test in §22.
  M3 store: sqlite3 schema, transaction discipline, TTL, own-release, peers, history.
  M4 ui: tkinter app with the seven sections in §12 calling the same check().
  M5 delivery: build_portable.bat, COMPANY_SETUP.md (including §9, §10 and §19), README, tests
     green, logs verified.
Commit after each milestone; branch cursor/build-all-milestones-0ed9.

§18. DELIVERY TO OTHER TRADERS
Traders must not run pip. scripts/build_portable.bat creates the venv, installs the few
dependencies, and produces a zip that another PC unzips and runs with scripts/run_app.bat. Runtime
dependencies: pandas (data boundary) and openpyxl (only for the Excel FFR/cache path). Everything
else stdlib. Fast startup; nothing to wait for.

§19. OPERATOR HANDOFF (docs/COMPANY_SETUP.md)
  1. git pull
  2. STEP ONE, before anything else: open prototype/check_limit.py, fill the CONFIG block, paste the
     connector at the paste point, and run it for one counterparty. Confirm the numbers with a desk
     user using prototype_report.txt.
  3. Then copy config.example.ini to config.ini; set treats.url, treats.library and [store] db_path.
  4. Set ttcpipp/cksblmp/ckovlmp = api; leave ffr.source = mock until CKBLOTP is confirmed.
  5. Run doctor, then extract, then check, then run_app.
  6. On failure read logs/cdl.log — it names the table, source mode and SQL.
Troubleshooting list to include: connector not pasted, wrong url or library, unknown tenor,
counterparty length, db_path unreachable, missing quarter column, sqlite lock, cache file missing.

§20. UNCONFIRMED — DO NOT INVENT; KEEP IN constants.py AND MARK PROVISIONAL
  - CKBLOTP column layout, and how product / currency class selects the right rows (top priority;
    the operator is investigating this now)
  - CKSBLMP counterparty column name (CFCPAC is a placeholder)
  - CFS0xx -> tenor bucket meaning; whether per-bucket limits exist or only occupied amounts
  - CFSLTT limit-type codes for Gold, IRS and Equity swaps (only FX01 is known)
  - Official currency -> Low/Normal/Medium/High lists (mock ships a small sample)
  - Pair -> classifying currency rule for non-USD pairs
  - Tenor bucket boundaries, especially beyond 1 year ("1Y+" is provisional)
  - CIRFMG interpretation beyond displaying the text
Add a one-line PROVISIONAL comment at each such value. Do not build abstraction layers "just in
case": a provisional rule is one function the operator can edit.

§21. OUT OF SCOPE (do not start)
Export of results to spreadsheets, admin release of other people's holds, dashboards or analytics,
direction netting, products beyond the four, authentication, background schedulers, containers,
web UI.

§22. TESTS (pytest, small and deterministic)
  - tenor: aliases, full grid, bucket boundaries, clear error for unknown input
  - counterparty: 4 and 7 accepted; 5, 6, 8 and empty rejected
  - ffr: percent parsing, FX currency derivation, class mapping, missing quarter column fallback
    (asserts the substituted column is logged)
  - calculators: all four products return notional * (1 + weight)
  - tabular: CSV mock loads with the real column names; cache round-trip write-then-read
  - availability: active holds reduce availability; expired holds do not
  - store: TTL expiry, own-release refusal, two-user stacking, exhaustion produces N, and a threaded
    concurrency test on one temp database
  - check: mock end-to-end Y and N, plus the ERROR path when a source raises
  - cli: doctor exits non-zero when a table is set to api but the connector is missing
  - PARITY: run prototype/check_limit.py --mock and the package check on the SAME reference inputs
    and assert identical decision, usage and bucket. This is what keeps the standalone script honest
    after refactors.
  - ui: import-only smoke test, skipped when tkinter is unavailable
Reference case that must produce a sensible Y on mock data:
  edmund / ABCDEFG / FX / "1 months" / USDHKD / 500000
  -> bucket Spot-1M, FX class Low, usage = 500000 * (1 + weight)
Mock fixtures must include a counterparty with a parent chain (ABCDEFG -> ABCDGRP), one with no
parent, a 4-character counterparty, one nearly exhausted so N is easy to demonstrate, and rows for
all four products.

§23. ACCEPTANCE CHECKS (list each in the PR description)
  - REQUIREMENTS.md, docs/PLAN.md and .cursor/rules in the FIRST commit
  - prototype/check_limit.py runs standalone with --mock, imports nothing from src/, and shows the
    numbered trace plus the marked paste point
  - pytest passes, including the parity test; state the count
  - doctor, extract and check all work in mock mode; check writes report.html
  - all four products selectable and computable
  - mock data is CSV, one file per table, using the real column names, with obviously synthetic values
  - dev_cache write path works via extract --save-cache and is gitignored
  - FFR api path implemented through the same connector as other tables (not a stub), table name
    from config (CKBLOTP), quarter column from config with a logged fallback
  - two-username hold stacking, TTL expiry and own-release refusal demonstrated
  - tkinter app shows all seven sections
  - no pandas import outside src/cdl/treats/ and prototype/
  - no FastAPI, Streamlit, SQLAlchemy or pydantic anywhere
  - db_path configurable, local by default; network path selects rollback journal mode
  - no employer name, endpoint URL, library name, credential or real value committed anywhere


