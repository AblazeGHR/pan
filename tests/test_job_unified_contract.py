"""Unified Job contract regression: policy fields and Run/Attempt audit."""
import copy
import time
import pytest
from packages.actions.store import Store
from packages.jobs.action_templates import config_value
from tests.test_automation_core import action
from tests.test_job_final_http_service import h, create, control, job_def


def base(store=None):
    return {'name':'unified','actionId':'pause','arguments':{},'schedule':[{'kind':'interval','intervalSec':60}], 'enabled':False}

def state():
    value = action()
    return {'revision': 0, 'actions': {'pause': value}, 'groups': {}, 'retired_plugins': []}


def test_unified_policy_defaults_are_conservative(tmp_path):
    value=config_value(base(),state())
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
    raw={**base(),**patch}
    with pytest.raises(ValueError):config_value(raw,state())

def test_event_query_is_structured_and_bounded(tmp_path):
    raw={**base(),'trigger':{'kind':'event','query':{'and':[{'field':'adapter','op':'eq','value':'codex'},{'not':{'field':'pinned','op':'eq','value':True}}]}}}
    raw['trigger']['eventTypes']=['workers.all_idle']
    assert config_value(raw,state())['trigger']['kind']=='event'

def test_attempt_audit_is_persisted_and_terminal_summary_is_non_executable(h):
    import asyncio
    job = create(h)
    enabled = control(h, 'jobs', job, 'enable')
    assert enabled.status_code == 200, enabled.text
    job = enabled.json()['result']['object']
    response = h.client.post('/api/jobs/' + job['id'] + '/runs', json={
        'expectedRevision': job['configRevision'], 'requestKey': 'unified-audit-run',
        'input': {}, 'cause': {'type': 'manual'}})
    assert response.status_code == 202, response.text
    asyncio.run(h.service.dispatch_once())
    state = h.service.final.get(job['id'])
    assert state['runs'] and state['attempts'] and state['effects']
    run = next(iter(state['runs'].values()))
    attempt = next(iter(state['attempts'].values()))
    effect = state['effects'][0]
    assert attempt['runId'] == run['runId'] and effect['attemptId'] == attempt['attemptId']
    assert effect['effectKey']['runId'] == run['runId']
    assert 'execution' not in state

from packages.jobs.unified import project

def test_legacy_job_projection_is_unified_without_storage_rewrite():
    row=project({'jobId':'j1','kind':'session-message','name':'hello','enabled':True,'schedule':[],'status':'scheduled'})
    assert row['action']['actionId']=='system_session_message'
    assert row['config']['trigger']=={'kind':'schedule'}
    assert row['compatibility']['migrationIssues']  # Missing legacy target/body cannot become immediate.
    assert row['config']['recovery']['enabled'] is False
    assert row['compatibility']['migration']=='system-executor'

def test_legacy_unknown_kind_is_conservative():
    row=project({'id':'j2','kind':'future-kind','enabled':True})
    assert row['action']['actionId']=='system.legacy_future_kind'
    assert row['config']['recovery']['enabled'] is False

import asyncio
from packages.jobs.action_runtime import Runtime
from tests.test_automation_core import until

def test_monitoring_without_signal_enters_restarting(h):
    job = create(h)
    enabled = control(h, 'jobs', job, 'enable')
    assert enabled.status_code == 200, enabled.text
    state = h.service.final.get(job['id'])
    assert state['runs'] == {} and state['attempts'] == {} and state['effects'] == []
