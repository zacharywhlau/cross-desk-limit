# Programming notes - cross-desk-limit

Python and command-line notes collected while building this project. Organised by
topic; each entry says what the thing is, why it is used here, and the smallest
example that makes it clear.

---

## 1. SQLite for a shared file on a network folder

### 1.1 Why not WAL over SMB

`journal_mode=WAL` keeps a write-ahead log and coordinates readers and writers through
a **shared-memory file** (`-shm`). Windows file sharing (SMB) does not give processes
on different machines real shared memory, so WAL on a network share can corrupt the
database. The rollback journal (`journal_mode=DELETE`) uses only ordinary file
locking, which SMB does implement.

```python
journal = "DELETE" if str(db_path).startswith("\\\\") else "WAL"
connection.execute(f"PRAGMA journal_mode = {journal}")
```

`\\server\share\file.db` is a UNC path. In a Python string that is written
`"\\\\server\\share"` (or `r"\\server\share"` with a raw string).

### 1.2 busy_timeout

SQLite allows one writer at a time. Without a timeout, a second writer fails
immediately with `database is locked`. With a timeout it retries internally.

```python
connection = sqlite3.connect(path, timeout=15.0)   # seconds, for the Python driver
connection.execute("PRAGMA busy_timeout = 15000")  # milliseconds, for SQLite itself
```

Set both: the `timeout=` argument covers the connect, the pragma covers statements.

### 1.3 BEGIN IMMEDIATE - the read-then-write race

The bug this prevents: two traders read "0.05mm available" at the same moment and both
decide Y. A plain `BEGIN` (deferred) takes the write lock only at the first write, so
both readers can be inside their transaction at once.

`BEGIN IMMEDIATE` takes the write lock **at the start**, so the second transaction
waits (up to `busy_timeout`) before it reads anything.

```python
connection = sqlite3.connect(path, isolation_level=None)  # autocommit: we drive it
connection.execute("BEGIN IMMEDIATE")
try:
    ...  # expire, re-read holds, decide, insert history, insert hold
    connection.execute("COMMIT")
except Exception:
    connection.execute("ROLLBACK")
    raise
```

`isolation_level=None` turns off the sqlite3 module's implicit transaction handling.
Without it the driver opens and commits transactions on its own and the explicit
`BEGIN IMMEDIATE` fights with it.

### 1.4 sqlite3.Row

```python
connection.row_factory = sqlite3.Row
row = connection.execute("SELECT * FROM temporary_holds WHERE id = ?", (1,)).fetchone()
row["username"]      # access by column name, not by position
```

Always pass values as parameters (`?`), never with f-strings: that is both injection
safety and correct typing.

### 1.5 lastrowid

```python
cursor = connection.execute("INSERT INTO limit_checks (...) VALUES (?,?,?)", values)
check_id = cursor.lastrowid          # the new AUTOINCREMENT id
```

---

## 2. Threads and determinism in tests

`threading.Barrier(n)` makes n threads start at the same instant, which is what makes a
concurrency test actually contend instead of running one after another.

```python
start = threading.Barrier(5)

def worker(index):
    start.wait(timeout=10)   # all five leave together
    ...

threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
for t in threads: t.start()
for t in threads: t.join(timeout=30)
```

Collect results under a `threading.Lock()`; assert on counts, not on order.

To make time-dependent behaviour (TTL expiry) deterministic, inject the clock instead
of sleeping:

```python
clock = {"now": datetime(2026, 1, 5, 10, 0, 0)}
store = HoldsStore(settings, clock=lambda: clock["now"])
clock["now"] += timedelta(minutes=61)   # "an hour later", instantly
```

---

## 3. Dataclasses

```python
from dataclasses import dataclass, field, replace

@dataclass(frozen=True)          # immutable + hashable: safe to pass around
class CheckRequest:
    username: str
    notional_usd: float
    sources: dict[str, str] = field(default_factory=dict)   # never a mutable default

other = replace(request, notional_usd=1_000_000)            # copy with one change
```

- `frozen=True` prevents accidental mutation of a value object.
- `field(default_factory=...)` is required for list/dict/set defaults.
- `dataclasses.replace` is the clean way to make a variant - used in the tests to
  build a Settings that reads its mock tables from a temp directory.

A `@property` on a frozen dataclass is the right place for a derived number:

```python
@property
def available(self) -> float:
    return self.deal_limit - self.utilisation - self.holds_usage
```

---

## 4. Protocols - depending on an interface, not a module

