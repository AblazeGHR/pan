$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$pidTarget = [int]$args[0]
$fileTarget = $args[1]
$root = [Windows.Automation.AutomationElement]::RootElement
$condition = New-Object Windows.Automation.PropertyCondition([Windows.Automation.AutomationElement]::ProcessIdProperty, $pidTarget)
$dialog = $null
for ($attempt = 0; $attempt -lt 50; $attempt++) {
    $dialog = $root.FindFirst([Windows.Automation.TreeScope]::Children, $condition)
    if ($dialog) { break }
    Start-Sleep -Milliseconds 200
}
if (-not $dialog) { throw 'No dialog for owned helper PID' }
$info = @{ title = $dialog.Current.Name; processId = $pidTarget; isOffscreen = $dialog.Current.IsOffscreen; rect = $dialog.Current.BoundingRectangle.ToString() }
if ($fileTarget -eq 'CANCEL') {
    $cancelCondition = New-Object Windows.Automation.PropertyCondition([Windows.Automation.AutomationElement]::AutomationIdProperty, '2')
    $cancel = $dialog.FindFirst([Windows.Automation.TreeScope]::Descendants, $cancelCondition)
    $info | ConvertTo-Json -Compress
    Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class CancelProbe { [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h, uint m, IntPtr w, IntPtr l); }
"@
    [CancelProbe]::PostMessage([IntPtr]$dialog.Current.NativeWindowHandle, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero) | Out-Null
    exit
}
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class DialogDisplay {
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
 [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int command);
 [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr dc, uint flags);
}
"@
[DialogDisplay]::ShowWindow([IntPtr]$dialog.Current.NativeWindowHandle, 9) | Out-Null
$info.foregroundRequested = [DialogDisplay]::SetForegroundWindow([IntPtr]$dialog.Current.NativeWindowHandle)
Start-Sleep -Milliseconds 300
Add-Type -AssemblyName System.Drawing
$rect = $dialog.Current.BoundingRectangle
$bitmap = New-Object Drawing.Bitmap([int]$rect.Width, [int]$rect.Height)
$graphics = [Drawing.Graphics]::FromImage($bitmap)
try {
    $dc = $graphics.GetHdc()
    try { $info.windowRendered = [DialogDisplay]::PrintWindow([IntPtr]$dialog.Current.NativeWindowHandle, $dc, 2) } finally { $graphics.ReleaseHdc($dc) }
    $bitmap.Save($args[2])
} finally { $graphics.Dispose(); $bitmap.Dispose() }
$editCondition = New-Object Windows.Automation.PropertyCondition([Windows.Automation.AutomationElement]::AutomationIdProperty, '1148')
$edit = $dialog.FindFirst([Windows.Automation.TreeScope]::Descendants, $editCondition)
if (-not $edit) { throw 'Filename edit not found' }
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class DialogProbe {
 [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern IntPtr SendMessage(IntPtr h, uint m, IntPtr w, string l);
 [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
}
"@
[DialogProbe]::SendMessage([IntPtr]$edit.Current.NativeWindowHandle, 0x000C, [IntPtr]::Zero, $fileTarget) | Out-Null
$buttonCondition = New-Object Windows.Automation.PropertyCondition([Windows.Automation.AutomationElement]::AutomationIdProperty, '1')
$button = $dialog.FindFirst([Windows.Automation.TreeScope]::Descendants, $buttonCondition)
if (-not $button) { throw 'Open button not found' }
$info.button = $button.Current.Name
$info | ConvertTo-Json -Compress
[DialogProbe]::PostMessage([IntPtr]$button.Current.NativeWindowHandle, 0x00F5, [IntPtr]::Zero, [IntPtr]::Zero) | Out-Null
