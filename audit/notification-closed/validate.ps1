$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$runtime = Join-Path $repo 'packages/web/test-results/notification-closed'
New-Item -ItemType Directory -Force $runtime | Out-Null
$env:TEMP = Join-Path $runtime 'temp'
$env:TMP = $env:TEMP
$env:npm_config_cache = Join-Path $runtime 'npm-cache'
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $runtime 'playwright-cache'
New-Item -ItemType Directory -Force $env:TEMP | Out-Null
$env:PAN_NOTIFICATION_E2E_PYTHON = (Get-Command python).Source
$env:PAN_NOTIFICATION_E2E_RUNTIME = Join-Path $runtime 'backend'
$env:PAN_NOTIFICATION_E2E_OUT = $PSScriptRoot
function Run-Step([string]$Name, [scriptblock]$Command) {
    Write-Output "STEP $Name"
    # Windows PowerShell 5 turns native stderr into ErrorRecords; stderr alone
    # is not a failed check. Preserve both streams and judge the actual exit.
    $previousErrorPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $Command 2>&1 | Tee-Object -FilePath (Join-Path $PSScriptRoot "$Name.txt")
        $stepExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorPreference
    }
    if ($stepExitCode -ne 0) { throw "$Name failed: $stepExitCode" }
}
Set-Location (Join-Path $repo 'packages/web')
if (Test-Path node_modules) {
    $modules = Get-Item node_modules
    if ($modules.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Refusing shared node_modules junction' }
}
Run-Step 'frozen-install' { pnpm install --frozen-lockfile --store-dir (Join-Path $runtime 'pnpm-store') --package-import-method copy }
Run-Step 'notification-unit' { pnpm exec vitest run src/utils/session-deeplink.test.tsx src/hooks/useWebSocket.test.tsx }
Run-Step 'build' { pnpm run build }
Run-Step 'isolated-chromium-install' { pnpm exec playwright install chromium }
Run-Step 'real-backend-e2e' { node e2e/notification-deeplink.real.e2e.mjs }
Set-Location $repo
Run-Step 'python-unit' { python -m pytest tests/test_notification_closed_browser.py tests/test_notifications_reminders.py -q --basetemp=.pytest-tmp-notification-closed-job -p no:cacheprovider }
Run-Step 'diff-check' { git diff --check }
Write-Output 'NOTIFICATION_CLOSED_VALIDATION_COMPLETE'
