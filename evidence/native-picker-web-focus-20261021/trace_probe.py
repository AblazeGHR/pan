"""Temporary diagnostics only; restore the exact product helper afterwards."""
from pathlib import Path
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[2]
p=ROOT/'packages/web/native_file_dialog.ps1';original=p.read_bytes()
s=original.decode('utf-8')
s=s.replace('    private bool attempted;', '''    private bool attempted;
    [DllImport("user32.dll")] private static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] private static extern bool IsWindowVisible(IntPtr hwnd);
    [DllImport("user32.dll")] private static extern bool IsWindowEnabled(IntPtr hwnd);
    [StructLayout(LayoutKind.Sequential)] private struct Last { public uint size; public uint time; }
    [DllImport("user32.dll")] private static extern bool GetLastInputInfo(ref Last last);
    [DllImport("kernel32.dll")] private static extern uint GetTickCount();
    public static void Trace(string stage, IntPtr target) {
        var last = new Last(); last.size = (uint)Marshal.SizeOf(last); GetLastInputInfo(ref last);
        Console.Error.WriteLine("trace stage="+stage+" fg="+GetForegroundWindow()+" target="+target+" visible="+IsWindowVisible(target)+" enabled="+IsWindowEnabled(target)+" inputAgeMs="+(GetTickCount()-last.time));
    }''')
s=s.replace('        SetWindowPos(hwnd, IntPtr.Zero', '        Trace("before-zorder",hwnd);\n        SetWindowPos(hwnd, IntPtr.Zero')
s=s.replace('        bool granted =', '        Trace("before-focus",hwnd);\n        bool granted =')
s=s.replace('        Console.Error.WriteLine(granted', '        Trace("after-focus",hwnd);\n        Console.Error.WriteLine(granted')
s=s.replace('        $owner.Show()', '        [PanPickerWindow]::Trace("before-owner-show",$owner.Handle)\n        $owner.Show()\n        [PanPickerWindow]::Trace("after-owner-show",$owner.Handle)')
mode=sys.argv[1] if len(sys.argv)>1 else 'trace'
if mode=='late-owner':
 s=s.replace('        [PanPickerWindow]::Trace("before-owner-show",$owner.Handle)', '        Start-Sleep -Seconds 3 # Test-only slow preparation before owner\n        [PanPickerWindow]::Trace("before-owner-show",$owner.Handle)')
try:
 p.write_text(s,encoding='utf-8')
 result=subprocess.run([sys.executable,str(Path(__file__).with_name('web_probe.py')),'mouse-'+mode],cwd=ROOT)
finally:p.write_bytes(original)
assert p.read_bytes()==original
sys.exit(result.returncode)
