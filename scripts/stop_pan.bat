@echo off
setlocal EnableExtensions

REM ============================================================
REM  Pan stopper
REM ============================================================

pushd "%~dp0.."
set "BASE_DIR=%CD%"
popd
set "PID_FILE=%BASE_DIR%\data\process.pid"

if not exist "%PID_FILE%" (
    echo [ERROR] PID file not found: %PID_FILE%
    exit /b 1
)

for /f "tokens=1,2 delims==" %%a in (%PID_FILE%) do (
    if /i "%%a"=="MAIN" set MAIN_PID=%%b
    if /i "%%a"=="CF"   set CF_PID=%%b
)

REM Kill main.py
if defined MAIN_PID (
    taskkill /PID %MAIN_PID% /T /F >nul 2>&1
    if errorlevel 1 taskkill /IM python.exe /T /F >nul 2>&1
) else (
    taskkill /IM python.exe /T /F >nul 2>&1
)

REM Kill cloudflared
if defined CF_PID (
    taskkill /PID %CF_PID% /T /F >nul 2>&1
    if errorlevel 1 taskkill /IM cloudflared.exe /T /F >nul 2>&1
) else (
    taskkill /IM cloudflared.exe /T /F >nul 2>&1
)

del "%PID_FILE%" 2>nul
endlocal
