# Owned real-Chromium run for the chat "Scroll chat to bottom" shortcut.
# Starts the isolated E2E server on a random unprotected port with a disposable
# runtime, runs the Playwright script, and stops only the process it started.
$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
$webRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = 'D:\project\Pan\.venv\Scripts\python.exe'
$runtime = Join-Path $webRoot 'test-results\chat-scroll-bottom-shortcut\runtime'
$outDir = Join-Path $webRoot 'test-results\chat-scroll-bottom-shortcut'
$log = Join-Path $runtime 'server.stdout.log'
$err = Join-Path $runtime 'server.stderr.log'
$identityPath = Join-Path $runtime 'server-identity.json'

if (-not (Test-Path -LiteralPath $python)) { throw "isolated Python is missing: $python" }
if (Test-Path -LiteralPath $runtime) { Remove-Item -LiteralPath $runtime -Recurse -Force }
New-Item -ItemType Directory -Path $runtime | Out-Null

# Choose a free loopback port; never reuse the protected Pan ports.
$probe = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
$probe.Start()
$port = ([System.Net.IPEndPoint]$probe.LocalEndpoint).Port
$probe.Stop()
if ($port -in @(8765, 8767, 8768)) { throw "invalid random port: $port" }
if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
  throw "refusing to start: port $port is already listening"
}

$server = $null
$ownedServerPid = $null
$oldPanPort = $env:PAN_PORT
$oldE2ERuntime = $env:PAN_E2E_RUNTIME
$oldBaseURL = $env:PAN_E2E_BASE_URL
$oldScrollPort = $env:PAN_CHAT_SCROLL_PORT
$oldScrollOut = $env:PAN_CHAT_SCROLL_OUT
try {
  $env:PAN_PORT = "$port"
  $env:PAN_E2E_RUNTIME = $runtime
  $env:PAN_E2E_BASE_URL = "http://127.0.0.1:$port"
  $env:PAN_CHAT_SCROLL_PORT = "$port"
  $env:PAN_CHAT_SCROLL_OUT = $outDir

  $server = Start-Process -FilePath $python `
    -ArgumentList @((Join-Path $webRoot 'e2e\server.py')) `
    -WorkingDirectory $repoRoot `
    -RedirectStandardOutput $log `
    -RedirectStandardError $err `
    -PassThru

  $launcherPid = $server.Id
  $deadline = (Get-Date).AddSeconds(45)
  do {
    Start-Sleep -Milliseconds 250
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
      try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$port/api/sessions?summary=1" -TimeoutSec 2
        $ready = ($response.StatusCode -eq 200)
      } catch { $ready = $false }
    }
  } while (-not $ready -and (Get-Date) -lt $deadline)
  if (-not $ready) { throw "isolated Pan server did not become ready on $port; see $log and $err" }

  $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $ownedServerPid"
  $listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction Stop
  if (-not $proc -or $proc.CommandLine -notlike '*e2e\server.py*' -or [int]$listener.OwningProcess -ne $ownedServerPid) {
    throw "refusing to test: server identity changed for pid $ownedServerPid port $port"
  }

  & pnpm --dir $webRoot exec node e2e/chat-scroll-bottom-shortcut.e2e.mjs
  if ($LASTEXITCODE -ne 0) { throw "chat scroll-bottom Chromium E2E failed with exit code $LASTEXITCODE" }
} finally {
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
  @{ stoppedPid = $ownedServerPid; launcherPid = if ($server) { $server.Id } else { $null }; port = $port; remainingListeners = @($remaining | ForEach-Object OwningProcess) } |
    ConvertTo-Json | Set-Content -LiteralPath (Join-Path $outDir 'cleanup.json') -Encoding UTF8
  if ($null -eq $oldPanPort) { Remove-Item Env:PAN_PORT -ErrorAction SilentlyContinue } else { $env:PAN_PORT = $oldPanPort }
  if ($null -eq $oldE2ERuntime) { Remove-Item Env:PAN_E2E_RUNTIME -ErrorAction SilentlyContinue } else { $env:PAN_E2E_RUNTIME = $oldE2ERuntime }
  if ($null -eq $oldBaseURL) { Remove-Item Env:PAN_E2E_BASE_URL -ErrorAction SilentlyContinue } else { $env:PAN_E2E_BASE_URL = $oldBaseURL }
  if ($null -eq $oldScrollPort) { Remove-Item Env:PAN_CHAT_SCROLL_PORT -ErrorAction SilentlyContinue } else { $env:PAN_CHAT_SCROLL_PORT = $oldScrollPort }
  if ($null -eq $oldScrollOut) { Remove-Item Env:PAN_CHAT_SCROLL_OUT -ErrorAction SilentlyContinue } else { $env:PAN_CHAT_SCROLL_OUT = $oldScrollOut }
}
