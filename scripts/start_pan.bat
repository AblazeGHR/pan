@echo off
setlocal EnableExtensions
REM Quick tunnel URL marker is emitted by start_pan_probe.ps1 from trycloudflare.com.
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
REM     Match the checkout boundary and a known Pan entry marker.  The
REM     launcher may be python/pythonw/uvicorn and need not spell main.py.
for /f "delims=" %%p in ('powershell -NoProfile -File "%SCRIPT_DIR%start_pan_probe.ps1" -Action ExistingMainPid -BaseDir "%BASE_DIR%"') do set "EXISTING_MAIN_PID=%%p"
if defined EXISTING_MAIN_PID (
    echo [ERROR] Pan Core is already running for this checkout, PID=%EXISTING_MAIN_PID%
    exit /b 2
)

mkdir "%BASE_DIR%\data" 2>nul

REM ---- 1. Do not recursively sweep the checkout before startup ----
REM     Python validates bytecode timestamps itself.  A full-tree cache sweep
REM     also walks data\workdirs, frontend dependencies, and other user data;
REM     on a busy checkout it can block long enough to make lifecycle restart
REM     time out before main.py is even launched.

set "PYTHON=%BASE_DIR%\.venv\Scripts\python.exe"
set "LOCAL_PYTHON=%PYTHON%"

REM A copied/incomplete .venv can leave python.exe behind without
REM pyvenv.cfg.  Checking only the file makes Start-Process report success
REM and then the child exits immediately (usually with exit code 106).  Probe
REM the interpreter and every dependency needed by both Pan Core and its
REM stdio MCP server before using it.  Core-only Python is not a valid Pan
REM runtime because manifests resolve ${PAN_PYTHON} to this interpreter.
if exist "%PYTHON%" (
    "%PYTHON%" -c "import fastapi, uvicorn, psutil, mcp.server.fastmcp" >nul 2>&1
    if errorlevel 1 set "PYTHON="
) else (
    set "PYTHON="
)

REM If the checkout-local environment is unavailable, accept the first valid
REM Python on PATH.  Validate every candidate because WindowsApps\python.exe
REM can be an app-execution alias rather than a usable interpreter.
if not defined PYTHON (
    for /f "delims=" %%p in ('where.exe python.exe 2^>nul') do (
        if not defined PYTHON (
            "%%p" -c "import fastapi, uvicorn, psutil, mcp.server.fastmcp" >nul 2>&1
            if not errorlevel 1 set "PYTHON=%%p"
        )
    )
)
if not defined PYTHON (
    echo [ERROR] No usable Python interpreter with Pan Core and MCP dependencies was found.
    echo         Checked: %LOCAL_PYTHON% and python.exe on PATH.
    echo         Repair the environment with scripts\setup.bat.
    exit /b 1
)
if /i not "%PYTHON%"=="%LOCAL_PYTHON%" (
    echo [WARN] Checkout .venv is unavailable; using fallback Python: %PYTHON%
)

set "MAIN_PY=%BASE_DIR%\main.py"
set "PID_MAIN=%BASE_DIR%\data\main_pid.txt"
set "PID_CF=%BASE_DIR%\data\cf_pid.txt"
set "PAN_STDOUT=%BASE_DIR%\data\logs\pan-console.out.log"
set "PAN_STDERR=%BASE_DIR%\data\logs\pan-console.err.log"
set "MAIN_PID="
set "CF_PID="
del "%PID_MAIN%" "%PID_CF%" 2>nul

REM ---- 2. Resolve the port used by main.py for the readiness check ----
if not defined PAN_PORT (
    for /f "delims=" %%p in ('powershell -NoProfile -File "%SCRIPT_DIR%start_pan_probe.ps1" -Action Port -BaseDir "%BASE_DIR%"') do set "PAN_PORT=%%p"
)
if not defined PAN_PORT set "PAN_PORT=8768"

