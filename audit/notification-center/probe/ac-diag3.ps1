$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)

$out = @{}

# Lock state: LogonUI running means the lock screen is up.
$out.logonUi = @(Get-Process LogonUI -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class Diag {
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr p);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetClassName(IntPtr hWnd, StringBuilder t, int c);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder t, int c);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
    [DllImport("user32.dll")] public static extern uint MapVirtualKey(uint uCode, uint uMapType);
    [StructLayout(LayoutKind.Sequential)]
    public struct KEYBDINPUT { public ushort wVk; public ushort wScan; public uint dwFlags; public uint time; public IntPtr dwExtraInfo; }
    [StructLayout(LayoutKind.Sequential)]
    public struct INPUT { public uint type; public KEYBDINPUT ki; }
    [DllImport("user32.dll")] public static extern uint SendInput(uint n, INPUT[] p, int cb);
    [DllImport("user32.dll", SetLastError = true)] public static extern IntPtr OpenInputDesktop(uint flags, bool inherit, uint access);

    public static uint SendKey(ushort vk) {
        var inputs = new INPUT[2];
        inputs[0].type = 1; inputs[0].ki = new KEYBDINPUT { wVk = vk, wScan = (ushort)MapVirtualKey(vk, 0) };
        inputs[1].type = 1; inputs[1].ki = new KEYBDINPUT { wVk = vk, wScan = (ushort)MapVirtualKey(vk, 0), dwFlags = 2 };
        return SendInput(2, inputs, Marshal.SizeOf(typeof(INPUT)));
    }

    public static string ForegroundInfo() {
        IntPtr fg = GetForegroundWindow();
        if (fg == IntPtr.Zero) return "none";
        var cls = new StringBuilder(256); GetClassName(fg, cls, cls.Capacity);
        var title = new StringBuilder(256); GetWindowText(fg, title, title.Capacity);
        uint pid; GetWindowThreadProcessId(fg, out pid);
        return fg.ToInt64() + "|" + pid + "|" + cls + "|" + title;
    }

    public static string PanelWindow() {
        string found = "";
        EnumWindows(delegate(IntPtr h, IntPtr p) {
            if (!IsWindowVisible(h)) return true;
            var cls = new StringBuilder(256); GetClassName(h, cls, cls.Capacity);
            if (cls.ToString() != "Windows.UI.Core.CoreWindow") return true;
            uint pid; GetWindowThreadProcessId(h, out pid);
            try {
                var proc = System.Diagnostics.Process.GetProcessById((int)pid);
                if (proc.ProcessName == "ShellExperienceHost" || proc.ProcessName == "ShellHost") {
                    var title = new StringBuilder(256); GetWindowText(h, title, title.Capacity);
                    found = h.ToInt64() + "|" + proc.ProcessName + "|" + title.ToString();
                    return false;
                }
            } catch {}
            return true;
        }, IntPtr.Zero);
        return found;
    }
}
'@ -ErrorAction Stop

$out.foregroundBefore = [Diag]::ForegroundInfo()
$out.inputDesktop = [Diag]::OpenInputDesktop(0, $false, 0x0100).ToInt64()  # DESKTOP_READOBJECTS
$out.sendF24 = [Diag]::SendKey(0x87)  # VK_F24, harmless probe key
$out.panelBefore = [Diag]::PanelWindow()

# Try opening via explorer.exe with the ms-actioncenter: URI.
Start-Process 'explorer.exe' -ArgumentList 'ms-actioncenter:'
Start-Sleep -Milliseconds 1800
$out.panelAfterExplorer = [Diag]::PanelWindow()
$out.foregroundAfter = [Diag]::ForegroundInfo()

[Console]::Out.WriteLine(($out | ConvertTo-Json -Depth 5 -Compress))
