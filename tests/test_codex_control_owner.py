import asyncio
import json
import threading
from functools import wraps
from types import SimpleNamespace

import pytest

from packages.core.adapters.codex.control_owner import ControlOwner, ControlRejected
from packages.core.adapters.codex.app_server_wrapper import AppServer
from packages.core.adapters.codex.adapter import CodexAdapter
from packages.core import worker
from packages.core.session import Session


def run_async(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))
    return run


def test_mode_generation_and_stale_writes():
    owner = ControlOwner()
    writes = []
    owner.write("pan", lambda: writes.append("first"), generation=0)
    assert owner.switch("native", 0) == {"mode": "native", "generation": 1}
    with pytest.raises(ControlRejected):
        owner.write("pan", lambda: writes.append("wrong"))
    with pytest.raises(ControlRejected):
        owner.switch("pan", 0)
    owner.write("native", lambda: writes.append("native"), generation=1)
    assert owner.switch("pan", 1)["generation"] == 2
    with pytest.raises(ControlRejected):
        owner.write("native", lambda: writes.append("late"), generation=1)
    assert writes == ["first", "native"]


def test_switch_waits_for_admitted_write_without_stopping_execution():
    owner = ControlOwner()
    entered, release, switched = threading.Event(), threading.Event(), threading.Event()
    def operation():
        entered.set()
        assert release.wait(2)
    writer = threading.Thread(target=lambda: owner.write("pan", operation))
    switcher = threading.Thread(target=lambda: (owner.switch("native", 0), switched.set()))
    writer.start()
    assert entered.wait(1)
    switcher.start()
    try:
        assert not switched.wait(.05)
    finally:
        release.set()
        writer.join(2)
        switcher.join(2)
    assert switched.is_set()
    assert not writer.is_alive() and not switcher.is_alive()


@pytest.mark.parametrize("value", [True, 1.0, "0", None])
def test_generation_is_not_coerced(value):
    with pytest.raises(ControlRejected):
        ControlOwner().switch("native", value)


def test_bridge_native_mode_keeps_read_events_but_fences_pan_writes():
    app = AppServer("node", "codex.js", ".", [], native_control=True)
    sent = []
    app._native_transport = SimpleNamespace(send=sent.append)
    app.control_owner.switch("native", 0)
    app._send({"id": 1, "method": "thread/read", "params": {}})
    for message in [{"method": "turn/start"}, {"method": "turn/interrupt"},
                    {"id": 4, "result": {"decision": "accept"}}, {"method": "unknown/newMutation"}]:
        with pytest.raises(ControlRejected):
            app._send(message)
    assert len(sent) == 1
    state = {"pending_requests": {"1": {"id": 1, "deadline": 0}}}
    app._safe_decline_expired(state)
    assert "1" in state["pending_requests"]


def runtime(monkeypatch):
    s = Session(id="ses_native_control_test", name="native", adapter="codex")
    s.adapter_config["native_control"] = True
    s.queue_pending = [{"type": "task", "text": "keep me", "seq": 1}]
    w = worker.Worker("worker-native-test", s.id, CodexAdapter())
    w.pending_signal = asyncio.Queue()
    monkeypatch.setattr(worker, "workers", {w.worker_id: w})
    monkeypatch.setattr(worker, "_workers_by_session", {s.id: w})
    monkeypatch.setattr(worker, "_session", lambda _: s)
    return s, w


@run_async
async def test_worker_switch_preserves_queue_and_running_state(monkeypatch):
    s, w = runtime(monkeypatch)
    w.status = "running"
    owner = ControlOwner()
    wires = []
    class Stdin:
        def write(self, raw):
            message = json.loads(raw)
            wires.append(message)
            receipt = owner.switch(message["mode"], message["generation"])
            worker._update_pending_interactions(w, {"type": "pan.control_mode",
                "requestId": message["requestId"], "ok": True, **receipt})
        async def drain(self):
            pass
    process = SimpleNamespace(stdin=Stdin(), returncode=None)
    w.process = process
    original = json.dumps(s.queue_pending)
    assert (await worker.switch_native_control(w.worker_id, "native"))["ok"] is True
    assert worker._native_handoff_blocked(s)
    assert await worker.send_control_message(w.worker_id, {"type": "interrupt"}) is not None
    executed = []
    async def handoff():
        executed.append(1)
    with pytest.raises(worker._QueuePausedBeforeHandoff):
        await worker._queue_handoff(w, s, handoff, None)
    # Native events still update replay state; no consumer task is cancelled.
    worker._update_pending_interactions(w, {"type": "codex.thread_status", "native_status": {"type": "active"}})
    assert w.native_status == {"type": "active"}
    assert (await worker.switch_native_control(w.worker_id, "pan"))["ok"] is True
    await worker._queue_handoff(w, s, handoff, None)
    assert executed == [1]
    assert w.status == "running" and w.process is process
    assert json.dumps(s.queue_pending) == original
    assert s.queue_paused is False
    assert w.pending_signal.get_nowait() == {"type": "queue_signal"}
    assert [m["type"] for m in wires] == ["control_mode", "control_mode"]


@run_async
async def test_lost_ack_keeps_gate_and_retry_reconciles_generation(monkeypatch):
    s, w = runtime(monkeypatch)
    owner = ControlOwner()
    emit = False
    class Stdin:
        def write(self, raw):
            message = json.loads(raw)
            try:
                snapshot = owner.switch(message["mode"], message["generation"])
                ok = True
            except ControlRejected:
                snapshot, ok = owner.snapshot(), False
            if emit:
                worker._update_pending_interactions(w, {"type": "pan.control_mode",
                    "requestId": message["requestId"], "ok": ok, **snapshot})
        async def drain(self):
            pass
    w.process = SimpleNamespace(stdin=Stdin(), returncode=None)
    assert (await worker.switch_native_control(w.worker_id, "native", timeout=.02))["ok"] is False
    assert w.native_control_mode == "switching" and worker._native_handoff_blocked(s)
    assert not w._native_mode_receipts
    emit = True
    # First retry learns authoritative native generation; no false success.
    receipt = await worker.switch_native_control(w.worker_id, "pan")
    assert receipt["ok"] is False and w.native_control_mode == "native"
    assert w.native_control_generation == 1
    assert (await worker.switch_native_control(w.worker_id, "pan"))["ok"] is True
    assert not worker._native_handoff_blocked(s)


@run_async
async def test_user_queue_pause_survives_native_round_trip(monkeypatch):
    s, w = runtime(monkeypatch)
    s.queue_paused = True
    w.native_control_mode = "native"
    w.native_control_mode = "pan"
    with pytest.raises(worker._QueuePausedBeforeHandoff):
        await worker._queue_handoff(w, s, lambda: None, None)
    assert s.queue_paused is True


@run_async
async def test_cancelled_switch_releases_lock_but_not_uncertain_gate(monkeypatch):
    s, w = runtime(monkeypatch)
    sent = asyncio.Event()
    class Stdin:
        def write(self, raw):
            sent.set()
        async def drain(self):
            pass
    w.process = SimpleNamespace(stdin=Stdin(), returncode=None)
    task = asyncio.create_task(worker.switch_native_control(w.worker_id, "native"))
    await sent.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert worker._native_handoff_blocked(s)
    assert not w._native_mode_lock.locked()
    assert not w._native_mode_receipts
