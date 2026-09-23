"""Windows-safe Pan process launcher and lifecycle primitives.

The launcher is the single owner of the Windows service boundary.  ``main.py``
owns the FastAPI/Uvicorn process and QQ bridge; this module owns starting that
process, the optional Pan cloudflared child, durable PID state, readiness, and
the verified hand-off used by the durable lifecycle supervisor.

The module deliberately never searches for arbitrary ``python.exe``
processes.  Every destructive operation starts with a PID, creation time,
checkout marker, entry marker, process type, and (for the service) the listener
owner check.  ``taskkill /T`` exists only as the final executor after the same
identity has been revalidated.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable


DEFAULT_PORT = 8768
READY_TIMEOUT_SEC = 30.0
STOP_TIMEOUT_SEC = 20.0
PID_TIME_TOLERANCE_SEC = 1.0
STATE_VERSION = 2
STATE_NAME = "process.json"
LEGACY_PID_NAME = "process.pid"
MAIN_PID_NAME = "main_pid.txt"
CF_PID_NAME = "cf_pid.txt"
QQ_PID_NAME = "qq_bot.pid"
QQ_STATE_NAME = "qq_bot.json"
MAIN_ENTRY_MARKER = "main.py"
QQ_ENTRY_MARKER = "bot.py"
SERVICE_NAMES = {"python", "pythonw", "uvicorn"}
_URL_RE = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")


class LauncherError(RuntimeError):
    """A checked launcher failure which must not trigger an unverified kill."""


class DuplicateStart(LauncherError):
    exit_code = 2


class PortConflict(LauncherError):
    exit_code = 3


def checkout_root(root: str | Path | None = None) -> Path:
    value = Path(root or Path(__file__).resolve().parents[2]).expanduser()
    try:
        return value.resolve()
    except OSError:
        return value.absolute()


def _data_dir(root: Path) -> Path:
    return root / "data"


def _logs_dir(root: Path) -> Path:
    path = _data_dir(root) / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _normal_path(value: str | Path) -> str:
    try:
        value = Path(value).expanduser().resolve()
    except OSError:
        value = Path(value).expanduser().absolute()
    return os.path.normcase(str(value)).replace("\\", "/").rstrip("/")


def _under_root(value: str | Path, root: str | Path) -> bool:
    candidate = _normal_path(value)
    base = _normal_path(root)
    return candidate == base or candidate.startswith(base + "/")


def _text_under_root(text: str, root: Path) -> bool:
    normalized = str(text or "").replace("\\", "/").lower()
    base = _normal_path(root).lower()
    return base in normalized and (
        normalized == base
        or normalized.startswith(base + "/")
        or ("=" + base + "/") in normalized
        or ("\"" + base + "/") in normalized
    )


def _atomic_json_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_config(root: str | Path | None = None) -> dict[str, Any]:
    """Load only launcher-relevant configuration with compatible defaults."""
    root_path = checkout_root(root)
    defaults: dict[str, Any] = {
        "python": "",
        "port": DEFAULT_PORT,
        "startup": {"console_hidden": False},
        "remote": {
            "enabled": False,
            "quick_tunnel": True,
            "config_path": "",
            "binary_path": "",
            "protocol": "",
        },
        "qq": {"enabled": True, "python": ""},
    }
    config_path = root_path / "config.json"
    if not config_path.is_file():
        return defaults
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LauncherError(f"could not parse {config_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise LauncherError(f"{config_path} must contain a JSON object")
    return _deep_merge(defaults, raw)


def configured_port(root: str | Path | None = None, environ: dict[str, str] | None = None) -> int:
    env = os.environ if environ is None else environ
    value = env.get("PAN_PORT")
    if value is None or not str(value).strip():
        value = load_config(root).get("port", DEFAULT_PORT)
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise LauncherError(f"Pan port must be an integer, got {value!r}") from exc
    if not 1 <= port <= 65535:
        raise LauncherError(f"Pan port is outside the valid range: {port}")
    return port


def _console_hidden(root: Path) -> bool:
    value = load_config(root).get("startup", {}).get("console_hidden", False)
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true"
    raise LauncherError("startup.console_hidden must be true or false")


def _candidate_argv(value: Any, source: str, root: Path) -> tuple[list[str] | None, str | None]:
    if isinstance(value, str):
        command, extra = value.strip(), []
    elif isinstance(value, dict):
        command = value.get("command")
        extra = value.get("args", [])
        if not isinstance(command, str) or not command.strip():
            return None, f"{source} command must be a non-empty string"
        if not isinstance(extra, list) or not all(isinstance(item, str) and item for item in extra):
            return None, f"{source} args must be a string array"
        command = command.strip()
    elif isinstance(value, list):
        if not value or not all(isinstance(item, str) and item for item in value):
            return None, f"{source} must be a non-empty string array"
        command, extra = value[0].strip(), list(value[1:])
    else:
        return None, f"{source} must be a string, array, or object"
    if not command:
        return None, f"{source} command is empty"
    path_like = (
        Path(command).is_absolute()
        or "/" in command
        or "\\" in command
        or (len(command) > 1 and command[1] == ":")
        or command.lower().endswith((".exe", ".cmd", ".bat", ".com"))
    )
    if path_like:
        candidate = Path(command).expanduser()
        if not candidate.is_file():
            return None, f"{source} executable path is missing"
        if os.name == "nt" and candidate.suffix.lower() not in {".exe", ".cmd", ".bat", ".com"}:
            return None, f"{source} path is not a Windows executable entry"
        if os.name != "nt" and not os.access(candidate, os.X_OK):
            return None, f"{source} path is not executable"
    elif shutil.which(command) is None:
        return None, f"{source} command is not available on PATH"
    return [command, *extra], None


def _python_works(argv: list[str], root: Path, timeout: float = 15.0) -> bool:
    try:
        result = subprocess.run(
            [*argv, "-c", "import fastapi, uvicorn, websockets, psutil, httpx; from mcp.server.fastmcp import FastMCP"],
            cwd=str(root), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def resolve_python_argv(root: str | Path | None = None, *, probe: bool = True) -> tuple[list[str], str]:
    """Resolve the interpreter with the historical Windows precedence."""
    root_path = checkout_root(root)
    config = load_config(root_path)
    venv_python = root_path / ".venv" / (
        Path("Scripts") / "python.exe" if os.name == "nt" else Path("bin") / "python"
    )
    candidates: list[tuple[Any, str]] = [
        (config.get("python"), "config.json python"),
        (os.environ.get("PAN_PYTHON"), "PAN_PYTHON"),
        (str(venv_python), "checkout .venv"),
        (shutil.which("python.exe") or shutil.which("python"), "python on PATH"),
        (sys.executable, "current interpreter"),
    ]
    seen: set[tuple[str, ...]] = set()
    errors: list[str] = []
    for value, source in candidates:
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        argv, error = _candidate_argv(value, source, root_path)
        if argv is None:
            errors.append(error or f"{source} is invalid")
            continue
        key = tuple(argv)
        if key in seen:
            continue
        seen.add(key)
        if not probe or _python_works(argv, root_path):
            return argv, source
        errors.append(f"{source} failed the Pan dependency probe")
    detail = "; ".join(errors[-3:])
    raise LauncherError(f"no usable Pan Python interpreter was found{': ' + detail if detail else ''}")


def _same_interpreter(argv: list[str]) -> bool:
    if len(argv) != 1:
        return False
    try:
        selected = Path(shutil.which(argv[0]) or argv[0]).resolve()
        current = Path(sys.executable).resolve()
        return os.path.normcase(str(selected)) == os.path.normcase(str(current))
    except OSError:
        return argv[0] == sys.executable


def _maybe_reexec_start(root: Path, cli_args: list[str]) -> int | None:
    if os.environ.get("PAN_LAUNCHER_BOOTSTRAPPED") == "1":
        return None
    argv, source = resolve_python_argv(root, probe=True)
    if _same_interpreter(argv):
        return None
    env = os.environ.copy()
    env["PAN_LAUNCHER_BOOTSTRAPPED"] = "1"
    print(f"[INFO] Pan Python source: {source}")
    result = subprocess.run(
        [*argv, "-m", "packages.core.launcher", "start", *cli_args],
        cwd=str(root), env=env, check=False,
    )
    return result.returncode


def process_create_time(pid: int | None) -> float | None:
    if not pid:
        return None
    try:
        import psutil
        return float(psutil.Process(int(pid)).create_time())
    except Exception:
        return None


def process_alive(pid: int | None, created_at: float | None = None) -> bool:
    if not pid:
        return False
    try:
        import psutil
        proc = psutil.Process(int(pid))
        if created_at is not None and abs(float(proc.create_time()) - float(created_at)) > PID_TIME_TOLERANCE_SEC:
            return False
        return proc.is_running()
    except Exception:
        return False


def listener_owner(port: int) -> int | None:
    """Return only the current local TCP listener owner for ``port``."""
    try:
        import psutil
        for connection in psutil.net_connections(kind="tcp"):
            address = connection.laddr
            local_port = getattr(address, "port", None)
            if local_port is None and isinstance(address, (tuple, list)) and len(address) > 1:
                local_port = address[1]
            if int(local_port or -1) != int(port):
                continue
            state = str(connection.status).upper()
            if state in {"LISTEN", "LISTENING", str(getattr(psutil, "CONN_LISTEN", "LISTEN")).upper()}:
                return int(connection.pid) if connection.pid else None
    except Exception:
        return None
    return None


def _inspect(pid: int | None) -> dict[str, Any] | None:
    if not pid:
        return None
    try:
        import psutil
        proc = psutil.Process(int(pid))
        with proc.oneshot():
            return {
                "pid": int(proc.pid),
                "createdAt": float(proc.create_time()),
                "name": str(proc.name() or ""),
                "exe": str(proc.exe() or "") if proc.exe() else "",
                "cmdline": [str(item) for item in (proc.cmdline() or [])],
                "cwd": str(proc.cwd() or "") if proc.cwd() else "",
                "running": bool(proc.is_running()),
            }
    except Exception:
        return None


def _process_type(info: dict[str, Any]) -> str:
    return Path(info.get("name") or info.get("exe") or "").stem.lower()


def process_identity(
    pid: int | None,
    root: str | Path,
    expected_created_at: float | None,
    *,
    process_type: str = "main",
    marker: str | None = None,
    entry_marker: str | None = None,
) -> dict[str, Any]:
    """Return a non-mutating, creation-time-aware process identity result."""
    root_path = checkout_root(root)
    result: dict[str, Any] = {
        "pid": pid, "createdAt": None, "root": str(root_path),
        "rootMarker": False, "commandMarker": False, "entryMarker": False,
        "processType": process_type, "ok": False, "error": None,
    }
    info = _inspect(pid)
    if not info:
        result["error"] = "process is not available"
        return result
    result.update({
        "createdAt": info["createdAt"], "name": info["name"],
        "exe": info["exe"], "cmdline": info["cmdline"], "cwd": info["cwd"],
    })
    if expected_created_at is None:
        result["error"] = "expected PID creation time is missing"
        return result
    if abs(float(info["createdAt"]) - float(expected_created_at)) > PID_TIME_TOLERANCE_SEC:
        result["error"] = "PID creation time does not match"
        return result
    command_text = " ".join(info["cmdline"])
    result["rootMarker"] = _under_root(info["cwd"], root_path) or _text_under_root(command_text, root_path)
    expected_entry = entry_marker or {
        "main": MAIN_ENTRY_MARKER,
        "qq": QQ_ENTRY_MARKER,
        "cloudflared": "cloudflared",
    }.get(process_type, process_type)
    result["entryMarker"] = expected_entry.lower() in command_text.replace("\\", "/").lower()
    result["commandMarker"] = not marker or marker.replace("\\", "/").lower() in command_text.replace("\\", "/").lower()
    name = _process_type(info)
    if process_type in {"main", "qq"}:
        type_ok = name in SERVICE_NAMES or name.startswith("python")
    elif process_type == "cloudflared":
        type_ok = name.startswith("cloudflared") or "cloudflared" in Path(info.get("exe") or "").stem.lower()
    else:
        type_ok = True
    if not type_ok:
        result["error"] = f"process type is not Pan {process_type}: {name or 'unknown'}"
    elif not result["rootMarker"]:
        result["error"] = "process is outside the target checkout"
    elif not result["entryMarker"]:
        result["error"] = f"process command line has no {expected_entry!r} entry marker"
    elif not result["commandMarker"]:
        result["error"] = "process command line marker does not match"
    elif not info["running"]:
        result["error"] = "process is not running"
    else:
        result["ok"] = True
    return result


def service_process_identity(pid: int | None, root: str | Path,
                             expected_created_at: float | None = None) -> dict[str, Any]:
    """Compatibility name used by lifecycle diagnostics and existing callers."""
    return process_identity(pid, root, expected_created_at, process_type="main")


def _record(pid: int, root: Path, process_type: str, argv: Iterable[str],
            created_at: float | None = None, marker: str | None = None) -> dict[str, Any]:
    info = _inspect(pid) or {}
    entry = {
        "main": MAIN_ENTRY_MARKER,
        "qq": QQ_ENTRY_MARKER,
        "cloudflared": "cloudflared",
    }.get(process_type, process_type)
    return {
        "pid": int(pid), "createdAt": created_at if created_at is not None else info.get("createdAt"),
        "root": str(root), "argv": [str(item) for item in argv],
        "marker": marker or f"pan-{process_type}:{_normal_path(root)}",
        "processType": process_type, "entry": entry,
    }


def _state_path(root: Path) -> Path:
    return _data_dir(root) / STATE_NAME


def _legacy_pid_path(root: Path) -> Path:
    return _data_dir(root) / LEGACY_PID_NAME


def _load_legacy_records(root: Path) -> dict[str, Any]:
    records: dict[str, Any] = {"main": None, "cloudflared": None}
    values: dict[str, str] = {}
    try:
        for line in _legacy_pid_path(root).read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                values[key.strip().upper()] = value.strip()
    except OSError:
        pass
    for key, field, process_type in (("MAIN", "main", "main"), ("CF", "cloudflared", "cloudflared")):
        try:
            pid = int(values.get(key, "0"))
        except ValueError:
            pid = 0
        if pid:
            records[field] = _record(pid, root, process_type, [])
            # Legacy files never contained a creation time.  Do not invent one:
            # a legacy record can be inspected, but it is not safe to kill.
            records[field]["createdAt"] = None
            records[field]["marker"] = None
    for name, field, process_type in ((MAIN_PID_NAME, "main", "main"), (CF_PID_NAME, "cloudflared", "cloudflared")):
        if records[field] is not None:
            continue
        try:
            pid = int((_data_dir(root) / name).read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            pid = 0
        if pid:
            records[field] = _record(pid, root, process_type, [])
            records[field]["createdAt"] = None
            records[field]["marker"] = None
    return records


def load_state(root: str | Path | None = None) -> dict[str, Any]:
    root_path = checkout_root(root)
    state = _read_json(_state_path(root_path))
    if state and _normal_path(state.get("root", "")) == _normal_path(root_path):
        state.setdefault("root", str(root_path))
        state.setdefault("version", STATE_VERSION)
        return state
    legacy = _load_legacy_records(root_path)
    return {
        "version": STATE_VERSION, "root": str(root_path),
        "port": None, "main": legacy.get("main"),
        "cloudflared": legacy.get("cloudflared"), "qq": None,
    }


def save_state(root: str | Path, state: dict[str, Any]) -> None:
    root_path = checkout_root(root)
    state = copy.deepcopy(state)
    state["version"] = STATE_VERSION
    state["root"] = str(root_path)
    state["updatedAt"] = time.time()
    _atomic_json_write(_state_path(root_path), state)
    _write_legacy_pid_files(root_path, state)


def _write_legacy_pid_files(root: Path, state: dict[str, Any]) -> None:
    data = _data_dir(root)
    data.mkdir(parents=True, exist_ok=True)
    main = state.get("main") or {}
    cloud = state.get("cloudflared") or {}
    lines = []
    if main.get("pid"):
        lines.append(f"MAIN={int(main['pid'])}")
    if cloud.get("pid"):
        lines.append(f"CF={int(cloud['pid'])}")
    if lines:
        (data / LEGACY_PID_NAME).write_text("\n".join(lines) + "\n", encoding="ascii")
    else:
        (data / LEGACY_PID_NAME).unlink(missing_ok=True)
    if main.get("pid"):
        (data / MAIN_PID_NAME).write_text(str(int(main["pid"])), encoding="ascii")
    else:
        (data / MAIN_PID_NAME).unlink(missing_ok=True)
    if cloud.get("pid"):
        (data / CF_PID_NAME).write_text(str(int(cloud["pid"])), encoding="ascii")
    else:
        (data / CF_PID_NAME).unlink(missing_ok=True)


def clear_state(root: str | Path, *, remove_qq: bool = True) -> None:
    root_path = checkout_root(root)
    for name in (STATE_NAME, LEGACY_PID_NAME, MAIN_PID_NAME, CF_PID_NAME):
        (_data_dir(root_path) / name).unlink(missing_ok=True)
    if remove_qq:
        (_data_dir(root_path) / QQ_PID_NAME).unlink(missing_ok=True)
        (_data_dir(root_path) / QQ_STATE_NAME).unlink(missing_ok=True)


def _qq_record(root: Path) -> dict[str, Any] | None:
    state = _read_json(_data_dir(root) / QQ_STATE_NAME)
    if state and _normal_path(state.get("root", "")) == _normal_path(root):
        return state
    try:
        pid = int((_data_dir(root) / QQ_PID_NAME).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    info = _inspect(pid)
    if not info:
        return None
    command = info.get("cmdline") or []
    if not any(QQ_ENTRY_MARKER in item.replace("\\", "/").lower() for item in command):
        return None
    if not (_under_root(info.get("cwd", ""), root) or _text_under_root(" ".join(command), root)):
        return None
    return _record(
        pid, root, "qq", command, info.get("createdAt"),
        str(root / "packages" / "qq" / "bot.py"),
    )


def refresh_qq_state(root: str | Path, state: dict[str, Any]) -> dict[str, Any] | None:
    root_path = checkout_root(root)
    record = _qq_record(root_path)
    # A launcher state written during startup contains the precise QQ record,
    # while the legacy numeric qq_bot.pid may be gone after a graceful main
    # teardown has begun.  Preserve that verified snapshot for the exit path;
    # identity is rechecked immediately before any termination.
    if record is None and state.get("qq"):
        record = state["qq"]
    state["qq"] = record
    if record:
        _atomic_json_write(_data_dir(root_path) / QQ_STATE_NAME, record)
    return record


def _same_record_alive(record: dict[str, Any] | None) -> bool:
    return bool(record and process_alive(record.get("pid"), record.get("createdAt")))


def _wait_record_gone(record: dict[str, Any], timeout: float) -> bool:
    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        if not _same_record_alive(record):
            return True
        time.sleep(0.1)
    return not _same_record_alive(record)


def _taskkill_verified(record: dict[str, Any], root: Path, log_path: Path | None) -> bool:
    identity = process_identity(
        record.get("pid"), root, record.get("createdAt"),
        process_type=record.get("processType", "main"),
        marker=record.get("marker"), entry_marker=record.get("entry"),
    )
    if not identity.get("ok"):
        return False
    if os.name != "nt":
        return False
    command = ["taskkill", "/PID", str(int(record["pid"])), "/T", "/F"]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        _write_log(log_path, f"fallback taskkill failed pid={record['pid']}: {exc}")
        return False
    _write_log(log_path, f"fallback taskkill pid={record['pid']} rc={completed.returncode}")
    return completed.returncode == 0


def _terminate_record(record: dict[str, Any] | None, root: Path, *, log_path: Path | None,
                      allow_fallback: bool = True) -> dict[str, Any]:
    if not record:
        return {"stopped": True, "fallback": False, "reason": "no recorded process"}
    identity = process_identity(
        record.get("pid"), root, record.get("createdAt"),
        process_type=record.get("processType", "main"),
        marker=record.get("marker"), entry_marker=record.get("entry"),
    )
    if not identity.get("ok"):
        if not _same_record_alive(record):
            return {"stopped": True, "fallback": False, "reason": "already stopped"}
        return {"stopped": False, "fallback": False, "reason": identity.get("error")}
    try:
        import psutil
        proc = psutil.Process(int(record["pid"]))
        proc.terminate()
    except Exception as exc:
        _write_log(log_path, f"Python terminate failed pid={record['pid']}: {exc}")
    if _wait_record_gone(record, 5.0):
        return {"stopped": True, "fallback": False, "reason": "Python terminate"}
    if allow_fallback and _taskkill_verified(record, root, log_path) and _wait_record_gone(record, 5.0):
        return {"stopped": True, "fallback": True, "reason": "verified taskkill /T /F"}
    return {"stopped": False, "fallback": False, "reason": "process did not stop after verified termination"}


def _write_log(path: Path | None, message: str) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{stamp}] {message}\n")


def _http_ready(port: int, path: str = "/api/sessions?summary=1", timeout: float = 1.0) -> tuple[bool, str | None]:
    url = f"http://127.0.0.1:{int(port)}{path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            if response.status != 200:
                return False, f"HTTP {response.status}"
            return True, None
    except Exception as exc:
        return False, str(exc)


def readiness(*, root: str | Path, port: int, expected_pid: int | None = None,
              expected_created_at: float | None = None) -> dict[str, Any]:
    root_path = checkout_root(root)
    owner = listener_owner(port)
    result: dict[str, Any] = {
        "port": int(port), "listenerOwner": owner,
        "listenerOwnerCreatedAt": process_create_time(owner), "ok": False,
    }
    if owner is None:
        result["error"] = "target port has no listener"
        return result
    if expected_pid is not None and owner != int(expected_pid):
        # Windows venv redirectors can keep the Popen PID alive while a child
        # Python process runs main.py and owns the listening socket.
        try:
            import psutil
            descendant = int(expected_pid) in {ancestor.pid for ancestor in psutil.Process(owner).parents()}
        except (psutil.Error, ValueError):
            descendant = False
        parent_identity = process_identity(
            expected_pid, root_path, expected_created_at,
            process_type="main", marker=f"--pan-root-marker={root_path}",
        ) if descendant else {"ok": False}
        if (not parent_identity.get("ok") or expected_created_at is None
                or result["listenerOwnerCreatedAt"] is None
                or result["listenerOwnerCreatedAt"] < expected_created_at):
            result["error"] = "target port listener owner does not match the Pan PID"
            return result
        result["verifiedDescendant"] = True
    identity = process_identity(
        owner, root_path, result["listenerOwnerCreatedAt"],
        process_type="main", marker=f"--pan-root-marker={root_path}" if expected_pid is not None else None,
    )
    result["identity"] = identity
    if not identity.get("ok"):
        result["error"] = identity.get("error")
        return result
    healthy, error = _http_ready(port)
    result["http"] = healthy
    if not healthy:
        result["error"] = error or "Pan readiness endpoint failed"
        return result
    result["ok"] = True
    return result


def wait_ready(*, root: str | Path, port: int, expected_pid: int | None = None,
               expected_created_at: float | None = None, timeout: float = READY_TIMEOUT_SEC,
               log_path: Path | None = None) -> dict[str, Any]:
    deadline = time.monotonic() + max(0.1, timeout)
    last: dict[str, Any] = {"ok": False, "error": "readiness did not start"}
    while time.monotonic() < deadline:
        last = readiness(
            root=root, port=port, expected_pid=expected_pid,
            expected_created_at=expected_created_at,
        )
        if last.get("ok"):
            return last
        time.sleep(0.25)
    _write_log(log_path, f"readiness timeout port={port} error={last.get('error')}")
    return last


def _creation_flags(hidden: bool) -> int:
    if os.name != "nt":
        return 0
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
    if hidden:
        flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return flags


def _open_redirect(path: Path, mode: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.open(mode, encoding="utf-8")


def _start_main(root: Path, port: int, python_argv: list[str], hidden: bool,
                log_path: Path) -> dict[str, Any]:
    main_path = root / "main.py"
    if not main_path.is_file():
        raise LauncherError(f"Pan entry point not found: {main_path}")
    # This marker is intentionally part of argv so PID reuse and a similarly
    # named checkout cannot satisfy identity by cwd alone.
    marker = f"--pan-root-marker={root}"
    argv = [*python_argv, str(main_path), f"--pan-root-marker={root}", f"--pan-port-marker={port}"]
    stdout_handle = stderr_handle = None
    try:
        if hidden:
            stdout_handle = _open_redirect(log_path.parent / "pan-console.out.log", "a")
            stderr_handle = _open_redirect(log_path.parent / "pan-console.err.log", "a")
        process = subprocess.Popen(
            argv, cwd=str(root), stdin=subprocess.DEVNULL,
            stdout=stdout_handle if hidden else None,
            stderr=stderr_handle if hidden else None,
            close_fds=True, creationflags=_creation_flags(hidden),
            env=os.environ.copy(),
        )
    except OSError as exc:
        raise LauncherError(f"failed to launch Pan Core: {exc}") from exc
    finally:
        if stdout_handle is not None:
            stdout_handle.close()
        if stderr_handle is not None:
            stderr_handle.close()
    created = process_create_time(process.pid)
    if created is None:
        process.terminate()
        raise LauncherError("Pan Core creation time could not be read")
    record = _record(process.pid, root, "main", argv, created, marker)
    identity = process_identity(process.pid, root, created, process_type="main", marker=marker)
    if not identity.get("ok"):
        _terminate_record(record, root, log_path=log_path)
        raise LauncherError(f"Pan Core identity failed: {identity.get('error')}")
    return record


def _cloudflared_binary(config: dict[str, Any]) -> str | None:
    remote = config.get("remote") or {}
    configured = str(remote.get("binary_path") or os.environ.get("PAN_CLOUDFLARED") or "").strip()
    if configured:
        path = shutil.which(configured) if not Path(configured).is_file() else configured
        return path
    return shutil.which("cloudflared.exe") or shutil.which("cloudflared")


def _named_tunnel_config(root: Path, config: dict[str, Any], port: int) -> tuple[list[str], str]:
    remote = config.get("remote") or {}
    source = str(remote.get("config_path") or os.environ.get("PAN_CF_CONFIG") or "").strip()
    if not source:
        source = str(Path(os.environ.get("USERPROFILE") or Path.home()) / ".cloudflared" / "config-test.yml")
    source_path = Path(source).expanduser()
    if not source_path.is_absolute():
        # start_pan.bat historically changed to the checkout before invoking
        # the PowerShell tunnel helper. Preserve that relative config meaning
        # when the Python launcher is called from an arbitrary cwd.
        source_path = root / source_path
    try:
        content = source_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LauncherError(f"named cloudflared config is unavailable: {source_path}: {exc}") from exc
    content = re.sub(r"http://localhost:\d+", f"http://localhost:{port}", content)
    protocol = str(remote.get("protocol") or "").strip()
    if protocol:
        content = re.sub(r"(?m)^\s*protocol\s*:.*\r?\n?", "", content)
        if not content.endswith("\n"):
            content += "\n"
        content += f"protocol: {protocol}\n"
    runtime = root / "data" / "cloudflared"
    runtime.mkdir(parents=True, exist_ok=True)
    output = runtime / f"pan_cf_config_{port}.yml"
    output.write_text(content, encoding="utf-8")
    return ["tunnel", "--config", str(output), "run"], str(output)


def start_cloudflared(root: str | Path, port: int, state: dict[str, Any],
                      *, log_path: Path | None = None) -> dict[str, Any] | None:
    root_path = checkout_root(root)
    config = load_config(root_path)
    remote = config.get("remote") or {}
    if remote.get("enabled") is not True:
        _write_log(log_path, "remote.enabled is not explicitly true; cloudflared skipped")
        return None
    binary = _cloudflared_binary(config)
    if not binary:
        _write_log(log_path, "cloudflared not found; Pan Core continues without a tunnel")
        return None
    port = int(port)
    quick = remote.get("quick_tunnel", True) is not False
    if quick:
        tunnel_log = _logs_dir(root_path) / f"pan_cf_quick_{port}.log"
        tunnel_log.unlink(missing_ok=True)
        args = ["tunnel", "--url", f"http://127.0.0.1:{port}", "--logfile", str(tunnel_log)]
        marker = str(tunnel_log)
    else:
        args, marker = _named_tunnel_config(root_path, config, port)
        tunnel_log = _logs_dir(root_path) / f"pan_cf_named_{port}.log"
    argv = [binary, *args]
    try:
        with tunnel_log.open("a", encoding="utf-8") as output:
            process = subprocess.Popen(
                argv, cwd=str(root_path), stdin=subprocess.DEVNULL,
                stdout=output, stderr=subprocess.STDOUT, close_fds=True,
                creationflags=_creation_flags(True), env=os.environ.copy(),
            )
    except (OSError, LauncherError) as exc:
        _write_log(log_path, f"cloudflared failed to start: {exc}")
        return None
    created = process_create_time(process.pid)
    record = _record(process.pid, root_path, "cloudflared", argv, created, marker)
    identity = process_identity(
        process.pid, root_path, created, process_type="cloudflared",
        marker=marker, entry_marker="cloudflared",
    )
    if not identity.get("ok"):
        _terminate_record(record, root_path, log_path=log_path)
        _write_log(log_path, f"cloudflared identity rejected: {identity.get('error')}")
        return None
    state["cloudflared"] = record
    save_state(root_path, state)
    _write_log(log_path, f"cloudflared started pid={process.pid} mode={'quick' if quick else 'named'}")
    if quick:
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            try:
                text = tunnel_log.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            match = _URL_RE.search(text)
            if match:
                _write_log(log_path, f"quick tunnel URL: {match.group(0)}")
                break
            if not process_alive(process.pid, created):
                break
            time.sleep(0.5)
    return record


def _cleanup_stale_children(root: Path, state: dict[str, Any], log_path: Path) -> None:
    for field in ("cloudflared", "qq"):
        record = state.get(field)
        if not record:
            continue
        identity = process_identity(
            record.get("pid"), root, record.get("createdAt"),
            process_type=record.get("processType", field),
            marker=record.get("marker"), entry_marker=record.get("entry"),
        )
        if identity.get("ok"):
            outcome = _terminate_record(record, root, log_path=log_path)
            if not outcome.get("stopped"):
                raise LauncherError(f"stale {field} process could not be stopped safely")
        state[field] = None


def start_service(root: str | Path | None = None, *, timeout: float = READY_TIMEOUT_SEC,
                  log_path: Path | None = None) -> dict[str, Any]:
    root_path = checkout_root(root)
    data = _data_dir(root_path)
    data.mkdir(parents=True, exist_ok=True)
    log_path = log_path or (_logs_dir(root_path) / "pan-launcher.log")
    port = configured_port(root_path)
    state = load_state(root_path)
    state["port"] = port
    refresh_qq_state(root_path, state)
    main = state.get("main")
    if main and process_identity(
        main.get("pid"), root_path, main.get("createdAt"),
        process_type="main", marker=main.get("marker"), entry_marker=main.get("entry"),
    ).get("ok"):
        raise DuplicateStart(f"Pan Core is already running for this checkout, PID={main.get('pid')}")
    _cleanup_stale_children(root_path, state, log_path)
    owner = listener_owner(port)
    if owner is not None:
        owner_created = process_create_time(owner)
        owner_identity = process_identity(owner, root_path, owner_created, process_type="main")
        if owner_identity.get("ok"):
            raise DuplicateStart(f"Pan Core is already listening on port {port}, PID={owner}")
        raise PortConflict(
            f"Pan port {port} is already owned by an unverified process PID={owner}; refusing to start"
        )
    python_argv, source = resolve_python_argv(root_path, probe=True)
    print(f"[INFO] Pan Python source: {source}")
    record = _start_main(root_path, port, python_argv, _console_hidden(root_path), log_path)
    state["main"] = record
    state["port"] = port
    # Capture a QQ child that may have spawned before the readiness barrier so
    # a failed startup cannot leave it orphaned.
    refresh_qq_state(root_path, state)
    save_state(root_path, state)
    _write_log(log_path, f"Pan Core started pid={record['pid']} port={port} root={root_path}")
    checks = wait_ready(
        root=root_path, port=port, expected_pid=record["pid"],
        expected_created_at=record["createdAt"], timeout=timeout, log_path=log_path,
    )
    if not checks.get("ok"):
        listener_pid = checks.get("listenerOwner")
        if (listener_pid and listener_pid != record["pid"]
                and checks.get("verifiedDescendant") and checks.get("identity", {}).get("ok")):
            listener_record = _record(
                listener_pid, root_path, "main", record["argv"],
                checks.get("listenerOwnerCreatedAt"), record["marker"],
            )
            _terminate_record(listener_record, root_path, log_path=log_path)
        qq_cleanup = _terminate_record(state.get("qq"), root_path, log_path=log_path)
        outcome = _terminate_record(record, root_path, log_path=log_path)
        clear_state(root_path)
        raise LauncherError(
            f"Pan Core did not become ready on port {port} within {timeout:g} seconds: "
            f"{checks.get('error')}; cleanup={{'main': {outcome}, 'qq': {qq_cleanup}}}"
        )
    listener_pid = checks.get("listenerOwner")
    if listener_pid and listener_pid != record["pid"]:
        record = _record(
            listener_pid, root_path, "main", record["argv"],
            checks["listenerOwnerCreatedAt"], record["marker"],
        )
        state["main"] = record
        _write_log(log_path, f"Pan Core listener pid={listener_pid} (launcher child)")
    refresh_qq_state(root_path, state)
    save_state(root_path, state)
    start_cloudflared(root_path, port, state, log_path=log_path)
    print(f"[OK] Pan Core API ready on 127.0.0.1:{port}, PID={record['pid']}")
    return state


def _request_internal_shutdown(port: int, log_path: Path | None) -> tuple[bool, str | None]:
    url = f"http://127.0.0.1:{int(port)}/api/internal/main/shutdown"
    try:
        request = urllib.request.Request(url, data=b"{}", method="POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=3.0) as response:
            body = json.loads(response.read().decode("utf-8") or "{}")
            if response.status == 200 and body.get("ok"):
                return True, None
            return False, str(body.get("error") or f"HTTP {response.status}")
    except Exception as exc:
        _write_log(log_path, f"internal graceful shutdown request failed: {exc}")
        return False, str(exc)


def stop_service(root: str | Path, port: int, old_pid: int | None = None,
                 old_pid_created_at: float | None = None, *, log_path: Path | None = None,
                 require_identity: bool = True) -> dict[str, Any]:
    """Gracefully stop Pan and owned children, with verified fallback only."""
    root_path = checkout_root(root)
    log_path = log_path or (_logs_dir(root_path) / "pan-launcher.log")
    state = load_state(root_path)
    refresh_qq_state(root_path, state)
    main = state.get("main") or {}
    pid = int(old_pid) if old_pid is not None else main.get("pid")
    created = old_pid_created_at if old_pid_created_at is not None else main.get("createdAt")
    if pid is None:
        pid = listener_owner(port)
    if pid is None:
        _cleanup_stale_children(root_path, state, log_path)
        clear_state(root_path)
        return {"stopped": True, "fallback": False, "reason": "Pan was already offline"}
    if created is None:
        raise LauncherError("current Pan PID creation time could not be verified")
    record = dict(main) if main.get("pid") == pid else _record(
        pid, root_path, "main", [], created, f"--pan-root-marker={root_path}",
    )
    record["pid"] = int(pid)
    record["createdAt"] = float(created)
    if main.get("pid") != pid or main.get("createdAt") is None:
        # Legacy process.pid had no command marker.  It is compatible only
        # when the durable caller supplies the creation time explicitly.
        record["marker"] = None
    identity = process_identity(
        pid, root_path, created, process_type="main",
        marker=record.get("marker"), entry_marker=record.get("entry", MAIN_ENTRY_MARKER),
    )
    if not identity.get("ok"):
        if not _same_record_alive(record):
            clear_state(root_path)
            return {"stopped": True, "fallback": False, "reason": "Pan was already offline"}
        # A restart may omit the old PID because it is recovering durable
        # state, but it must never turn that omission into permission to kill
        # an unverified PID. Every live target follows the same identity gate.
        raise LauncherError(f"refusing to stop unverified Pan PID={pid}: {identity.get('error')}")
    owner = listener_owner(port)
    if owner is not None and owner != pid:
        raise LauncherError(f"Pan port {port} is owned by PID={owner}, not verified Pan PID={pid}")
    _write_log(log_path, f"stopping Pan pid={pid} port={port}")
    coordinated, coordination_error = _request_internal_shutdown(port, log_path)
    if coordinated:
        _write_log(log_path, "Pan accepted internal graceful shutdown")
    deadline = time.monotonic() + STOP_TIMEOUT_SEC
    while time.monotonic() < deadline:
        if not _same_record_alive(record) and listener_owner(port) is None:
            break
        time.sleep(0.1)
    fallback = False
    current_owner = listener_owner(port)
    if current_owner is not None and current_owner != pid:
        # A different process took the configured port while graceful exit
        # was in flight. Do not kill the verified old PID in that race, and do
        # not mistake the foreign listener for a successful exit.
        raise LauncherError(
            f"Pan port {port} changed ownership to PID={current_owner}; refusing fallback"
        )
    if _same_record_alive(record) or current_owner is not None:
        # Revalidate the exact PID/create-time/root/entry immediately before
        # using the Windows-only process-tree executor.
        outcome = _terminate_record(record, root_path, log_path=log_path, allow_fallback=True)
        fallback = bool(outcome.get("fallback"))
        if not outcome.get("stopped"):
            raise LauncherError(
                f"Pan graceful shutdown failed ({coordination_error}); verified fallback failed: {outcome}"
            )
    for field in ("qq", "cloudflared"):
        child = state.get(field)
        if not child:
            continue
        outcome = _terminate_record(child, root_path, log_path=log_path, allow_fallback=True)
        if not outcome.get("stopped"):
            raise LauncherError(f"owned {field} process was not stopped: {outcome}")
        fallback = fallback or bool(outcome.get("fallback"))
    remaining = listener_owner(port)
    if remaining is not None:
        raise LauncherError(f"Pan port {port} still has listener PID={remaining}")
    clear_state(root_path)
    _write_log(log_path, f"Pan stopped pid={pid} fallback={'taskkill' if fallback else 'none'}")
    return {
        "stopped": True, "fallback": fallback,
        "fallbackExecutor": "taskkill /T /F" if fallback else None,
        "coordinated": coordinated, "pid": pid,
    }


def exit_service(root: str | Path, port: int, old_pid: int | None = None,
                 old_pid_created_at: float | None = None, *, log_path: Path | None = None) -> dict[str, Any]:
    return stop_service(
        root, port, old_pid, old_pid_created_at,
        log_path=log_path, require_identity=True,
    )


def restart_service(root: str | Path, port: int, old_pid: int | None = None,
                    old_pid_created_at: float | None = None, *, timeout: float = READY_TIMEOUT_SEC,
                    log_path: Path | None = None) -> dict[str, Any]:
    root_path = checkout_root(root)
    stop_result = stop_service(root_path, port, old_pid, old_pid_created_at, log_path=log_path)
    state = start_service(root_path, timeout=timeout, log_path=log_path)
    state["restart"] = stop_result
    return state


def owned_cloudflared(root: str | Path) -> dict[str, Any] | None:
    root_path = checkout_root(root)
    state = load_state(root_path)
    record = state.get("cloudflared")
    if not record:
        return None
    identity = process_identity(
        record.get("pid"), root_path, record.get("createdAt"), process_type="cloudflared",
        marker=record.get("marker"), entry_marker="cloudflared",
    )
    return {"record": record, "identity": identity}


def restart_cloudflared(root: str | Path, *, log_path: Path | None = None) -> dict[str, Any]:
    root_path = checkout_root(root)
    log_path = log_path or (_logs_dir(root_path) / "pan-launcher.log")
    state = load_state(root_path)
    old = state.get("cloudflared")
    killed: list[int] = []
    if old:
        outcome = _terminate_record(old, root_path, log_path=log_path, allow_fallback=True)
        if not outcome.get("stopped"):
            return {"ok": False, "error": outcome.get("reason"), "killed": killed}
        killed.append(int(old["pid"]))
        state["cloudflared"] = None
        save_state(root_path, state)
    port = configured_port(root_path)
    new = start_cloudflared(root_path, port, state, log_path=log_path)
    return {"ok": True, "killed": killed, "restarted": bool(new), "record": new}


def status(root: str | Path | None = None) -> dict[str, Any]:
    root_path = checkout_root(root)
    state = load_state(root_path)
    port = state.get("port") or configured_port(root_path)
    main = state.get("main")
    cloud = state.get("cloudflared")
    result = {
        "root": str(root_path), "port": port,
        "main": process_identity(
            main.get("pid"), root_path, main.get("createdAt"), process_type="main",
            marker=main.get("marker"), entry_marker=main.get("entry", MAIN_ENTRY_MARKER),
        ) if main else None,
        "cloudflared": process_identity(
            cloud.get("pid"), root_path, cloud.get("createdAt"), process_type="cloudflared",
            marker=cloud.get("marker"), entry_marker="cloudflared",
        ) if cloud else None,
        "listenerOwner": listener_owner(port),
    }
    refresh_qq_state(root_path, state)
    result["qq"] = process_identity(
        state["qq"].get("pid"), root_path, state["qq"].get("createdAt"),
        process_type="qq", marker=state["qq"].get("marker"), entry_marker=QQ_ENTRY_MARKER,
    ) if state.get("qq") else None
    return result


def _json_print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Pan internal Windows launcher")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("start", "status", "identity", "readiness", "exit", "restart"):
        command = sub.add_parser(name)
        command.add_argument("--root", default=None)
        if name in {"exit", "restart"}:
            command.add_argument("--port", type=int)
            command.add_argument("--old-pid", type=int)
            command.add_argument("--old-pid-created-at", type=float)
        if name in {"start", "restart"}:
            command.add_argument("--timeout", type=float, default=READY_TIMEOUT_SEC)
    return parser


def main(argv: list[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    parser = _build_parser()
    args = parser.parse_args(raw_args)
    root = checkout_root(args.root)
    try:
        if args.command == "start":
            reexec_result = _maybe_reexec_start(root, ["--root", str(root), "--timeout", str(args.timeout)])
            if reexec_result is not None:
                return int(reexec_result)
            start_service(root, timeout=args.timeout)
            return 0
        if args.command == "status":
            _json_print(status(root))
            return 0
        if args.command == "identity":
            _json_print(status(root))
            return 0
        if args.command == "readiness":
            result = readiness(root=root, port=configured_port(root))
            _json_print(result)
            return 0 if result.get("ok") else 1
        if args.command in {"exit", "restart"}:
            port = args.port or configured_port(root)
            if args.command == "exit":
                result = exit_service(root, port, args.old_pid, args.old_pid_created_at)
            else:
                result = restart_service(
                    root, port, args.old_pid, args.old_pid_created_at, timeout=args.timeout,
                )
            _json_print(result)
            return 0
    except LauncherError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return int(getattr(exc, "exit_code", 1))
    except KeyboardInterrupt:
        return 130
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