The rule in this project is that `logic/` must not import `store/`. But the
orchestrator needs the store's transaction. `typing.Protocol` gives structural typing:
the type is described where it is *used*, and any object with that shape satisfies it.

```python
from typing import Protocol

class HoldsGateway(Protocol):
    def commit_decision(self, request, compute, *, create_hold: bool = True): ...

def run_check(request, settings, store: HoldsGateway | None = None): ...
```

No import of the store, no base class to inherit, and the caller (cli / ui) wires the
real object in.

---

## 5. configparser and environment overrides

```python
parser = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
parser.read_dict(DEFAULTS)          # defaults first
parser.read(path, encoding="utf-8") # file wins over defaults
if env_name in os.environ:
    parser.set(section, key, os.environ[env_name])   # environment wins over the file
```

- `inline_comment_prefixes` is what allows `weight_column = 2025Q2   # comment`.
  Without it the value would literally be `2025Q2   # comment`.
- `parser.get` always returns `str`; convert and validate yourself (`int()`, a
  membership check against the allowed values) and raise one clear error type.
- Convention used here: `CDL_<SECTION>_<KEY>`, so `[store] db_path` becomes
  `CDL_STORE_DB_PATH`.

---

## 6. pathlib

```python
Path(__file__).resolve().parents[2]        # repository root from src/cdl/config.py
path.expanduser()                          # ~/... -> /home/you/...
(root / relative).resolve()                # join, then normalise
path.parent.mkdir(parents=True, exist_ok=True)
path.is_file()
path.suffix.lower()                        # ".csv"
for path in directory.rglob("*.py"): ...   # recursive glob
```

Refusing to write outside a directory - compare *resolved* parents, which defeats
`../escape` and symlinks:

```python
target = (directory / f"{table}.csv").resolve()
if target.parent != directory.resolve():
    raise CachePathError(...)
```

---

## 7. csv module (no pandas needed)

```python
import csv

with path.open("r", encoding="utf-8-sig", newline="") as handle:
    rows = [dict(row) for row in csv.DictReader(handle)]

with path.open("w", encoding="utf-8", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
```

- `encoding="utf-8-sig"` strips the byte-order mark Excel writes, which otherwise
  turns the first column name into `\ufeffTime Period`.
- `newline=""` is required on Windows; without it you get a blank line between rows.
- `extrasaction="ignore"` stops a stray key in one row from raising.

Keeping insertion order as the column order (dict preserves order since 3.7):

```python
seen: dict[str, None] = {}
for row in rows:
    for key in row:
        seen.setdefault(key, None)
columns = list(seen)
```

---

## 8. Keeping pandas at the boundary

pandas is imported **inside the function**, not at module top level, so the rest of
the tool works without it:

```python
def read_xlsx(path, sheet=0):
    try:
        import pandas as pd
    except ImportError as error:
        raise TabularError("reading .xlsx needs pandas and openpyxl") from error
    return dataframe_to_records(pd.read_excel(path, sheet_name=sheet, dtype=str))
```

Converting a DataFrame to plain Python immediately:

```python
frame = frame.where(frame.notna(), None)      # NaN -> None
records = frame.to_dict(orient="records")     # list[dict]
```

`dtype=str` on read keeps identifiers like `0012` from becoming `12`.

A test can enforce the boundary by reading the source tree:

```python
for path in (root / "src" / "cdl").rglob("*.py"):
    if path.parent.name != "treats":
        assert "import pandas" not in path.read_text(encoding="utf-8")
```

---

## 9. logging

```python
logger = logging.getLogger("cdl")            # one named tree; children are "cdl.store"
logger.setLevel(logging.INFO)
handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
logger.addHandler(handler)
logger.propagate = False                     # do not also print via the root logger
```

- Levels can differ per handler: the file gets INFO, the console only WARNING, so log
  lines do not bury the decision a trader is reading (`handler.setLevel(...)`).
- Lazy formatting - `logger.info("rows=%d", n)`, not `f"rows={n}"` - so the string is
  only built if the record is emitted.
- `propagate = False` breaks pytest's `caplog`, which listens on the root logger. The
  fix in `tests/conftest.py` is a fixture that turns propagation back on:

```python
logger = logging.getLogger("cdl")
logger.propagate = True
yield
```

- Never log a secret. Here the library name is masked before any SQL is logged:
  `text.replace(library, "<LIBRARY>")`.

---

## 10. Detecting that a placeholder function was replaced

The company connector is pasted in by the operator. To report "not pasted yet"
honestly, read the function's own source:

