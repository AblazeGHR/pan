"""Owned quota successor: canonical stores + actual loopback TCP + cold reopen."""
import asyncio
import copy
import json
import socket
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from packages.automation.api import install
USER = {'type': 'user', 'id': 'local'}
from packages.automation.quota_policy import Adapter, Store
from packages.automation.service import Service
from packages.jobs import final_api
from packages.jobs.final_service import bind


@contextmanager
def owned_tcp(root, adapter=None, dispatch=True, policy_clock=None):
    app = FastAPI()
    automation = Service(root / 'automation', SimpleNamespace(workers={}), None)
    automation.auth = final_api.CanonicalUserAuth(root / 'automation')
    service = bind(app, automation, root / 'canonical')
    service.legacy_roots = [root / 'background_jobs']
    policy = automation.quota_policy
    if adapter is not None:policy.adapter=adapter
    if policy_clock is not None:policy.clock=policy_clock
    app.state.automation = automation
    from packages.jobs.action_api import router as actions
    from packages.jobs.api import router as jobs
    final_api.install(app, actions, jobs)
    install(SimpleNamespace(app=app))

    server_loop=None

    @app.on_event('startup')
    async def start():
        nonlocal server_loop
        server_loop=asyncio.get_running_loop()
        await service.start()
        if not dispatch:
            service.closed=True
            service.task.cancel()
            try:await service.task
            except asyncio.CancelledError:pass
            service.task=None
            service.closed=False

    @app.on_event('shutdown')
    async def close():
        await automation.close()
        await service.close()

    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen()
    port = listener.getsockname()[1]
    assert port not in (8767, 8768)
    server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    thread.start()
    try:
        wait(lambda: server.started)
        with httpx.Client(base_url=f'http://127.0.0.1:{port}', trust_env=False, timeout=10) as client:
            token = client.post('/api/automation/login', json={'key': automation.auth.key()}).json()['token']
            client.headers['Authorization'] = 'Bearer ' + token
            yield SimpleNamespace(client=client, service=service, policy=policy, port=port, loop=server_loop)
    finally:
        server.should_exit = True
        thread.join(15)
        listener.close()
        assert not thread.is_alive()
        with socket.socket() as probe:
            assert probe.connect_ex(('127.0.0.1',port)) != 0


def wait(fn):
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        result = fn()
        if result:
            return result
        time.sleep(.05)
    raise AssertionError('owned TCP condition timed out')


def pump(h):
    # Subscribe before publication; drive exactly the owned source's next point.
    # Publishing before subscription snapshots an Event with no targets.
    async def drive():
        h.service.refresh_subscriptions()
        sources = h.service.creators.list()
        points = [c['nextFireAt'] for s in sources for c in s['cursor']['rules'].values()
                  if c.get('nextFireAt') and s['state'] == 'enabled']
        if points:
            await h.service.creator_runtime.tick(at=min(points))
        await h.service.tick()
    return asyncio.run_coroutine_threadsafe(drive(), h.loop).result(timeout=8)


URL = '/api/automation/quota-policy'


class RecordingQuota(Adapter):
    def __init__(self, now, used):
        self.now, self.used, self.queries, self.inventories = now, used, 0, 0

    async def query(self):
        self.queries += 1
        return {'status': 'known', 'observedAt': self.now, 'currentAccountId': 'acct-a',
            'accounts': [{'id': 'acct-a',
                'fiveHour': {'usedPercent': self.used, 'resetsAt': self.now + 300},
                'week': {'usedPercent': 10, 'resetsAt': self.now + 600}}]}

    async def inventory(self):
        self.inventories += 1
        return {'runtimeEpoch': 'quota-epoch', 'observedAt': self.now,
            'currentAccountId': 'acct-a', 'workers': [{
                'sessionId': 'owned-ma', 'workerId': 'owned-ma-worker', 'generation': 1,
                'role': 'ma', 'status': 'running', 'adapter': 'codex', 'accountId': 'acct-a',
                'alive': True, 'processIdentity': {'pid': 4242, 'birthToken': 'quota-birth'},
                'task': {'taskId': 'quota-task', 'executionSeq': 1, 'state': 'unfinished'}}]}


def automatic(h):
    state = h.client.get(URL).json()
    response = h.client.put(URL, json={'mode': 'automatic', 'maSessionId': 'owned-ma',
                                     'expectedRevision': state['revision']})
    assert response.status_code == 200, response.text


