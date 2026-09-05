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
$StopScript = Join-Path $Root "scripts\stop_pan.bat"
$ConfigPath = Join-Path $Root "config.json"
$LogDir = Join-Path $Root "data\logs"
$LogFile = Join-Path $LogDir "pan-exit.log"
if (-not $RegistryRoot) { $RegistryRoot = Join-Path $Root "data\background_jobs" }
if (-not $Port) {
    $Port = 8768
    if (Test-Path -LiteralPath $ConfigPath) {
        try {
            $config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
            if ($config.port) { $Port = [int]$config.port }
        } catch { }
    }
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-ExitLog([string]$Message) {
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -LiteralPath $LogFile -Value "[$stamp] $Message"
}

if (-not $Supervisor) {
    # The second PowerShell process is detached before the current Pan
    # process is stopped.  The stop script does not match this supervisor:
    # its checkout-bound process rule requires main.py.
    $arguments = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $PSCommandPath,
        "-Root", $Root,
        "-RequestId", $RequestId,
        "-JobId", $JobId,
        "-RegistryRoot", $RegistryRoot,
        "-Port", $Port,
        "-OldPid", $OldPid,
        "-OldPidCreatedAt", $OldPidCreatedAt,
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
    if (-not $JobId) { throw "durable lifecycle Job id is required" }
    Write-ExitLog "supervisor started request=$RequestId job=$JobId root=$Root port=$Port"
    $Python = Join-Path $Root ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        $PythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
        if (-not $PythonCommand) { throw "Pan Python interpreter not found" }
        $Python = $PythonCommand.Source
    }
    # The lifecycle runner invokes only stop_pan.bat and verifies that this
    # checkout's listener and verified old service process are gone.  It never
    # invokes a start/restart script for the exit operation.
    $runnerArgs = @(
        "-m", "packages.core.main_lifecycle", "--supervise",
        "--job-id", $JobId, "--root", $Root, "--port", $Port,
        "--registry-root", $RegistryRoot
    )
    if ($OldPid) { $runnerArgs += @("--old-pid", $OldPid) }
    if ($OldPidCreatedAt) { $runnerArgs += @("--old-pid-created-at", $OldPidCreatedAt) }
    & $Python @runnerArgs *>> $LogFile
    if ($LASTEXITCODE -ne 0) {
        throw "durable Pan exit supervisor failed with exit code $LASTEXITCODE"
    }
    Write-ExitLog "Pan exit lifecycle completed request=$RequestId job=$JobId"
}
catch {
    Write-ExitLog "Pan exit failed request=$RequestId error=$($_.Exception.Message)"
    exit 1
}
