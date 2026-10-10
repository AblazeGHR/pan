"""Large native resume must preserve context without replaying Pan history."""
import json
import threading
from types import SimpleNamespace

import pytest
from websockets.exceptions import ConnectionClosedError
from websockets.frames import Close
from websockets.sync.server import serve

from packages.core.adapters.codex import app_server_wrapper as wrapper
from packages.core.adapters.codex.native_transport import MAX_MESSAGE_BYTES, NativeTransport


def test_transport_accepts_history_larger_than_old_limit(monkeypatch):
    import websockets.sync.client as client
    original = client.connect
    limits = []
    def connect(*args, **kwargs):
        limits.append(kwargs["max_size"])
        return original(*args, **kwargs)
    monkeypatch.setattr(client, "connect", connect)
    transport = NativeTransport()
    payload = {"id": 2, "result": {"thread": {"id": "old", "turns": [
        {"text": "x" * (16 * 1024 * 1024 + 1024)}]}}}
    raw = json.dumps(payload)
    port = int(transport.url.rsplit(":", 1)[1])
    assert MAX_MESSAGE_BYTES == 64 * 1024 * 1024
    def handler(ws):
        ws.recv()
        ws.send(raw)
    with serve(handler, "127.0.0.1", port) as server:
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            transport.connect(lambda: None, timeout=2)
            assert limits == [MAX_MESSAGE_BYTES]
            transport.send({"method": "read"})
            assert next(transport.messages()) == payload
        finally:
            transport.close()
            server.shutdown()
            thread.join(3)
            assert not thread.is_alive()


@pytest.mark.parametrize("resumed", [False, True])
def test_start_requests_metadata_only_resume_not_new_thread(monkeypatch, resumed):
    app = wrapper.AppServer("node", "codex.js", ".", [])
    app.initial_thread_id = "retained" if resumed else None
    calls, emitted = [], []
    monkeypatch.setattr(wrapper.subprocess, "Popen", lambda *a, **k: SimpleNamespace())
    monkeypatch.setattr(wrapper.threading, "Thread", lambda *a, **k: SimpleNamespace(start=lambda: None))
    def request(method, params):
        calls.append((method, dict(params)))
        return len(calls)
    monkeypatch.setattr(app, "_request", request)
    monkeypatch.setattr(app, "_send", lambda message: None)
    metadata = {"thread": {"id": "retained" if resumed else "new", "turns": []},
                "model": "same-model", "approvalPolicy": "never"}
    monkeypatch.setattr(app, "_wait_response", lambda n: {"result": metadata} if n == 2 else {"result": {}})
    monkeypatch.setattr(wrapper, "_write_stdout", emitted.append)
    app.start()
    method, params = calls[-1]
    assert method == ("thread/resume" if resumed else "thread/start")
    if resumed:
        assert params["threadId"] == "retained"
        assert params["excludeTurns"] is True
        assert app._thread_has_turns is True  # Empty response isn't empty model context.
    else:
        assert "excludeTurns" not in params
        assert app._thread_has_turns is False
    assert app._thread_resume_info == metadata  # TUI still gets live metadata.
    assert emitted == [{"type": "thread.started", "thread_id": app.thread_id}]


@pytest.mark.parametrize("oversized", [False, True])
def test_transport_loss_while_process_alive_is_not_reported_as_exit(monkeypatch, oversized):
    app = wrapper.AppServer("node", "codex.js", ".", [], native_control=True)
    app.process = SimpleNamespace(poll=lambda: None)
    def messages():
        if oversized:
            raise ConnectionClosedError(None, Close(1009, "SECRET-CAPABILITY"))
        raise ValueError("SECRET-CAPABILITY")
        yield
    app._native_transport = SimpleNamespace(messages=messages)
    errors = []
    monkeypatch.setattr(wrapper, "_write_stderr", errors.append)
    app._read_native()
    with pytest.raises(RuntimeError) as captured:
        app._next()
    text = str(captured.value)
    assert "receive limit" in text if oversized else "disconnected" in text
    assert "exited" not in text and "returncode=None" not in text
    assert "SECRET-CAPABILITY" not in text + json.dumps(errors)


def test_actual_process_exit_reports_polled_exit_code():
    app = wrapper.AppServer("node", "codex.js", ".", [], native_control=True)
    app._native_transport = object()
    app.process = SimpleNamespace(returncode=None, poll=lambda: 7)
    app.incoming.put(None)
    with pytest.raises(RuntimeError, match=r"exited \(returncode=7\)"):
        app._next()
