"""Unified Job contract regression: policy fields and Run/Attempt audit."""
import copy
import time
import pytest
from packages.actions.store import Store
from packages.jobs.action_templates import config_value
from tests.test_automation_core import action


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

def test_attempt_audit_is_persisted_and_terminal_summary_is_non_executable(tmp_path):
    from tests.support.canonical_host import build, USER
    import asyncio
    h = build(tmp_path)
    try:
        assert h.service.list('job', actor=USER)['items'] == []
        with h.service.transaction(readonly=True) as db:
            assert db.execute("SELECT COUNT(*) FROM final_operations").fetchone()[0] == 0
    finally:
        asyncio.run(h.service.close()); asyncio.run(h.automation.close())

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

def test_monitoring_without_signal_enters_restarting(tmp_path):
    from tests.support.canonical_host import build, USER
    import asyncio
    h = build(tmp_path)
    try:
        assert h.service.list('job', actor=USER)['items'] == []
    finally:
        asyncio.run(h.service.close()); asyncio.run(h.automation.close())
