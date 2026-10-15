<#
.SYNOPSIS
    Ensure the unpackaged-app toast identity: a Start Menu shortcut carrying
    System.AppUserModel.ID plus a user-level (HKCU) protocol registration.

.DESCRIPTION
    Research probe for the Pan Windows-notification-center task. Mirrors the
    shape the production code will use:
      * Shortcut: %APPDATA%\Microsoft\Windows\Start Menu\Programs\Pan\Pan.lnk
        with System.AppUserModel.ID = Pan.Desktop.Notifications
      * Protocol: HKCU:\Software\Classes\pan (URL:Pan Protocol) launching the
        activation entry with the raw "%1" URI.

    All writes are user-scoped (no admin, no HKLM, no machine state).
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Aumid,
    [Parameter(Mandatory=$true)][string]$ShortcutPath,
    [Parameter(Mandatory=$true)][string]$ShortcutTarget,
    [Parameter(Mandatory=$true)][string]$ShortcutArguments,
    [string]$ShortcutDescription = 'Pan',
    [Parameter(Mandatory=$true)][string]$ProtocolName,
    [Parameter(Mandatory=$true)][string]$ProtocolCommand
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;

namespace PanToastIdentity {
    [ComImport]
    [Guid("000214F9-0000-0000-C000-000000000046")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface IShellLinkW {
        [PreserveSig] int GetPath([MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszFile, int cchMax, ref WIN32_FIND_DATA pFindData, uint fFlags);
        [PreserveSig] int GetIDList(out IntPtr ppidl);
        [PreserveSig] int SetIDList(IntPtr pidl);
        [PreserveSig] int GetDescription([MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszName, int cchMax);
        [PreserveSig] int SetDescription([MarshalAs(UnmanagedType.LPWStr)] string pszDescription);
        [PreserveSig] int GetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszDir, int cchMax);
        [PreserveSig] int SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string pszDir);
        [PreserveSig] int GetArguments([MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszArgs, int cchMax);
        [PreserveSig] int SetArguments([MarshalAs(UnmanagedType.LPWStr)] string pszArgs);
        [PreserveSig] int GetHotkey(out short pwHotkey);
        [PreserveSig] int SetHotkey(short wHotkey);
        [PreserveSig] int GetShowCmd(out int piShowCmd);
        [PreserveSig] int SetShowCmd(int iShowCmd);
        [PreserveSig] int GetIconLocation([MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszIconPath, int cchMax, out int piIcon);
        [PreserveSig] int SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string pszIconPath, int iIcon);
        [PreserveSig] int SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string pszPathRel, uint dwReserved);
        [PreserveSig] int Resolve(IntPtr hwnd, uint fFlags);
        [PreserveSig] int SetPath([MarshalAs(UnmanagedType.LPWStr)] string pszFile);
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    internal struct WIN32_FIND_DATA {
        internal uint dwFileAttributes;
        internal FILETIME ftCreationTime;
        internal FILETIME ftLastAccessTime;
        internal FILETIME ftLastWriteTime;
        internal uint nFileSizeHigh;
        internal uint nFileSizeLow;
        internal uint dwReserved0;
        internal uint dwReserved1;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 260)] internal string cFileName;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 14)] internal string cAlternateFileName;
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct FILETIME {
        internal uint dwLowDateTime;
        internal uint dwHighDateTime;
    }

    [ComImport]
    [Guid("0000010b-0000-0000-C000-000000000046")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface IPersistFile {
        void GetClassID(out Guid pClassID);
        [PreserveSig] int IsDirty();
        void Load([MarshalAs(UnmanagedType.LPWStr)] string pszFileName, int dwMode);
        void Save([MarshalAs(UnmanagedType.LPWStr)] string pszFileName, [MarshalAs(UnmanagedType.Bool)] bool fRemember);
        void SaveCompleted([MarshalAs(UnmanagedType.LPWStr)] string pszFileName);
        void GetCurFile([MarshalAs(UnmanagedType.LPWStr)] out string ppszFileName);
    }

    [ComImport]
    [Guid("886D8EEB-8CF2-4446-8D02-CDBA1D086238")]
    [InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface IPropertyStore {
        [PreserveSig] int GetCount(out int cProps);
        [PreserveSig] int GetAt(int iProp, out PROPERTYKEY pkey);
        [PreserveSig] int GetValue(ref PROPERTYKEY key, out PROPVARIANT pv);
        [PreserveSig] int SetValue(ref PROPERTYKEY key, ref PROPVARIANT pv);
        [PreserveSig] int Commit();
    }

    [StructLayout(LayoutKind.Sequential, Pack = 4)]
    internal struct PROPERTYKEY {
        internal Guid fmtid;
        internal uint pid;
    }

    [StructLayout(LayoutKind.Explicit)]
    internal struct PROPVARIANT {
        [FieldOffset(0)] internal ushort vt;
        [FieldOffset(8)] internal IntPtr pwszVal;
    }

    [ComImport]
    [Guid("00021401-0000-0000-C000-000000000046")]
    internal class ShellLink { }

    public static class Shortcut {
        // PKEY_AppUserModel_ID = {9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F9}, 5
        private static readonly PROPERTYKEY AppUserModelIDKey = new PROPERTYKEY {
            fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F9"),
            pid = 5
        };

        [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = true)]
        private static extern int SHGetPropertyStoreFromParsingName(
            [MarshalAs(UnmanagedType.LPWStr)] string pszPath, IntPtr pbc, int flags,
            ref Guid riid, [MarshalAs(UnmanagedType.Interface)] out IPropertyStore ppv);

        public static void Create(string linkPath, string target, string arguments,
                                  string workingDir, string description, string aumid) {
            // Step 1: create and save the .lnk (path/args/description only).
            var link = (IShellLinkW)new ShellLink();
            link.SetDescription(description);
            link.SetPath(target);
            link.SetArguments(arguments);
            if (!string.IsNullOrEmpty(workingDir)) {
                link.SetWorkingDirectory(workingDir);
            }
            var file = (IPersistFile)link;
            file.Save(linkPath, true);
            Marshal.ReleaseComObject(file);
            Marshal.ReleaseComObject(link);

            // Step 2: reopen the saved shortcut's property store and set the
            // AppUserModel.ID (GPS_READWRITE = 2).
            var iid = new Guid("886D8EEB-8CF2-4446-8D02-CDBA1D086238");
            IPropertyStore store;
            int hr = SHGetPropertyStoreFromParsingName(linkPath, IntPtr.Zero, 2, ref iid, out store);
            if (hr != 0) throw new COMException("Property store open failed", hr);
            var value = new PROPVARIANT();
            value.vt = 31; // VT_LPWSTR
            value.pwszVal = Marshal.StringToCoTaskMemUni(aumid);
            try {
                var key = AppUserModelIDKey;
                hr = store.SetValue(ref key, ref value);
                if (hr != 0) throw new COMException("SetValue failed", hr);
                hr = store.Commit();
                if (hr != 0) throw new COMException("Commit failed", hr);
            } finally {
                Marshal.FreeCoTaskMem(value.pwszVal);
                Marshal.ReleaseComObject(store);
            }
        }
    }
}
'@ -ErrorAction Stop

$shortcutDir = Split-Path -Parent $ShortcutPath
if (-not (Test-Path -LiteralPath $shortcutDir)) {
    New-Item -ItemType Directory -Path $shortcutDir -Force | Out-Null
}
[PanToastIdentity.Shortcut]::Create($ShortcutPath, $ShortcutTarget, $ShortcutArguments,
    (Split-Path -Parent $ShortcutTarget), $ShortcutDescription, $Aumid)

$classesKey = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Software\Classes', $true)
$protocolKey = $classesKey.CreateSubKey($ProtocolName)
$protocolKey.SetValue($null, "URL:Pan Protocol")
$protocolKey.SetValue('URL Protocol', '')
$protocolKey.Close()
$commandKey = $classesKey.CreateSubKey("$ProtocolName\shell\open\command")
$commandKey.SetValue($null, $ProtocolCommand)
$commandKey.Close()
$classesKey.Close()

[Console]::Out.WriteLine('{"ok":true,"shortcut":"' + $ShortcutPath + '"}')
