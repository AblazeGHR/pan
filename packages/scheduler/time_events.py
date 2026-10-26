"""Pre-cutover compatibility facade for time Event sources.

Legacy Job records are never loaded here. A durable pending envelope bridges
the source cursor and the automation Event Store; retries reuse its exact bytes.
"""
import asyncio
import copy
import hashlib
import json
import math
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from packages.jobs.action_templates import Conflict
from packages.jobs.runtime_owner import OwnerFence
from packages.plugins.lifecycle import io
from . import store

EVENT_TYPE = 'scheduler.time.v1'
OWNER = 'scheduler-event-v1'
PAYLOAD_SCHEMA = {'type': 'object', 'additionalProperties': False,
    'required': ['scheduleId', 'scheduleRevision', 'scheduledAt', 'emittedAt', 'eventId'],
    'properties': {'scheduleId': {'type': 'string'}, 'scheduleRevision': {'type': 'integer', 'minimum': 1},
                   **{k: {'type': 'string'} for k in ('scheduledAt', 'emittedAt', 'eventId')}}}


class Sources:
    def __init__(self, root):
        self.path = Path(root) / 'time-events' / 'sources.sqlite3'
        self.lock = threading.RLock()
        self.owner_fence = OwnerFence(Path(root).resolve().parent)

    @contextmanager
    def connection(self, *, readonly=False):
        # Reads of historical sources must not create/upgrade a legacy store,
        # nor require an executable owner epoch after canonical activation.
        if readonly and not self.path.exists():
            yield None
            return
        from contextlib import nullcontext
        guard = nullcontext() if readonly else self.owner_fence.writer()
        with guard, self.lock:
            if readonly:
                db = sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
                try:
                    db.execute('PRAGMA query_only=ON')
                    yield db
                finally:
                    db.close()
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            db = sqlite3.connect(self.path, timeout=5)
            try:
                db.execute('PRAGMA synchronous=FULL')
                with db:
                    db.execute('CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY, document TEXT NOT NULL)')
                    db.execute('CREATE TABLE IF NOT EXISTS source_requests(key TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,id TEXT NOT NULL)')
                    db.execute('CREATE TABLE IF NOT EXISTS source_links(trigger_id TEXT PRIMARY KEY,source_id TEXT NOT NULL,job_id TEXT NOT NULL)')
                    yield db
            finally:
                db.close()

    def list(self):
        with self.connection(readonly=True) as db:
            if db is None:return []
            return [json.loads(row[0]) for row in db.execute('SELECT document FROM sources ORDER BY id')]

    def get(self, sid):
        with self.connection(readonly=True) as db:
            if db is None:return None
            row = db.execute('SELECT document FROM sources WHERE id=?', (sid,)).fetchone()
            return json.loads(row[0]) if row else None

    @staticmethod
    def public(item):
        return {k: copy.deepcopy(v) for k, v in item.items() if k != 'pending'}

    @staticmethod
    def write(db, item):
        db.execute('INSERT OR REPLACE INTO sources VALUES(?,?)', (item['id'], json.dumps(item, allow_nan=False)))

    def put(self, raw, expected, sid=None, *, at=None,creation_key=None):
        from packages.core.background_jobs import _entry_next_fire
        at = at or datetime.now().replace(microsecond=0)
        if not isinstance(raw, dict) or set(raw) - {'name', 'schedule', 'enabled', 'misfirePolicy', 'graceSec'}:
            raise ValueError('Unknown time Event source fields')
        if not isinstance(raw.get('name'), str) or not 1 <= len(raw['name'].strip()) <= 200:
            raise ValueError('Source name required (1..200 characters)')
        if type(raw.get('enabled', False)) is not bool:raise ValueError('enabled must be boolean')
        spec = raw.get('schedule')
        if not isinstance(spec, dict) or set(spec) - {'kind', 'at', 'intervalSec', 'interval_sec', 'anchor', 'cron', 'timezone'}:
            raise ValueError('Invalid source schedule')
        schedule = store._normalize_schedule(spec)
        if schedule['kind']=='interval' and (isinstance(spec.get('intervalSec',spec.get('interval_sec')),bool)
                or not math.isfinite(schedule['intervalSec']) or schedule['intervalSec']<1):
            raise ValueError('Time Event interval requires at least one second')
        # Retain existing host-local representation and calculator, including TZ.
        from packages.jobs import cron
        if schedule.get('timezone'):cron._resolve_tz(schedule['timezone'])
        policy = raw.get('misfirePolicy', 'fire_now')
        if policy not in store.MISFIRE_POLICIES:raise ValueError('Invalid misfirePolicy')
        grace = raw.get('graceSec', 300)
        if isinstance(grace, bool) or not isinstance(grace, (int, float)) or not math.isfinite(grace) or not 0 <= grace <= 86400:
            raise ValueError('graceSec requires 0..86400 seconds')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            fingerprint=hashlib.sha256(json.dumps(raw,sort_keys=True,allow_nan=False).encode()).hexdigest()
            if creation_key:
                prior=db.execute('SELECT fingerprint,id FROM source_requests WHERE key=?',(creation_key,)).fetchone()
                if prior:
                    if prior[0]!=fingerprint:raise Conflict('Source creation key belongs to another request')
                    row=db.execute('SELECT document FROM sources WHERE id=?',(prior[1],)).fetchone()
                    if not row:raise Conflict('Previously created source was deleted')
                    return self.public(json.loads(row[0]))
            old_row = db.execute('SELECT document FROM sources WHERE id=?', (sid,)).fetchone() if sid else None
            if sid and not old_row:raise KeyError(sid)
            old = json.loads(old_row[0]) if old_row else None
            if type(expected) is not int or expected != (old['revision'] if old else 0):raise Conflict('Source revision changed')
            if old and old.get('pending'):raise Conflict('Settle admitted occurrence before editing source')
            if not old and db.execute('SELECT count(*) FROM sources').fetchone()[0] >= 256:raise ValueError('Source capacity reached')
            if schedule['kind'] == 'interval' and not schedule.get('anchor'):
                schedule['anchor'] = (old['schedule'].get('anchor') if old and old['schedule']['kind'] == 'interval' else None) or store.iso(at)
            same = old and old['schedule'] == schedule
            next_fire = old['nextFireAt'] if same else store.iso(_entry_next_fire(schedule, at))
            if not same and next_fire is None:raise ValueError('Schedule has no future occurrence')
            item = dict(id=sid or 'schedule_' + uuid.uuid4().hex, name=raw['name'].strip(), owner=OWNER,
                        revision=expected + 1, enabled=raw.get('enabled', False), schedule=schedule,
                        misfirePolicy=policy, graceSec=grace, nextFireAt=next_fire,
                        publishCursor=copy.deepcopy(old.get('publishCursor')) if old else None,
                        createdAt=old['createdAt'] if old else store.iso(at), updatedAt=store.iso(at))
            self.write(db, item)
            if creation_key:db.execute('INSERT INTO source_requests VALUES(?,?,?)',(creation_key,fingerprint,item['id']))
            return self.public(item)

    def delete(self, sid, expected):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT document FROM sources WHERE id=?', (sid,)).fetchone()
            if not row:raise KeyError(sid)
            item = json.loads(row[0])
            if type(expected) is not int or item['revision'] != expected:raise Conflict('Source revision changed')
            if item['enabled'] or item.get('pending') or (item.get('publishCursor') and not item['publishCursor'].get('eventStoreReleased')):
                raise Conflict('Disable and settle source before deletion')
            if db.execute('SELECT 1 FROM source_links WHERE source_id=?',(sid,)).fetchone():raise Conflict('Detach referencing Jobs before deleting this source')
            db.execute('DELETE FROM sources WHERE id=?', (sid,))

    def bind_link(self,sid,tid,jid):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM sources WHERE id=?',(sid,)).fetchone():raise Conflict('Selected Scheduler was deleted')
            db.execute('INSERT OR REPLACE INTO source_links VALUES(?,?,?)',(tid,sid,jid))

    def detach_links(self,jid,keep=()):
        with self.connection() as db:
            rows=db.execute('SELECT trigger_id FROM source_links WHERE job_id=?',(jid,)).fetchall()
            db.executemany('DELETE FROM source_links WHERE trigger_id=?',[(r[0],) for r in rows if r[0] not in keep])

    def references(self,sid):
        with self.connection(readonly=True) as db:
            if db is None:return []
            return [{'triggerId':r[0],'jobId':r[1]} for r in db.execute('SELECT trigger_id,job_id FROM source_links WHERE source_id=?',(sid,))]

    def snapshot(self):
        """Explicit migration input, including pending bytes and all edges.

        One read transaction prevents a mixed source/link/cursor snapshot. The
        caller supplies its reviewed digest to the canonical importer; reading
        this snapshot never activates or repairs the old records.
        """
        with self.connection(readonly=True) as db:
            if db is None:return {'sources': [], 'links': [], 'requests': []}
            db.execute('BEGIN')
            return {
                'sources': [json.loads(r[0]) for r in db.execute('SELECT document FROM sources ORDER BY id')],
                'links': [{'triggerId':r[0], 'sourceId':r[1], 'jobId':r[2]} for r in db.execute(
                    'SELECT trigger_id,source_id,job_id FROM source_links ORDER BY trigger_id')],
                'requests': [{'key':r[0], 'fingerprint':r[1], 'sourceId':r[2]} for r in db.execute(
                    'SELECT key,fingerprint,id FROM source_requests ORDER BY key')],
            }

    def composition_enabled(self,sid,enabled):
        """Trusted saga recovery only: fence a new source, retaining its fact."""
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT document FROM sources WHERE id=?',(sid,)).fetchone()
            if not row:return
            item=json.loads(row[0])
            if item['enabled']!=enabled or item.get('compositionBlocked',False)==enabled:
                item.update(enabled=enabled,compositionBlocked=not enabled,revision=item['revision']+1)
                self.write(db,item)

    def bind_operation(self,sid,key):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT document FROM sources WHERE id=?',(sid,)).fetchone()
            if not row:raise Conflict('Source missing during setup')
            item=json.loads(row[0]);item['setupOperation']=key
            self.write(db,item)

    def set_next_fire(self, sid, next_fire):
        """Adopt a legacy next-fire fact during an owner switch."""
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT document FROM sources WHERE id=?',(sid,)).fetchone()
            if not row: raise KeyError(sid)
            item=json.loads(row[0]); item['nextFireAt']=next_fire; self.write(db,item)

    def reserve(self, sid, at):
        from packages.core.background_jobs import _entry_next_fire
        from packages.jobs.cron import parse_datetime
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT document FROM sources WHERE id=?', (sid,)).fetchone()
            if not row:return None
            item = json.loads(row[0])
            if item.get('owner') != OWNER:raise ValueError('Unknown source owner; refused')
            if item.get('compositionBlocked'):return None
            if item.get('pending'):return item['pending']
            fire = parse_datetime(item.get('nextFireAt'))
            if not item['enabled'] or fire is None or fire > at:return None
            next_fire = store.iso(_entry_next_fire(item['schedule'], max(at, fire)))
            late = (at - fire).total_seconds() > item['graceSec']
            if late and (item['schedule']['kind'] == 'once' or item['misfirePolicy'] == 'skip'):
                # Same expiry/coalescing rules as the legacy scheduler; no Event.
                item.update(nextFireAt=next_fire, lastOutcome='expired' if item['schedule']['kind'] == 'once' else 'skipped', lastScheduledAt=store.iso(fire))
                if item['schedule']['kind'] == 'once':item['enabled'] = False
                self.write(db, item)
                return None
            scheduled = store.iso(fire)
            identity = json.dumps([OWNER, item['id'], item['revision'], scheduled], separators=(',', ':'))
            event_id = 'evt_schedule_' + hashlib.sha256(identity.encode()).hexdigest()
            emitted = datetime.now(timezone.utc).isoformat()
            payload = dict(scheduleId=item['id'], scheduleRevision=item['revision'], scheduledAt=scheduled, emittedAt=emitted, eventId=event_id)
            pending = dict(event=dict(id=event_id, type=EVENT_TYPE, schemaVersion=1,
                source={'type': 'core', 'id': 'scheduler'}, occurredAt=emitted, payload=payload), nextFireAt=next_fire)
            item['pending'] = pending
            self.write(db, item)
            return copy.deepcopy(pending)

    def advance(self, sid, event_id):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            item = json.loads(db.execute('SELECT document FROM sources WHERE id=?', (sid,)).fetchone()[0])
            pending = item.get('pending')
            if not pending or pending['event']['id'] != event_id:return
            event = pending['event']
            item.update(nextFireAt=pending['nextFireAt'], lastOutcome='published',
                        publishCursor={'eventId': event_id, **event['payload'],'eventStoreReleased':False})
            if item['schedule']['kind'] == 'once':item['enabled'] = False
            item.pop('pending')
            self.write(db, item)

    def acknowledged(self,sid,event_id):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT document FROM sources WHERE id=?',(sid,)).fetchone()
            if not row:return
            item=json.loads(row[0]);cursor=item.get('publishCursor')
            if cursor and cursor['eventId']==event_id and not cursor.get('eventStoreReleased'):
                cursor['eventStoreReleased']=True
                self.write(db,item)


