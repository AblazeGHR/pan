"""Existing same-user plugin IPC -> explicitly scoped Core operations.

There is no HTTP machine token and no network-accessible credential exchange.
Session IDs select a principal only after the original OS-local IPC boundary.
"""
import re
from urllib.parse import parse_qs, unquote, urlsplit

import httpx

from packages.plugins.lifecycle import io

_MARKER = object()
_SCOPE = 'pan.core.machine-ipc'

METADATA = {
    '/api/adapters', '/api/models', '/api/model-catalog',
    '/api/automation/system-fields', '/api/cbc/projects', '/api/kimi/workspaces',
    '/api/cbc/sessions', '/api/kimi/sessions', '/api/codex/sessions',
    '/api/claude/sessions', '/api/opencode/sessions', '/api/codex/quota',
}
SESSION = re.compile(r'/api/sessions/([a-zA-Z0-9_-]+)(?:/(?:managers|messages/[^/]+|usage|workspaces|handoff|reminders(?:/[^/]+)?|worker/(?:kill|delayed-restart)))?')
SELF = re.compile(r'/api/(?:self-queue/(?:read|consume|report-status|report-consume)|terminal-tools/[a-z_-]+|(?:reminding-card|locker-message|interaction-card)-tools/[a-z_-]+)')
CONTROL = {'/api/spawn', '/api/task', '/api/assign', '/api/send', '/api/notify',
           '/api/claim', '/api/unclaim', '/api/readonly', '/api/report-subscribe',
           '/api/report-unsubscribe', '/api/notifications/send', '/api/sessions/broadcast',
           '/api/sessions/batch-delete', '/api/qq/subscribe', '/api/qq/unsubscribe',
           '/api/wechat/subscribe', '/api/wechat/unsubscribe'}


def trusted(scope):
    return scope.get(_SCOPE) is _MARKER


def denied():
    return {'ok': False, 'error': {'code': 'permission_denied', 'message': 'Outside bound Core IPC scope'}}


def session_allowed(caller, target):
    return (isinstance(target, str) and bool(target) and
            (target == caller.id or not caller.restrict_to_managed or target in caller.managed))


def targets(body, query):
    result = []
    for key in ('sessionId', 'targetSessionId', 'targetSessionIds', 'sessionIds', 'ownerSessionId'):
        value = body.get(key)
        if value is not None:
            result.extend(value if isinstance(value, list) else [value])
        result.extend(query.get(key, []))
    return result


def permit(caller, method, path, body, get_session):
    parsed = urlsplit(path)
    decoded = unquote(parsed.path)
    encoded_message = re.fullmatch(
        r'/api/sessions/[a-zA-Z0-9_-]+/messages/pan%3[aA](?:[0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})', parsed.path)
    if (parsed.scheme or parsed.netloc or parsed.fragment or not path.startswith('/api/')
            or (decoded != parsed.path and not encoded_message)
            or any(p in {'.', '..'} for p in decoded.split('/'))):
        return False
    route, query = parsed.path, parse_qs(parsed.query)
    if method not in {'GET', 'POST', 'PUT', 'PATCH', 'DELETE'} or not isinstance(body, dict):
        return False
    if any(key in body and body[key] != caller.id for key in
           ('managerId', 'creatorSessionId', 'sourceSessionId', 'callerSessionId')):
        return False
    if any(not session_allowed(caller, target) for target in targets(body, query)):
        # Claim has its own existing server owner fence; only allow a caller
        # with the original canClaimUnmanaged capability to attempt it.
        if route != '/api/claim' or not caller.can_claim_unmanaged:
            return False
        target = get_session(body.get('sessionId', ''))
        if target is not None and target.managed_by not in (None, '', caller.id):
            return False
    match = SESSION.fullmatch(route)
    if match:
        if not session_allowed(caller, match[1]):
            return False
        # MCP does not offer browser queue/permissions endpoints. In particular
        # a raw transport call cannot rewrite its caller's capability flags.
        if method == 'PATCH' and any(key in body for key in ('panAccess', 'managed', 'managedBy', 'restrictToManaged')):
            return False
        return True
    if SELF.fullmatch(route):
        return method == 'POST'
    if route in CONTROL:
        return method == 'POST'
    if route in METADATA or route in {'/api/sessions', '/api/list'}:
        return method == 'GET' or route == '/api/sessions' and method == 'POST'
    if route == '/api/history/search':
        return method == 'GET' and len(query.get('sessionId', [])) == 1
    if re.fullmatch(r'/api/workspaces(?:/[a-zA-Z0-9_-]+)?', route):
        return method in {'GET', 'POST', 'DELETE'}
    if re.fullmatch(r'/api/(?:cbc|kimi|codex|claude|opencode)/import', route):
        return method == 'POST'
    if re.fullmatch(r'/api/agent-inbox/(?:list|read|consume|subscribe|unsubscribe|mode|backlog|recover)', route):
        return method == 'POST'
    if re.fullmatch(r'/api/interaction-cards/[^/]+(?:/receipt)?', route):
        return method == 'GET' and len(query.get('ownerSessionId', [])) == 1
    if route in {'/api/background-jobs', '/api/session-message-jobs'}:
        return method == 'GET' or method == 'POST' and bool(targets(body, query))
    if route == '/api/scheduler/tasks':
        return method == 'GET' or method == 'POST' and bool(targets(body, query))
    scheduled = re.fullmatch(r'/api/scheduler/tasks/([^/]+)(?:/(?:pause|resume|run-now|runs))?', route)
    if scheduled:
        from packages.scheduler import store
        record = store.get_task(scheduled[1])
        return (record is None and method == 'GET' or record is not None
                and bool(targets(record, {}))
                and all(session_allowed(caller, sid) for sid in targets(record, {})))
    job = re.fullmatch(r'/api/background-jobs/(job_[a-zA-Z0-9_-]+)(?:/(?:cancel|retry))?', route)
    if job:
        from packages.core import background_jobs
        record = background_jobs.get(job[1])
        if record is None:
            return method == 'GET'
        owned = targets(record, {})
        return bool(owned) and all(session_allowed(caller, sid) for sid in owned)
    return False


