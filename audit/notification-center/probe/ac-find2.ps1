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

public static class AcFind {
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr p);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetClassName(IntPtr hWnd, StringBuilder t, int c);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder t, int c);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);
    [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr hWnd, IntPtr hdcBlt, uint nFlags);
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

    // All visible top-level windows belonging to the shell panel hosts.
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

$out = @{ attempts = @(); opened = $false; windows = @(); matches = @(); captured = $null }

if ([AcFind]::ShellWindows().Count -gt 0) {
    $out.opened = $true
    $out.note = 'panel already open before injection'
} else {
    foreach ($i in 1..45) {
        $r = [AcFind]::SendWinN()
        $out.attempts += $r
        if ($r -eq 4) {
            Start-Sleep -Milliseconds 1500
            $wins = [AcFind]::ShellWindows()
            if ($wins.Count -gt 0) { $out.opened = $true; break }
            # Injection went through but nothing appeared; stop to avoid toggling.
            break
        }
        Start-Sleep -Seconds 2
    }
}

if ($out.opened) {
    $wins = [AcFind]::ShellWindows()
    $out.windows = $wins
    $target = $null
    foreach ($w in $wins) {
        $p = $w -split '\|'
        $rect = $p[4] -split ','
        $wdt = [int]$rect[2] - [int]$rect[0]
        $hgt = [int]$rect[3] - [int]$rect[1]
        if ($wdt -gt 200 -and $hgt -gt 200) { $target = @{ hwnd = [Int64]$p[0]; proc = $p[1]; cls = $p[2]; rect = $p[4] }; break }
    }
    if ($target) {
        $shot = Join-Path $PSScriptRoot 'action-center-window.png'
        if ([AcFind]::Capture([IntPtr]$target.hwnd, $shot)) { $out.captured = $shot }
        try {
            $el = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$target.hwnd)
            $all = $el.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
            $names = @()
            foreach ($item in $all) {
                $name = $item.Current.Name
                if (-not $name) { continue }
                $names += $name
                if ($name -like '*NCTEST*') {
                    $out.matches += @{ name = $name; type = $item.Current.ControlType.ProgrammaticName; automationId = $item.Current.AutomationId }
                }
            }
            $out.uiaCount = $names.Count
            $out.uiaSample = @($names | Select-Object -First 40)
        } catch {
            $out.uiaError = $_.Exception.Message
        }
    }
}

[Console]::Out.WriteLine(($out | ConvertTo-Json -Depth 6 -Compress))
