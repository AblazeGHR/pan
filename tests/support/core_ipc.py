"""Owned trusted stdio fixture for pre-existing transport/terminal regressions."""
import sys
import asyncio
import threading
from contextlib import asynccontextmanager
from packages.core.manifest_loader import ManifestConfig, McpServer
from packages.core.session import Session
from packages.plugins.runtime import Runtime
from packages.plugins.ipc import Bridge
from packages.web.machine_ipc import bind


def core_bridge(app, root, sid='caller', caller=None):
    manifest = ManifestConfig(mcp_servers=[McpServer('pan', command=sys.executable,
                                                   args=['-m', 'packages.mcp.server'])])
    caller = caller or Session(id=sid, name='Owned IPC fixture')
    caller.adapter_config['plugin_bindings'] = [{'plugin_id': 'pan', 'mode': 'standalone', 'enabled': True}]
    runtime = Runtime(lambda: manifest, lambda value: caller if value == caller.id else None)
    bind(runtime, app, lambda value: caller if value == caller.id else None)
    return Bridge(runtime, root)


@asynccontextmanager
async def independent_core_bridge(app, root, sid='caller', caller=None):
    """Serve actual IPC while an in-memory MCP client can block its own loop."""
    ready, stop = threading.Event(), threading.Event()
    state = {}

    async def serve():
        try:
            state['bridge'] = core_bridge(app, root, sid=sid, caller=caller)
        except BaseException as exc:
            state['error'] = exc
            ready.set()
            return
        ready.set()
        try:
            await asyncio.to_thread(stop.wait)
        finally:
            await state['bridge'].close()

    thread = threading.Thread(target=lambda: asyncio.run(serve()), daemon=True)
    thread.start()
    try:
        assert await asyncio.to_thread(ready.wait, 10), 'owned IPC bridge did not start'
        if 'error' in state:
            raise state['error']
        yield state['bridge']
    finally:
        stop.set()
        await asyncio.to_thread(thread.join, 10)
        assert not thread.is_alive(), 'owned IPC bridge did not stop'
