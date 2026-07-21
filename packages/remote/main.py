"""Entry point for the Pan Remote channel."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
from pathlib import Path

import uvicorn

from .api import make_app
from .tunnel import CloudflareTunnel


_CONFIG_FILE = Path(__file__).resolve().parent.parent.parent / "config.json"


def _load_config() -> dict:
    """Load config.json from the project root without importing Core."""
    if not _CONFIG_FILE.exists():
        return {}
    try:
        with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[Remote] warning: failed to read config.json: {e}")
        return {}


async def _start_status_server(tunnel: CloudflareTunnel, local_port: int, status_port: int):
    """Start a small FastAPI status server in the background."""
    app = make_app(tunnel, local_port)
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=status_port,
        log_level="info",
        access_log=False,
    )
    server = uvicorn.Server(config)
    await server.serve()


async def main():
    """Start the Remote channel: tunnel + status server."""
    config = _load_config()
    remote_cfg = config.get("remote", {})

    if not remote_cfg.get("enabled", False):
        print("[Remote] remote.enabled is false; exiting. Set it to true in config.json to start.")
        sys.exit(0)

    provider = remote_cfg.get("provider", "cloudflare")
    if provider != "cloudflare":
        print(f"[Remote] unsupported provider: {provider}")
        sys.exit(1)

    local_port = int(config.get("port", 8767))
    quick = remote_cfg.get("quick_tunnel", True)
    config_path = remote_cfg.get("config_path") or None
    binary_path = remote_cfg.get("binary_path") or None
    status_port = int(remote_cfg.get("status_port", 8769))

    # Environment overrides for local port, matching main.py behavior.
    env_port = os.environ.get("PAN_PORT")
    if env_port is not None:
        local_port = int(env_port)

    tunnel = CloudflareTunnel()

    result = await tunnel.start(
        port=local_port,
        quick=quick,
        config_path=config_path,
        binary_path=binary_path,
    )
    if isinstance(result, str):
        print(f"[Remote] failed to start tunnel: {result}")
        sys.exit(1)

    if tunnel.url():
        print(f"[Remote] public URL: {tunnel.url()}")
    else:
        print("[Remote] named tunnel running (public URL is configured in Cloudflare DNS)")

    # Start status server in the background.
    status_task = asyncio.create_task(
        _start_status_server(tunnel, local_port, status_port)
    )

    def _shutdown(sig):
        print(f"\n[Remote] received signal {sig}, stopping tunnel...")
        tunnel.stop()
        status_task.cancel()

    if sys.platform != "win32":
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _shutdown, sig)
    else:
        # Windows: rely on the default KeyboardInterrupt from asyncio.
        pass

    try:
        await status_task
    except asyncio.CancelledError:
        pass
    finally:
        tunnel.stop()


if __name__ == "__main__":
    asyncio.run(main())
