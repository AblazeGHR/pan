"""Phase 4a legacy MCP creation is backed by one Action Job record."""
import asyncio

import pytest
from fastapi import FastAPI
from starlette.requests import Request

from packages.actions.store import Store
from packages.jobs.action_runtime import Runtime
from packages.jobs import system_actions
from tests.test_automation_core import async_test, until
from tests.test_automation_job_links import setup as links_setup, close as links_close
from tests.test_authorization_permissions import full_permissions


async def _runtime(tmp_path):
    store = Store(tmp_path / 'actions')
    rt = Runtime(tmp_path / 'jobs', store, None)
    await rt.start()
    return rt


def _delegated_request(app):
    from packages.automation.authorization import _MARKER, _SCOPE_KEY
    return Request({'type': 'http', 'method': 'POST', 'path': '/api/background-jobs',
                    'headers': [], 'query_string': b'', 'app': app,
                    _SCOPE_KEY: _MARKER})


@async_test
async def test_trusted_legacy_http_entry_returns_action_job_in_old_shape(tmp_path, monkeypatch):
    svc, rt, sources = await links_setup(tmp_path)
    rt.authorization = svc.auth.permission_allowed
    app = FastAPI(); app.state.automation = svc
    class Target:
        id = 'ses_target'; readonly_session = False; managed_by = None
    monkeypatch.setattr('packages.web.server.sess.get', lambda sid: Target() if sid == 'ses_target' else None)
    async def complete(*args, **kwargs):
        return {'status': 'completed'}
    monkeypatch.setattr(system_actions, 'execute', complete)
    svc.auth.change_permissions(full_permissions(), 0)
    try:
        result = await __import__('packages.web.server', fromlist=['api_background_job_start']).api_background_job_start({
            'targetSessionId': 'ses_target', 'argv': ['python'], 'cwd': str(tmp_path),
            'name': 'legacy MCP', '_legacyRequestKey': 'retry-key',
        }, _delegated_request(app))
        assert result['jobId'].startswith('job_') and result['kind'] == 'background-process'
        stored = rt.get(result['jobId'])
        assert stored['action']['actionId'] == 'system_background_process'
        assert stored['legacyCompat'] == 'background-process'
        again = await __import__('packages.web.server', fromlist=['api_background_job_start']).api_background_job_start({
            'targetSessionId': 'ses_target', 'argv': ['python'], 'cwd': str(tmp_path),
            'name': 'legacy MCP', '_legacyRequestKey': 'retry-key',
        }, _delegated_request(app))
        assert again['jobId'] == result['jobId']
    finally:
        await links_close(svc, rt)


@async_test
async def test_background_mcp_shape_uses_system_action_and_same_job_id(tmp_path, monkeypatch):
    rt = await _runtime(tmp_path)
    async def complete(*args, **kwargs):
        await args[3](processJobId='job_child')
        return {'status': 'completed', 'processJobId': 'job_child'}
    monkeypatch.setattr(system_actions, 'execute', complete)
    try:
        job = rt.create_legacy('background-process', {
            'name': 'legacy process', 'targetSessionId': 'ses_target',
            'argv': ['python', '-c', 'pass'], 'cwd': str(tmp_path),
            'hideWindow': True,
        }, {'type': 'user'})
        await rt.launch(job['jobId'], expected=1, authority={'type': 'user'})
        async def done():
            current = rt.get(job['jobId'])
            return current if current.get('lastExecution', {}).get('status') == 'completed' else None
        await until(done)
        current = rt.get(job['jobId'])
        assert current['jobId'] == job['jobId']
        assert current['kind'] == 'background-process'
        assert current['legacyCompat'] == 'background-process'
        assert current['action']['api'] == 'action_reference'
        assert current['action']['actionId'] == 'system_background_process'
        assert current['lastExecution']['processJobId'] == 'job_child'
    finally:
        await rt.close()


