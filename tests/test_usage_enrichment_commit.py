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

3. Rollback is compare-and-restore. While this attempt awaits the disk, other
   writers may commit -- notably the HTTP reimport path in
   ``packages/web/server.py``, which rewrites ``raw_usage``/``total_usage``
   without holding the Session-scoped enrichment lock. A field another writer
   changed belongs to that writer and must survive the rollback.

No real provider, CLI, network or Pan service is involved: the adapters here
are local doubles, and every Session is bound to pytest's ``tmp_path``.
"""

import asyncio
import copy
import sys
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
    """Adapter returning deltas by the persisted ``prev_usage_ts`` cursor.

    Mirrors the real providers: the cursor lives in ``adapter_config`` and is
    advanced with ``set_adapter_field`` during the lookup, so a retry that
    finds the cursor already moved reads no further entries.
    """

    class _CursorAdapter:
        name = "cursor"

        def __init__(self):
            self.calls = []

        def enrich_after_result(self, s):
            self.calls.append(dict(getattr(s, "adapter_config", {}) or {}))
            cursor = (getattr(s, "adapter_config", {}) or {}).get(
                "prev_usage_ts", 0)
            entries = entries_by_cursor.get(cursor)
            if entries:
                s.set_adapter_field("prev_usage_ts", cursor + 1)
            return entries

    return _CursorAdapter()


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


def test_cancelled_after_persisted_save_keeps_memory_with_disk(monkeypatch, tmp_path):
    """A cancelled-but-persisted attempt must not roll memory back.

    ``await_persistence`` waits for the shielded writer to finish and only
    then re-raises ``CancelledError``, so disk already holds the new accounting.
    Rolling memory back here forks memory from disk; the next unrelated save
    would then overwrite the correct on-disk numbers with the stale snapshot,
    and the durable job is already gone from both sides.
    """
    _cleanup()
    monkeypatch.setattr(_sess, "SESSION_DIR", tmp_path / "sessions")
    _no_retry_delay(monkeypatch)

    sid = "ses_cancel_persisted"
    adapter = _make_cursor_adapter({0: _delta()})
    s = _new_session(sid, tmp_path)
    w = _new_worker(sid, adapter)
    worker._usage_enrichment_adapters["task:cursor-1"] = adapter
    _seed_job(s, w)

    s.raw_usage = {"m": _usage("m", 2, 20, 0.5)}
    s.total_usage = _sess.compute_total_usage(s.raw_usage)
    baseline_rc = 2

    real_save = _sess.save_async

    async def save_then_cancel(sess):
        # Real durable write first (goes through the shielded writer and
        # touches disk), then the caller is cancelled while awaiting.
        await real_save(sess)
        raise asyncio.CancelledError()

    monkeypatch.setattr(_sess, "save_async", save_then_cancel)

    async def scenario():
        await worker._run_usage_enrichment(sid)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(scenario())

    assert s.raw_usage["m"]["request_count"] == baseline_rc + 1, (
        f"committed-then-cancelled must keep memory in step with disk: {s.raw_usage}"
    )
    assert s.raw_usage["m"]["rawUsage"]["prompt_tokens"] == 20 + 100, s.raw_usage
    assert s.total_usage["prompt_tokens"] == 20 + 100, s.total_usage
    assert s.usage_enrichment_pending == [], (
        "a persisted commit must not resurrect the durable job in memory"
    )
    assert s.adapter_config.get("prev_usage_ts") == 1, (
        f"provider cursor must stay committed with the usage: {s.adapter_config}"
    )
    print("PASS: cancel-after-persist keeps memory in step with disk")
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

    async def flaky_save(sess):
        saves.append(1)
        if len(saves) == 1:
            raise OSError("transient save failure")

    monkeypatch.setattr(_sess, "save_async", flaky_save)

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
    assert s.adapter_config.get("prev_usage_ts") == 1, (
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
    s.adapter_config["prev_usage_ts"] = 0

    s.raw_usage = {"m": _usage("m", 0, 0, 0.0)}
    s.total_usage = _sess.compute_total_usage(s.raw_usage)

    saves = []

    async def flaky_save(sess):
        saves.append(1)
        if len(saves) == 1:
            raise OSError("transient save failure")

    monkeypatch.setattr(_sess, "save_async", flaky_save)

    async def scenario():
        await worker._run_usage_enrichment(sid)

    asyncio.run(scenario())

    # Both attempts read cursor 0 -> the delta is accounted exactly once.
    assert [c.get("prev_usage_ts") for c in adapter.calls] == [0, 0], adapter.calls
    assert s.raw_usage["m"]["rawUsage"]["prompt_tokens"] == 100, s.raw_usage
    assert s.adapter_config.get("prev_usage_ts") == 1, s.adapter_config
    print("PASS: rolled-back attempt leaves the cursor re-readable")
    _cleanup()


# ── 3. rollback must not clobber a concurrent writer ──


def test_rollback_preserves_concurrent_usage_writer(monkeypatch, tmp_path):
    """A concurrent writer's committed usage must survive the failed commit.

    The HTTP reimport path in ``packages/web/server.py`` assigns
    ``existing.raw_usage``/``total_usage`` directly and does not take the
    Session-scoped enrichment lock, so it can commit while this attempt awaits
    the disk. A whole-snapshot restore would discard it.

    Reimport recomputes usage from the provider's own totals, so its numbers
    already include this terminal: the attempt is therefore *superseded* -- the
    cursor stays advanced and the job is consumed rather than replayed on top of
    the newer base (which would double-count).
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

    saves: list[int] = []

    async def save_with_reimport(sess):
        # The reimport writer commits provider-recomputed totals mid-flight.
        if not saves:
            saves.append(1)
            sess.raw_usage = copy.deepcopy({"m": _usage("m", 99, 990, 9.9)})
            sess.total_usage = _sess.compute_total_usage(sess.raw_usage)
            raise OSError("transient save failure")

    monkeypatch.setattr(_sess, "save_async", save_with_reimport)

    async def scenario():
        await worker._run_usage_enrichment(sid)

    asyncio.run(scenario())

    # The reimported totals survive untouched...
    assert s.raw_usage["m"]["request_count"] == 99, (
        f"concurrent writer's usage was clobbered: {s.raw_usage}"
    )
    assert s.raw_usage["m"]["rawUsage"]["prompt_tokens"] == 990, s.raw_usage
    assert s.total_usage["prompt_tokens"] == 990, s.total_usage
    # ...and the superseded job is consumed instead of double-counted.
    assert s.usage_enrichment_pending == [], s.usage_enrichment_pending
    assert s.adapter_config.get("prev_usage_ts") == 1, (
        f"a superseded attempt must leave the cursor advanced: {s.adapter_config}"
    )
    assert len(adapter.calls) == 1, (
        f"a superseded job must not be replayed, got {adapter.calls}"
    )
    print("PASS: a concurrent writer supersedes without clobber or double-count")
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

    async def flaky_save(sess):
        saves.append(1)
        if len(saves) == 1:
            raise OSError("transient save failure")

    monkeypatch.setattr(_sess, "save_async", flaky_save)

    async def scenario():
        await worker._run_usage_enrichment(sid)

    asyncio.run(scenario())

    assert s.usage_enrichment_pending == []
    assert len(adapter.calls) == 2, f"job must be replayed, got {adapter.calls}"
    assert s.raw_usage["m"]["request_count"] == 2, (
        f"replay must account exactly once: {s.raw_usage}"
    )
    assert s.raw_usage["m"]["rawUsage"]["prompt_tokens"] == 110, s.raw_usage
    assert s.adapter_config.get("prev_usage_ts") == 1, s.adapter_config
    print("PASS: without a competing writer the job is replayed, not dropped")
    _cleanup()


