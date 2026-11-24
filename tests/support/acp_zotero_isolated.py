"""Windows-only disposable Zotero Custom smoke against an owned Pan fixture.

The Zotero hook waits at a barrier until this parent verifies the exact process,
profile, data path and SQLite handle. Existing Zotero processes are never touched.
All output and synthetic data are retained under the candidate's ignored tree.
"""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import uuid

import psutil

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from packages.acp.local import initialize
from tests import test_real_queue_http_e2e as h
from tests.support.acp_zotero_safety import require_no_existing_zotero

if os.name != 'nt':
    raise SystemExit('Windows-only fixture')
source = Path('D:/project/zotero-mcp-codex-live-models-20261124/plugin')
zotero = Path('E:/software/Zotero/zotero.exe')
node = shutil.which('node')
if not zotero.is_file() or not node or not (source / 'node_modules/esbuild').is_dir():
    raise SystemExit('isolated Zotero build prerequisites unavailable')
run = ROOT / '.workflow' / 'acp-acceptance' / ('zotero-' + uuid.uuid4().hex)
profile, library, workspace, data = (run / x for x in ('profile', 'library', 'workspace', 'data'))
for path in (profile / 'extensions', library, workspace, data, run / 'home', run / 'appdata', run / 'localappdata', run / 'shots', run / 'temp'):
    path.mkdir(parents=True, exist_ok=False)
assert all(path.resolve().is_relative_to(run.resolve()) for path in (profile, library, workspace, data))
plugin = run / 'addon'
subprocess.run([node, str(ROOT / 'tests/support/acp_zotero_build.mjs'), str(source), str(plugin)], check=True, timeout=90)
(profile / 'extensions' / 'chat@zotero-mcp').write_text(str(plugin), encoding='utf-8')
with socket.socket() as probe:
    probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]
if port in (8767, 8768, 23119): raise RuntimeError('protected port selected')
prefs = {'extensions.zotero.useDataDir':True, 'extensions.zotero.dataDir':str(library),
         'extensions.zotero.httpServer.port':port, 'extensions.zotero.httpServer.localAPI.enabled':False,
         'extensions.autoDisableScopes':0, 'extensions.enabledScopes':15, 'extensions.startupScanScopes':15,
         'app.update.enabled':False, 'extensions.zotero.sync.autoSync':False,
         'extensions.zotero.debug.log':True,
         'extensions.zotero.automaticScraperUpdates':False, 'extensions.zotero.firstRun2':False,
         'extensions.zotero.firstRunGuidance':False, 'browser.shell.checkDefaultBrowser':False,
         'extensions.zotero-chat.settings':json.dumps({'welcomed':True})}
(profile / 'user.js').write_text(''.join('user_pref(' + json.dumps(k) + ',' + json.dumps(v) + ');\n'
                                        for k, v in prefs.items()), encoding='utf-8')
(data / 'config.json').write_text(json.dumps({'qq':{'enabled':False}, 'wechat':{'enabled':False},
    'remote':{'enabled':False}, 'tailscale':{'startupEnabled':False}, 'lan':{'startupEnabled':False}}))
initialize(data, 'editor', 'default', workspace)
with socket.socket() as probe:
    probe.bind(('127.0.0.1', 0)); pan_port = probe.getsockname()[1]
