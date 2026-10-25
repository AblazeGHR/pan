"""Four-object authorization. HTTP is user Bearer; MCP authority comes only from local IPC.

The plugin bridge is a same-OS-user local capability boundary, not an OS sandbox.
No body or HTTP header can construct the private ASGI scope marker.
"""
import re
from urllib.parse import urlsplit
from fastapi import HTTPException
from packages.plugins.lifecycle import io

_MARKER = object()
_SCOPE_KEY = "pan.authorization.transport"


def object_path(path):
    """Narrow bridge allowlist; no groups, other MCPs or arbitrary HTTP proxy."""
    parsed = urlsplit(path)
    if parsed.scheme or parsed.netloc or parsed.fragment or not path.startswith('/api/'):
        return False
    if re.fullmatch(
        r'/api/final/(?:jobs(?:/[A-Za-z0-9_-]+(?:/(?:runs|operations))?)?'
        r'|(?:triggers|event-creators|job-templates)(?:/[A-Za-z0-9_-]+(?:/operations)?)?'
        r'|job-runs/[A-Za-z0-9_-]+(?:/operations)?|operations(?:/[A-Za-z0-9_-]+)?|actions)', parsed.path):
        return True
    if re.fullmatch(r'/api/(?:background-jobs(?:/[^/]+(?:/(?:cancel|retry))?)?|session-message-jobs)', parsed.path):
        return True
    return bool(re.fullmatch(
        r'/api/(?:actions(?:/actions(?:/[a-z0-9_]+)?|/session-operation-template)?'
        r'|actions/actions/[a-z0-9_]+/lifecycle'
        r'|action-drafts/actions/(?:put|delete)'
        r'|automation(?:/triggers(?:/[^/]+(?:/lifecycle)?)?|/runs/[^/]+/(?:cancel|confirm-recovery))?'
        r'|jobs/(?:action-jobs(?:/[^/]+(?:/(?:runs|run-now|cancel|retry|confirm-recovery|pause|resume|lifecycle))?)?'
        r'|job-templates(?:/[^/]+(?:/instantiate)?)?|action-references/[^/]+))', parsed.path))


def delegated(request):
    return request.scope.get(_SCOPE_KEY) is _MARKER


async def principal(request):
    svc = getattr(request.app.state, 'automation', None)
    if svc is None:
        raise HTTPException(503, 'Authorization unavailable')
    if delegated(request):
        return 'agent'
    if await io(svc.auth.authorized, request.headers.get('authorization', '')):
        return 'user'
    raise HTTPException(401, 'User Bearer or trusted MCP transport required')


async def require(request, kind=None, capability=None):
    who = await principal(request)
    if who == 'agent' and kind is not None:
        allowed, _ = await io(request.app.state.automation.auth.permission_allowed, kind, capability)
        if not allowed:
            raise HTTPException(403, f'Authorization denied: {kind}.{capability}')
    return who


async def authority(request):
    who = await principal(request)
    if who == 'user':
        return {'type': 'user'}
    state = await io(request.app.state.automation.auth.permissions_status)
    return {'type': 'agent', 'revision': state['revision']}


def bind_objects(runtime, app):
    """Only called by the host; reused routes retain their normal business gates."""
    import httpx
    async def call(method, path, body):
        if method not in {'GET','POST','PUT','DELETE','PATCH'} or not object_path(path):
            return {'ok': False, 'error': {'code': 403, 'message': 'Outside four-object MCP boundary'}}
        async def transport(scope, receive, send):
            scope[_SCOPE_KEY] = _MARKER
            await app(scope, receive, send)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=transport), base_url='http://ipc', trust_env=False, follow_redirects=False) as client:
            response = await client.request(method, path, json=body if body is not None else None)
        result = response.json()
        if response.status_code >= 400:
            if path.startswith('/api/final/'):
                return result  # WP01 structured error code/path/details, unchanged
            # Static authorization/validation response, never credentials or body.
            return {'ok': False, 'error': {'code': response.status_code,
                    'message': result.get('detail', 'Object operation rejected')}}
        return result
    runtime.authorization_objects = call


async def require_write(request, kind, old, new, *, ignored=()):
    """A combined update needs both config and lifecycle grants; neither substitutes."""
    await principal(request)
    if not isinstance(new, dict):
        raise HTTPException(400, 'Expected complete object configuration')
    if old is None:
        await require(request, kind, 'create')
        if kind != 'jobTemplate' and new.get('enabled', False):
            await require(request, kind, 'lifecycle')
        return
    lifecycle = kind != 'jobTemplate' and old.get('enabled', False) != new.get('enabled', False)
    ignored = set(ignored) | {'enabled'}
    changed = any(new.get(k) != old.get(k) for k in (set(old) | set(new)) - ignored)
    if changed or not lifecycle:
        await require(request, kind, 'updateConfig')
    if lifecycle:
        await require(request, kind, 'lifecycle')


def object_request(kind, operation, item=None, item_id=None, expected_revision=None,
                   idempotency_key=None):
    from urllib.parse import quote, urlencode
    bases = {'trigger':'/api/automation/triggers', 'action':'/api/actions/actions',
             'jobTemplate':'/api/jobs/job-templates', 'job':'/api/jobs/action-jobs'}
    if kind not in bases:
        raise ValueError('Unknown object kind')
    base = bases[kind]
    path = base + ('/' + quote(item_id, safe='') if item_id else '')
    if operation == 'read':
        return 'GET', {'trigger':'/api/automation','action':'/api/actions'}.get(kind, base), None
    if operation == 'create' or operation == 'updateConfig':
        if not isinstance(item, dict):
            raise ValueError('Complete item required')
        if operation == 'updateConfig' and not item_id:
            raise ValueError('Object ID required')
        payload = {'config' if kind == 'job' else 'item': item}
        if kind in ('trigger', 'jobTemplate') or operation == 'updateConfig':
            payload['expectedRevision'] = expected_revision
        if kind == 'trigger':
            return 'POST', base, payload
        return ('PUT', path, payload) if operation == 'updateConfig' else ('POST', base, payload)
    if not item_id or type(expected_revision) is not int:
        raise ValueError('Object ID and expectedRevision required')
    if operation == 'delete':
        return 'DELETE', path + '?' + urlencode({'expectedRevision':expected_revision}), None
    if operation == 'use' and kind == 'jobTemplate':
        return 'POST', path + '/instantiate', {'expectedRevision':expected_revision, 'idempotencyKey':idempotency_key}
    if operation in ('enable','disable') and kind != 'jobTemplate':
        return 'POST', path + '/lifecycle', {'expectedRevision':expected_revision, 'enabled':operation=='enable'}
    if kind == 'job' and operation in ('pause','resume','cancel','execute','retry','confirmRecovery'):
        suffix = {'execute':'run-now','confirmRecovery':'confirm-recovery'}.get(operation, operation)
        body = {'expectedRevision':expected_revision}
        if operation in ('retry','confirmRecovery'):
            body['confirm'] = True
        return 'POST', path + '/' + suffix, body
    raise ValueError('Operation is not supported for this object')


async def may_read_jobs(request):
    if request is None:
        return False
    try:
        await require(request, 'job', 'read')
        return True
    except HTTPException as exc:
        if exc.status_code in (401,403,503):
            return False
        raise


async def require_job(request, job, capability='read'):
    from packages.jobs.action_runtime import is_action_job
    if is_action_job(job):
        if request is None:
            raise HTTPException(401, 'User Bearer or trusted MCP transport required')
        await require(request, 'job', capability)
