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
# Compatibility wrapper only.  The only shutdown path is the Python launcher
# and its loopback graceful-shutdown coordination with Pan main.
$python = if ($RunnerPython) { $RunnerPython } else { "python" }
$arguments = @(
    "-m", "packages.core.main_lifecycle", "--supervise",
    "--job-id", $JobId, "--root", $Root, "--registry-root", $RegistryRoot,
    "--port", $Port
)
if ($OldPid) { $arguments += @("--old-pid", $OldPid) }
if ($OldPidCreatedAt) { $arguments += @("--old-pid-created-at", $OldPidCreatedAt) }
& $python @arguments
exit $LASTEXITCODE
