"""Report-only notification sources. No Session receiver and no history replay.

Locker intents commit with their new unread item. Error admission uses the
structured error event identity, never a log scan. Every external attempt is
claimed durably first; a crash/unknown receipt is never automatically retried.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import hashlib

from . import session
from .qq_reports import parse_target

SOURCES = ('lockerUnread', 'newError')
# Exact structured identities already recognized by the provider/runtime layer.
# This is a notification catalog, not another error classifier. Never learn
# entries from prose, logs, or arbitrary client input.
from .runtime_issues import _CODEX_QUOTA_CODES, _CODEX_CAPACITY_CODES, _CBC_RATE_CODES

ERROR_TYPE_CATALOG = tuple(
    {'id': f'{source}/{code}', 'label': f'{source} · {code}'}
    for source, codes in (
        ('codex_error_payload', {'usageLimitExceeded', 'serverOverloaded', 'responseStreamDisconnected'} | _CODEX_QUOTA_CODES | _CODEX_CAPACITY_CODES),
        ('cli.error', _CODEX_QUOTA_CODES | _CODEX_CAPACITY_CODES | _CBC_RATE_CODES | {'rate_limit'}),
    ) for code in sorted(codes)
)
_ERROR_TYPE_IDS = frozenset(row['id'] for row in ERROR_TYPE_CATALOG)


def normalize_error_exclusions(value, *, strict=False):
    raw = value if isinstance(value, dict) else {}
    types = raw.get('types', [])
    valid = (isinstance(value, dict) and not set(raw) - {'enabled', 'types'}
             and type(raw.get('enabled', False)) is bool
             and isinstance(types, list)
             and all(isinstance(t, str) and t in _ERROR_TYPE_IDS for t in types))
    if not valid:
        if strict:
            raise ValueError('errorNotificationExclusions requires enabled boolean and server-known types')
        return {'enabled': False, 'types': []}
    return {'enabled': raw.get('enabled', False), 'types': sorted(set(types))}


def error_type(record):
    cli = record.get('cli')
    if not isinstance(cli, dict):
        return None
    error = cli.get('error')
    code = cli.get('errorCode')
    if code is None and isinstance(error, dict):
        code = error.get('code')
        if code is None:
            code = error.get('codexErrorInfo')
            # Codex also serializes native enum variants as one-key objects.
            # Match the stable tag, never its payload/status/message contents.
            if isinstance(code, dict) and len(code) == 1:
                code = next(iter(code))
    source = cli.get('errorSource')
    if not isinstance(source, str) or type(code) not in (str, int):
        return None
    identity = f'{source}/{code}'
    return identity if identity in _ERROR_TYPE_IDS else None


def error_exclusion_policy():
    from .config import load_config
    ui = load_config().get('ui', {})
    return normalize_error_exclusions(ui.get('errorNotificationExclusions')), ui.get('errorNotificationExclusionsEpoch', '')


def error_is_excluded(record, policy):
    identity = error_type(record)
    return policy['enabled'] and identity is not None and identity in policy['types']

_log = logging.getLogger(__name__)
_task = None
_browser_sender = None


def normalize(value, *, strict=False):
    raw = value if isinstance(value, dict) else {}
    if strict and (not isinstance(value, dict) or set(raw) - {'enabled', 'system', 'browser', 'qqReport', 'qqTargets', 'epoch'}):
        raise ValueError('Notification source accepts enabled, System, Browser and QQ Report settings only')
    result = {}
    for key in ('enabled', 'system', 'browser', 'qqReport'):
        val = raw.get(key, False)
        if strict and type(val) is not bool:
            raise ValueError(f'{key} must be boolean')
        result[key] = val is True
    targets = raw.get('qqTargets', [])
    valid = isinstance(targets, list) and all(isinstance(t, str) and parse_target(t) for t in targets)
    if strict and not valid:
        raise ValueError('Invalid QQ contacts')
    result['qqTargets'] = sorted(set(targets)) if valid else []
    if strict and result['enabled'] and result['qqReport'] and not result['qqTargets']:
        raise ValueError('Select at least one QQ Report contact')
    result['epoch'] = raw.get('epoch', '') if isinstance(raw.get('epoch', ''), str) else ''
    return result


def settings(source):
    from .config import load_config
    return normalize(load_config().get('ui', {}).get('notifications', {}).get(source))


def schema(db):
    db.execute('''CREATE TABLE IF NOT EXISTS source_notification_events(
        source TEXT NOT NULL, identity TEXT NOT NULL, owner TEXT NOT NULL,
        targets TEXT NOT NULL, text TEXT NOT NULL, ref TEXT NOT NULL UNIQUE,
        PRIMARY KEY(source,identity))''')
    db.execute('''CREATE TABLE IF NOT EXISTS source_notification_attempts(
        source TEXT NOT NULL, identity TEXT NOT NULL, target TEXT NOT NULL,
        state TEXT NOT NULL, response TEXT,
        PRIMARY KEY(source,identity,target))''')
    db.execute('CREATE INDEX IF NOT EXISTS source_notification_owner ON source_notification_events(owner,source)')
    db.execute('CREATE INDEX IF NOT EXISTS source_notification_pending ON source_notification_attempts(state)')
    if 'exclusion_epoch' not in {row[1] for row in db.execute('PRAGMA table_info(source_notification_events)')}:
        db.execute("ALTER TABLE source_notification_events ADD COLUMN exclusion_epoch TEXT NOT NULL DEFAULT ''")


def visible(owner):
    # A structured product error may have no Session. Global routes can still
    # report that event; a nonempty but unavailable Session fails closed.
    if not owner:
        return True
    s = session.get(owner, load_history=False)
    # A missing or operation-locked source cannot authorize content forwarding.
    return s is not None and not s.readonly_session


def session_policy():
    from .config import load_config
    ui = load_config().get('ui', {})
    # QQ Report already reports terminal errors. Keep that default while adding
    # the explicit Behavior switch governing Session error admission.
    return ui.get('sessionErrorBridge', True) is True, ui.get('sessionErrorBridgeEpoch', '')


def routes(source, owner):
    """Union origins by actual delivery path, retaining each admission grant."""
    result = {}
    cfg = settings(source)
    if cfg['enabled']:
        for target in (['system'] if cfg['system'] else []) + (['browser'] if cfg['browser'] else []) + (cfg['qqTargets'] if cfg['qqReport'] else []):
            result.setdefault(target, {})['global'] = cfg['epoch']
    if source == 'newError':
        enabled, epoch = session_policy()
        s = session.get(owner, load_history=False) if owner else None
        if enabled and s is not None:
            from .notifications import normalize_notification_settings
            channels = normalize_notification_settings(getattr(s, 'notification_settings', None))
            targets = sorted(t for t in getattr(s, 'qq_report_targets', []) if parse_target(t))
            fingerprint = hashlib.sha256(json.dumps([epoch, channels, targets], sort_keys=True).encode()).hexdigest()
            for target in (['system'] if channels['system'] else []) + (['browser'] if channels['browser'] else []) + targets:
                result.setdefault(target, {})['session'] = fingerprint
    return result


def stage(db, source, identity, owner, text, *, recipient='user', error_record=None):
    """Best effort inside the content transaction; failure cannot reject content.

    The savepoint isolates even a partially written intent from the primary
    commit. Disabled/private events retain identity, but no text or targets.
    """
    try:
        db.execute('SAVEPOINT source_notification')
        policy, exclusion_epoch = error_exclusion_policy() if source == 'newError' else ({'enabled': False, 'types': []}, '')
        excluded = error_record is not None and error_is_excluded(error_record, policy)
        grants = routes(source, owner) if not excluded and recipient == 'user' and visible(owner) else {}
        from .session_error_log import sanitize
        # The QQ route accepts CQ markup. Treat source content as plain text,
        # so a Locker message cannot turn a notification into an image/mention.
        body = re.sub(r'\[CQ:', '[CQ-disabled:', str(sanitize(str(text))), flags=re.I)[:1200] if grants else ''
        ref = hashlib.sha256(f'{source}\0{identity}'.encode()).hexdigest()
        db.execute('INSERT OR IGNORE INTO source_notification_events(source,identity,owner,targets,text,ref,exclusion_epoch) VALUES(?,?,?,?,?,?,?)',
                   (source, identity, owner or '', json.dumps(grants), body, ref, exclusion_epoch))
        event = db.execute('SELECT targets FROM source_notification_events WHERE source=? AND identity=?',
                           (source, identity)).fetchone()
        original = json.loads(event['targets'])
        for target, origins in grants.items():
            # Re-observing an identity cannot grant a new path or new epoch.
            if target in original and original[target] == origins:
                db.execute('INSERT OR IGNORE INTO source_notification_attempts VALUES(?,?,?,?,NULL)',
                           (source, identity, target, 'pending'))
        db.execute('RELEASE source_notification')
    except Exception:
        try:
            db.execute('ROLLBACK TO source_notification')
            db.execute('RELEASE source_notification')
        except Exception:
            pass
        _log.warning('Notification admission failed source=%s', source, exc_info=False)


def error_event(record):
    """Observe only classified, non-cancellation Session error events."""
    if record.get('cancelled') or record.get('kind') not in {
        'terminal_error', 'worker_crash', 'worker_start_failure',
        'native_operation_error', 'transient_provider_error', 'tool_failure',
    }:
        return
    try:
        from packages.interactions.api import get_store
        owner = record.get('sessionId')
        text = (f"Pan: New error ({record['kind']})\nSession: {owner}\n"
                f"Event: {record['eventId']}\nTask: {record.get('taskId') or record.get('taskSeq')}\n"
                f"{record.get('errorText') or 'Error recorded'}")
        with get_store().db() as db:
            stage(db, 'newError', record['eventId'], owner, text, error_record=record)
    except Exception:
        _log.warning('Error notification admission failed', exc_info=False)


def _claim(store):
    with store.db() as db:
        rows = db.execute('''SELECT a.*,e.owner,e.targets,e.text,e.ref FROM source_notification_attempts a
            JOIN source_notification_events e USING(source,identity) WHERE a.state='pending'
            ORDER BY a.rowid LIMIT 32''').fetchall()
        for row in rows:
            current = routes(row['source'], row['owner']).get(row['target'], {})
            original = json.loads(row['targets']).get(row['target'], {})
            active = visible(row['owner']) and any(current.get(origin) == epoch for origin, epoch in original.items())
            state = 'attempted' if active else 'cancelled'
            db.execute('UPDATE source_notification_attempts SET state=? WHERE source=? AND identity=? AND target=?',
                       (state, row['source'], row['identity'], row['target']))
            if active:
                return dict(row)
    return None


def outcome(response):
    if not isinstance(response, dict):
        return 'unknown'
    if response.get('queued') is True:
        return 'queued'
    delivery = response.get('delivery') or response.get('status') or response.get('outcome') or response.get('state')
    if isinstance(delivery, dict):
        delivery = delivery.get('state') or delivery.get('status')
    code = response.get('error', {}).get('code') if isinstance(response.get('error'), dict) else None
    if code in ('unknown', 'uncertain', 'timeout', 'connection_error'):
        return 'unknown'
    if delivery in ('unknown', 'uncertain', 'queued', 'pending', 'accepted'):
        return 'queued' if delivery == 'queued' else 'unknown'
    if response.get('ok') is True:
        return 'sent'
    return 'failed'


async def drain_once(sender, store=None, *, browser_sender=None):
    if store is None:
        from packages.interactions.api import get_store
        store = await asyncio.to_thread(get_store)
    row = await asyncio.to_thread(_claim, store)
    if row is None:
        return False
    try:
        if row['target'] == 'system':
            from .notifications import dispatch_reminder
            payload = await asyncio.to_thread(dispatch_reminder, 'New error' if row['source'] == 'newError' else 'Locker new unread', row['text'])
            response = payload['system']
        elif row['target'] == 'browser':
            browser = browser_sender or _browser_sender
            if browser is None:
                response = {'ok': False, 'error': {'code': 'browser_unavailable'}}
            else:
                # The public live stream carries only an opaque wakeup handle.
                # Content is fetched through the existing UserAuth HTTP gate.
                await browser({'type': 'notification.source', 'notificationId': row['ref']})
                response = {'ok': True, 'delivery': 'queued'}
        else:
            kind, peer, bot = parse_target(row['target'])
            response = await sender(target_type='private' if kind == 'user' else 'group',
                                    target_id=peer, bot_uin=bot or None, text=row['text'])
    except Exception:
        response = {'ok': False, 'delivery': 'unknown', 'error': {'code': 'sender_exception'}}
    # Retain a bounded diagnostic receipt, never provider payload/private text.
    receipt = {'state': outcome(response)}
    if isinstance(response, dict) and isinstance(response.get('error'), dict):
        receipt['code'] = str(response['error'].get('code', ''))[:100]
    if isinstance(response, dict):
        for key in ('message_id', 'messageId', 'receiptId', 'code'):
            value = response.get(key)
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                receipt[key] = str(value)[:200]
    def settle():
        with store.db() as db:
            db.execute('UPDATE source_notification_attempts SET state=?,response=? WHERE source=? AND identity=? AND target=?',
                       (receipt['state'], json.dumps(receipt), row['source'], row['identity'], row['target']))
    await asyncio.to_thread(settle)
    return True


def browser_event(store, ref):
    with store.db() as db:
        row = db.execute('''SELECT e.*,a.state FROM source_notification_events e
            JOIN source_notification_attempts a USING(source,identity)
            WHERE e.ref=? AND a.target='browser' ''', (ref,)).fetchone()
        if row is None or row['state'] not in ('attempted', 'queued') or not visible(row['owner']):
            return None
        current = routes(row['source'], row['owner']).get('browser', {})
        original = json.loads(row['targets']).get('browser', {})
        if not any(current.get(origin) == epoch for origin, epoch in original.items()):
            return None
        return {'title': 'Pan: New error' if row['source'] == 'newError' else 'Pan: Locker new unread',
                'body': row['text'], 'sessionId': row['owner'], 'source': row['source']}


def invalidate_session(owner, previous_routes=None):
    """Changing Session connections retires their original admission grants."""
    from packages.interactions.api import get_store
    with get_store().db() as db:
        for row in db.execute("SELECT source,identity,targets FROM source_notification_events WHERE owner=? AND source='newError'", (owner,)).fetchall():
            grants = json.loads(row['targets'])
            for target, origins in grants.items():
                if previous_routes is None or origins.get('session') == previous_routes.get(target, {}).get('session'):
                    origins.pop('session', None)
            db.execute('UPDATE source_notification_events SET targets=? WHERE source=? AND identity=?',
                       (json.dumps(grants), row['source'], row['identity']))


def start(sender, browser_sender=None):
    global _task, _browser_sender
    if _task is not None and not _task.done():
        return
    _browser_sender = browser_sender
    async def pump():
        store = None
        while True:
            try:
                if store is None:
                    from packages.interactions.api import get_store
                    store = await asyncio.to_thread(get_store)
                more = await drain_once(sender, store)
            except Exception:
                _log.warning('Notification delivery failed; attempted identities will not retry', exc_info=False)
                more = False
            await asyncio.sleep(0 if more else 1)
    _task = asyncio.create_task(pump(), name='source-notifications')


async def stop():
    global _task, _browser_sender
    if _task is not None:
        _task.cancel()
        await asyncio.gather(_task, return_exceptions=True)
        _task = None
    _browser_sender = None