def admit(h, kind='once'):
    state = h.client.get(URL).json()
    response = h.client.post(URL + '/query-jobs', json={'kind': kind, 'expectedRevision': state['revision']})
    assert response.status_code == 200, response.text
    return response.json()['job']


def test_unknown_real_tcp_cold_reopen(tmp_path):
    root=tmp_path/'owned'
    with owned_tcp(root) as h:
        automatic(h)
        state=h.client.get(URL).json()
        body={'kind':'once','expectedRevision':state['revision']}
        assert h.client.post(URL+'/query-jobs',json=body,headers={'Authorization':'Bearer invalid'}).status_code==401
        assert h.client.post(URL+'/query-jobs',json={**body,'expectedRevision':0}).status_code==409
        assert h.client.post(URL+'/query-jobs',json={**body,'kind':'monitor95'}).status_code==409
        body['expectedRevision']=h.client.get(URL).json()['revision']
        first=admit(h);jid=first['jobId']
        assert h.client.post(URL+'/query-jobs',json=body).json()['job']['jobId']==jid
        assert admit(h)['jobId']==jid
        run=wait(lambda: next((r for r in h.service.runs(jid,actor=USER)['items'] if r['state']=='uncertain'),None))
        agg=h.service.final.store.get(jid)['finalRuntime']
        assert len(agg['attempts'])==len(agg['effects'])==1
        assert next(iter(agg['attempts'].values()))['state']=='uncertain'
        assert agg['effects'][0]['receipt']['receipt']['code']=='quota_provider_unavailable'
        state=h.policy.store.read()
        assert not state['commands']
        assert not any(k in state for k in ('nativeEffects','nativeAdmissions','reservations'))
        assert state['jobs']['once']==jid
        assert not (root/'background_jobs').exists()
        original=copy.deepcopy(agg)
        ports=[h.port]
    with owned_tcp(root) as h:
        ports.append(h.port)
        assert admit(h)['jobId']==jid
        assert h.client.post(URL+'/query-jobs',json=body).json()['job']['jobId']==jid
        agg=h.service.final.store.get(jid)['finalRuntime']
        for name in ('runs','attempts','effects'):assert agg[name]==original[name]
        assert h.client.get('/api/job-runs/'+run['runId']).json()==run
    out=Path('evidence/quota-native');out.mkdir(parents=True,exist_ok=True)
    (out/'tcp.json').write_text(json.dumps({'ports':ports,'attempts':1,'effects':1,'state':'uncertain',
        'coldReopenPreservedIdentity':True,'realAccountQueries':0,'ownedServersStopped':True},indent=2),encoding='utf-8')


def test_native_result_above_threshold_preserves_monitor_mapping(tmp_path):
    from packages.automation.quota_policy import Runtime
    adapter = RecordingQuota(100.0, 95)
    rt = Runtime(tmp_path / 'policy', tmp_path / 'jobs', adapter, clock=lambda: 100.0)
    rt.store.configure('automatic', 'owned-ma', 0, {'type': 'user', 'id': 'local'})
    result = asyncio.run(rt.native_result('job-owned', {
        'epoch': 1, 'purpose': 'monitor95', 'expectedAccountId': 'acct-a',
        'wakeTarget': None}, {'status': 'known', 'currentAccountId': 'acct-a',
            'accounts': [{'id': 'acct-a', 'fiveHour': {'usedPercent': 95,
                'resetsAt': 200}, 'week': {'usedPercent': 10, 'resetsAt': 300}}]}))
    assert result == {'thresholdReached': True, 'stop': True, 'reason': 'threshold_reached'}


def test_native_result_below_threshold_preserves_monitor_mapping(tmp_path):
    from packages.automation.quota_policy import Runtime
    rt = Runtime(tmp_path / 'policy', tmp_path / 'jobs', clock=lambda: 100.0)
    rt.store.configure('automatic', 'owned-ma', 0, {'type': 'user', 'id': 'local'})
    result = asyncio.run(rt.native_result('job-owned', {
        'epoch': 1, 'purpose': 'monitor95', 'expectedAccountId': 'acct-a',
        'wakeTarget': None}, {'status': 'known', 'currentAccountId': 'acct-a',
            'accounts': [{'id': 'acct-a', 'fiveHour': {'usedPercent': 94,
                'resetsAt': 200}, 'week': {'usedPercent': 10, 'resetsAt': 300}}]}))
    assert result == {'thresholdReached': False, 'stop': False, 'reason': 'observed'}


