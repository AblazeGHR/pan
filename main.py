#!/usr/bin/env python
"""Pan — entry point."""

import atexit
import json
import logging
import os
import shutil
import socket
import subprocess
import threading
from pathlib import Path
from urllib.parse import urlparse

from packages.web.server import app

_log = logging.getLogger("pan")

_PROJECT_ROOT = Path(__file__).resolve().parent
_QQ_BOT_PY = _PROJECT_ROOT / "packages" / "qq" / "bot.py"
_QQ_DIR = _QQ_BOT_PY.parent
_QQ_PID_FILE = _PROJECT_ROOT / "data" / "qq_bot.pid"
# QQ bot 跑在独立解释器上（项目 .venv 没有 nonebot 依赖）。
# 解释器路径的单一事实源是 config.json 的 qq.python（setup.bat 首次运行时写入），
# 解析链见 _resolve_qq_python()：PAN_QQ_PYTHON 环境变量 > qq.python > 此处平台默认。
# Windows 默认 miniforge；POSIX 上该盘符路径无效，退回 PATH 里的 python3。
_QQ_DEFAULT_PYTHON = (
    r"E:\software\miniforge\python.exe"
    if os.name == "nt"
    else (shutil.which("python3") or "python3")
)
# QQ bot 启动宽限期：spawn 后在此窗口内退出即视为"快速崩溃"，Pan Core 不受影响。
_QQ_STARTUP_GRACE_SEC = 2.0

_qq_proc: subprocess.Popen | None = None


def _is_pid_alive(pid: int) -> bool:
    """Best-effort liveness check for a local PID (works on Windows too).

    Windows 上 os.kill(pid, 0) 会直接 TerminateProcess（sig 即退出码），
    必须用 psutil 探测（worker.py 已依赖 psutil）。
    """
    try:
        import psutil
        return psutil.pid_exists(pid)
    except ImportError:
        return False


def _qq_ws_urls_from_env() -> list[str]:
    """Read ONEBOT_WS_URLS from packages/qq/.env (best-effort)."""
    try:
        env_text = (_QQ_DIR / ".env").read_text(encoding="utf-8")
    except OSError:
        return []
    for line in env_text.splitlines():
        line = line.strip()
        if not line.startswith("ONEBOT_WS_URLS="):
            continue
        try:
            urls = json.loads(line.split("=", 1)[1].strip())
        except (ValueError, json.JSONDecodeError):
            return []
        return list(urls) if isinstance(urls, (list, tuple)) else [urls]
    return []


def _qq_ws_urls_from_config() -> list[str]:
    """Read the active channel's WS URLs from config.json's qq section.

    Used when packages/qq/.env has no ONEBOT_WS_URLS (the new config-driven
    channel path). Picks qq.<channel>.ws_urls based on qq.channel, falling back
    to the legacy qq.ws_url field.
    """
    try:
        from packages.core.config import load_config

        qq = load_config().get("qq") or {}
    except Exception:
        return []
    name = (qq.get("channel") or "napcat").strip().lower()
    sub = qq.get(name) or {}
    ws = sub.get("ws_urls")
    if isinstance(ws, str):
        ws = [ws]
    if not ws and qq.get("ws_url"):
        ws = [qq["ws_url"]]
    return list(ws) if ws else []


def _napcat_reachable(timeout: float = 1.0) -> bool:
    """TCP-probe the active QQ channel's OneBot WS endpoint(s) (best-effort).

    Reads WS URLs from packages/qq/.env (ONEBOT_WS_URLS) first, then falls back
    to config.json's qq.<channel>.ws_urls (the new config-driven channel path).
    Returns True when at least one configured forward-WS host accepts a
    connection, or when no endpoint is configured (no degradation to report).
    Never raises; failures mean "unreachable".
    """
    urls = _qq_ws_urls_from_env() or _qq_ws_urls_from_config()
    if not urls:
        return True
    for url in urls:
        try:
            p = urlparse(url)
            host, port = p.hostname, p.port or (443 if p.scheme == "wss" else 80)
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            continue
    return False


def _qq_health_check() -> None:
    """Background check right after spawn: detect fast crash / NapCat unreachable.

    Runs on a daemon thread so Pan Core startup is never blocked. Only logs:
    the QQ module is a child process, its failure must not look like a Pan
    startup failure.
    """
    proc = _qq_proc
    if proc is None:
        return
    try:
        proc.wait(timeout=_QQ_STARTUP_GRACE_SEC)
    except subprocess.TimeoutExpired:
        # 子进程存活：NapCat 不可达时提示降级（bot.py 会自行每 3s 重试）。
        if not _napcat_reachable():
            _log.warning(
                "[Pan] NapCat unreachable — QQ module runs degraded "
                "(bot.py retries the OneBot WS connection every 3s)"
            )
        return
    _log.warning(
        "[Pan] QQ bot exited during startup (code %s) — QQ module degraded, "
        "Pan Core continues without QQ",
        proc.returncode,
    )


