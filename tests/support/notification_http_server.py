"""Owned recording-only QQ/System fixture; never starts a provider Worker."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from fastapi import HTTPException, Request
from packages.web import server
from packages.core import notifications, session, session_error_log, worker

data_root = Path(os.environ['PAN_E2E_DATA_ROOT']).resolve()
record_path = data_root / 'notification-records.jsonl'


def record(value):
    with record_path.open('a', encoding='utf-8') as output:
        output.write(json.dumps(value) + '\n')


def check(request):
    if request.headers.get('x-notification-fixture') != os.environ['PAN_TEST_NOTIFICATION_NONCE']:
        raise HTTPException(403)


async def fake_post(path, body):
    assert path == '/api/qq/send', 'No real QQ provider is permitted'
    record({'channel': 'qq', **body})
    return {'ok': True, 'message_id': 'recording-fake'}


async def fake_get(path, params=None):
    if path == '/api/qq/channels':
        return {'ok': True, 'channels': [{'name': 'Recording fake bot', 'bot_uin': '456', 'connected': True}]}
    assert path == '/api/qq/recent_contacts'
    return {'ok': True, 'contacts': [{'peerName': 'Recording fake contact', 'peerUin': '123', 'chatType': 1}]}


def fake_system(title, body):
    record({'channel': 'system', 'title': title, 'text': body})
    return {'ok': True, 'delivery': 'unknown', 'code': 'recording_fake'}


async def forbidden_spawn(*args, **kwargs):
    raise AssertionError('Notification acceptance must not launch a CLI Worker')


server._qq_plugin_post = fake_post
server._qq_plugin_get = fake_get
notifications.set_system_sender(fake_system)
worker._spawn_process = forbidden_spawn


@server.app.post('/__test/notification/emit')
async def emit(payload: dict, request: Request):
    check(request)
    owner = payload['owner']
    if payload['kind'] == 'locker':
        from packages.interactions.locker_messages import accept_locker_message
        row = accept_locker_message(owner, 'user', {'messageId': payload['id'], 'role': 'assistant',
            'content': 'Owned Locker content password=private-value', 'ts': '2026-10-27T00:00:00Z'})
        return {'id': row['id']}
    assert payload['kind'] == 'error'
    eid = await session_error_log.record_session_error_async(kind='terminal_error', terminal=True,
        error_text='Owned error password=private-value', error_text_source='pan.runtime', session_id=owner,
        cli_raw={'error_source': 'codex_error_payload', 'error': {'codexErrorInfo': payload['code'], 'message': 'Owned structured error'}} if 'code' in payload else None,
        task_seq=payload['id'], dedupe_token=str(payload['id']))
    return {'id': eid}


@server.app.get('/__test/notification/records')
async def records(request: Request):
    check(request)
    from packages.interactions.api import get_store
    with get_store().db() as db:
        attempts = [dict(row) for row in db.execute('SELECT source,identity,target,state FROM source_notification_attempts')]
        refs = [row[0] for row in db.execute("SELECT e.ref FROM source_notification_events e JOIN source_notification_attempts a USING(source,identity) WHERE a.target='browser'")]
        events = [dict(row) for row in db.execute('SELECT identity,exclusion_epoch,targets FROM source_notification_events')]
        browser_refs = [dict(row) for row in db.execute("SELECT e.identity,e.ref FROM source_notification_events e JOIN source_notification_attempts a USING(source,identity) WHERE a.target='browser'")]
    return {'records': [json.loads(line) for line in record_path.read_text(encoding='utf-8').splitlines()] if record_path.exists() else [],
            'attempts': attempts, 'refs': refs, 'browserRefs': browser_refs, 'events': events, 'workers': len(worker.list_workers())}


import tests.support.job_inbox_http_server  # noqa: E402,F401
