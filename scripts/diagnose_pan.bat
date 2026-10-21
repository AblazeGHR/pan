@echo off
setlocal
set "PAN_DIAGNOSTIC_PYTHON=%~dp0..\.venv\Scripts\python.exe"
if not exist "%PAN_DIAGNOSTIC_PYTHON%" set "PAN_DIAGNOSTIC_PYTHON=python"
"%PAN_DIAGNOSTIC_PYTHON%" "%~dp0diagnose_pan.py" %*
set "PAN_DIAGNOSTIC_RESULT=%ERRORLEVEL%"
if "%~1"=="" pause
exit /b %PAN_DIAGNOSTIC_RESULT%
