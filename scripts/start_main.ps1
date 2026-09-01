param(
    [string]$Python,
    [string]$MainPy,
    [string]$WorkDir,
    [string]$PidFile
)

$ErrorActionPreference = "Stop"

foreach ($path in @($Python, $MainPy, $WorkDir)) {
    if (-not $path) {
        throw "start_main.ps1 received an empty path argument"
    }
}
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Python executable not found: $Python"
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

$pidDirectory = Split-Path -Parent $PidFile
if ($pidDirectory) {
    New-Item -ItemType Directory -Force -Path $pidDirectory | Out-Null
}

# Start main.py directly with the venv interpreter — no cmd /k wrapper, so the
# recorded PID is the python process itself and stop_pan.bat's process-tree
# kill (/T) takes down main.py plus any child processes (incl. the QQ bot).
$p = Start-Process -FilePath $Python -ArgumentList @("`"$MainPy`"") -WorkingDirectory $WorkDir -WindowStyle Normal -PassThru
if (-not $p -or -not $p.Id) {
    throw "Python process could not be started"
}
$p.Id | Out-File -FilePath $PidFile -Encoding ascii -NoNewline
