"""Uvicorn signal boundary for the Pan host, separate from Worker interrupts."""
import uvicorn
from packages.core import worker


class PanServer(uvicorn.Server):
    def handle_exit(self, sig, frame):
        worker.begin_host_exit()
        super().handle_exit(sig, frame)
