"""Completion publication precedes OS thread teardown; reuse must honor it."""
import threading

import pytest

from packages.core.terminal.runner import _TrackedCall, TerminalRunner


def _runner(write):
    runner = TerminalRunner.__new__(TerminalRunner)
    runner._runtime = object()
    runner._worker_lock = threading.Lock()
    runner._closing = False
    runner._runner_state = "running"
    runner._write_worker = None
    runner._write_hook = write
    runner._input_ack_wait = 2
    runner._write_completed = 0
    runner._payload = lambda status, *, ok, extra=None: {
        "status": status, "ok": ok, **(extra or {}),
    }
    return runner


def test_next_input_owns_new_call_before_previous_thread_teardown(monkeypatch):
    release = threading.Event()
    exiting = threading.Event()
    original_run = threading.Thread.run

    def delayed_teardown(thread):
        original_run(thread)
        if thread.name == "runner-tracked-call":
            exiting.set()
            assert release.wait(5)

    monkeypatch.setattr(threading.Thread, "run", delayed_teardown)
    writes = []
    runner = _runner(lambda data: writes.append(data) or len(data))
    old_thread = None
    try:
        assert runner.input(b"first") == {"status": "done", "ok": True, "size": 5}
        assert exiting.wait(2)
        old_call = runner._write_worker
        old_thread = old_call._worker
        assert old_thread.is_alive()
        assert not old_call.in_flight
        assert runner.input(b"second") == {"status": "done", "ok": True, "size": 6}
        assert runner._write_worker is not old_call
        assert old_call.result == ("done", 5)
        assert writes == [b"first", b"second"]
    finally:
        release.set()
        if old_thread is not None:
            old_thread.join(2)
        if runner._write_worker is not None:
            runner._write_worker._worker.join(2)


def test_in_flight_call_cannot_reset_or_duplicate_write():
    entered = threading.Event()
    release = threading.Event()
    writes = []

    def write():
        writes.append("one")
        entered.set()
        assert release.wait(5)
        return 3

    call = _TrackedCall(write)
    try:
        assert call.run(0) == (False, None, None)
        assert entered.wait(2)
        with pytest.raises(RuntimeError, match="still running"):
            call.reset(lambda: writes.append("duplicate"))
        assert call.run(0) == (False, None, None)
        assert writes == ["one"]
        release.set()
        assert call.run(2) == (True, 3, None)
    finally:
        release.set()
        call._worker.join(2)


def test_input_reservation_rejects_second_request_before_thread_start(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    writes = []
    results = []
    original_run = _TrackedCall.run

    def delayed_start(call, timeout):
        if not entered.is_set():
            entered.set()
            assert release.wait(5)
        return original_run(call, timeout)

    monkeypatch.setattr(_TrackedCall, "run", delayed_start)
    runner = _runner(lambda data: writes.append(data) or len(data))
    first = threading.Thread(target=lambda: results.append(runner.input(b"original")))
    first.start()
    try:
        assert entered.wait(2)
        assert runner.input(b"duplicate") == {"status": "busy", "ok": False}
        assert writes == []
        release.set()
        first.join(3)
        assert not first.is_alive()
        assert results == [{"status": "done", "ok": True, "size": 8}]
        assert writes == [b"original"]
    finally:
        release.set()
        first.join(3)
