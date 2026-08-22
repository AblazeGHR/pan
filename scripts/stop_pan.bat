@echo off
setlocal EnableExtensions

REM ============================================================
REM  Pan stopper — kills only services started by start_pan.bat /
REM  spawned by main.py (incl. the QQ bridge). NEVER kills all
REM  python.exe processes.
REM ============================================================

pushd "%~dp0.."
set "BASE_DIR=%CD%"
popd
set "PID_FILE=%BASE_DIR%\data\process.pid"
set "QQ_PID_FILE=%BASE_DIR%\data\qq_bot.pid"

REM ---- 0. Read QQ bridge PID (written by main.py when it spawns bot.py) ----
set "QQ_PID="
if exist "%QQ_PID_FILE%" set /p QQ_PID=<"%QQ_PID_FILE%"

REM ---- 1. Read MAIN/CF PIDs recorded by start_pan.bat ----
set "MAIN_PID="
set "CF_PID="
if exist "%PID_FILE%" (
    for /f "usebackq tokens=1,2 delims==" %%a in ("%PID_FILE%") do (
        if /i "%%a"=="MAIN" set "MAIN_PID=%%b"
        if /i "%%a"=="CF"   set "CF_PID=%%b"
    )
)

REM ---- 2. Kill QQ bridge first — it may outlive a hard-killed main.py ----
if defined QQ_PID (
    taskkill /PID %QQ_PID% /T /F >nul 2>&1 && echo [OK] QQ bridge killed, PID=%QQ_PID%
) else (
    echo [INFO] No QQ pid recorded, will match by command line below...
)

REM ---- 3. Kill Pan Core (process tree includes the QQ bot child) ----
if defined MAIN_PID (
    taskkill /PID %MAIN_PID% /T /F >nul 2>&1 && echo [OK] Pan Core killed, PID=%MAIN_PID%
) else (
    echo [WARN] No MAIN pid recorded, matching by command line...
)

REM ---- 4. Kill cloudflared tunnel (optional service) ----
if defined CF_PID (
    taskkill /PID %CF_PID% /T /F >nul 2>&1 && echo [OK] cloudflared killed, PID=%CF_PID%
)

REM ---- 5. Fallback: precise command-line match, NEVER kill all python.exe ----
REM     5a. main.py whose command line contains this project root
powershell -NoProfile -Command "$base='%BASE_DIR%'; $p = Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -and $_.CommandLine -match [regex]::Escape($base) -and $_.CommandLine -match 'main\.py' }; if ($p) { $p | ForEach-Object { taskkill /PID $_.ProcessId /T /F 2>$null } }" >nul 2>&1

REM     5b. QQ bridge bot.py — the Pan QQ bot runs under the miniforge
REM         interpreter (project .venv has no nonebot), so require BOTH
REM         bot.py and the miniforge path on the command line.
powershell -NoProfile -Command "$py='E:\software\miniforge'; $p = Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -and $_.CommandLine -match 'bot\.py' -and $_.CommandLine -match [regex]::Escape($py) }; if ($p) { $p | ForEach-Object { taskkill /PID $_.ProcessId /T /F 2>$null } }" >nul 2>&1

REM     5c. cloudflared whose command line uses our temp tunnel config marker
powershell -NoProfile -Command "$p = Get-CimInstance Win32_Process -Filter \"Name='cloudflared.exe'\" | Where-Object { $_.CommandLine -and $_.CommandLine -match 'pan_cf_config_' }; if ($p) { $p | ForEach-Object { taskkill /PID $_.ProcessId /T /F 2>$null } }" >nul 2>&1

REM ---- 6. Clean up pid files ----
if exist "%PID_FILE%" del "%PID_FILE%" 2>nul
if exist "%QQ_PID_FILE%" del "%QQ_PID_FILE%" 2>nul

echo [OK] Pan stopped.
endlocal
