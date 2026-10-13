import asyncio
import json
import threading
from unittest.mock import AsyncMock
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


@run_async
async def test_restarted_native_kernel_uses_fresh_owner_and_discards_old_receipts(monkeypatch):
    s, w = runtime(monkeypatch)
    w.native_control_mode = "native"
    w.native_control_generation = 5
    w.native_kernel_ready = True
    w.native_output.extend(b"old-screen")
    w.native_output_total = 10
    old = asyncio.get_running_loop().create_future()
    w._native_mode_receipts["old"] = old
    worker.clear_native_runtime_state(w)
    assert old.result()["ok"] is False
    assert w._native_mode_receipts == {}
    assert w.native_control_mode == "pan" and w.native_control_generation == 0
    assert not w.native_kernel_ready
    assert w.native_output == b"" and w.native_output_total == 0
    owner = ControlOwner()
    class Stdin:
        def write(self, raw):
            message = json.loads(raw)
            receipt = owner.switch(message["mode"], message["generation"])
            worker._update_pending_interactions(w, {"type": "pan.control_mode",
                "requestId": message["requestId"], "ok": True, **receipt})
        async def drain(self): pass
    w.process = SimpleNamespace(stdin=Stdin(), returncode=None)
    assert (await worker.switch_native_control(w.worker_id, "native"))["ok"] is True
    assert w.native_control_mode == "native" and w.native_control_generation == 1
    assert s.queue_pending[0]["text"] == "keep me"


@run_async
async def test_restart_during_handoff_cannot_mark_replacement_unknown(monkeypatch):
    _, w = runtime(monkeypatch)
    sent = asyncio.Event()
    class Stdin:
        def write(self, raw): sent.set()
        async def drain(self): pass
    w.process = SimpleNamespace(stdin=Stdin(), returncode=None)
    task = asyncio.create_task(worker.switch_native_control(w.worker_id, "native"))
    await sent.wait()
    worker._bump_worker_generation(w)
    worker.clear_native_runtime_state(w)
    assert (await task)["ok"] is False
    assert w.native_control_mode == "pan" and w.native_control_error is None


@run_async
async def test_real_restart_entry_resets_reused_worker_before_new_process(monkeypatch):
    _, w = runtime(monkeypatch)
    w.native_control_mode = "native"
    w.native_control_generation = 8
    w.native_kernel_ready = True
    w.native_output.extend(b"retired-screen")
    w.native_output_total = 14
    for name in ("_stop_worker_tasks", "_kill_takeover_terminal", "_kill_process_tree",
                 "_record_legal_worker_state", "_restart_tasks", "_bcast"):
        monkeypatch.setattr(worker, name, AsyncMock())
    monkeypatch.setattr(worker, "_runtime_stopped", lambda _: True)
    monkeypatch.setattr(worker, "resolve_execution_mode", lambda *args: "stream")
    new_process = SimpleNamespace(returncode=None)
    async def spawn(*args, **kwargs):
        assert w.native_control_generation == 0 and w.native_control_mode == "pan"
        assert not w.native_kernel_ready and not w.native_output
        return new_process
    monkeypatch.setattr(worker, "_spawn_process", spawn)
    assert await worker._restart_worker_unlocked(w.worker_id) is None
    assert worker.workers[w.worker_id] is w and w.process is new_process


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
async def test_late_receipt_recovers_only_the_latest_issued_handoff(monkeypatch):
    s, w = runtime(monkeypatch)
    # This case requires an issued handoff before simulating a lost receipt.
    # Drive that timeout explicitly: a loaded runner can exhaust the 10ms
    # wall-clock budget before stdin.write, which tests a different boundary.
    async def receipt_timeout(awaitable, timeout):
        if asyncio.isfuture(awaitable):
            assert messages, "receipt timeout must follow an issued request"
            awaitable.cancel()  # Cancel the shield, not its underlying receipt.
            raise asyncio.TimeoutError
        return await awaitable
    monkeypatch.setattr(worker.asyncio, "wait_for", receipt_timeout)
    messages = []
    class Stdin:
        def write(self, raw):
            messages.append(json.loads(raw))
        async def drain(self):
            pass
    w.process = SimpleNamespace(stdin=Stdin(), returncode=None)
    assert (await worker.switch_native_control(w.worker_id, 'native', timeout=.01))['ok'] is False
    first = messages[-1]['requestId']
    assert w.native_control_mode == 'switching'
    worker._update_pending_interactions(w, {'type': 'pan.control_mode', 'requestId': first,
        'ok': False, 'mode': 'pan', 'generation': 0})
    assert w.native_control_mode == 'pan' and not worker._native_handoff_blocked(s)
    assert w.native_control_error == 'Native control request rejected'
    assert (await worker.switch_native_control(w.worker_id, 'native', timeout=.01))['ok'] is False
    second = messages[-1]['requestId']
    worker._update_pending_interactions(w, {'type': 'pan.control_mode', 'requestId': first,
        'ok': True, 'mode': 'pan', 'generation': 1})
    assert w.native_control_mode == 'switching'  # Superseded receipt cannot reopen input.
    worker._update_pending_interactions(w, {'type': 'pan.control_mode', 'requestId': second,
        'ok': True, 'mode': 'native', 'generation': 2})
    assert w.native_control_mode == 'native' and w.native_control_generation == 2
    assert w.native_control_error is None
    worker._update_pending_interactions(w, {'type': 'pan.control_mode', 'requestId': second,
        'ok': True, 'mode': 'pan', 'generation': 1})
    assert w.native_control_mode == 'native' and w.native_control_generation == 2
    assert s.queue_pending == [{'type': 'task', 'text': 'keep me', 'seq': 1}]


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
