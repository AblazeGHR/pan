"""Offline native-shape fixtures through real adapters and HTTP import handler.

Only native storage reads are substituted. Accounting, cursors, commit tickets,
handler coordination and disk reload are production code. Events fix the lookup
and save windows; no provider CLI, service, or user directory is accessed.
"""
import asyncio
import copy
import importlib
import threading

import pytest

from packages.core import session as sess, worker
from packages.web import server


def native_entries(kind, fresh):
    n = 110 if fresh else 10
    if kind == "codex":
        raw = dict(input_tokens=n, output_tokens=n // 10,
                   reasoning_output_tokens=n // 10, cached_input_tokens=n // 2,
                   cache_write_input_tokens=n // 5, total_tokens=n + n // 10)
        return [dict(model="m", rawUsage=raw, timestamp="2026-01-01T00:00:02Z")]
    if kind == "opencode":
        raw = dict(prompt_tokens=n, completion_tokens=n // 10,
                   reasoning_tokens=n // 10, cache_read_tokens=n // 2,
                   cache_write_tokens=n // 5, cost=n / 100)
        return [dict(model="m", rawUsage=raw, timestamp="2026-01-01T00:00:02Z")]
    entries = [dict(model="m", rawUsage=dict(prompt_tokens=10, completion_tokens=1,
                                            cache_read_tokens=5, credit=0.1),
                    timestamp="2026-01-01T00:00:01Z")]
    if fresh:
        entries.append(dict(model="m", rawUsage=dict(prompt_tokens=100,
                            completion_tokens=10, cache_read_tokens=50, credit=1),
                            timestamp="2026-01-01T00:00:02Z"))
    return entries


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(sess, "SESSION_DIR", tmp_path / "sessions")
    sess._cache.clear()
    worker._usage_enrichment_adapters.clear()
    worker._usage_enrichment_locks.clear()
    monkeypatch.setattr(worker, "_ENRICH_RETRY_BASE_SEC", 0)
    monkeypatch.setattr(worker, "_ENRICH_RETRY_MAX_SEC", 0)
    monkeypatch.setattr(worker, "find_alive_worker_by_session", lambda sid: None)
    monkeypatch.setattr(worker, "find_worker_by_session", lambda sid: None)
    async def quiet(*args, **kwargs):
        pass
    monkeypatch.setattr(server, "broadcast", quiet)
    yield
    sess._cache.clear()
    worker._usage_enrichment_adapters.clear()
    worker._usage_enrichment_locks.clear()


def setup_native(monkeypatch, kind):
    module = importlib.import_module(f"packages.core.adapters.{kind}.adapter")
    storage = importlib.import_module(f"packages.core.adapters.{kind}.sessions")
    cls = getattr(module, {"codex": "CodexAdapter", "opencode": "OpencodeAdapter",
                           "kimi": "KimiAdapter", "cbc": "CbcAdapter"}[kind])
    adapter = cls.__new__(cls)  # avoid CLI discovery; only real enrich is called
    monkeypatch.setattr(storage, "get_raw_usage",
                        lambda *args, **kwargs: copy.deepcopy(native_entries(kind, True)))
    if kind == "cbc":
        monkeypatch.setattr(cls, "_find_project_dir", staticmethod(lambda sid: ("fixture", "fixture")))
    return adapter


def new_session(kind):
    s = sess.Session(id=f"ses_native_{kind}", name="native", adapter=kind)
    s.cli_session_id = "fixture-native"
    s.history = [{"role": "user", "content": "fixture"}]
    sess._cache[s.id] = s
    old = native_entries(kind, False)
    sess.replace_usage_totals(s, sess.accumulate_raw_usage(
        None, sess.normalize_native_usage_entries(kind, old)), native_entries=old)
    s.usage_enrichment_pending = [dict(key=key, adapter=kind, state="pending",
        attempts=0, nextAttemptAt=0) for key in ("first", "second")]
    return s


def values(s):
    raw = s.raw_usage["m"]["rawUsage"]
    return raw["prompt_tokens"], raw["cache_read_tokens"]


@pytest.mark.parametrize("kind", ["codex", "opencode", "kimi", "cbc"])
@pytest.mark.parametrize("window", ["lookup", "save"])
@pytest.mark.parametrize("fresh", [False, True])
@pytest.mark.parametrize("fail", [False, True])
def test_native_reimport_interleavings(isolated, monkeypatch, kind, window, fresh, fail):
    adapter = setup_native(monkeypatch, kind)
    s = new_session(kind)
    for key in ("first", "second"):
        worker._usage_enrichment_adapters[key] = adapter
    entered, release = threading.Event(), threading.Event()
    original_enrich = adapter.enrich_after_result
    original_save = sess._save_body
    calls = []
    writes = []
    imports = []

    def enrich(snapshot):
        result = original_enrich(snapshot)
        calls.append(copy.deepcopy(snapshot.adapter_config))
        if window == "lookup" and len(calls) == 1:
            entered.set()
            assert release.wait(10), "lookup barrier timed out"
        return result

    def save(session, **kwargs):
        writes.append(1)
        if window == "save" and len(writes) == 1:
            entered.set()
            assert release.wait(10), "save barrier timed out"
            if fail:
                raise OSError("enrichment write failed")
        return original_save(session, **kwargs)

    class Provider:
        def parse_history(self, *args):
            return [{"role": "user", "content": "fixture"}]

        def get_raw_usage(self, *args):
            imports.append(1)
            # During a save, import must wait BEFORE the native read. An old
            # view available before the gate must never be captured for later.
            return copy.deepcopy(native_entries(kind, fresh if window == "lookup" else True))

    adapter.enrich_after_result = enrich
    monkeypatch.setattr(sess, "_save_body", save)

    async def scenario():
        # Save initial pending jobs with the real body, not the barrier double.
        await sess._persist_async(s.id, lambda: original_save(s))
        # Exercise the summary-cache hydration path used by real reimport.
        s._history_loaded = False
        original_gate = sess.usage_commit_lock(s)
        task = asyncio.create_task(worker._run_usage_enrichment(s.id))
        try:
            assert await asyncio.to_thread(entered.wait, 10)
            if window == "lookup":
                # fail here means the HTTP replacement itself fails durably.
                if fail:
                    def fail_import(session, **kwargs):
                        raise OSError("reimport write failed")
                    monkeypatch.setattr(sess, "_save_body", fail_import)
                    with pytest.raises(OSError, match="reimport"):
                        await server._import_session(Provider(), kind, {"session_id": s.cli_session_id})
                    assert values(s) == (10, 5)
                    assert len(s.usage_enrichment_pending) == 2
                    monkeypatch.setattr(sess, "_save_body", save)
                else:
                    result = await server._import_session(Provider(), kind, {"session_id": s.cli_session_id})
                    assert result["reimported"]
                release.set()
            else:
                imported = asyncio.create_task(server._import_session(
                    Provider(), kind, {"session_id": s.cli_session_id}))
                await asyncio.sleep(0)  # let the handler reach the occupied gate
                assert not imports, "import read native usage before taking gate"
                assert not imported.done()
                release.set()
                assert (await imported)["reimported"]
            await asyncio.wait_for(task, 10)
            assert sess.usage_commit_lock(s) is original_gate
        finally:
            release.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
    assert values(s) == (110, 55), s.raw_usage
    assert s.total_usage["prompt_tokens"] == 110
    assert s.total_usage["cache_hit_tokens"] == 55
    assert s.total_usage["completion_tokens"] == 11
    assert s.usage_enrichment_pending == []
    if kind == "opencode":
        assert s.raw_usage["m"]["rawUsage"]["cost"] == pytest.approx(1.1)
        assert s.adapter_config["opencode_prev_usage"]["cost"] == pytest.approx(1.1)
    if kind in {"kimi", "cbc"}:
        assert s.raw_usage["m"]["request_count"] == 2
    if window == "lookup":
        assert len(calls) == 3, "invalidated lookup must re-read and then drain second job"
    sess._cache.pop(s.id)
    restarted = sess.get(s.id)
    assert restarted.raw_usage == s.raw_usage
    assert restarted.adapter_config == s.adapter_config
    assert restarted.usage_enrichment_pending == []


def test_queued_metadata_save_cannot_persist_failed_candidate(isolated, monkeypatch):
    """A second real save ticket must observe rollback before it can serialize."""
    adapter = setup_native(monkeypatch, "opencode")
    s = new_session("opencode")
    worker._usage_enrichment_adapters["first"] = adapter
    entered, release = threading.Event(), threading.Event()
    original_save = sess._save_body
    writes = []

    def blocked_failure(session, **kwargs):
        writes.append(1)
        if len(writes) == 1:
            entered.set()
            assert release.wait(10)
            raise OSError("failed candidate")
        return original_save(session, **kwargs)

    monkeypatch.setattr(sess, "_save_body", blocked_failure)

    async def scenario():
        await sess._persist_async(s.id, lambda: original_save(s))
        enrichment = asyncio.create_task(worker._run_usage_enrichment(s.id))
        assert await asyncio.to_thread(entered.wait, 10)
        metadata = asyncio.create_task(sess.save_async(s))
        await asyncio.sleep(0)
        enrichment.cancel()
        enrichment.cancel()  # repeated cancellation must still retire the writer
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await enrichment
        await metadata

    asyncio.run(scenario())
    assert values(s) == (10, 5)
    assert len(s.usage_enrichment_pending) == 2
    sess._cache.pop(s.id)
    restarted = sess.get(s.id)
    assert restarted.raw_usage == s.raw_usage
    assert restarted.total_usage == s.total_usage
    assert restarted.adapter_config == s.adapter_config
    assert restarted.usage_enrichment_pending == s.usage_enrichment_pending


@pytest.mark.parametrize("kind", ["codex", "opencode", "kimi", "cbc"])
def test_new_native_import_starts_with_accounted_cursor(isolated, monkeypatch, tmp_path, kind):
    adapter = setup_native(monkeypatch, kind)
    class Provider:
        def parse_history(self, *args):
            return [{"role": "user", "content": "fixture"}]
        def get_raw_usage(self, *args):
            return copy.deepcopy(native_entries(kind, True))
        def get_session_title(self, *args):
            return "native-import"
    monkeypatch.setattr(server, "_build_session_params", lambda *args, **kwargs: {})
    async def scenario():
        result = await server._import_session(Provider(), kind,
            {"session_id": "new-native", "cwd": str(tmp_path)})
        assert "error" not in result
    asyncio.run(scenario())
    s = next(s for s in sess._cache.values() if s.cli_session_id == "new-native")
    assert s.total_usage["prompt_tokens"] == 110
    assert s.total_usage["cache_hit_tokens"] == 55
    assert adapter.enrich_after_result(worker._make_usage_enrichment_snapshot(s)) is None
    sess._cache.pop(s.id)
    assert sess.get(s.id).adapter_config == s.adapter_config


def test_terminal_append_during_job_removal_is_drained(isolated, monkeypatch):
    """Pause the writer's list snapshot while the real terminal path appends."""
    adapter = setup_native(monkeypatch, "opencode")
    s = new_session("opencode")
    entered, release = threading.Event(), threading.Event()
    calls = []
    original_enrich = adapter.enrich_after_result
    def enrich(snapshot):
        calls.append(1)
        return original_enrich(snapshot)
    adapter.enrich_after_result = enrich
    for key in ("first", "second"):
        worker._usage_enrichment_adapters[key] = adapter

    class PausedSnapshotList(list):
        armed = False
        def __iter__(self):
            before = list(list.__iter__(self))
            pause = self.armed
            self.armed = False
            yield from before
            if pause:
                entered.set()
                assert release.wait(10), "append barrier timed out"

    pending = PausedSnapshotList(s.usage_enrichment_pending)
    s.usage_enrichment_pending = pending
    async def scenario():
        await sess.save_async(s)
        pending.armed = True
        task = asyncio.create_task(worker._run_usage_enrichment(s.id))
        try:
            assert await asyncio.to_thread(entered.wait, 10)
            key = worker._queue_usage_enrichment(s, adapter, task_id="third",
                task_seq=3, worker_id="fixture", generation=0)
            release.set()
            await asyncio.wait_for(task, 10)
            assert key not in worker._usage_enrichment_adapters
        finally:
            release.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())
    assert s.usage_enrichment_pending is pending
    assert len(calls) == 3
    assert values(s) == (110, 55)
    sess._cache.pop(s.id)
    assert sess.get(s.id).usage_enrichment_pending == []
