"""Commit-protocol regressions for durable terminal usage enrichment.

These tests pin the commit semantics of
``packages.core.worker._run_usage_enrichment``. Three invariants:

1. ``session.save_async`` persists the whole Session through a *shielded*
   writer (:func:`await_persistence`): the real disk write finishes first and
   ``CancelledError`` is raised afterwards. Cancellation is therefore **not**
   evidence that nothing was persisted, so a cancelled attempt must not be
   rolled back unconditionally -- that would revert memory while disk already
   holds the new accounting, and the next save would write the stale numbers
   back over it.

2. Usage and the provider cursor form one commit unit. cbc derives "new"
   entries from ``raw_usage.request_count``; codex/kimi/opencode persist
   ``*_prev_usage`` / ``*_last_usage_ts`` cursors via ``set_adapter_field``.
   Rolling usage back while leaving the cursor advanced makes the next lookup
   return nothing, so the durable job is consumed with the usage lost.

3. Rollback restores only fields this attempt applied. Reimport and enrichment
   now share an async gate, and usage publish/save/rollback retire inside one
   persistence ticket so later metadata saves cannot serialize failed usage.
   The direct helper tests also pin field ownership and removed-field rollback.

No real provider, CLI, network or Pan service is involved: the adapters here
are local doubles, and every Session is bound to pytest's ``tmp_path``.
"""

import asyncio
import copy
import inspect
import json
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import session as _sess
from packages.core import worker


# ── helpers ──


def _cleanup():
    worker.workers.clear()
    worker._task_status.clear()
    worker._usage_enrichment_tasks.clear()
    worker._usage_enrichment_adapters.clear()
    worker._usage_enrichment_locks.clear()
    _sess._cache.clear()
    worker.set_broadcaster(None)


def _new_session(sid, tmp_path, adapter_name="cursor"):
    s = _sess.Session(id=sid, name="usage-commit", adapter=adapter_name)
    s.workdir = str(tmp_path)
    _sess._cache[sid] = s
    return s


def _new_worker(session_id, adapter):
    w = worker.Worker(
        worker_id="worker-usage-commit",
        session_id=session_id,
        adapter=adapter,
        status="idle",
        process=None,
        pending_signal=asyncio.Queue(),
        _replaying=False,
    )
    worker.workers[w.worker_id] = w
    return w


def _make_cursor_adapter(entries_by_cursor):
    """Adapter returning deltas by the persisted ``codex_prev_usage`` cursor.

    Faithful to the real absolute-total providers (codex / opencode): the cursor
    in ``adapter_config`` holds the provider's already-accounted token totals and
    the delta is ``provider_total - cursor`` -- see
    ``CodexAdapter.enrich_after_result``. ``entries_by_cursor`` therefore maps an
    already-accounted token total to the entries still owed, which is what makes
    a re-seeded cursor (after a reimport recompute) behave like the real thing
    instead of double counting.

    cbc is a different model (it diffs ``raw_usage.request_count`` directly) and
    is exercised by ``_make_count_based_adapter`` below.
    """

    class _CursorAdapter:
        name = "cursor"

        def __init__(self):
            self.calls = []

        def enrich_after_result(self, s):
            config = getattr(s, "adapter_config", {}) or {}
            self.calls.append(dict(config))
            prev = config.get("codex_prev_usage") or {}
            accounted = int(prev.get("input_tokens", 0) or 0)
            entries = entries_by_cursor.get(accounted)
            if entries:
                # Real CodexAdapter advances its own absolute cursor during the
                # lookup (``set_adapter_field("codex_prev_usage", ...)``), which
                # is what the enrichment then merges -- and must roll back with
                # the usage when the commit fails.
                total = accounted + sum(
                    int((e.get("rawUsage") or {}).get("prompt_tokens", 0) or 0)
                    for e in entries)
                s.set_adapter_field("codex_prev_usage", {"input_tokens": total})
            return entries

    return _CursorAdapter()


def _make_count_based_adapter(entries):
    """Adapter diffing the provider's entries against ``raw_usage.request_count``.

    This is cbc's real model (``_read_jsonl_new_entries``): no separate cursor,
    the accumulated ``request_count`` *is* the dedup state, so a recomputed total
    is self-consistent without any re-seeding.
    """

    class _CountAdapter:
        name = "cbc"

        def __init__(self):
            self.calls = []

        def enrich_after_result(self, s):
            acc = ((s.raw_usage or {}).get("m", {}) or {}).get(
                "request_count", 0)
            self.calls.append(acc)
            new = entries[acc:]
            return new or None

    return _CountAdapter()


