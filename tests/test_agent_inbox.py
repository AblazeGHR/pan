"""Deterministic shared-inbox concurrency and recovery contract tests."""
import asyncio
import pytest
from packages.core.agent_inbox import Inbox, InboxError, Router


def run(coro):
    return asyncio.run(coro)


class Queue:
    def __init__(self):
        self.rows = {}
        self.fail = set()
        self.crash = False
        self.calls = []

    async def lookup(self, sid, qid):
        return self.rows.get((sid, qid))

    async def put(self, sid, qid, message, notification=False):
        self.calls.append((sid, qid))
        if sid in self.fail:
            return {"status": "rejected"}
        self.rows[sid, qid] = {"status": "accepted", "queueItemId": qid}
        if self.crash:
            raise asyncio.CancelledError()
        return self.rows[sid, qid]


def setup(tmp_path, capacity=500):
    q = Queue()
    box = Inbox(tmp_path / "inbox.sqlite", q, capacity=capacity)
    lid = box.locker({"pluginId": "qq", "instanceId": "one", "botId": "bot", "scope": "user", "peerId": "7"})["id"]
    return box, q, lid


def test_shared_idempotency_capacity_and_conflict(tmp_path):
    b, q, lid = setup(tmp_path, 1)
    first = b.receive(lid, "native-1", {"text": "hello", "parts": [{"type": "file", "path": "a"}]})
    assert b.receive(lid, "native-1", first["content"])["id"] == first["id"]
    with pytest.raises(InboxError, match="conflict"):
        b.receive(lid, "native-1", {"text": "changed"})
    with pytest.raises(InboxError, match="capacity"):
        b.receive(lid, "native-2", {"text": "second"})
    assert b.consume(lid, [first["id"]])["consumed"] == [first["id"]]
    assert b.read(lid) == []
    assert b.read(lid, include_consumed=True)[0]["content"]["parts"] == first["content"]["parts"]


def test_partial_failure_fixed_recipients_and_manual_claim(tmp_path):
    b, q, lid = setup(tmp_path)
    b.subscribe(lid, "a", "full")
    b.subscribe(lid, "b", "full")
    m = b.receive(lid, "1", {"text": "body"})
    q.fail.add("b")
    run(b.forward(lid))
    assert len(q.rows) == 1
    assert b.consume(lid, [m["id"]])["busy"] == [m["id"]]
    b.subscribe(lid, "c", "full")
    q.fail.clear()
    run(b.forward(lid))
    assert sorted(s for s, _ in q.rows) == ["a", "b"]
    assert b.read(lid) == []
    assert len([s for s, _ in q.calls if s == "a"]) == 1


def test_queue_commit_then_crash_cold_recovery(tmp_path):
    b, q, lid = setup(tmp_path)
    b.subscribe(lid, "a", "full")
    b.receive(lid, "1", {"text": "body"})
    q.crash = True
    with pytest.raises(asyncio.CancelledError):
        run(b.forward(lid))
    q.crash = False
    cold = Inbox(tmp_path / "inbox.sqlite", q)
    run(cold.forward(lid))
    assert cold.read(lid) == []
    assert len(q.calls) == 1


def test_unknown_never_replays(tmp_path):
    b, q, lid = setup(tmp_path)
    b.subscribe(lid, "a", "full")
    b.receive(lid, "1", {"text": "body"})
    q.crash = True
    with pytest.raises(asyncio.CancelledError):
        run(b.forward(lid))
    q.rows.clear()  # receipt lost/expired: absence does not prove non-acceptance
    q.crash = False
    run(Inbox(tmp_path / "inbox.sqlite", q).forward(lid))
    assert len(q.calls) == 1
    assert b.read(lid)[0]["deliveries"]["a"]["status"] == "unknown"


