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
set "PAN_STOP_BASE=%BASE_DIR%"

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
    powershell -NoProfile -Command "$base=$env:PAN_STOP_BASE; $p=Get-CimInstance Win32_Process -Filter \"ProcessId=%QQ_PID%\"; if ($p -and $p.CommandLine -and $p.CommandLine -match [regex]::Escape($base) -and $p.CommandLine -match 'bot\.py') { exit 0 }; exit 1" >nul 2>&1
    if errorlevel 1 (
        echo [WARN] Recorded QQ pid does not belong to this Pan checkout, skipping PID=%QQ_PID%
    ) else (
        taskkill /PID %QQ_PID% /T /F >nul 2>&1 && echo [OK] QQ bridge killed, PID=%QQ_PID%
    )
) else (
    echo [INFO] No QQ pid recorded, will match by command line below...
)

REM ---- 3. Kill Pan Core (process tree includes the QQ bot child) ----
if defined MAIN_PID (
    powershell -NoProfile -Command "$base=$env:PAN_STOP_BASE.Replace('\','/').TrimEnd('/'); $root=$base+'/'; $p=Get-CimInstance Win32_Process -Filter \"ProcessId=%MAIN_PID%\"; if ($p -and $p.Name -match '^(python|pythonw|uvicorn)(\.exe)?$' -and $p.CommandLine -and $p.CommandLine.Replace('\','/').Contains($root) -and (($p.CommandLine -match 'main\.py') -or ($p.CommandLine -match 'packages[\\/]web[\\/]server') -or ($p.CommandLine -match 'uvicorn'))) { exit 0 }; exit 1" >nul 2>&1
    if errorlevel 1 (
        echo [WARN] Recorded MAIN pid does not belong to this Pan checkout, skipping PID=%MAIN_PID%
    ) else (
        taskkill /PID %MAIN_PID% /T /F >nul 2>&1 && echo [OK] Pan Core killed, PID=%MAIN_PID%
    )
) else (
    echo [WARN] No MAIN pid recorded, matching by command line...
)

REM ---- 4. Kill cloudflared tunnel (optional service) ----
if defined CF_PID (
    powershell -NoProfile -Command "$p=Get-CimInstance Win32_Process -Filter \"ProcessId=%CF_PID%\"; if ($p -and $p.Name -match '^cloudflared(\.exe)?$' -and $p.CommandLine -and $p.CommandLine -match 'pan_cf_(config|quick)_') { exit 0 }; exit 1" >nul 2>&1
    if errorlevel 1 (
        echo [WARN] Recorded CF pid is not Pan's marked tunnel, skipping PID=%CF_PID%
    ) else (
        taskkill /PID %CF_PID% /T /F >nul 2>&1 && echo [OK] cloudflared killed, PID=%CF_PID%
    )
)

REM ---- 5. Fallback: precise command-line match, NEVER kill all python.exe ----
REM     5a. main.py whose command line contains this project root
powershell -NoProfile -Command "$base=$env:PAN_STOP_BASE.Replace('\','/').TrimEnd('/'); $root=$base+'/'; $p = Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(python|pythonw|uvicorn)(\.exe)?$' -and $_.CommandLine -and $_.CommandLine.Replace('\','/').Contains($root) -and (($_.CommandLine -match 'main\.py') -or ($_.CommandLine -match 'packages[\\/]web[\\/]server') -or ($_.CommandLine -match 'uvicorn')) }; if ($p) { $p | ForEach-Object { taskkill /PID $_.ProcessId /T /F 2>$null } }" >nul 2>&1

