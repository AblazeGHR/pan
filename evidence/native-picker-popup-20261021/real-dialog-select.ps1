$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$pidTarget = [int]$args[0]
$fileTarget = $args[1]
$root = [Windows.Automation.AutomationElement]::RootElement
$pidCondition = New-Object Windows.Automation.PropertyCondition([Windows.Automation.AutomationElement]::ProcessIdProperty, $pidTarget)
$titleCondition = New-Object Windows.Automation.PropertyCondition([Windows.Automation.AutomationElement]::NameProperty, 'Select server attachments')
$condition = New-Object Windows.Automation.AndCondition($pidCondition, $titleCondition)
$dialog = $null
for ($attempt = 0; $attempt -lt 50; $attempt++) {
    $dialog = $root.FindFirst([Windows.Automation.TreeScope]::Descendants, $condition)
    if ($dialog) { break }
    Start-Sleep -Milliseconds 200
}
if (-not $dialog) { throw 'No dialog for owned helper PID' }
$info = @{ title = $dialog.Current.Name; processId = $pidTarget; isOffscreen = $dialog.Current.IsOffscreen; rect = $dialog.Current.BoundingRectangle.ToString() }
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class DialogDisplay {
 [StructLayout(LayoutKind.Sequential)] public struct GUIInfo {
  public int size; public uint flags; public IntPtr active, focus, capture, menu, move, caret;
  public int left, top, right, bottom;
 }
 [DllImport("user32.dll")] public static extern bool GetGUIThreadInfo(uint thread, ref GUIInfo info);
 public delegate bool EnumProc(IntPtr h, IntPtr p);
 [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc callback, IntPtr p);
 [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr h, System.Text.StringBuilder text, int n);
 [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
 [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X; public int Y; }
 [DllImport("user32.dll")] public static extern IntPtr WindowFromPoint(POINT point);
 [DllImport("user32.dll")] public static extern IntPtr GetAncestor(IntPtr h, uint flags);
 [DllImport("kernel32.dll")] public static extern uint GetCurrentProcessId();
 [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
 [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
 [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
 [DllImport("user32.dll")] public static extern int GetWindowLong(IntPtr h, int index);
 [DllImport("user32.dll")] public static extern IntPtr GetWindow(IntPtr h, uint command);
 [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr dc, uint flags);
}
"@
$hwnd = [IntPtr]$dialog.Current.NativeWindowHandle
$info.hwnd = $hwnd.ToInt64()
$info.foreground = [DialogDisplay]::GetForegroundWindow().ToInt64()
[uint32]$threadPid = 0
$threadId = [DialogDisplay]::GetWindowThreadProcessId($hwnd, [ref]$threadPid)
$gui = New-Object DialogDisplay+GUIInfo
$gui.size = [Runtime.InteropServices.Marshal]::SizeOf($gui)
$info.guiThreadRead = [DialogDisplay]::GetGUIThreadInfo($threadId, [ref]$gui)
$info.keyboardFocusRoot = [DialogDisplay]::GetAncestor($gui.focus, 2).ToInt64()
$info.owner = [DialogDisplay]::GetWindow($hwnd, 4).ToInt64()
$point = New-Object DialogDisplay+POINT
$point.X = [int]($dialog.Current.BoundingRectangle.X + $dialog.Current.BoundingRectangle.Width / 2)
$point.Y = [int]($dialog.Current.BoundingRectangle.Y + $dialog.Current.BoundingRectangle.Height / 2)
$info.centerTopWindow = [DialogDisplay]::GetAncestor([DialogDisplay]::WindowFromPoint($point), 2).ToInt64()
$info.topmost = ([DialogDisplay]::GetWindowLong($hwnd, -20) -band 8) -ne 0
$info.windows = @()
[DialogDisplay]::EnumWindows({ param($h, $p)
    [uint32]$windowPid = 0
    [DialogDisplay]::GetWindowThreadProcessId($h, [ref]$windowPid) | Out-Null
    if ($windowPid -eq $pidTarget) {
        $name = New-Object System.Text.StringBuilder 256
        [DialogDisplay]::GetClassName($h, $name, 256) | Out-Null
        $script:info.windows += @{ hwnd = $h.ToInt64(); class = $name.ToString(); visible = [DialogDisplay]::IsWindowVisible($h); topmost = ([DialogDisplay]::GetWindowLong($h, -20) -band 8) -ne 0 }
    }
    return $true
}, [IntPtr]::Zero) | Out-Null
if ($fileTarget -eq 'CANCEL') {
    $info | ConvertTo-Json -Compress -Depth 4
    [DialogDisplay]::PostMessage($hwnd, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero) | Out-Null
    exit
}
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
$info | ConvertTo-Json -Compress -Depth 4
[DialogProbe]::PostMessage([IntPtr]$button.Current.NativeWindowHandle, 0x00F5, [IntPtr]::Zero, [IntPtr]::Zero) | Out-Null
