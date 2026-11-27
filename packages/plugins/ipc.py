"""Local JSON message bridge: Windows Named Pipe / Unix domain socket, no HTTP."""
from __future__ import annotations

import asyncio
import json
import os
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from multiprocessing.connection import Client, Listener

from .runtime import Runtime, error

ENV = "PAN_PLUGIN_IPC"
MAX_FRAME = 2 * 1024 * 1024


def _family():
    return "AF_PIPE" if os.name == "nt" else "AF_UNIX"


def request(message: dict, address=None):
    address = address or os.environ.get(ENV)
    if not address:
        raise RuntimeError("Pan plugin runtime is not connected")
    raw = json.dumps(message, ensure_ascii=False).encode()
    if len(raw) > MAX_FRAME:
        raise ValueError("Plugin request exceeds frame limit")
    with Client(address, family=_family(), authkey=None) as connection:
        connection.send_bytes(raw)
        if not connection.poll(40):
            raise TimeoutError("Plugin response unconfirmed; do not automatically replay")
        return json.loads(connection.recv_bytes(MAX_FRAME))


class _ScriptRuntime:
    def __init__(self, runtime):
        self.runtime = runtime

    async def handle(self, message):
        fields = {'session_id', 'operation', 'profile', 'credential', 'method', 'path', 'body'}
        if (not isinstance(message, dict) or set(message) != fields or message['session_id'] != ''
                or message['operation'] != 'script_http'):
            return {'ok': False, 'error': {'code': 403, 'message': 'Script operation refused'}}
        callback = getattr(self.runtime, 'script_http', None)
        if callback is None or self.runtime.closing:
            return {'ok': False, 'error': {'code': 403, 'message': 'Script access unavailable'}}
        task = asyncio.current_task()
        self.runtime.requests.add(task)
        try:
            return await callback(message['profile'], message['credential'], message['method'], message['path'], message['body'])
        finally:
            self.runtime.requests.discard(task)

    async def close(self):
        pass


class Bridge:
    def __init__(self, runtime, directory, *, script_only=False):
        self.script_only = script_only
        self.runtime = _ScriptRuntime(runtime) if script_only else runtime
        self.loop = asyncio.get_running_loop()
        self.stopping = threading.Event()
        self.slots = threading.BoundedSemaphore(32)
        unique = uuid.uuid4().hex
        self.endpoint = None
        if os.name == "nt":
            prefix = 'pan-script' if script_only else 'pan-plugins'
            self.address = rf"\\.\pipe\{prefix}-{os.getpid()}-{unique}"
        else:
            if script_only:
                raise OSError('script IPC is unsupported on this platform')
            from packages.core.platform.plugin_ipc import UnixEndpoint
            self.endpoint = UnixEndpoint()
            self.address = self.endpoint.address
        try:
            if script_only:
                from packages.core.platform.script_ipc import listener
                self.listener = listener(self.address)
            else:
                self.listener = Listener(self.address, family=_family(), authkey=None)
            self.thread = threading.Thread(target=self._accept, name="pan-plugin-ipc", daemon=True)
            self.thread.start()
        except BaseException:
            if hasattr(self, 'listener'):
                self.listener.close()
            if self.endpoint is not None:
                self.endpoint.close()
            raise

    def _accept(self):
        while not self.stopping.is_set():
            try:
                connection = self.listener.accept()
            except (OSError, EOFError):
                break
            if self.stopping.is_set():
                connection.close()
                break
            if not self.slots.acquire(blocking=False):
                connection.close()
                continue
            threading.Thread(target=self._serve, args=(connection,), daemon=True).start()

    def _serve(self, connection):
        try:
            with connection:
                if not connection.poll(3):
                    return
                message = json.loads(connection.recv_bytes(MAX_FRAME))
                future = asyncio.run_coroutine_threadsafe(self.runtime.handle(message), self.loop)
                try:
                    result = future.result(38)
                except Exception as exc:
                    result = (error("plugin_request_failed", "Script request failed") if self.script_only
                              else error("plugin_request_failed", str(exc) or "Request did not complete"))
                raw = json.dumps(result, ensure_ascii=False).encode()
                if len(raw) > MAX_FRAME:
                    raw = json.dumps(error("result_too_large", "Plugin result exceeds frame limit")).encode()
                connection.send_bytes(raw)
        except (OSError, EOFError, ValueError):
            pass
        finally:
            self.slots.release()

    async def close(self):
        self.stopping.set()
        # Wake accept before closing the listener; a blocked Windows accept does
        # not reliably retire when another thread merely closes its handle.
        def wake():
            try:
                with Client(self.address, family=_family(), authkey=None):
                    pass
            except OSError:
                pass
            self.listener.close()
            self.thread.join(3)
        await asyncio.to_thread(wake)
        if self.thread.is_alive():
            raise RuntimeError('Plugin IPC accept thread cleanup unconfirmed')
        try:
            await self.runtime.close()
        finally:
            if self.endpoint is not None:
                self.endpoint.close()


@asynccontextmanager
async def plugin_lifespan(get_manifest, get_session, directory, on_native_change=None):
    runtime = Runtime(get_manifest, get_session, on_native_change, data_root=directory)
    try:
        bridge = Bridge(runtime, directory)
    except BaseException:
        await runtime.close()
        raise
    script_bridge = None
    script_locator = Path(directory) / 'script-ipc.json'
    try:
        if os.name == 'nt':
            script_bridge = Bridge(runtime, directory, script_only=True)
            temp_script = script_locator.with_name(script_locator.name + '.' + uuid.uuid4().hex + '.tmp')
            temp_script.parent.mkdir(parents=True, exist_ok=True)
            fd_script = os.open(temp_script, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd_script, 'w', encoding='utf-8') as stream:
                json.dump({'address': script_bridge.address, 'pid': os.getpid()}, stream)
            os.replace(temp_script, script_locator)
        # Locator only, not an HTTP credential. The existing OS-local pipe/socket
        # remains the provenance boundary; CLI operations have their own scope.
        locator = Path(directory) / 'core-ipc.json'
        locator.parent.mkdir(parents=True, exist_ok=True)
        temporary = locator.with_name(locator.name + '.' + uuid.uuid4().hex + '.tmp')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump({'address': bridge.address, 'pid': os.getpid()}, stream)
        os.replace(temporary, locator)
    except BaseException:
        if 'temp_script' in locals():
            temp_script.unlink(missing_ok=True)
        if 'temporary' in locals():
            temporary.unlink(missing_ok=True)
        try:
            if script_bridge is not None:
                await script_bridge.close()
                try:
                    if json.loads(script_locator.read_text(encoding='utf-8')).get('address') == script_bridge.address:
                        script_locator.unlink()
                except FileNotFoundError:
                    pass
        finally:
            await bridge.close()
        raise
    previous = os.environ.get(ENV)
    os.environ[ENV] = bridge.address
    try:
        runtime.start()
        yield runtime
    finally:
        if previous is None:
            os.environ.pop(ENV, None)
        else:
            os.environ[ENV] = previous
        try:
            if script_bridge is not None:
                await script_bridge.close()
                try:
                    if json.loads(script_locator.read_text(encoding='utf-8')).get('address') == script_bridge.address:
                        script_locator.unlink()
                except FileNotFoundError:
                    pass
        finally:
            await bridge.close()
        try:
            if json.loads(locator.read_text(encoding='utf-8')).get('address') == bridge.address:
                locator.unlink()
        except FileNotFoundError:
            pass
