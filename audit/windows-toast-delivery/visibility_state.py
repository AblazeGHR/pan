"""Read-only notification gates, no notification contents or settings writes."""
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import winreg

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from packages.core import windows_toast


def registry_value(hive, path, name):
    try:
        with winreg.OpenKey(hive, path, 0, winreg.KEY_READ) as key:
            value, kind = winreg.QueryValueEx(key, name)
        return {'present': True, 'type': kind, 'value': value}
    except FileNotFoundError:
        return {'present': False}
    except OSError as exc:
        return {'error': exc.winerror}


def collect():
    shell = ctypes.WinDLL('shell32', use_last_error=True)
    shell.SHQueryUserNotificationState.argtypes = [ctypes.POINTER(ctypes.c_int)]
    shell.SHQueryUserNotificationState.restype = ctypes.c_long
    state = ctypes.c_int()
    hresult = shell.SHQueryUserNotificationState(ctypes.byref(state))
    names = {1: 'QUNS_NOT_PRESENT', 2: 'QUNS_BUSY', 3: 'QUNS_RUNNING_D3D_FULL_SCREEN',
             4: 'QUNS_PRESENTATION_MODE', 5: 'QUNS_ACCEPTS_NOTIFICATIONS',
             6: 'QUNS_QUIET_TIME', 7: 'QUNS_APP'}
    result = {'at': datetime.now(timezone.utc).isoformat(),
              'shellNotificationState': {'hresult': f'0x{hresult & 0xffffffff:08X}',
                  'state': state.value, 'name': names.get(state.value, 'unknown')},
              'registry': {},
              'doNotDisturb': {'verified': False, 'reason':
                  'No callable Windows desktop UI tool; SHQueryUserNotificationState is not a definitive Windows 11 DND switch query.'}}
    keys = [
        ('userToastEnabled', winreg.HKEY_CURRENT_USER,
         r'Software\Microsoft\Windows\CurrentVersion\PushNotifications', 'ToastEnabled'),
        ('userNoToastPolicy', winreg.HKEY_CURRENT_USER,
         r'Software\Policies\Microsoft\Windows\CurrentVersion\PushNotifications', 'NoToastApplicationNotification'),
        ('machineNoToastPolicy', winreg.HKEY_LOCAL_MACHINE,
         r'Software\Policies\Microsoft\Windows\CurrentVersion\PushNotifications', 'NoToastApplicationNotification'),
        ('userDisableNotificationCenter', winreg.HKEY_CURRENT_USER,
         r'Software\Policies\Microsoft\Windows\Explorer', 'DisableNotificationCenter'),
        ('machineDisableNotificationCenter', winreg.HKEY_LOCAL_MACHINE,
         r'Software\Policies\Microsoft\Windows\Explorer', 'DisableNotificationCenter'),
    ]
    for name in ('Enabled', 'ShowBanner', 'ShowInActionCenter'):
        keys.append(('app' + name, winreg.HKEY_CURRENT_USER,
            r'Software\Microsoft\Windows\CurrentVersion\Notifications\Settings\Pan.Desktop.Notifications', name))
    for label, hive, path, name in keys:
        result['registry'][label] = {'path': path, 'name': name,
                                    **registry_value(hive, path, name)}
    script = (
        "$ErrorActionPreference='Stop'\n"
        "[void][Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime]\n"
        "$notifier=[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Pan.Desktop.Notifications')\n"
        "[Console]::Out.WriteLine((@{ok=$true; setting=[string]$notifier.Setting}|ConvertTo-Json -Compress))\n"
    )
    result['appWinRTSetting'] = windows_toast._run_powershell(script)
    return result


if __name__ == '__main__':
    result = collect()
    (Path(__file__).parent / 'visibility-state.json').write_text(
        json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))
