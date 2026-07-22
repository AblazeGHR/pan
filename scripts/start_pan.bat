@echo off
setlocal EnableExtensions
cls

REM ============================================================
REM  Pan launcher
REM ============================================================

pushd "%~dp0.."
set "BASE_DIR=%CD%"
popd
set "SCRIPT_DIR=%~dp0"
set "PID_FILE=%BASE_DIR%\data\process.pid"

mkdir "%BASE_DIR%\data" 2>nul

REM ---- 1. Clean Python cache ----
for /d /r "%BASE_DIR%" %%d in (__pycache__) do (
    if exist "%%d" rmdir /s /q "%%d" 2>nul
)
del /s /f /q "%BASE_DIR%\*.pyc" 2>nul
del /s /f /q "%BASE_DIR%\*.pyo" 2>nul

set "ACTIVATE=%BASE_DIR%\.venv\Scripts\activate.bat"
if not exist "%ACTIVATE%" (
    echo [ERROR] Virtual env not found: %ACTIVATE%
    pause
    exit /b 1
)

set "MAIN_PY=%BASE_DIR%\main.py"
set "PID_MAIN=%BASE_DIR%\data\main_pid.txt"
set "PID_CF=%BASE_DIR%\data\cf_pid.txt"

REM ---- 2. Start main.py ----
powershell -NoProfile -File "%SCRIPT_DIR%start_main.ps1" -Activate "%ACTIVATE%" -MainPy "%MAIN_PY%" -WorkDir "%BASE_DIR%" -PidFile "%PID_MAIN%"
timeout /t 1 /nobreak >nul
set /p MAIN_PID=<"%PID_MAIN%"

REM ---- 3. Wait for server ----
timeout /t 0 /nobreak >nul

REM ---- 4. Start cloudflared (optional) ----
where cloudflared >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [WARN] cloudflared not found in PATH, skipping remote tunnel.
) else (
    if not defined PAN_CF_CONFIG set "PAN_CF_CONFIG=%USERPROFILE%\.cloudflared\config-test.yml"
    powershell -NoProfile -File "%SCRIPT_DIR%start_cf.ps1" -PidFile "%PID_CF%" -ConfigPath "%PAN_CF_CONFIG%"
    set /p CF_PID=<"%PID_CF%"
)

REM ---- 5. Save PIDs ----
echo MAIN=%MAIN_PID% > "%PID_FILE%"
if defined CF_PID echo CF=%CF_PID% >> "%PID_FILE%"

del "%PID_MAIN%" 2>nul
del "%PID_CF%" 2>nul
endlocal
