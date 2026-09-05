param(
    [string]$Root,
    [string]$RequestId,
    [switch]$Supervisor
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

    Write-RestartLog "scheduled Pan restart request=$RequestId root=$Root"

    # Do not replace these with taskkill or a second startup implementation.
    # The existing scripts scope process selection to this checkout's PID and
    # command-line markers, and start_pan.bat owns the venv/PID-file contract.
    & $StopScript *>> $LogFile
    if ($LASTEXITCODE -ne 0) {
        throw "stop_pan.bat failed with exit code $LASTEXITCODE"
    }

    # Let taskkill and uvicorn release the listener before start_pan checks for
    # duplicate instances and starts the new main.py process.
    Start-Sleep -Seconds 1

    & $StartScript *>> $LogFile
    if ($LASTEXITCODE -ne 0) {
        throw "start_pan.bat failed with exit code $LASTEXITCODE"
    }
    Write-RestartLog "Pan restart start script completed request=$RequestId"
}
catch {
    Write-RestartLog "Pan restart failed request=$RequestId error=$($_.Exception.Message)"
    exit 1
}
