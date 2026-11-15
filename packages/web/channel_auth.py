"""Ephemeral credentials for the existing QQ/WeChat Core callbacks only.

Minted by the owning Pan process and injected only into its channel child.
They never authenticate a user, access settings, or authorize arbitrary Sessions.
"""
import asyncio
import gzip
import hmac
import json
import secrets
import re
from urllib.parse import urlsplit

from starlette.responses import JSONResponse

HEADER = 'x-pan-channel-credential'
ENV = 'PAN_CHANNEL_CREDENTIAL'
_tokens = {}


def child_environment(channel):
    if channel not in {'qq', 'wechat'}:
        raise ValueError('Unknown channel')
    token = _tokens.setdefault(channel, secrets.token_urlsafe(32))
    return {ENV: token}


def identify(headers):
    value = headers.get(HEADER, '')
    if not value:
        return None
    return next((name for name, token in _tokens.items() if hmac.compare_digest(value.encode(), token.encode())), None)


def belongs(channel, session):
    return session is not None and (session.name.startswith(('qq-', 'qqg-')) if channel == 'qq'
                                    else session.name.startswith('wx-'))


def permitted(channel, method, path, body, get_session):
    parsed = urlsplit(path)
    if parsed.query or parsed.fragment or parsed.netloc or parsed.scheme:
        return False
    if method == 'GET' and path in {'/api/sessions', '/api/models', '/api/manifest/command-routes'}:
        return True
    if method == 'POST' and path == f'/api/{channel}/notify':
        return True  # Original subscription/Locker gates still own recipients.
    if path == '/api/sessions' and method == 'POST':
        return (set(body) == {'name'} and isinstance(body['name'], str)
                and belongs(channel, type('Name', (), {'name': body['name']})()))
    match = re.fullmatch(r'/api/sessions/(ses_[A-Za-z0-9_-]+)', path)
    if match:
        return belongs(channel, get_session(match[1])) and (method == 'GET' or
            method == 'PATCH' and channel == 'qq' and set(body) == {'gameId'})
    if method == 'POST' and path in {'/api/spawn', '/api/task'}:
        return (set(body) <= ({'sessionId'} if path.endswith('spawn') else {'sessionId', 'text'})
                and belongs(channel, get_session(body.get('sessionId', ''))))
    return False


async def dispatch(app, scope, receive, send, channel):
    from packages.core import session
    def get(sid):
        return session.get(sid, load_history=False)
    headers = dict(scope['headers'])
    if b'origin' in headers or any(k.startswith(b'sec-fetch-') for k in headers):
        return await _deny(scope, receive, send)
    if scope['type'] == 'websocket':
        if channel != 'qq' or scope['path'] != '/ws/agent':
            return await _deny(scope, receive, send)
        async def checked_receive():
            message = await receive()
            if message['type'] != 'websocket.receive':
                return message
            try:
                value = json.loads(message.get('text', ''))
                allowed = value.get('type') in {'subscribe', 'reconnect'}
                allowed = allowed and set(value) <= {'type', 'eventTypes', 'sessionIds'}
                allowed = allowed and value.get('eventTypes', ['worker.result']) == ['worker.result']
                for sid in value.get('sessionIds', []):
                    allowed = allowed and await asyncio.to_thread(lambda: belongs(channel, get(sid)))
                if allowed:
                    return message
            except (ValueError, TypeError, AttributeError):
                pass
            await send({'type': 'websocket.close', 'code': 4403})
            return {'type': 'websocket.disconnect', 'code': 4403}
        async def checked_send(message):
            if message['type'] == 'websocket.send':
                value = json.loads(message.get('text', '{}'))
                if value.get('type') == 'worker.result':
                    if not await asyncio.to_thread(lambda: belongs(channel, get(value.get('sessionId', '')))):
                        return
            await send(message)
        return await app(scope, checked_receive, checked_send)
    raw = bytearray()
    while True:
        message = await receive()
        if message['type'] == 'http.disconnect':
            return
        raw.extend(message.get('body', b''))
        if len(raw) > 256 * 1024:
            return await _deny(scope, receive, send)
        if not message.get('more_body'):
            break
    try:
        body = json.loads(raw) if raw else {}
        allowed = isinstance(body, dict) and await asyncio.to_thread(
            permitted, channel, scope['method'], scope['path'], body, get)
    except (ValueError, TypeError, KeyError):
        allowed = False
    if not allowed:
        return await _deny(scope, receive, send)
    consumed = False
    async def replay():
        nonlocal consumed
        if not consumed:
            consumed = True
            return {'type': 'http.request', 'body': bytes(raw), 'more_body': False}
        return await receive()
    # Filter Session discovery before it leaves the authenticated channel.
    if scope['method'] == 'GET' and scope['path'] == '/api/sessions':
        messages = []
        async def capture(message):
            messages.append(message)
        await app(scope, replay, capture)
        payload = b''.join(m.get('body', b'') for m in messages)
        response_start = next((m for m in messages if m.get('type') == 'http.response.start'), {})
        response_headers = dict(response_start.get('headers', []))
        if response_headers.get(b'content-encoding') == b'gzip':
            payload = gzip.decompress(payload)
        value = json.loads(payload)
        if isinstance(value, dict) and isinstance(value.get('sessions'), list):
            value['sessions'] = [s for s in value['sessions'] if belongs(channel, type('Name', (), {'name': s.get('name', '')})())]
        return await JSONResponse(value)(scope, receive, send)
    await app(scope, replay, send)


async def _deny(scope, receive, send):
    if scope['type'] == 'websocket':
        return await send({'type': 'websocket.close', 'code': 4403})
    return await JSONResponse({'detail': 'Channel scope denied'}, status_code=403)(scope, receive, send)
