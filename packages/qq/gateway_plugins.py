"""Manifest-driven lifecycle for locally installed OneBot QQ gateways.

Only processes started by this manager may be stopped. The manifest lives in
data/qq_plugins/manifest.json; the seed is shipped beside this module.
"""

from __future__ import annotations

import copy
import json
import os
import re
import socket
import subprocess
import threading
from pathlib import Path
from urllib.parse import urlsplit

import psutil

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "data" / "qq_plugins" / "manifest.json"
SEED = Path(__file__).with_name("gateway_plugins_manifest.json")
_LOCK = threading.RLock()
_PROCESSES: dict[str, subprocess.Popen] = {}
_IDENTITIES: dict[str, tuple[int, float, list[str]]] = {}
_ID = re.compile(r"^[a-z][a-z0-9_-]{0,47}$")
_CHANNELS = {"napcat", "llonebot", "snowluma", "onebot"}


def _load() -> dict:
    if not MANIFEST.exists():
        MANIFEST.parent.mkdir(parents=True, exist_ok=True)
        MANIFEST.write_text(SEED.read_text(encoding="utf-8"), encoding="utf-8")
    value = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("plugins"), list):
        raise ValueError("QQ plugin manifest must contain a plugins array")
    ids = set()
    for plugin in value["plugins"]:
        _validate(plugin)
        if plugin["id"] in ids:
            raise ValueError(f"duplicate QQ plugin id: {plugin['id']}")
        ids.add(plugin["id"])
    return value


def _validate(plugin: dict) -> None:
    if not isinstance(plugin, dict) or not _ID.fullmatch(str(plugin.get("id", ""))):
        raise ValueError("plugin id must use lowercase letters, digits, _ or -")
    if not isinstance(plugin.get("name"), str) or not plugin["name"].strip():
        raise ValueError("plugin name is required")
    if plugin.get("channel") not in _CHANNELS:
        raise ValueError("plugin channel must be napcat, llonebot, snowluma or onebot")
    url = plugin.get("wsUrl")
    parsed = urlsplit(url) if isinstance(url, str) else None
    if not parsed or parsed.scheme not in {"ws", "wss"} or not parsed.hostname or not parsed.port:
        raise ValueError("plugin wsUrl must be a ws:// or wss:// URL with a port")
    if not isinstance(plugin.get("cwd"), str) or not Path(plugin["cwd"]).is_absolute():
        raise ValueError("plugin cwd must be absolute")
    command = plugin.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(v, str) and v for v in command):
        raise ValueError("plugin command must be a nonempty string array")
    if not Path(command[0]).is_absolute():
        raise ValueError("plugin command executable must be an absolute path")
    if not isinstance(plugin.get("autoStart", False), bool):
        raise ValueError("plugin autoStart must be boolean")
    env = plugin.get("env", {})
    if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
        raise ValueError("plugin env must be a string map")


def _save(value: dict) -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    temp = MANIFEST.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, MANIFEST)


def _plugin(plugin_id: str) -> dict:
    for plugin in _load()["plugins"]:
        if plugin["id"] == plugin_id:
            return plugin
    raise ValueError(f"unknown QQ plugin: {plugin_id}")


def _owned(plugin_id: str) -> psutil.Process | None:
    identity = _IDENTITIES.get(plugin_id)
    if not identity:
        return None
    pid, created, command = identity
    try:
        process = psutil.Process(pid)
        if abs(process.create_time() - created) > 0.01 or process.cmdline() != command:
            return None
        if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
            return None
        return process
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None


def _port_open(url: str) -> bool:
    target = urlsplit(url)
    try:
        with socket.create_connection((target.hostname, target.port), timeout=0.25):
            return True
    except OSError:
        return False


def list_plugins(selected: str | None) -> dict:
    with _LOCK:
        result = []
        for plugin in _load()["plugins"]:
            item = copy.deepcopy(plugin)
            item["running"] = _owned(plugin["id"]) is not None
            item["endpointReachable"] = _port_open(plugin["wsUrl"])
            item["installed"] = Path(plugin["cwd"]).is_dir() and Path(plugin["command"][0]).is_file()
            result.append(item)
        return {"plugins": result, "selected": selected, "manifestPath": str(MANIFEST)}


def start(plugin_id: str) -> dict:
    with _LOCK:
        plugin = _plugin(plugin_id)
        if _owned(plugin_id):
            return {"ok": True, "alreadyRunning": True}
        if _port_open(plugin["wsUrl"]):
            raise ValueError("OneBot endpoint already listens; Pan cannot take ownership of that process")
        cwd = Path(plugin["cwd"])
        executable = Path(plugin["command"][0])
        if not cwd.is_dir() or not executable.is_file():
            raise ValueError("plugin installation or executable is missing")
        log = MANIFEST.parent / f"{plugin_id}.log"
        with log.open("ab") as output:
            proc = subprocess.Popen(plugin["command"], cwd=str(cwd), env={**os.environ, **plugin.get("env", {})}, stdin=subprocess.DEVNULL,
                                    stdout=output, stderr=subprocess.STDOUT, close_fds=True,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0)
        try:
            observed = psutil.Process(proc.pid)
            identity = (proc.pid, observed.create_time(), observed.cmdline())
        except psutil.Error:
            if proc.poll() is None:
                proc.terminate()
            raise
        _PROCESSES[plugin_id] = proc
        _IDENTITIES[plugin_id] = identity
        return {"ok": True, "pid": proc.pid, "logPath": str(log)}


def stop(plugin_id: str) -> dict:
    with _LOCK:
        process = _owned(plugin_id)
        if process is None:
            raise ValueError("Pan does not own a running process for this plugin")
        process.terminate()
        try:
            process.wait(timeout=5)
        except psutil.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        _PROCESSES.pop(plugin_id, None)
        _IDENTITIES.pop(plugin_id, None)
        return {"ok": True}


def set_autostart(plugin_id: str, enabled: bool) -> dict:
    with _LOCK:
        value = _load()
        for plugin in value["plugins"]:
            if plugin["id"] == plugin_id:
                plugin["autoStart"] = enabled
                _save(value)
                return {"ok": True}
        raise ValueError(f"unknown QQ plugin: {plugin_id}")


def auto_start(selected: str | None) -> None:
    if not selected:
        return
    try:
        plugin = _plugin(selected)
        if plugin.get("autoStart"):
            start(selected)
    except (ValueError, OSError, psutil.Error) as exc:
        print(f"[QQ plugins] automatic start failed: {exc}")


def stop_all() -> None:
    for plugin_id in list(_IDENTITIES):
        try:
            stop(plugin_id)
        except (ValueError, OSError, psutil.Error) as exc:
            print(f"[QQ plugins] stop {plugin_id} failed: {exc}")
