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


def test_unknown_production_process_restart_real_http(tmp_path, monkeypatch):
    """Strict successor to the original real-HTTP gap, using two owned OS PIDs."""
    import sqlite3
    import psutil
    from tests import test_real_queue_http_e2e as real_http

    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port=reservation.getsockname()[1]
    assert port not in (8767,8768)
    launcher=Path('evidence/quota-native/production_http_server.py').resolve()
    monkeypatch.setattr(real_http,'PORT',port)
    monkeypatch.setattr(real_http,'SERVER_LAUNCHER',launcher)
    rt=real_http._PanRuntime(tmp_path/'owned-production-quota')
    rt.data_root.mkdir()
    (rt.data_root/'config.json').write_text(json.dumps({'plugin_manifests':[],
        'scheduler':{'enabled':True}}),encoding='utf-8')
    evidence={'port':port,'launcher':str(launcher),'generations':[],
        'realAccountQueries':0,'sourceCandidate':'a66edbaa94c1a12b22915267e22346a255f3740f'}

    def calls():
        path=rt.data_root/'quota-default-calls.jsonl'
        return [json.loads(row) for row in path.read_text(encoding='utf-8').splitlines()] if path.exists() else []

    def aggregate(jid):
        # Supplement HTTP Run reads with the authoritative canonical Attempt/
        # effect rows; there is no public Attempt/effect detail route yet.
        path=rt.data_root/'job-final/event-creators/creators.sqlite3'
        with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
            db.execute('PRAGMA query_only=ON')
            row=db.execute("SELECT document FROM final_documents WHERE kind='job' AND id=?",(jid,)).fetchone()
            assert row is not None
            return json.loads(row[0])['finalRuntime']

    def started():
        rt.start()
        process=psutil.Process(rt.process.pid)
        assert str(launcher) in ' '.join(process.cmdline())
        listeners=[]
        for pid,born,command in rt._owned_processes:
            owned=psutil.Process(pid)
            assert abs(owned.create_time()-born)<=.01
            if any(c.status==psutil.CONN_LISTEN and c.laddr.port==port
                   for c in owned.net_connections(kind='tcp')):
                listeners.append({'pid':pid,'birthTime':born})
        assert listeners
        evidence['generations'].append({'pid':process.pid,'birthTime':process.create_time(),
            'listeners':listeners})

    def event_facts():
        path=rt.data_root/'job-final/canonical-events.sqlite3'
        with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True) as db:
            db.execute('PRAGMA query_only=ON')
            return db.execute('SELECT eventId,factDigest,record FROM events ORDER BY eventId').fetchall()

    def stopped():
        handle=rt.process
        if handle is None:return
        rt._capture_owned_processes()
        owned=list(rt._owned_processes)
        rt.stop()
        assert handle.poll()==0
        retired=[]
        for pid,born,command in owned:
            try:
                process=psutil.Process(pid)
                assert abs(process.create_time()-born)>.01 or not process.is_running()
            except psutil.NoSuchProcess:
                pass
            retired.append({'pid':pid,'birthTime':born,'retired':True})
        assert real_http._port_is_free(port)
        with socket.socket() as probe:
            probe.bind(('127.0.0.1',port))
        evidence['generations'][-1].update(exitCode=handle.returncode,
            ownedProcesses=retired,portReleased=True)

    try:
        started()
        with httpx.Client(base_url=rt.base_url,timeout=15,trust_env=False) as client:
            key=(rt.data_root/'automation/user.key').read_text(encoding='utf-8')
            token=client.post('/api/automation/login',json={'key':key}).json()['token']
            client.cookies.clear()
            headers={'Authorization':'Bearer '+token}
            invalid={'Authorization':'Bearer invalid'}
            state=client.get(URL).json()
            assert state['mode']=='manual' and state['adapter']=='unavailable'
            body={'disabledAccountIds':['owned-disabled-account'],'expectedRevision':state['revision']}
            assert client.put(URL+'/disabled-accounts',json=body).status_code==401
            assert client.put(URL+'/disabled-accounts',json=body,headers=invalid).status_code==401
            assert client.put(URL+'/disabled-accounts',json={**body,'expectedRevision':True},headers=headers).status_code==400
            assert client.put(URL+'/disabled-accounts',json=body,headers=headers).status_code==200
            assert client.put(URL+'/disabled-accounts',json=body,headers=headers).status_code==409
            state=client.get(URL).json()
            config={'mode':'automatic','maSessionId':'owned-ma','expectedRevision':state['revision']}
            assert client.put(URL,json=config,headers=invalid).status_code==401
            assert client.put(URL,json=config,headers=headers).status_code==200
            assert client.put(URL,json=config,headers=headers).status_code==409
            state=client.get(URL).json()
            request={'kind':'monitor95','expectedRevision':state['revision']}
            rejected=client.post(URL+'/query-jobs',json=request,headers=headers)
            assert rejected.status_code==409 and 'account unknown' in rejected.text
            assert client.get('/api/jobs',headers=headers).json()['items']==[]
            state=client.get(URL).json()
            request={'kind':'once','expectedRevision':state['revision']}
            assert client.post(URL+'/query-jobs',json=request,headers=invalid).status_code==401
            assert client.post(URL+'/query-jobs',json={**request,'expectedRevision':0},headers=headers).status_code==409
            admitted=client.post(URL+'/query-jobs',json=request,headers=headers)
            assert admitted.status_code==200,admitted.text
            job=admitted.json()['job'];jid=job['jobId']
            def unknown():
                rows=client.get('/api/jobs/'+jid+'/runs',headers=headers).json()['items']
                return next((r for r in rows if r['state']=='uncertain'),None)
            run=real_http._wait_for(unknown,label='original canonical Run unknown')
            rid=run['runId']
            assert client.get('/api/job-runs/'+rid,headers=invalid).status_code==401
            assert client.get('/api/job-runs/'+rid,headers=headers).json()==run
            original=aggregate(jid)
            facts=event_facts()
            assert len(facts)==1
            assert len(original['runs'])==len(original['attempts'])==len(original['effects'])==1
            attempt=next(iter(original['attempts'].values()))
            effect=original['effects'][0]
            assert attempt['runId']==rid and attempt['state']=='uncertain'
            assert effect['attemptId']==attempt['attemptId'] and effect['effectKey']['runId']==rid
            assert effect['outcome']=='unknown'
            assert effect['receipt']['receipt']['code']=='quota_provider_unavailable'
            # Exact successor for the retired facade's old manual-confirmation
            # envelope. Never reopen those routes or expect legacy {ok,error}.
            assert client.patch('/api/jobs/'+jid,json={'enabled':True},headers=headers).status_code==405
            for method,path,body in [('PATCH','/api/legacy/jobs/'+jid,{'enabled':True}),
                    ('POST','/api/legacy/jobs/'+jid+'/run-now',{}),('DELETE','/api/legacy/jobs/'+jid,None)]:
                assert client.request(method,path,json=body,headers=invalid).status_code==401
                retired=client.request(method,path,json=body,headers=headers)
                assert retired.status_code==410 and retired.json()['detail']['code']=='retired'
            retry={'operation':'retry','expectedStateRevision':run['stateRevision'],'requestKey':'owned-quota-unknown-retry'}
            assert client.post('/api/job-runs/'+rid+'/operations',json=retry,headers=invalid).status_code==401
            refused=client.post('/api/job-runs/'+rid+'/operations',json=retry,headers=headers)
            assert refused.status_code==409 and refused.json()['error']['code']=='reconciliation_required'
            assert calls() and [c['method'] for c in calls()]==['query','query']
            before_calls=calls()
            assert {c['pid'] for c in before_calls}<={p['pid'] for p in evidence['generations'][0]['listeners']}
            policy=Store(rt.data_root/'automation').read()
            assert policy['quota']['status']=='unknown' and not policy['commands']
            assert not any(k in policy for k in ('nativeEffects','nativeAdmissions','reservations'))
            for _ in range(4):
                assert client.post(URL+'/query-jobs',json=request,headers=headers).json()['job']==job
                assert client.get('/api/job-runs/'+rid,headers=headers).json()==run
                time.sleep(.25)
            assert calls()==before_calls
            stopped()
            assert aggregate(jid)==original
            started()
            assert evidence['generations'][0]['pid']!=evidence['generations'][1]['pid']
            # Reuse the original token, Job and Run identity after full lifespan
            # and OS process replacement; no admission/execution probe endpoint.
            for _ in range(4):
                restored=client.get(URL).json()
                assert restored['mode']=='automatic' and restored['adapter']=='unavailable'
                assert restored['disabledAccountIds']==['owned-disabled-account']
                assert restored['accountConfigRevision']==1 and not restored['commands']
                assert client.get('/api/job-runs/'+rid,headers=headers).json()==run
                assert client.get('/api/jobs/'+jid+'/runs',headers=headers).json()['items']==[run]
                assert client.post(URL+'/query-jobs',json=request,headers=headers).json()['job']==job
                time.sleep(.25)
            assert calls()==before_calls
            recovered=aggregate(jid)
            for field in ('runs','attempts','effects'):assert recovered[field]==original[field]
            assert event_facts()==facts
            assert Store(rt.data_root/'automation').read()==policy
            refused=client.post('/api/job-runs/'+rid+'/operations',json=retry,headers=headers)
            assert refused.status_code==409 and refused.json()['error']['code']=='reconciliation_required'
            state=client.get(URL).json()
            manual={'mode':'manual','maSessionId':'owned-ma','expectedRevision':state['revision']}
            assert client.put(URL,json=manual,headers=invalid).status_code==401
            assert client.put(URL,json=manual,headers=headers).status_code==200
            assert client.put(URL,json=manual,headers=headers).status_code==409
            blocked=client.post(URL+'/query-jobs',json=request,headers=headers)
            assert blocked.status_code==409 and 'Automatic policy required' in blocked.text
            assert client.get('/api/job-runs/'+rid,headers=headers).json()==run
            assert event_facts()==facts and calls()==before_calls
            assert aggregate(jid)['effects']==original['effects']
            assert not Store(rt.data_root/'automation').read()['commands']
            assert not rt.fake_records()
            evidence.update(jobId=jid,runId=rid,attemptId=attempt['attemptId'],effectKey=effect['effectKey'],
                adapterCalls=before_calls,queryCalls=2,onceExecutionQueries=1,monitorBindingQueries=1,
                wakeCalls=0,effects=1,attempts=1,unknownPreserved=True,noResendAfterProcessRestart=True)
            evidence.update(eventId=facts[0][0],eventFactDigest=facts[0][1],eventUnchanged=True,
                unknownRetryRejected=True,retiredFacadeAuthFirst=True,manualGatePreserved=True)
    finally:
        try:stopped()
        finally:
            evidence['allOwnedProcessesStopped']=rt.process is None and all(
                g.get('portReleased') for g in evidence['generations'])
            Path('evidence/quota-native/process-tcp.json').write_text(
                json.dumps(evidence,indent=2)+'\n',encoding='utf-8')