def test_new_boundary_notification_backlog_disable(tmp_path):
    b, q, lid = setup(tmp_path)
    old = b.receive(lid, "old", {"text": "secret"})
    b.subscribe(lid, "a", "notification")
    run(b.forward(lid))
    assert not q.rows
    b.receive(lid, "new", {"text": "secret2"})
    run(b.forward(lid))
    assert len(q.rows) == 1
    assert len(b.read(lid)) == 2
    b.subscribe(lid, "a", "full")
    run(b.forward(lid))
    assert len(q.rows) == 1
    run(b.forward(lid, backlog=True))
    assert b.read(lid) == []
    b.set_available("qq", False)
    with pytest.raises(InboxError, match="unavailable"):
        b.receive(lid, "third", {"text": "no"})
    assert b.list()[0]["available"] is False


def test_concurrent_manual_and_automatic(tmp_path):
    b, q, lid = setup(tmp_path)
    b.subscribe(lid, "a", "full")
    m = b.receive(lid, "1", {"text": "x"})
    async def race():
        await asyncio.gather(b.forward(lid), b.forward(lid))
    run(race())
    assert len(q.calls) == 1
    assert b.consume(lid, [m["id"]])["consumed"] == []


def test_router_user_extension_rejects(tmp_path):
    b, q, lid = setup(tmp_path)
    with pytest.raises(InboxError, match="unsupported"):
        run(Router(b).route({"kind": "user_inbox"}, "1", {"text": "x"}))

def test_bot_scope_instance_isolation(tmp_path):
    b, q, lid = setup(tmp_path)
    source = b.list()[0]["source"]
    lids = {lid}
    for key, value in [("botId", "other"), ("scope", "group"), ("instanceId", "two")]:
        lids.add(b.locker({**source, key: value})["id"])
    assert len(lids) == 4
    for key in lids:
        b.receive(key, "same-native", {"text": "x"})
    assert len({b.read(key)[0]["id"] for key in lids}) == 4


def test_legacy_migration_preserves_original_and_never_resurrects(tmp_path):
    import json
    from packages.core.inbox_service import migrate_qq
    b, q, _ = setup(tmp_path)
    root = tmp_path / "qq_inbox"
    root.mkdir()
    path = root / "7.json"
    original = json.dumps([{"id": "old-synthetic", "scope": "group", "target_id": "7", "text": "old"}])
    path.write_text(original, encoding="utf-8")
    migrate_qq(b, root)
    lid = next(r["id"] for r in b.list() if r["source"]["botId"] == "")
    row = b.read(lid)[0]
    assert row["identityKind"] == "legacy"
    assert row["sourceMessageId"].startswith("legacy_")
    b.consume(lid, [row["id"]])
    migrate_qq(b, root)
    assert b.read(lid) == []
    assert path.read_text(encoding="utf-8") == original


def test_legacy_subscription_maps_full_without_old_backlog(tmp_path, monkeypatch):
    from packages.core.inbox_service import sync_qq_subscriptions
    from packages.core import worker
    from types import SimpleNamespace
    b, q, lid = setup(tmp_path)
    b.receive(lid, "old", {"text": "old"})
    monkeypatch.setattr(worker, "_list_sessions_shallow", lambda: [SimpleNamespace(id="a", qq_subscriptions={"user:7@bot"})])
    sync_qq_subscriptions(b, lid)
    run(b.forward(lid))
    assert not q.rows
    b.receive(lid, "new", {"text": "new"})
    run(b.forward(lid))
    assert len(q.rows) == 1
    assert [r["content"]["text"] for r in b.read(lid)] == ["old"]


def test_notification_manual_race_does_not_resurrect(tmp_path):
    b, q, lid = setup(tmp_path)
    b.subscribe(lid, "a", "notification")
    m = b.receive(lid, "1", {"text": "secret"})
    put = q.put
    async def consume_first(*args, **kwargs):
        assert b.consume(lid, [m["id"]])["consumed"] == [m["id"]]
        assert kwargs["notification"] is True
        return await put(*args, **kwargs)
    q.put = consume_first
    run(b.forward(lid))
    assert b.read(lid) == []


def test_manual_first_blocks_automatic_claim(tmp_path):
    b, q, lid = setup(tmp_path)
    b.subscribe(lid, "a", "full")
    m = b.receive(lid, "1", {"text": "x"})
    b.consume(lid, [m["id"]])
    run(b.forward(lid))
    assert not q.calls


