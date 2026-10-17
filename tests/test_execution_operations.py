"""Per-operation aggregate and atomic owner-cohort recovery contracts."""
import asyncio
import copy

import pytest

from packages.core import legal_offline as offline, session as sess, worker
from tests.test_legal_offline import setup, begin, runtime_registry, reload


@pytest.mark.parametrize("error", ["done", "429"])
def test_foreground_terminal_cannot_retire_auxiliary(error):
    async def run():
        s, w = setup()
        identity = await begin(s, w)
        async with worker.queue_lock(s.id):
            await worker._commit_offline(s, lambda c: offline.admit(c, identity,
                kind="command/exec", operation_id="command"))
        seq = s.task_seq
        await worker._persist_terminal_state(w, s, "error" if error == "429" else "done", error)
        assert not s.legal_offline and not offline.is_illegal(s)
        assert s.task_seq == seq
        async with worker.queue_lock(s.id):
            await worker._commit_offline(s, lambda c: offline.finish_operation(c, "command", identity,
                reason="deferred-command-final"))
        assert s.legal_offline and reload(s).legal_offline
    asyncio.run(run())


def test_owner_death_after_safe_foreground_one_cohort_no_expansion():
    async def run():
        s, w = setup(["A"])
        identity = await begin(s, w)
        async with worker.queue_lock(s.id):
            def admit(c):
                for key in ("c1", "c2"):
                    offline.admit(c, identity, kind="command/exec", operation_id=key)
            await worker._commit_offline(s, admit)
        await worker._persist_terminal_state(w, s, "done", "ok")
        w.process.returncode = 1
        await worker._detect_offline_exit(w, identity, "kill")
        assert offline.is_illegal(s)
        assert set(s.illegal_batches) == {"c1", "c2"}
        assert s.illegal_batches["c1"]["messages"] == ["A"]
        assert s.illegal_batches["c2"]["messages"] == []
        s.queue_pending.append(dict(id="B", type="task", deliveryState="queued"))
        await worker._detect_offline_exit(w, identity, "late-kill")
        assert "queueLockSource" not in s.queue_pending[-1]
        assert reload(s).execution_operations["c1"]["state"] == "illegal"
    asyncio.run(run())


def test_v1_migration_stable_and_v2_without_foreground_preserves_false():
    identity = dict(token="old", generation=1, executionSeq=2)
    legacy = dict(id="stable", state="running", **identity)
    s = sess.Session(id="legacy", name="legacy", legal_offline=False, offline_run=legacy)
    assert s.execution_operations["stable"]["id"] == "stable"
    data = s.to_dict()
    data["offline_run"] = None
    loaded = sess.Session._from_data(copy.deepcopy(data))
    assert not loaded.legal_offline and loaded.offline_run is None
    assert loaded.execution_operations == s.execution_operations
    assert sess.Session._from_data(loaded.to_dict()).operation_revision == loaded.operation_revision


def test_terminal_replace_failure_does_not_publish_ledger(monkeypatch):
    async def run():
        s, w = setup()
        await begin(s, w)
        before = copy.deepcopy(s.execution_operations)
        original = sess.os.replace
        def fail(src, dest):
            if str(dest).endswith(s.id + ".json"):
                raise OSError("replace injected")
            return original(src, dest)
        monkeypatch.setattr(sess.os, "replace", fail)
        with pytest.raises(OSError, match="replace injected"):
            await worker._persist_terminal_state(w, s, "done", "ok")
        assert s.execution_operations == before and not s.legal_offline
    asyncio.run(run())
