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
set "PAN_START_BASE=%BASE_DIR%"

REM ---- 0. Refuse duplicate Pan instances before touching caches/PIDs ----
REM     Match the project root and main.py, never a bare python.exe.
for /f "delims=" %%p in ('powershell -NoProfile -Command "$base=$env:PAN_START_BASE.Replace('\','/').TrimEnd('/'); $p=Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -and $_.CommandLine.Replace('\','/').Contains($base) -and $_.CommandLine.Contains('main.py') }; if ($p) { $p | Select-Object -First 1 -ExpandProperty ProcessId }"') do set "EXISTING_MAIN_PID=%%p"
if defined EXISTING_MAIN_PID (
    echo [ERROR] Pan Core is already running for this checkout, PID=%EXISTING_MAIN_PID%
    exit /b 2
)

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
    exit /b 1
)

set "MAIN_PY=%BASE_DIR%\main.py"
set "PID_MAIN=%BASE_DIR%\data\main_pid.txt"
set "PID_CF=%BASE_DIR%\data\cf_pid.txt"
set "MAIN_PID="
set "CF_PID="
del "%PID_MAIN%" "%PID_CF%" 2>nul

REM ---- 2. Resolve the port used by main.py for the readiness check ----
if not defined PAN_PORT (
    for /f "delims=" %%p in ('powershell -NoProfile -Command "$cfgPath=Join-Path $env:PAN_START_BASE 'config.json'; if (Test-Path -LiteralPath $cfgPath) { $cfg=Get-Content -LiteralPath $cfgPath -Raw | ConvertFrom-Json; if ($cfg.port) { $cfg.port } else { 8768 } } else { 8768 }"') do set "PAN_PORT=%%p"
)
if not defined PAN_PORT set "PAN_PORT=8768"

REM ---- 2. Start main.py ----
powershell -NoProfile -File "%SCRIPT_DIR%start_main.ps1" -Python "%PYTHON%" -MainPy "%MAIN_PY%" -WorkDir "%BASE_DIR%" -PidFile "%PID_MAIN%"
if errorlevel 1 (
    echo [ERROR] Failed to launch Pan Core.
    goto :start_failed
)
if not exist "%PID_MAIN%" (
    echo [ERROR] Pan Core launcher did not write a PID file: %PID_MAIN%
    goto :start_failed
)
set /p MAIN_PID=<"%PID_MAIN%"
if not defined MAIN_PID (
    echo [ERROR] Pan Core launcher wrote an empty PID file: %PID_MAIN%
    goto :start_failed
)
set "PAN_MAIN_PID=%MAIN_PID%"
powershell -NoProfile -Command "$p=Get-Process -Id $env:PAN_MAIN_PID -ErrorAction SilentlyContinue; if (-not $p) { exit 1 }" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Pan Core exited immediately, PID=%MAIN_PID%.
    goto :start_failed
)
echo [OK] Pan Core process started, PID=%MAIN_PID%

REM ---- 3. Wait for the HTTP API to come up ----
for /l %%i in (1,1,30) do (
    powershell -NoProfile -Command "$url='http://127.0.0.1:'+$env:PAN_PORT+'/api/sessions?summary=1'; try { Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }" >nul 2>&1
    if not errorlevel 1 goto :server_ready
    powershell -NoProfile -Command "Start-Sleep -Seconds 1" >nul 2>&1
)
echo [ERROR] Pan Core did not become ready on port %PAN_PORT% within 30 seconds.
goto :start_failed

:server_ready
echo [OK] Pan Core API ready on 127.0.0.1:%PAN_PORT%

REM ---- 4. Start cloudflared (optional) ----
where.exe cloudflared >nul 2>&1
if errorlevel 1 (
    echo [WARN] cloudflared not found in PATH, skipping remote tunnel.
) else (
    powershell -NoProfile -File "%SCRIPT_DIR%start_cf.ps1" -PidFile "%PID_CF%"
    if errorlevel 1 (
        echo [WARN] cloudflared failed to start, continuing with Pan Core only.
    ) else if exist "%PID_CF%" set /p CF_PID=<"%PID_CF%"
)
if defined CF_PID echo [OK] cloudflared started, PID=%CF_PID%

REM ---- 5. Save PIDs ----
echo MAIN=%MAIN_PID% > "%PID_FILE%"
if defined CF_PID echo CF=%CF_PID% >> "%PID_FILE%"

del "%PID_MAIN%" "%PID_CF%" 2>nul
echo [OK] Pan started. Stop with scripts\stop_pan.bat
endlocal
exit /b 0

:start_failed
if defined MAIN_PID taskkill /PID "%MAIN_PID%" /T /F >nul 2>&1
del "%PID_MAIN%" "%PID_CF%" 2>nul
endlocal
exit /b 1
