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


def canonical_path(path):
    """Public canonical resources only; deprecated aliases are HTTP-only."""
    parsed = urlsplit(path)
    if parsed.scheme or parsed.netloc or parsed.fragment or not path.startswith('/api/'):
        return False
    if parsed.path == '/api/jobs/actions':
        return True
    # These are concrete legacy routes, even where their segment looks like an ID.
    if re.match(r'/api/jobs/(?:action-jobs|job-templates|action-references|kinds|templates|next|settings)(?:/|$)', parsed.path):
        return False
    return bool(re.fullmatch(
        r'/api/(?:jobs(?:/[A-Za-z0-9_-]+(?:/(?:runs|operations))?)?'
        r'|(?:triggers|event-creators|job-templates)(?:/[A-Za-z0-9_-]+(?:/operations)?)?'
        r'|job-runs/[A-Za-z0-9_-]+(?:/operations)?|operations(?:/[A-Za-z0-9_-]+)?)', parsed.path))


READ, CANCEL = 'read', 'cancel'

#: Historical non-canonical addresses and the only method class each may serve.
#: Everything absent here — every retired legacy *write* root, including the
#: shared list/create addresses below — is not writable through the bridge.
_LEGACY_SURFACE = (
    # Historical Job/Template reads: retained until the last legacy owner drains.
    (r'/api/jobs/action-jobs', READ),
    (r'/api/jobs/action-jobs/schema', READ),
    (r'/api/jobs/action-jobs/[A-Za-z0-9_-]+/runs', READ),
    (r'/api/jobs/action-references/[A-Za-z0-9_-]+', READ),
    (r'/api/jobs/job-templates', READ),
    # Legacy Action catalog/template reads (``action_drafts`` discovery only).
    (r'/api/actions', READ),
    (r'/api/actions/session-operation-template', READ),
    # Legacy background Job and scheduler aliases kept inspectable for R6.
    (r'/api/background-jobs', READ),
    (r'/api/background-jobs/[A-Za-z0-9_-]+', READ),
    (r'/api/scheduler/tasks', READ),
    (r'/api/scheduler/tasks/[A-Za-z0-9_-]+', READ),
    (r'/api/scheduler/tasks/[A-Za-z0-9_-]+/runs', READ),
    (r'/api/scheduler/next', READ),
    (r'/api/scheduler/status', READ),
    # Explicit historical cancel controls: the only retained legacy POST.
    (r'/api/jobs/action-jobs/[A-Za-z0-9_-]+/cancel', CANCEL),
    (r'/api/background-jobs/[A-Za-z0-9_-]+/cancel', CANCEL),
)
_LEGACY_METHODS = {READ: {'GET', 'HEAD', 'OPTIONS'}, CANCEL: {'POST'}}


def _legacy_surface(path):
    """Method class of a retained historical address, or ``None``."""
    for pattern, kind in _LEGACY_SURFACE:
        if re.fullmatch(pattern, path):
            return kind
    return None


def object_path(path, method=None):
    """Narrow trusted bridge allowlist: canonical objects plus retained history.

    Canonical ``/api`` resources own every write, so they stay fully
    method-addressable.  Historical addresses keep only the method class they
    were inventoried for: reads and the explicit ``/cancel`` controls.
    Passing *method* (as the IPC bridge always does)
    therefore refuses a retired legacy write; omitting it answers "is this
    address part of either retained surface", which catalog checks need without
    silently granting a write.
    """
    parsed = urlsplit(path)
    if parsed.scheme or parsed.netloc or parsed.fragment or not path.startswith('/api/'):
        return False
    if canonical_path(path):
        return True
    kind = _legacy_surface(parsed.path)
    if kind is None:
        return False
    if method is None:
        return True
    return method.upper() in _LEGACY_METHODS[kind]


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
        if method not in {'GET','POST','PUT','DELETE','PATCH'} or not object_path(path, method):
            return {'ok': False, 'error': {'code': 403, 'message': 'Outside four-object MCP boundary'}}
        async def transport(scope, receive, send):
            scope[_SCOPE_KEY] = _MARKER
            await app(scope, receive, send)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=transport), base_url='http://ipc', trust_env=False, follow_redirects=False) as client:
            response = await client.request(method, path, json=body if body is not None else None)
        result = response.json()
        if response.status_code >= 400:
            if canonical_path(path):
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


class LegacyObjectWriteRetired(ValueError):
    """The compatibility tool lacks the canonical operation envelope."""


def object_request(kind, operation, item=None, item_id=None, expected_revision=None,
                   idempotency_key=None):
    """Retained four-object reads; writes use the canonical operation tools.

    The old tool cannot express both stateRevision and configRevision, a Run
    identity, or the canonical request envelope.  Guessing these values would
    bypass CAS or conflate retry with a new Run.  Keep its discovery/read
    compatibility and reject writes before transport; the canonical MCP tools
    already expose each complete operation envelope.
    """
    bases = {'trigger': '/api/triggers', 'action': '/api/jobs/actions',
             'jobTemplate': '/api/job-templates', 'job': '/api/jobs'}
    if kind not in bases:
        raise ValueError('Unknown object kind')
    if operation == 'read':
        return 'GET', bases[kind], None
    raise LegacyObjectWriteRetired('Legacy object writes retired; use canonical operation tools')


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
