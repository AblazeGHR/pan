# Real-Chromium verification for the mobile Session Details fullscreen change.
#
# Mirrors e2e/run.ps1 (build -> isolated Pan server -> Chromium assertions) but
# runs on its own port and keeps its runtime dir outside the checkout, so it can
# run next to the other tasks that own 8765/8767 and the protected dev server.
param(
  [int]$Port = 8791
)

$ErrorActionPreference = 'Stop'

$reservedPorts = @(8765, 8767, 8768)
if ($reservedPorts -contains $Port) {
  throw "port $Port is reserved for another task or the protected dev service; pass -Port <free port>"
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$webRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$runtime = Join-Path $env:TEMP 'pan-e2e-session-details'
$python = 'D:\project\Pan\.venv\Scripts\python.exe'
$log = Join-Path $runtime 'server.stdout.log'
$err = Join-Path $runtime 'server.stderr.log'
$identityPath = Join-Path $runtime 'server-identity.json'

if (-not (Test-Path -LiteralPath $python)) { throw "isolated Python is missing: $python" }
if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
  throw "refusing to start: port $Port is already listening"
}
if (Test-Path -LiteralPath $runtime) { Remove-Item -LiteralPath $runtime -Recurse -Force }
New-Item -ItemType Directory -Path $runtime | Out-Null

$oldPanPort = $env:PAN_PORT
$oldE2ERuntime = $env:PAN_E2E_RUNTIME
$oldE2EBase = $env:PAN_E2E_BASE_URL
$launcherPid = $null
$ownedServerPid = $null
try {
  $env:PAN_PORT = "$Port"
  $env:PAN_E2E_RUNTIME = $runtime
  $env:PAN_E2E_BASE_URL = "http://127.0.0.1:$Port"

  & pnpm --dir $webRoot build
  if ($LASTEXITCODE -ne 0) { throw "web build failed with exit code $LASTEXITCODE" }

  $server = Start-Process -FilePath $python `
    -ArgumentList @((Join-Path $webRoot 'e2e\server.py')) `
    -WorkingDirectory $repoRoot `
    -RedirectStandardOutput $log `
    -RedirectStandardError $err `
    -PassThru
  $launcherPid = $server.Id

  $deadline = (Get-Date).AddSeconds(60)
  $ready = $false
  do {
    Start-Sleep -Milliseconds 250
    try {
      $response = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/api/sessions?summary=1" -TimeoutSec 3
      $ready = ($response.StatusCode -eq 200)
    } catch { $ready = $false }
  } while (-not $ready -and (Get-Date) -lt $deadline)
  if (-not $ready) { throw "isolated Pan server did not become ready on $Port; see $log and $err" }

  # The venv `python.exe` is a redirector: the listening process is the child
  # recorded in server-identity.json, not necessarily the launched pid.
  $serverPid = $launcherPid
  if (Test-Path -LiteralPath $identityPath) {
    $identity = Get-Content -LiteralPath $identityPath -Raw | ConvertFrom-Json
    if ([int]$identity.port -eq $Port -and [string]$identity.checkout -eq $repoRoot) {
      $serverPid = [int]$identity.pid
    }
  }
  $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $serverPid" -ErrorAction SilentlyContinue
  if (-not $proc -or $proc.CommandLine -notlike '*e2e\server.py*') {
    throw "refusing to test: pid $serverPid is not the E2E server command"
  }
  $ownedServerPid = $serverPid

  & pnpm --dir $webRoot exec node e2e/session-details-fullscreen.mjs
  if ($LASTEXITCODE -ne 0) { throw "real Chromium session-details verification failed with exit code $LASTEXITCODE" }
  Write-Host "session-details fullscreen verification passed on port $Port; artifacts: $runtime\artifacts"
}
finally {
  # The listening socket can be owned by a different pid than the launcher
  # (Start-Process wrapper), so stop whoever owns the port — but only after
  # checking that the process really is this checkout's e2e server.
  $candidates = @()
  if ($launcherPid) { $candidates += $launcherPid }
  if ($ownedServerPid) { $candidates += $ownedServerPid }
  $candidates += @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { [int]$_.OwningProcess })
  foreach ($candidatePid in ($candidates | Select-Object -Unique)) {
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $candidatePid" -ErrorAction SilentlyContinue
    if ($proc -and $proc.CommandLine -like '*e2e\server.py*' -and $proc.CommandLine -like "*$repoRoot*") {
      Stop-Process -Id $proc.ProcessId -Force
      Wait-Process -Id $proc.ProcessId -Timeout 10 -ErrorAction SilentlyContinue
    }
  }
  $remaining = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
  if ($remaining.Count -gt 0) {
    Write-Warning "port $Port still has listeners after cleanup: $($remaining.OwningProcess -join ',')"
  }
  if ($null -eq $oldPanPort) { Remove-Item Env:PAN_PORT -ErrorAction SilentlyContinue } else { $env:PAN_PORT = $oldPanPort }
  if ($null -eq $oldE2ERuntime) { Remove-Item Env:PAN_E2E_RUNTIME -ErrorAction SilentlyContinue } else { $env:PAN_E2E_RUNTIME = $oldE2ERuntime }
  if ($null -eq $oldE2EBase) { Remove-Item Env:PAN_E2E_BASE_URL -ErrorAction SilentlyContinue } else { $env:PAN_E2E_BASE_URL = $oldE2EBase }
}
