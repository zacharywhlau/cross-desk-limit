#!/usr/bin/env bash
# The ten minute demo of docs/DEMO.md, on mock data only.
# Each command is printed before it runs; the first failure stops the script.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT/src"

PYTHON="python3"
if [ -x "$ROOT/.venv/bin/python" ]; then
    PYTHON="$ROOT/.venv/bin/python"
fi

# A demo database of its own, so the desk's holds and history are left alone.
DEMO_DB="${DEMO_DB:-$ROOT/data/demo_cross_desk_limit.db}"
export CDL_STORE_DB_PATH="$DEMO_DB"
rm -f "$DEMO_DB"

step() {
    echo
    echo "=============================================================="
    echo "  $1"
    echo "=============================================================="
}

run() {
    echo
    echo "\$ $*"
    "$@"
}

# The demo shows both outcomes, so a non-zero exit from `check` on an N is expected.
run_expect_reject() {
    echo
    echo "\$ $*"
    if "$@"; then
        echo "expected a rejection here" >&2
        exit 1
    fi
}

step "0. Everything is mock: no endpoint, no desk data"
run "$PYTHON" -m cdl.cli doctor

step "1. A deal that fits: Y, with the FFR weight and the usage behind it"
run "$PYTHON" -m cdl.cli check --user edmund --cpty ABCDEFG --product FX \
    --tenor "1 months" --pair USDHKD --notional 500000

step "2. The same name, nearly exhausted short end: a hard N"
run "$PYTHON" -m cdl.cli check --user edmund --cpty EFGHIJK --product FX \
    --tenor 1M --pair USDHKD --notional 500000 || true

step "3. No limit beyond five years, so the long end is closed"
run "$PYTHON" -m cdl.cli check --user edmund --cpty ABCDEFG --product FX \
    --tenor 10Y --pair USDHKD --notional 100000 || true

step "4. A second trader claims capacity on the same counterparty"
run "$PYTHON" -m cdl.cli check --user olivia --cpty ABCDEFG --product FX \
    --tenor 3M --pair EURUSD --notional 4000000
run "$PYTHON" -m cdl.cli peers --cpty ABCDEFG

step "5. Only the username that created a hold may release it"
run_expect_reject "$PYTHON" -m cdl.cli release --hold-id 1 --user olivia
run "$PYTHON" -m cdl.cli release --hold-id 1 --user edmund

step "6. Today's checks, both outcomes recorded"
run "$PYTHON" -m cdl.cli history

step "7. All four products are reachable"
for product in FX Gold IRS "Equity swaps"; do
    run "$PYTHON" -m cdl.cli check --user edmund --cpty ABCD --product "$product" \
        --tenor 6M --pair USD --notional 100000 --no-hold
done

echo
echo "=============================================================="
echo "  Done. report.html holds the last breakdown."
echo "  Open the window with:  scripts/run_app.sh"
echo "  Demo database:         $DEMO_DB"
echo "=============================================================="
