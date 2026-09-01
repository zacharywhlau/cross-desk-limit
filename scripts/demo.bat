@echo off
rem The ten minute demo of docs\DEMO.md, on mock data only.
rem Each command is echoed before it runs.
setlocal
set ROOT=%~dp0..
set PYTHONPATH=%ROOT%\src

if exist "%ROOT%\.venv\Scripts\python.exe" (
    set PYTHON="%ROOT%\.venv\Scripts\python.exe"
) else (
    set PYTHON=python
)

rem A demo database of its own, so the desk's holds and history are left alone.
set CDL_STORE_DB_PATH=%ROOT%\data\demo_cross_desk_limit.db
if exist "%CDL_STORE_DB_PATH%" del /q "%CDL_STORE_DB_PATH%"

pushd "%ROOT%"

echo.
echo ==============================================================
echo   0. Everything is mock: no endpoint, no desk data
echo ==============================================================
%PYTHON% -m cdl.cli doctor

echo.
echo ==============================================================
echo   1. A deal that fits: Y, with the FFR weight and usage behind it
echo ==============================================================
%PYTHON% -m cdl.cli check --user edmund --cpty ABCDEFG --product FX ^
    --tenor "1 months" --pair USDHKD --notional 500000

echo.
echo ==============================================================
echo   2. The same name, nearly exhausted short end: a hard N
echo ==============================================================
%PYTHON% -m cdl.cli check --user edmund --cpty EFGHIJK --product FX ^
    --tenor 1M --pair USDHKD --notional 500000

echo.
echo ==============================================================
echo   3. No limit beyond five years, so the long end is closed
echo ==============================================================
%PYTHON% -m cdl.cli check --user edmund --cpty ABCDEFG --product FX ^
    --tenor 10Y --pair USDHKD --notional 100000

echo.
echo ==============================================================
echo   4. A second trader claims capacity on the same counterparty
echo ==============================================================
%PYTHON% -m cdl.cli check --user olivia --cpty ABCDEFG --product FX ^
    --tenor 3M --pair EURUSD --notional 4000000
%PYTHON% -m cdl.cli peers --cpty ABCDEFG

echo.
echo ==============================================================
echo   5. Only the username that created a hold may release it
echo ==============================================================
%PYTHON% -m cdl.cli release --hold-id 1 --user olivia
%PYTHON% -m cdl.cli release --hold-id 1 --user edmund

echo.
echo ==============================================================
echo   6. Today's checks, both outcomes recorded
echo ==============================================================
%PYTHON% -m cdl.cli history

echo.
echo ==============================================================
echo   Done. report.html holds the last breakdown.
echo   Open the window with:  scripts\run_app.bat
echo ==============================================================

popd
endlocal
