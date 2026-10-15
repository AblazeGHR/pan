[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Aumid,
    [Parameter(Mandatory=$true)][string]$Title,
    [Parameter(Mandatory=$true)][string]$BodyText,
    [string]$LaunchUri = '',
    [string]$Tag = '',
    [string]$Group = '',
    [string]$ButtonText = ''
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)

function Esc([string]$s) { return [System.Security.SecurityElement]::Escape($s) }
if (-not $ButtonText) { $ButtonText = 'Open' }

$xmlText = '<?xml version="1.0" encoding="utf-8"?>'
$launchAttr = ''
if ($LaunchUri) { $launchAttr = ' launch="' + (Esc $LaunchUri) + '"' }
$xmlText += '<toast' + $launchAttr + '>'
$xmlText += '<visual><binding template="ToastGeneric">'
$xmlText += '<text>' + (Esc $Title) + '</text>'
$xmlText += '<text>' + (Esc $BodyText) + '</text>'
$xmlText += '</binding></visual>'
if ($LaunchUri) {
    $xmlText += '<actions><action activationType="protocol" arguments="' + (Esc $LaunchUri) + '" content="' + (Esc $ButtonText) + '"/></actions>'
}
$xmlText += '</toast>'

[void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime]
[void][Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType=WindowsRuntime]
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($xmlText)
$toast = New-Object Windows.UI.Notifications.ToastNotification $xml
if ($Tag) { $toast.Tag = $Tag }
if ($Group) { $toast.Group = $Group }
$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($Aumid)
$notifier.Show($toast)
[Console]::Out.WriteLine('{"ok":true,"xml":"' + ($xmlText -replace '"','\"') + '"}')
