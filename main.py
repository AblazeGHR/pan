#!/usr/bin/env python
"""Pan — entry point."""

import atexit
import logging
import os
import subprocess
from pathlib import Path

from packages.web.server import app

_log = logging.getLogger("pan")

_PROJECT_ROOT = Path(__file__).resolve().parent
_QQ_BOT_PY = _PROJECT_ROOT / "packages" / "qq" / "bot.py"
_QQ_DIR = _QQ_BOT_PY.parent
_QQ_PID_FILE = _PROJECT_ROOT / "data" / "qq_bot.pid"
# NoneBot 依赖装在 miniforge（项目 .venv 没有），故 QQ bot 用独立解释器。
# 可用环境变量 PAN_QQ_PYTHON 覆盖。
_QQ_DEFAULT_PYTHON = r"E:\software\miniforge\python.exe"

_qq_proc: subprocess.Popen | None = None


def _is_pid_alive(pid: int) -> bool:
    """Best-effort liveness check for a local PID (works on Windows too)."""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


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

    python = os.environ.get("PAN_QQ_PYTHON") or _QQ_DEFAULT_PYTHON
    try:
        _qq_proc = subprocess.Popen([python, str(_QQ_BOT_PY)], cwd=str(_QQ_DIR))
    except Exception as e:  # noqa: BLE001
        _log.error("[Pan] QQ bot spawn failed: %s", e)
        _qq_proc = None
        return
    _QQ_PID_FILE.write_text(str(_qq_proc.pid), encoding="utf-8")
    _log.info("[Pan] QQ bot started (pid %s, %s)", _qq_proc.pid, python)


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
