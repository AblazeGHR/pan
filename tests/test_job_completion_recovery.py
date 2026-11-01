"""Completion evidence is distinct from delivery certainty and recovery intent."""
import asyncio
import copy

import pytest

from packages.actions.store import Store
from packages.jobs.action_runtime import Runtime, Conflict
from packages.jobs import system_actions
from tests.test_automation_core import action
from tests.job_retirement_native_fixture import h as native_h  # noqa: F401
from tests.test_retirement_rev16_behavior import h as delivery_h  # noqa: F401


def config():
    return {'name':'monitor','actionId':'pause','arguments':{},'enabled':True,'schedule':[],
            'trigger':{'kind':'immediate'},'completion':{'kind':'monitoring','signalType':'done'},
            'recovery':{'enabled':True,'monitoring':True,'maxAttempts':3,'backoffSec':[0,0,0],'stableResetSec':300}}


@pytest.mark.parametrize('result,expected',[
    ({'ok':True,'processTreeEnded':True,'panJobCompletion':{'type':'done','completed':True}},'completed'),
    ({'ok':True,'processTreeEnded':False,'panJobCompletion':{'type':'done','completed':True}},'restarting'),
    ({'ok':True,'processTreeEnded':True,'panJobCompletion':{'type':'other','completed':True}},'restarting'),
    ({'status':'unknown'},'uncertain'),
    ({'status':'uncertain','panJobCompletion':{'type':'done','completed':True}},'uncertain'),
])
def test_completion_and_unknown_are_independent(tmp_path,monkeypatch,result,expected):
    async def run():
        store=Store(tmp_path/'actions');store.put('actions',{**action(),'monitoring':True,'recoverable':True})
        rt=Runtime(tmp_path/'jobs',store,None);await rt.start()
        calls=[]
        async def execute(*_):calls.append(1);return result
        monkeypatch.setattr('packages.actions.executor.execute_dynamic',execute)
        try:
            job=rt.create(config(),True);await rt.launch(job['jobId'],1)
            await asyncio.gather(*rt.tasks.values())
            current=rt.get(job['jobId'])
            assert current['status']==expected
            assert current['snapshotCleanup']=='cleaned'
            assert not store.read().get('jobExecutions')
            if expected=='uncertain':
                await rt.tick({'enabled':True});assert calls==[1]
        finally:await rt.close()
    asyncio.run(run())


def test_recovery_exhausts_same_run_distinct_attempts(tmp_path,monkeypatch):
    async def run():
        store=Store(tmp_path/'actions');store.put('actions',{**action(),'monitoring':True,'recoverable':True})
        rt=Runtime(tmp_path/'jobs',store,None);await rt.start()
        async def execute(*_):return {'ok':True}
        monkeypatch.setattr('packages.actions.executor.execute_dynamic',execute)
        try:
            job=rt.create(config(),True)
            for _ in range(4):
                await rt.tick({'enabled':True});await asyncio.gather(*list(rt.tasks.values()))
            current=rt.get(job['jobId'])
            assert current['status']=='failed'
            assert len(current['attempts'])==3
            assert len({a['runId'] for a in current['attempts']})==1
            assert len({a['id'] for a in current['attempts']})==3
            assert current['recoveryFailures']==3
        finally:await rt.close()
    asyncio.run(run())


def test_system_delivery_confirmation_does_not_resend_receipts(delivery_h):
    # Original accepted/unknown/new checkpoint semantics now live in the
    # canonical SessionDelivery reservation, not system_actions.execute.
    # Exercise real queue acceptance, lost ACK, durable child fences and a
    # cold owner. Only the proven never-attempted slot may deliver; accepted
    # and unknown originals are queried, never sent again.
    from tests.test_retirement_session_runtime import test_mixed_accepted_unknown_and_unsent_only_unsent_retargets
    test_mixed_accepted_unknown_and_unsent_only_unsent_retargets(delivery_h, False)


def test_unavailable_event_retains_persistent_queue(tmp_path):
    async def run():
        store=Store(tmp_path/'actions');store.put('actions',action())
        rt=Runtime(tmp_path/'jobs',store,None);await rt.start()
        try:
            cfg={**config(),'completion':{'kind':'ordinary'},'recovery':{**config()['recovery'],'enabled':False,'monitoring':False},
                 'trigger':{'kind':'event','eventTypes':['owned.change'],'query':{'field':'name','op':'eq','value':'a'}}}
            job=rt.create(cfg,True);target=rt.event_targets('owned.change')[0]
            rt.queue_event({'id':'event1','type':'owned.change','payload':{}},target)
            rt.event_available=lambda _:False
            await rt.tick({'enabled':True})
            current=rt.get(job['jobId'])
            assert len(current['pendingRuns'])==1 and not current.get('attempts')
            assert rt.event_targets('owned.change')==[]
        finally:await rt.close()
    asyncio.run(run())


