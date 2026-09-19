"""Regression tests for dashboard WebSocket user injection."""

import asyncio
import json
import sys
from pathlib import Path

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


def test_user_inject_without_worker_persists_and_acknowledges(monkeypatch):
    """An open dashboard WS uses the durable session queue when offline.

    契约（统一服务端队列）：user_inject 不再走 send_session，而是进入
    enqueue_user_message 的规范队列；ack 携带 queueItemId / queueRevision。
    """
    srv.ws_clients.clear()
    ws = _FakeWS({"type": "user_inject", "sessionId": "session-1", "text": "hello"})
    calls = []

    async def fake_enqueue_user_message(session_id, text, client_message_id=None):
        calls.append(("enqueue_user_message", session_id, text, client_message_id))
        return {"status": "queued", "workerId": None, "sessionId": session_id,
                "queueItemId": "q_test_1"}

    monkeypatch.setattr(srv.worker, "enqueue_user_message", fake_enqueue_user_message)

    asyncio.run(srv.ws_endpoint(ws))

    assert calls == [("enqueue_user_message", "session-1", "hello", None)]
    assert ws.sent == [{"type": "user_inject.accepted", "sessionId": "session-1",
                        "workerId": None, "clientMessageId": None,
                        "queueItemId": "q_test_1", "queueRevision": 0}]
    assert ws not in srv.ws_clients


def test_user_inject_rejects_attachment_for_another_session(monkeypatch):
    srv.ws_clients.clear()
    ws = _FakeWS({
        "type": "user_inject",
        "sessionId": "session-1",
        "text": "[foreign](/api/attachments/upload_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.txt?session_id=session-2)",
    })
    enqueue_calls = []

    monkeypatch.setattr(srv.sess, "get", lambda session_id: object() if session_id in {"session-1", "session-2"} else None)

    async def fake_enqueue_user_message(*args, **kwargs):
        enqueue_calls.append((args, kwargs))
        return {"status": "queued"}

    monkeypatch.setattr(srv.worker, "enqueue_user_message", fake_enqueue_user_message)

    asyncio.run(srv.ws_endpoint(ws))

    assert enqueue_calls == []
    assert ws.sent == [{
        "type": "user_inject.rejected",
        "sessionId": "session-1",
        "message": "Attachment belongs to another session",
        "error": {
            "code": "attachment_session_mismatch",
            "message": "Attachment belongs to another session",
        },
    }]
    assert ws not in srv.ws_clients


def test_dashboard_ping_gets_pong():
    srv.ws_clients.clear()
    ws = _FakeWS({"type": "ping"})

    asyncio.run(srv.ws_endpoint(ws))

    assert ws.sent == [{"type": "pong"}]
    assert ws not in srv.ws_clients


def test_user_inject_rejects_cross_session_structured_parts(monkeypatch, tmp_path):
    """DEC-002: a queued chip/inline part never adopts another Session's upload."""
    from packages.core import session as _sess

    monkeypatch.setattr(srv, "ATTACHMENTS_DIR", tmp_path / "attachments")
    _sess._cache.clear()
    owner = _sess.Session(id="ses_ws_owner", name="owner", workdir=str(tmp_path))
    target = _sess.Session(id="ses_ws_target", name="target", workdir=str(tmp_path))
    _sess._cache[owner.id] = owner
    _sess._cache[target.id] = target
    srv.ws_clients.clear()
    stored = srv._attachment_session_dir(owner.id) / ("upload_" + "a" * 32 + ".txt")
    stored.parent.mkdir(parents=True)
    stored.write_bytes(b"owner bytes")
    srv._register_attachment(owner.id, stored.name, {
        "source": "upload", "displayName": "owner.txt", "storageFilename": stored.name,
        "path": str(stored), "size": 11, "mimeType": "text/plain",
    })
    ws = _FakeWS({
        "type": "user_inject",
        "sessionId": target.id,
        "parts": [{"type": "attachment", "attachmentId": stored.name}],
    })
    enqueue_calls = []

    async def fake_enqueue_user_message(*args, **kwargs):
        enqueue_calls.append((args, kwargs))
        return {"status": "queued"}

    monkeypatch.setattr(srv.worker, "enqueue_user_message", fake_enqueue_user_message)

    asyncio.run(srv.ws_endpoint(ws))

    assert enqueue_calls == []
    assert target.queue_pending == []
    assert ws.sent == [{
        "type": "user_inject.rejected",
        "sessionId": target.id,
        "message": "Attachment belongs to another session",
        "error": {
            "code": "attachment_session_mismatch",
            "message": "Attachment belongs to another session",
        },
    }]
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
