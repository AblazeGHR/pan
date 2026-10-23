"""Durable bounded event inbox. All methods run off-loop; no import-time IO."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time

RETENTION_SECONDS = 7 * 24 * 60 * 60
MAX_EVENTS = 100_000
MAX_BYTES = 256 * 1024 * 1024
PAYLOAD_BYTES = 64 * 1024
BATCH = 100


class CapacityError(ValueError):
    pass


def fact_identity(event):
    return json.dumps({k: event.get(k) for k in ('type', 'schemaVersion', 'source', 'payload')},
                      sort_keys=True, ensure_ascii=False, allow_nan=False)


class EventStore:
    def __init__(self, root, *, retention=RETENTION_SECONDS, max_events=MAX_EVENTS, max_bytes=MAX_BYTES, clock=time.time):
        self.path = Path(root) / 'events.sqlite3'
        self.retention, self.max_events, self.max_bytes, self.clock = retention, max_events, max_bytes, clock
        self.lock = threading.RLock()

    @contextmanager
    def connection(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=5)
            try:
                db.row_factory = sqlite3.Row
                db.execute('PRAGMA synchronous=FULL')
                # Connection-local limit: setting it only at initialize would
                # leave subsequent admission connections effectively unbounded.
                db.execute('PRAGMA max_page_count=81920')
                with db:
                    yield db
            finally:
                db.close()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE,
                fingerprint TEXT NOT NULL, event TEXT NOT NULL, targets TEXT NOT NULL,
                received REAL NOT NULL, bytes INTEGER NOT NULL, done INTEGER NOT NULL DEFAULT 0,
                deliveries INTEGER NOT NULL DEFAULT 0, last_error TEXT)''')
            db.execute('CREATE INDEX IF NOT EXISTS events_pending ON events(done,seq)')
            db.execute('CREATE INDEX IF NOT EXISTS events_retention ON events(done,received)')
            db.execute('PRAGMA user_version=1')

    def append(self, event, targets):
        event_json = json.dumps(event, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        payload = json.dumps(event['payload'], ensure_ascii=False, allow_nan=False).encode('utf-8')
        if len(payload) > PAYLOAD_BYTES:
            raise ValueError('Event payload exceeds 64 KiB')
        # occurredAt is assigned anew by a redelivering producer. Keep the first
        # envelope, but reject ID reuse with a different source/type/payload.
        fingerprint = hashlib.sha256(fact_identity(event).encode('utf-8')).hexdigest()
        targets_json = json.dumps(targets, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        size = len(event_json.encode('utf-8')) + len(targets_json.encode('utf-8'))
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM events WHERE id=?', (event['id'],)).fetchone()
            if old:
                if old['fingerprint'] != fingerprint:
                    raise ValueError('Event ID already belongs to a different fact')
                return {'event': json.loads(old['event']), 'duplicate': True}
            usage = db.execute('SELECT count(*),coalesce(sum(bytes),0) FROM events').fetchone()
            if usage[0] >= self.max_events or usage[1] + size > self.max_bytes:
                raise CapacityError('Event Store capacity reached; admission refused')
            db.execute('INSERT INTO events(id,fingerprint,event,targets,received,bytes) VALUES(?,?,?,?,?,?)',
                       (event['id'], fingerprint, event_json, targets_json, self.clock(), size))
        return {'event': event, 'duplicate': False}

    @staticmethod
    def decode(row):
        return dict(seq=row['seq'], event=json.loads(row['event']), targets=json.loads(row['targets']),
                    receivedAt=row['received'], status='delivered' if row['done'] else 'pending',
                    deliveryCount=row['deliveries'], lastError=row['last_error'])

    def pending(self, limit=64, after=0):
        with self.connection() as db:
            return [self.decode(r) for r in db.execute('SELECT * FROM events WHERE done=0 AND seq>? ORDER BY seq LIMIT ?', (after, limit))]

    def lookup(self, event_id):
        with self.connection() as db:
            row = db.execute('SELECT * FROM events WHERE id=?', (event_id,)).fetchone()
            return self.decode(row) if row else None

    def page(self, after=0, limit=20):
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= BATCH:
            raise ValueError('after must be nonnegative; limit must be 1..100')
        with self.connection() as db:
            rows = [self.decode(r) for r in db.execute('SELECT * FROM events WHERE seq>? ORDER BY seq LIMIT ?', (after, limit))]
        return {'events': rows, 'nextAfter': rows[-1]['seq'] if rows else after}

    def delivery(self, event_id, *, done=False, error=None):
        with self.connection() as db:
            db.execute('UPDATE events SET done=?, deliveries=deliveries+1,last_error=? WHERE id=?',
                       (int(done), str(error)[:2000] if error else None, event_id))

    def replay(self, ids):
        if not isinstance(ids, list) or not 1 <= len(ids) <= BATCH or any(not isinstance(i, str) or not i or len(i)>128 for i in ids) or len(set(ids)) != len(ids):
            raise ValueError('eventIds requires 1..100 unique Event IDs')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = db.execute('SELECT id,received,done FROM events WHERE id IN (' + ','.join('?' for _ in ids) + ')', ids).fetchall()
            if len(rows) != len(ids) or any(r['done'] and r['received'] < self.clock()-self.retention for r in rows):
                raise ValueError('Event missing or expired; replay never recreates an Event')
            db.executemany('UPDATE events SET done=0,last_error=NULL WHERE id=?', [(i,) for i in ids])
        return ids

    def maintenance(self):
        with self.connection() as db:
            # Never silently discard undelivered facts to meet retention/capacity.
            deleted = db.execute('DELETE FROM events WHERE done=1 AND received<?', (self.clock()-self.retention,)).rowcount
            return {'deletedEvents': deleted, 'retainedIds': {r[0] for r in db.execute('SELECT id FROM events')}}

    def stats(self):
        with self.connection() as db:
            total, size, pending = db.execute('SELECT count(*),coalesce(sum(bytes),0),coalesce(sum(done=0),0) FROM events').fetchone()
            overdue = db.execute('SELECT count(*) FROM events WHERE done=0 AND received<?', (self.clock()-self.retention,)).fetchone()[0]
            pages = db.execute('PRAGMA page_count').fetchone()[0]
            page_size = db.execute('PRAGMA page_size').fetchone()[0]
        return dict(events=total, bytes=size, pending=pending, databaseBytes=pages*page_size,
                    maxEvents=self.max_events, maxBytes=self.max_bytes, retentionSeconds=self.retention,
                    overduePending=overdue, physicalCapacityBytes=320*1024*1024,
                    warning=total >= self.max_events*.8 or size >= self.max_bytes*.8 or bool(overdue),
                    full=total >= self.max_events or size >= self.max_bytes)
