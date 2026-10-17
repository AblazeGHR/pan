"""Owned real-main/React runtime for native-click acceptance; sends no toasts.

Background Job invokes --serve. --check verifies readiness and identity before
handing control to MA; --stop verifies the same PID creation time/command and
uses the production graceful shutdown endpoint. No other service is touched.
"""
import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import runpy
import socket
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def owned_path(raw):
    path = Path(raw).resolve()
    if path.parent != HERE or not path.name.startswith('runtime-click-'):
        raise ValueError('Runtime must be an immediate task-owned runtime-click-* directory')
    return path


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def creation_filetime(pid):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        values = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *[ctypes.byref(value) for value in values]):
            raise ctypes.WinError(ctypes.get_last_error())
        return (values[0].dwHighDateTime << 32) | values[0].dwLowDateTime
    finally:
        kernel.CloseHandle(handle)


def verified_identity(runtime):
    import psutil
    identity = json.loads((runtime / 'identity.json').read_text(encoding='utf-8'))
    process = psutil.Process(identity['pid'])
    if creation_filetime(process.pid) != identity['creationFiletime']:
        raise RuntimeError('PID creation identity changed')
    command = process.cmdline()
    normalized_command = {str(Path(arg).resolve()).casefold() for arg in command if not arg.startswith('--')}
    if str(Path(__file__).resolve()).casefold() not in normalized_command or str(runtime).casefold() not in normalized_command:
        raise RuntimeError('Process command does not match owned runtime')
    owned_connections = process.net_connections(kind='tcp')
    if not any(connection.status == 'LISTEN' and connection.laddr.ip == '127.0.0.1'
               and connection.laddr.port == identity['port'] for connection in owned_connections):
        raise RuntimeError('Expected owned listener is absent')
    return identity


def request(identity, route, *, stop=False):
    if identity['port'] in (8767, 8768):
        raise RuntimeError('Protected port refused')
    req = urllib.request.Request(identity['origin'] + route,
        data=b'' if stop else None, method='POST' if stop else 'GET',
        headers={'X-Pan-Owned-Preflight': '1'})
    with urllib.request.urlopen(req, timeout=8) as response:
        return response.status, response.read()


