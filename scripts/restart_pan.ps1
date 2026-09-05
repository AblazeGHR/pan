param(
    [string]$Root,
    [string]$RequestId,
    [switch]$Supervisor,
    [string]$JobId,
    [string]$RegistryRoot,
    [int]$Port,
    [int]$OldPid,
    [double]$OldPidCreatedAt
)

$ErrorActionPreference = "Stop"

if (-not $Root) {
    throw "Pan root is required"
}

$Root = (Resolve-Path -LiteralPath $Root).Path
$ScriptDir = Join-Path $Root "scripts"
$StopScript = Join-Path $ScriptDir "stop_pan.bat"
$StartScript = Join-Path $ScriptDir "start_pan.bat"
$LogDir = Join-Path $Root "data\logs"
$LogFile = Join-Path $LogDir "pan-restart.log"
if (-not $RegistryRoot) { $RegistryRoot = Join-Path $Root "data\background_jobs" }
if (-not $Port) {
    $Port = 8768
    $configPath = Join-Path $Root "config.json"
    if (Test-Path -LiteralPath $configPath) {
        try {
            $config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
            if ($config.port) { $Port = [int]$config.port }
        } catch { }
    }
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-RestartLog([string]$Message) {
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -LiteralPath $LogFile -Value "[$stamp] $Message"
}

if (-not $Supervisor) {
    # The request process launches this first, short-lived hop.  The second
    # PowerShell process is the real supervisor and owns the stop/start chain;
    # it is started hidden before the current Pan process is stopped.
    $arguments = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $PSCommandPath,
        "-Root", $Root,
        "-RequestId", $RequestId,
        "-Supervisor"
    )
    Start-Process -FilePath "powershell.exe" -ArgumentList $arguments `
        -WorkingDirectory $Root -WindowStyle Hidden | Out-Null
    exit 0
}

try {
    if (-not (Test-Path -LiteralPath $StopScript -PathType Leaf)) {
        throw "stop script not found: $StopScript"
    }
    if (-not (Test-Path -LiteralPath $StartScript -PathType Leaf)) {
        throw "start script not found: $StartScript"
    }

    Write-RestartLog "supervisor started request=$RequestId job=$JobId root=$Root port=$Port"
    if (-not $JobId) { throw "durable lifecycle Job id is required" }
    $Python = Join-Path $Root ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        $PythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
        if (-not $PythonCommand) { throw "Pan Python interpreter not found" }
        $Python = $PythonCommand.Source
    }

    # The detached supervisor delegates both stop_pan.bat and start_pan.bat
    # to the durable Python lifecycle runner.  The runner verifies the target
    # checkout, listener owner, PID creation time, and /api/health before it
    # records ready; neither script's launch return alone means success.
    Start-Sleep -Seconds 1
    $runnerArgs = @(
        "-m", "packages.core.main_lifecycle", "--supervise",
        "--job-id", $JobId, "--root", $Root, "--port", $Port,
        "--registry-root", $RegistryRoot
    )
    if ($OldPid) { $runnerArgs += @("--old-pid", $OldPid) }
    if ($OldPidCreatedAt) { $runnerArgs += @("--old-pid-created-at", $OldPidCreatedAt) }
    & $Python @runnerArgs *>> $LogFile
    if ($LASTEXITCODE -ne 0) {
        throw "durable Pan lifecycle supervisor failed with exit code $LASTEXITCODE"
    }
    Write-RestartLog "Pan restart lifecycle completed request=$RequestId job=$JobId"
}
catch {
    Write-RestartLog "Pan restart failed request=$RequestId error=$($_.Exception.Message)"
    exit 1
}
