"""Bounded scheduler and attempt transitions, with owned deterministic barriers."""
import asyncio
import copy
from datetime import datetime, timezone

import pytest

from packages.actions.store import validate_action
from packages.automation.store import Ledger, Conflict
from tests.test_automation_core import async_test, action, trigger, event, setup, until


def test_parallel_trigger_contract(tmp_path):
    ledger=Ledger(tmp_path)
    t=ledger.put(trigger(executionMode='parallel'),0,True)
    assert t['maxConcurrency']==3


def test_action_explicit_idempotence():
    assert validate_action({**action(),'idempotent':True})['idempotent'] is True
    assert validate_action(action())['idempotent'] is False


def test_uncertain_confirmation_keeps_run_and_new_attempt(tmp_path):
    ledger=Ledger(tmp_path)
    ledger.put(trigger(),0,True)
    ledger.append(dict(id='r',triggerId='t',triggerRevision=1,event=event(),status='queued'))
    first=ledger.claim('r')
    ledger.recover()
    uncertain=ledger.read()['runs']['r']
    confirmed=ledger.confirm_uncertain('r',uncertain['revision'])
    second=ledger.claim('r')
    assert confirmed['id']==second['id']=='r'
    assert first['activeAttemptId']!=second['activeAttemptId']
    assert second['attempts'][0]['status']=='uncertain' and second['attemptCount']==2


async def rows(engine):
    return list((await engine.io(engine.ledger.read))['runs'].values())


async def wait_status(engine, status, count=1, attempts=None):
    async def ready():
        r=await rows(engine)
        return len(r)==count and all(x['status']==status and (attempts is None or x['attemptCount']==attempts) for x in r)
    await until(ready)
    return await rows(engine)


@pytest.mark.parametrize('fields',[
    {'executionMode':'unknown'}, {'executionMode':True}, {'executionMode':[]},
    {'executionMode':'parallel','maxConcurrency':0}, {'executionMode':'parallel','maxConcurrency':33},
    {'executionMode':'parallel','maxConcurrency':True}, {'executionMode':'parallel','maxConcurrency':1.5},
    {'maxConcurrency':2}, {'retry':None}, {'retry':True}, {'retry':{'enabled':1}},
    {'retry':{'maxAttempts':0}}, {'retry':{'maxAttempts':11}}, {'retry':{'maxAttempts':True}},
    {'retry':{'jitter':True}}, {'queuePolicy':'ignore'}])
def test_unknown_types_and_bounds_rejected_without_write(tmp_path,fields):
    ledger=Ledger(tmp_path)
    with pytest.raises(ValueError):
        ledger.put(trigger(**fields),0,True)
    assert not ledger.path.exists()


@pytest.mark.parametrize('value',[None,1,'true',{},[]])
def test_action_idempotent_strict_boolean(value):
    with pytest.raises(ValueError):
        validate_action({**action(),'idempotent':value})


def test_defaults_and_concurrency_change_need_user_even_disabled(tmp_path):
    ledger=Ledger(tmp_path)
    draft={**trigger(),'enabled':False}
    t=ledger.put(draft,0)
    assert t['executionMode']=='serial' and t['maxConcurrency']==1
    assert t['retry']=={'enabled':False,'maxAttempts':3}
    with pytest.raises(PermissionError,match='concurrency'):
        ledger.put({**draft,'executionMode':'parallel'},1)
    t=ledger.put({**draft,'executionMode':'parallel','maxConcurrency':32},1,True)
    assert t['maxConcurrency']==32
    with pytest.raises(PermissionError):
        ledger.put({**trigger(),'executionMode':'parallel'},2)


