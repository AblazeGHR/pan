param(
    [string]$PanUrl = 'http://127.0.0.1:8768/react/?panE2E=1',
    [string]$ChromePath = 'C:\Program Files\Google\Chrome\Application\chrome.exe',
    [ValidateRange(1024, 65535)]
    [int]$DebugPort = 9222,
    [string]$ProfileDirectory = (Join-Path $env:USERPROFILE '.codex\browser-profiles\pan-debug')
)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $ChromePath -PathType Leaf)) {
    throw "Chrome not found: $ChromePath. Pass -ChromePath with its executable path."
}
$pageUri = $null
if (-not [Uri]::TryCreate($PanUrl, [UriKind]::Absolute, [ref]$pageUri) -or
    $pageUri.Scheme -notin @('http', 'https')) {
    throw 'PanUrl must be an absolute HTTP or HTTPS URL.'
}
$profilePath = [IO.Path]::GetFullPath($ProfileDirectory)
if ($profilePath.Contains('"') -or $PanUrl.Contains('"')) {
    throw 'ProfileDirectory and PanUrl must not contain double quotes.'
}
$listeners = @(Get-NetTCPConnection -State Listen -ErrorAction Stop |
    Where-Object { $_.LocalPort -eq $DebugPort })
if ($listeners.Count -gt 0) {
    throw "Port $DebugPort is already in use. Close the existing debug browser or choose another -DebugPort and update the Codex MCP browser-url to match."
}

New-Item -ItemType Directory -Path $profilePath -Force | Out-Null
$browserArguments = @(
    "--remote-debugging-port=$DebugPort",
    '--remote-debugging-address=127.0.0.1',
    ('--user-data-dir="{0}"' -f $profilePath),
    '--no-first-run',
    '--no-default-browser-check',
    ('"{0}"' -f $PanUrl)
)

# This is a visible, interactive browser requested by the user.
# No Pan service is started, stopped, or restarted.
Start-Process -FilePath $ChromePath -ArgumentList $browserArguments | Out-Null
Write-Host "Chrome launch requested. Codex MCP endpoint: http://127.0.0.1:$DebugPort"
Write-Host "Browser profile: $profilePath"
Write-Host 'Reload the Codex MCP connection if its browser tools are not available.'
Write-Host 'This launcher does not start a recording. Use Chrome DevTools MCP to inspect or control the page.'
