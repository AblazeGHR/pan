@echo off
setlocal
set "PAN_STOP_PYTHON=%~dp0..\.venv\Scripts\python.exe"
if not exist "%PAN_STOP_PYTHON%" (
    echo [ERROR] Pan checkout Python is missing. Use the registered pan exit command.
    exit /b 1
)
if /I "%~1"=="--diagnose" (
    call "%~dp0diagnose_pan.bat" --root "%~dp0.."
    if errorlevel 1 echo [WARN] Diagnostic capture failed; continuing requested stop.
)
"%PAN_STOP_PYTHON%" "%~dp0pan_cli.py" --root "%~dp0.." exit
exit /b %ERRORLEVEL%
