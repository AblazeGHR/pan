"""Independent native resources keep durable liability without foreground effects."""
import asyncio
import copy
import json
import importlib.util
import sys
import threading
from pathlib import Path
from queue import Queue
from types import SimpleNamespace

import pytest

from packages.core import worker, session, legal_offline as offline
from packages.core.adapters.codex.app_server_wrapper import AppServer
from packages.core.adapters.codex.resident_tui import ResidentTui
from test_legal_offline import setup, begin, runtime_registry


async def harness(monkeypatch, messages, case="ok"):
    s, w = setup()
    await begin(s, w)
    w.adapter = SimpleNamespace(name="codex")
    s.cli_session_id = "thread"
    w.native_control_mode, w.native_control_generation = "native", 1
    runtime = (w, w.generation, w.process, w.process.stdin)
    app = AppServer("node", "codex.js", ".", [], native_control=True, native_passthrough=True)
    app.thread_id = "thread"
    app.process = SimpleNamespace(poll=lambda: None)
    app.control_owner.switch("native", 0)
    app._turn_active.set()
    app.native_admission_timeout = .03 if case in {"lost-ack", "late-ack"} else 2
    events, sent, replies, pending, receipts = [], [], [], [], []
    loop = asyncio.get_running_loop()
    async def handle(event):
        if event["type"] == "pan.native_turn_admission":
            try:
                receipt = await worker._admit_native_turn(w, runtime, request_id=event["requestId"],
                    thread_id=event["threadId"], control_generation=event["controlGeneration"],
                    operation=event["operation"], context=event.get("connection"))
                receipt["ok"] = True
            except OSError:
                receipt = dict(requestId=event["requestId"], ok=False)
            receipts.append(receipt)
            if case == "owner-switch":
                app.control_owner.switch("pan", 1)
            if case not in {"lost-ack", "late-ack"}:
                app.native_admission_receipt(receipt)
        else:
            await worker._native_operation_event(w, runtime, event)
    def emit(event):
        events.append(event)
        if event["type"] in {"pan.native_turn_admission", "pan.native_turn_not_forwarded",
                            "pan.native_operation_bind", "pan.native_operation_terminal",
                            "pan.native_operation_uncertain"}:
            pending.append(asyncio.run_coroutine_threadsafe(handle(event), loop))
    def send(message):
        disk = json.loads(session._path(s.id).read_text(encoding="utf-8"))
        assert not disk["legal_offline"]
        assert len(disk["execution_operations"]) >= 2
        sent.append(message)
        if case == "send-unknown":
            raise OSError("send may have reached provider")
    app._native_transport = SimpleNamespace(peer=lambda _: SimpleNamespace(send=send))
    tui = ResidentTui(app, emit)
    monkeypatch.setattr(tui, "_from_server", lambda: None)
    monkeypatch.setattr(tui, "_down_send", replies.append)
    if case == "replace-failed":
        monkeypatch.setattr(session.os, "replace", lambda *_: (_ for _ in ()).throw(OSError("replace")))
    await asyncio.to_thread(tui._handle, [json.dumps(m) for m in messages])
    async def drain():
        processed = 0
        while processed < len(pending):
            batch = pending[processed:]
            processed = len(pending)
            for future in batch:
                await asyncio.wrap_future(future)
    await drain()
    if case == "late-ack":
        for receipt in receipts:
            app.native_admission_receipt(receipt)
    return s, w, app, tui, sent, events, drain


def command(key):
    return dict(id=key, method="command/exec", params=dict(command=["local", key], processId="same"))


@pytest.mark.parametrize("case", ["ok", "replace-failed", "lost-ack", "late-ack", "owner-switch", "send-unknown"])
def test_command_admission_failure_windows(monkeypatch, case):
    async def run():
        s, w, app, tui, sent, events, drain = await harness(monkeypatch, [command(1)], case)
        aux = [op for op in s.execution_operations.values() if op["kind"] == "command/exec"]
        assert bool(sent) == (case in {"ok", "send-unknown"})
        if case == "replace-failed":
            assert not aux
        elif case in {"lost-ack", "late-ack", "owner-switch"}:
            assert len(aux) == 1 and aux[0]["safeTerminal"]
        else:
            assert len(aux) == 1 and not aux[0]["safeTerminal"]
        assert w._execution_seq == 1
    asyncio.run(run())


