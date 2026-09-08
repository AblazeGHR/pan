$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$webRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$runtime = Join-Path $webRoot 'test-results\pan-e2e-runtime'
$port = 8765
$python = 'D:\project\Pan\.venv\Scripts\python.exe'
$log = Join-Path $runtime 'server.stdout.log'
$err = Join-Path $runtime 'server.stderr.log'
$identityPath = Join-Path $runtime 'server-identity.json'
$lifecyclePath = Join-Path $runtime 'lifecycle.json'

if (-not (Test-Path -LiteralPath $python)) { throw "isolated Python is missing: $python" }
if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
  throw "refusing to start: permitted E2E port $port is already listening"
}
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

  & pnpm --dir $webRoot build
  $buildExitCode = $LASTEXITCODE
  if ($buildExitCode -ne 0) { throw "isolated web build failed with exit code $buildExitCode" }

  $server = Start-Process -FilePath $python `
    -ArgumentList @((Join-Path $webRoot 'e2e\server.py')) `
    -WorkingDirectory $repoRoot `
    -RedirectStandardOutput $log `
    -RedirectStandardError $err `
    -PassThru

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

  & pnpm --dir $webRoot exec node e2e/run-browser.mjs
  $testExitCode = $LASTEXITCODE
  if ($testExitCode -ne 0) { throw "real Chromium E2E failed with exit code $testExitCode" }
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