@pytest.mark.parametrize('used', [94, 95])
def test_monitor_known_real_tcp(tmp_path, monkeypatch, used):
    from packages.core import background_jobs
    def forbidden(*args, **kwargs):
        raise AssertionError('legacy writer called')
    monkeypatch.setattr(background_jobs, '_create', forbidden)
    monkeypatch.setattr(background_jobs, '_update', forbidden)
    now=time.time()
    adapter=RecordingQuota(now, used)
    with owned_tcp(tmp_path/'owned', adapter, dispatch=False, policy_clock=lambda:now) as h:
        automatic(h)
        body={'kind':'monitor95','expectedRevision':h.client.get(URL).json()['revision']}
        response=h.client.post(URL+'/query-jobs',json=body)
        assert response.status_code==200,response.text
        job=response.json()['job'];jid=job['jobId']
        assert adapter.queries==1  # Existing admission binds the current account.
        assert h.client.post(URL+'/query-jobs',json=body).json()['job']==job
        assert adapter.queries==1
        pump(h)
        agg=h.service.final.store.get(jid)['finalRuntime']
        assert len(agg['runs'])==len(agg['attempts'])==len(agg['effects'])==1, h.service.diagnostics
        assert next(iter(agg['runs'].values()))['state']=='completed'
        detail=agg['effects'][0]['receipt']['receipt']
        assert detail['quotaStatus']=='known' and detail['thresholdReached']==(used>=95)
        assert adapter.queries==2
        commands=h.policy.store.read()['commands']
        assert len(commands)==int(used>=95)
        if commands:
            command=next(iter(commands.values()))
            assert command['operation']=='wake_ma' and command['status']=='ready'
        composed=h.service.creators.composition_for_key(job['compositionKey'])
        source=h.service.creators.get(composed['objectIds']['creators']['clock'])
        assert source['state']==('disabled' if used>=95 else 'enabled')
        # Receipt replay uses the existing executor store; no business callback.
        effect=agg['effects'][0]
        attempt=agg['attempts'][effect['attemptId']]
        replay=asyncio.run(h.service.executor.execute(attempt={**attempt,'state':'running'},
            effect_key=effect['effectKey'],frozen_plan={}))
        assert replay==effect['receipt']
        assert adapter.queries==2 and h.policy.store.read()['commands']==commands


@pytest.mark.parametrize('change', ['manual', 'epoch', 'disabled', 'account', 'lost-index-manual'])
def test_policy_gates_real_tcp(tmp_path, change):
    now=time.time();adapter=RecordingQuota(now,95)
    with owned_tcp(tmp_path/'owned',adapter,dispatch=False,policy_clock=lambda:now) as h:
        automatic(h);job=admit(h,'monitor95');jid=job['jobId']
        state=h.client.get(URL).json()
        if change=='lost-index-manual':
            h.policy.store.change(lambda s:(s['jobs'].clear(),s['nativeJobs'].clear()))
            state=h.client.get(URL).json()
        if change in ('manual','epoch','lost-index-manual'):
            response=h.client.put(URL,json={'mode':'automatic' if change=='epoch' else 'manual',
                'maSessionId':'owned-ma','expectedRevision':state['revision']})
            assert response.status_code==200,response.text
        elif change=='disabled':
            response=h.client.put(URL+'/disabled-accounts',json={'disabledAccountIds':['acct-a'],
                'expectedRevision':state['revision']})
            assert response.status_code==200,response.text
        else:
            original=adapter.query
            async def changed():
                value=await original()
                value['currentAccountId']='acct-b';value['accounts'][0]['id']='acct-b'
                return value
            adapter.query=changed
        pump(h)
        assert not h.policy.store.read()['commands']
        agg=h.service.final.store.get(jid).get('finalRuntime',{})
        if change=='account':
            assert len(agg['attempts'])==len(agg['effects'])==1
            assert next(iter(agg['runs'].values()))['state']=='uncertain'
            assert agg['effects'][0]['receipt']['receipt']['reason']=='account_changed'
            assert adapter.queries==2
        else:
            assert not agg.get('attempts') and adapter.queries==1
        composed=h.service.creators.composition_for_key(job['compositionKey'])
        assert h.service.creators.get(composed['objectIds']['creators']['clock'])['state']=='disabled'


