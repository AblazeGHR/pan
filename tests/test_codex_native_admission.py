"""Private native admission: durable JSON precedes any provider send attempt."""
import asyncio
import io
import json
import threading
from queue import Queue
from types import SimpleNamespace

import pytest

from packages.core import worker, session
from packages.core.adapters.codex.app_server_wrapper import AppServer, _read_pan_stdin
from packages.core.adapters.codex.native_admission import NativeAdmission
from packages.core.adapters.codex.resident_tui import ResidentTui
from test_legal_offline import setup, runtime_registry


@pytest.mark.parametrize("operation", ["turn/start", "thread/compact/start"])
@pytest.mark.parametrize("case", ["accepted", "replace-failed", "ack-lost", "late-ack", "owner-switch", "thread-switch", "send-unknown", "provider-rejected", "rejection-conflict"])
def test_proxy_requires_durable_ack_and_preserves_unknown(monkeypatch, case, operation):
    async def run():
        s, w = setup()
        w.adapter = SimpleNamespace(name="codex")
        s.cli_session_id = "thread"
        w.native_control_mode, w.native_control_generation = "native", 1
        runtime = (w, w.generation, w.process, w.process.stdin)
        app = AppServer("node", "codex.js", ".", [], native_control=True)
        app.thread_id = "thread"
        app.process = SimpleNamespace(poll=lambda: None)
        app.control_owner.switch("native", 0)
        app.native_admission_timeout = .05 if case in {"ack-lost", "late-ack"} else 2
        events, sent, replies, futures = [], [], [], []
        ack = {}
        if case == "replace-failed":
            def fail(*_):
                raise OSError("injected replace failure")
            monkeypatch.setattr(session.os, "replace", fail)
        loop = asyncio.get_running_loop()
        async def admit(event):
            try:
                receipt = await worker._admit_native_turn(w, runtime, request_id=event["requestId"],
                    thread_id=event["threadId"], control_generation=event["controlGeneration"], operation=event["operation"])
                ack.update(receipt, ok=True)
            except OSError:
                ack.update(requestId=event["requestId"], ok=False)
            if case == "owner-switch":
                app.control_owner.switch("pan", 1)
            if case == "thread-switch":
                app.thread_id = "other"
            if case not in {"ack-lost", "late-ack"}:
                app.native_admission_receipt(ack)
        def emit(event):
            events.append(event)
            if event["type"] == "pan.native_turn_admission":
                assert sent == []
                futures.append(asyncio.run_coroutine_threadsafe(admit(event), loop))
            elif event["type"] == "pan.native_turn_not_forwarded":
                futures.append(asyncio.run_coroutine_threadsafe(worker._retire_native_admission(w, runtime, event), loop))
        def send(message):
            disk = json.loads(session._path(s.id).read_text(encoding="utf-8"))
            assert disk["legal_offline"] is False
            assert disk["offline_run"]["nativeAdmissionId"] == app._native_admission.id
            assert disk["offline_run"]["id"] == app._native_admission.run_id
            assert disk["task_seq"] == 1
            sent.append(message)
            if case == "send-unknown":
                raise OSError("may have reached provider")
        app._native_transport = SimpleNamespace(peer=lambda _: SimpleNamespace(send=send))
        tui = ResidentTui(app, emit)
        monkeypatch.setattr(tui, "_from_server", lambda: None)
        monkeypatch.setattr(tui, "_down_send", replies.append)
        message = {"id": 7, "method": operation, "params": {"threadId": "thread", "input": []}}
        await asyncio.to_thread(tui._handle, [json.dumps(message)])
        for future in list(futures):
            await asyncio.wrap_future(future)
        await asyncio.sleep(0)
        for future in list(futures):
            await asyncio.wrap_future(future)
        if case == "late-ack":
            app.native_admission_receipt(ack)
        if case in {"accepted", "send-unknown", "provider-rejected", "rejection-conflict"}:
            assert sent == [message] and not s.legal_offline
            run = dict(s.offline_run)
            assert w._execution_seq == s.task_seq == 1
            again = await worker._admit_native_turn(w, runtime, request_id=ack["requestId"],
                thread_id="thread", control_generation=1)
            assert again["runId"] == run["id"] and w._execution_seq == 1
            if case in {"provider-rejected", "rejection-conflict"}:
                value = app._native_admission
                if case == "rejection-conflict":
                    assert value.started("turn")
                    assert not value.rejected()
                    assert not s.legal_offline
                else:
                    assert value.rejected()
                    await asyncio.wrap_future(futures[-1])
                    assert s.legal_offline
            elif case == "send-unknown":
                assert app._native_admission.state == "unknown"
                assert not app._native_admission.cancel()
        else:
            assert sent == [] and replies
            assert s.legal_offline
            if case == "replace-failed":
                assert s.task_seq == w._execution_seq == 0
                assert s.offline_run is None
    asyncio.run(run())


