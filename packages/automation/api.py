"""Trigger management only. Event registration/publishing has no public route."""
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from starlette.datastructures import MutableHeaders
from urllib.parse import urlsplit
from packages.core.system_fields import public_schema
from .store import Conflict
from packages.jobs import chains
from packages.jobs.final_api import actor as canonical_actor
from .authorization import require as object_require, require_write, authority
from .policy import SCHEDULING_SCHEMA, MAX_CONCURRENCY, MAX_ATTEMPTS, BACKOFF_SECONDS, BACKOFF_CAP_SECONDS


def install(server):
    from .job_link_api import router as job_link_router
    server.app.include_router(job_link_router)
    router = APIRouter()
    cookie_name = 'pan_automation_session'

    def set_cookie(response, request, token, days):
        response.set_cookie(cookie_name, f'{token}.{days}', httponly=True, samesite='lax', path='/',
                            secure=request.url.scheme == 'https', max_age=days * 86400 if days else None)

    def clear_cookie(response, request):
        response.delete_cookie(cookie_name, path='/', httponly=True, samesite='lax',
                               secure=request.url.scheme == 'https')

    def same_origin(request):
        try:
            origin = urlsplit(request.headers.get('origin', ''))
            target = request.url
            return (origin.scheme in ('http', 'https') and origin.username is None and origin.password is None
                    and not origin.path and not origin.query and not origin.fragment
                    and (origin.scheme, origin.hostname, origin.port if origin.port is not None else (443 if origin.scheme == 'https' else 80))
                    == (target.scheme, target.hostname, target.port if target.port is not None else (443 if target.scheme == 'https' else 80)))
        except ValueError:
            return False

    class CookieAuthMiddleware:
        """Normalize valid Cookie sessions without a task or body-stream relay."""

        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope['type'] != 'http':
                return await self.app(scope, receive, send)
            request = Request(scope)
            svc = getattr(server.app.state, 'automation', None)
            cookie = request.cookies.get(cookie_name, '')
            token, separator, cookie_days = cookie.rpartition('.')
            if not separator:
                token, cookie_days = cookie, ''
            header = request.headers.get('authorization', '')
            bearer = header.lower().startswith('bearer ')
            state = None
            # Explicit Bearer credentials retain their own authentication path.
            # Only the matching browser Cookie needs policy refresh in that case.
            if (svc is not None and token and request.url.path.startswith('/api/')
                    and (not bearer or header.partition(' ')[2] == token)):
                try:
                    state = await svc.engine.io(svc.auth.session, token, False)
                    if state['authenticated']:
                        if not bearer and request.method in {'POST', 'PUT', 'PATCH', 'DELETE'} and not same_origin(request):
                            response = JSONResponse({'detail': 'Cookie writes require a same-origin Origin'}, status_code=403)
                            return await response(scope, receive, send)
                        state = await svc.engine.io(svc.auth.session, token)
                        if not bearer:
                            scope['headers'] = [(k, v) for k, v in scope['headers'] if k != b'authorization'] + [
                                (b'authorization', ('Bearer ' + token).encode())]
                except (OSError, ValueError):
                    response = JSONResponse({'detail': 'Session storage unavailable'}, status_code=503)
                    return await response(scope, receive, send)

            async def send_headers(message):
                if message['type'] == 'http.response.start':
                    headers = MutableHeaders(scope=message)
                    if request.url.path.startswith('/api/automation/'):
                        headers['Cache-Control'] = 'no-store'
                    # The duration marker is a refresh hint, never lifetime
                    # authority. Explicit login/logout/settings own their header.
                    if (state and state['authenticated']
                            and (state['renewed'] or cookie_days != str(state['sessionDays']))
                            and 'set-cookie' not in headers):
                        response = Response()
                        set_cookie(response, request, token, state['sessionDays'])
                        headers.append('set-cookie', response.headers['set-cookie'])
                await send(message)

            await self.app(scope, receive, send_headers)

    server.app.add_middleware(CookieAuthMiddleware)

    def service():
        value = getattr(server.app.state, 'automation', None)
        if value is None:
            raise HTTPException(503, 'Automation runtime unavailable')
        return value

    async def authorized(request):
        return await service().engine.io(service().auth.authorized, request.headers.get('authorization', ''))

    async def require(request):
        if not await authorized(request):
            raise HTTPException(401, 'User login required')

    async def require_permission(request, kind, capability):
        return await object_require(request, kind, capability)

    @router.get('/api/automation/authorization')
    async def authorization_status(request: Request):
        await require(request)
        return await service().engine.io(service().auth.authorization_status)

    @router.put('/api/automation/authorization')
    async def authorization_change(data: dict, request: Request, response: Response):
        await require(request)
        if set(data) != {'currentPassword', 'newPassword', 'confirmNewPassword', 'expectedRevision'}:
            raise HTTPException(400, 'Expected currentPassword, newPassword, confirmNewPassword, and expectedRevision')
        try:
            result = await service().engine.io(
                service().auth.change_password,
                data['currentPassword'], data['newPassword'], data['confirmNewPassword'], data['expectedRevision'])
            clear_cookie(response, request)
            return result
        except PermissionError as exc:
            raise HTTPException(401, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(503, str(exc)) from exc

    @router.put('/api/automation/authorization/permissions')
    async def authorization_permissions(data: dict, request: Request):
        await require(request)
        if set(data) != {'permissions', 'expectedRevision'}:
            raise HTTPException(400, 'Expected permissions and expectedRevision')
        try:
            return await service().engine.io(service().auth.change_permissions,
                                             data['permissions'], data['expectedRevision'])
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(503, str(exc)) from exc

    @router.get('/api/automation/session')
    async def session_status(request: Request):
        header = request.headers.get('authorization', '')
        scheme, _, token = header.partition(' ')
        state = await service().engine.io(service().auth.session, token if scheme.lower() == 'bearer' else '')
        return {k: state[k] for k in ('authenticated', 'expiresAt', 'sessionDays')}

    @router.get('/api/automation/settings')
    async def session_settings(request: Request):
        await require(request)
        return {'sessionDays': service().auth.session_days}

    @router.put('/api/automation/settings')
    async def update_session_settings(data: dict, request: Request, response: Response):
        await require(request)
        if set(data) != {'sessionDays'}:
            raise HTTPException(400, 'Expected sessionDays')
        try:
            result = await service().engine.io(service().auth.change_session_days, data['sessionDays'])
            token = request.headers.get('authorization', '').partition(' ')[2]
            set_cookie(response, request, token, result['sessionDays'])
            return result
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(503, 'Session settings persistence failed') from exc

    @router.post('/api/automation/login')
    async def login(data: dict, request: Request, response: Response):
        try:
            token = await service().engine.io(service().auth.login, data.get('key'))
            set_cookie(response, request, token, service().auth.session_days)
            return {'token': token, 'expiresIn': service().auth.session_lifetime}
        except PermissionError as exc:
            raise HTTPException(401, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(503, 'Session storage unavailable') from exc

    @router.post('/api/automation/logout')
    async def logout(request: Request, response: Response):
        try:
            await service().engine.io(service().auth.logout, request.headers.get('authorization', ''))
        except OSError as exc:
            raise HTTPException(503, 'Session revocation persistence failed') from exc
        clear_cookie(response, request)
        return {'ok': True}

    @router.post('/api/automation/revoke')
    async def revoke(request: Request, response: Response):
        await require(request)
        try:
            await service().engine.io(service().auth.revoke)
        except OSError as exc:
            raise HTTPException(503, 'Session revocation persistence failed') from exc
        clear_cookie(response, request)
        return {'ok': True}

    @router.get('/api/automation')
    async def view(request: Request):
        await require_permission(request, 'trigger', 'read')
        svc = service()
        state = chains.public(await svc.engine.io(svc.ledger.read))
        pending_jobs={op['jobId'] for op in state.get('linkOperations',{}).values() if op.get('jobId') and op['status'] not in {'complete','cancelled'}}
        for trigger in state['triggers'].values():
            if trigger.get('ownerJobId') in pending_jobs:trigger.update(enabled=False,setupPending=True)
        state.pop('linkOperations',None)
        return {**state, 'storage': {'events': await svc.engine.io(svc.events.stats) if svc.started else {'available': False}, 'runs': await svc.engine.io(svc.ledger.stats)}, 'definitions': list(svc.bus.definitions.values()), 'dependencies': {t['id']: {'available': svc.engine.available(t), 'unavailable': [e for e in t['eventTypes'] if not svc.bus.available(e)]} for t in state['triggers'].values()}, 'diagnostics': list(svc.bus.diagnostics) + list(svc.engine.diagnostics), 'limits': {'subscriberQueue': svc.bus.capacity, 'pendingRuns': svc.engine.max_pending, 'triggers': svc.engine.max_triggers}}

    @router.get('/api/automation/system-fields')
    async def system_fields():
        # Static read-only contract; available even before the runtime starts so
        # GUI/MCP/Agents can discover fields without an Automation login.
        return public_schema()

    @router.get('/api/automation/schema')
    async def schema():
        return {'version': 1, 'scheduling': SCHEDULING_SCHEMA,
                'systemFields': {'version': 1, 'path': '/api/automation/system-fields'},
                'quotaPolicy':{'version':2,'defaultMode':'manual','modes':['manual','automatic'],
                               'queryJobKinds':['monitor95','once'],'maxAgeSeconds':300,
                               'thresholdPercent':95,'queryIntervalSeconds':60,
                               'disabledAccountIds':{'type':'array','maxItems':128,'default':[]},
                               'adapterContractVersion':2,'inventoryMaxAgeSeconds':5,
                               'host':{'defaultEnabled':False,'productionEffects':'unbound','explicitRoles':['ma','ta'],
                                       'policyIdentityDomain':'cc-switch-provider:codex','maxOfficialProviders':32,
                                       'providerSampleBudgetSeconds':8,'workerLaunchBinding':'unavailable'},
                               'defaultAdapter':'unavailable','unknownReplayed':False},
                'chains': chains.SCHEMA, 'actionProperties': {'idempotent': {'type': 'boolean', 'default': False}},
                'limits': {'maxConcurrency': MAX_CONCURRENCY, 'maxAttempts': MAX_ATTEMPTS,
                           'backoffSeconds': BACKOFF_SECONDS, 'backoffCapSeconds': BACKOFF_CAP_SECONDS}}

    @router.get('/api/automation/quota-policy')
    async def quota_view():
        from .quota_policy import normalize, rank
        policy=getattr(service(),'quota_policy',None)
        if policy is None:raise HTTPException(503,'Quota policy unavailable')
        state=await service().engine.io(policy.store.read)
        snapshot=normalize(state['quota'],policy.clock())
        return {**state,'quota':snapshot,'rankedAccounts':rank(snapshot,state['excluded'],state['disabledAccountIds']),
                'adapter':('unavailable' if type(policy.adapter).__name__=='Adapter' or
                            (type(policy.adapter).__name__=='HostAdapter' and not state['host']['enabled'])
                           else 'host-readonly' if type(policy.adapter).__name__=='HostAdapter' else 'host-injected'),
                'diagnostics':policy.diagnostics}

    @router.put('/api/automation/quota-policy/host')
    async def quota_host(data:dict,request:Request):
        await require(request)
        if set(data)!={'enabled','sessionRoles','expectedRevision'}:
            raise HTTPException(400,'Expected enabled/sessionRoles/expectedRevision')
        policy=service().quota_policy
        try:
            result=await service().engine.io(policy.store.set_host,
                {'enabled':data['enabled'],'sessionRoles':data['sessionRoles']},data['expectedRevision'])
            await policy.cancel_obsolete_jobs(await canonical_actor(request))
            return result
        except Conflict as exc:raise HTTPException(409,str(exc)) from exc
        except ValueError as exc:raise HTTPException(400,str(exc)) from exc
        except OSError as exc:raise HTTPException(503,'Host configuration persistence failed') from exc

    @router.put('/api/automation/quota-policy/disabled-accounts')
    async def quota_disabled_accounts(data:dict,request:Request):
        await require(request)
        if set(data)!={'disabledAccountIds','expectedRevision'}:
            raise HTTPException(400,'Expected disabledAccountIds/expectedRevision')
        policy=getattr(service(),'quota_policy',None)
        if policy is None:raise HTTPException(503,'Quota policy unavailable')
        try:
            result=await service().engine.io(policy.store.set_disabled,data['disabledAccountIds'],data['expectedRevision'])
            await policy.cancel_obsolete_jobs(await canonical_actor(request))
            return result
        except Conflict as exc:raise HTTPException(409,str(exc)) from exc
        except ValueError as exc:raise HTTPException(400,str(exc)) from exc
        except OSError as exc:raise HTTPException(503,'Disabled account configuration persistence failed') from exc

    @router.put('/api/automation/quota-policy')
    async def quota_configure(data:dict,request:Request):
        await require(request)
        if set(data)!={'mode','maSessionId','expectedRevision'}:raise HTTPException(400,'Expected mode/maSessionId/expectedRevision')
        policy=getattr(service(),'quota_policy',None)
        if policy is None:raise HTTPException(503,'Quota policy unavailable')
        try:
            result=await service().engine.io(policy.store.configure,data['mode'],data['maSessionId'],data['expectedRevision'],await canonical_actor(request))
            await policy.cancel_obsolete_jobs(await canonical_actor(request))
            return result
        except ValueError as exc:raise HTTPException(409,str(exc)) from exc
        except OSError as exc:raise HTTPException(503,'Quota policy persistence failed') from exc

    @router.post('/api/automation/quota-policy/query-jobs')
    async def quota_query_job(data:dict,request:Request):
        await require(request)
        if set(data)!={'kind','expectedRevision'}:raise HTTPException(400,'Expected kind/expectedRevision')
        policy=getattr(service(),'quota_policy',None)
        if policy is None:raise HTTPException(503,'Quota policy unavailable')
        try:return {'job':await policy.create_monitor(data['kind'],data['expectedRevision'],await canonical_actor(request))}
        except ValueError as exc:raise HTTPException(409,str(exc)) from exc
        except RuntimeError as exc:raise HTTPException(503,str(exc)) from exc
        except OSError as exc:raise HTTPException(503,'Quota Job persistence failed; reload before retry') from exc

    @router.post('/api/automation/triggers')
    async def put(data: dict, request: Request):
        svc = service()
        if set(data) - {'item', 'expectedRevision', 'agentId'} or not isinstance(data.get('item'), dict):
            raise HTTPException(400, 'Expected item and expectedRevision')
        try:
            state = await svc.engine.io(svc.ledger.read)
            existing = state['triggers'].get(data['item'].get('id'))
            normalized = dict(data['item'])
            if existing:
                for key in ('executionMode', 'maxConcurrency', 'retry'):
                    normalized.setdefault(key, existing.get(key))
            await require_write(request, 'trigger', existing, normalized,
                                ignored=('id','revision','createdBy','createdAt','updatedAt','authorization'))
            if data['item'].get('id') not in state['triggers'] and len(state['triggers']) >= svc.engine.max_triggers:
                raise ValueError('Trigger capacity reached')
            source = await authority(request)
            user = True
            # Origin is informational. It grants no privileges.
            creator = {'type': source['type']}
            item = await svc.engine.io(svc.ledger.put, data['item'], data.get('expectedRevision'), user, creator, source)
            await svc.engine.kick()
            return {'item': item}
        except PermissionError as exc:
            raise HTTPException(401, str(exc)) from exc
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.post('/api/automation/triggers/{tid}/lifecycle')
    async def trigger_lifecycle(tid: str, data: dict, request: Request):
        await require_permission(request, 'trigger', 'lifecycle')
        if set(data) != {'enabled','expectedRevision'} or type(data['enabled']) is not bool:
            raise HTTPException(400, 'Expected enabled and expectedRevision')
        svc=service()
        try:
            item=await svc.engine.io(svc.ledger.set_enabled, tid, data['enabled'],
                                     data['expectedRevision'], await authority(request))
            await svc.engine.kick()
            return {'item':item}
        except KeyError as exc:raise HTTPException(404,'Trigger missing') from exc
        except Conflict as exc:raise HTTPException(409,str(exc)) from exc

    @router.delete('/api/automation/triggers/{tid}')
    async def delete(tid: str, expectedRevision: int, request: Request):
        await require_permission(request, 'trigger', 'delete')
        try:
            await service().engine.io(service().ledger.delete, tid, expectedRevision)
            return {'ok': True}
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except (KeyError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.post('/api/automation/runs/{rid}/cancel')
    async def cancel(rid: str, data: dict, request: Request):
        await require_permission(request, 'job', 'lifecycle')
        if set(data) != {'expectedRevision'}:
            raise HTTPException(400, 'Expected expectedRevision')
        try:
            svc = service()
            run = await svc.engine.io(svc.ledger.cancel, rid, data['expectedRevision'])
            task=getattr(svc.engine,'chain_tasks',{}).get(rid)
            if run.get('cancellationRequested') and task:task.cancel()
            await svc.engine.kick()
            return {'run': chains.public(run)}
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(404, 'Run not found') from exc

    @router.post('/api/automation/runs/{rid}/confirm-recovery')
    async def confirm_recovery(rid: str, data: dict, request: Request):
        await require_permission(request, 'job', 'execute')
        if set(data) != {'expectedRevision', 'confirm'} or data['confirm'] is not True:
            raise HTTPException(400, 'Expected expectedRevision and confirm:true; uncertain effects may repeat')
        try:
            svc = service()
            run = await svc.engine.io(svc.ledger.confirm_uncertain, rid, data['expectedRevision'], svc.engine.max_pending, await authority(request))
            await svc.engine.kick()
            return {'run': chains.public(run)}
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(404, 'Run not found') from exc

    @router.get('/api/automation/events')
    async def events(request:Request,after: int = 0, limit: int = 20,includeExecution:bool=False):
        if includeExecution:await require_permission(request,'trigger','read')
        try:
            return {**await service().event_page(after, limit,includeExecution),
                    'storage': await service().engine.io(service().events.stats)}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.post('/api/automation/events/replay')
    async def replay(data: dict, request: Request):
        await require(request)
        if set(data) != {'eventIds'}:
            raise HTTPException(400, 'Expected eventIds only')
        try:
            return {'scheduled': await service().replay(data['eventIds']), 'automaticRunRetry': False}
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post('/api/automation/events/maintenance')
    async def maintenance(request: Request):
        await require(request)
        result = await service().maintenance()
        service().wake.set()
        return result

    server.app.include_router(router)
