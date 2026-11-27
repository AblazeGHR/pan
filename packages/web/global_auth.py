"""One default-deny ASGI boundary, including mounted apps and WebSockets.

Network metadata never establishes identity. Trusted in-process object IPC keeps
its existing narrow allowlist and Agent authorization; it is not a user login.
"""
import asyncio
import re
from urllib.parse import urlsplit, quote_from_bytes

from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse, Response, RedirectResponse
from starlette.datastructures import MutableHeaders

from packages.automation.authorization import delegated, object_path

COOKIE = 'pan_automation_session'
PUBLIC = {
    ('GET', '/'), ('GET', '/react/'), ('GET', '/react/index.html'),
    ('GET', '/api/health'), ('GET', '/api/automation/session'),
    ('POST', '/api/automation/login'),
}
ASSET = re.compile(r'/react/assets/[A-Za-z0-9_.-]+\.(?:js|css|svg|woff2?|png|ico)$')
SPA_ROUTE = re.compile(r'/react/(?:locker|actions|automation|editor|jobs|terminals|schedules|manage/[^/.]+|plugins/[^/.]+/[^/.]+)/?')
WRITES = {'POST', 'PUT', 'PATCH', 'DELETE'}


def same_origin(connection):
    try:
        origin = urlsplit(connection.headers.get('origin', ''))
        target = urlsplit(connection.scope.get('pan.auth.external-origin', str(connection.url)))
        scheme = {'ws': 'http', 'wss': 'https'}.get(target.scheme, target.scheme)
        return (origin.scheme in ('http', 'https') and origin.username is None
                and origin.password is None and not origin.path and not origin.query
                and not origin.fragment and origin.hostname is not None
                and (origin.scheme, origin.hostname, origin.port if origin.port is not None else (443 if origin.scheme == 'https' else 80))
                == (scheme, target.hostname, target.port if target.port is not None else (443 if scheme == 'https' else 80)))
    except ValueError:
        return False


def set_cookie(response, connection, token, days):
    response.set_cookie(COOKIE, f'{token}.{days}', httponly=True, samesite='lax', path='/',
                        secure=connection.url.scheme in ('https', 'wss') or connection.scope.get('pan.auth.secure', False),
                        max_age=days * 86400 if days else None)


