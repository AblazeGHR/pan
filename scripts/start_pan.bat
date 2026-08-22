@echo off
setlocal EnableExtensions
cls

REM ============================================================
REM  Pan launcher
REM  Starts Pan Core (main.py). The QQ bridge (packages/qq/bot.py)
REM  is spawned by main.py itself when config qq.enabled is true —
REM  no separate start step needed here.
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

set "PYTHON=%BASE_DIR%\.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
    echo [ERROR] Virtual env python not found: %PYTHON%
    pause
    exit /b 1
)

set "MAIN_PY=%BASE_DIR%\main.py"
set "PID_MAIN=%BASE_DIR%\data\main_pid.txt"
set "PID_CF=%BASE_DIR%\data\cf_pid.txt"

REM ---- 2. Start main.py ----
powershell -NoProfile -File "%SCRIPT_DIR%start_main.ps1" -Python "%PYTHON%" -MainPy "%MAIN_PY%" -WorkDir "%BASE_DIR%" -PidFile "%PID_MAIN%"
timeout /t 1 /nobreak >nul
set /p MAIN_PID=<"%PID_MAIN%"
echo [OK] Pan Core started, PID=%MAIN_PID%

REM ---- 3. Wait for server to come up ----
timeout /t 2 /nobreak >nul

REM ---- 4. Start cloudflared (optional) ----
set "CF_PID="
where cloudflared >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [WARN] cloudflared not found in PATH, skipping remote tunnel.
) else (
    powershell -NoProfile -File "%SCRIPT_DIR%start_cf.ps1" -PidFile "%PID_CF%"
    set /p CF_PID=<"%PID_CF%"
    echo [OK] cloudflared started, PID=%CF_PID%
)

REM ---- 5. Save PIDs ----
echo MAIN=%MAIN_PID% > "%PID_FILE%"
if defined CF_PID echo CF=%CF_PID% >> "%PID_FILE%"

del "%PID_MAIN%" 2>nul
del "%PID_CF%" 2>nul
echo [OK] Pan started. Stop with scripts\stop_pan.bat
endlocal
