"""Checkout-owned MCP gateway and named tunnel lifecycle; never adopt foreign PIDs."""
import contextlib
import json
import os
import re
from pathlib import Path
import subprocess
import time

from packages.core import launcher


def _directory(root):
    path = Path(root) / "data" / "mcp_remote"
    path.mkdir(parents=True, exist_ok=True)
    return path


@contextlib.contextmanager
def _lock(root):
    with (_directory(root) / "runtime.lock").open("a+b") as handle:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def _read(root):
    path = _directory(root) / "state.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save(root, state):
    path = _directory(root) / "state.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state), encoding="utf-8")
    temporary.replace(path)


def _identity(root, record):
    if not record:
        return {"ok": False}
    return launcher.process_identity(record.get("pid"), root, record.get("createdAt"),
        process_type=record.get("processType"), marker=record.get("marker"),
        entry_marker=record.get("entry"))


def status(root):
    with _lock(root):
        config = launcher.load_config(root).get("mcp_remote") or {}
        state = _read(root)
        result = {"enabled": config.get("enabled") is True,
                  "publicUrl": "https://" + config["public_hostname"] + "/mcp" if config.get("public_hostname") else "",
                  "port": config.get("port", 9742)}
        for name in ("gateway", "tunnel"):
            record = state.get(name)
            identity = _identity(root, record)
            result[name] = {"running": bool(identity.get("ok")),
                            "pid": record.get("pid") if record else None}
        result["gateway"]["listening"] = (result["gateway"]["running"] and
            launcher.listener_owner(result["port"]) == result["gateway"]["pid"])
        return result


def _stop(root, state):
    for name in ("tunnel", "gateway"):
        if not state.get(name):
            continue
        result = launcher._terminate_record(state.get(name), Path(root), log_path=None)
        if not result["stopped"]:
            raise launcher.LauncherError(f"Refusing to stop {name}: {result['reason']}")
        state.pop(name, None)
        _save(root, state)


def _spawn(root, state, name, argv, marker, process_type, entry):
    with (_directory(root) / (name + ".log")).open("a", encoding="utf-8") as log:
        process = subprocess.Popen(argv, cwd=str(root), stdin=subprocess.DEVNULL,
            stdout=log, stderr=subprocess.STDOUT, close_fds=True,
            creationflags=launcher._creation_flags(True))
    # Windows venv launchers can spawn a second interpreter: record the real child.
    pid = process.pid
    if name == "gateway":
        deadline = time.monotonic() + 15
        port = launcher.load_config(root)["mcp_remote"].get("port", 9742)
        import psutil
        while time.monotonic() < deadline:
            owner = launcher.listener_owner(port)
            children = [p.pid for p in psutil.Process(process.pid).children(recursive=True)] if process.poll() is None else []
            if owner in [process.pid, *children]:
                pid = owner
                break
            if process.poll() is not None:
                break
            time.sleep(.1)
    record = launcher._record(pid, Path(root), process_type, argv,
                              launcher.process_create_time(pid), marker)
    record["entry"] = entry
    state[name] = record
    _save(root, state)
    if not _identity(root, record).get("ok"):
        raise launcher.LauncherError(f"{name} failed to start; see data/mcp_remote/{name}.log")
    if name == "gateway" and launcher.listener_owner(port) != pid:
        raise launcher.LauncherError("MCP gateway did not become ready")


def control(root, action):
    root = launcher.checkout_root(root)
    with _lock(root):
        state = _read(root)
        if action in ("stop", "restart"):
            _stop(root, state)
        if action == "stop":
            return {"ok": True}
        if action not in ("start", "restart"):
            raise launcher.LauncherError("Unknown MCP lifecycle action")
        config = launcher.load_config(root)
        settings = config.get("mcp_remote") or {}
        if settings.get("enabled") is not True:
            raise launcher.LauncherError("Enable mcp_remote in config.json first")
        port = int(settings.get("port", 9742))
        if not 1024 <= port <= 65535 or port == int(config.get("port", 8768)):
            raise launcher.LauncherError("MCP port must be separate from the Pan API")
        from packages.remote.mcp_gateway import validate_config
        validate_config(settings)
        source = Path(settings.get("config_path", "")).expanduser()
        if not source.is_absolute():
            source = root / source
        if not source.is_file():
            raise launcher.LauncherError("Named MCP tunnel config not found")
        content = source.read_text(encoding="utf-8")
        frontend = (config.get("remote") or {}).get("config_path")
        if frontend:
            frontend_path = Path(frontend).expanduser()
            if not frontend_path.is_absolute():
                frontend_path = root / frontend_path
            if frontend_path.is_file():
                ids = [re.search(r"(?m)^tunnel:\s*(\S+)", text) for text in
                       (content, frontend_path.read_text(encoding="utf-8"))]
                if all(ids) and ids[0].group(1) == ids[1].group(1):
                    raise launcher.LauncherError("Use a dedicated MCP tunnel, not a replica of the frontend tunnel")
        if f"http://127.0.0.1:{port}" not in content:
            raise launcher.LauncherError("MCP tunnel must target the configured loopback port")
        binary = launcher._cloudflared_binary({"remote": {"binary_path": settings.get("binary_path")}})
        if not binary:
            raise launcher.LauncherError("cloudflared executable not found")
        for name in ("gateway", "tunnel"):
            record = state.get(name)
            if record and not _identity(root, record).get("ok") and launcher._same_record_alive(record):
                raise launcher.LauncherError(f"{name} identity changed; refusing to overwrite its ownership record")
        if not _identity(root, state.get("gateway")).get("ok"):
            if launcher.listener_owner(port):
                raise launcher.LauncherError("MCP port is occupied by an unowned service; leave it running")
            python, _ = launcher.resolve_python_argv(root)
            runtime = _directory(root) / "gateway.json"
            runtime.write_text(json.dumps({**settings, "pan_project_root": str(root),
                "pan_api_url": f"http://127.0.0.1:{config.get('port', 8768)}"}), encoding="utf-8")
            try:
                _spawn(root, state, "gateway", [*python, "-B", "-m", "packages.remote.mcp_gateway", str(runtime)],
                       str(runtime), "mcp_gateway", "packages.remote.mcp_gateway")
            except Exception:
                _stop(root, state)
                raise
        if not _identity(root, state.get("tunnel")).get("ok"):
            runtime = _directory(root) / "cloudflared.yml"
            runtime.write_text(content, encoding="utf-8")
            try:
                _spawn(root, state, "tunnel", [binary, "tunnel", "--config", str(runtime), "run"],
                       str(runtime), "cloudflared", "cloudflared")
            except Exception:
                _stop(root, state)
                raise
        return {"ok": True}
