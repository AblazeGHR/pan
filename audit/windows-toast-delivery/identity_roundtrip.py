"""Exercise the source registration fragment twice, with no toast, then clean.

Only a random task-owned AppUserModelId is written. The production AUMID is
read only, to preserve the pre-experiment baseline for MA's visible check.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import uuid
import winreg

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from packages.core import windows_toast

HERE = Path(__file__).resolve().parent
PREFIX = 'Software\\Classes\\AppUserModelId\\'


def production_identity():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, PREFIX + windows_toast.TOAST_APP_ID) as key:
            try:
                value, kind = winreg.QueryValueEx(key, 'DisplayName')
                return {'keyExists': True, 'displayName': value, 'type': kind}
            except FileNotFoundError:
                return {'keyExists': True, 'displayNamePresent': False}
    except FileNotFoundError:
        return {'keyExists': False}


def main():
    result = {'at': datetime.now(timezone.utc).isoformat(),
              'productionBefore': production_identity()}
    app_id = 'Pan.Owned.Identity20261017.' + uuid.uuid4().hex
    path = PREFIX + app_id
    script = (
        "$ErrorActionPreference='Stop'\n"
        "$result=@{ok=$false}\n"
        + windows_toast.build_identity_script(app_id)
        + "$result.ok=$true\n"
        "$result.sessionId=[Diagnostics.Process]::GetCurrentProcess().SessionId\n"
        "$result.sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value\n"
        "$result.interactive=[Environment]::UserInteractive\n"
        "[Console]::Out.WriteLine(($result|ConvertTo-Json -Compress))\n"
    )
    try:
        first = windows_toast._run_powershell(script)
        second = windows_toast._run_powershell(script)
        assert first.get('ok') and first.get('identityRegistered')
        assert second.get('ok') and second.get('identityRegistered')
        sid = first.pop('sid')
        second.pop('sid')
        parent_identity = subprocess.run(['whoami', '/user'], capture_output=True, text=True,
                                        check=True, creationflags=subprocess.CREATE_NO_WINDOW).stdout
        sid_match = re.search(r'S-1-\d+(?:-\d+)+', parent_identity)
        result['helperSameUser'] = bool(sid_match and sid_match.group() == sid)
        result['helperOwnerHash'] = hashlib.sha256(sid.encode()).hexdigest()
        result['first'] = first
        result['second'] = second
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            result['readback'] = winreg.QueryValueEx(key, 'DisplayName')[0]
            assert result['readback'] == 'Pan'
    finally:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as key:
                subkeys, values, _ = winreg.QueryInfoKey(key)
                if subkeys != 0 or values != 1 or winreg.QueryValueEx(key, 'DisplayName')[0] != 'Pan':
                    raise RuntimeError('Unexpected registry changes; refusing cleanup')
                winreg.DeleteValue(key, 'DisplayName')
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
            result['ownedKeyRemoved'] = True
        except FileNotFoundError:
            result['ownedKeyRemoved'] = True
    result['productionAfter'] = production_identity()
    assert result['productionBefore'] == result['productionAfter']
    result['noNotificationsSent'] = True
    (HERE / 'identity-roundtrip.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