class GlobalAuthMiddleware:
    def __init__(self, app, server):
        self.app = app
        self.server = server

    async def __call__(self, scope, receive, send):
        if scope['type'] not in {'http', 'websocket'}:
            return await self.app(scope, receive, send)
        connection = HTTPConnection(scope)
        # Explicit external origins support HTTPS-terminating proxies without
        # trusting their Forwarded headers or using proxy metadata as identity.
        configured = getattr(self.server.state, 'global_auth_origins', ())
        if not isinstance(configured, (list, tuple)):
            return await JSONResponse({'detail': 'Invalid authentication origins'}, status_code=503)(scope, receive, send)
        for value in configured:
            if not isinstance(value, str):
                continue
            external = urlsplit(value)
            if (external.scheme == 'https' and external.netloc == connection.headers.get('host')
                    and external.username is None and external.password is None
                    and not external.path and not external.query and not external.fragment):
                scope['pan.auth.external-origin'] = value
                scope['pan.auth.secure'] = True
                break
        ws = scope['type'] == 'websocket'
        path = scope['path']
        method = scope.get('method', '')
        public = not ws and ((method, path) in PUBLIC or method in {'GET', 'HEAD'} and ASSET.fullmatch(path))
        navigation = not ws and method == 'GET' and 'text/html' in connection.headers.get('accept', '')
        if navigation and SPA_ROUTE.fullmatch(path):
            public = True
        # This marker can only be set by the in-process object IPC adapter.
        # Never let it turn into unrestricted HTTP authority.
        if delegated(connection) and object_path(path) and not ws:
            return await self.app(scope, receive, send)
        from .machine_ipc import trusted
        if not ws and trusted(scope):
            return await self.app(scope, receive, send)
        from .channel_auth import HEADER, identify, dispatch
        if HEADER in connection.headers:
            channel = identify(connection.headers)
            if channel:
                return await dispatch(self.app, scope, receive, send, channel)
            if ws:
                return await send({'type': 'websocket.close', 'code': 4401})
            return await JSONResponse({'detail': 'Invalid channel credential'}, status_code=401)(scope, receive, send)
        header = connection.headers.get('authorization', '')
        scheme, _, token = header.partition(' ')
        bearer = bool(header)
        cookie_days = ''
        if not bearer:
            cookie = connection.cookies.get(COOKIE, '')
            token, separator, cookie_days = cookie.rpartition('.')
            if not separator:
                token, cookie_days = cookie, ''
        elif scheme.lower() != 'bearer':
            token = ''
        auth = getattr(getattr(self.server.state, 'automation', None), 'auth', None)
        state = None

        async def reject(status, detail):
            if ws:
                await send({'type': 'websocket.close', 'code': 4403 if status == 403 else 4401})
            else:
                await JSONResponse({'detail': detail}, status_code=status,
                                   headers={'Cache-Control': 'no-store'})(scope, receive, send)

        # Login CSRF must be checked even before any Cookie exists.
        if (not ws and path == '/api/automation/login' and method == 'POST'
                and not same_origin(connection)):
            return await reject(403, 'Login requires a same-origin Origin')
        try:
            maintenance = getattr(self.server.state, 'job_maintenance', None)
            if (maintenance and maintenance.get('mode') == 'maintenance'
                    and maintenance.get('error', {}).get('code') == 'runtime_ownership_unverified'):
                from packages.jobs.maintenance import authority
                auth = authority(maintenance)

            if token and auth is not None:
                state = await asyncio.to_thread(auth.session, token, False)
            valid = state is not None and state['authenticated']
            if not public and not valid:
                if navigation and path == '/react':
                    query = quote_from_bytes(scope.get('query_string', b''), safe="!$&'()*+,-./;=?@_~%")
                    return await RedirectResponse('/react/' + ('?' + query if query else ''), status_code=303)(scope, receive, send)
                return await reject(401, 'User login required')
            if valid:
                if (ws and (not bearer or 'origin' in connection.headers)
                        or not ws and not bearer and method in WRITES) and not same_origin(connection):
                    return await reject(403, 'A same-origin Origin is required')
                state = await asyncio.to_thread(auth.session, token)
                # Existing route authorization sees the same UserAuth identity.
                if not bearer:
                    scope['headers'] = [(k, v) for k, v in scope['headers'] if k.lower() != b'authorization'] + [
                        (b'authorization', ('Bearer ' + token).encode())]
        except (OSError, ValueError, TypeError, KeyError):
            return await reject(503, 'Authentication storage unavailable')

        if ws:
            return await self._websocket(scope, receive, send, auth, token)

        async def send_headers(message):
            if message['type'] == 'http.response.start':
                headers = MutableHeaders(scope=message)
                headers['Cache-Control'] = 'no-store' if not ASSET.fullmatch(path) else 'public, max-age=31536000, immutable'
                if (state and state['authenticated'] and not bearer
                        and (state['renewed'] or cookie_days != str(state['sessionDays']))
                        and 'set-cookie' not in headers):
                    response = Response()
                    set_cookie(response, connection, token, state['sessionDays'])
                    headers.append('set-cookie', response.headers['set-cookie'])
            await send(message)
        await self.app(scope, receive, send_headers)

    async def _websocket(self, scope, receive, send, auth, token):
        """Bound revocation latency even for an idle or outbound-only socket."""
        closed = False
        lock = asyncio.Lock()

        async def checked_send(message):
            nonlocal closed
            async with lock:
                if closed:
                    return
                if message['type'] == 'websocket.close':
                    closed = True
                await send(message)

        task = asyncio.create_task(self.app(scope, receive, checked_send))
        try:
            while not task.done():
                done, _ = await asyncio.wait({task}, timeout=1)
                if done:
                    break
                try:
                    valid = (await asyncio.to_thread(auth.session, token, False))['authenticated']
                except (OSError, ValueError, TypeError, KeyError):
                    valid = False
                if not valid:
                    await checked_send({'type': 'websocket.close', 'code': 4401})
                    task.cancel()
                    break
            result = (await asyncio.gather(task, return_exceptions=True))[0]
            if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
                raise result
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