```python
import inspect

_PLACEHOLDER_MARKER = "PASTE THE COMPANY IMPLEMENTATION HERE"

def connector_is_pasted() -> bool:
    try:
        return _PLACEHOLDER_MARKER not in inspect.getsource(query_to_dataframe)
    except (OSError, TypeError):
        return True          # source not available (frozen build): assume it is real
```

---

## 11. argparse

```python
parser = argparse.ArgumentParser(prog="cdl", description="...")
subparsers = parser.add_subparsers(dest="command", required=True)

check = subparsers.add_parser("check", help="full decision")
check.add_argument("--user", required=True)
check.add_argument("--no-hold", action="store_true")      # flag -> args.no_hold
check.add_argument("--hold-id", type=int, dest="hold_id") # --hold-id -> args.hold_id
check.set_defaults(func=command_check)                    # dispatch without if/elif

args = parser.parse_args(argv)
return int(args.func(args))
```

- `set_defaults(func=...)` is the tidy way to dispatch subcommands.
- Return an exit code from `main()` and call it as `sys.exit(main())`; argparse itself
  raises `SystemExit(2)` on a bad command line.
- `choices=[...]` gives free validation and a readable error.

---

## 12. tkinter

```python
import tkinter as tk
from tkinter import ttk, messagebox

root = tk.Tk()
root.title("...")
frame = ttk.Frame(root, padding=8)
frame.grid(sticky="nsew")
root.columnconfigure(0, weight=1)     # let column 0 absorb extra width
root.rowconfigure(0, weight=1)
```

- `ttk` widgets are the themed ones; use `tk.Label` only when you need something ttk
  will not style, such as a 40-point coloured letter (`fg=`).
- `grid` needs `columnconfigure`/`rowconfigure` with `weight=1` for anything to
  stretch. `sticky="nsew"` means "fill the cell".
- Widget state: `widget.configure(state="disabled")` / `"normal"`.
- A variable binds a widget to Python: `var = tk.StringVar(value="FX")`,
  `ttk.Entry(frame, textvariable=var)`, then `var.get()`.
- Tables are `ttk.Treeview(parent, columns=[...], show="headings")`; fill with
  `insert("", "end", values=[...])` and clear with `delete(*get_children())`.
- Events: `widget.bind("<Return>", lambda event: submit())`,
  `"<<TreeviewSelect>>"` for a selection change.
- Building the window without `mainloop()` makes it testable:
  `root.update()` processes pending events once, then `root.destroy()`.
- Headless machines have no display. `tkinter.TclError` on `Tk()` is the signal to
  skip; under Linux CI, `xvfb-run python -m pytest` provides a virtual display.

---

## 13. pytest

```ini
# pytest.ini
[pytest]
pythonpath = src        # import cdl.* without installing the package
testpaths = tests
addopts = -q
```

Fixtures and parametrisation used here:

```python
@pytest.fixture(autouse=True)                       # applies to every test
def _no_local_config(monkeypatch):
    monkeypatch.setenv("CDL_CONFIG", str(example))  # undone after each test

@pytest.fixture
def settings(tmp_path, monkeypatch):                # tmp_path is per-test and unique
    monkeypatch.setenv("CDL_STORE_DB_PATH", str(tmp_path / "x.db"))
    return load_settings()

@pytest.mark.parametrize(("raw", "expected"), [("1M", "1 months"), ("spot", "Spot")])
def test_alias(raw, expected): ...

def test_error_message():
    with pytest.raises(UnknownTenorError) as error:
        normalise_tenor("banana")
    assert "Spot" in str(error.value)

assert usage == pytest.approx(509_000.0)            # float comparison
pd = pytest.importorskip("pandas")                  # skip if the dependency is absent
```

- `monkeypatch.setenv/setattr/delenv` is automatically reverted; never edit
  `os.environ` directly in a test.
- `capsys.readouterr().out` captures printed output - how the CLI is tested.
- Parametrising over a real constant (`@pytest.mark.parametrize("label",
  constants.TENOR_GRID)`) turns one assertion into 89 named cases.

Running a subprocess in a test (the parity test runs the standalone prototype):

```python
completed = subprocess.run(
    [sys.executable, str(script), "--mock", "--json"],
    capture_output=True, text=True, cwd=tmp_path, check=False,
)
assert completed.returncode == 0, completed.stdout + completed.stderr
payload = json.loads(completed.stdout.strip().splitlines()[-1])
```