def test_stdin_routes_ack_while_control_callback_is_blocked(monkeypatch):
    entered, release, acked = threading.Event(), threading.Event(), threading.Event()
    def blocked(message):
        entered.set()
        assert release.wait(2)
    stream = io.StringIO('{"type":"native_input"}\n{"type":"native_admission","requestId":"a"}\n')
    monkeypatch.setattr("sys.stdin", stream)
    task, controls = Queue(), Queue()
    _read_pan_stdin(task, controls, native_handler=blocked, admission_handler=lambda _: acked.set())
    assert entered.wait(1) and acked.is_set()
    release.set()


def test_single_transition_late_ack_cancel_and_unknown():
    emitted = []
    value = NativeAdmission("thread", 1, emitted.append)
    assert value.cancel()
    value.acknowledge({"ok": True, "runId": "late"})
    assert value.state == "cancelled" and value.run_id is None
    assert not value.cancel()
    other = NativeAdmission("thread", 1, emitted.append)
    other.acknowledge({"ok": True, "runId": "run"})
    def unknown():
        raise OSError("attempted")
    with pytest.raises(OSError):
        other.send(unknown)
    assert other.state == "unknown" and not other.cancel()


def test_same_thread_concurrent_reservation_only_one_writer():
    app = AppServer("node", "codex.js", ".", [], native_control=True)
    app.thread_id = "thread"
    first = app.reserve_native_admission("thread", 1, lambda _: None)
    with pytest.raises(RuntimeError, match="already executing"):
        app.reserve_native_admission("thread", 1, lambda _: None)
    assert app._native_admission is first


def test_restarted_runtime_rejects_ack_and_old_cancellation():
    async def run():
        s, w = setup()
        w.adapter = SimpleNamespace(name="codex")
        s.cli_session_id = "thread"
        w.native_control_mode, w.native_control_generation = "native", 1
        runtime = (w, w.generation, w.process, w.process.stdin)
        receipt = await worker._admit_native_turn(w, runtime, request_id="old", thread_id="thread", control_generation=1)
        worker._bump_worker_generation(w)
        with pytest.raises(RuntimeError, match="retired"):
            await worker._admit_native_turn(w, runtime, request_id="new", thread_id="thread", control_generation=1)
        assert not await worker._retire_native_admission(w, runtime, receipt)
        assert not s.legal_offline
    asyncio.run(run())


def test_provider_late_turn_completed_cannot_finish_current_admission():
    app = AppServer("node", "codex.js", ".", [], native_control=True)
    app.thread_id = "thread"
    value = NativeAdmission("thread", 1, lambda _: None)
    value.acknowledge({"ok": True, "runId": "run"})
    value.send(lambda: None)
    assert value.started("current")
    state = {"admission": value, "external_turn": True, "done": False}
    for thread, turn in [("other", "current"), ("thread", "old")]:
        app._handle_server_message({"method": "turn/completed", "params": {
            "threadId": thread, "turn": {"id": turn, "status": "completed"}}}, state)
        assert not state["done"]
    app._handle_server_message({"method": "turn/completed", "params": {
        "threadId": "thread", "turn": {"id": "current", "status": "completed"}}}, state)
    assert state["done"]


def test_busy_reservation_cannot_clear_or_duplicate_durable_execution():
    async def run():
        s, w = setup()
        w.adapter = SimpleNamespace(name="codex")
        s.cli_session_id = "thread"
        w.native_control_mode, w.native_control_generation = "native", 1
        w._native_reservation_busy = True
        runtime = (w, w.generation, w.process, w.process.stdin)
        await worker._admit_native_turn(w, runtime, request_id="one", thread_id="thread", control_generation=1)
        assert not w._native_reservation_busy and w._native_external_turn_active
        with pytest.raises(RuntimeError, match="conflicts"):
            await worker._admit_native_turn(w, runtime, request_id="two", thread_id="thread", control_generation=1)
        assert not s.legal_offline and s.task_seq == 1
    asyncio.run(run())


def test_writer_waiting_during_restart_never_commits_or_acknowledges_successor(monkeypatch):
    async def run():
        s, w = setup()
        w.adapter = SimpleNamespace(name="codex")
        s.cli_session_id = "thread"
        w.native_control_mode, w.native_control_generation = "native", 1
        runtime = (w, w.generation, w.process, w.process.stdin)
        entered, release = threading.Event(), threading.Event()
        def preceding():
            entered.set()
            assert release.wait(3)
        ticket = asyncio.create_task(session._persist_async_outcome(s.id, preceding))
        assert await asyncio.to_thread(entered.wait, 2)
        pending = asyncio.create_task(worker._admit_native_turn(w, runtime, request_id="retiring",
            thread_id="thread", control_generation=1))
        await asyncio.sleep(0)
        worker._bump_worker_generation(w)
        release.set()
        await ticket
        with pytest.raises(RuntimeError, match="changed"):
            await pending
        assert s.legal_offline and s.task_seq == w._execution_seq == 0
        assert s.offline_run is None
    asyncio.run(run())