def test_readonly_binding_construction_disabled_and_custom(tmp_path,monkeypatch):
    from packages.automation import quota_host
    from packages.automation.quota_policy import Runtime, Unavailable
    from packages.cc_switch_bridge.provider import Bridge
    def forbidden(*args,**kwargs):raise AssertionError('construction/disabled host performed IO')
    monkeypatch.setattr(Bridge,'read_only',forbidden)
    monkeypatch.setattr(quota_host.ReadSource,'read',forbidden)
    monkeypatch.setattr(quota_host,'retained_process',forbidden)
    rt=Runtime(tmp_path/'policy',tmp_path/'jobs')
    adapter=quota_host.bind_readonly(rt,SimpleNamespace(),tmp_path)
    assert isinstance(adapter,quota_host.HostAdapter) and adapter.runtime is rt and adapter.ports=={}
    assert quota_host.bind_readonly(rt,SimpleNamespace(),tmp_path) is adapter
    assert not rt.store.path.exists()
    assert rt.store.read()['host']=={'enabled':False,'sessionRoles':{}}
    for operation in (adapter.query,adapter.inventory):
        with pytest.raises(Unavailable,match='quota_host_disabled'):asyncio.run(operation())
    with pytest.raises(Unavailable,match='unbound'):
        asyncio.run(adapter.perform({'operation':'wake_ma'},'unused'))
    custom=RecordingQuota(time.time(),95)
    rt.adapter=custom
    monkeypatch.setattr(quota_host,'production',forbidden)
    assert quota_host.bind_readonly(rt,SimpleNamespace(),tmp_path) is custom