@pytest.mark.parametrize("review_first", [False, True])
def test_command_review_concurrent_precise_terminal_and_foreground_preserved(monkeypatch, review_first):
    async def run():
        review = dict(id=3, method="review/start", params=dict(threadId="thread", delivery="detached", target={}))
        s, w, app, tui, sent, events, drain = await harness(monkeypatch, [command(1), command(2), review])
        assert len(sent) == 3
        seq = w._execution_seq
        # Notification before start reply; exact child binding later resolves it.
        terminal = dict(method="turn/completed", params=dict(threadId="child", turn=dict(id="review-turn", status="completed")))
        if review_first:
            tui._aux_notification(terminal)
        tui._aux_response(dict(id=3, result=dict(reviewThreadId="child", turn=dict(id="review-turn", status="inProgress"))))
        await drain()
        if not review_first:
            tui._aux_notification(dict(method="turn/completed", params=dict(threadId="wrong", turn=dict(id="review-turn"))))
            await drain()
            assert not next(op for op in s.execution_operations.values() if op["kind"] == "review/detached")["safeTerminal"]
            tui._aux_notification(terminal)
        # write/terminate responses have other RPC IDs and cannot retire exec.
        tui._aux_response(dict(id=99, result={}))
        tui._aux_response(dict(id=1, result=dict(exitCode=7)))
        await drain()
        await worker._persist_terminal_state(w, s, "error", "429")
        assert not s.legal_offline and not offline.is_illegal(s)
        prior = copy.deepcopy(s.last_result)
        tui._aux_response(dict(id=2, result=dict(exitCode=0)))
        await drain()
        assert s.legal_offline and w._execution_seq == seq and s.last_result == prior
        tui._aux_notification(terminal)
        tui._aux_response(dict(id=2, result=dict(exitCode=0)))
        await drain()
        assert s.last_result == prior
    asyncio.run(run())


def test_review_reply_is_not_completion_and_conflicting_binding_stays_uncertain(monkeypatch):
    async def run():
        review = dict(id=3, method="review/start", params=dict(threadId="thread", delivery="detached", target={}))
        s, w, app, tui, sent, events, drain = await harness(monkeypatch, [review])
        tui._aux_response(dict(id=3, result=dict(reviewThreadId="child", turn=dict(id="r", status="inProgress"))))
        await drain()
        await worker._persist_terminal_state(w, s, "done", "ok")
        assert not s.legal_offline
        op = next(op for op in s.execution_operations.values() if op["kind"] == "review/detached")
        runtime = (w, w.generation, w.process, w.process.stdin)
        await worker._native_operation_event(w, runtime, dict(type="pan.native_operation_bind",
            requestId=op["id"], runId=op["id"], binding=dict(threadId="wrong")))
        tui._aux_notification(dict(method="turn/completed", params=dict(threadId="child", turn=dict(id="r"))))
        await drain()
        assert not s.legal_offline and s.execution_operations[op["id"]]["uncertainty"] == "conflicting-resource-binding"
    asyncio.run(run())


