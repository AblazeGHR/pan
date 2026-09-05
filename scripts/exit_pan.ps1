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
$StopScript = Join-Path $Root "scripts\stop_pan.bat"
$LogDir = Join-Path $Root "data\logs"
$LogFile = Join-Path $LogDir "pan-exit.log"

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
    Write-ExitLog "scheduled Pan exit request=$RequestId root=$Root"

    # Stop only.  No start script, restart script, broad process kill, or
    # service recovery is allowed in this supervisor.
    & $StopScript *>> $LogFile
    if ($LASTEXITCODE -ne 0) {
        throw "stop_pan.bat failed with exit code $LASTEXITCODE"
    }
    Write-ExitLog "Pan stop script completed request=$RequestId"
}
catch {
    Write-ExitLog "Pan exit failed request=$RequestId error=$($_.Exception.Message)"
    exit 1
}
