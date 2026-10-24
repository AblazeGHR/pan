"""Latest-main snapshot/authorization coexistence for the narrow delete fix."""
import copy
import asyncio

import httpx
import pytest

from packages.core import background_jobs as jobs
from packages.jobs import api as legacy
from tests.test_automation_core import async_test
from tests.test_authorization_mcp import harness, close
from tests.test_job_action_templates import config


def test_cleanup_pending_preserves_system_snapshot_attempt_and_no_terminal_notice(tmp_path, monkeypatch):
    record=dict(jobId='job_snapshot_delete',kind='background-process',status='starting',
                name='owned',targetSessionId='owned',argv=['synthetic'],cwd=str(tmp_path),createdAt=1)
    jobs._create(record)
    before=jobs.get(record['jobId'])
    snapshot=copy.deepcopy(before['attemptSnapshot'])
    attempt=copy.deepcopy(before['attempts'])
    monkeypatch.setattr(jobs,'_terminate_job_processes',lambda *a: {'confirmed':False,'reason':'unconfirmed'})
    with pytest.raises(ValueError,match='unconfirmed'):
        jobs.delete_job(record['jobId'])
    jobs.runner_update(record['jobId'],status='completed',exitCode=0,processTreeEnded=True)
    current=jobs.get(record['jobId'])
    assert current['status']=='cleanup_pending'
    assert current['attemptSnapshot']==snapshot
    assert current['attempts']==attempt
    assert current['snapshotCleanup']=='active'
    assert not current.get('terminalEventId'), 'late terminal observation cannot publish a terminal notice'
    assert not current.get('lastExecution')
    monkeypatch.setattr(jobs,'_terminate_job_processes',lambda *a: {'confirmed':True})
    assert jobs.delete_job(record['jobId'])
    with pytest.raises(ValueError,match='not found'):
        jobs.runner_update(record['jobId'],status='completed')


@async_test
async def test_authorized_action_delete_preserves_active_snapshot_and_legacy_guard(tmp_path,monkeypatch):
    h=await harness(tmp_path,monkeypatch)
    h.app.include_router(legacy.router)
    monkeypatch.setattr(legacy,'_job_root',lambda:h.rt.root)
    try:
        j=h.rt.create(config(),True)
        jid=j['jobId'];endpoint='/api/jobs/action-jobs/'+jid+'?expectedRevision=1'
        claimed=h.rt.claim(jid,'owned_combination_execution',1)
        snap=h.rt.templates.snapshot(claimed,claimed['execution'])
        jobs._update(jid,{'status':'cleanup_pending'},registry_root=h.rt.root)
        before=copy.deepcopy(h.rt.get(jid))
        token=h.svc.auth.login(h.svc.auth.key());headers={'Authorization':'Bearer '+token}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(h.app),base_url='http://owned') as client:
            assert (await client.delete(endpoint)).status_code==401
            response=await client.delete(endpoint,headers=headers)
            assert response.status_code==409
            blocked=(await client.delete('/api/jobs/'+jid,headers=headers)).json()
            assert blocked['error']['code']=='user_confirmation_required'
            assert h.rt.get(jid)==before
            assert h.rt.actions.read()['jobExecutions']['owned_combination_execution']['digest']==snap['digest']
            # The existing finalizer performs cleanup before a legitimate user
            # can delete; deletion does not cancel/finish an admitted Attempt.
            h.rt.finalize(jid,'owned_combination_execution','uncertain')
            assert 'owned_combination_execution' not in h.rt.actions.read().get('jobExecutions',{})
            assert (await client.delete('/api/jobs/action-jobs/'+jid+'?expectedRevision=0',headers=headers)).status_code==409
            assert (await client.delete(endpoint,headers=headers)).json()['deleted']
            assert not jobs.get(jid,h.rt.root)
    finally:
        await close(h)
