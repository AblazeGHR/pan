"""Trigger management only. Event registration/publishing has no public route."""
from fastapi import APIRouter, HTTPException, Request
from .store import Conflict
from .policy import SCHEDULING_SCHEMA, MAX_CONCURRENCY, MAX_ATTEMPTS, BACKOFF_SECONDS, BACKOFF_CAP_SECONDS


def install(server):
    router = APIRouter()

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

    @router.post('/api/automation/login')
    async def login(data: dict):
        try:
            token = await service().engine.io(service().auth.login, data.get('key'))
            return {'token': token, 'expiresIn': service().auth.lifetime}
        except PermissionError as exc:
            raise HTTPException(401, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.post('/api/automation/logout')
    async def logout(request: Request):
        await service().engine.io(service().auth.logout, request.headers.get('authorization', ''))
        return {'ok': True}

    @router.get('/api/automation')
    async def view():
        svc = service()
        state = await svc.engine.io(svc.ledger.read)
        return {**state, 'storage': {'events': await svc.engine.io(svc.events.stats) if svc.started else {'available': False}, 'runs': await svc.engine.io(svc.ledger.stats)}, 'definitions': list(svc.bus.definitions.values()), 'dependencies': {t['id']: {'available': svc.engine.available(t), 'unavailable': [e for e in t['eventTypes'] if not svc.bus.available(e)]} for t in state['triggers'].values()}, 'diagnostics': list(svc.bus.diagnostics) + list(svc.engine.diagnostics), 'limits': {'subscriberQueue': svc.bus.capacity, 'pendingRuns': svc.engine.max_pending, 'triggers': svc.engine.max_triggers}}

    @router.get('/api/automation/schema')
    async def schema():
        return {'version': 1, 'scheduling': SCHEDULING_SCHEMA,
                'quotaPolicy':{'version':2,'defaultMode':'manual','modes':['manual','automatic'],
                               'queryJobKinds':['monitor95','once'],'maxAgeSeconds':300,
                               'thresholdPercent':95,'queryIntervalSeconds':60,
                               'disabledAccountIds':{'type':'array','maxItems':128,'default':[]},
                               'adapterContractVersion':2,'inventoryMaxAgeSeconds':5,
                               'host':{'defaultEnabled':False,'productionEffects':'unbound','explicitRoles':['ma','ta'],
                                       'policyIdentityDomain':'cc-switch-provider:codex','maxOfficialProviders':32,
                                       'providerSampleBudgetSeconds':8,'workerLaunchBinding':'unavailable'},
                               'defaultAdapter':'unavailable','unknownReplayed':False},
                'actionProperties': {'idempotent': {'type': 'boolean', 'default': False}},
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
            await policy.cancel_obsolete_jobs()
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
            await policy.cancel_obsolete_jobs()
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
            result=await service().engine.io(policy.store.configure,data['mode'],data['maSessionId'],data['expectedRevision'])
            await policy.cancel_obsolete_jobs()
            return result
        except ValueError as exc:raise HTTPException(409,str(exc)) from exc
        except OSError as exc:raise HTTPException(503,'Quota policy persistence failed') from exc

    @router.post('/api/automation/quota-policy/query-jobs')
    async def quota_query_job(data:dict,request:Request):
        await require(request)
        if set(data)!={'kind','expectedRevision'}:raise HTTPException(400,'Expected kind/expectedRevision')
        policy=getattr(service(),'quota_policy',None)
        if policy is None:raise HTTPException(503,'Quota policy unavailable')
        try:return {'job':await policy.create_monitor(data['kind'],data['expectedRevision'])}
        except ValueError as exc:raise HTTPException(409,str(exc)) from exc
        except OSError as exc:raise HTTPException(503,'Quota Job persistence failed; reload before retry') from exc

    @router.post('/api/automation/triggers')
    async def put(data: dict, request: Request):
        svc = service()
        if set(data) - {'item', 'expectedRevision', 'agentId'} or not isinstance(data.get('item'), dict):
            raise HTTPException(400, 'Expected item and expectedRevision')
        try:
            state = await svc.engine.io(svc.ledger.read)
            if data['item'].get('id') not in state['triggers'] and len(state['triggers']) >= svc.engine.max_triggers:
                raise ValueError('Trigger capacity reached')
            user = await authorized(request)
            # Origin is informational. It grants no privileges.
            creator = {'type': 'user'} if user else {'type': 'agent', 'id': data.get('agentId')}
            item = await svc.engine.io(svc.ledger.put, data['item'], data.get('expectedRevision'), user, creator)
            await svc.engine.kick()
            return {'item': item}
        except PermissionError as exc:
            raise HTTPException(401, str(exc)) from exc
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.delete('/api/automation/triggers/{tid}')
    async def delete(tid: str, expectedRevision: int, request: Request):
        await require(request)
        try:
            await service().engine.io(service().ledger.delete, tid, expectedRevision)
            return {'ok': True}
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except (KeyError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.post('/api/automation/runs/{rid}/cancel')
    async def cancel(rid: str, data: dict, request: Request):
        await require(request)
        if set(data) != {'expectedRevision'}:
            raise HTTPException(400, 'Expected expectedRevision')
        try:
            svc = service()
            run = await svc.engine.io(svc.ledger.cancel, rid, data['expectedRevision'])
            await svc.engine.kick()
            return {'run': run}
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(404, 'Run not found') from exc

    @router.post('/api/automation/runs/{rid}/confirm-recovery')
    async def confirm_recovery(rid: str, data: dict, request: Request):
        await require(request)
        if set(data) != {'expectedRevision', 'confirm'} or data['confirm'] is not True:
            raise HTTPException(400, 'Expected expectedRevision and confirm:true; uncertain effects may repeat')
        try:
            svc = service()
            run = await svc.engine.io(svc.ledger.confirm_uncertain, rid, data['expectedRevision'], svc.engine.max_pending)
            await svc.engine.kick()
            return {'run': run}
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(404, 'Run not found') from exc

    @router.get('/api/automation/events')
    async def events(after: int = 0, limit: int = 20):
        try:
            return {**await service().engine.io(service().events.page, after, limit),
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
