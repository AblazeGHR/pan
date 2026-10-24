"""Owned random-port HTTP fixture with production worker/queue and fake CLI."""
import json
import os
from pathlib import Path
import socket
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
runtime = Path(sys.argv[1]).resolve()
runtime.mkdir(parents=True, exist_ok=True)
for key in list(os.environ):
    if key.startswith('PAN_'):
        del os.environ[key]
os.environ['HANDOFF_TEST_ROOT'] = str(runtime)
for key in ('PAN_BACKGROUND_JOBS_DIR', 'PAN_SCHEDULER_DIR', 'PAN_TERMINALS_DIR'):
    os.environ[key] = str(runtime / key.lower())
os.environ['PAN_SESSION_ERROR_LOG_PATH'] = str(runtime / 'errors.jsonl')
from packages.core import config, session, workspace, worker
config.CONFIG_FILE = runtime / 'config.json'
config.CONFIG_FILE.write_text(json.dumps({'plugin_manifests': [], 'scheduler': {'enabled': False},
    'worker': {'auto_report': {'system': False, 'ma': True, 'user': False}}}), encoding='utf-8')
session.SESSION_DIR = runtime / 'sessions'
workspace.WORKSPACE_DIR = runtime / 'workspaces'
from packages.core.adapters.cbc.adapter import CbcAdapter
CbcAdapter._resolve_cbc_argv = lambda self: [sys.executable, str(Path(__file__).with_name('handoff_existing_fake.py'))]
CbcAdapter.enrich_after_result = lambda *args: None
CbcAdapter.supports_spawn_system_prompt_file = False
from packages.web import server
server.DATA_DIR = runtime / 'data'
server.WORKDIRS_DIR = runtime / 'workdirs'
server.ATTACHMENTS_DIR = runtime / 'attachments'
a = session.create('source-A', adapter='cbc', original_prompt='A metadata only', workdir=str(runtime))
b = session.create('existing-B', adapter='cbc', original_prompt='B original', handoff_prompt='B prior brief',
    cli_session_id='fake-existing-B', workdir=str(runtime),
    history=[{'role': 'user', 'content': 'existing B context'}])
effective = b.system_prompt

sock = socket.socket()
sock.bind(('127.0.0.1', 0))
port = sock.getsockname()[1]
assert port not in (8767, 8768)
identity = {'pid': os.getpid(), 'port': port, 'root': str(ROOT), 'runtime': str(runtime),
    'source': a.id, 'target': b.id, 'effectivePrompt': effective}

@server.app.get('/__handoff/identity')
async def fixture_identity():
    return identity

@server.app.post('/__handoff/coldspawn')
async def coldspawn():
    # Explicit owned test action, only after the current task is idle.
    w = worker.find_worker_by_session(b.id)
    assert w is not None and w.status == 'idle'
    await worker.kill_worker(w.worker_id)
    b.cli_session_id = None
    session.save(b)
    spawned = await worker.create_worker(b.id)
    assert not isinstance(spawned, str), spawned
    return {'ok': True, 'systemPrompt': b.system_prompt}

@server.app.post('/__handoff/shutdown')
async def shutdown():
    for w in list(worker.workers.values()):
        await worker.kill_worker(w.worker_id)
    host.should_exit = True
    return {'ok': True}

import uvicorn
host = uvicorn.Server(uvicorn.Config(server.app, lifespan='off', log_level='warning'))
(runtime / 'identity.json').write_text(json.dumps(identity), encoding='utf-8')
host.run(sockets=[sock])