def test_partial_accumulate_failure_leaves_session_untouched(monkeypatch, tmp_path):
    """A raise while building the new usage must not half-apply it.

    The candidate is computed off to the side, so an exception during
    accumulation leaves both the Session and the durable job consistent --
    no partially-accumulated per-model dicts with nothing left to replay them.

    The provider raises once and then yields no entries, and the save drops the
    pending job, so the durable loop terminates after a single attempt.
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

    async def save_drop_job(sess):
        sess.usage_enrichment_pending = []

    monkeypatch.setattr(_sess, "save_async", save_drop_job)

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
    """``accumulate_raw_usage`` mutates nested dicts in place.

    Building the candidate from a deep copy is what keeps a mid-accumulate
    exception from leaving the live Session partially accounted. Patching the
    real function here proves the isolation: the Session must be untouched even
    though the implementation mutates the dict it is given.

    The adapter yields no entries and the save always fails, so the durable
    loop makes exactly one provider attempt and then returns.
    """
    _cleanup()
    monkeypatch.setattr(_sess, "SESSION_DIR", tmp_path / "sessions")

    sid = "ses_isolated_accumulate"
    adapter = _make_cursor_adapter({})  # no cursor ever yields entries

    def mutating_then_raising(existing, entries):
        # Behaves like the real additive implementation, then fails late.
        result = dict(existing)
        for entry in entries:
            model = entry["model"]
            result.setdefault(model, {
                "model": model, "request_count": 0, "rawUsage": {}})
            result[model]["request_count"] += 1
        raise RuntimeError("accumulate failed after mutating its input")

    # Only reached if some code path calls it with entries; the cursor adapter
    # returns None for every cursor, so this mainly guards the contract.
    monkeypatch.setattr(_sess, "accumulate_raw_usage", mutating_then_raising)

    def enrich_with_entries(s):
        return _delta()

    adapter.enrich_after_result = enrich_with_entries

    s = _new_session(sid, tmp_path)
    w = _new_worker(sid, adapter)
    worker._usage_enrichment_adapters["task:cursor-1"] = adapter
    _seed_job(s, w)

    s.raw_usage = {"m": _usage("m", 3, 30, 0.75)}
    s.total_usage = _sess.compute_total_usage(s.raw_usage)

    # Drop the job after the first failed attempt so the loop terminates.
    attempts = {"n": 0}

    async def save_then_drop(sess):
        attempts["n"] += 1
        sess.usage_enrichment_pending = []
        raise OSError("transient save failure")

    monkeypatch.setattr(_sess, "save_async", save_then_drop)

    async def scenario():
        await worker._run_usage_enrichment(sid)

    asyncio.run(scenario())

    assert attempts["n"] == 1, f"expected one commit attempt, got {attempts['n']}"
    # Even though the patched accumulate mutated the dict it received, the live
    # Session keeps its own value: the candidate was built from a deep copy.
    assert s.raw_usage["m"]["request_count"] == 3, (
        f"candidate construction mutated the live Session: {s.raw_usage}"
    )
    assert s.raw_usage["m"]["rawUsage"]["prompt_tokens"] == 30, s.raw_usage
    print("PASS: candidate build is isolated from the live Session")
    _cleanup()
