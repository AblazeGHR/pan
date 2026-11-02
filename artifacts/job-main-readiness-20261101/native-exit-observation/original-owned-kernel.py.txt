"""Native-control app-server ownership; no unguarded Node descendants."""
from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path


class OwnedKernel:
    stdin = stdout = stderr = None

    def __init__(self, argv, cwd):
        root = str(Path(__file__).resolve().parents[4])
        if root not in sys.path:
            sys.path.insert(0, root)
        from packages.core.terminal.backend import ConPtyBackend
        self.backend = ConPtyBackend.spawn(argv, cwd=cwd, rows=30, cols=120)
        self.pid = self.backend.pid
        self._reader = threading.Thread(target=self._drain, daemon=True)
        self._reader.start()

    def _drain(self):
        try:
            while not self.backend.wait_dead(0):
                self.backend.read(65536, timeout=.2)
        except Exception:
            # Diagnostic stdout is not the transport. The authenticated WS
            # reader independently observes kernel liveness/protocol failure.
            pass

    def poll(self):
        return self.backend.exit_code if self.backend.wait_dead(0) else None

    @property
    def returncode(self):
        return self.poll()

    def terminate(self):
        _, remaining = self.backend.guard.terminate_tree(self.pid, timeout=2)
        if remaining:
            raise RuntimeError("Native kernel tree cleanup unconfirmed")

    kill = terminate

    def wait(self, timeout=None):
        if not self.backend.wait_dead(2 if timeout is None else timeout):
            raise subprocess.TimeoutExpired("native-kernel", timeout)
        return self.backend.exit_code

    def close(self):
        self._reader.join(1)
        if self._reader.is_alive():
            raise RuntimeError("Native kernel reader cleanup unconfirmed")
        report = self.backend.close()
        if report.get("closed") is not True:
            raise RuntimeError("Native kernel handle cleanup unconfirmed")
