"""Existing-target handoff: real store/queue and HTTP boundary, isolated profile."""
import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient

from packages.core import session as sess, worker
from packages.mcp import server as mcp
from packages.web import server


@pytest.fixture(autouse=True)
def no_provider_processes(monkeypatch):
    monkeypatch.setattr(worker, '_wake_worker', AsyncMock())


def pair():
    a = sess.create('source', adapter='codex', original_prompt='SOURCE RULES')
    b = sess.create('existing', adapter='codex', original_prompt='B original',
                    handoff_prompt='B earlier brief', cli_session_id='B-cli',
                    history=[{'role': 'user', 'content': 'B context'}])
    return a, b


def run(a, b, **kw):
    return sess.handoff_session(a.id, 'new brief', target_session_id=b.id, **kw)


@pytest.mark.parametrize('override,text,expected', [(False, None, 'SOURCE RULES'),
    (True, 'explicit original', 'explicit original'), (True, '', '')])
def test_preserves_actual_prompt_context_and_cold_load(override, text, expected):
    a, b = pair()
    effective = b.system_prompt
    assert effective != b.original_prompt and effective
    context = copy.deepcopy(b.history)
    config = copy.deepcopy(b.adapter_config)
    b.queue_paused = True
    result = run(a, b, override_original_prompt=override, original_prompt=text)
    assert result == (a, b)
    assert b.original_prompt == expected and b.handoff_prompt == 'new brief'
    assert b.system_prompt == effective and b.history == context
    assert b.adapter_config == config and b.cli_session_id == 'B-cli'
    assert b.name == 'existing' and b.queue_paused
    assert len(b.queue_pending) == 1
    item = b.queue_pending[0]
    assert item['text'] == 'new brief' and item['sourceSessionId'] == a.id
    assert a.handoff_receipt['queueItemId'] == item['id']
    assert not worker.queue_item_eligible(b, item)
    sess._cache.clear()
    cold = sess.get(b.id)
    assert cold.system_prompt == effective and cold.original_prompt == expected
    assert cold.cli_session_id == 'B-cli' and cold.history == context
    # Every worker construction reads this compatibility view. Metadata edits
    # after cold restart still cannot revive the create-mode prompt composer.
    cold.original_prompt = 'later metadata'
    cold.handoff_prompt = 'later brief'
    assert cold.system_prompt == effective
    # Exercise the actual stream spawn argument builder for resume and newspawn.
    fake_adapter = SimpleNamespace(supports_spawn_system_prompt=True)
    assert worker._spawn_system_prompt_args(fake_adapter, cold, False) is None
    cold.cli_session_id = None
    assert worker._spawn_system_prompt_args(fake_adapter, cold, False) == ['--system-prompt', effective]


def test_merge_preserves_bindings_managed_subscriptions_and_source_task(monkeypatch):
    a, b = pair()
    ca, cb = sess.create('a-child'), sess.create('b-child')
    a.managed, b.managed = [ca.id], [cb.id]
    ca.managed_by, cb.managed_by = a.id, b.id
    a.report_subscriptions, b.report_subscriptions = {ca.id}, {cb.id}
    a.qq_subscriptions, b.qq_subscriptions = {'user:a'}, {'user:b'}
    a.wechat_subscriptions, b.wechat_subscriptions = {'user:wa'}, {'user:wb'}
    a.queue_pending = [{'id': 'source-queued', 'text': 'keep', 'type': 'task'}]
    a.queue_paused = True
    aw = SimpleNamespace(status='running', generation=42)
    monkeypatch.setattr(worker, 'find_alive_worker_by_session', lambda sid: aw if sid == a.id else None)
    assert run(a, b) == (a, b)
    assert aw.status == 'running' and aw.generation == 42
    assert a.queue_paused and a.queue_pending[0]['id'] == 'source-queued'
    assert b.managed == [cb.id, ca.id, a.id] and ca.managed_by == b.id
    assert b.report_subscriptions == {cb.id, ca.id, a.id}
    assert b.qq_subscriptions == {'user:a', 'user:b'}
    assert b.wechat_subscriptions == {'user:wa', 'user:wb'}
    # A terminal report uses the new graph, without replaying handoff input.
    monkeypatch.setattr(worker, '_bcast', AsyncMock())
    asyncio.run(worker._enqueue_report(a.id, 'done', 'final report', 'task-A', 'worker-A'))
    assert len([i for i in b.queue_pending if i.get('text') == 'new brief']) == 1
    assert any(i.get('sessionId') == a.id and i.get('result') == 'final report' for i in b.queue_pending)


