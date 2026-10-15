$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)

Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class Kbd {
    [DllImport("user32.dll")] public static extern uint MapVirtualKey(uint uCode, uint uMapType);
    [StructLayout(LayoutKind.Sequential)]
    public struct KEYBDINPUT {
        public ushort wVk; public ushort wScan; public uint dwFlags; public uint time; public IntPtr dwExtraInfo;
    }
    [StructLayout(LayoutKind.Sequential)]
    public struct INPUT { public uint type; public KEYBDINPUT ki; }
    [DllImport("user32.dll")] public static extern uint SendInput(uint nInputs, INPUT[] pInputs, int cbSize);
    public static uint WinN() {
        var inputs = new INPUT[4];
        inputs[0].type = 1; inputs[0].ki = new KEYBDINPUT { wVk = 0x5B, wScan = (ushort)MapVirtualKey(0x5B, 0) };
        inputs[1].type = 1; inputs[1].ki = new KEYBDINPUT { wVk = 0x4E, wScan = (ushort)MapVirtualKey(0x4E, 0) };
        inputs[2].type = 1; inputs[2].ki = new KEYBDINPUT { wVk = 0x4E, wScan = (ushort)MapVirtualKey(0x4E, 0), dwFlags = 2 };
        inputs[3].type = 1; inputs[3].ki = new KEYBDINPUT { wVk = 0x5B, wScan = (ushort)MapVirtualKey(0x5B, 0), dwFlags = 2 };
        return SendInput(4, inputs, Marshal.SizeOf(typeof(INPUT)));
    }
}
'@ -ErrorAction Stop

$result = @{ shellProcesses = @(); rootChildren = @(); matches = @() }

# Shell-ish processes with main windows (reveals the real notification host).
foreach ($p in (Get-Process | Where-Object { $_.ProcessName -match 'Shell|StartMenu|Experience' })) {
    $result.shellProcesses += @{ name = $p.ProcessName; id = $p.Id; mainTitle = $p.MainWindowTitle; mainHwnd = $p.MainWindowHandle.ToInt64() }
}

$sent = [Kbd]::WinN()
$result.sendInput = $sent
Start-Sleep -Milliseconds 1600

# UIA root children: name/class/process for every top-level UI element.
$root = [System.Windows.Automation.AutomationElement]::RootElement
$children = $root.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)
foreach ($c in $children) {
    $cur = $c.Current
    $entry = @{ name = $cur.Name; class = $cur.ClassName; pid = $cur.ProcessId; type = $cur.ControlType.ProgrammaticName; hwnd = $c.NativeWindowHandle }
    try { $entry.process = (Get-Process -Id $cur.ProcessId -ErrorAction Stop).ProcessName } catch { $entry.process = '?' }
    if ($entry.process -match 'Shell|StartMenu|Experience' -or $entry.class -match 'CoreWindow') {
        $result.rootChildren += $entry
    }
    if ($cur.Name -like '*NCTEST*') { $result.matches += $entry }
}

[Console]::Out.WriteLine(($result | ConvertTo-Json -Depth 6 -Compress))
