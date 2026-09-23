param(
    [string]$PanUrl = 'http://127.0.0.1:8768/react/',
    [string]$ChromePath = 'C:\Program Files\Google\Chrome\Application\chrome.exe',
    [switch]$FullContent
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$recorder = Join-Path $PSScriptRoot 'record-browser.mjs'
$node = (Get-Command node.exe -ErrorAction Stop).Source
if (-not (Test-Path -LiteralPath $ChromePath -PathType Leaf)) {
    throw "Chrome not found: $ChromePath. Pass -ChromePath with its executable path."
}

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$output = Join-Path $root "packages\web\test-results\browser-trace-$stamp"
New-Item -ItemType Directory -Path $output -Force | Out-Null
$arguments = @($recorder, '--chrome', $ChromePath, '--url', $PanUrl, '--out', $output)
if ($FullContent) { $arguments += '--full-content' }

Write-Host "Trace directory: $output"
Write-Host 'A dedicated Chrome window will open. Use that window to reproduce the issue.'
Write-Host 'Press Enter here when the problem appears; type q and Enter to finish.'
& $node @arguments
if ($LASTEXITCODE -ne 0) { throw "Browser recorder exited with code $LASTEXITCODE" }