def test_notification_gate_retains_outbox_and_never_reexecutes(tmp_path,monkeypatch):
    async def run():
        store=Store(tmp_path/'actions');store.put('actions',action())
        rt=Runtime(tmp_path/'jobs',store,None);await rt.start()
        effects=[];notices=[];allowed=False
        async def execute(*_):effects.append(1);return {'ok':True}
        async def emit(*_):return True
        async def notice(sid,text,**kwargs):
            assert 'secret business text' not in text
            notices.append((sid,kwargs['event_id']))
            return {'ok':allowed}
        monkeypatch.setattr('packages.actions.executor.execute_dynamic',execute)
        monkeypatch.setattr('packages.core.background_jobs._scheduled_task_emit_strict',emit)
        monkeypatch.setattr('packages.core.background_jobs._worker.enqueue_notice',notice)
        cfg={**config(),'completion':{'kind':'ordinary'},'recovery':{**config()['recovery'],'enabled':False,'monitoring':False},
             'notification':{'on':['completed'],'target':'job','sessionIds':['result-session']}}
        try:
            job=rt.create(cfg,True);await rt.launch(job['jobId'],1);await asyncio.gather(*rt.tasks.values())
            assert rt.get(job['jobId'])['terminalOutbox']
            allowed=True
            await rt.tick({'enabled':True})
            assert effects==[1] and not rt.get(job['jobId'])['terminalOutbox']
            assert len(set(notices))==1  # Same durable queue idempotency key.
        finally:await rt.close()
    asyncio.run(run())


def test_event_query_is_frozen_across_monitor_attempts(tmp_path,monkeypatch):
    async def run():
        store=Store(tmp_path/'actions');store.put('actions',{**action(),'monitoring':True,'recoverable':True})
        rt=Runtime(tmp_path/'jobs',store,None);await rt.start()
        selections=[];contexts=[]
        async def resolve(_):selections.append(1);return {'ids':['original'],'candidateCount':1}
        async def execute(a,p,context,progress,pause):
            contexts.append(copy.deepcopy(context['jobSelection']))
            return {'ok':True}  # Missing completion -> one more Attempt.
        monkeypatch.setattr('packages.actions.session_query.resolve',resolve)
        monkeypatch.setattr('packages.actions.executor.execute_dynamic',execute)
        try:
            cfg={**config(),'trigger':{'kind':'event','eventTypes':['owned.e'],'query':{'field':'name','op':'eq','value':'first'}}}
            job=rt.create(cfg,True);target=rt.event_targets('owned.e')[0]
            rt.queue_event({'id':'e','type':'owned.e','payload':{}},target)
            await rt.tick({'enabled':True});await asyncio.gather(*rt.tasks.values())
            await rt.tick({'enabled':True});await asyncio.gather(*rt.tasks.values())
            assert selections==[1] and contexts==[{'ids':['original'],'candidateCount':1}]*2
            assert len({a['runId'] for a in rt.get(job['jobId'])['attempts']})==1
        finally:await rt.close()
    asyncio.run(run())


def test_event_admission_respects_shared_scheduler_disabled(tmp_path,monkeypatch):
    async def run():
        store=Store(tmp_path/'actions');store.put('actions',action())
        rt=Runtime(tmp_path/'jobs',store,None);await rt.start()
        monkeypatch.setattr('packages.core.background_jobs._scheduled_task_config',lambda:{'enabled':False})
        try:
            cfg={**config(),'completion':{'kind':'ordinary'},'recovery':{**config()['recovery'],'enabled':False,'monitoring':False},
                 'trigger':{'kind':'event','eventTypes':['owned.e'],'query':{'field':'pinned','op':'eq','value':True}}}
            job=rt.create(cfg,True)
            await rt.match_event({'id':'e','type':'owned.e','payload':{}},rt.event_targets('owned.e'))
            current=rt.get(job['jobId'])
            assert len(current['pendingRuns'])==1 and not current.get('attempts')
        finally:await rt.close()
    asyncio.run(run())