def test_readonly_binding_rejects_mutation_ports(tmp_path,monkeypatch):
    from packages.automation import quota_host
    from packages.automation.quota_policy import Runtime
    rt=Runtime(tmp_path/'policy',tmp_path/'jobs');original=rt.adapter
    forbidden=quota_host.HostAdapter(rt.store,None,None,ports={'wake_ma':lambda:None})
    monkeypatch.setattr(quota_host,'production',lambda *args:forbidden)
    with pytest.raises(ValueError,match='zero mutation ports'):
        quota_host.bind_readonly(rt,SimpleNamespace(),tmp_path)
    assert rt.adapter is original and forbidden.runtime is None


def test_service_readonly_host_admission_sample_once_reopen(tmp_path,monkeypatch):
    from packages.automation import quota_host
    counts={'constructed':0,'sample':0}
    root=tmp_path/'owned'
    class Source:
        def read_for_policy(self,disabled):
            counts['sample']+=1
            return {'observedAt':time.time(),'managed':{'status':'unknown'},'native':{'status':'unknown'},
                'provider':{'status':'unknown'},'binding':'unknown','reason':'owned-unavailable',
                'policySnapshot':{'status':'unknown'},'profileBindings':{}}
    def factory(store,worker,profile):
        assert Path(profile)==root
        counts['constructed']+=1
        return quota_host.HostAdapter(store,Source(),None)
    monkeypatch.setattr(quota_host,'production',factory)
    with owned_tcp(root) as h:
        assert counts=={'constructed':1,'sample':0}
        assert h.policy.adapter.runtime is h.policy and not h.policy.adapter.ports
        assert asyncio.run(h.policy.query())['status']=='unknown'
        assert counts['sample']==0
        state=h.client.get(URL).json()
        response=h.client.put(URL+'/host',json={'enabled':True,'sessionRoles':{},'expectedRevision':state['revision']})
        assert response.status_code==200,response.text
        automatic(h)
        request={'kind':'once','expectedRevision':h.client.get(URL).json()['revision']}
        job=h.client.post(URL+'/query-jobs',json=request).json()['job'];jid=job['jobId']
        wait(lambda:h.service.runs(jid,actor=USER)['items'] and h.service.runs(jid,actor=USER)['items'][0]['state']=='uncertain')
        original=h.service.final.store.get(jid)['finalRuntime']
        assert counts['sample']==1
        assert h.client.post(URL+'/query-jobs',json=request).json()['job']==job
        assert counts['sample']==1 and not h.policy.store.read()['commands']
    with owned_tcp(root) as h:
        assert counts=={'constructed':2,'sample':1}
        assert h.client.post(URL+'/query-jobs',json=request).json()['job']==job
        agg=h.service.final.store.get(jid)['finalRuntime']
        assert agg['attempts']==original['attempts'] and agg['effects']==original['effects']
        state=h.client.get(URL).json()
        assert h.client.put(URL+'/host',json={'enabled':False,'sessionRoles':{},'expectedRevision':state['revision']}).status_code==200
        assert asyncio.run(h.policy.query())['status']=='unknown'
        assert counts['sample']==1 and not h.policy.store.read()['commands']