def _stop_qq_bot() -> None:
    """Terminate the QQ bot subprocess if still running (graceful path)."""
    global _qq_proc
    if _qq_proc is not None:
        if _qq_proc.poll() is None:
            _qq_proc.terminate()
            try:
                _qq_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _qq_proc.kill()
                _qq_proc.wait()
            _log.info("[Pan] QQ bot stopped (pid %s)", _qq_proc.pid)
        _qq_proc = None
    _QQ_PID_FILE.unlink(missing_ok=True)


def _resolve_qq_python() -> str:
    """解析 QQ bot 解释器路径（优先级链）：

    1. PAN_QQ_PYTHON 环境变量（临时覆盖，调试用）
    2. config.json 的 qq.python（单一事实源，setup.bat 写入）
    3. 平台默认 _QQ_DEFAULT_PYTHON（nt: E盘 miniforge / POSIX: python3）
    """
    env = os.environ.get("PAN_QQ_PYTHON")
    if env:
        return env
    try:
        from packages.core.config import load_config

        configured = ((load_config().get("qq") or {}).get("python") or "").strip()
    except Exception:
        configured = ""
    if configured:
        return configured
    return _QQ_DEFAULT_PYTHON


def _spawn_qq_bot() -> None:
    """Start the QQ bridge (packages/qq/bot.py) if config qq.enabled.

    The QQ bot is a child of this process, so stopping main.py (e.g. via
    stop_pan.bat's process-tree kill) takes the QQ bot down with it. A pid
    file is written so the stop script can also target it directly.
    """
    global _qq_proc
    from packages.core.config import load_config

    cfg_qq = load_config().get("qq") or {}
    if not cfg_qq.get("enabled", True):
        _log.info("[Pan] QQ module disabled (qq.enabled=false), skipping bot.py")
        return

    # Avoid double-spawning if a previous QQ bot is still alive (e.g. main.py
    # was restarted without going through stop_pan.bat).
    if _QQ_PID_FILE.exists():
        try:
            old_pid = int(_QQ_PID_FILE.read_text(encoding="utf-8").strip())
        except ValueError:
            old_pid = 0
        if old_pid and _is_pid_alive(old_pid):
            _log.warning("[Pan] QQ bot pid %s still alive, skipping spawn — stop it first", old_pid)
            return

    python = _resolve_qq_python()
    try:
        # bot.py 顶层 `from packages.qq import ...` 需要项目根在 sys.path；
        # cwd 是 packages/qq 且子进程不继承父进程 sys.path，必须显式注入
        # PYTHONPATH，否则每次 spawn 都 ModuleNotFoundError 退出（code 1）。
        _qq_proc = subprocess.Popen(
            [python, str(_QQ_BOT_PY)],
            cwd=str(_QQ_DIR),
            env={**os.environ, "PYTHONPATH": str(_PROJECT_ROOT)},
        )
    except Exception as e:  # noqa: BLE001
        _log.error("[Pan] QQ bot spawn failed: %s", e)
        _qq_proc = None
        return
    _QQ_PID_FILE.write_text(str(_qq_proc.pid), encoding="utf-8")
    _log.info("[Pan] QQ bot started (pid %s, %s)", _qq_proc.pid, python)
    # 后台健康检查：快速崩溃 / NapCat 不可达 → 只打降级日志，不阻塞 Pan Core。
    threading.Thread(target=_qq_health_check, name="qq-health-check", daemon=True).start()


if __name__ == "__main__":
    import uvicorn
    from packages.core.config import load_config
    from packages.core.logging_setup import setup_logging

    # 本地日志：data/logs/pan.log（大小/天轮转）+ console 双输出
    setup_logging()

    host = os.environ.get("PAN_HOST", "127.0.0.1")
    env_port = os.environ.get("PAN_PORT")
    if env_port is not None:
        port = int(env_port)
    else:
        port = load_config().get("port", 8768)

    _log.info("Pan starting on %s:%s", host, port)

    # No-auth guard (#16, resolved by policy): the API has no authentication.
    # Binding to anything but loopback exposes every endpoint on the network.
    import ipaddress
    try:
        is_loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        is_loopback = host in ("localhost", "::1")
    if not is_loopback:
        _log.warning(
            "Pan API has NO authentication and is bound to '%s' — all endpoints "
            "are reachable by anyone on this network. Keep PAN_HOST at 127.0.0.1 "
            "unless you know what you are doing.",
            host,
        )

    # QQ bridge (NoneBot bot.py) is part of the Pan process tree: spawned when
    # qq.enabled, torn down when this server shuts down.
    _spawn_qq_bot()
    atexit.register(_stop_qq_bot)

    config = uvicorn.Config(app, host=host, port=port, log_level="info", access_log=False)
    server = uvicorn.Server(config)
    server.run()

    # uvicorn returns after a graceful shutdown (Ctrl+C / SIGTERM handled
    # internally); make sure the QQ bot is torn down too.
    _stop_qq_bot()
