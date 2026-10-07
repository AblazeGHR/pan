param(
    [ValidateSet('OlderPages', 'Runtime', 'Draft', 'Navigation', 'Scrollbar', 'ColdShell')]
    [string]$Scenario = 'OlderPages',
    [string]$Dist,
    [string]$PythonExecutable,
    [ValidateRange(1, 20)]
    [int]$Samples = 3,
    [switch]$Baseline
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../../..')).Path
$webRoot = Join-Path $repoRoot 'packages/web'
$artifactRoot = Join-Path $webRoot ('test-results/devtools-performance-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
$scripts = @{
    OlderPages = 'older-pages.e2e.mjs'
    Runtime = 'stream-runtime-benchmark.mjs'
    Draft = 'composer-draft-persistence.mjs'
    Navigation = 'navigation-first-click.e2e.mjs'
    Scrollbar = 'navigation-first-click.e2e.mjs'
    ColdShell = 'cold-shell.e2e.mjs'
}
if ($Baseline -and $Scenario -notin @('OlderPages', 'ColdShell')) { throw '-Baseline applies only to OlderPages and ColdShell.' }
if ($Dist -and $Scenario -notin @('OlderPages', 'Runtime', 'ColdShell')) { throw '-Dist applies only to OlderPages, Runtime and ColdShell.' }
$distRoot = if ($Dist) { (Resolve-Path -LiteralPath $Dist).Path } else { Join-Path $webRoot 'dist' }
if ($Scenario -ne 'Draft' -and !(Test-Path -LiteralPath (Join-Path $distRoot 'index.html'))) {
    throw "Build the selected checkout first; missing $distRoot/index.html"
}
New-Item -ItemType Directory -Path $artifactRoot | Out-Null
$envNames = @('PAN_OLDER_DIST', 'PAN_OLDER_SAMPLES', 'PAN_OLDER_BASELINE', 'PAN_BENCH_DIST', 'PAN_NAV_CHECKOUT', 'PAN_E2E_PYTHON', 'PAN_NAV_SCROLLBAR', 'PAN_COLD_DIST', 'PAN_COLD_SAMPLES', 'PAN_COLD_CHECK')
$savedEnvironment = @{}
foreach ($name in $envNames) { $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
Push-Location $webRoot
try {
    $env:PAN_OLDER_DIST = $distRoot
    $env:PAN_BENCH_DIST = $distRoot
    $env:PAN_COLD_DIST = $distRoot
    $env:PAN_COLD_SAMPLES = [string]$Samples
    $env:PAN_COLD_CHECK = if ($Baseline) { '0' } else { '1' }
    $env:PAN_OLDER_SAMPLES = [string]$Samples
    $env:PAN_OLDER_BASELINE = if ($Baseline) { '1' } else { '0' }
    $env:PAN_NAV_CHECKOUT = $repoRoot
    $env:PAN_NAV_SCROLLBAR = if ($Scenario -eq 'Scrollbar') { '1' } else { '0' }
    if ($Scenario -in @('Navigation', 'Scrollbar')) {
        $localPython = Join-Path $repoRoot '.venv/Scripts/python.exe'
        $env:PAN_E2E_PYTHON = if ($PythonExecutable) {
            (Get-Command $PythonExecutable -ErrorAction Stop).Source
        } elseif (Test-Path -LiteralPath $localPython) { $localPython } else {
            (Get-Command python -ErrorAction Stop).Source
        }
    }
    $scriptPath = Join-Path $webRoot ('e2e/' + $scripts[$Scenario])
    $arguments = @($scriptPath)
    if ($Scenario -eq 'Scrollbar') { $arguments += '--scrollbar' }
    Write-Host "Scenario: $Scenario; evidence: $artifactRoot"
    & node @arguments *> (Join-Path $artifactRoot 'result.log')
    if ($LASTEXITCODE -ne 0) { throw "Scenario failed; inspect $artifactRoot/result.log" }
    Write-Host "Passed. Evidence: $artifactRoot/result.log"
} finally {
    Pop-Location
    foreach ($name in $envNames) { [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], 'Process') }
}