@async_test
async def test_message_and_broadcast_keep_legacy_targets_and_schedule(tmp_path):
    rt = await _runtime(tmp_path)
    try:
        message = rt.create_legacy('session-message', {
            'targetSessionId': 'ses_one', 'text': 'hello',
            'schedule': {'type': 'once', 'delaySeconds': 3600},
            'description': 'legacy message',
        }, {'type': 'user'})
        broadcast = rt.create_legacy('session-broadcast', {
            'targetSessionIds': ['ses_one', 'ses_two'], 'text': 'broadcast',
            'schedule': {'type': 'weekly', 'weekday': 1, 'time': '09:30', 'timezone': 'UTC'},
        }, {'type': 'user'})
        assert message['jobId'] != broadcast['jobId']
        assert rt.get(message['jobId'])['kind'] == 'session-message'
        assert rt.get(message['jobId'])['config']['actionId'] == 'system_session_message'
        assert rt.get(broadcast['jobId'])['targetSessionIds'] == ['ses_one', 'ses_two']
        assert rt.get(broadcast['jobId'])['config']['actionId'] == 'system_session_broadcast'
        from packages.jobs.legacy_compat import legacy_view
        assert legacy_view(rt.get(broadcast['jobId']))['schedule']['type'] == 'weekly'
        assert isinstance(rt.get(broadcast['jobId'])['schedule'], list)
    finally:
        await rt.close()


@async_test
async def test_unknown_system_action_is_not_replayed_by_new_runtime(tmp_path, monkeypatch):
    rt = await _runtime(tmp_path)
    async def uncertain(*args, **kwargs):
        return {'status': 'uncertain'}
    monkeypatch.setattr(system_actions, 'execute', uncertain)
    try:
        job = rt.create_legacy('background-process', {
            'name': 'unknown legacy', 'targetSessionId': 'ses_target',
            'argv': ['python'], 'cwd': str(tmp_path),
        }, {'type': 'user'})
        await rt.launch(job['jobId'], expected=1, authority={'type': 'user'})
        await asyncio.gather(*rt.tasks.values())
        current = rt.get(job['jobId'])
        assert current['lastExecution']['status'] == 'uncertain'
        assert current['enabled'] is False
        await rt.close()
        fresh = Runtime(tmp_path / 'jobs', rt.actions, None)
        await fresh.start()
        try:
            assert fresh.get(job['jobId'])['attempts'] == current['attempts']
            with pytest.raises(Exception):
                await fresh.launch(job['jobId'], expected=current['configRevision'], authority={'type': 'user'})
        finally:
            await fresh.close()
    finally:
        if not rt.closed:
            await rt.close()


@async_test
async def test_legacy_reconciler_does_not_own_action_admission_or_unknown_result(tmp_path, monkeypatch):
    from packages.core import background_jobs
    rt = await _runtime(tmp_path)
    monkeypatch.setenv('PAN_BACKGROUND_JOBS_DIR', str(rt.root))
    try:
        job = rt.create_legacy('background-process', {
            'name': 'admitted Action', 'targetSessionId': 'ses_target',
            'argv': ['python'], 'cwd': str(tmp_path),
        })
        # An admitted Action has no Runner until its system effect starts.
        background_jobs._update(job['jobId'], {'status': 'running'}, registry_root=rt.root)
        assert background_jobs.reconcile_running(rt.root) == 0
        assert rt.get(job['jobId'])['status'] == 'running'
        background_jobs._update(job['jobId'], {'status': 'uncertain', 'enabled': False,
            'lastExecution': {'id': 'interrupted', 'status': 'uncertain'}}, registry_root=rt.root)
        # A late detached process receipt is evidence; it cannot overwrite the
        # recovered Action result or make its effect eligible for replay.
        background_jobs.runner_update(job['jobId'], status='completed', exitCode=0)
        current = rt.get(job['jobId'])
        assert current['status'] == current['lastExecution']['status'] == 'uncertain'
        assert current['processOutcome'] == {'status': 'completed', 'exitCode': 0}
        assert not current['enabled']
    finally:
        await rt.close()
