$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
[void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime]
foreach ($id in @('Pan.NotificationsProbe', 'Pan.NoSuchAppAtAll', 'Microsoft.Windows.Explorer')) {
    try {
        $n = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($id)
        Write-Output ("{0} => {1}" -f $id, $n.Setting)
    } catch {
        Write-Output ("{0} => ERROR {1}" -f $id, $_.Exception.Message)
    }
}
