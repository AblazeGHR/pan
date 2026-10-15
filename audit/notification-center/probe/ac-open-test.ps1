$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)

Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;

public static class AcProbe {
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
    public delegate bool EnumChildProc(IntPtr hWnd, IntPtr lParam);

    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool EnumChildWindows(IntPtr hWnd, EnumChildProc lpEnumFunc, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetClassName(IntPtr hWnd, StringBuilder text, int count);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint processId);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);
    [DllImport("user32.dll")] public static extern uint MapVirtualKey(uint uCode, uint uMapType);
    [DllImport("user32.dll")] public static extern uint SendInput(uint nInputs, INPUT[] pInputs, int cbSize);
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint dwFlags, uint dx, uint dy, uint dwData, UIntPtr dwExtraInfo);
    [DllImport("user32.dll")] public static extern IntPtr FindWindow(string lpClassName, string lpWindowName);

    [StructLayout(LayoutKind.Sequential)]
    public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }

    [StructLayout(LayoutKind.Sequential)]
    public struct KEYBDINPUT {
        public ushort wVk; public ushort wScan; public uint dwFlags; public uint time; public IntPtr dwExtraInfo;
    }
    [StructLayout(LayoutKind.Sequential)]
    public struct MOUSEINPUT {
        public int dx; public int dy; public uint mouseData; public uint dwFlags; public uint time; public IntPtr dwExtraInfo;
    }
    [StructLayout(LayoutKind.Explicit)]
    public struct INPUTUNION {
        [FieldOffset(0)] public KEYBDINPUT ki;
        [FieldOffset(0)] public MOUSEINPUT mi;
    }
    [StructLayout(LayoutKind.Sequential)]
    public struct INPUT { public uint type; public INPUTUNION u; }

    public static uint SendWinN() {
        var inputs = new INPUT[4];
        inputs[0].type = 1; inputs[0].u.ki = new KEYBDINPUT { wVk = 0x5B, wScan = (ushort)MapVirtualKey(0x5B, 0), dwFlags = 0 };
        inputs[1].type = 1; inputs[1].u.ki = new KEYBDINPUT { wVk = 0x4E, wScan = (ushort)MapVirtualKey(0x4E, 0), dwFlags = 0 };
        inputs[2].type = 1; inputs[2].u.ki = new KEYBDINPUT { wVk = 0x4E, wScan = (ushort)MapVirtualKey(0x4E, 0), dwFlags = 2 };
        inputs[3].type = 1; inputs[3].u.ki = new KEYBDINPUT { wVk = 0x5B, wScan = (ushort)MapVirtualKey(0x5B, 0), dwFlags = 2 };
        return SendInput(4, inputs, Marshal.SizeOf(typeof(INPUT)));
    }

    public static uint ClickAt(int x, int y) {
        var inputs = new INPUT[2];
        inputs[0].type = 0; inputs[0].u.mi = new MOUSEINPUT { dx = x, dy = y, dwFlags = 0x0001 | 0x8000 }; // MOVE|ABSOLUTE
        inputs[1].type = 0; inputs[1].u.mi = new MOUSEINPUT { dx = x, dy = y, dwFlags = 0x0002 | 0x8000 }; // DOWN|ABSOLUTE
        uint r = SendInput(2, inputs, Marshal.SizeOf(typeof(INPUT)));
        inputs = new INPUT[1];
        inputs[0].type = 0; inputs[0].u.mi = new MOUSEINPUT { dx = x, dy = y, dwFlags = 0x0004 | 0x8000 }; // UP|ABSOLUTE
        r = SendInput(1, inputs, Marshal.SizeOf(typeof(INPUT)));
        return r;
    }

    public static List<string> TopWindows() {
        var found = new List<string>();
        EnumWindows(delegate(IntPtr h, IntPtr p) {
            if (!IsWindowVisible(h)) return true;
            var title = new StringBuilder(512); GetWindowText(h, title, title.Capacity);
            var cls = new StringBuilder(512); GetClassName(h, cls, cls.Capacity);
            uint pid; GetWindowThreadProcessId(h, out pid);
            found.Add(string.Format("{0}|{1}|{2}|{3}", h.ToInt64(), pid, cls, title));
            return true;
        }, IntPtr.Zero);
        return found;
    }

    public static List<string> ChildWindows(IntPtr parent) {
        var found = new List<string>();
        EnumChildWindows(parent, delegate(IntPtr h, IntPtr p) {
            var title = new StringBuilder(512); GetWindowText(h, title, title.Capacity);
            var cls = new StringBuilder(512); GetClassName(h, cls, cls.Capacity);
            RECT r; GetWindowRect(h, out r);
            found.Add(string.Format("{0}|{1}|{2}|{3},{4},{5},{6}", h.ToInt64(), cls, title, r.Left, r.Top, r.Right, r.Bottom));
            return true;
        }, IntPtr.Zero);
        return found;
    }

    public static bool HasShellExperienceHostPanel() {
        bool found = false;
        EnumWindows(delegate(IntPtr h, IntPtr p) {
            if (!IsWindowVisible(h)) return true;
            var cls = new StringBuilder(512); GetClassName(h, cls, cls.Capacity);
            if (cls.ToString() != "Windows.UI.Core.CoreWindow") return true;
            uint pid; GetWindowThreadProcessId(h, out pid);
            try {
                var proc = System.Diagnostics.Process.GetProcessById((int)pid);
                if (proc.ProcessName == "ShellExperienceHost") { found = true; return false; }
            } catch {}
            return true;
        }, IntPtr.Zero);
        return found;
    }
}
'@ -ErrorAction Stop

$out = @{ userInteractive = [Environment]::UserInteractive; sessionId = (Get-Process -Id $PID).SessionId }

$fg = [AcProbe]::GetForegroundWindow()
$parts = @()
foreach ($line in [AcProbe]::TopWindows()) { if ($line.StartsWith("$([Int64]$fg)|")) { $parts = $line -split '\|' } }
$out.foreground = if ($parts.Count -gt 0) { @{ hwnd = $parts[0]; pid = $parts[1]; class = $parts[2]; title = $parts[3] } } else { $null }

$out.panelBefore = [AcProbe]::HasShellExperienceHostPanel()

$r = [AcProbe]::SendWinN()
$out.sendInputResult = $r
$found = $false
foreach ($i in 1..10) {
    Start-Sleep -Milliseconds 400
    if ([AcProbe]::HasShellExperienceHostPanel()) { $found = $true; break }
}
$out.panelAfterWinN = $found

if (-not $found) {
    # Enumerate taskbar children to find the clock button as a fallback opener.
    $tray = [AcProbe]::FindWindow('Shell_TrayWnd', $null)
    $out.trayHwnd = [Int64]$tray
    $children = [AcProbe]::ChildWindows($tray)
    $out.trayChildren = @()
    foreach ($c in $children) {
        $cp = $c -split '\|'
        $out.trayChildren += @{ hwnd = $cp[0]; class = $cp[1]; title = $cp[2]; rect = $cp[3] }
    }
}

# Close the panel if the probe opened it.
if ($found) {
    [AcProbe]::SendWinN()  # toggle closed again (second Win+N)
    Start-Sleep -Milliseconds 400
    $out.closedAgain = -not [AcProbe]::HasShellExperienceHostPanel()
}

[Console]::Out.WriteLine(($out | ConvertTo-Json -Depth 6 -Compress))
