#!/usr/bin/env bash
# Start the tkinter window (development convenience; the desk runs run_app.bat).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$ROOT/src"
PYTHON="python3"
if [ -x "$ROOT/.venv/bin/python" ]; then
    PYTHON="$ROOT/.venv/bin/python"
fi
cd "$ROOT"
exec "$PYTHON" -m cdl.ui.app "$@"
