"""Owned minimal API harness: actual production routes + native registration store.
No Pan lifespan, Workers, product config/data, or launcher execution.
"""
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
for key in tuple(os.environ):
    if key.upper().startswith('PAN_'): os.environ.pop(key)
os.environ['PYTHONPATH'] = str(ROOT)
from fastapi import FastAPI, Body
import uvicorn
from packages.core import login_startup as login
from packages.core import config

with tempfile.TemporaryDirectory(prefix='pan-login-browser-') as temporary:
    home = Path(temporary)
    config.CONFIG_FILE = home / 'config.json'
    os.environ['XDG_CONFIG_HOME'] = str(home / 'xdg')
    os.environ['XDG_CONFIG_DIRS'] = str(home / 'system-xdg')
    os.environ['XDG_CURRENT_DESKTOP'] = 'test-desktop'
    os.environ['DISPLAY'] = ':99'
    name = 'Pan.Test.' + uuid.uuid4().hex
    key = 'Software\\PanAutostartTests\\' + name
    store = login.NativeStore(sys.platform, home=home, name=name, run_key=key)
    manager = login.LoginStartup(Path('D:/中文 Pan') if sys.platform == 'win32' else home / '中文 Pan',
                                 store=store, target_allowed=True)
    login.LoginStartup = lambda: manager
    from packages.web.server import api_get_login_startup, api_put_login_startup
    app = FastAPI()
    app.get('/api/settings/login-startup')(api_get_login_startup)
    app.put('/api/settings/login-startup')(api_put_login_startup)
    original_write = store.write
    denied = False
    def write(value):
        if denied: raise PermissionError('Injected native permission denial')
        original_write(value)
    store.write = write

    @app.post('/__test/control')
    def control(body: dict = Body(...)):
        global denied
        denied = body.get('denied', False)
        if 'foreign' in body: original_write('foreign checkout sentinel' if body['foreign'] else None)
        return {'ok': True}

    @app.post('/__test/shutdown')
    def shutdown():
        server.should_exit = True
        return {'ok': True}

    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    assert sock.getsockname()[1] not in (8767, 8768)
    print(json.dumps({'port': sock.getsockname()[1], 'key': key, 'root': str(home)}), flush=True)
    try:
        server = uvicorn.Server(uvicorn.Config(app, log_level='error', access_log=False))
        server.run(sockets=[sock])
    finally:
        # UUID namespace is exclusively owned. The browser also disables before exit.
        if sys.platform == 'win32':
            import winreg
            try: winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
            except FileNotFoundError: pass
        else: store.path.unlink(missing_ok=True)
