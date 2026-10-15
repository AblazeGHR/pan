<#
.SYNOPSIS>
    Capture a screenshot of the Windows Action Center (notification panel).

.DESCRIPTION
    Research probe: opens the Action Center with a real Win+N key injection,
    captures the primary screen, closes the panel again. No notification
    settings, DND or focus-assist state are modified.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$OutPath,
    [int]$SettleMs = 1800
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

public static class PanKeyInject {
    [DllImport("user32.dll")] public static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, UIntPtr dwExtraInfo);
}
'@

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$VK_LWIN = 0x5B
$VK_N = 0x4E
$VK_ESC = 0x1B
$KEYUP = 2

function Send-WinN {
    [PanKeyInject]::keybd_event([byte]$VK_LWIN, 0, 0, [UIntPtr]::Zero)
    [PanKeyInject]::keybd_event([byte]$VK_N, 0, 0, [UIntPtr]::Zero)
    [PanKeyInject]::keybd_event([byte]$VK_N, 0, [byte]$KEYUP, [UIntPtr]::Zero)
    [PanKeyInject]::keybd_event([byte]$VK_LWIN, 0, [byte]$KEYUP, [UIntPtr]::Zero)
}

$opened = $false
try {
    Start-Process 'ms-actioncenter:'
    $opened = $true
} catch {
    $opened = $false
}
if (-not $opened) {
    Send-WinN
}
Start-Sleep -Milliseconds $SettleMs

$bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$bmp = New-Object System.Drawing.Bitmap($bounds.Width, $bounds.Height)
$g = [System.Drawing.Graphics]::FromImage($bmp)
try {
    $g.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
    $bmp.Save($OutPath, [System.Drawing.Imaging.ImageFormat]::Png)
} finally {
    $g.Dispose()
    $bmp.Dispose()
}

# Close the panel again (Esc), leaving the toast recorded in the notification center.
[PanKeyInject]::keybd_event([byte]$VK_ESC, 0, 0, [UIntPtr]::Zero)
[PanKeyInject]::keybd_event([byte]$VK_ESC, 0, [byte]$KEYUP, [UIntPtr]::Zero)
Start-Sleep -Milliseconds 400
[Console]::Out.WriteLine('{"ok":true,"screenshot":"' + $OutPath + '"}')
