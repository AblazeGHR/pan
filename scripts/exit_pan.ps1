param(
    [string]$Root,
    [string]$RequestId,
    [switch]$Supervisor,
    [string]$JobId,
    [string]$RegistryRoot,
    [int]$Port,
    [int]$OldPid,
    [double]$OldPidCreatedAt,
    [string]$RunnerPython
)

$ErrorActionPreference = "Stop"
$pythonResolver = Join-Path $PSScriptRoot "resolve_pan_python.ps1"
if (Test-Path -LiteralPath $pythonResolver -PathType Leaf) {
    . $pythonResolver
}
$LogFile = $null
$FailureScript = Join-Path $PSScriptRoot "mark_lifecycle_job_failed.ps1"

function Write-ExitLog([string]$Message) {
    if (-not $LogFile) { return }
    $stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -LiteralPath $LogFile -Value "[$stamp] $Message"
}

function Persist-RunnerFailure([string]$Message) {
    if (-not $JobId -or -not $RegistryRoot -or
        -not (Test-Path -LiteralPath $FailureScript -PathType Leaf)) {
        Write-ExitLog "could not persist runner failure: lifecycle failure helper is unavailable"
        return
    }
    try {
        & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass `
            -File $FailureScript -JobId $JobId -RegistryRoot $RegistryRoot `
            -ErrorMessage $Message
        if ($LASTEXITCODE -ne 0) {
            Write-ExitLog "lifecycle failure helper returned exit code $LASTEXITCODE"
        }
    } catch {
        Write-ExitLog "lifecycle failure helper failed: $($_.Exception.Message)"
    }
}

if (-not $Supervisor) {
    # The second PowerShell process is detached before the current Pan
    # process is stopped.  The stop script does not match this supervisor:
    # its checkout-bound process rule requires main.py.
    $arguments = @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $PSCommandPath,
        "-Root", $Root, "-RequestId", $RequestId, "-JobId", $JobId,
        "-RegistryRoot", $RegistryRoot, "-Port", $Port,
        "-OldPid", $OldPid, "-OldPidCreatedAt", $OldPidCreatedAt, "-Supervisor"
    )
    Start-Process -FilePath "powershell.exe" -ArgumentList $arguments `
        -WorkingDirectory $Root -WindowStyle Hidden | Out-Null
    exit 0
}

try {
    if (-not $Root) { throw "Pan root is required" }
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
    if (-not (Test-Path -LiteralPath $StopScript -PathType Leaf)) {
        throw "stop script not found: $StopScript"
    }
    if (-not $JobId) { throw "durable lifecycle Job id is required" }
    Write-ExitLog "supervisor started request=$RequestId job=$JobId root=$Root port=$Port"
    if (-not (Get-Command Resolve-PanPython -ErrorAction SilentlyContinue)) {
        throw "Pan Python interpreter not found: resolver script missing"
    }
    $pythonCandidate = Resolve-PanPython -Root $Root -Explicit $RunnerPython
    $PythonArgv = @($pythonCandidate.Argv)
    $Python = $PythonArgv[0]
    # Exit remains stop-only: this runner never invokes a start script.
    $runnerArgs = @(
        "-m", "packages.core.main_lifecycle", "--supervise",
        "--job-id", $JobId, "--root", $Root, "--port", $Port,
        "--registry-root", $RegistryRoot
    )
    if ($OldPid) { $runnerArgs += @("--old-pid", $OldPid) }
    if ($OldPidCreatedAt) { $runnerArgs += @("--old-pid-created-at", $OldPidCreatedAt) }
    & $Python @($PythonArgv | Select-Object -Skip 1) @runnerArgs *>> $LogFile
    if ($LASTEXITCODE -ne 0) {
        throw "durable Pan exit supervisor failed with exit code $LASTEXITCODE"
    }
    $jobPath = Join-Path (Join-Path $RegistryRoot "jobs") ($JobId + ".json")
    $currentJob = Get-Content -LiteralPath $jobPath -Raw | ConvertFrom-Json
    if (@('offline', 'failed', 'timed_out') -notcontains [string]$currentJob.phase) {
        throw "lifecycle runner exited without a terminal Job state"
    }
    Write-ExitLog "Pan exit lifecycle completed request=$RequestId job=$JobId"
}
catch {
    $message = "Pan exit runner failed: $($_.Exception.Message)"
    Write-ExitLog "Pan exit failed request=$RequestId error=$message"
    Persist-RunnerFailure $message
    exit 1
}
