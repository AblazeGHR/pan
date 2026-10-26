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