class Runtime:
    def __init__(self, sources, admit, diagnostics, acknowledge=None,source_ready=None,cursor_advanced=None):
        self.sources, self.admit, self.diagnostics = sources, admit, diagnostics
        self.acknowledge=acknowledge
        self.source_ready=source_ready
        self.cursor_advanced=cursor_advanced
        self.lock = asyncio.Lock()

    async def tick(self, cfg, *, at=None):
        if not cfg.get('enabled', True):return
        # Reserve the entire pass, not only the SQLite write: owner activation
        # cannot race an in-flight publish/ack or a legacy callback.
        with self.sources.owner_fence.legacy_pass():
            return await self._tick(cfg, at=at)

    async def _tick(self, cfg, *, at=None):
        async with self.lock:
            try:items=await io(self.sources.list)
            except Exception as exc:
                self.diagnostics.append({'phase':'scheduler-event-store','error':str(exc)[:1000]})
                return
            for item in items:
                try:
                    if self.cursor_advanced:
                        await io(self.cursor_advanced, item['id'])
                    if item.get('setupOperation') and (not self.source_ready or not await io(self.source_ready,item['setupOperation'])):continue
                    if self.acknowledge and item.get('publishCursor') and not item['publishCursor'].get('eventStoreReleased'):
                        await self.acknowledge(item['publishCursor']['eventId'])
                        await io(self.sources.acknowledged,item['id'],item['publishCursor']['eventId'])
                    pending = await io(self.sources.reserve, item['id'], at or datetime.now().replace(microsecond=0))
                    if not pending:continue
                    # Awaiting this commit is the only authority to advance.
                    receipt = await self.admit(copy.deepcopy(pending['event']))
                    if receipt.get('persisted') is not True or receipt.get('event', {}).get('id') != pending['event']['id']:
                        raise ValueError('Durable Event receipt identity mismatch')
                    await io(self.sources.advance, item['id'], pending['event']['id'])
                    if self.cursor_advanced:
                        await io(self.cursor_advanced, item['id'])
                    if self.acknowledge:
                        await self.acknowledge(pending['event']['id'])
                        await io(self.sources.acknowledged,item['id'],pending['event']['id'])
                except Exception as exc:
                    self.diagnostics.append({'phase': 'scheduler-event', 'scheduleId': item['id'], 'error': str(exc)[:1000]})