@async_test
async def test_parallel_bounded_independent_and_live_reduction(tmp_path,monkeypatch):
    entered=asyncio.Queue();release={};active=set();maximum=0
    async def execute(a,args,e,progress):
        nonlocal maximum
        key=(a['id'],e['id']);active.add(key);maximum=max(maximum,sum(k[0]=='pause' for k in active))
        release[key]=asyncio.Event();await entered.put(key)
        try:
            await release[key].wait();return {'ok':True}
        finally:active.remove(key)
    engine,_,_=await setup(tmp_path,execute)
    snapshot,release_snapshot=asyncio.Event(),asyncio.Event()
    original=engine.io
    async def io(fn,*args,**kw):
        result=await original(fn,*args,**kw)
        if (fn==engine.ledger.read and asyncio.current_task() is engine.tasks.get('t')
                and not snapshot.is_set() and any(r['status']=='succeeded' for r in result['runs'].values())):
            snapshot.set();await release_snapshot.wait()
        return result
    monkeypatch.setattr(engine,'io',io)
    engine.ledger.put(trigger(executionMode='parallel',maxConcurrency=2),0,True)
    try:
        for i in range(4):await engine.match(event(str(i)))
        first=[await asyncio.wait_for(entered.get(),2) for _ in range(2)]
        assert maximum==2 and len(active)==2
        engine.actions.put('actions',{**action(),'id':'other'})
        engine.ledger.put({**trigger('other'),'actionId':'other'},0,True)
        await engine.match(event('independent'))
        independent=await asyncio.wait_for(entered.get(),2)
        assert independent==('other','independent') and len(active)==3
        with pytest.raises(Conflict,match='Running Run count'):
            engine.ledger.put(trigger(executionMode='parallel',maxConcurrency=1),1,True)
        release[first[0]].set()
        await asyncio.wait_for(snapshot.wait(),2)
        # Keep the second slot occupied until the confirmed reduction commits.
        engine.ledger.put(trigger(executionMode='parallel',maxConcurrency=1),1,True)
        await engine.kick()
        release_snapshot.set()
        assert entered.empty() and len([k for k in active if k[0]=='pause'])==1
        release[first[1]].set()
        successor=await asyncio.wait_for(entered.get(),2)
        assert successor==('pause','2') and maximum==2
        await engine.close()
        assert not active and not engine.tasks and not engine.signals
        state=await rows(engine)
        assert any(r['status']=='uncertain' for r in state)
        assert any(r['status']=='queued' for r in state)
    finally:
        release_snapshot.set()
        for r in release.values():r.set()
        await engine.close()


