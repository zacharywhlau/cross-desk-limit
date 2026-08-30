@echo off
rem The command line: doctor ^| extract ^| check ^| peers ^| history ^| release
rem Examples:
rem   scripts\run_check.bat doctor
rem   scripts\run_check.bat extract --save-cache
rem   scripts\run_check.bat check --user edmund --cpty ABCDEFG --product FX ^
rem       --tenor "1 months" --pair USDHKD --notional 500000
setlocal
set ROOT=%~dp0..
set PYTHONPATH=%ROOT%\src

if exist "%ROOT%\.venv\Scripts\python.exe" (
    set PYTHON="%ROOT%\.venv\Scripts\python.exe"
) else (
    set PYTHON=python
)

pushd "%ROOT%"
%PYTHON% -m cdl.cli %*
set EXITCODE=%ERRORLEVEL%
popd
endlocal & exit /b %EXITCODE%