def test_reference_loss_and_reset_admission_real_tcp(tmp_path):
    now=time.time();adapter=RecordingQuota(now,95)
    with owned_tcp(tmp_path/'owned',adapter,dispatch=False,policy_clock=lambda:now) as h:
        automatic(h)
        inventory=asyncio.run(h.policy.inventory())
        from packages.automation import quota_identity
        target=quota_identity.worker(inventory['workers'][0], inventory['runtimeEpoch'])
        state=h.policy.store.read()
        job=h.policy.ensure_job(state,'reset',now+10,None,target)
        h.policy.store.change(lambda s:(s['jobs'].clear(),s['nativeJobs'].clear()))
        asyncio.run(h.policy.tick_jobs())
        # Frozen canonical graph supplies the execution reference even when the
        # policy index write/receipt was lost. No query of historical projections.
        pump(h)
        agg=h.service.final.store.get(job['jobId'])['finalRuntime']
        assert len(agg['attempts'])==len(agg['effects'])==1
        assert next(iter(agg['runs'].values()))['state']=='completed'
        assert adapter.queries==1
        assert len(h.policy.store.read()['commands'])==1
        assert h.policy.ensure_job(state,'reset',now+10,None,target)['jobId']==job['jobId']
        assert h.policy.store.read()['jobs']['reset']==job['jobId']


def test_agent_authority_revocation_real_tcp(tmp_path):
    from packages.jobs.final_contracts import ContractError
    now=time.time();adapter=RecordingQuota(now,95)
    with owned_tcp(tmp_path/'owned',adapter,dispatch=False,policy_clock=lambda:now) as h:
        automatic(h);state=h.policy.store.read()
        body={'kind':'once','expectedRevision':state['revision']}
        user_job=admit(h)
        state=h.policy.store.read()
        denied={'type':'agent','id':'agent','revision':0}
        with pytest.raises(ContractError,match='denied'):
            h.policy.ensure_job(state,'once',now+10,actor=denied)
        auth=h.service.auth
        grants=auth._default_permissions()
        for kind in ('job','eventCreator','trigger','action'):
            for capability in grants[kind]:grants[kind][capability]=True
        auth.change_permissions(grants,0)
        actor={'type':'agent','id':'agent','revision':1}
        job=h.policy.ensure_job(state,'once',now+10,actor=actor)
        assert h.client.post(URL+'/query-jobs',json=body).json()['job']==user_job
        assert h.policy.store.read()['jobs']['once']==job['jobId']
        item=h.service.get('job',job['jobId'],actor=USER)
        assert item['createdBy']=={'type':'agent','id':'agent'}
        auth.change_permissions(auth._default_permissions(),1)
        pump(h)
        assert not adapter.queries
        assert h.service.events.page()==[]
        agg=h.service.final.store.get(job['jobId']).get('finalRuntime',{})
        assert not agg.get('attempts')
        assert h.service.creator_runtime.diagnostics
        # Authenticated user cleanup can disable only the owned future graph
        # even after the creating Agent's lifecycle grant was revoked.
        state=h.client.get(URL).json()
        response=h.client.put(URL,json={'mode':'manual','maSessionId':'owned-ma',
            'expectedRevision':state['revision']})
        assert response.status_code==200,response.text
        composed=h.service.creators.composition_for_key(job['compositionKey'])
        assert h.service.creators.get(composed['objectIds']['creators']['clock'])['state']=='disabled'