def _seed_job(s, w, key="task:cursor-1"):
    job = {
        "key": key, "adapter": "cursor", "taskId": key, "taskSeq": None,
        "workerId": w.worker_id, "generation": 0, "state": "pending",
        "attempts": 0, "nextAttemptAt": 0.0, "createdAt": 0.0,
    }
    s.usage_enrichment_pending = [job]
    return job


def _usage(model, count, prompt, credit):
    return {
        "model": model,
        "request_count": count,
        "rawUsage": {
            "prompt_tokens": prompt, "completion_tokens": 1, "credit": credit,
        },
    }


def _delta(model="m", prompt=100, credit=0.25):
    return [{"model": model, "rawUsage": {"prompt_tokens": prompt,
                                          "credit": credit}}]


def _no_retry_delay(monkeypatch):
    monkeypatch.setattr(worker, "_ENRICH_RETRY_BASE_SEC", 0)
    monkeypatch.setattr(worker, "_ENRICH_RETRY_MAX_SEC", 0)


# ── 1. cancellation is not evidence of "not persisted" ──


def _patch_save(monkeypatch, save_impl):
    """Route the commit's durable write through ``save_impl(sess)``.

    ``worker._commit_usage_enrichment`` runs inside a persistence ticket and
    runs ``session._save_body`` in the save executor thread -- so the
    replacement must be **sync**; an async one would never be awaited.

    ``save_impl`` receives the Session and may raise to fail the write.
    """
    if inspect.iscoroutinefunction(save_impl):
        raise AssertionError(
            "_save_body runs in the save executor thread; use a sync callable")

    def _body(sess, **kwargs):
        return save_impl(sess)

    monkeypatch.setattr(_sess, "_save_body", _body)
    return _body


def _disk_usage(session_id, tmp_path):
    """Re-read the durable record from disk (no in-memory state involved)."""
    path = tmp_path / "sessions" / (session_id + ".json")
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8")).get("raw_usage")


def test_real_cancel_during_successful_write_commits(monkeypatch, tmp_path):
    """Real task.cancel() while a *successful* write is in flight.

    The writer is slowed so the cancellation provably lands before the write
    finishes. The durable record is then re-read from disk and compared with
    memory, the durable job and the provider cursor: all must agree.
    """
    _cleanup()
    monkeypatch.setattr(_sess, "SESSION_DIR", tmp_path / "sessions")
    _no_retry_delay(monkeypatch)

    sid = "ses_cancel_ok"
    adapter = _make_cursor_adapter({0: _delta()})
    s = _new_session(sid, tmp_path)
    w = _new_worker(sid, adapter)
    worker._usage_enrichment_adapters["task:cursor-1"] = adapter
    _seed_job(s, w)
    s.raw_usage = {"m": _usage("m", 2, 20, 0.5)}
    s.total_usage = _sess.compute_total_usage(s.raw_usage)
    baseline_rc = 2

    real_save_body = _sess._save_body
    started = threading.Event()

    def slow_save_body(sess, **kwargs):
        started.set()
        time.sleep(0.25)          # cancellation lands inside this window
        return real_save_body(sess, **kwargs)

    monkeypatch.setattr(_sess, "_save_body", slow_save_body)

    async def scenario():
        task = asyncio.ensure_future(worker._run_usage_enrichment(sid))
        while not started.is_set():
            await asyncio.sleep(0.005)
        task.cancel()               # real cancel, mid-write
        return task

    task = asyncio.run(scenario())
    assert task.cancelled(), "the cancellation must reach the caller"

    disk = _disk_usage(sid, tmp_path)
    assert disk is not None, "durable record must exist after a successful write"
    assert disk["m"]["request_count"] == baseline_rc + 1, f"on disk: {disk}"
    # memory, disk, job and cursor must all agree the commit happened
    assert s.raw_usage["m"]["request_count"] == disk["m"]["request_count"], s.raw_usage
    assert s.total_usage["prompt_tokens"] == disk["m"]["rawUsage"]["prompt_tokens"], (
        f"total disagrees with disk: {s.total_usage}"
    )
    assert s.usage_enrichment_pending == [], "committed job must not be resurrected"
    assert s.adapter_config["codex_prev_usage"]["input_tokens"] == 100, (
        f"committed cursor must equal the accounted total: {s.adapter_config}"
    )
    print("PASS: real cancel during a successful write keeps memory == disk")
    _cleanup()


