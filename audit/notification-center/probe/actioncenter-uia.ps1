<#
.SYNOPSIS
    Action Center inspection probe: ensure the panel is open (retrying Win+N
    while UIPI blocks injection), capture the panel window with
    PrintWindow(PW_RENDERFULLCONTENT), enumerate UI Automation items and
    optionally invoke a matching item.

.DESCRIPTION
    Research probe for the Pan Windows-notification-center task. A UIA invoke
    is an OS-mediated activation of the real notification-center item, NOT a
    human click. Does not modify notification settings, DND or focus assist.
#>
[CmdletBinding()]
param(
    [string]$OutDir = $PSScriptRoot,
    [string]$MatchName = 'Pan',
    [switch]$Invoke,
    [int]$Retries = 45,
    [int]$RetrySleepMs = 2000
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)

Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Drawing

Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;

public static class PanWin3 {
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr p);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetClassName(IntPtr hWnd, StringBuilder t, int c);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder t, int c);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT r);
    [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr hWnd, IntPtr hdc, uint flags);
    [DllImport("user32.dll")] public static extern uint MapVirtualKey(uint uCode, uint uMapType);
    [StructLayout(LayoutKind.Sequential)]
    public struct KEYBDINPUT { public ushort wVk; public ushort wScan; public uint dwFlags; public uint time; public IntPtr dwExtraInfo; }
    [StructLayout(LayoutKind.Sequential)]
    public struct INPUT { public uint type; public KEYBDINPUT ki; }
    [DllImport("user32.dll")] public static extern uint SendInput(uint n, INPUT[] p, int cb);
    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }

    public static uint SendWinN() {
        var inputs = new INPUT[4];
        inputs[0].type = 1; inputs[0].ki = new KEYBDINPUT { wVk = 0x5B, wScan = (ushort)MapVirtualKey(0x5B, 0) };
        inputs[1].type = 1; inputs[1].ki = new KEYBDINPUT { wVk = 0x4E, wScan = (ushort)MapVirtualKey(0x4E, 0) };
        inputs[2].type = 1; inputs[2].ki = new KEYBDINPUT { wVk = 0x4E, wScan = (ushort)MapVirtualKey(0x4E, 0), dwFlags = 2 };
        inputs[3].type = 1; inputs[3].ki = new KEYBDINPUT { wVk = 0x5B, wScan = (ushort)MapVirtualKey(0x5B, 0), dwFlags = 2 };
        return SendInput(4, inputs, Marshal.SizeOf(typeof(INPUT)));
    }

    public static void SendEsc() {
        var inputs = new INPUT[2];
        inputs[0].type = 1; inputs[0].ki = new KEYBDINPUT { wVk = 0x1B, wScan = (ushort)MapVirtualKey(0x1B, 0) };
        inputs[1].type = 1; inputs[1].ki = new KEYBDINPUT { wVk = 0x1B, wScan = (ushort)MapVirtualKey(0x1B, 0), dwFlags = 2 };
        SendInput(2, inputs, Marshal.SizeOf(typeof(INPUT)));
    }

    // Visible top-level windows of the two shell panel hosts.
    public static List<string> ShellWindows() {
        var found = new List<string>();
        EnumWindows(delegate(IntPtr h, IntPtr p) {
            if (!IsWindowVisible(h)) return true;
            uint pid; GetWindowThreadProcessId(h, out pid);
            string procName;
            try { procName = System.Diagnostics.Process.GetProcessById((int)pid).ProcessName; }
            catch { return true; }
            if (procName != "ShellExperienceHost" && procName != "ShellHost") return true;
            var cls = new StringBuilder(256); GetClassName(h, cls, cls.Capacity);
            var title = new StringBuilder(256); GetWindowText(h, title, title.Capacity);
            RECT r; GetWindowRect(h, out r);
            found.Add(string.Format("{0}|{1}|{2}|{3}|{4},{5},{6},{7}", h.ToInt64(), procName, cls, title, r.Left, r.Top, r.Right, r.Bottom));
            return true;
        }, IntPtr.Zero);
        return found;
    }

    public static bool Capture(IntPtr hWnd, string path) {
        RECT r; if (!GetWindowRect(hWnd, out r)) return false;
        int w = r.Right - r.Left, h = r.Bottom - r.Top;
        if (w <= 0 || h <= 0) return false;
        using (var bmp = new System.Drawing.Bitmap(w, h))
        using (var g = System.Drawing.Graphics.FromImage(bmp)) {
            IntPtr hdc = g.GetHdc();
            bool ok;
            try { ok = PrintWindow(hWnd, hdc, 2); }
            finally { g.ReleaseHdc(hdc); }
            if (!ok) return false;
            bmp.Save(path, System.Drawing.Imaging.ImageFormat.Png);
        }
        return true;
    }
}
'@ -ReferencedAssemblies System.Drawing -ErrorAction Stop