def test_lost_admission_index_receipt_and_concurrent_duplicate_tcp(tmp_path,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    now=time.time();adapter=RecordingQuota(now,94)
    with owned_tcp(tmp_path/'owned',adapter,dispatch=False,policy_clock=lambda:now) as h:
        automatic(h)
        body={'kind':'once','expectedRevision':h.client.get(URL).json()['revision']}
        original=h.policy.native.remember
        def lost(value, **kwargs):raise OSError('owned injected policy reference persistence failure')
        monkeypatch.setattr(h.policy.native,'remember',lost)
        response=h.client.post(URL+'/query-jobs',json=body)
        assert response.status_code==503,response.text
        monkeypatch.setattr(h.policy.native,'remember',original)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results=list(pool.map(lambda _:h.client.post(URL+'/query-jobs',json=body),range(4)))
        assert all(r.status_code==200 for r in results),[r.text for r in results]
        jobs=[r.json()['job'] for r in results]
        assert all(j==jobs[0] for j in jobs)
        jid=jobs[0]['jobId']
        assert len(h.service.creators.list())==1
        pump(h)
        agg=h.service.final.store.get(jid)['finalRuntime']
        assert len(agg['attempts'])==len(agg['effects'])==1
        assert adapter.queries==1
        assert h.policy.store.read()['jobs']['once']==jid


def test_unbound_legacy_admission_still_fenced(tmp_path):
    from packages.automation.quota_policy import Runtime
    from packages.jobs.final_contracts import ContractError
    with owned_tcp(tmp_path/'owned',dispatch=False) as h:
        unbound=Runtime(tmp_path/'unbound',h.policy.root,RecordingQuota(time.time(),95))
        unbound.store.configure('automatic','owned-ma',0)
        with pytest.raises(ContractError,match='fence'):
            asyncio.run(unbound.create_monitor('monitor95',1))
        assert unbound.adapter.queries==0


def test_monitor_unknown_no_retry_cold_reopen_tcp(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    now=time.time();adapter=RecordingQuota(now,94);root=tmp_path/'owned'
    with owned_tcp(root,adapter,dispatch=False,policy_clock=lambda:now) as h:
        automatic(h)
        body={'kind':'monitor95','expectedRevision':h.client.get(URL).json()['revision']}
        with ThreadPoolExecutor(max_workers=4) as pool:
            responses=list(pool.map(lambda _:h.client.post(URL+'/query-jobs',json=body),range(4)))
        assert all(r.status_code==200 for r in responses),[r.text for r in responses]
        jobs=[r.json()['job'] for r in responses]
        assert all(j==jobs[0] for j in jobs) and adapter.queries==1
        jid=jobs[0]['jobId']
        async def unknown():
            adapter.queries+=1
            return {'status':'unknown'}
        adapter.query=unknown
        pump(h)
        original=h.service.final.store.get(jid)['finalRuntime']
        assert len(original['attempts'])==len(original['effects'])==1
        assert next(iter(original['runs'].values()))['state']=='uncertain'
        for _ in range(2):pump(h)
        agg=h.service.final.store.get(jid)['finalRuntime']
        assert agg['attempts']==original['attempts'] and agg['effects']==original['effects']
        assert adapter.queries==2 and not h.policy.store.read()['commands']
    with owned_tcp(root,adapter,dispatch=False,policy_clock=lambda:now) as h:
        assert h.client.post(URL+'/query-jobs',json=body).json()['job']==jobs[0]
        pump(h)
        agg=h.service.final.store.get(jid)['finalRuntime']
        assert agg['attempts']==original['attempts'] and agg['effects']==original['effects']
        assert adapter.queries==2 and not h.policy.store.read()['commands']


def test_manual_change_during_original_attempt_tcp(tmp_path):
    now=time.time();adapter=RecordingQuota(now,95)
    entered=threading.Event();release=threading.Event()
    original=adapter.query
    async def blocked():
        value=await original()
        if adapter.queries>1:
            entered.set()
            assert await asyncio.to_thread(release.wait,8)
        return value
    adapter.query=blocked
    with owned_tcp(tmp_path/'owned',adapter,dispatch=False,policy_clock=lambda:now) as h:
        automatic(h);job=admit(h,'monitor95');jid=job['jobId']
        async def drive():
            h.service.refresh_subscriptions()
            source=h.service.creators.list()[0]
            await h.service.creator_runtime.tick(at=source['cursor']['rules']['query']['nextFireAt'])
            await h.service.tick()
        pending=asyncio.run_coroutine_threadsafe(drive(),h.loop)
        try:
            assert entered.wait(5)
            before=h.service.final.store.get(jid)['finalRuntime']
            attempt=next(iter(before['attempts']))
            state=h.client.get(URL).json()
            response=h.client.put(URL,json={'mode':'manual','maSessionId':'owned-ma',
                'expectedRevision':state['revision']})
            assert response.status_code==200,response.text
            during=h.service.final.store.get(jid)['finalRuntime']
            assert during['attempts'][attempt]['state']=='running'
        finally:
            release.set()
            pending.result(timeout=8)
        after=h.service.final.store.get(jid)['finalRuntime']
        assert len(after['attempts'])==len(after['effects'])==1
        assert after['attempts'][attempt]['state']=='uncertain'
        assert after['effects'][0]['receipt']['receipt']['reason']=='policy_changed'
        assert not h.policy.store.read()['commands'] and adapter.queries==2