def test_real_cancel_during_failing_write_rolls_back(monkeypatch, tmp_path):
    """Real task.cancel() while a *failing* write is in flight.

    await_persistence consumes the writer's real exception and re-raises the
    cancellation, so CancelledError alone cannot say whether bytes landed. The
    decision must follow the writer's verifiable result: here nothing is on
    disk, so usage, cursor and job all roll back and the terminal is accounted
    exactly once after the replay.
    """
    _cleanup()
    monkeypatch.setattr(_sess, "SESSION_DIR", tmp_path / "sessions")
    _no_retry_delay(monkeypatch)

    sid = "ses_cancel_fail"
    adapter = _make_cursor_adapter({0: _delta()})
    s = _new_session(sid, tmp_path)
    w = _new_worker(sid, adapter)
    worker._usage_enrichment_adapters["task:cursor-1"] = adapter
    _seed_job(s, w)
    s.raw_usage = {"m": _usage("m", 2, 20, 0.5)}
    s.total_usage = _sess.compute_total_usage(s.raw_usage)
    baseline_rc = 2

    real_save_body = _sess._save_body
    started = threading.Event()
    fail = {"on": True}

    def slow_failing_save_body(sess, **kwargs):
        started.set()
        time.sleep(0.25)
        if fail["on"]:
            raise OSError("disk full")
        return real_save_body(sess, **kwargs)

    monkeypatch.setattr(_sess, "_save_body", slow_failing_save_body)

    async def scenario():
        task = asyncio.ensure_future(worker._run_usage_enrichment(sid))
        while not started.is_set():
            await asyncio.sleep(0.005)
        task.cancel()                # real cancel, mid failing write
        return task

    task = asyncio.run(scenario())
    assert task.cancelled()

    assert _disk_usage(sid, tmp_path) is None, "a failed write must leave no record"
    assert [j["key"] for j in (s.usage_enrichment_pending or [])] == [
        "task:cursor-1"], s.usage_enrichment_pending
    assert s.raw_usage["m"]["request_count"] == baseline_rc, (
        f"uncommitted usage must roll back: {s.raw_usage}"
    )
    assert not s.adapter_config.get("codex_prev_usage"), (
        f"uncommitted cursor must roll back too: {s.adapter_config}"
    )

    # Replay with a working writer: accounted exactly once, on disk too.
    monkeypatch.setattr(_sess, "_save_body", real_save_body)
    asyncio.run(worker._run_usage_enrichment(sid))
    disk = _disk_usage(sid, tmp_path)
    assert s.raw_usage["m"]["request_count"] == baseline_rc + 1, s.raw_usage
    assert disk["m"]["request_count"] == baseline_rc + 1, f"disk: {disk}"
    assert s.usage_enrichment_pending == []
    print("PASS: real cancel during a failing write rolls back and replays once")
    _cleanup()


