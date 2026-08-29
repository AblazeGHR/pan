"""Regression tests for dashboard WebSocket user injection."""

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from fastapi import WebSocketDisconnect

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import packages.web.server as srv


class _FakeWS:
    def __init__(self, message: dict):
        self._message = json.dumps(message)
        self._received = False
        self.sent: list[dict] = []

    async def accept(self):
        pass

    async def receive_text(self):
        if not self._received:
            self._received = True
            return self._message
        raise WebSocketDisconnect(code=1000)

    async def send_json(self, data: dict):
        self.sent.append(data)


def test_user_inject_without_worker_spawns_and_delivers(monkeypatch):
    """An open dashboard WS must not silently drop the first message."""
    srv.ws_clients.clear()
    ws = _FakeWS({"type": "user_inject", "sessionId": "session-1", "text": "hello"})
    created = SimpleNamespace(worker_id="worker-1")
    calls = []

    monkeypatch.setattr(srv.worker, "find_worker_by_session", lambda _: None)

    async def fake_create_worker(session_id):
        calls.append(("create", session_id))
        return created

    async def fake_send_task(worker_id, text, source="agent"):
        calls.append(("send", worker_id, text, source))
        return None

    monkeypatch.setattr(srv.worker, "create_worker", fake_create_worker)
    monkeypatch.setattr(srv.worker, "send_task", fake_send_task)

    asyncio.run(srv.ws_endpoint(ws))

    assert calls == [
        ("create", "session-1"),
        ("send", "worker-1", "hello", "user"),
    ]
    assert ws not in srv.ws_clients


def test_agent_task_does_not_send_after_spawn_failure(monkeypatch):
    """A failed agent spawn must return an error without dereferencing None."""
    srv.agent_clients.clear()
    srv.agent_subscriptions.clear()
    ws = _FakeWS({"type": "task", "sessionId": "session-1", "text": "hello"})
    send_calls = []

    monkeypatch.setattr(srv.worker, "find_worker_by_session", lambda _: None)

    async def fake_create_worker(session_id):
        return "spawn failed"

    async def fake_send_task(*args, **kwargs):
        send_calls.append((args, kwargs))
        return None

    monkeypatch.setattr(srv.worker, "create_worker", fake_create_worker)
    monkeypatch.setattr(srv.worker, "send_task", fake_send_task)

    asyncio.run(srv.ws_agent_endpoint(ws))

    assert ws.sent == [{"type": "error", "message": "spawn failed"}]
    assert send_calls == []
    assert ws not in srv.agent_clients
