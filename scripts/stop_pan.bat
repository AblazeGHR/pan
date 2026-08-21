@echo off
setlocal EnableExtensions

REM ============================================================
REM  Pan stopper — only kills services started by start_pan.bat
REM ============================================================

pushd "%~dp0.."
set "BASE_DIR=%CD%"
popd
set "PID_FILE=%BASE_DIR%\data\process.pid"

REM ---- 1. Prefer exact PIDs recorded by start_pan.bat ----
if exist "%PID_FILE%" (
    for /f "usebackq tokens=1,2 delims==" %%a in ("%PID_FILE%") do (
        if /i "%%a"=="MAIN" set "MAIN_PID=%%b"
        if /i "%%a"=="CF"   set "CF_PID=%%b"
    )
)

if defined MAIN_PID (
    taskkill /PID %MAIN_PID% /T /F >nul 2>&1
) else (
    echo [WARN] No MAIN pid recorded (missing/corrupt %PID_FILE%), matching by command line...
)

REM ---- 2. Fallback: precise command-line match, NEVER kill all python.exe ----
REM     python.exe whose command line contains this project's root and main.py
powershell -NoProfile -Command "$base='%BASE_DIR%'; $p = Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -and $_.CommandLine -match [regex]::Escape($base) -and $_.CommandLine -match 'main\.py' }; if ($p) { $p | ForEach-Object { taskkill /PID $_.ProcessId /T /F 2>$null } }" >nul 2>&1

REM ---- 3. Kill cloudflared started by start_cf.ps1 ----
if defined CF_PID (
    taskkill /PID %CF_PID% /T /F >nul 2>&1
)
REM     Fallback: cloudflared whose command line uses our temp tunnel config marker
powershell -NoProfile -Command "$p = Get-CimInstance Win32_Process -Filter \"Name='cloudflared.exe'\" | Where-Object { $_.CommandLine -and $_.CommandLine -match 'pan_cf_config_' }; if ($p) { $p | ForEach-Object { taskkill /PID $_.ProcessId /T /F 2>$null } }" >nul 2>&1

REM ---- 4. Clean up PID file ----
if exist "%PID_FILE%" del "%PID_FILE%" 2>nul

endlocal
