"""Isolated Pan instance for the New Session native folder picker wiring.

The fixture is the existing real-Chromium launcher: real product routes, real
React build, disposable runtime. Only the OS dialog itself is scripted here,
because a native dialog cannot be driven from Chromium; the route, picker
singleton, gate, timeout and cleanup stay the product ones. Real-dialog
behavior is proven separately by ``evidence/new-session-native-directory-*``.
"""
import asyncio
import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

for key in [name for name in os.environ if name.upper().startswith('PAN_')]:
    del os.environ[key]
os.environ.setdefault('PAN_E2E_RUNTIME', str(Path(__file__).parents[1] / 'test-results' / 'native-directory-runtime'))
os.environ.setdefault('PAN_E2E_FAKE_CLI', str(ROOT / 'tests/support/frontend_stream_cli.py'))
os.environ.setdefault('PAN_PORT', '8765')

spec = importlib.util.spec_from_file_location(
    'native_directory_browser_fixture', Path(__file__).with_name('server.py'),
)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
assert fixture.RUNTIME.is_relative_to(ROOT / 'packages/web/test-results')
assert fixture.PORT not in (8767, 8768)
assert os.environ['PAN_E2E_FAKE_CLI'] == str(ROOT / 'tests/support/frontend_stream_cli.py')

PICKED = fixture.WORKDIR / 'picked-folder'
calls: list[dict] = []
# Deterministic script: pending → selected, then cancel, unsupported, busy.
SCRIPT = [
    {'status': 'selected', 'paths': [str(PICKED)], 'delay': 1.2},
    {'status': 'cancelled', 'paths': [], 'delay': 0.0},
    {'status': 'unavailable', 'paths': [], 'delay': 0.0},
    {'status': 'busy', 'paths': [], 'delay': 0.0},
]


async def scripted_pick(directory: str = '', mode: str = 'files'):
    step = SCRIPT[len(calls)] if len(calls) < len(SCRIPT) else {'status': 'cancelled', 'paths': [], 'delay': 0.0}
    calls.append({'directory': directory, 'mode': mode, 'status': step['status']})
    if step['delay']:
        await asyncio.sleep(step['delay'])
    return {'status': step['status'], 'paths': step['paths']}


original_isolation = fixture._install_test_isolation


def isolate(server_module):
    original_isolation(server_module)
    from packages.core import session as session_store
    from packages.web.native_file_picker import native_file_picker

    # The dialog itself is scripted; every request/gate/cleanup path is real.
    native_file_picker.pick = scripted_pick

    @server_module.app.get('/__probe/state')
    async def probe_state():
        return {
            'calls': calls,
            'sessionCount': len(session_store.list_all(load_history=False)),
            'pickerProcessOwned': native_file_picker._process is not None,
        }


fixture._install_test_isolation = isolate
PICKED.mkdir(parents=True, exist_ok=True)
fixture.main()
