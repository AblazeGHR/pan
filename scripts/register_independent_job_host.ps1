<#
Explicit, per-user installation. Never run this from Pan lifespan.
The Task Scheduler is the external host; stopping Pan does not stop this task.
This script does not migrate existing Jobs or enable Pan configuration.
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)][string]$Python,
    [Parameter(Mandatory)][string]$DataRoot,
    [ValidateRange(1024, 65535)][int]$Port = 8795,
    [ValidatePattern('^[A-Za-z0-9_-]+$')][string]$TaskName = 'PanIndependentJobs',
    [switch]$StartNow
)
$ErrorActionPreference = 'Stop'
$repoPath = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$pythonPath = (Resolve-Path -LiteralPath $Python).Path
if (-not [System.IO.Path]::IsPathFullyQualified($DataRoot)) {
    throw 'DataRoot must be an explicit absolute path'
}
$rootPath = [System.IO.Path]::GetFullPath($DataRoot)
if ($pythonPath.Contains('"') -or $rootPath.Contains('"')) {
    throw 'Quoted paths are not supported'
}
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    throw 'Task already exists; inspect it before an explicit replacement'
}
$userName = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$arguments = '-m packages.jobs.independent_service --root "' + $rootPath + '" --port ' + $Port
$action = New-ScheduledTaskAction -Execute $pythonPath -Argument $arguments -WorkingDirectory $repoPath
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $userName
$principal = New-ScheduledTaskPrincipal -UserId $userName -LogonType Interactive -RunLevel Limited
# Long-lived host, one instance; no finite default execution time limit.
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
if ($PSCmdlet.ShouldProcess($TaskName, 'Register independent per-user Job host')) {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings | Out-Null
    if ($StartNow) { Start-ScheduledTask -TaskName $TaskName }
}
