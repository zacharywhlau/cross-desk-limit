@echo off
rem Start the tkinter window. Uses the portable venv when one is present.
setlocal
set ROOT=%~dp0..
set PYTHONPATH=%ROOT%\src

if exist "%ROOT%\.venv\Scripts\python.exe" (
    set PYTHON="%ROOT%\.venv\Scripts\python.exe"
) else (
    set PYTHON=python
)

pushd "%ROOT%"
%PYTHON% -m cdl.ui.app %*
set EXITCODE=%ERRORLEVEL%
popd
endlocal & exit /b %EXITCODE%
