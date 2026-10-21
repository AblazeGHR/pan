"""Bounded runtime evidence, independent of Session stores and their executors.

No request bodies, query strings, provider text, frame locals or credentials
are collected. A daemon writes records; request/Worker paths only update small
in-memory structures. This observes stalls and never restarts a Worker.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import Future
from collections import deque
from contextlib import asynccontextmanager
import contextvars
import hashlib
import json
import os
import queue
from pathlib import Path
import sys
import threading
import time
import uuid


request_id = contextvars.ContextVar("pan_diagnostic_request_id", default=None)


class AccessLogExecutor:
    """A bounded daemon writer; a frozen console must not hold interpreter exit."""
    def __init__(self):
        self._queue = queue.Queue(maxsize=1)
        self._lock = threading.Lock()
        self._thread = None

    def submit(self, function, *args):
        future = Future()
        with self._lock:
            self._queue.put_nowait((future, function, args))
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name="pan-http-log", daemon=True)
                self._thread.start()
        return future

    def _run(self):
        while True:
            future, function, args = self._queue.get()
            if not future.set_running_or_notify_cancel():
                continue
            try:
                value = function(*args)
            except BaseException as exc:
                future.set_exception(exc)
            else:
                future.set_result(value)


class RuntimeDiagnostics:
    def __init__(self, *, capacity=256, stall_seconds=2.0):
        self.capacity = capacity
        self.stall_seconds = stall_seconds
        self.instance = uuid.uuid4().hex
        self._lock = threading.Lock()
        self._active = {}
        self._events = deque(maxlen=2048)
        self._recent = deque(maxlen=128)
        self._pools = {}
        self._dropped = 0
        self._write_failures = 0
        self._heartbeat = time.monotonic()
        self._max_lag = 0.0
        self._stop = threading.Event()
        self._thread = None
        self._loop = None
        self._task_capture_pending = False

    def record(self, kind, **fields):
        with self._lock:
            event = {"time": time.time(), "kind": kind,
                     "pid": os.getpid(), "instance": self.instance, **fields}
            if len(self._events) == self._events.maxlen:
                self._dropped += 1
            self._events.append(event)
            self._recent.append(event)

    def begin(self, kind, **fields):
        token = uuid.uuid4().hex[:16]
        with self._lock:
            if len(self._active) < self.capacity:
                self._active[token] = {"kind": kind, "started": time.monotonic(),
                                       "stage": "queued", **fields}
            else:
                self._dropped += 1
        return token

    def stage(self, token, stage, **fields):
        with self._lock:
            if token in self._active:
                self._active[token].update(stage=stage, **fields)

    def end(self, token, *, outcome="completed", **fields):
        with self._lock:
            active = self._active.pop(token, None)
        if active is not None:
            elapsed = (time.monotonic() - active.pop("started")) * 1000
            self.record(active.pop("kind") + ".end", id=token,
                        elapsedMs=round(elapsed, 3), outcome=outcome,
                        **active, **fields)

    def pool(self, name, *, queued=0, running=0, completed=0, wait_ms=None, run_ms=None):
        with self._lock:
            p = self._pools.setdefault(name, {"queued": 0, "running": 0, "completed": 0})
            p["queued"] += queued
            p["running"] += running
            p["completed"] += completed
            if wait_ms is not None:
                p["lastWaitMs"] = round(wait_ms, 3)
            if run_ms is not None:
                p["lastRunMs"] = round(run_ms, 3)

    def pulse(self):
        now = time.monotonic()
        with self._lock:
            lag = max(0, now - self._heartbeat - 0.25)
            self._max_lag = max(self._max_lag, lag)
            self._heartbeat = now
        if lag >= self.stall_seconds:
            # A GIL-holding operation can starve the monitor thread too. Keep
            # the measured gap even if the heartbeat wins the scheduling race.
            self.record("runtime.loop_recovered", lagMs=round(lag * 1000, 3))

    def snapshot(self):
        now = time.monotonic()
        with self._lock:
            return {"pid": os.getpid(), "instance": self.instance,
                    "monitorRunning": self._thread is not None and self._thread.is_alive(),
                    "heartbeatAgeMs": round((now - self._heartbeat) * 1000, 3),
                    "maxLoopLagMs": round(self._max_lag * 1000, 3),
                    "droppedRecords": self._dropped, "writeFailures": self._write_failures,
                    "executors": {k: dict(v) for k, v in self._pools.items()},
                    "active": [{"id": k, **{a: b for a, b in v.items() if a != "started"},
                                "ageMs": round((now - v["started"]) * 1000, 3)}
                               for k, v in self._active.items()],
                    "recent": list(self._recent)}

    @staticmethod
    def thread_stacks():
        # Locations only: never format source lines or inspect frame locals.
        names = {t.ident: t.name for t in threading.enumerate()}
        result = []
        frames_by_thread = list(sys._current_frames().items())
        frames_by_thread.sort(key=lambda pair: pair[0] != threading.main_thread().ident)
        for ident, frame in frames_by_thread[:64]:
            frames = []
            while frame is not None and len(frames) < 32:
                frames.append({"file": frame.f_code.co_filename, "line": frame.f_lineno,
                               "function": frame.f_code.co_name})
                frame = frame.f_back
            result.append({"threadId": ident, "name": names.get(ident), "frames": frames})
        return result

    @staticmethod
    def task_stacks():
        result = []
        for task in list(asyncio.all_tasks())[:64]:
            result.append({"name": task.get_name(), "done": task.done(),
                           "frames": [{"file": f.f_code.co_filename,
                                       "line": f.f_lineno, "function": f.f_code.co_name}
                                      for f in task.get_stack(limit=16)]})
        return result

    def _write(self, directory, records):
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "runtime-diagnostics.jsonl"
        if path.exists() and path.stat().st_size >= 5 * 1024 * 1024:
            for index in range(2, -1, -1):
                old = path.with_name(path.name + (f".{index}" if index else ""))
                new = path.with_name(path.name + f".{index + 1}")
                if old.exists():
                    os.replace(old, new)
        with path.open("a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")

    def _watch(self, directory):
        root = Path(__file__).resolve().parents[2]
        try:
            # Fingerprint disk sources at startup on this thread, never on the
            # event loop. Later collector HEAD alone cannot identify old code.
            hashes = {}
            for relative in ("packages/web/server.py", "packages/core/worker.py",
                             "packages/core/session.py", "packages/core/runtime_diagnostics.py"):
                hashes[relative] = hashlib.sha256((root / relative).read_bytes()).hexdigest()
            self.record("runtime.source", root=str(root), executable=sys.executable,
                        sourceSha256=hashes)
        except OSError as exc:
            self.record("runtime.source_unavailable", errorType=type(exc).__name__)
        last_capture = 0.0
        while True:
            stopping = self._stop.wait(0.5)
            snapshot = self.snapshot()
            slow = [a for a in snapshot["active"] if a["ageMs"] >= self.stall_seconds * 1000]
            stalled = snapshot["heartbeatAgeMs"] >= self.stall_seconds * 1000
            now = time.monotonic()
            if (slow or stalled) and now - last_capture >= 10:
                last_capture = now
                snapshot.pop("recent", None)  # never recursively embed old snapshots
                self.record("runtime.stall", snapshot=snapshot, threads=self.thread_stacks())
                if self._loop is not None and not self._task_capture_pending:
                    self._task_capture_pending = True
                    def tasks_when_runnable(requested_at=now):
                        self._task_capture_pending = False
                        self.record("runtime.tasks", requestedAt=requested_at, tasks=self.task_stacks())
                    try:
                        self._loop.call_soon_threadsafe(tasks_when_runnable)
                    except RuntimeError:
                        self._task_capture_pending = False
            with self._lock:
                records = list(self._events)
                self._events.clear()
            if records:
                try:
                    self._write(directory, records)
                except (OSError, ValueError):
                    with self._lock:
                        self._write_failures += 1
            if stopping:
                return

    @asynccontextmanager
    async def monitor(self, directory):
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("runtime monitor already active")
        self._loop = asyncio.get_running_loop()
        self._stop.clear()
        with self._lock:
            self._heartbeat = time.monotonic()
        self._thread = threading.Thread(target=self._watch, args=(Path(directory),),
                                        name="pan-runtime-diagnostics", daemon=True)
        self._thread.start()
        self.record("runtime.start", python=sys.version.split()[0])

        async def heartbeat():
            while True:
                self.pulse()
                await asyncio.sleep(0.25)

        task = asyncio.create_task(heartbeat(), name="pan-diagnostic-heartbeat")
        try:
            yield self
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            self.record("runtime.stop")
            self._stop.set()
            # Default-executor starvation must not prevent monitor teardown.
            deadline = time.monotonic() + 1.0
            while self._thread.is_alive() and time.monotonic() < deadline:
                await asyncio.sleep(0.01)


runtime = RuntimeDiagnostics()


async def run_in_executor(pool, executor, function, /, *args, **kwargs):
    """Separate queue delay from execution, including cancellation before start."""
    enqueued = time.monotonic()
    label = getattr(function, "__qualname__", type(function).__name__)[:120]
    token = runtime.begin("executor", pool=pool, operation=label, requestId=request_id.get())
    runtime.pool(pool, queued=1)
    context = contextvars.copy_context()

    def call():
        started = time.monotonic()
        runtime.pool(pool, queued=-1, running=1, wait_ms=(started - enqueued) * 1000)
        runtime.stage(token, "running", threadId=threading.get_ident(),
                      waitMs=round((started - enqueued) * 1000, 3))
        outcome = "completed"
        try:
            return context.run(function, *args, **kwargs)
        except BaseException:
            outcome = "error"
            raise
        finally:
            runtime.pool(pool, running=-1, completed=1,
                         run_ms=(time.monotonic() - started) * 1000)
            runtime.end(token, outcome=outcome, runMs=round((time.monotonic() - started) * 1000, 3))

    try:
        future = executor.submit(call)
    except BaseException:
        runtime.pool(pool, queued=-1)
        runtime.end(token, outcome="submission_failed")
        raise

    def cancelled(f):
        if f.cancelled():
            runtime.pool(pool, queued=-1)
            runtime.end(token, outcome="cancelled_before_start")

    future.add_done_callback(cancelled)
    return await asyncio.wrap_future(future)
