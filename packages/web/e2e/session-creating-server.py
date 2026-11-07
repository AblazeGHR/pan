"""Owned real-server gate: delay before persistence and after WS broadcast, no sleeps."""
import asyncio
import importlib.util
from pathlib import Path
from fastapi import Body
from fastapi.responses import JSONResponse

spec = importlib.util.spec_from_file_location('fixture', Path(__file__).with_name('server.py'))
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
import packages.web.server as server

# This fixture intentionally loads no plugins. Explicitly disable MCP in its
# creation parameters; persistence, HTTP handlers and WS remain production code.
build_params = server._build_session_params
def isolated_params(data, **kwargs):
    return build_params({**data, 'mcpServers': []}, **kwargs)
server._build_session_params = isolated_params

gates = {}
mode = 'success'

@server.app.post('/__creating/arm')
async def arm(data: dict = Body(...)):
    global mode
    mode = data.get('mode', 'success')
    gates.clear()
    gates.update(before=asyncio.Event(), after=asyncio.Event(), entered=False, committed=False)
    return {'ok': True}

@server.app.post('/__creating/release/{phase}')
async def release(phase: str):
    gates[phase].set()
    return {'ok': True}

@server.app.get('/__creating/state')
async def state():
    return {key: gates.get(key, False) for key in ('entered', 'committed')}

@server.app.middleware('http')
async def gate(request, call_next):
    if request.url.path != '/api/sessions' or request.method != 'POST' or not gates:
        return await call_next(request)
    gates['entered'] = True
    await gates['before'].wait()
    if mode == 'failure':
        return JSONResponse({'error': 'controlled creation failure'})
    response = await call_next(request)
    gates['committed'] = True
    await gates['after'].wait()
    return response

fixture.main()