def test_two_inbox_instances_only_one_put(tmp_path):
    b, q, lid = setup(tmp_path)
    other = Inbox(tmp_path / "inbox.sqlite", q)
    b.subscribe(lid, "a", "full")
    b.receive(lid, "1", {"text": "x"})
    async def race():
        await asyncio.gather(b.forward(lid), other.forward(lid))
    run(race())
    assert len(q.calls) == 1


def test_direct_queue_route_idempotency_conflict_and_unknown(tmp_path):
    b, q, lid = setup(tmp_path)
    router = Router(b)
    target = {"kind": "session_queue", "sessionId": "a", "source": b.list()[0]["source"]}
    run(router.route(target, "1", {"text": "x"}))
    run(router.route(target, "1", {"text": "x"}))
    assert len(q.calls) == 1
    with pytest.raises(InboxError, match="conflict"):
        run(router.route(target, "1", {"text": "changed"}))
    q.crash = True
    with pytest.raises(asyncio.CancelledError):
        run(router.route(target, "2", {"text": "crash"}))
    q.rows.clear()
    q.crash = False
    assert run(router.route(target, "2", {"text": "crash"}))["status"] == "unknown"
    assert len(q.calls) == 2

def test_real_queue_durable_commit_then_locker_interruption(tmp_path, monkeypatch):
    from packages.core.inbox_service import get_inbox, SessionQueue, qq_source
    from packages.core import session, worker
    from unittest.mock import AsyncMock
    monkeypatch.setattr(worker, "_wake_worker", AsyncMock())
    monkeypatch.setattr(worker, "_bcast", AsyncMock())
    s = session.create("real-queue", workdir=str(tmp_path))
    box = get_inbox()
    lid = box.locker(qq_source("b", "group", "7"))["id"]
    box.subscribe(lid, s.id, "full")
    m = box.receive(lid, "native", {"text": "durable", "parts": [{"type": "text", "text": "durable"}]})
    original = box.queue.put
    async def crash_after_commit(*args, **kwargs):
        result = await original(*args, **kwargs)
        assert result["status"] == "accepted"
        raise asyncio.CancelledError()
    monkeypatch.setattr(box.queue, "put", crash_after_commit)
    with pytest.raises(asyncio.CancelledError):
        run(box.forward(lid))
    import json
    disk = json.loads((session.SESSION_DIR / f"{s.id}.json").read_text(encoding="utf-8"))
    assert len(disk["queue_pending"]) == 1
    qid = disk["queue_pending"][0]["id"]
    assert disk["queue_pending"][0]["messageId"] == m["id"]
    session._cache.clear()
    session._all_loaded = False
    cold = Inbox(box.path, SessionQueue())
    run(cold.forward(lid))
    assert cold.read(lid) == []
    restored = session.get(s.id)
    assert [r["id"] for r in restored.queue_pending] == [qid]
    assert restored.queue_pending[0]["parts"] == m["content"]["parts"]


def test_disabled_source_retains_pending_and_blocks_forward(tmp_path):
    b, q, lid = setup(tmp_path)
    b.subscribe(lid, "a", "full")
    b.receive(lid, "1", {"text": "retained"})
    b.set_available("qq", False)
    assert run(b.forward(lid))["status"] == "processed"
    assert not q.calls
    assert b.read(lid)[0]["content"]["text"] == "retained"
    fresh = b.locker({**b.list()[0]["source"], "peerId": "different"})
    assert fresh["available"] is False


def test_source_identity_conflict_across_router_destinations(tmp_path):
    b, q, lid = setup(tmp_path)
    b.receive(lid, "1", {"text": "original"})
    with pytest.raises(InboxError, match="conflict"):
        run(Router(b).route({"kind": "session_queue", "sessionId": "a", "source": b.list()[0]["source"]}, "1", {"text": "changed"}))