$result = @{
    attempts = @()
    alreadyOpen = $false
    openedByProbe = $false
    panel = $null
    windows = @()
    matches = @()
    invoked = $null
    captured = $null
    matchName = $MatchName
}

$panels = [PanWin3]::ShellWindows()
if ($panels.Count -gt 0) {
    $result.alreadyOpen = $true
} else {
    foreach ($i in 1..$Retries) {
        $r = [PanWin3]::SendWinN()
        $result.attempts += $r
        if ($r -eq 4) {
            Start-Sleep -Milliseconds 1500
            $panels = [PanWin3]::ShellWindows()
            if ($panels.Count -gt 0) { $result.openedByProbe = $true }
            break   # do not toggle again after a successful injection
        }
        Start-Sleep -Milliseconds $RetrySleepMs
    }
}

if ($panels.Count -gt 0) {
    $target = $null
    foreach ($w in $panels) {
        $p = $w -split '\|'
        $rect = $p[4] -split ','
        $wdt = [int]$rect[2] - [int]$rect[0]
        $hgt = [int]$rect[3] - [int]$rect[1]
        if ($wdt -gt 200 -and $hgt -gt 200) {
            $target = @{ hwnd = [Int64]$p[0]; proc = $p[1]; cls = $p[2]; width = $wdt; height = $hgt }
            break
        }
    }
    if (-not $target) {
        $p = ($panels[0] -split '\|')
        $target = @{ hwnd = [Int64]$p[0]; proc = $p[1]; cls = $p[2] }
    }
    $result.panel = $target
    $result.windows = $panels

    $shot = Join-Path $OutDir 'action-center-window.png'
    if ([PanWin3]::Capture([IntPtr]$target.hwnd, $shot)) { $result.captured = $shot }

    try {
        $el = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$target.hwnd)
        $all = $el.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
        foreach ($item in $all) {
            $name = $item.Current.Name
            if (-not $name) { continue }
            if ($name -like "*$MatchName*") {
                $entry = @{
                    name = $name
                    type = $item.Current.ControlType.ProgrammaticName
                    automationId = $item.Current.AutomationId
                }
                $result.matches += $entry
                if ($Invoke -and $null -eq $result.invoked) {
                    $invoked = $false
                    # Prefer an invokable descendant (the action button).
                    try {
                        $sons = $item.FindAll([System.Windows.Automation.TreeScope]::Descendants,
                            (New-Object System.Windows.Automation.PropertyCondition(
                                [System.Windows.Automation.AutomationElement]::IsInvokePatternAvailableProperty, $true)))
                        foreach ($son in $sons) {
                            try {
                                $son.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
                                $result.invoked = @{ via = 'descendant'; name = $son.Current.Name; type = $son.Current.ControlType.ProgrammaticName; ok = $true }
                                $invoked = $true
                                break
                            } catch {}
                        }
                    } catch {}
                    if (-not $invoked) {
                        try {
                            $item.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
                            $result.invoked = @{ via = 'self'; name = $name; ok = $true }
                        } catch {
                            $result.invoked = @{ via = 'self'; name = $name; ok = $false; error = $_.Exception.Message }
                        }
                    }
                }
            }
        }
    } catch {
        $result.uiaError = $_.Exception.Message
    }
}

# Close the panel again only if the probe opened it.
if ($result.openedByProbe) {
    Start-Sleep -Milliseconds 600
    [PanWin3]::SendEsc()
    Start-Sleep -Milliseconds 300
}

[Console]::Out.WriteLine(($result | ConvertTo-Json -Depth 6 -Compress))
