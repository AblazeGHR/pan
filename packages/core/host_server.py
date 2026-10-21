"""Uvicorn signal boundary for the Pan host, separate from Worker interrupts."""
import uvicorn
from packages.core import worker


class PanServer(uvicorn.Server):
    async def serve(self, sockets=None):
        from packages.core.logging_setup import _resolve_log_path
        from packages.core.config import load_config
        from packages.core.runtime_diagnostics import runtime
        directory = _resolve_log_path(load_config().get("logging", {})).parent
        async with runtime.monitor(directory):
            return await super().serve(sockets=sockets)

    def handle_exit(self, sig, frame):
        worker.begin_host_exit()
        super().handle_exit(sig, frame)
