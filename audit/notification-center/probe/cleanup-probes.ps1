<#
.SYNOPSIS
    One-command cleanup of probe-only leftovers after the acceptance run:
      1. remove the probe AUMID's notifications from the notification center
      2. delete the probe-only AppUserModelId registry key (HKCU)

.DESCRIPTION
    Production code installs no persistent registration objects. This script
    only removes objects created by the research probes under the probe AUMID
    Pan.NotificationsProbe. It never touches global notification settings, DND
    or other applications' entries.
#>
[CmdletBinding()]
param([string]$Aumid = 'Pan.NotificationsProbe')
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)

$result = @{ aumid = $Aumid; notificationsCleared = $false; registryKeyRemoved = $false }

try {
    [void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime]
    [Windows.UI.Notifications.ToastNotificationManager]::History.Clear($Aumid)
    $result.notificationsCleared = $true
} catch {
    $result.notificationError = $_.Exception.Message
}

try {
    $keyPath = "Software\Classes\AppUserModelId\$Aumid"
    $classes = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Software\Classes', $true)
    if ($classes.OpenSubKey("AppUserModelId\$Aumid")) {
        $classes.DeleteSubKeyTree("AppUserModelId\$Aumid")
        $result.registryKeyRemoved = $true
    }
    # Remove the AppUserModelId container only when it is empty (other apps
    # may have their own AUMID registrations).
    $container = $classes.OpenSubKey('AppUserModelId')
    if ($container -and $container.SubKeyCount -eq 0 -and $container.ValueCount -eq 0) {
        $container.Close()
        $classes.DeleteSubKey('AppUserModelId')
        $result.containerRemoved = $true
    } elseif ($container) {
        $container.Close()
    }
    $classes.Close()
} catch {
    $result.registryError = $_.Exception.Message
}

[Console]::Out.WriteLine(($result | ConvertTo-Json -Compress))
