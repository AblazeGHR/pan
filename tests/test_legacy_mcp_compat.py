"""Phase 4a legacy MCP creation is backed by one Action Job record."""
import asyncio

import pytest
from fastapi import FastAPI, HTTPException
from starlette.requests import Request

from packages.actions.store import Store
from packages.jobs.action_runtime import Runtime
from packages.jobs import system_actions
from tests.test_automation_core import async_test, until
from tests.test_automation_job_links import setup as links_setup, close as links_close
from tests.test_authorization_permissions import full_permissions
from tests.test_job_automatic_recovery import env  # noqa: F401


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


def test_trusted_legacy_http_entry_is_retired_and_creates_no_job(env):
    import copy
    # The canonical UserAuth boundary precedes the retained 410 response.
    before = env.service.list('job', actor={'type': 'user', 'id': 'local'})
    path = '/api/legacy/jobs'
    denied = env.client.post(path, json={'kind': 'background-process'},
        headers={'Authorization': 'Bearer invalid'})
    assert denied.status_code == 401
    retired = env.client.post(path, json={'kind': 'background-process'})
    assert retired.status_code == 410 and retired.json()['detail']['code'] == 'retired'
    assert env.service.list('job', actor={'type': 'user', 'id': 'local'}) == before
    assert before['items'] == []


def test_background_mcp_shape_uses_system_action_and_same_job_id(env):
    from tests.test_job_automatic_recovery import create, job_definition, drive_until
    from packages.jobs.independent_executor import ExecutionStore, effect_id
    marker = env.tmp / 'one-process.txt'
    code = f"from pathlib import Path; Path({str(marker)!r}).write_text('once')"
    jid, rid = create(env, job_definition(env, {'automatic': False}, code))
    asyncio.run(drive_until(env, jid, rid, 'completed'))
    original = env.service.final.get(jid)
    assert marker.read_text(encoding='utf-8') == 'once'
    assert len(original['runs']) == len(original['attempts']) == len(original['effects']) == 1
    assert original['runs'][rid]['jobId'] == jid
    effect = original['effects'][0]
    document = ExecutionStore(env.root / 'executions').get(effect_id(effect['effectKey']))
    assert document['jobId'] == jid and document['effectKey'] == effect['effectKey']
    assert document['attempt']['attemptId'] == effect['attemptId']
    assert [j['id'] for j in env.service.list('job', actor={'type':'user','id':'local'})['items']] == [jid]
    asyncio.run(env.service.dispatch_once())
    assert env.service.final.get(jid) == original


@async_test
async def test_message_and_broadcast_keep_legacy_targets_and_schedule(tmp_path):
    with pytest.raises(PermissionError, match='retired'):
        await _runtime(tmp_path)


def test_unknown_system_action_is_not_replayed_by_new_runtime(env, monkeypatch):
    import copy
    from tests.test_job_automatic_recovery import seed, observe
    from packages.jobs.final_service import FinalService
    from packages.jobs.final_contracts import ContractError
    row = seed(env, monkeypatch)
    row.doc['result'] = {'effectKey': row.key, 'outcome': 'unknown',
        'receipt': {'code': 'original-unconfirmed'}, 'cleanupProof': {'confirmed': False}}
    observe(env)
    original = copy.deepcopy(env.service.final.get(row.jid))
    assert original['runs'][row.rid]['state'] == 'uncertain'
    asyncio.run(env.service.close())
    fresh = FinalService(env.root, env.auth)
    try:
        fresh.recover()
        assert fresh.final.get(row.jid) == original
        with pytest.raises(ContractError) as rejected:
            fresh.control_run(row.rid, {'operation':'retry', 'requestKey':'unknown-no-replay',
                'expectedStateRevision':original['runs'][row.rid]['stateRevision']},
                actor={'type':'user','id':'local'})
        assert rejected.value.code == 'reconciliation_required'
        for _ in range(2): asyncio.run(fresh.dispatch_once())
        assert fresh.final.get(row.jid) == original and not row.original.calls
    finally:
        asyncio.run(fresh.close())


@async_test
async def test_legacy_reconciler_does_not_own_action_admission_or_unknown_result(tmp_path, monkeypatch):
    with pytest.raises(PermissionError, match='retired'):
        await _runtime(tmp_path)
