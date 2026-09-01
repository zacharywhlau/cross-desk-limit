#!/usr/bin/env bash
# Cloud Agent bootstrap for cross-desk-limit.
# Idempotent: installs system tkinter, creates .venv (with system site-packages
# so the desk's tkinter GUI is importable) and installs the runtime + dev deps.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

SUDO=""
if command -v sudo >/dev/null 2>&1 && [ "$(id -u)" -ne 0 ]; then
    SUDO="sudo"
fi

export DEBIAN_FRONTEND=noninteractive
$SUDO apt-get update -qq
# python3-tk: the tkinter window (src/cdl/ui/app.py). python3-venv: the .venv below.
$SUDO apt-get install -y --no-install-recommends python3-tk python3-venv

if [ ! -x "$ROOT/.venv/bin/python" ]; then
    # --system-site-packages exposes the apt-installed tkinter inside the venv.
    python3 -m venv --system-site-packages "$ROOT/.venv"
fi

"$ROOT/.venv/bin/python" -m pip install --upgrade pip
# Runtime deps (pandas data boundary, openpyxl Excel/xlsx path) plus pytest for the suite.
"$ROOT/.venv/bin/python" -m pip install -r "$ROOT/requirements.txt" "pytest>=8.0"

"$ROOT/.venv/bin/python" -c "import pandas, openpyxl, pytest, tkinter; print('cross-desk-limit env ready:', 'pandas', pandas.__version__, 'openpyxl', openpyxl.__version__, 'tk', tkinter.TkVersion)"