def test_waiting_admission_cancellation_cannot_legalize_another_run():
    async def run():
        s, w = setup()
        w.adapter = SimpleNamespace(name="codex")
        s.cli_session_id = "thread"
        w.native_control_mode, w.native_control_generation = "native", 1
        runtime = (w, w.generation, w.process, w.process.stdin)
        first = await worker._admit_native_turn(w, runtime, request_id="first", thread_id="thread", control_generation=1)
        assert await worker._retire_native_admission(w, runtime, first)
        second = await worker._admit_native_turn(w, runtime, request_id="second", thread_id="thread", control_generation=1)
        assert not await worker._retire_native_admission(w, runtime, first)
        assert not s.legal_offline and s.offline_run["id"] == second["runId"]
    asyncio.run(run())


def test_passthrough_observed_execution_is_explicitly_not_pre_admitted():
    async def run():
        s, w = setup()
        w.adapter = SimpleNamespace(name="codex")
        s.cli_session_id = "thread"
        w.native_control_mode, w.native_control_generation = "native", 1
        runtime = (w, w.generation, w.process, w.process.stdin)
        await worker._admit_native_turn(w, runtime, request_id="observed", thread_id="thread", control_generation=1,
                                       operation="observed-turn")
        assert s.offline_run["nativeAdmissionTiming"] == "after-start"
        assert not s.legal_offline
    asyncio.run(run())


def test_stdin_ack_bypasses_blocked_controls_but_tasks_keep_order(monkeypatch):
    entered, release, acked = threading.Event(), threading.Event(), threading.Event()
    order = []
    def blocked(message):
        entered.set()
        assert release.wait(2)
        order.append("mode")
    stream = io.StringIO('{"type":"control_mode"}\n{"text":"next"}\n{"type":"native_admission"}\n')
    monkeypatch.setattr("sys.stdin", stream)
    task, controls = Queue(), Queue()
    _read_pan_stdin(task, controls, mode_handler=blocked, admission_handler=lambda _: acked.set())
    assert entered.wait(1) and acked.is_set() and task.empty()
    release.set()
    assert task.get(timeout=2) == {"text": "next"}
    assert order == ["mode"]
    assert task.get(timeout=2) is None


def test_late_not_forwarded_cannot_retire_already_illegal_execution():
    async def run():
        s, w = setup()
        w.adapter = SimpleNamespace(name="codex")
        s.cli_session_id = "thread"
        w.native_control_mode, w.native_control_generation = "native", 1
        runtime = (w, w.generation, w.process, w.process.stdin)
        receipt = await worker._admit_native_turn(w, runtime, request_id="late", thread_id="thread", control_generation=1)
        s.offline_run["state"] = "illegal"
        w.status = "error"
        assert not await worker._retire_native_admission(w, runtime, receipt)
        assert not s.legal_offline and w.status == "error"
    asyncio.run(run())


def test_active_turn_compaction_keeps_existing_forwarding_semantics(monkeypatch):
    app = AppServer("node", "codex.js", ".", [], native_control=True)
    app.thread_id = "thread"
    app.process = SimpleNamespace(poll=lambda: None)
    app.control_owner.switch("native", 0)
    app._turn_active.set()
    sent, events = [], []
    app._native_transport = SimpleNamespace(peer=lambda _: SimpleNamespace(send=sent.append))
    tui = ResidentTui(app, events.append)
    monkeypatch.setattr(tui, "_from_server", lambda: None)
    message = {"id": 1, "method": "thread/compact/start", "params": {"threadId": "thread"}}
    tui._handle([json.dumps(message)])
    assert sent == [message]
    assert not any(e["type"] == "pan.native_turn_admission" for e in events)


@pytest.mark.parametrize("already_started", [False, True])
def test_provider_rpc_rejection_uses_correlated_response_without_erasing_started(already_started):
    app = AppServer("node", "codex.js", ".", [], native_control=True)
    app.thread_id = "thread"
    events, replies = [], []
    value = app.reserve_native_admission("thread", 1, events.append)
    value.acknowledge({"ok": True, "runId": "run"})
    value.send(lambda: None)
    if already_started:
        value.started("turn")
    tui = ResidentTui(app, events.append)
    tui._native_starts[7] = value
    tui.peer = SimpleNamespace(messages=lambda: iter([{"id": 7, "error": {"code": -1}}]))
    tui._down_send = replies.append
    tui._from_server()
    assert replies == [{"id": 7, "error": {"code": -1}}]
    if already_started:
        assert value.state == "started" and app._native_turn_pending.is_set()
        assert not any(e["type"] == "pan.native_turn_not_forwarded" for e in events)
    else:
        assert value.state == "rejected" and not app._native_turn_pending.is_set()
        refusal = next(e for e in events if e["type"] == "pan.native_turn_not_forwarded")
        assert refusal["requestId"] == value.id and refusal["runId"] == "run"