`sys.executable` is the interpreter running the tests, so the subprocess uses the same
environment; `cwd=tmp_path` keeps the file the script writes out of the repository.

---

## 14. Small Python details worth remembering

```python
from __future__ import annotations      # postponed annotations: "X | None" on 3.9

text.endswith(("week", "weeks"))        # endswith/startswith accept a tuple
"ABCD".isalnum() and "ABCD".isascii()   # cheap validation, no regex
f"{15_991_000 / 1e6:,.2f}mm"            # '15.99mm' - thousands separator + precision
f"{value:<12}" / f"{value:>12}"         # left / right pad for aligned console tables
notional != notional                    # the only True-for-NaN test without math.isnan
raise FfrError(...) from error          # keep the original traceback as __cause__
```

`try/except/else/finally` with a generator-based context manager:

```python
from contextlib import contextmanager

@contextmanager
def connect(self):
    connection = sqlite3.connect(...)
    try:
        yield connection
    finally:
        connection.close()      # runs even if the body raises
```

Sorting quarter columns as strings works because `2025Q1 < 2025Q2 < 2026Q1`
lexicographically - a fixed-width format is worth choosing for exactly this reason:

```python
quarters = sorted(c for c in columns if re.match(r"^20\d\dQ[1-4]$", c))
chosen = quarters[-1]           # highest-sorting = most recent
```

---

## 15. Command line / git

```bash
python3 -m pytest                       # -m runs a module as a script
python3 -m pytest tests/test_ffr.py -k fallback -vv
PYTHONPATH=src python3 -m cdl.cli doctor
echo $?                                 # exit code of the last command

xvfb-run python3 -m pytest tests/test_ui.py   # virtual X display for tkinter
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

Windows batch equivalents used by `scripts/`:

```bat
setlocal                     rem variables stay inside this script
set ROOT=%~dp0..             rem %~dp0 = folder of the script, with trailing backslash
pushd "%ROOT%" & popd        rem change directory and come back
%PYTHON% -m cdl.cli %*       rem %* = forward every argument
if errorlevel 1 goto :failed rem "errorlevel N" means "N or higher"
endlocal & exit /b %EXITCODE%
^                            rem line continuation (the Windows "\")
```

git, as used in this project:

```bash
git checkout -b cursor/build-all-milestones-0ed9
git add -A && git commit -m "M2: ..."
git push -u origin cursor/build-all-milestones-0ed9
git log --oneline -5
```

One commit per logical change (one per milestone here), and the requirements
documents in the very first commit so no context is lost if a session ends.

## 16. Cloud Agent development environment

The repo runs on the Cursor Cloud Agent default image (Ubuntu 24.04, Python
3.12). Setup lives in `.cursor/`:

- `.cursor/environment.json` - `{"name": ..., "install": "bash .cursor/install.sh"}`.
  A committed `environment.json` is repo-file managed and takes precedence over any
  dashboard/DB config, so the environment follows the branch.
- `.cursor/install.sh` - idempotent bootstrap:

```bash
sudo apt-get update
sudo apt-get install -y --no-install-recommends python3-tk python3-venv
python3 -m venv --system-site-packages .venv     # only if .venv is absent
.venv/bin/python -m pip install -r requirements.txt "pytest>=8.0"
```

Two things that are easy to get wrong:

- **tkinter is a system package, not a wheel.** `pip install tkinter` does not
  exist. Install `python3-tk` with apt, then create the venv with
  `--system-site-packages` so the apt-provided `tkinter` is importable inside
  `.venv`. Without that flag the window raises `ModuleNotFoundError: tkinter`.
- **The GUI needs an X display.** In the cloud VM one already runs at
  `DISPLAY=:1`; locally use `xvfb-run` (see section 15). `python -m cdl.ui.app`
  reads `DISPLAY`; `build_window` raises `tkinter.TclError` when none is present,
  which `test_ui.py` turns into a `skip`.

### 16.1 Gotcha: the agent artifacts directory is a slow FUSE mount

`/opt/cursor/artifacts` is backed by a FUSE agent-store, not local disk. A single
`cp` there can take ~10 s, and a process that writes *incrementally* to a file on
that mount (e.g. `pytest > /opt/cursor/artifacts/run.log`, which flushes progress
dots) can stall for a long time. Always write logs to local disk first and copy
the finished file once:

```bash
python3 -m pytest > /tmp/run.log 2>&1        # fast: local tmpfs
cp /tmp/run.log /opt/cursor/artifacts/       # one write to the slow mount
```
