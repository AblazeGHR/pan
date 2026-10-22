"""Thin adapters between shared Inbox, legacy QQ bindings and existing Queue."""
from __future__ import annotations
import json
import asyncio
import logging
from pathlib import Path
from .agent_inbox import Inbox, InboxError, identity
from . import session as sess


class SessionQueue:
    async def lookup(self, sid, qid):
        from . import worker
        s = worker._get_session_shallow(sid)
        if s is None:
            return None
        row = next((r for r in s.queue_pending if r.get("id") == qid), None)
        row = row or (s.queue_delivery_ledger or {}).get(qid)
        return {"status": "accepted", "queueItemId": qid, "fingerprint": row.get("clientMessageId")} if row else None

    async def put(self, sid, qid, message, notification=False):
        from . import worker
        import time
        s = worker._get_session_shallow(sid)
        if s is None or worker._shutdown_started:
            return {"status": "rejected", "error": "session unavailable"}
        content = message["content"]
        fingerprint = identity("inbox_", [message["id"], content, notification])
        async with worker.queue_lock(sid):
            old = await self.lookup(sid, qid)
            if old:
                if old["fingerprint"] != fingerprint:
                    raise InboxError("queue identity conflict")
                return old
            source = message.get("source", {})
            plugin = source.get("pluginId", "plugin")
            target = f"{source.get('scope', '')}:{source.get('peerId', '')}"
            text = (f"Agent Inbox update: {message.get('lockerId', '')}" if notification else content.get("text", ""))
            item = {"id": qid, "queueItemId": qid, "type": "qq" if plugin == "qq" else "notice",
                    "kind": "qq" if plugin == "qq" else "notice", "source": plugin, "sourcePluginId": plugin,
                    "clientMessageId": fingerprint, "messageId": message["id"],
                    "lockerId": message.get("lockerId"), "text": text, "result": text,
                    "sourceMessageId": message.get("sourceMessageId", message["id"]),
                    "inboxSource": source, "sourceIdentityKind": message.get("identityKind", "native"),
                    "channel": plugin, "channelTarget": target, "qqTarget": target,
                    "targetType": source.get("scope"), "targetId": source.get("peerId"),
                    "botUin": source.get("botId", ""), "canReply": not notification,
                    "revision": 1, "createdAt": time.time(), "deliveryState": "queued", "dispatchState": "queued",
                    "inboxNotification": notification}
            if not notification:
                item.update({k: v for k, v in content.items() if k in {"parts", "attachments", "envelope", "nickname", "time"}})
                item["inboxContent"] = content
            persisted = await worker._append_new_queue_item(s, item)
        # Return durable acceptance immediately. Worker startup/CLI readiness
        # must never delay shared consumption or turn it into an unknown result.
        task = asyncio.create_task(_signal_queue(sid, qid, s.queue_revision, dict(persisted)))
        _signals.add(task)
        task.add_done_callback(_signals.discard)
        return {"status": "accepted", "queueItemId": qid, "fingerprint": fingerprint}


_signals = set()


async def _signal_queue(sid, qid, revision, item):
    from . import worker
    try:
        await worker._bcast({"type": "queue.item_added", "sessionId": sid,
                             "queueItemId": qid, "queueRevision": revision, "item": item})
        await worker._wake_worker(sid, auto_spawn=True)
    except Exception:
        logging.getLogger(__name__).warning("Inbox queue signal failed for %s/%s; durable Queue retained", sid, qid, exc_info=True)



_instances = {}


def get_inbox(root=None):
    root = Path(root) if root is not None else sess.SESSION_DIR.parent
    path = root / "agent_inbox" / "inbox.sqlite"
    key = str(path.resolve())
    if key not in _instances:
        _instances[key] = Inbox(path, SessionQueue())
    return _instances[key]


def qq_source(bot="", scope="user", peer="", instance="qq"):
    return {"pluginId": "qq", "instanceId": instance, "botId": str(bot or ""), "scope": scope, "peerId": str(peer)}


def migrate_qq(box, root):
    """Import old JSON without modifying it. A per-file marker prevents resurrection.

    Old synthetic `id` is not a native OneBot ID. Unscoped records retain unknown
    bot provenance; they are never assigned to the currently configured account.
    """
    root = Path(root)
    with box.db() as db:
        db.execute("CREATE TABLE IF NOT EXISTS migrations(path TEXT PRIMARY KEY)")
    for path in sorted(root.rglob("*.json")) if root.exists() else []:
        relative = path.relative_to(root).as_posix()
        with box.db() as db:
            if db.execute("SELECT 1 FROM migrations WHERE path=?", (relative,)).fetchone():
                continue
        rows = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            raise InboxError("invalid legacy QQ inbox: " + relative)
        # Capacity overflow is diagnosed before import, never truncated.
        if len(rows) > box.capacity:
            raise InboxError("legacy inbox exceeds capacity: " + relative)
        for index, row in enumerate(rows):
            source = qq_source(row.get("bot_uin") or (path.parent.name if path.parent != root else ""),
                               row.get("scope", "user"), row.get("target_id", path.stem))
            lid = box.locker(source)["id"]
            mid = identity("legacy_", [relative, index, row])
            box.receive(lid, mid, row, identity_kind="legacy", _historical_import=True)
        with box.db() as db:
            db.execute("INSERT OR IGNORE INTO migrations VALUES(?)", (relative,))


def sync_qq_subscriptions(box, lid):
    locker = next(r for r in box.list() if r["id"] == lid)
    source = locker["source"]
    if source["pluginId"] != "qq":
        return
    key = f"{source['scope']}:{source['peerId']}"
    exact = key + "@" + source["botId"] if source["botId"] else None
    known = {r["session"] for r in box.subscriptions(lid)}
    from . import worker
    for s in worker._list_sessions_shallow():
        if s.id in known or not ({key, exact} - {None}) & (s.qq_subscriptions or set()):
            continue
        box.subscribe(lid, s.id, "full")


async def recover(manifest=None):
    box = get_inbox()
    migrate_qq(box, sess.SESSION_DIR.parent / "qq_inbox")
    if manifest is not None:
        reconcile_sources(manifest)
    sync_qq_patterns(box)
    for locker in box.list():
        sync_qq_subscriptions(box, locker["id"])
        await box.forward(locker["id"])


def reconcile_sources(manifest):
    """Global uninstall disables ingress/forwarding; content remains inspectable.

    Session tool binding changes intentionally do not disable global sources.
    """
    from packages.plugins.catalog import Catalog
    registered = set(Catalog(manifest).plugins)
    box = get_inbox()
    for pid in {"qq"} | {r["source"]["pluginId"] for r in box.list()}:
        if pid not in registered:
            box.set_available(pid, False)


def sync_qq_patterns(box):
    """Persist legacy source bindings before new Lockers can receive messages.

    New Locker creation applies these defaults in the same SQLite transaction,
    before its first message. Best-effort HTTP notify is never the eligibility
    boundary, so several offline arrivals cannot be skipped by a late notify.
    """
    from . import worker
    rows = [("qq", s.id, key) for s in worker._list_sessions_shallow() for key in (s.qq_subscriptions or set())]
    with box.db() as db:
        db.execute("DELETE FROM legacy_bindings WHERE plugin='qq'")
        db.executemany("INSERT INTO legacy_bindings VALUES(?,?,?)", rows)
