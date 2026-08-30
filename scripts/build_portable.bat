@echo off
rem Build a zip another PC can unzip and run. Traders never run pip.
rem   1. creates .venv in the repository
rem   2. installs the two runtime dependencies (pandas, openpyxl)
rem   3. copies everything needed at runtime into dist\cross-desk-limit
rem   4. zips it
setlocal enabledelayedexpansion
set ROOT=%~dp0..
set DIST=%ROOT%\dist
set STAGE=%DIST%\cross-desk-limit

pushd "%ROOT%"

echo [1/5] creating the virtual environment
if not exist "%ROOT%\.venv\Scripts\python.exe" (
    python -m venv "%ROOT%\.venv" || goto :failed
)

echo [2/5] installing runtime dependencies
"%ROOT%\.venv\Scripts\python.exe" -m pip install --upgrade pip || goto :failed
"%ROOT%\.venv\Scripts\python.exe" -m pip install -r "%ROOT%\requirements.txt" || goto :failed

echo [3/5] staging files
if exist "%STAGE%" rmdir /s /q "%STAGE%"
mkdir "%STAGE%" || goto :failed
xcopy /e /i /q "%ROOT%\src" "%STAGE%\src" >nul || goto :failed
xcopy /e /i /q "%ROOT%\prototype" "%STAGE%\prototype" >nul || goto :failed
xcopy /e /i /q "%ROOT%\scripts" "%STAGE%\scripts" >nul || goto :failed
xcopy /e /i /q "%ROOT%\data\mock_treats" "%STAGE%\data\mock_treats" >nul || goto :failed
xcopy /e /i /q "%ROOT%\docs" "%STAGE%\docs" >nul || goto :failed
xcopy /e /i /q "%ROOT%\.venv" "%STAGE%\.venv" >nul || goto :failed
copy /y "%ROOT%\config.example.ini" "%STAGE%\" >nul || goto :failed
copy /y "%ROOT%\README.md" "%STAGE%\" >nul || goto :failed
copy /y "%ROOT%\REQUIREMENTS.md" "%STAGE%\" >nul || goto :failed
copy /y "%ROOT%\requirements.txt" "%STAGE%\" >nul || goto :failed

echo [4/5] checking the staged copy
set PYTHONPATH=%STAGE%\src
"%STAGE%\.venv\Scripts\python.exe" -m cdl.cli doctor
if errorlevel 1 echo     doctor reported problems - read the output above.

echo [5/5] zipping
powershell -NoProfile -Command "Compress-Archive -Force -Path '%STAGE%' -DestinationPath '%DIST%\cross-desk-limit.zip'" || goto :failed

echo.
echo Done: %DIST%\cross-desk-limit.zip
echo On the other PC: unzip it, copy config.example.ini to config.ini, edit it,
echo then run scripts\run_app.bat
popd
endlocal & exit /b 0

:failed
echo.
echo BUILD FAILED - see the message above.
popd
endlocal & exit /b 1
