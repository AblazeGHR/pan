$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
try {
    $inputData = [Console]::In.ReadToEnd() | ConvertFrom-Json
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type @"
using System;
using System.Runtime.InteropServices;
public sealed class PanPickerWindow : IDisposable {
    private delegate void WinEvent(IntPtr hook, uint evt, IntPtr hwnd,
        int objectId, int childId, uint thread, uint time);
    private readonly WinEvent callback;
    private readonly IntPtr owner;
    private IntPtr hook;
    private bool attempted;
    [DllImport("kernel32.dll")] private static extern uint GetCurrentProcessId();
    [DllImport("user32.dll")] private static extern IntPtr SetWinEventHook(uint min, uint max,
        IntPtr module, WinEvent callback, uint pid, uint thread, uint flags);
    [DllImport("user32.dll")] private static extern bool UnhookWinEvent(IntPtr hook);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] private static extern int GetClassName(IntPtr hwnd, System.Text.StringBuilder name, int size);
    [DllImport("user32.dll")] private static extern IntPtr GetWindow(IntPtr hwnd, uint command);
    [DllImport("user32.dll")] private static extern bool SetForegroundWindow(IntPtr hwnd);
    [DllImport("user32.dll")] private static extern bool SetWindowPos(IntPtr hwnd, IntPtr after, int x, int y, int width, int height, uint flags);
    [StructLayout(LayoutKind.Sequential)]
    private struct FlashInfo {
        public uint size; public IntPtr hwnd; public uint flags;
        public uint count; public uint timeout;
    }
    [DllImport("user32.dll")] private static extern bool FlashWindowEx(ref FlashInfo info);
    public PanPickerWindow(IntPtr owner) {
        this.owner = owner;
        callback = OnShow;
        // Receive only this helper's show events, on its modal UI loop.
        hook = SetWinEventHook(0x8002, 0x8002, IntPtr.Zero, callback, GetCurrentProcessId(), 0, 0);
        if (hook == IntPtr.Zero) throw new InvalidOperationException("Dialog event hook unavailable");
    }
    private void OnShow(IntPtr hook, uint evt, IntPtr hwnd, int objectId, int childId, uint thread, uint time) {
        if (attempted || objectId != 0 || childId != 0 || GetWindow(hwnd, 4) != owner) return;
        var name = new System.Text.StringBuilder(64);
        GetClassName(hwnd, name, name.Capacity);
        if (name.ToString() != "#32770") return;
        attempted = true;
        // HWND_TOP (zero) changes ordinary z-order, never HWND_TOPMOST.
        SetWindowPos(hwnd, IntPtr.Zero, 0, 0, 0, 0, 0x43);
        bool granted = SetForegroundWindow(hwnd);
        Console.Error.WriteLine(granted ? "pan-picker: foreground-granted" : "pan-picker: foreground-denied");
        if (!granted) {
            var info = new FlashInfo();
            info.size = (uint)Marshal.SizeOf(info);
            info.hwnd = owner;
            info.flags = 14; // FLASHW_TRAY | FLASHW_TIMERNOFG.
            FlashWindowEx(ref info);
        }
    }
    public void Dispose() {
        if (hook != IntPtr.Zero) { UnhookWinEvent(hook); hook = IntPtr.Zero; }
        GC.KeepAlive(callback);
    }
}
"@
    $dialog = New-Object System.Windows.Forms.OpenFileDialog
    $owner = New-Object System.Windows.Forms.Form
    $activation = $null
    try {
        # Give the console-free helper a normal taskbar owner so the picker
        # remains discoverable when Windows refuses foreground activation.
        # Never make it topmost or override the OS foreground policy.
        $owner.Text = 'Pan server attachments'
        $owner.ShowInTaskbar = $true
        $owner.WindowState = [System.Windows.Forms.FormWindowState]::Normal
        $owner.Size = New-Object System.Drawing.Size(360, 120)
        $label = New-Object System.Windows.Forms.Label
        $label.Text = 'Select files in the file dialog. Then confirm in Pan.'
        $label.Dock = [System.Windows.Forms.DockStyle]::Fill
        $owner.Controls.Add($label)
        $owner.StartPosition = [System.Windows.Forms.FormStartPosition]::CenterScreen
        $dialog.Title = 'Select server attachments'
        $dialog.Multiselect = $true
        $dialog.CheckFileExists = $true
        $dialog.RestoreDirectory = $true
        if ($inputData.directory) {
            $initial = [IO.Path]::GetFullPath($inputData.directory)
            if (-not (Test-Path -LiteralPath $initial -PathType Container)) { throw 'Invalid initial directory' }
            $dialog.InitialDirectory = $initial
        }
        $owner.Show()
        # Request focus once when this helper's actual native dialog is shown.
        # Keep the callback alive and release its hook with the dialog owner.
        $activation = New-Object PanPickerWindow($owner.Handle)
        if ($dialog.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) {
            @{ status = 'selected'; paths = @($dialog.FileNames) } | ConvertTo-Json -Compress
        } else {
            @{ status = 'cancelled'; paths = @() } | ConvertTo-Json -Compress
        }
    } finally { if ($activation) { $activation.Dispose() }; $dialog.Dispose(); $owner.Dispose() }
} catch {
    @{ status = 'failed'; paths = @() } | ConvertTo-Json -Compress
    exit 1
}
