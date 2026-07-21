"""Small status HTTP server for the Remote channel.

Exposes tunnel status and basic control endpoints on a separate port so the
Dashboard can display remote access information without importing Core internals.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import FastAPI

from .tunnel import CloudflareTunnel


def make_app(tunnel: CloudflareTunnel, local_port: int) -> FastAPI:
    """Create a FastAPI app that reports tunnel status."""
    app = FastAPI(title="Pan Remote")

    @app.get("/status")
    async def status():
        started = tunnel.started_at()
        uptime_seconds = None
        if started is not None:
            uptime_seconds = (datetime.now() - started).total_seconds()
        return {
            "running": tunnel.is_running(),
            "url": tunnel.url(),
            "local_port": local_port,
            "started_at": started.isoformat() if started else None,
            "uptime_seconds": uptime_seconds,
        }

    return app
