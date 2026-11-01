"""Read-only original source evidence, independent of retired source hosts.

No normalization, schema upgrade, directory creation, or execution is allowed.
The complete transactional snapshot is explicit input to reviewed adoption.
"""
import copy
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class SourceHistory:
    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / "time-events" / "sources.sqlite3"

    @property
    def owner_fence(self):
        # Only explicit import needs profile identity; ordinary reads need no
        # authority construction or executable owner epoch.
        from packages.jobs.runtime_owner import OwnerFence
        return OwnerFence(self.root.resolve().parent)

    @contextmanager
    def connection(self):
        if not self.path.exists():
            yield None
            return
        db = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
        try:
            db.execute("PRAGMA query_only=ON")
            yield db
        finally:
            db.close()

    @staticmethod
    def public(item):
        return {key: copy.deepcopy(value) for key, value in item.items() if key != "pending"}

    def list(self):
        with self.connection() as db:
            return [] if db is None else [json.loads(row[0]) for row in db.execute(
                "SELECT document FROM sources ORDER BY id")]

    def get(self, source_id):
        with self.connection() as db:
            row = None if db is None else db.execute(
                "SELECT document FROM sources WHERE id=?", (source_id,)).fetchone()
            return json.loads(row[0]) if row else None

    def references(self, source_id):
        with self.connection() as db:
            return [] if db is None else [{"triggerId": row[0], "jobId": row[1]} for row in db.execute(
                "SELECT trigger_id,job_id FROM source_links WHERE source_id=? ORDER BY trigger_id", (source_id,))]

    def snapshot(self):
        with self.connection() as db:
            if db is None:
                return {"sources": [], "links": [], "requests": []}
            db.execute("BEGIN")
            return {
                "sources": [json.loads(row[0]) for row in db.execute("SELECT document FROM sources ORDER BY id")],
                "links": [{"triggerId": row[0], "sourceId": row[1], "jobId": row[2]} for row in db.execute(
                    "SELECT trigger_id,source_id,job_id FROM source_links ORDER BY trigger_id")],
                "requests": [{"key": row[0], "fingerprint": row[1], "sourceId": row[2]} for row in db.execute(
                    "SELECT key,fingerprint,id FROM source_requests ORDER BY key")],
            }