@pytest.mark.parametrize('conflict', ['self', 'missing', 'copy', 'adapter', 'manager', 'ancestor', 'workspace', 'qq', 'badgraph', 'readonly'])
def test_preflight_rejects_without_any_partial_modification(conflict):
    a, b = pair()
    kw = {}
    target = b.id
    if conflict == 'self': target = a.id
    if conflict == 'missing': target = 'ses_nonexistent'
    if conflict == 'copy': kw['copy_settings'] = True
    if conflict == 'adapter': kw['adapter'] = 'codex'
    if conflict == 'manager':
        p = sess.create('parent'); p.managed = [a.id]; a.managed_by = p.id
    if conflict == 'ancestor': a.managed = [b.id]; b.managed_by = a.id
    if conflict == 'workspace': a.workspace_ids = ['ws_other']
    if conflict == 'qq':
        c = sess.create('other'); c.qq_subscriptions = {'user:x'}; a.qq_subscriptions = {'user:x'}
    if conflict == 'badgraph': a.managed = ['missing-child']
    if conflict == 'readonly': b.readonly_session = True
    before = {s.id: copy.deepcopy(s.to_dict()) for s in sess.list_all()}
    result = sess.handoff_session(a.id, 'brief', target_session_id=target, **kw)
    assert isinstance(result, str), result
    assert {s.id: s.to_dict() for s in sess.list_all()} == before


@pytest.mark.parametrize('illegal', [False, True])
def test_rollback_after_write_failure(monkeypatch, illegal):
    a, b = pair()
    if illegal:
        b.execution_operations = {'unfinished': {'id': 'unfinished', 'state': 'illegal'}}
        b.illegal_batches = {'unfinished': {'id': 'unfinished', 'messages': [], 'createdAt': 1, 'recovered': False}}
    before = {s.id: copy.deepcopy(s.to_dict()) for s in (a, b)}
    save = sess.save
    count = 0
    def fail_once(s):
        nonlocal count
        count += 1
        if count == 2: raise OSError('injected write failure')
        save(s)
    monkeypatch.setattr(sess, 'save', fail_once)
    assert 'persistence failed' in run(a, b)
    assert not b.queue_pending and a.handoff_receipt is None
    for s in (a, b):
        # updated_at is an observation of successful rollback persistence.
        before[s.id]['updated_at'] = s.updated_at
        before[s.id]['summary_projection']['revision'] = s.summary_projection['revision']
        before[s.id]['summary_projection']['updated_at'] = s.summary_projection['updated_at']
        assert s.to_dict() == before[s.id]
    sess._cache.clear()
    assert sess.get(b.id).original_prompt == 'B original'
    assert sess.get(a.id).managed_by is None


def test_duplicate_and_unknown_do_not_reenqueue():
    a, b = pair()
    assert run(a, b) == (a, b)
    item = b.queue_pending.pop()
    item['deliveryState'] = 'writing'
    b.queue_delivery_ledger[item['id']]['deliveryState'] = 'writing'
    assert run(a, b) == (a, b)
    assert b.queue_pending == []
    assert isinstance(sess.handoff_session(a.id, 'different', target_session_id=b.id), str)
    assert isinstance(sess.handoff_session(a.id, 'new brief'), str)
    assert not b.queue_pending


@pytest.mark.parametrize('value', [None, '', '  \n '])
def test_frozen_empty_prompt_does_not_gain_metadata(value):
    a, b = pair()
    b.system_prompt = value
    assert run(a, b) == (a, b)
    sess._cache.clear()
    cold = sess.get(b.id)
    assert cold.original_prompt == 'SOURCE RULES'
    assert cold.system_prompt == value


def test_illegal_target_message_keeps_gate_and_original_queue():
    from packages.core import legal_offline
    a, b = pair()
    b.legal_offline = False
    b.offline_run = {'id': 'unfinished', 'state': 'illegal'}
    b.execution_operations = {'unfinished': b.offline_run}
    b.illegal_batches = {'unfinished': {'id': 'unfinished', 'messages': [], 'createdAt': 1, 'recovered': False}}
    assert legal_offline.is_illegal(b)
    assert run(a, b) == (a, b)
    assert not worker.queue_item_eligible(b, b.queue_pending[0])
    assert b.legal_offline is False and b.offline_run['id'] == 'unfinished'


