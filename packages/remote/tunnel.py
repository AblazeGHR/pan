"""Cloudflare tunnel manager for the Pan Remote channel."""

from __future__ import annotations

import asyncio
import re
import shutil
from datetime import datetime
from pathlib import Path


class CloudflareTunnel:
    """Manage a cloudflared subprocess for exposing Pan to the internet."""

    _URL_RE = re.compile(r"https://[a-zA-Z0-9\-]+\.trycloudflare\.com")

    def __init__(self):
        self._proc: asyncio.subprocess.Process | None = None
        self._url: str | None = None
        self._started_at: datetime | None = None
        self._stdout_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None

    @staticmethod
    def _resolve_binary(binary_path: str | None) -> str:
        """Return the cloudflared binary path, falling back to PATH lookup."""
        if binary_path:
            p = Path(binary_path)
            if p.exists():
                return str(p.resolve())
            return binary_path
        which = shutil.which("cloudflared")
        if which:
            return which
        # Final fallback; subprocess will raise FileNotFoundError if absent.
        return "cloudflared"

    async def start(
        self,
        port: int,
        *,
        quick: bool = True,
        config_path: str | None = None,
        binary_path: str | None = None,
    ) -> str | None:
        """Start cloudflared.

        For quick tunnels, returns the public URL once parsed. For named tunnels
        the URL is determined by the Cloudflare tunnel config; this method
        returns None for the URL and the caller should rely on DNS.

        Returns an error string on failure, or the public URL on success for
        quick tunnels.
        """
        if self._proc is not None and self._proc.returncode is None:
            return self._url

        bin_path = self._resolve_binary(binary_path)

        if quick:
            args = [bin_path, "tunnel", "--url", f"http://127.0.0.1:{port}"]
        else:
            if not config_path:
                return "Named tunnel requires remote.config_path"
            cfg = Path(config_path)
            if not cfg.exists():
                return f"cloudflared config not found: {config_path}"
            args = [bin_path, "tunnel", "--config", str(cfg), "run"]

        try:
            self._proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            return f"cloudflared binary not found: {bin_path}"
        except OSError as e:
            return f"Failed to start cloudflared: {e}"

        self._started_at = datetime.now()
        self._stdout_task = asyncio.create_task(self._read_stream(self._proc.stdout, "stdout"))
        self._stderr_task = asyncio.create_task(self._read_stream(self._proc.stderr, "stderr"))

        if quick:
            # Wait up to 30 seconds for the quick tunnel URL to appear.
            for _ in range(60):
                if self._url:
                    return self._url
                if self._proc.returncode is not None:
                    break
                await asyncio.sleep(0.5)
            if not self._url:
                return "Timed out waiting for quick tunnel URL"

        return None

    async def _read_stream(self, stream: asyncio.StreamReader | None, label: str):
        """Read cloudflared output line by line, extracting the quick URL."""
        if stream is None:
            return
        while True:
            try:
                line = await stream.readline()
            except Exception:
                break
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip()
            if not text:
                continue
            print(f"[cloudflared {label}] {text}")
            if self._url is None:
                match = self._URL_RE.search(text)
                if match:
                    self._url = match.group(0)
                    print(f"[Remote] quick tunnel URL: {self._url}")

    def stop(self) -> None:
        """Terminate the cloudflared process tree."""
        if self._stdout_task and not self._stdout_task.done():
            self._stdout_task.cancel()
        if self._stderr_task and not self._stderr_task.done():
            self._stderr_task.cancel()

        if self._proc is None:
            return

        try:
            if self._proc.returncode is None and self._proc.pid:
                # Use psutil if available, otherwise fall back to terminate/kill.
                try:
                    import psutil

                    parent = psutil.Process(self._proc.pid)
                    for child in parent.children(recursive=True):
                        try:
                            child.kill()
                        except psutil.NoSuchProcess:
                            pass
                    parent.kill()
                except ImportError:
                    self._proc.terminate()
        except ProcessLookupError:
            pass
        except Exception as e:
            print(f"[Remote] error stopping tunnel: {e}")
        finally:
            self._proc = None
            self._url = None
            self._started_at = None

    def is_running(self) -> bool:
        """Return True if cloudflared is still running."""
        return self._proc is not None and self._proc.returncode is None

    def url(self) -> str | None:
        """Return the quick tunnel URL, or None if not quick/named."""
        return self._url

    def started_at(self) -> datetime | None:
        """Return the tunnel start time, or None if not started."""
        return self._started_at

    def __del__(self):
        """Best-effort cleanup on garbage collection."""
        try:
            self.stop()
        except Exception:
            pass
