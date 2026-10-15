$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)

$link = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\PanProbe\PanProbe.lnk'
"exists: $(Test-Path -LiteralPath $link)"
if (Test-Path -LiteralPath $link) {
    (Get-Item -LiteralPath $link).Length
    $shell = New-Object -ComObject Shell.Application
    $folder = $shell.Namespace((Split-Path -Parent $link))
    $item = $folder.ParseName((Split-Path -Leaf $link))
    "aumid property: '" + $item.ExtendedProperty('System.AppUserModel.ID') + "'"
}

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

namespace PanDiag {
    [ComImport]
    [Guid("886D8EEB-8CF2-4446-8D02-CDBA1D086238")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface IPropertyStore { }

    public static class Probe {
        [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = true)]
        private static extern int SHGetPropertyStoreFromParsingName(
            [MarshalAs(UnmanagedType.LPWStr)] string pszPath, IntPtr pbc, int flags,
            ref Guid riid, [MarshalAs(UnmanagedType.Interface)] out IPropertyStore ppv);

        public static string Try(string path, int flags) {
            var iid = new Guid("886D8EEB-8CF2-4446-8D02-CDBA1D086238");
            IPropertyStore store;
            int hr;
            try {
                hr = SHGetPropertyStoreFromParsingName(path, IntPtr.Zero, flags, ref iid, out store);
            } catch (Exception ex) {
                return "throw: " + ex.GetType().Name + " " + ex.Message;
            }
            return "hr=0x" + hr.ToString("X8") + (hr == 0 ? " OK" : "");
        }
    }
}
'@

"flags=0 (default): " + [PanDiag.Probe]::Try($link, 0)
"flags=1 (handlerpropertiesonly): " + [PanDiag.Probe]::Try($link, 1)
"flags=2 (readwrite): " + [PanDiag.Probe]::Try($link, 2)