REM ---- 2. Start main.py ----
powershell -NoProfile -File "%SCRIPT_DIR%start_main.ps1" -Python "%PYTHON%" -MainPy "%MAIN_PY%" -WorkDir "%BASE_DIR%" -PidFile "%PID_MAIN%" -StdoutFile "%PAN_STDOUT%" -StderrFile "%PAN_STDERR%"
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
powershell -NoProfile -File "%SCRIPT_DIR%start_pan_probe.ps1" -Action ProcessAlive -BaseDir "%BASE_DIR%" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Pan Core exited immediately, PID=%MAIN_PID%.
    goto :start_failed
)
echo [OK] Pan Core process started, PID=%MAIN_PID%

REM ---- 3. Wait for the HTTP API to come up ----
REM     Keep the retry loop inside one PowerShell probe process.  Spawning a
REM     new PowerShell once per second adds avoidable process startup latency.
powershell -NoProfile -File "%SCRIPT_DIR%start_pan_probe.ps1" -Action WaitReady -BaseDir "%BASE_DIR%" -Port "%PAN_PORT%" -TimeoutSec 30 >nul 2>&1
if errorlevel 1 goto :server_not_ready
goto :server_ready

:server_not_ready
echo [ERROR] Pan Core did not become ready on port %PAN_PORT% within 30 seconds.
goto :start_failed

:server_ready
echo [OK] Pan Core API ready on 127.0.0.1:%PAN_PORT%

REM ---- 4. Start cloudflared (optional) ----
set "PAN_REMOTE_STATE="
for /f "delims=" %%r in ('powershell -NoProfile -File "%SCRIPT_DIR%start_pan_probe.ps1" -Action RemoteState -BaseDir "%BASE_DIR%"') do set "PAN_REMOTE_STATE=%%r"
if not defined PAN_REMOTE_STATE set "PAN_REMOTE_STATE=invalid"
if /i not "%PAN_REMOTE_STATE%"=="enabled" (
    echo [INFO] remote.enabled is not explicitly true ^(%PAN_REMOTE_STATE%^), skipping Cloudflare Tunnel.
    goto :remote_tunnel_done
)
REM ---- 4b. Resolve remote.quick_tunnel (default quick, matches main.py) ----
set "PAN_QUICK_STATE="
for /f "delims=" %%q in ('powershell -NoProfile -File "%SCRIPT_DIR%start_pan_probe.ps1" -Action QuickState -BaseDir "%BASE_DIR%"') do set "PAN_QUICK_STATE=%%q"
if not defined PAN_QUICK_STATE set "PAN_QUICK_STATE=quick"

where.exe cloudflared >nul 2>&1
if errorlevel 1 (
    echo [WARN] cloudflared not found in PATH, skipping remote tunnel.
    goto :remote_tunnel_done
)
set "PAN_CF_QUICK_LOG=%BASE_DIR%\data\logs\pan_cf_quick_%PAN_PORT%.log"
if /i "%PAN_QUICK_STATE%"=="named" (
    powershell -NoProfile -File "%SCRIPT_DIR%start_cf.ps1" -PidFile "%PID_CF%"
    if errorlevel 1 (
        echo [WARN] cloudflared failed to start, continuing with Pan Core only.
    ) else if exist "%PID_CF%" set /p CF_PID=<"%PID_CF%"
) else (
    powershell -NoProfile -File "%SCRIPT_DIR%start_cf_quick.ps1" -PidFile "%PID_CF%" -Port "%PAN_PORT%" -LogFile "%PAN_CF_QUICK_LOG%"
    if errorlevel 1 (
        echo [WARN] cloudflared quick tunnel failed to start, continuing with Pan Core only.
    ) else if exist "%PID_CF%" set /p CF_PID=<"%PID_CF%"
)

if defined CF_PID if /i not "%PAN_QUICK_STATE%"=="named" (
    echo [INFO] Quick tunnel log: %PAN_CF_QUICK_LOG%
    REM Quick tunnel URL marker is emitted by start_pan_probe.ps1 from trycloudflare.com.
    powershell -NoProfile -File "%SCRIPT_DIR%start_pan_probe.ps1" -Action QuickUrl -BaseDir "%BASE_DIR%" -LogFile "%PAN_CF_QUICK_LOG%"
)

:remote_tunnel_done
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
