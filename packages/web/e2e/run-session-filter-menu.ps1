<#
.SYNOPSIS
Isolated real-Chromium E2E for the Session filter menu (grouped, scrollable,
viewport-constrained).

Builds the React app, starts the E2E-owned backend (e2e/server.py) on a
dynamically selected protected-free port, runs
e2e/session-filter-menu.e2e.mjs against real FastAPI routes and the real
/ws broadcast path, then stops the owned server with pid/command/port
identity checks.

Ports 8767 and 8768 are refused. The dynamic port is verified not to be an
existing listener before the server starts; the server itself also refuses
those ports and bind-exclusive-probes its port.
#>
param(
    [string]$Python = 'D:\project\Pan-main\.venv\Scripts\python.exe'
)

$ErrorActionPreference = 'Stop'

# pnpm writes progress/warnings (e.g. vite chunk-size notes) to stderr.
# With ErrorActionPreference=Stop a native stderr write raises
# NativeCommandError and aborts the script; run pnpm with Continue and judge
# only by its exit code.
function Invoke-Pnpm {
    param([string[]]$PnpmArgs)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $captured = & pnpm @PnpmArgs 2>&1
        $code = $LASTEXITCODE
        # Forward captured output without leaking it into the function result.
        $captured | ForEach-Object { Write-Host $_ }
        return $code
    } finally {
        $ErrorActionPreference = $previous
    }
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$webRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$runtime = Join-Path $webRoot 'test-results\pan-e2e-session-filter-menu'
$log = Join-Path $runtime 'server.stdout.log'
$err = Join-Path $runtime 'server.stderr.log'
$identityPath = Join-Path $runtime 'server-identity.json'
$lifecyclePath = Join-Path $runtime 'lifecycle.json'

if (-not (Test-Path -LiteralPath $Python)) { throw "isolated Python is missing: $Python" }

$port = $null
for ($attempt = 0; $attempt -lt 12; $attempt++) {
    $candidate = Get-Random -Minimum 18000 -Maximum 48000
    if ($candidate -eq 8767 -or $candidate -eq 8768) { continue }
    if (Get-NetTCPConnection -LocalPort $candidate -State Listen -ErrorAction SilentlyContinue) { continue }
    $port = $candidate
    break
}
if (-not $port) { throw 'could not select a free dynamic E2E port' }
if ($port -eq 8767 -or $port -eq 8768) { throw "refusing protected port $port" }

if (Test-Path -LiteralPath $runtime) { Remove-Item -LiteralPath $runtime -Recurse -Force }
New-Item -ItemType Directory -Path $runtime | Out-Null

$oldPanPort = $env:PAN_PORT
$oldE2ERuntime = $env:PAN_E2E_RUNTIME
$oldE2EBase = $env:PAN_E2E_BASE_URL
$server = $null
$ownedServerPid = $null
$startedAt = [DateTimeOffset]::UtcNow
try {
    $env:PAN_PORT = "$port"
    $env:PAN_E2E_RUNTIME = $runtime
    $env:PAN_E2E_BASE_URL = "http://127.0.0.1:$port"

    $buildExitCode = Invoke-Pnpm -PnpmArgs @('--dir', $webRoot, 'build')
    if ($buildExitCode -ne 0) { throw "isolated web build failed with exit code $buildExitCode" }

    $server = Start-Process -FilePath $Python `
      -ArgumentList @((Join-Path $webRoot 'e2e\server.py')) `
      -WorkingDirectory $repoRoot `
      -RedirectStandardOutput $log `
      -RedirectStandardError $err `
      -WindowStyle Hidden -PassThru

    $launcherPid = $server.Id
    $deadline = (Get-Date).AddSeconds(30)
    do {
        Start-Sleep -Milliseconds 200
        $ready = $false
        if (Test-Path -LiteralPath $identityPath) {
            try {
                $identity = Get-Content -LiteralPath $identityPath -Raw | ConvertFrom-Json
                $candidatePid = [int]$identity.pid
                $candidateProc = Get-CimInstance Win32_Process -Filter "ProcessId = $candidatePid" -ErrorAction SilentlyContinue
                $ready = ([int]$identity.port -eq $port -and [string]$identity.checkout -eq $repoRoot -and
                  $candidateProc -and $candidateProc.CommandLine -like '*e2e\server.py*')
                if ($ready) { $ownedServerPid = $candidatePid }
            } catch { $ready = $false }
        }
        if ($ready) {
            try { $response = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port/api/sessions?summary=1" -TimeoutSec 2; $ready = ($response.StatusCode -eq 200) } catch { $ready = $false }
        }
    } while (-not $ready -and (Get-Date) -lt $deadline)
    if (-not $ready) { throw "isolated Pan server did not become ready on $port; see $log and $err" }

    $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $ownedServerPid"
    if (-not $proc -or $proc.CommandLine -notlike '*e2e\server.py*') {
        throw "refusing to test: pid $ownedServerPid is not the E2E server command"
    }
    $listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction Stop
    if ([int]$listener.OwningProcess -ne $ownedServerPid) {
        throw "refusing to test: port $port is owned by unexpected pid $($listener.OwningProcess)"
    }

    @{ pid = $ownedServerPid; launcherPid = $launcherPid; port = $port; checkout = $repoRoot; startedAt = $startedAt; log = $log; errorLog = $err } |
      ConvertTo-Json | Set-Content -LiteralPath $lifecyclePath -Encoding UTF8

    $testExitCode = Invoke-Pnpm -PnpmArgs @('--dir', $webRoot, 'exec', 'node', 'e2e/session-filter-menu.e2e.mjs')
    if ($testExitCode -ne 0) { throw "session filter menu E2E failed with exit code $testExitCode" }
}
finally {
    if ($ownedServerPid) {
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $ownedServerPid" -ErrorAction SilentlyContinue
        $listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
        $owned = $proc -and $proc.CommandLine -like '*e2e\server.py*' -and $listener -and ([int]$listener.OwningProcess -eq $ownedServerPid)
        if ($owned) {
            Stop-Process -Id $ownedServerPid -Force
            Wait-Process -Id $ownedServerPid -Timeout 10 -ErrorAction SilentlyContinue
        } else {
            Write-Warning "E2E cleanup skipped because pid/command/port identity no longer matches; pid=$ownedServerPid port=$port"
        }
    }
    $remaining = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    @{ stoppedPid = $ownedServerPid; port = $port; remainingListeners = @($remaining | ForEach-Object OwningProcess); finishedAt = [DateTimeOffset]::UtcNow } |
      ConvertTo-Json | Set-Content -LiteralPath (Join-Path $runtime 'cleanup.json') -Encoding UTF8
    if ($null -eq $oldPanPort) { Remove-Item Env:PAN_PORT -ErrorAction SilentlyContinue } else { $env:PAN_PORT = $oldPanPort }
    if ($null -eq $oldE2ERuntime) { Remove-Item Env:PAN_E2E_RUNTIME -ErrorAction SilentlyContinue } else { $env:PAN_E2E_RUNTIME = $oldE2ERuntime }
    if ($null -eq $oldE2EBase) { Remove-Item Env:PAN_E2E_BASE_URL -ErrorAction SilentlyContinue } else { $env:PAN_E2E_BASE_URL = $oldE2EBase }
}