def test_notification_real_queue_contains_no_body_and_does_not_consume(tmp_path, monkeypatch):
    from packages.core.inbox_service import get_inbox, qq_source
    from packages.core import session, worker
    from unittest.mock import AsyncMock
    monkeypatch.setattr(worker, "_wake_worker", AsyncMock())
    monkeypatch.setattr(worker, "_bcast", AsyncMock())
    s = session.create("notification", workdir=str(tmp_path))
    b = get_inbox()
    lid = b.locker(qq_source("b", "user", "1"))["id"]
    b.subscribe(lid, s.id, "notification")
    m = b.receive(lid, "native", {"text": "UNIQUE_SECRET_BODY", "parts": [{"type": "text", "text": "UNIQUE_SECRET_PART"}]})
    run(b.forward(lid))
    import json
    disk = json.loads((session.SESSION_DIR / f"{s.id}.json").read_text(encoding="utf-8"))
    assert "UNIQUE_SECRET" not in json.dumps(disk["queue_pending"])
    assert "parts" not in disk["queue_pending"][0]
    assert b.read(lid)[0]["id"] == m["id"]


def test_mcp_existing_managed_permission_and_missing_identity(monkeypatch):
    from packages.mcp import server
    calls = []
    caller = {"id": "self", "managed": [], "panAccess": {"restrictToManaged": True}}
    monkeypatch.setattr(server, "_caller_identity", lambda: caller)
    monkeypatch.setattr(server, "_api", lambda *args: calls.append(args) or {})
    result = server.agent_inbox(operation="subscribe", locker_id="locker", session_id="outsider", mode="full")
    assert result["error"]["code"] == "permission_denied" and calls == []
    monkeypatch.setattr(server, "_caller_identity", lambda: None)
    assert server.agent_inbox()["error"]["code"] == "missing_identity"
    assert calls == []


def test_frontend_schema_actual_payload(tmp_path):
    import json
    from pathlib import Path
    import jsonschema
    b, q, lid = setup(tmp_path)
    schema = json.loads((Path(__file__).parents[1] / "docs/design/Agent-Inbox-Router-Locker.schema.json").read_text(encoding="utf-8-sig"))
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate({"schemaVersion": 1, "lockers": b.list()}, schema)
    b.subscribe(lid, "a", "full")
    b.receive(lid, "1", {"text": "x", "parts": [{"type": "text", "text": "x"}]})
    q.fail.add("a")
    run(b.forward(lid))
    for name, value in [("Message", b.read(lid)[0]), ("Subscription", b.subscriptions(lid)[0])]:
        jsonschema.validate(value, {"$ref": "#/$defs/" + name, "$defs": schema["$defs"]})


def test_uninstall_reconciliation_disables_but_retains(tmp_path, monkeypatch):
    from packages.core import inbox_service
    from packages.core.manifest_loader import load_manifests
    b, q, lid = setup(tmp_path)
    b.receive(lid, "1", {"text": "keep"})
    monkeypatch.setattr(inbox_service, "get_inbox", lambda: b)
    inbox_service.reconcile_sources(load_manifests([]))
    assert b.list()[0]["available"] is False
    assert b.read(lid)[0]["content"]["text"] == "keep"
    with pytest.raises(InboxError, match="unavailable"):
        run(Router(b).route({"kind": "session_queue", "sessionId": "a", "source": b.list()[0]["source"]}, "2", {"text": "no"}))


def test_legacy_oversize_is_diagnostic_without_truncation(tmp_path):
    from packages.core.inbox_service import migrate_qq
    import json
    b, q, lid = setup(tmp_path, 1)
    root = tmp_path / "legacy"
    root.mkdir()
    path = root / "7.json"
    original = json.dumps([{"text": "one"}, {"text": "two"}])
    path.write_text(original, encoding="utf-8")
    with pytest.raises(InboxError, match="capacity"):
        migrate_qq(b, root)
    assert path.read_text(encoding="utf-8") == original