def test_shared_parent_and_concurrent_duplicate():
    from concurrent.futures import ThreadPoolExecutor
    a, b = pair()
    parent = sess.create('parent')
    parent.managed = [a.id, b.id]
    parent.report_subscriptions = {a.id, b.id}
    a.managed_by = b.managed_by = parent.id
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(a, b), range(2)))
    assert results == [(a, b), (a, b)]
    assert len(b.queue_pending) == 1
    assert parent.managed == [b.id] and parent.report_subscriptions == {b.id}


def test_explicit_settings_preserve_cli_and_validate(monkeypatch):
    a, b = pair()
    assert run(a, b, permission_mode='bypass') == (a, b)
    assert b.permission_mode == 'bypass' and b.cli_session_id == 'B-cli'
    a2 = sess.create('a2', adapter='codex')
    result = run(a2, b, model='invalid-model')
    assert isinstance(result, str) and 'model' in result
    monkeypatch.setattr(worker, 'find_alive_worker_by_session', lambda sid: object())
    assert 'live/native' in run(a2, b, permission_mode='bypass')


def test_http_existing_wiring_and_original_receipt(monkeypatch):
    a, b = pair()
    monkeypatch.setattr(server, 'broadcast', AsyncMock())
    monkeypatch.setattr(server.background_jobs, 'retarget_session_jobs', Mock(return_value={'errors': []}))
    client = TestClient(server.app)
    body = {'handoffPrompt': 'new brief', 'targetSessionId': b.id}
    first = client.post(f'/api/sessions/{a.id}/handoff', json=body).json()
    assert first['ok'] and first['session']['id'] == b.id, first
    second = client.post(f'/api/sessions/{a.id}/handoff', json=body).json()
    assert second['briefDelivery']['queueItemId'] == first['briefDelivery']['queueItemId']
    assert len(b.queue_pending) == 1
    assert first['session']['systemPrompt'] != first['session']['originalPrompt']


def test_http_ambiguous_existing_settings_do_not_modify_or_create(monkeypatch):
    a, b = pair()
    before = copy.deepcopy([a.to_dict(), b.to_dict()])
    client = TestClient(server.app)
    for extra in ({'copySettings': None}, {'copySettings': 'false'}, {'targetSessionId': None},
                  {'copySettings': True}, {'unexpected': 'value'}):
        result = client.post(f'/api/sessions/{a.id}/handoff', json={
            'targetSessionId': b.id, 'handoffPrompt': 'brief', **extra}).json()
        assert 'error' in result
        assert [a.to_dict(), b.to_dict()] == before
        assert len(sess.list_all()) == 2


def test_mcp_checks_both_ids_without_claim_and_preserves_omission(monkeypatch):
    calls = []
    monkeypatch.setattr(mcp, '_caller_identity', lambda: {'id': 'caller'})
    monkeypatch.setattr(mcp, '_check_access', lambda sid, **kw: calls.append((sid, kw)))
    api = Mock(return_value={'ok': True})
    monkeypatch.setattr(mcp, '_api', api)
    assert mcp.session_handoff('A', 'brief', target_session_id='B')['ok']
    assert {sid for sid, _ in calls} == {'A', 'B'}
    assert all(not kw.get('claim') for _, kw in calls)
    api.assert_called_once_with('POST', '/api/sessions/A/handoff', {'handoffPrompt': 'brief', 'targetSessionId': 'B'})
    api.reset_mock()
    monkeypatch.setattr(mcp, '_check_access', lambda sid, **kw: {'error': 'denied'} if sid == 'B' else None)
    assert mcp.session_handoff('A', 'brief', target_session_id='B')['error'] == 'denied'
    api.assert_not_called()


@pytest.mark.parametrize('managed', [['A'], ['B']])
def test_mcp_real_access_gate_denies_either_missing_permission(monkeypatch, managed):
    monkeypatch.setattr(mcp, '_caller_identity', lambda: {'id': 'caller', 'managed': managed,
        'panAccess': {'restrictToManaged': True, 'canClaimUnmanaged': True}})
    api = Mock()
    monkeypatch.setattr(mcp, '_api', api)
    result = mcp.session_handoff('A', 'brief', target_session_id='B')
    assert result['error']['code'] == 'permission_denied'
    api.assert_not_called()  # No source claim before target access failure.


def test_mcp_schema_distinguishes_existing_target_and_omitted_copy():
    schema = next(tool for tool in asyncio.run(mcp.mcp.list_tools()) if tool.name == 'session_handoff')
    assert 'target_session_id' in schema.inputSchema['properties']
    assert schema.inputSchema['properties']['copy_settings']['default'] is None
    assert 'source' in schema.description and 'system_prompt' in schema.description
