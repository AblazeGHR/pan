param(
    [Parameter(Mandatory = $true)][string]$JobId,
    [Parameter(Mandatory = $true)][string]$RegistryRoot,
    [Parameter(Mandatory = $true)][string]$ErrorMessage
)

$ErrorActionPreference = "Stop"

if ($JobId -notmatch '^job_[A-Za-z0-9]+$') {
    throw "invalid lifecycle Job id"
}

$JobsDir = Join-Path $RegistryRoot "jobs"
$JobPath = Join-Path $JobsDir ($JobId + ".json")
$LockPath = Join-Path $JobsDir ($JobId + ".lock")
if (-not (Test-Path -LiteralPath $JobPath -PathType Leaf)) {
    throw "lifecycle Job record not found: $JobPath"
}

# Match background_jobs._job_lock on Windows.  This keeps this no-Python
# fallback in the same read/modify/write transaction as the normal runner.
$fullLockPath = [System.IO.Path]::GetFullPath($LockPath)
$sha = [System.Security.Cryptography.SHA256]::Create()
try {
    $digest = ([BitConverter]::ToString(
        $sha.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($fullLockPath))
    )).Replace('-', '').ToLowerInvariant()
}
finally {
    $sha.Dispose()
}
$mutex = New-Object System.Threading.Mutex($false, ("Local\PanBackgroundJob_" + $digest))
$owned = $false
$tmpPath = $JobPath + "." + $PID + ".tmp"
$backupPath = $JobPath + "." + $PID + ".bak"
try {
    $owned = $mutex.WaitOne(30000)
    if (-not $owned) { throw "timed out acquiring lifecycle Job lock" }

    $job = Get-Content -LiteralPath $JobPath -Raw | ConvertFrom-Json
    $terminalPhases = @('ready', 'offline', 'failed', 'timed_out')
    if (($terminalPhases -contains [string]$job.phase) -or
        (@('completed', 'failed', 'cancelled') -contains [string]$job.status)) {
        exit 0
    }

    $errors = @()
    if ($job.PSObject.Properties.Name -contains 'errors' -and $null -ne $job.errors) {
        $errors = @($job.errors)
    }
    if ($job.PSObject.Properties.Name -contains 'error' -and $job.error -and
        -not ($errors -contains [string]$job.error)) {
        $errors += [string]$job.error
    }
    if (-not ($errors -contains $ErrorMessage)) { $errors += $ErrorMessage }

    $job.phase = 'failed'
    $job.status = 'failed'
    if ($job.PSObject.Properties.Name -contains 'error') {
        $job.error = $ErrorMessage
    } else {
        $job | Add-Member -MemberType NoteProperty -Name error -Value $ErrorMessage
    }
    if ($job.PSObject.Properties.Name -contains 'errors') {
        $job.errors = $errors
    } else {
        $job | Add-Member -MemberType NoteProperty -Name errors -Value $errors
    }
    $job.updatedAt = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
    $json = $job | ConvertTo-Json -Depth 20
    [System.IO.File]::WriteAllText(
        $tmpPath, ($json + [Environment]::NewLine),
        (New-Object System.Text.UTF8Encoding($false))
    )
    if (Test-Path -LiteralPath $backupPath) {
        Remove-Item -LiteralPath $backupPath -Force
    }
    [System.IO.File]::Replace($tmpPath, $JobPath, $backupPath)
    Remove-Item -LiteralPath $backupPath -Force -ErrorAction SilentlyContinue
}
finally {
    if (Test-Path -LiteralPath $tmpPath) {
        Remove-Item -LiteralPath $tmpPath -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $backupPath) {
        Remove-Item -LiteralPath $backupPath -Force -ErrorAction SilentlyContinue
    }
    if ($owned) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
