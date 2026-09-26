$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$webRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$port = 8796
if ($port -eq 8768 -or $port -eq 8767) { throw "invalid protected E2E port: $port" }
$listeners = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
if ($listeners.Count -gt 0) {
  $owners = $listeners | ForEach-Object {
    $ownerPid = [int]$_.OwningProcess
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $ownerPid" -ErrorAction SilentlyContinue
    [pscustomobject]@{ pid = $ownerPid; command = $process.CommandLine }
  }
  throw "refusing to use occupied isolated E2E port $port; listener owners: $($owners | ConvertTo-Json -Compress)"
}

$runId = [guid]::NewGuid().ToString('N')
$runtime = Join-Path $webRoot "test-results\keep-position-runtime-$runId"
$python = 'D:\project\Pan\.venv\Scripts\python.exe'
$log = Join-Path $runtime 'server.stdout.log'
$err = Join-Path $runtime 'server.stderr.log'
$identityPath = Join-Path $runtime 'server-identity.json'
if (-not (Test-Path -LiteralPath $python)) { throw "isolated E2E Python is missing: $python" }
New-Item -ItemType Directory -Path $runtime | Out-Null

$server = $null
$ownedServerPid = $null
$oldPanPort = $env:PAN_PORT
$oldE2ERuntime = $env:PAN_E2E_RUNTIME
$oldBaseURL = $env:PAN_E2E_BASE_URL
try {
  $env:PAN_PORT = "$port"
  $env:PAN_E2E_RUNTIME = $runtime
  $env:PAN_E2E_BASE_URL = "http://127.0.0.1:$port"
  $server = Start-Process -FilePath $python `
    -ArgumentList @((Join-Path $webRoot 'e2e\server.py')) `
    -WorkingDirectory $repoRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $log `
    -RedirectStandardError $err `
    -PassThru

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
      $listener = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
      $ready = ($listener.Count -eq 1 -and [int]$listener[0].OwningProcess -eq $ownedServerPid)
    }
    if ($ready) {
      try { $response = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port/api/sessions?summary=1" -TimeoutSec 2; $ready = ($response.StatusCode -eq 200) } catch { $ready = $false }
    }
  } while (-not $ready -and (Get-Date) -lt $deadline)
  if (-not $ready) { throw "isolated Pan E2E server did not become ready on port $port; see $log and $err" }

  $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $ownedServerPid"
  $listener = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction Stop)
  if ($listener.Count -ne 1 -or [int]$listener[0].OwningProcess -ne $ownedServerPid -or
      -not $proc -or $proc.CommandLine -notlike '*e2e\server.py*') {
    throw "refusing to test: E2E server identity changed for pid $ownedServerPid port $port"
  }

  $env:PAN_KEEP_POSITION_PORT = "$port"
  & pnpm --dir $webRoot exec node e2e/keep-position-stability.e2e.mjs
  if ($LASTEXITCODE -ne 0) { throw "keep-position Chromium E2E failed with exit code $LASTEXITCODE" }
}
finally {
  if ($ownedServerPid) {
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $ownedServerPid" -ErrorAction SilentlyContinue
    $listener = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
    $owned = $proc -and $proc.CommandLine -like '*e2e\server.py*' -and
      $listener.Count -eq 1 -and [int]$listener[0].OwningProcess -eq $ownedServerPid
    if ($owned) {
      Stop-Process -Id $ownedServerPid -Force
      Wait-Process -Id $ownedServerPid -Timeout 10 -ErrorAction SilentlyContinue
    } else {
      Write-Warning "E2E cleanup skipped because pid/command/port identity no longer matches; pid=$ownedServerPid port=$port"
    }
  }
  $remaining = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
  @{ stoppedPid = $ownedServerPid; launcherPid = if ($server) { $server.Id } else { $null }; port = $port; remainingListeners = @($remaining | ForEach-Object OwningProcess) } |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $runtime 'cleanup.json') -Encoding UTF8
  if ($null -eq $oldPanPort) { Remove-Item Env:PAN_PORT -ErrorAction SilentlyContinue } else { $env:PAN_PORT = $oldPanPort }
  if ($null -eq $oldE2ERuntime) { Remove-Item Env:PAN_E2E_RUNTIME -ErrorAction SilentlyContinue } else { $env:PAN_E2E_RUNTIME = $oldE2ERuntime }
  if ($null -eq $oldBaseURL) { Remove-Item Env:PAN_E2E_BASE_URL -ErrorAction SilentlyContinue } else { $env:PAN_E2E_BASE_URL = $oldBaseURL }
  Remove-Item Env:PAN_KEEP_POSITION_PORT -ErrorAction SilentlyContinue
}
