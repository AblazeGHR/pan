"""Private authenticated transport for a resident Codex execution kernel.

This is opt-in infrastructure, not an externally exposed control endpoint.
The capability must never be included in Pan events, history or browser data.
Disconnecting a remote TUI can interrupt a Codex turn; mode changes must retain
that client. Transport loss is terminal here: never silently resume a kernel.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import socket
import time
from typing import Any, Callable


class NativeTransport:
    def __init__(self) -> None:
        # Codex currently requires a concrete port. Closing this reservation
        # leaves a bind race; auth + startup failure are mandatory, not a claim
        # that the port is atomically reserved for the child.
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        self.url = f"ws://127.0.0.1:{port}"
        self._capability = secrets.token_urlsafe(32)
        self._connection: Any = None
        self._context: Any = None

    def server_args(self) -> list[str]:
        return ["--listen", self.url, "--ws-auth", "capability-token",
                "--ws-token-sha256", hashlib.sha256(
                    self._capability.encode("ascii")).hexdigest()]

    def peer(self, poll: Callable[[], int | None]) -> "NativeTransport":
        peer = object.__new__(NativeTransport)
        peer.url, peer._capability = self.url, self._capability
        peer._connection = peer._context = None
        peer.connect(poll)
        return peer

    def connect(self, poll: Callable[[], int | None], timeout: float = 10.0) -> None:
        from websockets.sync.client import connect

        deadline = time.monotonic() + timeout
        while True:
            if poll() is not None:
                raise RuntimeError("Codex native kernel exited before connection")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Codex native kernel connection timed out")
            try:
                context = connect(
                    self.url, proxy=None, open_timeout=min(0.5, remaining),
                    close_timeout=0.5, max_size=16 * 1024 * 1024,
                    additional_headers={"Authorization": f"Bearer {self._capability}"},
                )
                connection = context.__enter__()
            except OSError:
                time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
                continue
            # Authentication/protocol failures are not transient and must not
            # attach to an unrelated listener that won the port race.
            self._connection = connection
            self._context = context
            return

    def send(self, message: dict[str, Any]) -> None:
        if self._connection is None:
            raise RuntimeError("Codex native transport is not connected")
        self._connection.send(json.dumps(message, ensure_ascii=True, separators=(",", ":")))

    def messages(self):
        if self._connection is None:
            raise RuntimeError("Codex native transport is not connected")
        for raw in self._connection:
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise RuntimeError("Invalid Codex native protocol message")
            yield value

    def close(self) -> None:
        connection, self._connection = self._connection, None
        context, self._context = self._context, None
        if context is not None:
            context.__exit__(None, None, None)
        elif connection is not None:
            connection.close()
