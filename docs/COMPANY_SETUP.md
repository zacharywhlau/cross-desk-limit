# Operator setup and handoff

This is the only document you need on the corporate PC. It covers the first run of the
standalone prototype (§10, §19), the package configuration, the `dev_cache` workflow
(§9) and the troubleshooting list.

Nothing in this repository contains an endpoint URL, a library/schema name, a
credential or a real counterparty. Every one of those is a value you fill in locally,
in files that are gitignored (`config.ini`, `dev_cache/`, `logs/`).

---

## 0. What this tool is

A trader types a proposed deal and presses Submit. The tool answers **Y** or **N**,
shows the numbers behind the decision, the counterparty's parent chain and agreement
text as reference, who else currently holds capacity, and today's check history. On Y
it records a **temporary hold** in a shared SQLite database so a teammate looking at
the same residual capacity minutes later sees the claim.

A hold is a soft reservation, **not a booking**. Booking happens elsewhere, by other
teams. The limit system is read-only for this tool.

---

## 1. STEP ONE - the standalone prototype, before anything else

    git pull

Open `prototype/check_limit.py`. It is one file, readable top to bottom, and imports
nothing from `src/`.

1. Fill in the **CONFIG block** at the very top: `URL`, `LIBRARY`, the four table
   names if they differ, and `FFR_WEIGHT_COLUMN` (the quarter column to read).
2. Paste the company connector at the **paste point**:

   ```python
   def query_to_dataframe(url, payload):
       """PASTE THE COMPANY IMPLEMENTATION HERE."""
       raise NotImplementedError("Paste the company connector, then re-run.")
   ```

3. Run it for one counterparty:

   ```bat
   python prototype\check_limit.py --cpty ABCDEFG --product FX --tenor "1 months" ^
       --pair USDHKD --notional 500000
   ```

   Default modes for this first run are `TTCPIPP`, `CKSBLMP` and `CKOVLMP` = real,
   FFR = mock. Add `--mock` to run everything from the mock CSVs.

4. It prints a numbered trace (`[1/6]` … `[6/6]`) and writes the same trace to
   `prototype_report.txt`. **Show that file to a desk user and confirm the numbers**
   before anyone relies on the package.

The trace never prints the URL or a credential, and the library name is replaced by
`<LIBRARY>`. On failure it prints which numbered step failed, the source mode, the
table, the SQL it attempted and the exception, then exits non-zero.

---

## 2. Configure the package

    copy config.example.ini config.ini

Then edit `config.ini`:

| Section    | Key                | What to set                                              |
|------------|--------------------|----------------------------------------------------------|
| `treats`   | `url`              | the internal endpoint                                    |
| `treats`   | `library`          | the schema/library holding the tables                    |
| `treats`   | `ttcpipp/cksblmp/ckovlmp` | `api` once the connector is pasted                |
| `ffr`      | `source`           | leave `mock` until CKBLOTP is confirmed, then `api`       |
| `ffr`      | `table`            | `CKBLOTP`                                                |
| `ffr`      | `weight_column`    | the quarter in force, e.g. `2025Q2`                      |
| `store`    | `db_path`          | the shared network path for the holds database           |

Paste the connector into `src/cdl/treats/api.py` as well - same function, same paste
point. That is the only file in the package that talks to the endpoint.

Any value can also be overridden by an environment variable, e.g.
`CDL_STORE_DB_PATH`, `CDL_FFR_WEIGHT_COLUMN`, `CDL_TREATS_CKSBLMP`.

`config.ini` is gitignored. Never commit it.

---

## 3. Run it

    scripts\run_check.bat doctor
    scripts\run_check.bat extract
    scripts\run_check.bat check --user edmund --cpty ABCDEFG --product FX ^
        --tenor "1 months" --pair USDHKD --notional 500000
    scripts\run_app.bat

- `doctor` prints PASS/FAIL per item: config found, the effective source of every
  table, whether the connector is pasted, each `api` table queried, `db_path`
  reachable and writable, and the `ffr.weight_column` present. Non-zero exit on
  failure. It never prints credentials.
- `extract` shows, per table: source mode, `library.table`, the exact SQL sent, row
  count, column names and the first rows. `--table` limits it to one table;
  `--save-cache` writes `dev_cache\<TABLE>.csv`.
- `check` prints the full decision and breakdown and writes `report.html`.
  `--no-hold` skips writing the hold.
- `peers`, `history` and `release` are the holds/history commands.
- `run_app.bat` opens the window (login, input, decision, breakdown, chain, peers,
  history).

Logs go to `logs\cdl.log` and to the console (warnings and above). At startup the log
names the config path and every table's effective source; per fetch it logs source
mode, `library.table`, row count, elapsed ms and, in `api` mode, the SQL with the
library name masked. It never logs credentials or the endpoint URL.

---

## 4. The shared holds database

`[store] db_path` should point at the shared network folder so the desk sees each
other's holds. That folder holds **only** this SQLite database - never an extract.

The store handles the network case for you:

- A UNC path (`\\server\share\...`) is detected automatically and the database then
  uses the rollback journal (`journal_mode=DELETE`). WAL needs shared memory and is
  unsafe over SMB. Force the decision with `[store] network_path = true|false`.
