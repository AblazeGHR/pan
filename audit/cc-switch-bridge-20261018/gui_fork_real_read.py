"""Authorized real read-only Pan Bridge query. Never refreshes quota or switches."""
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from packages.cc_switch_bridge.provider import Bridge

case = Path(__file__).resolve().parent / 'gui-fork-real-read'
private = case / 'private'
private.mkdir(parents=True, exist_ok=True)
for name in ('CC_SWITCH_TEST_HOME', 'CC_SWITCH_CONFIG_DIR', 'CODEX_HOME'):
    os.environ.pop(name, None)
exe = Path('D:/openTools/cc-switch-pan/cc-switch.exe')
(private / 'settings.json').write_text(json.dumps({'executable': str(exe)}), encoding='utf-8')
source = Path.home()
paths = [source / name for name in ('.cc-switch/cc-switch.db', '.cc-switch/codex_oauth_auth.json',
                                  '.cc-switch/settings.json', '.cc-switch/live-state.json', '.codex/auth.json', '.codex/config.toml')]


def snapshot():
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None for path in paths}


before = snapshot()
bridge = Bridge(private)
apps = {}
for app in ('codex', 'claude', 'gemini'):
    value = bridge.invoke('list', {'app': app})
    apps[app] = {'providers': len(value['providers']), 'accounts': len(value['accounts']),
                 'reauthRequired': sum(account['reauthRequired'] for account in value['accounts']),
                 'currentPresent': value['currentProviderId'] is not None}
assert snapshot() == before, 'Production database/credential/client configuration bytes changed during read-only query'
result = {'passed': True, 'backend': 'installed GUI Fork through real Pan Bridge', 'readOnly': True,
          'productionFilesUnchanged': True, 'realAccountSwitches': 0, 'realQuotaRefreshes': 0, 'apps': apps}
(case / 'result.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps(result))
