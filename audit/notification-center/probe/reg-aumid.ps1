[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Aumid,
    [string]$DisplayName = 'Pan'
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)

# HKCU-only AppUserModelId registration (user-level, non-admin). Candidate for
# making the shell treat the AUMID as a known app without a Start Menu shortcut.
$classRoot = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Software\Classes', $true)
$key = $classRoot.CreateSubKey("AppUserModelId\$Aumid")
$key.SetValue('DisplayName', $DisplayName)
$key.Close()
$classRoot.Close()

# Read back for evidence.
$check = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey("Software\Classes\AppUserModelId\$Aumid")
[Console]::Out.WriteLine('{"ok":true,"aumid":"' + $Aumid + '","displayName":"' + $check.GetValue('DisplayName') + '"}')
$check.Close()