- `busy_timeout_ms` (default 15000) is always applied and transactions stay short.
- One decision is one transaction opened with `BEGIN IMMEDIATE`: expire stale holds,
  re-read active holds, compute availability, insert history, insert the hold on Y,
  commit. That is what stops two traders spending the same last capacity.

Holds expire after `hold_ttl_minutes` (default 60). Only the creating username may
release one. Expired and released holds free capacity immediately.

---

## 5. dev_cache workflow (weekends, or the endpoint is down)

Purpose: work on real-shaped data when the endpoint is unavailable, without storing
desk data in Git or on the shared network folder.

**Create** the cache while the endpoint still works:

    scripts\run_check.bat extract --save-cache

This runs the configured `api` sources once, writes `dev_cache\<TABLE>.csv` per table
and prints each file path, row count and column list.

**Use** it when the endpoint is down: set the affected tables to `cache` in
`config.ini`, then run as usual. Startup logs which tables came from cache.

**Manual export**: save it as `dev_cache\<TABLE>.csv` (or `.xlsx`) with the original
headers. No code change is needed - `.xlsx` is read when pandas and openpyxl are
available.

Rules:

- `dev_cache/` is gitignored and must never be committed.
- It is never written to the shared network folder.
- Delete it when you are finished.
- It is never used in production.
- The tool refuses to write a cache file outside the configured `dev_cache` path.

---

## 6. Delivery to other traders

Traders must not run pip.

    scripts\build_portable.bat

creates `.venv`, installs the two runtime dependencies (pandas for the data boundary,
openpyxl only for the Excel FFR/cache path), stages `src`, `prototype`, `scripts`,
`data\mock_treats`, `docs`, `config.example.ini` and the venv, runs `doctor` against
the staged copy, and produces `dist\cross-desk-limit.zip`. The other PC unzips it,
copies `config.example.ini` to `config.ini`, edits it, and runs `scripts\run_app.bat`.

---

## 7. What is still unconfirmed (§20)

These are marked `PROVISIONAL` in the code, each in one place you can edit:

| Item | Where |
|------|-------|
| CKBLOTP column layout and the product / currency-class row selection (top priority) | `resolve_ffr_selection` in `src/cdl/logic/ffr.py` |
| CKSBLMP counterparty column name (`CFCPAC` is a placeholder) | `constants.COL_LIMIT_COUNTERPARTY` |
| `CFS0xx` -> tenor bucket meaning, and whether per-bucket limits exist | `constants.BUCKET_INDEX`, `build_surface` in `logic/availability.py` |
| CFSLTT codes for Gold, IRS and Equity swaps (only `FX01` is known) | `constants.LIMIT_TYPE_BY_PRODUCT` |
| Official currency -> Low/Normal/Medium/High lists | `constants.CURRENCY_CLASS_BY_CURRENCY` |
| Pair -> classifying currency rule for non-USD pairs | `classifying_currency` in `logic/ffr.py` |
| Tenor bucket boundaries, especially beyond 1 year | `bucket_for` in `logic/tenor.py` |
| CIRFMG interpretation beyond displaying the text | `constants.COL_AGREEMENT_TEXT` |
| CKOVLMP key column (`CICPAC` is a placeholder) | `constants.COL_AGREEMENT_COUNTERPARTY` |

When a rule is confirmed, edit the one function or constant and run `pytest`.

---

## 8. Troubleshooting

| Symptom | Cause and fix |
|---------|---------------|
| `the company connector has not been pasted` | Paste it into `src/cdl/treats/api.py` (and the prototype). `doctor` reports this as FAIL whenever a table is set to `api`. |
| `[treats] url is not set` / `library is not set` | Fill them in `config.ini`, or set `CDL_TREATS_URL` / `CDL_TREATS_LIBRARY`. |
| Rows come back but columns look wrong | You are pointed at the wrong library. Check `extract` output: it prints `library.table` and the column list. |
| `unknown tenor '...'` | Use a grid value or an alias; the message lists valid examples. The grid is the 89 FFR "Time Period" values. |
| `counterparty ... must be exactly 4 or exactly 7` | Counterparty length is validated before any remote call. |
| `cannot reach the folder for db_path` | The network share is not mounted or you have no write permission. Point `db_path` at a local file to keep working. |
| `no quarter column (e.g. 2025Q2) found` | `ffr.weight_column` names a column that is not in the grid and no `20\d\dQ[1-4]` column exists at all. |
| A weight looks stale | Search `logs\cdl.log` for `ffr weight column ... missing`: the configured column was absent and the highest-sorting quarter was used instead. |
| `is busy or unavailable` (SQLite lock) | Another PC is mid-transaction. Retry; raise `busy_timeout_ms` if it persists. Confirm the journal mode is `DELETE` on a UNC path. |
| `no cache file for <TABLE>` | Run `extract --save-cache` while the endpoint works, or drop a manual export at `dev_cache\<TABLE>.csv`. |
| `doctor` says `[WARN] config file found` | There is no `config.ini`, so the built-in defaults (every source mock) are in use. Copy `config.example.ini`. |
| The window will not open | tkinter is missing from that Python install. Use the portable zip built by `build_portable.bat`. |
