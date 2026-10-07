"""Tailnet-only Serve controller. No reset, Funnel, login, or arbitrary targets.

All operations (including config saves) share a lock. A write-ahead ownership
record survives command timeout/process death; recovery only removes an exact
matching, exclusive node port. Background Serve survives abrupt Pan exit until
the next recovery; normal exit removes it. External CLI changes are never adopted.
"""
from __future__ import annotations

import asyncio
import functools
import ipaddress
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading

DEFAULT = {"startupEnabled": False, "protocol": "http", "port": 8768}


def validate(value):
    if not isinstance(value, dict) or set(value) - set(DEFAULT):
        raise ValueError("Invalid Tailscale configuration fields")
    result = {**DEFAULT, **value}
    if type(result["startupEnabled"]) is not bool:
        raise ValueError("startupEnabled must be boolean")
    if result["protocol"] not in ("http", "https"):
        raise ValueError("protocol must be http or https")
    if type(result["port"]) is not int or not 1 <= result["port"] <= 65535:
        raise ValueError("port must be an integer between 1 and 65535")
    return result


def cli(*args):
    binary = shutil.which("tailscale")
    if not binary:
        raise RuntimeError("未安装 Tailscale CLI，或 tailscale 不在 PATH 中")
    try:
        proc = subprocess.run([binary, *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=15,
                              stdin=subprocess.DEVNULL,
                              creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Tailscale 命令超时；结果未确认，请刷新或关闭以协调入口") from exc
    if proc.returncode:
        raise RuntimeError("Tailscale: " + (proc.stderr or proc.stdout or "命令失败")[-2000:].strip())
    return proc.stdout


def snapshot(config, port):
    """Include all state removed by a port-specific off; never delete extra paths."""
    key = str(port)
    return {"tcp": (config.get("TCP") or {}).get(key),
            "web": {k: v for k, v in (config.get("Web") or {}).items() if k.rsplit(":", 1)[-1] == key},
            "funnel": {k: v for k, v in (config.get("AllowFunnel") or {}).items() if k.rsplit(":", 1)[-1] == key},
            "foreground": {k: snapshot(v, port) for k, v in (config.get("Foreground") or {}).items()
                           if occupied(snapshot(v, port))}}


def occupied(part):
    return part["tcp"] is not None or bool(part["web"] or part["funnel"] or part["foreground"])


class Controller:
    def __init__(self, directory: Path, target_port: int, *, run=cli, read_config=None, write_config=None):
        if type(target_port) is not int or not 1 <= target_port <= 65535:
            raise ValueError("Pan runtime port must be between 1 and 65535")
        self.directory = directory
        self.target_port = target_port
        self.run = run
        self.read_config = read_config
        self.write_config = write_config
        self.lock = threading.RLock()
        self.closing = False
        self.error = ""
        self.origins: frozenset[str] = frozenset()
        self.lease = None
        self._workers = set()

    def acquire(self):
        """Only one Pan process may recover/mutate this profile's journal."""
        if self.lease is not None:
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        handle = (self.directory / "runtime.lock").open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                if os.fstat(handle.fileno()).st_size == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise RuntimeError("另一 Pan 实例正在管理此 Tailscale 配置，拒绝操作") from exc
        self.lease = handle

    @property
    def path(self):
        return self.directory / "ownership.json"

    def record(self):
        if not self.path.exists():
            return None
        value = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("version") != 1:
            raise RuntimeError("Tailscale 所有权记录损坏；请人工检查，未修改 Serve")
        validate(value["config"])
        return value

    def persist(self, value):
        self.directory.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        with temp.open("w", encoding="utf-8") as handle:
            json.dump(value, handle)
            handle.flush()
            os.fsync(handle.fileno())
        temp.replace(self.path)

    def settings(self):
        return validate((self.read_config() if self.read_config else {}).get("tailscale", {}))

    def inspect(self):
        node = json.loads(self.run("status", "--json"))
        serve = json.loads(self.run("serve", "status", "--json"))
        if not isinstance(serve, dict):
            raise RuntimeError("无法识别 Serve 状态，未修改配置")
        own = node.get("Self") or {}
        ips = node.get("TailscaleIPs") or own.get("TailscaleIPs") or []
        ip = next((v for v in ips if ipaddress.ip_address(v).version == 4), "")
        dns = str(own.get("DNSName") or "").rstrip(".")
        return node, serve, ip, dns

    def expected(self, config, dns):
        target = f"127.0.0.1:{self.target_port}"
        return {"tcp": {"TCPForward": target} if config["protocol"] == "http" else {"HTTPS": True},
                "web": {} if config["protocol"] == "http" else {
                    f"{dns}:{config['port']}": {"Handlers": {"/": {"Proxy": f"http://{target}"}}}},
                "funnel": {}, "foreground": {}}

    def url(self, config, ip, dns):
        host = ip if config["protocol"] == "http" else dns
        if not host:
            return ""
        port = config["port"]
        suffix = "" if port == (80 if config["protocol"] == "http" else 443) else f":{port}"
        return f"{config['protocol']}://{host}{suffix}"

    def _stop(self):
        self.origins = frozenset()
        record = self.record()
        if not record:
            return
        node, serve, _, _ = self.inspect()
        if record.get("nodeID") != (node.get("Self") or {}).get("ID"):
            raise RuntimeError("Tailscale 账户/设备已变化，未移除入口；请人工检查所有权记录")
        config = record["config"]
        current = snapshot(serve, config["port"])
        if occupied(current):
            if current != record["expected"]:
                raise RuntimeError("入口已被外部修改，未移除；请人工检查 Serve，Pan 保留所有权记录")
            flag = "tcp" if config["protocol"] == "http" else "https"
            self.run("serve", f"--{flag}={config['port']}", "off")
            _, after, _, _ = self.inspect()
            if occupied(snapshot(after, config["port"])):
                raise RuntimeError("关闭后入口仍存在，未确认成功；保留所有权记录")
        self.path.unlink()

    def _start(self, config):
        node, serve, ip, dns = self.inspect()
        if node.get("BackendState") != "Running" or not ip:
            raise RuntimeError("Tailscale 未在线；请先在 Tailscale 应用中连接账户")
        if config["protocol"] == "https" and (not dns or dns not in (node.get("CertDomains") or [])):
            raise RuntimeError("HTTPS 尚未就绪：请在 Tailscale 管理界面启用 HTTPS/证书，再重试；Pan 不自动登录或授权")
        if occupied(snapshot(serve, config["port"])):
            raise RuntimeError("此端口已有 Serve/Funnel 入口，拒绝接管；请选择其它端口")
        expected = self.expected(config, dns)
        node_id = (node.get("Self") or {}).get("ID")
        if not node_id:
            raise RuntimeError("无法确认 Tailscale 设备身份")
        self.persist({"version": 1, "config": config, "expected": expected, "phase": "creating", "nodeID": node_id})
        flag = "tcp" if config["protocol"] == "http" else "https"
        target = f"{'tcp' if flag == 'tcp' else 'http'}://127.0.0.1:{self.target_port}"
        try:
            self.run("serve", "--bg", f"--{flag}={config['port']}", target)
            _, after, _, _ = self.inspect()
            if snapshot(after, config["port"]) != expected:
                raise RuntimeError("Serve 返回后入口不匹配，未确认开启成功")
            self.persist({"version": 1, "config": config, "expected": expected, "phase": "active", "nodeID": node_id})
            self.origins = frozenset({self.url(config, ip, dns)})
        except Exception:
            # Timeout/nonzero can still have changed daemon state. Reconcile before
            # reporting failure; retain journal if cleanup cannot be confirmed.
            self._stop()
            raise

    def status(self, *, probe=True):
        with self.lock:
            try:
                config = self.settings()
            except Exception as exc:
                config = dict(DEFAULT)
                self.error = str(exc)
            result = {"config": config, "running": False, "url": "", "error": self.error,
                      "target": f"http://127.0.0.1:{self.target_port}", "pendingCleanup": self.path.exists()}
            if not probe:
                return result
            try:
                node, serve, ip, dns = self.inspect()
                record = self.record()
                running = bool(record and record.get("phase") == "active" and
                               record.get("nodeID") == (node.get("Self") or {}).get("ID") and
                               snapshot(serve, record["config"]["port"]) == record["expected"] and
                               node.get("BackendState") == "Running")
                active = record["config"] if running else config
                url = self.url(active, ip, dns)
                self.origins = frozenset({url}) if running and url else frozenset()
                result.update(running=running, url=url, ip=ip, dns=dns,
                              online=node.get("BackendState") == "Running", activeConfig=active if running else None)
                if record and not running:
                    result["error"] = self.error or "入口状态与所有权记录不匹配，请关闭重试或人工检查"
            except Exception as exc:
                self.origins = frozenset()
                result["error"] = str(exc)
            return result

    def control(self, action, config=None, *, cancel=None, probe=True):
        with self.lock:
            self.error = ""
            try:
                if self.closing:
                    raise RuntimeError("Pan 正在退出")
                self.acquire()
                if action == "save":
                    value = validate(config)
                    raw = self.read_config()
                    raw["tailscale"] = value
                    self.write_config(raw)
                elif action == "stop":
                    self._stop()
                elif action == "start":
                    value = self.settings()
                    # Changing endpoint is a serialized stop-then-create. Failure
                    # leaves closed rather than silently restoring the old opening.
                    self._stop()
                    self._start(value)
                    if cancel is not None and cancel.is_set():
                        self._stop()
                        raise RuntimeError("开启请求已取消，入口已关闭")
                else:
                    raise ValueError("Unknown action")
                result = self.status(probe=probe)
                return {"ok": action != "start" or result["running"], **result}
            except Exception as exc:
                self.error = str(exc)
                return {"ok": False, **self.status(probe=probe), "error": self.error}

    def startup(self):
        with self.lock:
            try:
                config = self.settings()
                if not self.path.exists() and not config["startupEnabled"]:
                    return {"ok": True, **self.status(probe=False)}
                # Recovery still probes owned state; lifecycle never performs an
                # extra settings-page status probe after cleanup.
                result = self.control("stop", probe=False)
                if result["ok"] and config["startupEnabled"]:
                    return self.control("start")
            except Exception as exc:
                self.error = str(exc)
                return {"ok": False, "error": self.error}
            return result

    def shutdown(self):
        """Synchronous cleanup; operation() releases lease AFTER this worker retires."""
        with self.lock:
            self.closing = True
            if not self.path.exists():
                self.origins = frozenset()
                return {"ok": True, **self.status(probe=False)}
            try:
                self.acquire()
                self._stop()
                return {"ok": True, **self.status(probe=False)}
            except Exception as exc:
                self.error = str(exc)
                return {"ok": False, **self.status(probe=False), "error": self.error}

    def _release_lease(self):
        with self.lock:
            if self.lease:
                self.lease.close()
                self.lease = None

    async def operation(self, method, *args):
        # Keep a shielded executor Future, not a separately cancellable Task.
        # Every cancellation is deferred until the worker has returned. Shutdown
        # lease release is a SECOND worker, submitted only after cleanup retires.
        cancel = threading.Event()
        cancelled = False
        loop = asyncio.get_running_loop()
        if method == self.control and args and args[0] == "start":
            work = functools.partial(method, *args, cancel=cancel)
        else:
            work = functools.partial(method, *args)
        future = loop.run_in_executor(None, work)
        self._workers.add(future)

        async def retire(pending):
            nonlocal cancelled
            while True:
                try:
                    return await asyncio.shield(pending)
                except asyncio.CancelledError:
                    cancel.set()
                    cancelled = True
                    if pending.done():
                        return pending.result()  # Also consume a callable's own cancellation.

        try:
            try:
                return await retire(future)
            finally:
                self._workers.discard(future)
                if method == self.shutdown:
                    # A worker may have released the control mutex but still be
                    # retiring its callable. Do not release its process lease
                    # until every already-submitted worker has actually returned.
                    # Closing admission prevents later controls acquiring it.
                    for pending in tuple(self._workers):
                        try:
                            await retire(pending)
                        except Exception:
                            pass  # Consume failed workers; retirement still proved.
                    await retire(loop.run_in_executor(None, self._release_lease))
        finally:
            if cancelled:
                raise asyncio.CancelledError