def test_atomic_claim_fences_concurrent_readers_and_trigger_limit(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    ledger=Ledger(tmp_path)
    ledger.put(trigger(executionMode='parallel',maxConcurrency=2),0,True)
    for i in range(8):ledger.append(dict(id=str(i),triggerId='t',triggerRevision=1,event=event(str(i)),status='queued'))
    with ThreadPoolExecutor(max_workers=8) as pool:
        claimed=list(pool.map(ledger.claim,[str(i) for i in range(8)]))
    assert sum(r is not None for r in claimed)==2
    assert sum(r['status']=='running' for r in ledger.read()['runs'].values())==2


@async_test
async def test_retry_backoff_budget_identities_event_dedup_and_recovery(tmp_path):
    calls=[]
    async def execute(*args):calls.append(len(calls));return {'ok':False}
    engine,_,_=await setup(tmp_path,execute)
    clock=[1000.0];engine.ledger.clock=lambda:clock[0]
    engine.actions.put('actions',{**action(),'idempotent':True})
    engine.ledger.put(trigger(retry={'enabled':True}),0,True)
    try:
        await engine.match(event())
        first=(await wait_status(engine,'retry_wait'))[0]
        assert first['attemptCount']==1 and first['attempts'][0]['status']=='failed'
        assert datetime.fromisoformat(first['nextAttemptAt']).timestamp()==1001
        await engine.kick();await asyncio.sleep(0)
        assert len(calls)==1
        # A new Engine recovers the persisted wait without losing count/time.
        await engine.close()
        from packages.automation.engine import Engine
        engine=Engine(engine.ledger,engine.bus,engine.actions,execute,engine.state_reader)
        await engine.start()
        assert len(calls)==1
        clock[0]=1001;await engine.kick()
        second=(await wait_status(engine,'retry_wait',attempts=2))[0]
        assert second['attemptCount']==2 and datetime.fromisoformat(second['nextAttemptAt']).timestamp()==1003
        clock[0]=1003;await engine.kick()
        final=(await wait_status(engine,'failed'))[0]
        assert final['attemptCount']==3 and len({a['id'] for a in final['attempts']})==3
        assert [a['number'] for a in final['attempts']]==[1,2,3]
        assert all(a['status']=='failed' for a in final['attempts'])
        await engine.match(event());await engine.kick()
        assert len(calls)==3 and len(await rows(engine))==1
    finally:await engine.close()


@async_test
async def test_idempotence_or_policy_cannot_be_promoted_after_first_start(tmp_path):
    for field in ('action','trigger'):
        entered,release=asyncio.Event(),asyncio.Event();calls=[]
        async def execute(*args):
            calls.append(1);entered.set();await release.wait();return {'ok':False}
        engine,_,_=await setup(tmp_path/field,execute)
        engine.actions.put('actions',{**action(),'idempotent':field=='trigger'})
        engine.ledger.put(trigger(retry={'enabled':field=='action'}),0,True)
        try:
            await engine.match(event());await asyncio.wait_for(entered.wait(),2)
            if field=='action':engine.actions.put('actions',{**action(),'idempotent':True})
            else:engine.ledger.put(trigger(retry={'enabled':True}),1,True)
            release.set();row=(await wait_status(engine,'failed'))[0]
            assert row['retryEligible'] is False and calls==[1]
        finally:release.set();await engine.close()


@async_test
async def test_action_change_during_backoff_fails_without_new_effect(tmp_path):
    calls=[]
    async def execute(*args):calls.append(1);return {'ok':False}
    engine,_,_=await setup(tmp_path,execute);clock=[1000.0];engine.ledger.clock=lambda:clock[0]
    engine.actions.put('actions',{**action(),'idempotent':True})
    engine.ledger.put(trigger(retry={'enabled':True}),0,True)
    try:
        await engine.match(event());await wait_status(engine,'retry_wait')
        engine.actions.put('actions',{**action(),'idempotent':True,'enabled':False})
        clock[0]+=1;await engine.kick()
        row=(await wait_status(engine,'failed'))[0]
        assert len(calls)==1 and 'Action changed' in row['error']
        assert row['attemptCount']==2 and row['attempts'][0]['status']=='failed'
    finally:await engine.close()


@async_test
async def test_wait_cancel_disable_and_reenable_never_replay_history(tmp_path):
    calls=[]
    async def execute(*args):calls.append(1);return {'ok':False}
    engine,_,_=await setup(tmp_path,execute);clock=[1000.0];engine.ledger.clock=lambda:clock[0]
    engine.actions.put('actions',{**action(),'idempotent':True})
    t=trigger(retry={'enabled':True});engine.ledger.put(t,0,True)
    try:
        await engine.match(event('cancel'))
        row=(await wait_status(engine,'retry_wait'))[0]
        engine.ledger.cancel(row['id'],row['revision'])
        assert engine.ledger.read()['runs'][row['id']]['attempts'][0]['status']=='failed'
        await engine.match(event('disable'))
        async def waiting():return any(r['status']=='retry_wait' for r in await rows(engine))
        await until(waiting)
        engine.ledger.put({**t,'enabled':False},1,True);await engine.kick()
        assert all(r['status']=='cancelled' for r in await rows(engine))
        engine.ledger.put(t,2,True);clock[0]+=100;await engine.kick()
        await engine.match(event('disable'));await engine.match(event('cancel'))
        assert calls==[1,1] and all(r['status']=='cancelled' for r in await rows(engine))
    finally:await engine.close()


@async_test
async def test_query_retry_uses_frozen_targets_and_only_failed_targets(tmp_path):
    from functools import partial
    from packages.actions.executor import execute_dynamic
    queries=[];calls=[]
    async def query(predicate):queries.append(1);return {'ids':['a','b'],'candidateCount':2}
    async def pause(sid,args):
        calls.append(sid);return {'ok':sid=='a' or calls.count('b')>1}
    engine,_,_=await setup(tmp_path,partial(execute_dynamic,pause=pause,query=query))
    engine.actions.put('actions',{**action(),'idempotent':True,'execution':{'kind':'session_operation','operation':'pause_everything','selector':{'mode':'query','query':{'field':'name','op':'contains','value':'chosen'}}}})
    clock=[1000.0];engine.ledger.clock=lambda:clock[0];engine.ledger.put(trigger(retry={'enabled':True}),0,True)
    try:
        await engine.match(event());first=(await wait_status(engine,'retry_wait'))[0]
        assert first['targetIds']==['a','b'] and calls==['a','b']
        clock[0]+=1;await engine.kick();final=(await wait_status(engine,'succeeded'))[0]
        assert queries==[1] and calls==['a','b','b']
        assert final['targetIds']==['a','b'] and final['attempts'][0]['targetResults'][1]['ok'] is False
        assert final['attempts'][1]['targetResults'][1]['ok'] is True
    finally:await engine.close()


@async_test
async def test_uncertain_no_auto_retry_confirm_revision_and_frozen_query(tmp_path):
    entered=asyncio.Event();calls=[]
    async def execute(*args):
        calls.append(1);entered.set();await asyncio.Event().wait()
    engine,_,_=await setup(tmp_path,execute)
    engine.actions.put('actions',{**action(),'idempotent':True})
    engine.ledger.put(trigger(retry={'enabled':True}),0,True)
    await engine.match(event());await asyncio.wait_for(entered.wait(),2);await engine.close()
    row=(await rows(engine))[0]
    assert row['status']=='uncertain' and 'nextAttemptAt' not in row
    with pytest.raises(Conflict):engine.ledger.confirm_uncertain(row['id'],row['revision']-1)
    confirmed=engine.ledger.confirm_uncertain(row['id'],row['revision'])
    with pytest.raises(Conflict):engine.ledger.confirm_uncertain(row['id'],row['revision'])
    assert confirmed['status']=='queued' and confirmed['attempts'][0]['status']=='uncertain'
    from packages.automation.engine import Engine
    async def success(*args):calls.append(2);return {'ok':True}
    engine=Engine(engine.ledger,engine.bus,engine.actions,success,engine.state_reader)
    try:
        await engine.start();final=(await wait_status(engine,'succeeded'))[0]
        assert calls==[1,2] and final['attemptCount']==2
        assert final['attempts'][0]['status']=='uncertain' and final['attempts'][1]['reason']=='user-confirmed'
    finally:await engine.close()


@pytest.mark.parametrize('mode',['serial','parallel'])
@async_test
async def test_serial_retry_head_order_and_parallel_independent_tail(tmp_path,mode):
    calls=[]
    async def execute(a,args,e,progress):
        calls.append(e['id']);return {'ok':e['id']!='head' or calls.count('head')>1}
    engine,_,_=await setup(tmp_path,execute);clock=[1000.0];engine.ledger.clock=lambda:clock[0]
    engine.actions.put('actions',{**action(),'idempotent':True})
    engine.ledger.put(trigger(executionMode=mode,retry={'enabled':True}),0,True)
    try:
        await engine.match(event('head'));await wait_status(engine,'retry_wait')
        await engine.match(event('tail'))
        if mode=='parallel':
            await until(lambda:asyncio.sleep(0,result='tail' in calls))
        else:
            owner=engine.tasks['t'];assert not owner.done() and calls==['head']
        clock[0]+=1;await engine.kick();await wait_status(engine,'succeeded',count=2)
        assert calls==(['head','head','tail'] if mode=='serial' else ['head','tail','head'])
    finally:await engine.close()


@async_test
async def test_disable_running_allows_finish_cancels_queue_and_wait(tmp_path):
    entered,release=asyncio.Event(),asyncio.Event();calls=[]
    async def execute(*args):calls.append(1);entered.set();await release.wait();return {'ok':False}
    engine,_,_=await setup(tmp_path,execute)
    engine.actions.put('actions',{**action(),'idempotent':True});t=trigger(retry={'enabled':True});engine.ledger.put(t,0,True)
    try:
        await engine.match(event('running'));await entered.wait();await engine.match(event('queued'))
        engine.ledger.put({**t,'enabled':False},1,True);await engine.kick()
        assert sorted(r['status'] for r in await rows(engine))==['cancelled','running']
        release.set()
        async def done():return all(r['status'] in {'cancelled','failed'} for r in await rows(engine))
        await until(done);assert calls==[1]
        assert not any('nextAttemptAt' in r for r in await rows(engine))
    finally:release.set();await engine.close()


@async_test
async def test_retry_pause_gate_plugin_loss_and_new_generation(tmp_path):
    calls=[]
    async def execute(*args):calls.append(1);return {'ok':len(calls)>1}
    engine,_,handle=await setup(tmp_path,execute);clock=[1000.0];engine.ledger.clock=lambda:clock[0]
    engine.actions.put('actions',{**action(),'idempotent':True});engine.ledger.put(trigger(retry={'enabled':True}),0,True)
    try:
        await engine.match(event());await wait_status(engine,'retry_wait')
        await engine.bus.deactivate(handle);clock[0]+=1;await engine.kick()
        assert calls==[1] and (await rows(engine))[0]['status']=='retry_wait'
        with pytest.raises(ValueError):engine.bus.publish(handle,'workers.all_idle',{})
        fresh=engine.bus.activate('core','workers');engine.bus.register(fresh,'workers.all_idle',{'type':'object'})
        await engine.kick();row=(await wait_status(engine,'succeeded'))[0]
        assert calls==[1,1] and row['attemptCount']==2
    finally:await engine.close()


@async_test
async def test_cancel_after_finish_commit_preserves_committed_outcome(tmp_path,monkeypatch):
    engine,_,_=await setup(tmp_path);engine.ledger.put(trigger(),0,True)
    committed=asyncio.Event();original=engine.io
    async def io(fn,*args,**kw):
        result=await original(fn,*args,**kw)
        if fn==engine.ledger.finish:
            committed.set();await asyncio.Event().wait()
        return result
    monkeypatch.setattr(engine,'io',io)
    await engine.match(event());await asyncio.wait_for(committed.wait(),2);await engine.close()
    row=(await rows(engine))[0]
    assert row['status']=='succeeded' and row['attempts'][0]['status']=='succeeded'
    assert not engine.tasks and not engine.signals


@async_test
async def test_retry_schedule_write_failure_is_uncertain_not_reexecution(tmp_path,monkeypatch):
    import packages.automation.store as module
    engine,_,_=await setup(tmp_path,lambda_execute_failure)
    engine.actions.put('actions',{**action(),'idempotent':True});engine.ledger.put(trigger(retry={'enabled':True}),0,True)
    original=module.atomic
    def atomic(path,state):
        if any(r['status']=='retry_wait' for r in state['runs'].values()):raise OSError('injected retry schedule fsync failure')
        return original(path,state)
    monkeypatch.setattr(module,'atomic',atomic)
    try:
        await engine.match(event());row=(await wait_status(engine,'uncertain'))[0]
        assert row['attemptCount']==1 and row['attempts'][0]['status']=='uncertain'
        assert 'fsync failure' in row['error'] and 'nextAttemptAt' not in row
        await engine.kick();assert len(await rows(engine))==1
    finally:await engine.close()


async def lambda_execute_failure(*args):return {'ok':False}


@async_test
async def test_uncertain_query_confirmation_reuses_ids_and_successes(tmp_path):
    from functools import partial
    from packages.actions.executor import execute_dynamic
    entered=asyncio.Event();calls=[];queries=[]
    async def query(q):queries.append(1);return {'ids':['a','b'],'candidateCount':2}
    async def pause(sid,args):
        calls.append(sid)
        if sid=='b':entered.set();await asyncio.Event().wait()
        return {'ok':True}
    engine,_,_=await setup(tmp_path,partial(execute_dynamic,pause=pause,query=query))
    engine.actions.put('actions',{**action(),'execution':{'kind':'session_operation','operation':'pause_everything','selector':{'mode':'query','query':{'field':'name','op':'eq','value':'before'}}}})
    engine.ledger.put(trigger(),0,True)
    await engine.match(event());await asyncio.wait_for(entered.wait(),2);await engine.close()
    row=(await rows(engine))[0];assert row['status']=='uncertain' and row['targetIds']==['a','b']
    engine.ledger.confirm_uncertain(row['id'],row['revision'])
    async def no_query(q):raise AssertionError('query must not be reevaluated')
    async def success(sid,args):calls.append(sid);return {'ok':True}
    from packages.automation.engine import Engine
    engine=Engine(engine.ledger,engine.bus,engine.actions,partial(execute_dynamic,pause=success,query=no_query),engine.state_reader)
    try:
        await engine.start();final=(await wait_status(engine,'succeeded'))[0]
        assert calls==['a','b','b'] and queries==[1]
        assert final['attempts'][0]['status']=='uncertain' and final['attempts'][0]['targetResults']==[{'sessionId':'a','ok':True}]
        assert final['attempts'][1]['status']=='succeeded' and final['retryEligible'] is False
    finally:await engine.close()


def test_manual_confirmation_is_bounded_and_disabled_rejected(tmp_path):
    ledger=Ledger(tmp_path);t=trigger();ledger.put(t,0,True)
    ledger.append(dict(id='r',triggerId='t',triggerRevision=1,event=event(),status='queued'))
    for attempt in range(3):
        run=ledger.claim('r');ledger.interrupt('r',run['activeAttemptId'],'owned interruption')
        run=ledger.read()['runs']['r']
        if attempt<2:ledger.confirm_uncertain('r',run['revision'])
    with pytest.raises(Conflict,match='limit'):ledger.confirm_uncertain('r',run['revision'])
    ledger.put({**t,'enabled':False},1,True)
    with pytest.raises(Conflict,match='Enable'):ledger.confirm_uncertain('r',run['revision'])
    assert [a['status'] for a in ledger.read()['runs']['r']['attempts']]==['uncertain']*3


def test_retry_defaults_cap_and_schema_matches_parser():
    import jsonschema
    from packages.automation.policy import backoff,SCHEDULING_SCHEMA
    assert [backoff(i) for i in range(1,11)]==[1,2,4,8,16,32,60,60,60,60]
    for value in [{'executionMode':'parallel'},{'executionMode':'parallel','maxConcurrency':32},{'retry':{'enabled':True,'maxAttempts':3}}]:
        jsonschema.validate(value,SCHEDULING_SCHEMA)
    for value in [{'executionMode':'serial','maxConcurrency':3},{'retry':{'enabled':'true'}},{'retry':{'maxAttempts':11}},{'mode':'parallel'}]:
        with pytest.raises(jsonschema.ValidationError):jsonschema.validate(value,SCHEDULING_SCHEMA)


@async_test
async def test_schema_and_confirmation_api_require_user_and_exact_revision(tmp_path):
    import httpx
    from fastapi import FastAPI
    from types import SimpleNamespace
    from packages.automation.api import install
    from tests.test_event_store import setup as service_setup
    svc,_=await service_setup(tmp_path)
    app=FastAPI();app.state.automation=svc;install(SimpleNamespace(app=app))
    try:
        svc.ledger.append(dict(id='r',triggerId='t',triggerRevision=1,event=event(),actionId='pause',arguments={},status='queued'))
        r=svc.ledger.claim('r');svc.ledger.interrupt('r',r['activeAttemptId'],'owned uncertainty');r=svc.ledger.read()['runs']['r']
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://owned') as client:
            schema=(await client.get('/api/automation/schema')).json()
            assert schema['limits']['maxConcurrency']==32 and schema['actionProperties']['idempotent']['default'] is False
            body={'expectedRevision':r['revision'],'confirm':True}
            url='/api/automation/runs/r/confirm-recovery'
            assert (await client.post(url,json=body)).status_code==401
            token=(await client.post('/api/automation/login',json={'key':svc.auth.key()})).json()['token']
            headers={'Authorization':'Bearer '+token}
            assert (await client.post(url,json={**body,'confirm':False},headers=headers)).status_code==400
            assert (await client.post(url,json={**body,'source':'core'},headers=headers)).status_code==400
            assert (await client.post(url,json={**body,'expectedRevision':r['revision']-1},headers=headers)).status_code==409
            assert (await client.post(url,json=body,headers=headers)).status_code==200
            assert (await client.post(url,json=body,headers=headers)).status_code==409
            await client.post('/api/automation/logout',headers=headers)
        await wait_status(svc.engine,'succeeded')
        assert svc.ledger.read()['runs']['r']['attempts'][0]['status']=='uncertain'
    finally:await svc.close()



def test_older_client_updates_preserve_explicit_scheduler_policy(tmp_path):
    ledger=Ledger(tmp_path)
    ledger.put(trigger(executionMode='parallel',maxConcurrency=4,retry={'enabled':True,'maxAttempts':5}),0,True)
    t=ledger.put({**trigger(),'name':'old-client-name-edit'},1,True)
    assert t['executionMode']=='parallel' and t['maxConcurrency']==4
    assert t['retry']=={'enabled':True,'maxAttempts':5}


@async_test
async def test_committed_retry_does_not_overlap_unretired_attempt_task(tmp_path,monkeypatch):
    calls=[];committed,retire,observed=asyncio.Event(),asyncio.Event(),asyncio.Event()
    async def execute(*args):calls.append(1);return {'ok':len(calls)>1}
    engine,_,_=await setup(tmp_path,execute);clock=[1000.0];engine.ledger.clock=lambda:clock[0]
    engine.actions.put('actions',{**action(),'idempotent':True});engine.ledger.put(trigger(executionMode='parallel',retry={'enabled':True}),0,True)
    original=engine.io
    async def io(fn,*args,**kw):
        result=await original(fn,*args,**kw)
        if fn==engine.ledger.finish and result['attemptCount']==1:
            committed.set();await retire.wait()
        if fn==engine.ledger.read and asyncio.current_task() is engine.tasks.get('t') and clock[0]==1001:
            observed.set()
        return result
    monkeypatch.setattr(engine,'io',io)
    try:
        await engine.match(event());await asyncio.wait_for(committed.wait(),2)
        clock[0]=1001;await engine.kick();await asyncio.wait_for(observed.wait(),2)
        assert calls==[1] and (await rows(engine))[0]['attemptCount']==1
        retire.set();await wait_status(engine,'succeeded')
        assert calls==[1,1]
    finally:retire.set();await engine.close()


@async_test
async def test_run_capacity_diagnostic_and_waiting_counts_as_pending(tmp_path):
    entered,release=asyncio.Event(),asyncio.Event()
    async def execute(*args):entered.set();await release.wait();return {'ok':False}
    engine,_,_=await setup(tmp_path,execute);engine.max_pending=1
    engine.actions.put('actions',{**action(),'idempotent':True});engine.ledger.put(trigger(retry={'enabled':True}),0,True)
    try:
        await engine.match(event('held'));await entered.wait();release.set();await wait_status(engine,'retry_wait')
        await engine.match(event('over-capacity'))
        row=next(r for r in await rows(engine) if r['event']['id']=='over-capacity')
        assert row['status']=='failed' and row['attempts']==[] and row['error']=='Trigger backlog full'
        assert any(d['phase']=='capacity' and d['eventId']=='over-capacity' for d in engine.diagnostics)
    finally:release.set();await engine.close()


@async_test
async def test_event_store_replay_during_backoff_never_resets_attempt(tmp_path):
    from tests.test_event_store import setup as service_setup
    svc,_=await service_setup(tmp_path)
    svc.engine.execute=lambda_execute_failure;svc.ledger.clock=lambda:1000.0
    svc.engine.actions.put('actions',{**action(),'idempotent':True})
    svc.ledger.put(trigger(retry={'enabled':True}),1,True)
    try:
        await svc.bus.publish_async(svc.core,'workers.all_idle',{'runningCount':0},'stored-retry')
        first=(await wait_status(svc.engine,'retry_wait'))[0]
        await svc.replay(['stored-retry'])
        async def delivered():return (await svc.engine.io(svc.events.page))['events'][0]['deliveryCount']>=2
        await until(delivered)
        row=(await rows(svc.engine))[0]
        assert row['id']==first['id'] and row['attemptCount']==1 and row['nextAttemptAt']==first['nextAttemptAt']
    finally:await svc.close()



@async_test
async def test_close_after_parallel_claim_before_child_start_creates_no_effect(tmp_path,monkeypatch):
    engine,calls,_=await setup(tmp_path)
    engine.ledger.put(trigger(executionMode='parallel'),0,True)
    claimed=asyncio.Event();original=engine.io
    async def io(fn,*args,**kw):
        result=await original(fn,*args,**kw)
        if fn==engine.ledger.claim and result:
            claimed.set();await asyncio.Event().wait()
        return result
    monkeypatch.setattr(engine,'io',io)
    await engine.match(event());await asyncio.wait_for(claimed.wait(),2);await engine.close()
    row=(await rows(engine))[0]
    assert calls==[] and row['status']=='uncertain' and row['attemptCount']==1
    assert not engine.tasks and not engine.signals
    await engine.kick();assert not engine.tasks


@async_test
async def test_parallel_empty_snapshot_wake_is_not_lost(tmp_path,monkeypatch):
    engine,calls,_=await setup(tmp_path)
    engine.ledger.put(trigger(executionMode='parallel'),0,True)
    snapshot,release=asyncio.Event(),asyncio.Event();original=engine.io
    async def io(fn,*args,**kw):
        result=await original(fn,*args,**kw)
        if (fn==engine.ledger.read and asyncio.current_task() is engine.tasks.get('t')
                and calls and not snapshot.is_set() and not any(r['status']=='queued' for r in result['runs'].values())):
            snapshot.set();await release.wait()
        return result
    monkeypatch.setattr(engine,'io',io)
    try:
        await engine.match(event('first'));await asyncio.wait_for(snapshot.wait(),2)
        owner=engine.tasks['t'];await engine.match(event('second'));assert engine.tasks['t'] is owner
        release.set();await wait_status(engine,'succeeded',count=2)
        assert calls==['selected','selected']
    finally:release.set();await engine.close()
