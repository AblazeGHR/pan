"""Unified Job contract regression: policy fields and Run/Attempt audit."""
import copy
import time
import pytest
from packages.actions.store import Store
from packages.jobs.action_templates import config_value
from tests.test_automation_core import action


def base(store):
    store.put('actions', action())
    return {'name':'unified','actionId':'pause','arguments':{},'schedule':[{'kind':'interval','intervalSec':60}], 'enabled':False}


def test_unified_policy_defaults_are_conservative(tmp_path):
    store=Store(tmp_path/'actions'); value=config_value(base(store),store.read())
    assert value['trigger']=={'kind':'schedule'}
    assert value['recovery']['enabled'] is False and value['recovery']['maxAttempts']==3
    assert value['overlap']=={'mode':'forbid','parallelLimit':3}
    assert value['completion']=={'kind':'ordinary'} and value['notification']['target']=='job'

@pytest.mark.parametrize('patch',[
    {'trigger':{'kind':'event','query':{'field':'unknown','op':'eq','value':'x'}}},
    {'trigger':{'kind':'event','query':{'field':'name','op':'eq','value':1}}},
    {'recovery':{'enabled':True,'monitoring':False,'maxAttempts':3,'backoffSec':[5,15,60],'stableResetSec':300}},
    {'completion':{'kind':'monitoring'}},
    {'overlap':{'mode':'parallel','parallelLimit':33}},
    {'overlap':{'mode':'parallel','parallelLimit':3},'recovery':{'enabled':False,'monitoring':True,'maxAttempts':3,'backoffSec':[5,15,60],'stableResetSec':300}},
    {'notification':{'on':['completed'],'target':'session-message'}},
])
def test_invalid_unified_policy_fails_closed(tmp_path,patch):
    store=Store(tmp_path/'actions'); raw={**base(store),**patch}
    with pytest.raises(ValueError):config_value(raw,store.read())

def test_event_query_is_structured_and_bounded(tmp_path):
    store=Store(tmp_path/'actions'); raw={**base(store),'trigger':{'kind':'event','query':{'and':[{'field':'adapter','op':'eq','value':'codex'},{'not':{'field':'pinned','op':'eq','value':True}}]}}}
    raw['trigger']['eventTypes']=['workers.all_idle']
    assert config_value(raw,store.read())['trigger']['kind']=='event'

def test_attempt_audit_is_persisted_and_terminal_summary_is_non_executable(tmp_path):
    from packages.jobs.action_runtime import Runtime
    import asyncio
    async def run():
        rt=Runtime(tmp_path/'jobs',Store(tmp_path/'actions'),lambda *_:None)
        rt.actions.put('actions',action())
        job=rt.create({**base(rt.actions),'enabled':True},True)
        admitted=rt.claim(job['jobId'],'exec-1',1)
        assert admitted['execution']['attemptId'].startswith('attempt_')
        assert admitted['attempts'][0]['status']=='running'
        rt.templates.snapshot(admitted,admitted['execution'])
        rt.finalize(job['jobId'],'exec-1','uncertain',{'receipt':{'status':'unknown'}})
        done=rt.get(job['jobId'])
        assert done['lastExecution']['status']=='uncertain' and done['lastExecution']['attemptId'].startswith('attempt_')
        assert done['attempts'][0]['sideEffectUncertainty'] is True
        assert not rt.actions.read().get('jobExecutions')
    asyncio.run(run())

from packages.jobs.unified import project

def test_legacy_job_projection_is_unified_without_storage_rewrite():
    row=project({'jobId':'j1','kind':'session-message','name':'hello','enabled':True,'schedule':[],'status':'scheduled'})
    assert row['action']['actionId']=='system_session_message'
    assert row['config']['trigger']=={'kind':'immediate'}
    assert row['config']['recovery']['enabled'] is False
    assert row['compatibility']['migration']=='system-executor'

def test_legacy_unknown_kind_is_conservative():
    row=project({'id':'j2','kind':'future-kind','enabled':True})
    assert row['action']['actionId']=='system.legacy_future_kind'
    assert row['config']['recovery']['enabled'] is False

import asyncio
from packages.jobs.action_runtime import Runtime
from tests.test_automation_core import until

def test_monitoring_without_signal_enters_restarting(tmp_path):
    async def run():
        store=Store(tmp_path/'actions');store.put('actions',{**action(),'monitoring':True,'recoverable':True})
        rt=Runtime(tmp_path/'jobs',store,lambda *_: {'ok':True});await rt.start()
        raw=base(store);store.put('actions',{**action(),'monitoring':True,'recoverable':True})
        cfg={**raw,'enabled':True,'completion':{'kind':'monitoring','signalType':'health.completed'},'recovery':{'enabled':True,'monitoring':True,'maxAttempts':3,'backoffSec':[0,0,0],'stableResetSec':300}}
        job=rt.create(cfg,True);await rt.launch(job['jobId'],1)
        await until(lambda:asyncio.sleep(0,result=rt.get(job['jobId']).get('lastStatus')=='restarting'))
        current=rt.get(job['jobId']);assert current['status']=='restarting' and len(current['attempts'])==1
        await rt.tasks[job['jobId']]
        await rt.tick({'enabled':True,'max_concurrent_dispatch':5});await asyncio.sleep(.05)
        assert len(rt.get(job['jobId'])['attempts'])>=2
        await rt.close()
    asyncio.run(run())
