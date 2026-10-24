"""Unified Job/Template migration and Inbox permission boundary regressions."""
import copy
import asyncio
import pytest

from packages.actions.store import Store
from packages.jobs.action_templates import Templates
from packages.jobs.unified import project
from tests.test_automation_core import action


def test_disabled_agent_template_cannot_smuggle_enabled_instance(tmp_path):
    store=Store(tmp_path/'actions');store.put('actions',action())
    config={'name':'fixed','actionId':'pause','arguments':{},'enabled':True,
            'schedule':[],'trigger':{'kind':'immediate'}}
    with pytest.raises(PermissionError):
        Templates(store).put({'name':'agent draft','config':config},0,False)
    assert not store.read().get('jobTemplates')


def test_legacy_message_projection_is_disabled_typed_and_does_not_mutate():
    old={'jobId':'old','kind':'session-message','name':'old reminder','enabled':True,
         'targetSessionId':'session','text':'body','schedule':{'type':'interval','intervalSeconds':60},
         'lastExecution':{'id':'execution','runId':'logical-run'}}
    before=copy.deepcopy(old);view=project(old)
    assert view['config']['schedule']==[{'kind':'interval','intervalSec':60}]
    assert view['config']['enabled'] is False and old==before
    assert view['run']['runId']=='logical-run'


@pytest.mark.parametrize('kind,schedule',[
    ('future-kind',[]),('session-message',{'type':'weekly','weekday':True,'time':'13:00'}),
    ('session-message',{'type':'once','delaySeconds':5}),
    ('session-message',{'type':'weekly','weekday':0,'time':'12:00:15'}),
])
def test_ambiguous_legacy_migration_has_explicit_diagnostics(kind,schedule):
    view=project({'jobId':'old','kind':kind,'targetSessionId':'s','text':'body','enabled':True,'schedule':schedule})
    assert view['config']['enabled'] is False
    assert view['compatibility']['migrationIssues']
    assert view['compatibility']['reviewRequired'] is True
    assert view['config']['trigger']=={'kind':'schedule'} and view['config']['schedule']==[]


def test_readonly_inbox_and_job_notices_accept_queue_but_never_auto_force(tmp_path,monkeypatch):
    from unittest.mock import AsyncMock
    from packages.core import session,worker
    from packages.core.inbox_service import SessionQueue
    target=session.create('readonly-boundary',workdir=str(tmp_path));target.readonly_session=True;session.save(target)
    monkeypatch.setattr(worker,'_wake_worker',AsyncMock())
    monkeypatch.setattr(worker,'_bcast',AsyncMock())
    async def run():
        inbox=await SessionQueue().put(target.id,'q_owned',{'id':'m_owned','content':{'text':'not allowed'},
            'source':{'pluginId':'qq','scope':'user','peerId':'7'}})
        result=await worker.enqueue_notice(target.id,'Job status',source='automation',event_id='job-result:owned',notice_kind='job_result')
        # Trusted system/channel ingress may persist while readonly. It is not
        # CLI admission or permission for automatic Force; keep legacy semantics.
        assert inbox['status']=='accepted' and result['ok'] is True
        assert len(target.queue_pending)==2
        assert all(worker._schedule_auto_force(target.id,row) is None for row in target.queue_pending)
    asyncio.run(run())


def test_compatibility_event_without_loop_closes_coroutine(monkeypatch):
    from packages.scheduler import api
    coroutines=[]
    async def emit():pass
    def broadcast(_):
        result=emit();coroutines.append(result);return result
    monkeypatch.setitem(api._state,'broadcast',broadcast)
    api.on_event({'type':'job.updated'})
    assert len(coroutines)==1 and coroutines[0].cr_frame is None


def test_mcp_inbox_access_cannot_grant_job_enable_or_instantiation(monkeypatch):
    from packages.mcp import server
    calls=[]
    monkeypatch.setattr(server,'_caller_identity',lambda:{'id':'self','managed':[], 'panAccess':{'restrictToManaged':True}})
    monkeypatch.setattr(server,'_api',lambda *args:calls.append(args) or {})
    denied=server.agent_inbox('subscribe',locker_id='shared',session_id='outsider',mode='full')
    assert denied['error']['code']=='permission_denied'
    assert 'error' in server.agent_inbox('instantiate',locker_id='shared')
    assert 'error' in server.job_config_drafts('put','template',{'name':'draft','enabled':True})
    assert 'error' in server.job_config_drafts('instantiate','template',{'enabled':False},'jtpl_fixed',1)
    assert calls==[]


@pytest.mark.parametrize('weekday,cron',[(0,'30 12 * * 1'),(6,'30 12 * * 0')])
def test_weekly_projection_preserves_day_and_timezone(weekday,cron):
    view=project({'kind':'session-message','targetSessionId':'s','text':'body',
        'schedule':{'type':'weekly','weekday':weekday,'time':'12:30:00','timezone':'Asia/Singapore'}})
    assert view['config']['schedule']==[{'kind':'cron','cron':cron,'timezone':'Asia/Singapore'}]
    assert not view['compatibility']['migrationIssues']


def test_absolute_occurrence_never_shifts_and_lifecycle_stays_protected():
    old={'kind':'session-message','targetSessionId':'s','text':'body',
        'schedule':{'type':'once','delaySeconds':5},'nextRunAt':'2020-01-01T12:00:00Z'}
    assert project(old)['config']['schedule']==[{'kind':'once','at':'2020-01-01T12:00:00Z'}]
    assert project({'kind':'main-lifecycle'})['compatibility']['migrationIssues']
