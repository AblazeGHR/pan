param([string]$Url, [string]$Profile, [string]$Log)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$notify = New-Object System.Windows.Forms.NotifyIcon
$notify.Icon = [Drawing.SystemIcons]::Information
$notify.BalloonTipTitle = 'Pan: isolated closed-browser probe'
$notify.BalloonTipText = 'Click this test notification to open the isolated Pan session.'
$notify.Visible = $true
$context = New-Object System.Windows.Forms.ApplicationContext
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 45000
$timer.add_Tick({ $context.ExitThread() })
$notify.add_BalloonTipShown({ Add-Content -LiteralPath $Log -Value 'shown' })
$notify.add_BalloonTipClicked({
    Add-Content -LiteralPath $Log -Value 'native-click'
    Start-Process -FilePath 'C:\Program Files\Google\Chrome\Application\chrome.exe' -ArgumentList @('--no-first-run', '--no-default-browser-check', "--user-data-dir=`"$Profile`"", "`"$Url`"")
    Add-Content -LiteralPath $Log -Value 'launch-accepted'
    $context.ExitThread()
})
try {
    Add-Content -LiteralPath $Log -Value 'api-call'
    $notify.ShowBalloonTip(10000)
    $timer.Start()
    [Windows.Forms.Application]::Run($context)
} finally {
    $timer.Dispose()
    $notify.Dispose()
    $context.Dispose()
    Add-Content -LiteralPath $Log -Value 'disposed'
}