def test_multinstance_inflight_and_manual_claim_barrier(tmp_path):
    b, q, lid = setup(tmp_path)
    other = Inbox(b.path, q)
    b.subscribe(lid, "a", "full")
    m = b.receive(lid, "1", {"text": "body"})
    async def race():
        started, release = asyncio.Event(), asyncio.Event()
        original = q.put
        async def delayed(*args, **kwargs):
            started.set()
            await release.wait()
            return await original(*args, **kwargs)
        q.put = delayed
        first = asyncio.create_task(b.forward(lid))
        await started.wait()
        assert other.consume(lid, [m["id"]])["busy"] == [m["id"]]
        await other.forward(lid)
        assert other.read(lid)[0]["deliveries"]["a"]["status"] == "unknown"
        release.set()
        await first
    run(race())
    assert len(q.calls) == 1 and b.read(lid) == []


def test_explicit_delete_keeps_identity_tombstone(tmp_path):
    b, q, lid = setup(tmp_path)
    source = b.list()[0]["source"]
    b.receive(lid, "1", {"text": "delete-body"})
    b.delete(lid)
    assert b.list() == []
    b.locker(source)
    with pytest.raises(InboxError, match="previously deleted"):
        b.receive(lid, "1", {"text": "delete-body"})
    with b.db() as db:
        assert "delete-body" not in db.execute("SELECT content FROM origins").fetchone()[0]

def test_legacy_defaults_apply_before_multiple_arrivals_or_late_notify(tmp_path, monkeypatch):
    from packages.core.inbox_service import sync_qq_patterns, sync_qq_subscriptions
    from packages.core import worker
    from types import SimpleNamespace
    b, q, _ = setup(tmp_path)
    monkeypatch.setattr(worker, "_list_sessions_shallow", lambda: [SimpleNamespace(id="a", qq_subscriptions={"group:other@bot"})])
    sync_qq_patterns(b)
    source = {**b.list()[0]["source"], "scope": "group", "peerId": "other"}
    lid = b.locker(source)["id"]
    b.receive(lid, "first", {"text": "first"})
    b.receive(lid, "second", {"text": "second"})
    # The notify could arrive much later; it must neither skip the first nor replay old backlog.
    sync_qq_subscriptions(b, lid)
    run(b.forward(lid))
    assert len(q.rows) == 2 and b.read(lid) == []

def test_full_acceptance_does_not_wait_for_worker_startup(tmp_path, monkeypatch):
    from packages.core.inbox_service import get_inbox, qq_source
    from packages.core import session, worker
    from unittest.mock import AsyncMock
    monkeypatch.setattr(worker, "_bcast", AsyncMock())
    s = session.create("startup-blocked", workdir=str(tmp_path))
    b = get_inbox()
    lid = b.locker(qq_source("b", "user", "7"))["id"]
    b.subscribe(lid, s.id, "full")
    b.receive(lid, "1", {"text": "body"})
    async def check():
        entered, release = asyncio.Event(), asyncio.Event()
        async def blocked(*args, **kwargs):
            entered.set()
            await release.wait()
        monkeypatch.setattr(worker, "_wake_worker", blocked)
        await asyncio.wait_for(b.forward(lid), timeout=1)
        assert b.read(lid) == [] and len(s.queue_pending) == 1
        await entered.wait()
        release.set()
    run(check())

def test_notification_and_full_subscribers_both_match(tmp_path):
    b, q, lid = setup(tmp_path)
    b.subscribe(lid, "full", "full")
    b.subscribe(lid, "notify", "notification")
    b.receive(lid, "1", {"text": "body"})
    q.fail.add("notify")
    run(b.forward(lid))
    assert sorted(s for s, _ in q.calls) == ["full", "notify"]
    assert b.read(lid) == []  # notification failure cannot prevent full shared consumption
    state = b.read(lid, include_consumed=True)[0]
    assert state["deliveries"]["full"]["status"] == "accepted"
    assert state["deliveries"]["notification:notify"]["status"] == "failed"

def test_queue_router_enforces_same_source_schema_as_locker(tmp_path):
    b, q, lid = setup(tmp_path)
    with pytest.raises(InboxError, match="invalid source"):
        run(Router(b).route({"kind": "session_queue", "sessionId": "a", "source": {"pluginId": "qq"}}, "1", {"text": "x"}))
    assert not q.calls