def bind(runtime, app, get_session):
    async def cli(method, path, body, instance=None):
        if (method, path) not in {
            ('POST', '/api/system-actions/main-lifecycle'),
            ('GET', '/api/main/identity'), ('POST', '/api/main/exit'),
            ('POST', '/api/main/restart'), ('POST', '/api/main/console'),
            ('POST', '/api/internal/main/shutdown'),
        }:
            return denied()
        headers = {}
        if isinstance(instance, dict):
            headers = {'X-Pan-Expected-Pid': str(instance.get('pid')),
                       'X-Pan-Expected-Created-At': str(instance.get('createdAt'))}
        async def transport(scope, receive, send):
            scope[_SCOPE] = _MARKER
            await app(scope, receive, send)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=transport, client=('127.0.0.1', 0)),
                                     base_url='http://ipc', trust_env=False, follow_redirects=False) as client:
            response = await client.request(method, path, json=body if method != 'GET' else None, headers=headers)
        if response.status_code >= 400:
            return {'ok': False, 'error': {'code': response.status_code, 'message': 'Lifecycle IPC rejected'}}
        return response.json()
    runtime.cli_http = cli

    async def call(sid, method, path, body):
        caller = await io(get_session, sid)
        if caller is None or not isinstance(path, str):
            return denied()
        body = {} if body is None else body
        if not await io(permit, caller, method, path, body, get_session):
            return denied()
        headers = {'X-Pan-Queue-Caller': caller.id, 'X-Pan-Terminal-Caller': caller.id}
        async def transport(scope, receive, send):
            scope[_SCOPE] = _MARKER
            await app(scope, receive, send)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=transport, client=('127.0.0.1', 0)),
                                     base_url='http://ipc', trust_env=False, follow_redirects=False) as client:
            response = await client.request(method, path, json=body if method != 'GET' else None, headers=headers)
        if response.status_code >= 400:
            return {'ok': False, 'error': {'code': response.status_code, 'message': 'Core IPC operation rejected'}}
        result = response.json()
        if caller.restrict_to_managed and isinstance(result, dict):
            for key in ('sessions', 'workers'):
                if isinstance(result.get(key), list):
                    result[key] = [item for item in result[key] if isinstance(item, dict)
                                   and session_allowed(caller, item.get('sessionId', item.get('id')))]
            if isinstance(result.get('jobs'), list):
                result['jobs'] = [job for job in result['jobs'] if targets(job, {})
                                  and all(session_allowed(caller, sid) for sid in targets(job, {}))]
            if isinstance(result.get('tasks'), list):
                result['tasks'] = [item for item in result['tasks'] if targets(item, {})
                                   and all(session_allowed(caller, sid) for sid in targets(item, {}))]
        return result
    runtime.core_http = call
