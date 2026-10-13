"""An opt-in listener for the existing app; no proxy, provider or extra lifespan."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import ipaddress
import socket

import psutil
import uvicorn

DEFAULT = {"startupEnabled": False, "host": "", "port": 8780}


def addresses():
    return sorted({item.address for items in psutil.net_if_addrs().values() for item in items
                   if item.family == socket.AF_INET
                   and not ipaddress.ip_address(item.address).is_loopback
                   and not ipaddress.ip_address(item.address).is_link_local})


def validate(value, *, check_local=True):
    if not isinstance(value, dict) or set(value) - set(DEFAULT):
        raise ValueError("Invalid LAN configuration fields")
    result = {**DEFAULT, **value}
    if type(result["startupEnabled"]) is not bool:
        raise ValueError("startupEnabled must be boolean")
    if type(result["port"]) is not int or not 1 <= result["port"] <= 65535:
        raise ValueError("port must be an integer between 1 and 65535")
    host = result["host"]
    if not isinstance(host, str):
        raise ValueError("host must be an IPv4 address")
    if host:
        try:
            address = ipaddress.IPv4Address(host)
        except ValueError as exc:
            raise ValueError("host must be an IPv4 address") from exc
        if address.is_loopback or address.is_multicast or host == "255.255.255.255":
            raise ValueError("请选择本机非回环 IPv4 地址")
    if check_local and host and host != "0.0.0.0" and host not in addresses():
        raise ValueError("请选择当前本机 IPv4 地址；0.0.0.0 表示所有接口")
    return result


class ListenerApp:
    """Keep process identities and local administration off this listener.

    Even loopback clients reaching an all-interface listener are remote here.
    Forwarded headers are never used, and Uvicorn proxy_headers is disabled.
    """
    def __init__(self, app, origins):
        self.app, self.origins = app, origins

    async def __call__(self, scope, receive, send):
        headers = dict(scope.get("headers", []))
        host = headers.get(b"host", b"").decode("latin1")
        origin = headers.get(b"origin", b"").decode("latin1")
        path = scope["path"]
        private = path.startswith(("/api/internal/", "/api/self-queue/", "/api/terminal-tools/",
                                   "/api/mcp/", "/api/codex/quota")) or path == "/ws/agent"
        management = path.startswith(("/api/remote/", "/api/settings/", "/api/config/",
                                      "/api/main/", "/api/manifest/")) or path in {"/api/claim", "/api/unclaim", "/api/readonly"}
        allowed = f"http://{host}" in self.origins
        allowed = allowed and (not origin or origin == f"http://{host}")
        if scope["type"] == "websocket":
            allowed = allowed and bool(origin)
        if private or not allowed or (management and scope.get("method") not in {"GET", "HEAD"}):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                await send({"type": "http.response.start", "status": 403,
                            "headers": [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body", "body": b'{"detail":"LAN entry: forbidden host/origin or local administration required"}'})
            return
        await self.app({**scope, "pan_lan_entry": True}, receive, send)


class LanServer(uvicorn.Server):
    def install_signal_handlers(self):
        # Uvicorn versions predating capture_signals use this method instead.
        pass

    @contextmanager
    def capture_signals(self):
        # The main server remains the sole owner of process signals.
        yield


class Controller:
    def __init__(self, app, *, read_config, write_config):
        self.app, self.read_config, self.write_config = app, read_config, write_config
        self.lock = asyncio.Lock()
        self.server = self.task = self.socket = None
        self.active_config = None
        self.origins = frozenset()
        self.error = ""
        self.closing = False

    def config(self):
        return validate(self.read_config().get("lan", {}), check_local=False)

    def status(self):
        try:
            config = self.config()
        except Exception as exc:
            config = None
            self.error = str(exc)
        running = (self.task is not None and not self.task.done() and self.server.started
                   and not self.server.should_exit and bool(self.origins))
        return {"config": config, "activeConfig": self.active_config, "running": running,
                "addresses": addresses(), "urls": sorted(self.origins) if running else [],
                "error": self.error, "localManagementOnly": True}

    async def _settle(self, task):
        """Keep ownership through repeated caller cancellation, then report it.

        Await the actual task's terminal event; never cancel it or poll for it.
        A terminal task's own exception/cancellation is propagated separately.
        """
        cancelled = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancelled = True
            except Exception:
                break
        try:
            task.result()
        except Exception as exc:
            if cancelled:
                self.error = str(exc)
                raise asyncio.CancelledError from exc
            raise
        return cancelled

    async def shutdown(self):
        # Closing is visible before waiting for the operation owner. Its stop
        # must survive cancellation even while still waiting to acquire lock.
        self.closing = True
        owner = asyncio.create_task(self.operation("stop"))
        if await self._settle(owner):
            raise asyncio.CancelledError

    async def stop(self):
        self.origins = frozenset()
        if self.server is not None:
            self.server.should_exit = True
        cancelled = False
        try:
            if self.task is not None:
                cancelled = await self._settle(self.task)
        finally:
            if self.task is None or self.task.done():
                if self.socket is not None:
                    self.socket.close()
                self.server = self.task = self.socket = None
                self.active_config = None
        if cancelled:
            raise asyncio.CancelledError

    async def start(self):
        config = validate(self.config())
        if not config["host"]:
            raise ValueError("请先选择并保存本机监听地址")
        if self.task is not None and not self.task.done():
            if (self.active_config == config and self.server.started
                    and not self.server.should_exit and self.origins):
                return
            raise ValueError("请先关闭当前 LAN 入口，再应用新配置")
        if self.socket is not None:
            self.socket.close()
        hosts = addresses() if config["host"] == "0.0.0.0" else [config["host"]]
        if not hosts:
            raise ValueError("没有可用的本机 IPv4 地址")
        origins = frozenset(f"http://{host}:{config['port']}" for host in hosts)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            # On Windows, do not silently share a port owned by another process.
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            sock.bind((config["host"], config["port"]))
            sock.listen(128)
            sock.setblocking(False)
            server = LanServer(uvicorn.Config(ListenerApp(self.app, origins), lifespan="off",
                proxy_headers=False, log_level="warning", access_log=False,
                timeout_graceful_shutdown=2))
            self.socket, self.server = sock, server
            self.task = asyncio.create_task(server.serve(sockets=[sock]))
            for _ in range(200):
                if self.task.done():
                    await self.task
                    raise RuntimeError("LAN listener exited before startup")
                if server.started:
                    if self.closing:
                        raise ValueError("Pan is shutting down")
                    self.active_config, self.origins = dict(config), origins
                    self.task.add_done_callback(self._finished)
                    return
                await asyncio.sleep(0.01)
            raise RuntimeError("LAN listener startup timed out")
        except BaseException:
            await self.stop()
            sock.close()
            raise

    def _finished(self, task):
        if task is not self.task:
            if not task.cancelled():
                task.exception()
            return
        self.origins = frozenset()
        if self.socket is not None:
            self.socket.close()
        if not task.cancelled() and task.exception() is not None:
            self.error = f"LAN listener stopped: {task.exception()}"
        elif self.server is not None and not self.server.should_exit:
            self.error = "LAN listener stopped unexpectedly; refresh and retry"

    async def operation(self, action, value=None):
        async with self.lock:
            try:
                if action == "status":
                    return self.status()
                if self.closing and action != "stop":
                    raise ValueError("Pan is shutting down")
                if action == "save":
                    config = validate(value)
                    raw = self.read_config()
                    self.write_config({**raw, "lan": config})
                elif action == "start":
                    await self.start()
                elif action == "stop":
                    await self.stop()
                elif action == "startup":
                    if self.config()["startupEnabled"]:
                        await self.start()
                else:
                    raise ValueError("Unknown LAN action")
                self.error = ""
            except Exception as exc:
                self.error = str(exc)
            return self.status()
