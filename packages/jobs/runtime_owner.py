"""Profile-wide durable epoch CAS and legacy/canonical writer exclusion.

Participants validate or reserve through the same authority before store work.
Legacy passes reserve a durable slot instead of holding a DB lock across store
locks, HTTP or await. An abandoned slot blocks activation as unknown ownership.
"""
from contextlib import contextmanager
from functools import wraps
import inspect
from pathlib import Path
import sqlite3
import threading
import uuid

from packages.core.platform.runtime_lock import RuntimeLock

_local = threading.local()


def refused(message):
    from packages.jobs.final_contracts import ContractError
    raise ContractError("revision_conflict", message)


class OwnerFence:
    def __init__(self, profile_root):
        self.root = Path(profile_root).resolve()
        self.path = self.root / "job-runtime-owner.sqlite3"
        self.lock = RuntimeLock(self.root / "job-runtime-owner.lock")
        self.token = None

    @contextmanager
    def transaction(self):
        joined = getattr(_local, "joined", {})
        key = str(self.path)
        if key in joined:
            yield joined[key]
            return
        self.root.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TABLE IF NOT EXISTS owner(singleton INTEGER PRIMARY KEY CHECK(singleton=1), epoch INTEGER NOT NULL, nonce TEXT NOT NULL, state TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS legacy_leases(id TEXT PRIMARY KEY)")
            db.execute("CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            _local.joined = {**joined, key: db}
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            _local.joined = joined
            db.close()

    def status(self):
        with self.transaction() as db:
            row = db.execute("SELECT epoch,nonce,state FROM owner WHERE singleton=1").fetchone()
            return dict(zip(("epoch", "nonce", "state"), row)) if row else None

    def _check(self, db, token):
        row = db.execute("SELECT epoch,nonce,state FROM owner WHERE singleton=1").fetchone()
        if row is None and token is None:
            return  # Pre-cutover fixture/legacy operation.
        if row is None or token is None or tuple(row[:2]) != tuple(token) or row[2] not in {"activating", "active"}:
            refused("Job runtime owner epoch fence rejected writer")

    @contextmanager
    def writer(self, *, canonical=False):
        with self.transaction() as db:
            token = self.token if canonical and self.lock.file is not None else None
            self._check(db, token)
            yield

    def claim(self, expected_epoch):
        if self.token is not None:
            refused("Runtime already acquired an owner epoch")
        try:
            self.lock.acquire()
        except NotImplementedError as exc:
            from packages.jobs.final_contracts import ContractError
            raise ContractError("capability_unsupported", str(exc)) from exc
        except OSError:
            refused("Another canonical runtime owns this profile")
        try:
            with self.transaction() as db:
                row = db.execute("SELECT epoch FROM owner WHERE singleton=1").fetchone()
                current = row[0] if row else 0
                if type(expected_epoch) is not int or current != expected_epoch:
                    refused("Owner epoch CAS conflict")
                if db.execute("SELECT 1 FROM legacy_leases LIMIT 1").fetchone():
                    refused("Legacy pass still active or ownership unknown")
                token = (current + 1, uuid.uuid4().hex)
                db.execute("INSERT OR REPLACE INTO owner VALUES(1,?,?, 'activating')", token)
            self.token = token
            return token
        except BaseException:
            self.lock.release()
            raise

    def set_state(self, state):
        if state not in {"active", "failed", "closed"}:
            raise ValueError("Invalid owner state")
        with self.transaction() as db:
            self._check(db, self.token)
            db.execute("UPDATE owner SET state=? WHERE singleton=1", (state,))

    def activation_intent(self, digest=None):
        with self.transaction() as db:
            row = db.execute("SELECT value FROM metadata WHERE key='activation'").fetchone()
            if digest is not None:
                self._check(db, self.token)
                if row is not None and row[0] != digest:
                    refused("Activation intent differs from the reviewed owner handoff")
                db.execute("INSERT OR REPLACE INTO metadata VALUES('activation',?)", (digest,))
            return row[0] if row else None

    def release(self, *, failed=False):
        try:
            if self.token is not None and self.lock.file is not None:
                current = self.status()
                if current and current["state"] in {"activating", "active"}:
                    self.set_state("failed" if failed else "closed")
        finally:
            self.lock.release()

    @contextmanager
    def legacy_pass(self):
        lease = uuid.uuid4().hex
        with self.transaction() as db:
            self._check(db, None)
            db.execute("INSERT INTO legacy_leases VALUES(?)", (lease,))
        try:
            yield
        finally:
            with self.transaction() as db:
                db.execute("DELETE FROM legacy_leases WHERE id=?", (lease,))


def legacy_writer(root, *, reservation=False):
    """Fence a complete legacy mutation or async pass, including its effects."""
    def decorate(fn):
        if inspect.iscoroutinefunction(fn):
            @wraps(fn)
            async def asynchronous(*args, **kwargs):
                with OwnerFence(root(*args, **kwargs)).legacy_pass():
                    return await fn(*args, **kwargs)
            return asynchronous
        @wraps(fn)
        def synchronous(*args, **kwargs):
            owner = OwnerFence(root(*args, **kwargs))
            with owner.legacy_pass() if reservation else owner.writer():
                return fn(*args, **kwargs)
        return synchronous
    return decorate