REM     5b. QQ bridge bot.py — runs under an interpreter OUTSIDE the project
REM         .venv (nonebot lives there). Resolve the same way main.py does:
REM         PAN_QQ_PYTHON env > config.json qq.python > E-drive miniforge
REM         fallback. Require BOTH bot.py and that interpreter's directory
REM         (bare "python" interpreters fall back to the project root, since
REM         bot.py's own path already contains it).
powershell -NoProfile -Command "$py=''; if ($env:PAN_QQ_PYTHON) { $py=$env:PAN_QQ_PYTHON } else { try { $c=Get-Content -Raw '%BASE_DIR%\config.json' | ConvertFrom-Json; if ($c.qq.python) { $py=$c.qq.python } } catch {} }; if (-not $py) { $py='E:\software\miniforge\python.exe' }; $frag=Split-Path -Parent $py; if (-not $frag) { $frag='%BASE_DIR%' }; $p = Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -and $_.CommandLine -match 'bot\.py' -and $_.CommandLine -match [regex]::Escape($frag) }; if ($p) { $p | ForEach-Object { taskkill /PID $_.ProcessId /T /F 2>$null } }" >nul 2>&1

REM     5c. cloudflared belonging to Pan: command line carries either the
REM         named-tunnel temp yml marker (pan_cf_config_) or the quick-tunnel
REM         logfile marker (pan_cf_quick_) — never a bare service config
powershell -NoProfile -Command "$p = Get-CimInstance Win32_Process -Filter \"Name='cloudflared.exe'\" | Where-Object { $_.CommandLine -and $_.CommandLine -match 'pan_cf_(config|quick)_' }; if ($p) { $p | ForEach-Object { taskkill /PID $_.ProcessId /T /F 2>$null } }" >nul 2>&1

REM ---- 6. Clean up pid files ----
REM ---- 6a. A successful taskkill is not enough: no target-port listener
REM      may remain, and a listener owned by another checkout is a failure.
set "PAN_STOP_PORT="
for /f "delims=" %%p in ('powershell -NoProfile -Command "$cfg=Join-Path $env:PAN_STOP_BASE 'config.json'; try { if (Test-Path -LiteralPath $cfg) { $c=Get-Content -LiteralPath $cfg -Raw | ConvertFrom-Json; if ($c.port) { $c.port } else { 8768 } } else { 8768 } } catch { 8768 }"') do set "PAN_STOP_PORT=%%p"
if not defined PAN_STOP_PORT set "PAN_STOP_PORT=8768"
powershell -NoProfile -Command "$base=$env:PAN_STOP_BASE.Replace('\','/').TrimEnd('/'); $root=$base+'/'; $bad=@(); try { $ls=Get-NetTCPConnection -LocalPort ([int]$env:PAN_STOP_PORT) -State Listen -ErrorAction SilentlyContinue; foreach ($l in $ls) { $p=Get-CimInstance Win32_Process -Filter ('ProcessId='+$l.OwningProcess); if (-not $p -or -not $p.CommandLine -or -not $p.CommandLine.Replace('\','/').Contains($root) -or (($p.CommandLine -notmatch 'main\.py') -and ($p.CommandLine -notmatch 'packages[\\/]web[\\/]server') -and ($p.CommandLine -notmatch 'uvicorn'))) { $bad += $l.OwningProcess } }; $remaining=Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^(python|pythonw|uvicorn)(\.exe)?$' -and $_.CommandLine -and $_.CommandLine.Replace('\','/').Contains($root) -and (($_.CommandLine -match 'main\.py') -or ($_.CommandLine -match 'packages[\\/]web[\\/]server') -or ($_.CommandLine -match 'uvicorn')) }; if ($remaining) { $bad += @($remaining | ForEach-Object { $_.ProcessId }) } } catch { $bad += 'listener-check-error' }; if ($bad.Count) { exit 1 }" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Pan listener on port %PAN_STOP_PORT% remains or has an unverified owner.
    endlocal
    exit /b 1
)
if exist "%PID_FILE%" del "%PID_FILE%" 2>nul
if exist "%QQ_PID_FILE%" del "%QQ_PID_FILE%" 2>nul

echo [OK] Pan stopped.
endlocal
