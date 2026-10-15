<#
.SYNOPSIS
    Show one Windows toast via the WinRT ToastNotificationManager and exit.

.DESCRIPTION
    Research probe for the Pan Windows-notification-center task. The toast XML
    is passed as a base64 UTF-8 literal (payload isolation), the notifier uses
    the AUMID backed by the Start Menu shortcut, and Tag/Group provide
    replace-by-target dedup. The process exits immediately after Show(); no
    message loop, no long-lived helper.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Aumid,
    [Parameter(Mandatory=$true)][string]$XmlBase64,
    [string]$Tag = '',
    [string]$Group = ''
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)

[void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime]
[void][Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType=WindowsRuntime]

$xmlString = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($XmlBase64))
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($xmlString)
$toast = New-Object Windows.UI.Notifications.ToastNotification $xml
if ($Tag) { $toast.Tag = $Tag }
if ($Group) { $toast.Group = $Group }

$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($Aumid)
$notifier.Show($toast)

[Console]::Out.WriteLine('{"ok":true,"method":"winrt.toast"}')