def test_cancelled_save_with_no_write_is_not_committed(monkeypatch, tmp_path):
    """A save raising CancelledError without writing is NOT a commit.

    Guards the exact misclassification found in review: retirement of the writer
    is not evidence of a successful write.
    """
    _cleanup()
    monkeypatch.setattr(_sess, "SESSION_DIR", tmp_path / "sessions")
    _no_retry_delay(monkeypatch)

    sid = "ses_cancel_nowrite"
    adapter = _make_cursor_adapter({0: _delta()})
    s = _new_session(sid, tmp_path)
    w = _new_worker(sid, adapter)
    worker._usage_enrichment_adapters["task:cursor-1"] = adapter
    _seed_job(s, w)
    s.raw_usage = {"m": _usage("m", 1, 10, 0.25)}
    s.total_usage = _sess.compute_total_usage(s.raw_usage)

    def cancel_without_writing(sess, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(_sess, "_save_body", cancel_without_writing)

    async def scenario():
        with pytest.raises(asyncio.CancelledError):
            await worker._run_usage_enrichment(sid)

    asyncio.run(scenario())

    assert _disk_usage(sid, tmp_path) is None
    assert s.raw_usage["m"]["request_count"] == 1, (
        f"an unwritten save must not count as committed: {s.raw_usage}"
    )
    assert [j["key"] for j in (s.usage_enrichment_pending or [])] == [
        "task:cursor-1"], s.usage_enrichment_pending
    assert not s.adapter_config.get("codex_prev_usage"), s.adapter_config
    print("PASS: a cancelled save with no write is not a commit")
    _cleanup()



# ── 2. usage and provider cursor commit/roll back as one unit ──


def test_save_failure_rolls_back_usage_and_cursor_together(monkeypatch, tmp_path):
    """A failed save must roll back usage *and* the provider cursor.

    With a cursor-driven adapter, advancing the cursor while rolling usage
    back makes the retry read an empty delta; the job is then consumed and the
    usage is lost permanently. The retry must re-read the same entries and
    account them exactly once.
    """
    _cleanup()
    monkeypatch.setattr(_sess, "SESSION_DIR", tmp_path / "sessions")
    _no_retry_delay(monkeypatch)

    sid = "ses_cursor_rollback"
    adapter = _make_cursor_adapter({0: _delta()})
    s = _new_session(sid, tmp_path)
    w = _new_worker(sid, adapter)
    worker._usage_enrichment_adapters["task:cursor-1"] = adapter
    _seed_job(s, w)

    s.raw_usage = {"m": _usage("m", 4, 40, 1.0)}
    s.total_usage = _sess.compute_total_usage(s.raw_usage)
    baseline_rc = 4

    saves = []

    def flaky_save(sess):
        saves.append(1)
        if len(saves) == 1:
            raise OSError("transient save failure")

    _patch_save(monkeypatch, flaky_save)

    async def scenario():
        await worker._run_usage_enrichment(sid)

    asyncio.run(scenario())

    # failed save + retry-state save + successful save
    assert len(saves) == 3, f"unexpected save count {len(saves)}"
    assert s.usage_enrichment_pending == []
    assert len(adapter.calls) == 2, (
        f"provider must be re-read after rollback, got {adapter.calls}"
    )
    assert s.raw_usage["m"]["request_count"] == baseline_rc + 1, (
        f"retry must account exactly once: {s.raw_usage}"
    )
    assert s.raw_usage["m"]["rawUsage"]["prompt_tokens"] == 140, s.raw_usage
    assert s.raw_usage["m"]["rawUsage"]["credit"] == 1.25, s.raw_usage
    assert s.total_usage["credit"] == 1.25, s.total_usage
    assert s.adapter_config["codex_prev_usage"]["input_tokens"] == 100, (
        f"cursor must commit together with usage: {s.adapter_config}"
    )
    print("PASS: usage and provider cursor commit/rollback as one unit")
    _cleanup()


def test_cursor_not_advanced_when_rollback_happens(monkeypatch, tmp_path):
    """After a rolled-back attempt the cursor must still read the old value.

    Guards the specific data-loss path: if the cursor survived the rollback,
    the replayed job would find no new entries and be dropped with the usage.
    """
    _cleanup()
    monkeypatch.setattr(_sess, "SESSION_DIR", tmp_path / "sessions")
    _no_retry_delay(monkeypatch)

    sid = "ses_cursor_value"
    adapter = _make_cursor_adapter({0: _delta(), 1: _delta(prompt=7, credit=0.7)})
    s = _new_session(sid, tmp_path)
    w = _new_worker(sid, adapter)
    worker._usage_enrichment_adapters["task:cursor-1"] = adapter
    _seed_job(s, w)
    s.adapter_config["codex_prev_usage"] = {"input_tokens": 0}

    s.raw_usage = {"m": _usage("m", 0, 0, 0.0)}
    s.total_usage = _sess.compute_total_usage(s.raw_usage)

    saves = []

    def flaky_save(sess):
        saves.append(1)
        if len(saves) == 1:
            raise OSError("transient save failure")

    _patch_save(monkeypatch, flaky_save)

    async def scenario():
        await worker._run_usage_enrichment(sid)

    asyncio.run(scenario())

    # Both attempts read cursor 0 -> the delta is accounted exactly once.
    assert [c.get("codex_prev_usage", {}).get("input_tokens") for c in adapter.calls] == [0, 0], (
        f"both lookups must read the same pre-lookup cursor: {adapter.calls}")
    assert s.raw_usage["m"]["rawUsage"]["prompt_tokens"] == 100, s.raw_usage
    assert s.adapter_config["codex_prev_usage"]["input_tokens"] == 100, (
        f"committed cursor must equal the accounted total: {s.adapter_config}"
    )
    print("PASS: rolled-back attempt leaves the cursor re-readable")
    _cleanup()


# ── 3. rollback must not clobber a concurrent writer ──


def test_rollback_preserves_concurrent_usage_writer(monkeypatch, tmp_path):
    """A concurrent writer's committed usage must survive the failed commit.

    The HTTP reimport path in ``packages/web/server.py`` assigns
    ``existing.raw_usage``/``total_usage`` directly and does not take the
    Session-scoped enrichment lock, so it can commit while this attempt awaits
    the disk. A whole-snapshot restore would discard it.

    There is deliberately **no** "superseded" shortcut here: the reimport read
    the provider before this attempt published, so nothing proves its total
    already contains this terminal. The job is therefore replayed and the delta
    is applied to whatever the live usage is -- which is why the total ends up
    one higher than the reimport wrote.
    """
    _cleanup()
    monkeypatch.setattr(_sess, "SESSION_DIR", tmp_path / "sessions")
    _no_retry_delay(monkeypatch)

    sid = "ses_concurrent_writer"
    adapter = _make_cursor_adapter({0: _delta()})
    s = _new_session(sid, tmp_path)
    w = _new_worker(sid, adapter)
    worker._usage_enrichment_adapters["task:cursor-1"] = adapter
    _seed_job(s, w)

    s.raw_usage = {"m": _usage("m", 1, 10, 0.25)}
    s.total_usage = _sess.compute_total_usage(s.raw_usage)

    def save_then_reimport(sess):
        if len(saves) == 0:
            saves.append(1)
            # A reimport commits a recomputed total mid-flight.
            sess.raw_usage = copy.deepcopy({"m": _usage("m", 99, 990, 9.9)})
            sess.total_usage = _sess.compute_total_usage(sess.raw_usage)
            raise OSError("transient save failure")

    saves: list[int] = []
    _patch_save(monkeypatch, save_then_reimport)

    async def scenario():
        await worker._run_usage_enrichment(sid)

    asyncio.run(scenario())

    # The reimport's total is the base; the replayed job added its delta once.
    assert s.raw_usage["m"]["request_count"] == 100, (
        f"expected reimport base 99 + one replayed delta: {s.raw_usage}"
    )
    assert s.raw_usage["m"]["rawUsage"]["prompt_tokens"] == 990 + 100, s.raw_usage
    assert s.total_usage["prompt_tokens"] == 990 + 100, (
        f"total must match the raw usage it summarises: {s.total_usage}"
    )
    assert s.usage_enrichment_pending == [], s.usage_enrichment_pending
    # The job was replayed rather than dropped, and settled exactly once more.
    assert len(adapter.calls) == 2, f"expected one replay, got {adapter.calls}"
    print("PASS: a concurrent writer's usage is preserved and reconciled")
    _cleanup()


def test_rollback_keeps_own_usage_when_no_concurrent_writer(monkeypatch, tmp_path):
    """Control case: with no competing writer the failed attempt still replays.

    Guards against over-correcting #3: the compare-and-restore must not turn
    every save failure into "superseded", which would silently drop usage.
    """
    _cleanup()
    monkeypatch.setattr(_sess, "SESSION_DIR", tmp_path / "sessions")
    _no_retry_delay(monkeypatch)

    sid = "ses_no_concurrent_writer"
    adapter = _make_cursor_adapter({0: _delta()})
    s = _new_session(sid, tmp_path)
    w = _new_worker(sid, adapter)
    worker._usage_enrichment_adapters["task:cursor-1"] = adapter
    _seed_job(s, w)

    s.raw_usage = {"m": _usage("m", 1, 10, 0.25)}
    s.total_usage = _sess.compute_total_usage(s.raw_usage)

    saves = []

    def flaky_save(sess):
        saves.append(1)
        if len(saves) == 1:
            raise OSError("transient save failure")

    _patch_save(monkeypatch, flaky_save)

    async def scenario():
        await worker._run_usage_enrichment(sid)

    asyncio.run(scenario())

    assert s.usage_enrichment_pending == []
    assert len(adapter.calls) == 2, f"job must be replayed, got {adapter.calls}"
    assert s.raw_usage["m"]["request_count"] == 2, (
        f"replay must account exactly once: {s.raw_usage}"
    )
    assert s.raw_usage["m"]["rawUsage"]["prompt_tokens"] == 110, s.raw_usage
    assert s.adapter_config["codex_prev_usage"]["input_tokens"] == 100, (
        f"committed cursor must equal the accounted total: {s.adapter_config}"
    )
    print("PASS: without a competing writer the job is replayed, not dropped")
    _cleanup()


def test_provider_failure_before_accumulate_keeps_session_intact(monkeypatch, tmp_path):
    """A provider failure *before* accumulation must leave the Session intact.

    This is the "the lookup blew up" path, not the half-applied-accumulation
    path (that one is covered by
    ``test_accumulate_exception_does_not_mutate_session_usage``, which really
    does raise inside ``accumulate_raw_usage``). Here the provider fails, so no
    candidate is ever built and nothing is published.
    """
    _cleanup()
    monkeypatch.setattr(_sess, "SESSION_DIR", tmp_path / "sessions")

    sid = "ses_partial_accumulate"
    adapter = _make_cursor_adapter({0: _delta()})
    state = {"calls": 0}

    original_enrich = adapter.enrich_after_result

    def enrich_once_then_empty(s):
        state["calls"] += 1
        if state["calls"] == 1:
            # Force the failure inside the commit block, after the lookup.
            raise RuntimeError("provider delta exploded")
        return None

    adapter.enrich_after_result = enrich_once_then_empty
    s = _new_session(sid, tmp_path)
    w = _new_worker(sid, adapter)
    worker._usage_enrichment_adapters["task:cursor-1"] = adapter
    _seed_job(s, w)

    before = {"m": _usage("m", 3, 30, 0.75)}
    s.raw_usage = copy.deepcopy(before)
    s.total_usage = _sess.compute_total_usage(s.raw_usage)

    _no_retry_delay(monkeypatch)

    async def scenario():
        await worker._run_usage_enrichment(sid)

    asyncio.run(scenario())

    # The failed attempt published nothing: the Session is unchanged.
    assert s.raw_usage["m"]["request_count"] == 3, s.raw_usage
    assert s.raw_usage["m"]["rawUsage"]["prompt_tokens"] == 30, (
        f"half-applied usage leaked into the Session: {s.raw_usage}"
    )
    assert s.total_usage["prompt_tokens"] == 30, s.total_usage
    print("PASS: a failed accumulation leaves the Session untouched")
    _cleanup()


def test_accumulate_exception_does_not_mutate_session_usage(monkeypatch, tmp_path):
    _cleanup()
    s = _new_session("ses_candidate", tmp_path)
    w = _new_worker(s.id, _make_cursor_adapter({}))
    job = _seed_job(s, w)
    s.raw_usage = {"m": _usage("m", 3, 30, 0.75)}
    s.total_usage = _sess.compute_total_usage(s.raw_usage)
    before = copy.deepcopy(s.raw_usage)

    def mutating_then_raising(existing, entries):
        existing["m"]["request_count"] += 1
        raise RuntimeError("partial candidate")

    monkeypatch.setattr(_sess, "accumulate_raw_usage", mutating_then_raising)
    with pytest.raises(RuntimeError, match="partial candidate"):
        worker._commit_usage_enrichment(
            s, worker._make_usage_enrichment_snapshot(s), _delta(), job)
    assert s.raw_usage == before
    assert s.usage_enrichment_pending == [job]
    _cleanup()


def test_second_pending_job_is_drained_after_first(monkeypatch, tmp_path):
    """A failing first job must not strand the second pending job.

    The durable loop is a `while True` over ``usage_enrichment_pending``; a
    rolled-back job is re-queued at the front and the loop must keep going so a
    later job is still processed (and the earlier one eventually settles).
    """
    _cleanup()
    monkeypatch.setattr(_sess, "SESSION_DIR", tmp_path / "sessions")
    _no_retry_delay(monkeypatch)

    sid = "ses_two_jobs"
    adapter = _make_cursor_adapter({0: _delta(prompt=10, credit=0.1)})
    s = _new_session(sid, tmp_path)
    w = _new_worker(sid, adapter)
    worker._usage_enrichment_adapters["task:a"] = adapter
    worker._usage_enrichment_adapters["task:b"] = adapter

    first = _seed_job(s, w, key="task:a")
    second = dict(first)
    second.update({"key": "task:b", "taskId": "task:b"})
    s.usage_enrichment_pending = [first, second]

    s.raw_usage = {"m": _usage("m", 0, 0, 0.0)}
    s.total_usage = _sess.compute_total_usage(s.raw_usage)

    attempts = {"n": 0}

    def flaky(sess):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise OSError("first save fails")

    _patch_save(monkeypatch, flaky)

    async def scenario():
        await worker._run_usage_enrichment(sid)

    asyncio.run(scenario())

    # Both jobs settled: nothing left pending.
    assert s.usage_enrichment_pending == [], (
        f"a job was stranded: {s.usage_enrichment_pending}"
    )
    # job:a failed once and replayed; job:b was drained in the same loop.
    assert len(adapter.calls) >= 3, (
        f"expected a replay plus the second job, calls={adapter.calls}"
    )
    print("PASS: the durable loop drains every pending job")
    _cleanup()


def test_restart_recovery_replays_persisted_job(monkeypatch, tmp_path):
    """A job left pending by a crash is picked up by the recovery scan.

    Simulates a restart: the Session is written with a pending job, the process
    state is cleared, and ``recover_pending_usage_enrichment`` must schedule it
    again so the terminal is not silently dropped.
    """
    _cleanup()
    monkeypatch.setattr(_sess, "SESSION_DIR", tmp_path / "sessions")
    _no_retry_delay(monkeypatch)

    sid = "ses_restart"
    adapter = _make_cursor_adapter({0: _delta(prompt=7, credit=0.7)})
    s = _new_session(sid, tmp_path)
    w = _new_worker(sid, adapter)
    worker._usage_enrichment_adapters["task:cursor-1"] = adapter
    _seed_job(s, w)
    s.raw_usage = {"m": _usage("m", 0, 0, 0.0)}
    s.total_usage = _sess.compute_total_usage(s.raw_usage)

    # Persist the pending job, then "restart": drop in-memory state.
    asyncio.run(_sess.save_async(s))
    _cleanup()

    reloaded = _sess.get(sid)
    assert reloaded is not None, "the session must reload from disk"
    assert [j["key"] for j in (reloaded.usage_enrichment_pending or [])] == [
        "task:cursor-1"], reloaded.usage_enrichment_pending

    # Scheduling needs a running loop (it creates the durable worker task), so
    # the recovery scan runs inside one.
    async def run_recovery():
        scheduled = worker.recover_pending_usage_enrichment()
        task = worker._usage_enrichment_tasks.get(sid)
        # The durable worker must actually be running, not just registered.
        await asyncio.sleep(0)
        return scheduled, (task is not None and not task.done())

    scheduled, task_running = asyncio.run(run_recovery())
    assert scheduled == 1, f"recovery must schedule the pending job, got {scheduled}"
    assert task_running, "the recovered durable worker must be running"
    print("PASS: a persisted pending job is recovered after restart")
    _cleanup()


def test_rollback_never_takes_back_a_field_a_concurrent_writer_moved(
        monkeypatch, tmp_path):
    """A cursor a concurrent writer moved after our merge must survive.

    This is the ownership rule in ``_restore_usage_enrichment_state``: a field is
    only taken back while it still holds the value *this attempt* wrote. The
    merge recorded that it wrote ``prev_usage_ts``; a concurrent writer then moves
    that same field while the save is in flight. If the rollback restored the
    provider's value regardless of who owns the field now, it would hand the
    field back to a stale cursor and undo the other writer's commit.

    ``other_cursor`` is the control: nothing else touches it, so it must come
    back to the provider's value.
    """
    _cleanup()
    monkeypatch.setattr(_sess, "SESSION_DIR", tmp_path / "sessions")
    _no_retry_delay(monkeypatch)

    sid = "ses_cursor_owner"
    adapter = _make_cursor_adapter({0: _delta()})
    s = _new_session(sid, tmp_path)
    w = _new_worker(sid, adapter)
    worker._usage_enrichment_adapters["task:cursor-1"] = adapter
    _seed_job(s, w)

    s.raw_usage = {"m": _usage("m", 1, 10, 0.25)}
    s.total_usage = _sess.compute_total_usage(s.raw_usage)
    s.adapter_config["codex_prev_usage"] = {"input_tokens": 0}

    real_save_body = _sess._save_body
    saves: list[int] = []

    def save_then_concurrent(sess):
        if len(saves) == 0:
            saves.append(1)
            # A concurrent writer (reimport / settings) moves the SAME cursor
            # field after our merge already wrote it.
            sess.adapter_config["codex_prev_usage"] = {"input_tokens": 9900}
            raise OSError("transient save failure")
        return real_save_body(sess)

    _patch_save(monkeypatch, save_then_concurrent)

    async def scenario():
        await worker._run_usage_enrichment(sid)

    asyncio.run(scenario())

    assert s.adapter_config.get("codex_prev_usage", {}).get("input_tokens") == 9900, (
        f"rollback took back a cursor a concurrent writer owns: {s.adapter_config}"
    )
    print("PASS: rollback never takes back a field a concurrent writer moved")
    _cleanup()


def test_rollback_never_takes_back_a_field_the_merge_skipped(tmp_path):
    s = _new_session("ses_skipped_cursor", tmp_path)
    snapshot = worker._make_usage_enrichment_snapshot(s)
    snapshot.set_adapter_field("cursor", 1)
    s.set_adapter_field("cursor", 1)  # independent live owner
    applied, model = worker._merge_usage_enrichment_state(s, snapshot)
    assert "cursor" not in applied
    worker._restore_usage_enrichment_state(s, snapshot, applied, model)
    assert s.adapter_config["cursor"] == 1
    _cleanup()


def test_removed_cursor_and_model_restore_on_failure(monkeypatch, tmp_path):
    s = _new_session("ses_removed_cursor", tmp_path)
    s.set_adapter_field("cursor", 10)
    snapshot = worker._make_usage_enrichment_snapshot(s)
    snapshot.set_adapter_field("cursor", None)
    snapshot.model = "native-model"
    w = _new_worker(s.id, _make_cursor_adapter({}))
    job = _seed_job(s, w)
    def fail(sess, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(_sess, "_save_body", fail)
    with pytest.raises(OSError):
        worker._commit_usage_enrichment(s, snapshot, None, job)
    assert s.adapter_config["cursor"] == 10
    assert s.model == snapshot.model_before
    assert s.usage_enrichment_pending == [job]
    _cleanup()


def test_cursor_only_change_rolls_back_with_save_failure(monkeypatch, tmp_path):
    """A save failure with *no* usage delta must still roll the cursor back.

    The provider can advance its cursor and report nothing new (already-counted
    entries). The commit then has no usage to apply, but the cursor moved; if the
    save fails and only the usage were rolled back, the cursor would stay
    advanced and the replayed lookup would find no entries -- losing whatever the
    cursor was standing in for.
    """
    _cleanup()
    monkeypatch.setattr(_sess, "SESSION_DIR", tmp_path / "sessions")
    _no_retry_delay(monkeypatch)

    sid = "ses_cursor_only"
    # Returns entries for cursor 0 but leaves the cursor advanced by the adapter.
    adapter = _make_cursor_adapter({})
    cursor_calls = []

    def enrich_advancing_cursor(sess):
        cursor_calls.append(copy.deepcopy(sess.adapter_config))
        sess.set_adapter_field("codex_prev_usage", {"input_tokens": 1})
        return None  # no new usage entries

    adapter.enrich_after_result = enrich_advancing_cursor
    s = _new_session(sid, tmp_path)
    w = _new_worker(sid, adapter)
    worker._usage_enrichment_adapters["task:cursor-1"] = adapter
    _seed_job(s, w)
    s.raw_usage = {"m": _usage("m", 2, 20, 0.5)}
    s.total_usage = _sess.compute_total_usage(s.raw_usage)
    s.adapter_config["codex_prev_usage"] = {"input_tokens": 0}

    real_save_body = _sess._save_body
    attempts = {"n": 0}

    def fail_first_save(sess):
        # Only the commit's own save fails; the retry-state save inside
        # retry_job must succeed, otherwise the durable loop spins forever.
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise OSError("disk full")
        # On the replay let the provider path finish and settle the job.
        return real_save_body(sess)

    _patch_save(monkeypatch, fail_first_save)

    async def scenario():
        await worker._run_usage_enrichment(sid)

    asyncio.run(scenario())

    assert cursor_calls, "the provider must have been consulted"
    # Usage untouched (no delta was ever applied)...
    assert s.raw_usage["m"]["request_count"] == 2, s.raw_usage
    # Retry must see the restored cursor, then successfully commit its advance.
    assert [c["codex_prev_usage"]["input_tokens"] for c in cursor_calls] == [0, 0]
    assert s.adapter_config["codex_prev_usage"]["input_tokens"] == 1
    assert s.usage_enrichment_pending == []
    sess_disk = json.loads((tmp_path / "sessions" / (sid + ".json")).read_text())
    assert sess_disk["adapter_config"]["codex_prev_usage"]["input_tokens"] == 1
    _cleanup()


# ── the real HTTP reimport handler must go through the shared gate ──
#
# The unit tests above call ``session.replace_usage_totals`` directly, which
# proves the *mechanism*. This drives ``server._import_session`` so the wiring
# is covered too: the handler is what actually replaces a live Session's usage
# in production, and if it ever assigns ``raw_usage`` directly again the
# coordination silently disappears.

def test_http_reimport_handler_uses_the_shared_usage_gate(monkeypatch, tmp_path):
    """A reimport through the real handler re-seeds cursors and bumps revision."""
    _cleanup()
    monkeypatch.setattr(_sess, "SESSION_DIR", tmp_path / "sessions")
    sid = "ses_http_reimport"
    s = _new_session(sid, tmp_path, adapter_name="codex")
    s.cli_session_id = "thread-http"
    # a cursor the provider had advanced, and usage that predates it
    s.raw_usage = {"m": _usage("m", 1, 10, 0.25)}
    s.total_usage = _sess.compute_total_usage(s.raw_usage)
    s.adapter_config["codex_prev_usage"] = {"input_tokens": 10}
    revision_before = _sess.usage_revision(s)

    class _Provider:
        def session_exists(self, session_id, cwd):
            return True

        def parse_history(self, session_id, cwd):
            return [{"role": "user", "content": "hi"}]

        def get_raw_usage(self, session_id, cwd):
            return [{"model": "m", "rawUsage": {"input_tokens": 110}}]

        def get_session_title(self, session_id, cwd):
            return "imported thread"

    import packages.web.server as server

    async def scenario():
        return await server._import_session(
            _Provider(), "codex", {"session_id": "thread-http"})

    result = asyncio.run(scenario())
    assert "error" not in result, f"reimport failed: {result}"

    # The handler installed the recomputed total ...
    assert s.raw_usage["m"]["request_count"] == 1, s.raw_usage
    assert s.raw_usage["m"]["rawUsage"]["prompt_tokens"] == 110, s.raw_usage
    assert s.total_usage["prompt_tokens"] == 110
    # ... through the gate, so the revision advanced and the absolute cursor was
    # re-seeded to match the recomputed total instead of staying behind it.
    assert _sess.usage_revision(s) > revision_before, (
        "the reimport must go through replace_usage_totals (revision bump)"
    )
    assert s.adapter_config["codex_prev_usage"]["input_tokens"] == 110, (
        f"the provider cursor must be re-seeded by the reimport: {s.adapter_config}"
    )
    print("PASS: the HTTP reimport handler uses the shared usage gate")
    _cleanup()
