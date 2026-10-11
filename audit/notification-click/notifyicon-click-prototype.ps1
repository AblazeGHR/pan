<#
.SYNOPSIS
    Windows-native notification click prototype for Pan session deep links
    (research artifact — branch research/notification-click-session-20261011).

.DESCRIPTION
    Today's Pan system notification (packages/core/notifications.py) is a
    fire-and-forget PowerShell process: ShowBalloonTip(5000) + 6s sleep +
    Dispose.  There is NO click handler and the process is gone before a user
    could click, so native click → open Pan is impossible in the current shape.

    This prototype models the only viable native route: a HELPER process that
    stays alive, hosts a NotifyIcon, wires BalloonTipClicked
    (https://learn.microsoft.com/en-us/dotnet/api/system.windows.forms.notifyicon.balloontipclicked)
    and opens the session deep link on click.  The deep-link contract matches
    the web frontend prototype: <origin>/react/?session=<id>.

    Modes:
      -Mode simulate (default, SAFE):
          No NotifyIcon, no balloon, no browser launch.  Builds the deep link,
          runs the click action against a result file, and asserts the URL.
      -Mode live (NOT run by the research task, visibly disturbs the desktop):
          Shows a real balloon tip and opens the browser on click.

.PARAMETER SessionId
    Session reference; validated against the same allowlist the frontend uses
    (^[A-Za-z0-9_-]{1,64}$), so only opaque ids ever reach the OS.

.PARAMETER Origin
    Pan web origin.  Loopback / tailscale / tunnel all work because the origin
    is configured here and never baked into the notification body.

.PARAMETER ResultFile
    simulate mode: file that receives the URL the click action "opened".

.PARAMETER Mode
    simulate (default) | live

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File notifyicon-click-prototype.ps1 `
        -SessionId "ses_0123456789abcdef" -Origin "http://127.0.0.1:8768" -Mode simulate
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SessionId,

    [string]$Origin = 'http://127.0.0.1:8768',

    [string]$ResultFile = '',

    [ValidateSet('simulate', 'live')]
    [string]$Mode = 'simulate'
)

$ErrorActionPreference = 'Stop'

if ($SessionId -notmatch '^[A-Za-z0-9_-]{1,64}$') {
    Write-Error "SessionId does not match the allowlist ^[A-Za-z0-9_-]{1,64}$"
    exit 2
}

function New-PanDeepLink {
    param([string]$DeepLinkOrigin, [string]$DeepLinkSessionId)
    # Same contract as the web prototype: /react/?session=<id>; only the opaque
    # session id is carried — never the notification body.
    $base = $DeepLinkOrigin.TrimEnd('/')
    return "$base/react/?session=$([uri]::EscapeDataString($DeepLinkSessionId))"
}

function Invoke-PanNotificationClick {
    param([string]$Url, [string]$ClickMode, [string]$ClickResultFile)
    if ($ClickMode -eq 'live') {
        Start-Process $Url
        return
    }
    # simulate: no window, no browser — record what would have been opened.
    if ([string]::IsNullOrWhiteSpace($ClickResultFile)) {
        throw 'simulate mode requires -ResultFile'
    }
    Set-Content -LiteralPath $ClickResultFile -Value $Url -Encoding utf8 -NoNewline
}

$url = New-PanDeepLink -DeepLinkOrigin $Origin -DeepLinkSessionId $SessionId

if ($Mode -eq 'simulate') {
    $target = if ([string]::IsNullOrWhiteSpace($ResultFile)) {
        Join-Path $env:TEMP 'pan-notifyicon-click-prototype.url.txt'
    } else { $ResultFile }
    Invoke-PanNotificationClick -Url $url -ClickMode 'simulate' -ClickResultFile $target
    $written = (Get-Content -LiteralPath $target -Raw).Trim()
    $expected = "$($Origin.TrimEnd('/'))/react/?session=$SessionId"
    if ($written -ne $expected) {
        Write-Error "simulate mismatch: wrote '$written', expected '$expected'"
        exit 1
    }
    "SIMULATE_OK url=$written result=$target"
    exit 0
}

# ---- live mode (research task does NOT execute this; kept for review only) ----
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$notify = New-Object System.Windows.Forms.NotifyIcon
$notify.Icon = [System.Drawing.SystemIcons]::Information
$notify.BalloonTipTitle = $null
$notify.BalloonTipText = $null
$notify.Visible = $true
if ($ResultFile) { Set-Content -LiteralPath $ResultFile -Value $url -Encoding utf8 -NoNewline }
Register-ObjectEvent -InputObject $notify -EventName BalloonTipClicked -Action {
    Invoke-PanNotificationClick -Url $using:url -ClickMode 'live' -ClickResultFile ''
} | Out-Null
$notify.ShowBalloonTip(10000)
$deadline = (Get-Date).AddSeconds(60)
while ((Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 500 }
$notify.Dispose()