if pan_port in (8767, 8768, port): raise RuntimeError('protected or conflicting port selected')
h.PORT, h.SERVER_LAUNCHER = pan_port, ROOT / 'tests/support/automation_http_server.py'
runtime = h._PanRuntime(data)
env = dict(os.environ, MOZ_NO_REMOTE='1', HOME=str(run / 'home'), USERPROFILE=str(run / 'home'),
           APPDATA=str(run / 'appdata'), LOCALAPPDATA=str(run / 'localappdata'),
           TMP=str(run / 'temp'), TEMP=str(run / 'temp'),
           ZMC_TEST_SCRIPT=str(ROOT / 'tests/support/acp_zotero_smoke.js'),
           ZMC_TEST_OUT=str(run / 'result.json'), ZMC_SNAPSHOT_DIR=str(run / 'shots'),
           ZMC_DEFAULT_CHAT_FOLDER=str(workspace), ACP_ZOTERO_PROFILE=str(profile),
           ACP_ZOTERO_DATA=str(library), ACP_ZOTERO_IDENTITY=str(run / 'identity.json'),
           ACP_ZOTERO_APPROVED=str(run / 'approved'), ACP_PYTHON=sys.executable,
           ACP_PAN_ROOT=str(run), ACP_SOURCE_ROOT=str(ROOT), ACP_WORKSPACE=str(workspace), PYTHONPATH=str(ROOT))
print('Evidence directory:', run, flush=True)
proc = None
created = None
try:
    # Do not rely on the application's --new-instance flag to protect a
    # running user instance. Abort before launching anything if one exists.
    require_no_existing_zotero(psutil.process_iter())
    runtime.start()
    with (run / 'zotero.log').open('w', encoding='utf-8') as log:
        startup = subprocess.STARTUPINFO(); startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
        proc = subprocess.Popen([str(zotero), '--profile', str(profile), '--new-instance', '-no-remote',
                                 '-ZoteroDebugText'], env=env, stdout=log, stderr=subprocess.STDOUT,
                                startupinfo=startup, creationflags=subprocess.CREATE_NO_WINDOW)
        owned = psutil.Process(proc.pid); created = owned.create_time()
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline and not (run / 'identity.json').is_file():
            if proc.poll() is not None:
                raise RuntimeError(f'isolated Zotero exited before identity barrier (exit={proc.returncode})')
            time.sleep(.2)
        identity = json.loads((run / 'identity.json').read_text(encoding='utf-8'))
        if identity['pid'] != proc.pid or owned.create_time() != created or Path(owned.exe()).resolve() != zotero.resolve():
            raise RuntimeError('Zotero process identity does not match launched process')
        if Path(identity['profile']).resolve() != profile.resolve() or Path(identity['data']).resolve() != library.resolve():
            raise RuntimeError('Zotero reported an unexpected profile or library')
        cmdline = owned.cmdline()
        if str(profile).lower() not in [s.lower() for s in cmdline]:
            raise RuntimeError('Zotero process command lacks disposable profile')
        owned_pids = {owned.pid, *(child.pid for child in owned.children(recursive=True))}
        require_no_existing_zotero(psutil.process_iter(), owned_pids=owned_pids)
        files = [Path(f.path).resolve() for f in owned.open_files() if Path(f.path).name.lower() == 'zotero.sqlite']
        if files != [(library / 'zotero.sqlite').resolve()]:
            raise RuntimeError('could not verify exclusive synthetic Zotero SQLite handle')
        (run / 'os-verification.json').write_text(json.dumps({'pid':proc.pid, 'profileMatches':True,
            'libraryMatches':True, 'sqliteHandleMatches':True, 'distinctLaunchedProcess':True}, indent=2))
        (run / 'approved').write_text('verified', encoding='ascii')
        proc.wait(timeout=120)
    result = json.loads((run / 'result.json').read_text(encoding='utf-8'))
    if not result.get('ok'):
        raise RuntimeError('isolated Zotero smoke failed; see result.json')
    messages = h._received(runtime, cwd=str(workspace), text=lambda value:'Synthetic ACP isolation paper' in value)
    if not messages: raise RuntimeError('synthetic paper context not found in owned provider input')
    print('Zotero smoke passed; synthetic paper reached provider', flush=True)
finally:
    try:
        if proc and proc.poll() is None and created is not None:
            current = psutil.Process(proc.pid)
            if current.create_time() == created and Path(current.exe()).resolve() == zotero.resolve():
                proc.terminate()
                proc.wait(timeout=10)
    finally:
        runtime.stop()
