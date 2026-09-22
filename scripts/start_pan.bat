@echo off
setlocal EnableExtensions
REM Compatibility entry point: all startup decisions live in the Python launcher.
pushd "%~dp0.."
set "PAN_ROOT=%CD%"

REM Bootstrap with the checkout interpreter when present. launcher.start then
REM applies config.json python > PAN_PYTHON > .venv > PATH and re-execs itself
REM when the configured interpreter differs from this bootstrap process.
if exist "%PAN_ROOT%\.venv\Scripts\python.exe" (
    "%PAN_ROOT%\.venv\Scripts\python.exe" -m packages.core.launcher start --root "%PAN_ROOT%"
) else (
    python -m packages.core.launcher start --root "%PAN_ROOT%"
)
set "PAN_EXIT=%ERRORLEVEL%"
popd
endlocal & exit /b %PAN_EXIT%
