"""Shared Agent Inbox. SQLite owns claims; Session Queue owns acceptance.

No cross-file transaction is claimed. An interrupted intent is reconciled by
its original QueueID. Missing receipts after an attempt mean unknown, never retry.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class InboxError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def identity(prefix, value):
    return prefix + hashlib.sha256(canonical(value).encode()).hexdigest()


class Inbox:
    def __init__(self, path: Path, queue, *, capacity=500):
        self.path, self.queue, self.capacity = Path(path), queue, capacity
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._forward_lock = asyncio.Lock()
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS lockers(id TEXT PRIMARY KEY, source TEXT NOT NULL, available INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS sources(plugin TEXT PRIMARY KEY, available INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS messages(seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
                    locker TEXT NOT NULL, native_id TEXT NOT NULL, content TEXT NOT NULL, identity_kind TEXT NOT NULL,
                    consumed INTEGER NOT NULL DEFAULT 0, deliveries TEXT NOT NULL DEFAULT '{}', claimed INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS subscriptions(locker TEXT NOT NULL, session TEXT NOT NULL,
                    mode TEXT NOT NULL, boundary INTEGER NOT NULL, PRIMARY KEY(locker,session));
                CREATE TABLE IF NOT EXISTS routes(id TEXT PRIMARY KEY, signature TEXT NOT NULL,
                    state TEXT NOT NULL, session TEXT NOT NULL, queue_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS origins(id TEXT PRIMARY KEY, content TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS deleted_messages(id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS legacy_bindings(plugin TEXT NOT NULL, session TEXT NOT NULL,
                    target TEXT NOT NULL, PRIMARY KEY(plugin,session,target));
            ''')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def validate_source(self, source):
        keys = {"pluginId", "instanceId", "botId", "scope", "peerId"}
        if not isinstance(source, dict) or set(source) != keys or any(not isinstance(v, str) for v in source.values()) or any(not source[k] for k in keys - {"botId"}):
            raise InboxError("invalid source: pluginId/instanceId/botId/scope/peerId required")

    def locker(self, source):
        self.validate_source(source)
        lid = identity("locker_", source)
        with self.db() as db:
            available = db.execute("SELECT available FROM sources WHERE plugin=?", (source["pluginId"],)).fetchone()
            created = db.execute("INSERT OR IGNORE INTO lockers VALUES(?,?,?)", (lid, canonical(source), available[0] if available else 1)).rowcount
            if created:
                target = f"{source['scope']}:{source['peerId']}"
                exact = target + "@" + source["botId"] if source["botId"] else target
                boundary = db.execute("SELECT coalesce(max(seq),0) FROM messages").fetchone()[0]
                for binding in db.execute("SELECT DISTINCT session FROM legacy_bindings WHERE plugin=? AND target IN (?,?)",
                                          (source["pluginId"], target, exact)).fetchall():
                    db.execute("INSERT OR IGNORE INTO subscriptions VALUES(?,?,?,?)", (lid, binding[0], "full", boundary))
        return next(row for row in self.list() if row["id"] == lid)

    def _locker(self, db, lid):
        row = db.execute("SELECT * FROM lockers WHERE id=?", (lid,)).fetchone()
        if row is None:
            raise InboxError("locker not found")
        return row

    def list(self):
        with self.db() as db:
            return [{"id": r["id"], "kind": "agent_inbox", "source": json.loads(r["source"]),
                     "available": bool(r["available"]), "pendingCount": db.execute(
                         "SELECT count(*) FROM messages WHERE locker=? AND consumed=0", (r["id"],)).fetchone()[0]}
                    for r in db.execute("SELECT * FROM lockers ORDER BY id")]

    def set_available(self, plugin_id, available):
        if type(available) is not bool:
            raise InboxError("available must be boolean")
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO sources VALUES(?,?)", (plugin_id, available))
            for row in db.execute("SELECT id,source FROM lockers").fetchall():
                if json.loads(row["source"])["pluginId"] == plugin_id:
                    db.execute("UPDATE lockers SET available=? WHERE id=?", (available, row["id"]))

    def receive(self, lid, native_id, content, *, identity_kind="native", _historical_import=False):
        if not isinstance(native_id, str) or not native_id or not isinstance(content, dict):
            raise InboxError("stable source message id and content object required")
        self._validate_content(content)
        if identity_kind not in {"native", "legacy"}:
            raise InboxError("invalid identity kind")
        encoded = canonical(content)
        mid = identity("message_", [lid, native_id])
        with self.db() as db:
            locker = self._locker(db, lid)
            if not locker["available"] and not _historical_import:
                raise InboxError("source unavailable")
            old = db.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
            if old:
                if old["content"] != encoded or old["identity_kind"] != identity_kind:
                    raise InboxError("source message identity conflict")
                return self._message(old)
            if db.execute("SELECT 1 FROM deleted_messages WHERE id=?", (mid,)).fetchone():
                raise InboxError("source message previously deleted")
            count = db.execute("SELECT count(*) FROM messages WHERE locker=? AND consumed=0", (lid,)).fetchone()[0]
            if count >= self.capacity:
                raise InboxError("locker capacity exceeded; unconsumed messages retained")
            self._origin(db, json.loads(locker["source"]), native_id, content)
            db.execute("INSERT INTO messages(id,locker,native_id,content,identity_kind) VALUES(?,?,?,?,?)",
                       (mid, lid, native_id, encoded, identity_kind))
            return self._message(db.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone())

    def _origin(self, db, source, native_id, content):
        key = identity("origin_", [source, native_id])
        encoded = identity("content_", content)
        old = db.execute("SELECT content FROM origins WHERE id=?", (key,)).fetchone()
        if old and old[0] != encoded:
            raise InboxError("source message identity conflict")
        db.execute("INSERT OR IGNORE INTO origins VALUES(?,?)", (key, encoded))

    def _validate_content(self, content):
        if "text" in content and not isinstance(content["text"], str):
            raise InboxError("content.text must be a string")
        for key in ("parts", "attachments"):
            if key in content and (not isinstance(content[key], list) or not all(isinstance(p, dict) for p in content[key])):
                raise InboxError(f"content.{key} must be an array of objects")
        if "envelope" in content and not isinstance(content["envelope"], dict):
            raise InboxError("content.envelope must be an object")
        try:
            canonical(content)
        except (TypeError, ValueError) as exc:
            raise InboxError("content must be finite JSON") from exc

    def _message(self, row):
        return {"id": row["id"], "lockerId": row["locker"], "sequence": row["seq"],
                "sourceMessageId": row["native_id"], "identityKind": row["identity_kind"],
                "content": json.loads(row["content"]), "consumed": bool(row["consumed"]),
                "claimed": bool(row["claimed"]), "deliveries": json.loads(row["deliveries"])}

    def read(self, lid, *, limit=100, include_consumed=False):
        if type(limit) is not int or not 1 <= limit <= 500:
            raise InboxError("limit must be 1..500")
        if type(include_consumed) is not bool:
            raise InboxError("includeConsumed must be boolean")
        with self.db() as db:
            self._locker(db, lid)
            return [self._message(r) for r in db.execute(
                "SELECT * FROM messages WHERE locker=? AND (consumed=0 OR ?) ORDER BY seq LIMIT ?",
                (lid, include_consumed, limit))]

    def consume(self, lid, message_ids):
        if not isinstance(message_ids, list) or not all(isinstance(x, str) for x in message_ids):
            raise InboxError("messageIds must be a list of IDs")
        result = {"consumed": [], "busy": []}
        with self.db() as db:
            self._locker(db, lid)
            for mid in dict.fromkeys(message_ids):
                row = db.execute("SELECT * FROM messages WHERE locker=? AND id=? AND consumed=0", (lid, mid)).fetchone()
                if not row:
                    continue
                if row["claimed"]:
                    result["busy"].append(mid)
                else:
                    db.execute("UPDATE messages SET consumed=1 WHERE id=?", (mid,))
                    result["consumed"].append(mid)
        return result

    def subscribe(self, lid, sid, mode):
        if mode not in {"silent", "notification", "full"} or not isinstance(sid, str) or not sid:
            raise InboxError("mode must be silent/notification/full and session required")
        with self.db() as db:
            self._locker(db, lid)
            old = db.execute("SELECT * FROM subscriptions WHERE locker=? AND session=?", (lid, sid)).fetchone()
            if not old or old["mode"] != mode:
                boundary = db.execute("SELECT coalesce(max(seq),0) FROM messages").fetchone()[0]
                db.execute("INSERT OR REPLACE INTO subscriptions VALUES(?,?,?,?)", (lid, sid, mode, boundary))
            return dict(db.execute("SELECT * FROM subscriptions WHERE locker=? AND session=?", (lid, sid)).fetchone())

    def subscriptions(self, lid):
        with self.db() as db:
            self._locker(db, lid)
            return [dict(r) for r in db.execute("SELECT * FROM subscriptions WHERE locker=? ORDER BY session", (lid,))]

    def _plan(self, lid, backlog):
        with self.db() as db:
            if not self._locker(db, lid)["available"]:
                return []
            rows = db.execute("SELECT * FROM messages WHERE locker=? AND consumed=0 ORDER BY seq", (lid,)).fetchall()
            subs = db.execute("SELECT * FROM subscriptions WHERE locker=?", (lid,)).fetchall()
            ids = []
            for row in rows:
                plan = json.loads(row["deliveries"])
                if not row["claimed"]:
                    # Notifications do not freeze the eventual full recipient set.
                    full = [s for s in subs if s["mode"] == "full" and (backlog or row["seq"] > s["boundary"])]
                    selected = [(s, False) for s in full] + [
                        (s, True) for s in subs if s["mode"] == "notification" and row["seq"] > s["boundary"]]
                    for s, notification in selected:
                        key = "notification:" + s["session"] if notification else s["session"]
                        plan.setdefault(key, {"sessionId": s["session"], "notification": notification,
                            "queueItemId": identity("q_", [row["id"], key]), "status": "ready"})
                    db.execute("UPDATE messages SET deliveries=?,claimed=? WHERE id=?",
                               (canonical(plan), bool(full), row["id"]))
                if plan:
                    ids.append(row["id"])
            return ids

    def _delivery(self, mid, key, state):
        with self.db() as db:
            row = db.execute("SELECT deliveries,consumed FROM messages WHERE id=?", (mid,)).fetchone()
            if row is None:
                return
            plan = json.loads(row[0])
            if plan[key]["status"] != "accepted":
                plan[key]["status"] = state
            full = [p for p in plan.values() if not p["notification"]]
            consumed = bool(row["consumed"]) or (bool(full) and all(p["status"] == "accepted" for p in full))
            db.execute("UPDATE messages SET deliveries=?,consumed=? WHERE id=?", (canonical(plan), consumed, mid))

    def _begin_delivery(self, mid, key):
        with self.db() as db:
            row = db.execute("SELECT deliveries FROM messages WHERE id=?", (mid,)).fetchone()
            if row is None:
                return False
            plan = json.loads(row[0])
            state = plan[key]["status"]
            if state in {"ready", "failed"}:
                plan[key]["status"] = "in_flight"
                db.execute("UPDATE messages SET deliveries=? WHERE id=?", (canonical(plan), mid))
                return True
            return False

    async def forward(self, lid, *, backlog=False):
        async with self._forward_lock:
            for mid in self._plan(lid, backlog):
                with self.db() as db:
                    stored = db.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
                    if stored is None:
                        continue
                    row = self._message(stored)
                    row["source"] = json.loads(self._locker(db, lid)["source"])
                for key, intent in row["deliveries"].items():
                    with self.db() as db:
                        if not self._locker(db, lid)["available"]:
                            return {"status": "unavailable"}
                    state = intent["status"]
                    if state == "accepted":
                        continue
                    if state in {"in_flight", "unknown"}:
                        receipt = await self.queue.lookup(intent["sessionId"], intent["queueItemId"])
                        state = "accepted" if receipt and receipt.get("status") == "accepted" else "unknown"
                        self._delivery(mid, key, state)
                        continue
                    # A durable pre-write intent is mandatory, including notifications.
                    if not self._begin_delivery(mid, key):
                        continue
                    try:
                        receipt = await self.queue.put(intent["sessionId"], intent["queueItemId"], row,
                                                       notification=intent["notification"])
                        state = {"accepted": "accepted", "rejected": "failed"}.get(receipt.get("status"), "unknown")
                    except Exception:
                        state = "unknown"
                    self._delivery(mid, key, state)
            return {"status": "processed"}

    def delete(self, lid):
        with self.db() as db:
            self._locker(db, lid)
            if db.execute("SELECT 1 FROM messages WHERE locker=? AND claimed=1 AND consumed=0", (lid,)).fetchone():
                raise InboxError("active delivery prevents deletion")
            for row in db.execute("SELECT deliveries FROM messages WHERE locker=?", (lid,)):
                if any(p["status"] in {"in_flight", "unknown"} for p in json.loads(row[0]).values()):
                    raise InboxError("active or unknown delivery prevents deletion")
            db.execute("INSERT OR IGNORE INTO deleted_messages SELECT id FROM messages WHERE locker=?", (lid,))
            db.execute("DELETE FROM messages WHERE locker=?", (lid,))
            db.execute("DELETE FROM subscriptions WHERE locker=?", (lid,))
            db.execute("DELETE FROM lockers WHERE id=?", (lid,))
        return {"deleted": lid}


class Router:
    """Fixed internal destinations only; no executable or URL targets."""
    def __init__(self, inbox, *, user_inbox=None):
        self.inbox = inbox
        self.user_inbox = user_inbox

    async def route(self, target, source_message_id, content):
        if not isinstance(source_message_id, str) or not source_message_id or not isinstance(content, dict):
            raise InboxError("stable source message id and content required")
        self.inbox._validate_content(content)
        kind = target.get("kind")
        if kind == "user_inbox" and self.user_inbox is not None:
            return await self.user_inbox.route(target, source_message_id, content)
        if kind == "agent_inbox":
            message = self.inbox.receive(target["lockerId"], source_message_id, content)
            await self.inbox.forward(target["lockerId"])
            return {"status": "stored", "message": message}
        if kind == "session_queue":
            self.inbox.validate_source(target["source"])
            qid = identity("q_", [target["sessionId"], target["source"], source_message_id])
            message = {"id": source_message_id, "content": copy.deepcopy(content), "source": target["source"]}
            signature = canonical(message)
            with self.inbox.db() as db:
                available = db.execute("SELECT available FROM sources WHERE plugin=?", (target["source"]["pluginId"],)).fetchone()
                if available and not available[0]:
                    raise InboxError("source unavailable")
                self.inbox._origin(db, target["source"], source_message_id, content)
                old = db.execute("SELECT * FROM routes WHERE id=?", (qid,)).fetchone()
                if old and old["signature"] != signature:
                    raise InboxError("source message identity conflict")
                if old and old["state"] == "accepted":
                    return {"status": "accepted", "queueItemId": qid}
                reconcile = old and old["state"] in {"in_flight", "unknown"}
                if not reconcile:
                    db.execute("INSERT OR REPLACE INTO routes VALUES(?,?,?,?,?)",
                               (qid, signature, "in_flight", target["sessionId"], qid))
            if reconcile:
                result = await self.inbox.queue.lookup(target["sessionId"], qid)
                result = result or {"status": "unknown", "queueItemId": qid}
            else:
                try:
                    result = await self.inbox.queue.put(target["sessionId"], qid, message)
                except Exception:
                    result = {"status": "unknown", "queueItemId": qid}
            state = result.get("status", "unknown")
            with self.inbox.db() as db:
                db.execute("UPDATE routes SET state=? WHERE id=?", (state, qid))
            return result
        raise InboxError("unsupported destination: user_inbox is an unimplemented extension")
