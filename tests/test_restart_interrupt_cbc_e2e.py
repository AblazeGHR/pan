"""End-to-end process tests for restart/interrupt on the cbc stream path."""

import asyncio
import os
import sys
from pathlib import Path

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import session as _sess
from packages.core import worker
from packages.core.adapters import CbcAdapter


FAKE_CBC = r'''
import json, os, sys, time
fast_marker = os.environ["PAN_FAKE_CBC_FAST"]
print(json.dumps({"type":"system", "subtype":"init", "session_id":"cbc-e2e", "model":"test"}), flush=True)
for line in sys.stdin:
    msg = json.loads(line)
    if msg.get("type") != "user":
        continue
    if not os.path.exists(fast_marker):
        time.sleep(30)
    print(json.dumps({"type":"result", "result":"restarted task completed", "is_error":False}), flush=True)
'''


def test_cbc_interrupt_kills_old_process_and_recovers_task(tmp_path, monkeypatch):
    """A running cbc task must be interrupted and replayed by the new process."""
    worker.workers.clear()
    worker._task_status.clear()
    worker._inflight_task_ids.clear()
    _sess._cache.clear()
    worker.set_broadcaster(None)

    fake = tmp_path / "fake_cbc.py"
    fast_marker = tmp_path / "fast"
    fake.write_text(FAKE_CBC, encoding="utf-8")
    monkeypatch.setattr(CbcAdapter, "_resolve_cbc_argv", lambda self: [sys.executable, str(fake)])
    monkeypatch.setenv("PAN_FAKE_CBC_FAST", str(fast_marker))
    monkeypatch.setattr(worker, "_DEFAULTS_INITIALIZED", True)

    s = _sess.Session(id="ses-cbc-e2e", name="cbc-e2e", adapter="cbc",
                      model="test-model", workdir=str(tmp_path))
    _sess._cache[s.id] = s

    async def save_noop(_session):
        return None

    async def scenario():
        original_save = _sess.save_async
        _sess.save_async = save_noop
        try:
            w = await worker.create_worker(s.id)
            assert not isinstance(w, str)
            result = await worker.assign(s.id, "long running task", task_id="e2e-task")
            assert result["status"] == "queued"
            for _ in range(100):
                if w.status == "running":
                    break
                await asyncio.sleep(0.01)
            assert w.status == "running"
            old_pid = w.process.pid

            fast_marker.write_text("1", encoding="ascii")
            assert await worker.interrupt_worker(w.worker_id) is None
            replacement = worker.get_worker(w.worker_id)
            assert replacement is w
            assert replacement.process is not None
            assert replacement.process.pid != old_pid
            assert not psutil.pid_exists(old_pid), "interrupt left the old cbc process alive"

            for _ in range(200):
                if s.last_result and s.last_result.get("result") == "restarted task completed":
                    break
                await asyncio.sleep(0.01)
            assert s.last_result and s.last_result["status"] == "done", (
                f"status={w.status}, queue={s.queue_pending}, "
                f"signals={w.pending_signal.qsize()}, returncode={w.process.returncode}, "
                f"cmdline={psutil.Process(w.process.pid).cmdline() if psutil.pid_exists(w.process.pid) else 'dead'}"
            )
            assert not s.queue_pending
            assert not worker._inflight_task_ids
        finally:
            w = worker.find_worker_by_session(s.id)
            if w:
                proc = w.process
                await worker.kill_worker(w.worker_id)
                if proc is not None:
                    await proc.wait()
                    transport = getattr(proc, "_transport", None)
                    if transport is not None:
                        transport.close()
            _sess.save_async = original_save

    asyncio.run(scenario())
    worker.workers.clear()
    _sess._cache.clear()
