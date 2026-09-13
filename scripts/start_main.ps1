param(
    [string]$Python,
    [string]$MainPy,
    [string]$WorkDir,
    [string]$PidFile,
    [string]$StdoutFile,
    [string]$StderrFile,
    [bool]$ConsoleHidden = $false
)

$ErrorActionPreference = "Stop"
$pythonResolver = Join-Path $PSScriptRoot "resolve_pan_python.ps1"
if (Test-Path -LiteralPath $pythonResolver -PathType Leaf) {
    . $pythonResolver
}

foreach ($path in @($MainPy, $WorkDir)) {
    if (-not $path) {
        throw "start_main.ps1 received an empty path argument"
    }
}
if (-not (Test-Path -LiteralPath $MainPy -PathType Leaf)) {
    throw "Pan entry point not found: $MainPy"
}
if (-not (Test-Path -LiteralPath $WorkDir -PathType Container)) {
    throw "Pan working directory not found: $WorkDir"
}
if (-not $PidFile) {
    throw "PID file path is required"
}

$selected = Resolve-PanPython -Root $WorkDir -Explicit $Python
$PythonArgv = @($selected.Argv)
$Python = $PythonArgv[0]
Write-Host "[INFO] Pan Python source: $($selected.Source)"

# The launcher owns the console window, so read this launch-time option here
# instead of making the Python service responsible for Windows UI state. Old
# config.json files do not have the startup section and retain the default
# visible console behavior.
$configPath = Join-Path $WorkDir "config.json"
if (Test-Path -LiteralPath $configPath -PathType Leaf) {
    try {
        $config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
        $configured = $config.startup.console_hidden
        if ($null -ne $configured) {
            if ($configured -is [bool]) {
                $ConsoleHidden = [bool]$configured
            } elseif ($configured -is [string]) {
                switch ($configured.Trim().ToLowerInvariant()) {
                    "true" { $ConsoleHidden = $true }
                    "false" { $ConsoleHidden = $false }
                    default {
                        throw "startup.console_hidden must be true or false"
                    }
                }
            } else {
                throw "startup.console_hidden must be a JSON boolean"
            }
        }
    } catch {
        throw "Could not read startup.console_hidden from ${configPath}: $($_.Exception.Message)"
    }
}

foreach ($path in @($StdoutFile, $StderrFile)) {
    if (-not $path) {
        throw "start_main.ps1 requires stdout/stderr log paths"
    }
    $directory = Split-Path -Parent $path
    if ($directory) {
        New-Item -ItemType Directory -Force -Path $directory | Out-Null
    }
}

$pidDirectory = Split-Path -Parent $PidFile
if ($pidDirectory) {
    New-Item -ItemType Directory -Force -Path $pidDirectory | Out-Null
}

# Start main.py directly with the venv interpreter. Hidden mode redirects both
# streams to durable files, making failures before Pan's logging setup
# diagnosable when the batch window is launched by double-click. Visible mode
# keeps a real console attached to the new process so the user can watch it.
# Both modes use an independent process; stop_pan.bat still owns the recorded
# process tree and can terminate it.
if ($ConsoleHidden) {
    $p = Start-Process -FilePath $Python -ArgumentList (@($PythonArgv | Select-Object -Skip 1) + @("`"$MainPy`"")) `
        -WorkingDirectory $WorkDir -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $StdoutFile -RedirectStandardError $StderrFile
} else {
    $p = Start-Process -FilePath $Python -ArgumentList (@($PythonArgv | Select-Object -Skip 1) + @("`"$MainPy`"")) `
        -WorkingDirectory $WorkDir -WindowStyle Normal -PassThru
}
if (-not $p -or -not $p.Id) {
    throw "Python process could not be started"
}
$p.Id | Out-File -FilePath $PidFile -Encoding ascii -NoNewline