def serve(runtime):
    import psutil
    # Apply the same narrow storage substitution as existing isolated_http_server
    # before running the real entry point. Never inherit a deployed config/root.
    for key in tuple(os.environ):
        if key.upper().startswith('PAN_') or key.upper() == 'PYTHONPATH':
            os.environ.pop(key)
    os.environ['PYTHONPATH'] = str(ROOT)
    runtime.mkdir(parents=True, exist_ok=False)
    data = runtime / 'data'
    data.mkdir()
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    assert port not in (8767, 8768)
    origin = f'http://127.0.0.1:{port}'
    os.environ.update(PAN_PORT=str(port), PAN_HOST='127.0.0.1', PAN_API_URL=origin,
        PAN_BACKGROUND_JOBS_DIR=str(data / 'background_jobs'),
        PAN_SCHEDULER_DIR=str(data / 'scheduler'))
    from packages.core import config, session, workspace, reminders, background_jobs, data_catalog
    from packages.core.terminal import registry, secret_store
    from packages.scheduler import store as scheduler
    config.CONFIG_FILE = runtime / 'config.json'
    write_json(config.CONFIG_FILE, {
        'port': port, 'plugin_manifests': [], 'scheduler': {'enabled': False},
        'qq': {'enabled': False, 'plugin_id': 'owned-disabled'}, 'wechat': {'enabled': False},
        'tailscale': {'startupEnabled': False}, 'lan': {'startupEnabled': False},
        'logging': {'file': str(data / 'logs/pan.log'), 'console': True},
        'ui': {'session_lifecycle': {'startupPreference': 'ask'}}})
    session.SESSION_DIR = data / 'sessions'
    workspace.WORKSPACE_DIR = data / 'workspaces'
    reminders.REMINDER_PATH = data / 'reminders.json'
    background_jobs.DEFAULT_ROOT = data / 'background_jobs'
    scheduler.DEFAULT_ROOT = data / 'scheduler'
    scheduler.LEGACY_TASKS_ROOT = data / 'legacy-scheduler'
    registry.DEFAULT_REGISTRY_ROOT = data / 'terminals'
    secret_store.DEFAULT_SECRETS_ROOT = data / 'terminals'
    data_catalog.DATA_ROOT = data
    import packages.web.server as web
    web._PROJECT_DIR = runtime
    web.DATA_DIR = data
    web.WORKDIRS_DIR = data / 'workdirs'
    web.ATTACHMENTS_DIR = data / 'attachments'
    workdir = data / 'session-workdir'
    workdir.mkdir(parents=True)
    marker = 'OWNED-TOAST-CLICK-SESSION-HISTORY-20261017'
    target = session.create('OWNED Toast click acceptance 20261017', adapter='codex',
        workdir=str(workdir), model='gpt-6.1-sol',
        notification_settings={'browser': False, 'system': False},
        history=[{'role': 'user', 'content': 'Owned Windows toast click acceptance target.'},
                 {'role': 'assistant', 'content': marker}])
    session.save(target)
    process = psutil.Process()
    identity = {'pid': os.getpid(), 'creationFiletime': creation_filetime(os.getpid()),
        'createdUtc': datetime.fromtimestamp(process.create_time(), timezone.utc).isoformat(),
        'command': process.cmdline(), 'checkout': str(ROOT), 'runtime': str(runtime),
        'dataDir': str(data), 'configFile': str(config.CONFIG_FILE), 'port': port, 'origin': origin,
        'sessionId': target.id, 'sessionName': target.name,
        'deepLink': origin + '/react/?session=' + target.id, 'historyMarker': marker,
        'distSource': str(web.REACT_DIST_DIR.resolve()),
        'indexSha256': hashlib.sha256((web.REACT_DIST_DIR / 'index.html').read_bytes()).hexdigest(),
        'startup': 'runpy main.py __main__; real Uvicorn and production lifespan',
        'notificationsSentByLauncher': False}
    write_json(runtime / 'identity.json', identity)

    @web.app.middleware('http')
    async def observe_owned_access(request, call_next):
        if request.url.path == '/react/' or request.url.path == '/api/sessions/' + target.id:
            record = {'at': datetime.now(timezone.utc).isoformat(), 'path': request.url.path,
                'query': request.url.query, 'preflight': request.headers.get('X-Pan-Owned-Preflight') == '1',
                'userAgent': request.headers.get('User-Agent', '')[:180]}
            with (runtime / 'access.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(record) + '\n')
        return await call_next(request)

    try:
        runpy.run_path(str(ROOT / 'main.py'), run_name='__main__')
    finally:
        write_json(runtime / 'stopped.json', {'at': datetime.now(timezone.utc).isoformat(), 'pid': os.getpid()})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--serve', action='store_true')
    modes.add_argument('--check', action='store_true')
    modes.add_argument('--stop', action='store_true')
    parser.add_argument('--runtime', required=True)
    args = parser.parse_args()
    runtime = owned_path(args.runtime)
    if args.serve:
        return serve(runtime)
    identity = verified_identity(runtime)
    if args.stop:
        status, body = request(identity, '/api/internal/main/shutdown', stop=True)
        print(json.dumps({'httpStatus': status, 'shutdownReceipt': json.loads(body)}))
        return
    health_status, health = request(identity, '/api/health')
    session_status, target = request(identity, '/api/sessions/' + identity['sessionId'])
    page_status, page = request(identity, '/react/?session=' + identity['sessionId'])
    assert health_status == session_status == page_status == 200
    assert identity['sessionId'].encode() in target and b'<html' in page.lower()
    active_dist = ROOT / 'packages/web/dist'
    evidence = {**identity, 'health': json.loads(health),
        'initialDistSource': identity['distSource'],
        'initialIndexSha256': identity['indexSha256'],
        'distSource': str(active_dist.resolve()),
        'indexSha256': hashlib.sha256((active_dist / 'index.html').read_bytes()).hexdigest(),
        'activeDistSource': str(active_dist.resolve()),
        'activeIndexSha256': hashlib.sha256((active_dist / 'index.html').read_bytes()).hexdigest(),
        'checks': {'sessionApi': session_status, 'deepLinkHtml': page_status},
        'preflightCompletedAt': datetime.now(timezone.utc).isoformat()}
    write_json(HERE / 'runtime-ready.json', evidence)
    print(json.dumps(evidence, indent=2))


if __name__ == '__main__':
    main()
