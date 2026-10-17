"""Local JSON message bridge: Windows Named Pipe / Unix domain socket, no HTTP."""
from __future__ import annotations

import asyncio
import json
import os
import threading
import uuid
from contextlib import asynccontextmanager
from multiprocessing.connection import Client, Listener
from pathlib import Path

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


class Bridge:
    def __init__(self, runtime, directory):
        self.runtime = runtime
        self.loop = asyncio.get_running_loop()
        self.stopping = threading.Event()
        self.slots = threading.BoundedSemaphore(32)
        unique = uuid.uuid4().hex
        if os.name == "nt":
            self.address = rf"\\.\pipe\pan-plugins-{os.getpid()}-{unique}"
        else:
            # Keep the socket pathname short, while staying in the owning worktree.
            path = Path(directory) / "plugin-ipc"
            path.mkdir(parents=True, exist_ok=True)
            self.address = str(path / f"{unique[:12]}.sock")
        self.listener = Listener(self.address, family=_family(), authkey=None)
        self.thread = threading.Thread(target=self._accept, name="pan-plugin-ipc", daemon=True)
        self.thread.start()

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
                    result = error("plugin_request_failed", str(exc) or "Request did not complete")
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
        await self.runtime.close()


@asynccontextmanager
async def plugin_lifespan(get_manifest, get_session, directory, on_native_change=None):
    runtime = Runtime(get_manifest, get_session, on_native_change, data_root=directory)
    bridge = Bridge(runtime, directory)
    previous = os.environ.get(ENV)
    os.environ[ENV] = bridge.address
    runtime.start()
    try:
        yield runtime
    finally:
        if previous is None:
            os.environ.pop(ENV, None)
        else:
            os.environ[ENV] = previous
        await bridge.close()
