"""Resident kernel transport; no provider or user credentials involved."""
import hashlib
import json
import threading

import pytest
from websockets.exceptions import InvalidStatus
from websockets.http11 import Response
from websockets.datastructures import Headers
from websockets.sync.server import serve

from packages.core.adapters.codex.native_transport import NativeTransport
from packages.core.adapters.codex.app_server_wrapper import AppServer
from packages.core.adapters.codex.adapter import CodexAdapter
from packages.core.session import Session


def test_private_capability_only_digest_in_arguments():
    transport = NativeTransport()
    args = transport.server_args()
    assert args[:2] == ["--listen", transport.url]
    assert transport.url.startswith("ws://127.0.0.1:")
    assert transport._capability not in " ".join(args)
    assert args[-1] == hashlib.sha256(transport._capability.encode()).hexdigest()
    assert NativeTransport()._capability != transport._capability


def test_auth_unicode_roundtrip_and_close():
    transport = NativeTransport()
    seen = []
    def authorize(connection, request):
        seen.append(request.headers.get("Authorization"))
        if seen[-1] != "Bearer " + transport._capability:
            return Response(401, "Unauthorized", Headers(), b"denied")
    def handler(ws):
        for raw in ws:
            ws.send(raw)
    port = int(transport.url.rsplit(":", 1)[1])
    with serve(handler, "127.0.0.1", port, process_request=authorize) as server:
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            transport.connect(lambda: None, timeout=1)
            payload = {"id": 1, "method": "test", "params": {"text": "中文\n输入"}}
            transport.send(payload)
            assert next(transport.messages()) == payload
            assert seen == ["Bearer " + transport._capability]
            transport.close()
            transport.close()
            with pytest.raises(RuntimeError):
                transport.send(payload)
        finally:
            transport.close()
            server.shutdown()
            thread.join(2)
            assert not thread.is_alive()


def test_auth_failure_is_not_retried_or_attached():
    transport = NativeTransport()
    attempts = []
    def reject(connection, request):
        attempts.append(1)
        return Response(401, "Unauthorized", Headers(), b"denied")
    port = int(transport.url.rsplit(":", 1)[1])
    with serve(lambda ws: None, "127.0.0.1", port, process_request=reject) as server:
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            with pytest.raises(InvalidStatus):
                transport.connect(lambda: None, timeout=1)
            assert len(attempts) == 1
            assert transport._connection is None
        finally:
            server.shutdown()
            thread.join(2)


def test_dead_process_and_zero_budget_fail_closed():
    transport = NativeTransport()
    with pytest.raises(RuntimeError, match="exited"):
        transport.connect(lambda: 6)
    with pytest.raises(TimeoutError):
        transport.connect(lambda: None, timeout=0)
    assert transport._connection is None


def test_default_and_opt_in_adapter_arguments():
    adapter = CodexAdapter()
    session = Session(id="ses_native_test", name="native", adapter="codex")
    assert "--native-control" not in adapter.build_spawn_args(session)
    session.adapter_config["native_control"] = "true"
    assert "--native-control" not in adapter.build_spawn_args(session)
    session.adapter_config["native_control"] = True
    assert "--native-control" in adapter.build_spawn_args(session)


def test_native_reader_preserves_events_and_eof(monkeypatch):
    app = AppServer("node", "codex.js", ".", [], native_control=True)
    class Transport:
        def messages(self):
            yield {"method": "item/agentMessage/delta", "params": {"delta": "中文"}}
    app._native_transport = Transport()
    app._read_native()
    assert app.incoming.get_nowait()["params"]["delta"] == "中文"
    assert app.incoming.get_nowait() is None


def test_native_reader_errors_do_not_leak(monkeypatch):
    app = AppServer("node", "codex.js", ".", [], native_control=True)
    class Transport:
        def messages(self):
            raise ValueError("SECRET-CAPABILITY")
    app._native_transport = Transport()
    errors = []
    monkeypatch.setattr("packages.core.adapters.codex.app_server_wrapper._write_stderr", errors.append)
    app._read_native()
    assert app.incoming.get_nowait() is None
    assert errors and "SECRET-CAPABILITY" not in json.dumps(errors)


def test_failed_terminal_display_does_not_disconnect_authoritative_history(monkeypatch):
    from packages.core.adapters.codex.resident_tui import ResidentTui
    app = AppServer("node", "codex.js", ".", [], native_control=True)
    events = []
    tui = ResidentTui(app, events.append)
    tui.ready.set()
    tui.down = type("ClosedDisplay", (), {"send": lambda *_: (_ for _ in ()).throw(OSError("closed"))})()
    app._resident_tui = tui
    notifications = [{"method": "item/completed", "params": {"threadId": "t", "turnId": "turn",
        "item": {"type": "agentMessage", "id": "answer", "text": "persist this"}}},
        {"method": "turn/completed", "params": {"threadId": "t", "turn": {"id": "turn", "status": "completed"}}}]
    app.thread_id = "t"
    app._native_transport = type("Transport", (), {"messages": lambda _: iter(notifications)})()
    app._read_native()
    assert [app.incoming.get_nowait(), app.incoming.get_nowait()] == notifications
    assert app.incoming.get_nowait() is None
    assert any(event.get("type") == "pan.native_error" for event in events)