@pytest.mark.skipif(sys.platform != "win32", reason="Windows owned local Codex kernel")
@pytest.mark.parametrize("history_mode", ["legacy", "paginated"])
def test_real_local_codex_command_and_detached_review(tmp_path, monkeypatch, history_mode):
    """Real app-server peer path and synthetic HTTP provider, no remote models."""
    from packages.core.adapters.codex.adapter import _resolve_codex_node, _resolve_codex_js
    from test_codex_native_kernel_real import SyntheticResponses
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("aux_probe", root / "audit/terminal/codex/probe_codex_remote_tui.py")
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    home, cwd = tmp_path / "home", tmp_path / "workspace"
    home.mkdir(); cwd.mkdir()
    helper.seed_placeholder_credentials(str(home), [])
    monkeypatch.setenv("CODEX_HOME", str(home))
    provider = SyntheticResponses()
    options = ["-c", 'model="probe-model"', "-c", 'model_provider="probe_local"', "-c",
        f'model_providers.probe_local={{name="probe",base_url="{provider.base_url}",wire_api="responses"}}']
    app = AppServer(_resolve_codex_node(), _resolve_codex_js(), str(cwd), options,
                    native_control=True, native_passthrough=True)
    original_request = app._request
    def request(method, params):
        if method == "initialize":
            params = {**params, "capabilities": {"experimentalApi": True}}
        return original_request(method, params)
    monkeypatch.setattr(app, "_request", request)
    original_params = app._thread_params
    monkeypatch.setattr(app, "_thread_params", lambda: {**original_params(), "historyMode": history_mode})
    async def run():
        await asyncio.to_thread(app.start)
        s, w = setup()
        s.cli_session_id = app.thread_id
        w.adapter = SimpleNamespace(name="codex")
        w.process = app.process
        w.native_control_mode, w.native_control_generation = "native", 1
        runtime = (w, w.generation, w.process, None)
        app.control_owner.switch("native", 0)
        incoming, replies, pending, events = Queue(), Queue(), [], []
        loop = asyncio.get_running_loop()
        async def handle(event):
            if event["type"] == "pan.native_turn_admission":
                receipt = await worker._admit_native_turn(w, runtime, request_id=event["requestId"],
                    thread_id=event["threadId"], control_generation=event["controlGeneration"],
                    operation=event["operation"], context=event.get("connection"))
                disk = json.loads(session._path(s.id).read_text(encoding="utf-8"))
                assert not disk["legal_offline"]
                app.native_admission_receipt({**receipt, "ok": True})
            else:
                await worker._native_operation_event(w, runtime, event)
        def emit(event):
            events.append(event)
            if event["type"] in {"pan.native_turn_admission", "pan.native_turn_not_forwarded",
                                "pan.native_operation_bind", "pan.native_operation_terminal",
                                "pan.native_operation_uncertain"}:
                pending.append(asyncio.run_coroutine_threadsafe(handle(event), loop))
        class Down:
            def __iter__(self):
                while (message := incoming.get()) is not None:
                    yield json.dumps(message)
            def send(self, raw):
                replies.put(json.loads(raw))
        tui = ResidentTui(app, emit)
        app._resident_tui = tui
        connection = threading.Thread(target=tui._handle, args=(Down(),), daemon=True)
        connection.start()
        async def reply(key):
            while True:
                message = await asyncio.to_thread(replies.get, True, 15)
                if message.get("id") == key:
                    return message
        try:
            incoming.put(dict(id=1, method="initialize", params=dict(clientInfo=dict(name="pan-local-proof", version="1"), capabilities={})))
            assert "result" in await reply(1)
            incoming.put(dict(method="initialized", params={}))
            marker = cwd / "command.txt"
            incoming.put(dict(id=2, method="command/exec", params=dict(command=[sys.executable, "-c",
                "from pathlib import Path; Path('command.txt').write_text('local')"], cwd=str(cwd))))
            response = await reply(2)
            assert response.get("result", {}).get("exitCode") == 0, response
            for future in list(pending):
                await asyncio.wrap_future(future)
            assert marker.read_text() == "local" and s.legal_offline
            assert w._execution_seq == s.task_seq == 0
            def durable_before_model():
                disk = json.loads(session._path(s.id).read_text(encoding="utf-8"))
                assert not disk["legal_offline"]
                assert any(op["kind"] == "review/detached" and not op["safeTerminal"]
                           for op in disk["execution_operations"].values())
            provider.on_request = durable_before_model
            incoming.put(dict(id=3, method="review/start", params=dict(threadId=app.thread_id,
                delivery="detached", target=dict(type="custom", instructions="Reply with a short local synthetic review."))))
            response = await reply(3)
            if history_mode == "paginated":
                assert response.get("error", {}).get("message") == "paginated threads do not support detached review"
                for future in list(pending):
                    await asyncio.wrap_future(future)
                assert s.legal_offline and not provider.received.is_set()
                return
            assert "result" in response, response
            assert await asyncio.to_thread(provider.received.wait, 15)
            assert not s.legal_offline
            provider.release.set()
            # The receiving peer or primary observer must deliver the exact child terminal.
            for _ in range(200):
                if any(e.get("proof") == "review-turn-completed" for e in events):
                    break
                await asyncio.sleep(.05)
            for future in list(pending):
                await asyncio.wrap_future(future)
            assert s.legal_offline, [(e.get("type"), e.get("reason")) for e in events]
            review = next(op for op in s.execution_operations.values() if op["kind"] == "review/detached")
            assert review["binding"]["threadId"] == response["result"]["reviewThreadId"]
            assert review["binding"]["threadId"] != app.thread_id
            assert not provider.failures
        finally:
            tui.stopped.set()
            incoming.put(None)
            if tui.peer:
                await asyncio.to_thread(tui.peer.close)
            await asyncio.to_thread(connection.join, 3)
            assert not connection.is_alive()
            app._resident_tui = None
    try:
        asyncio.run(run())
    finally:
        provider.close()
        app.close()
