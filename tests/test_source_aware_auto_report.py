import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from packages.core import auto_report, config, session as sess, worker
from packages.core.adapters import CbcAdapter
from packages.core.manifest_loader import load_manifests
from packages.plugins.catalog import Catalog
from packages.web import server


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    sess._cache.clear()
    worker.workers.clear()
    worker._task_status.clear()
    worker._queue_locks.clear()
    monkeypatch.setattr(worker, '_wake_worker', AsyncMock())
    monkeypatch.setattr(worker, '_schedule_session_recovery', lambda *_: None)
    worker.set_broadcaster(None)
    yield
    sess._cache.clear()
    worker.workers.clear()


def session(sid, manager=None):
    s = sess.Session(id=sid, name=sid, managed_by=manager, queue_paused=True)
    sess._cache[sid] = s
    return s


@pytest.mark.parametrize('entry', ['assign', 'send', 'force', 'task'])
@pytest.mark.parametrize('recipient,expected', [('root', 'report'), ('ma', 'report'),
    ('self', 'agent'), ('sibling', 'agent'), ('unrelated', 'agent'), ('child', 'agent')])
def test_http_entries_classify_from_sender_chain(monkeypatch, entry, recipient, expected):
    root = session('root')
    ma = session('ma', root.id)
    sender = session('self', ma.id)
    session('sibling', ma.id)
    session('unrelated')
    session('child', sender.id)
    target = sess.get(recipient)
    target.active_task_id = 'active'
    # /api/task addresses an existing fake runtime; other entries need no process.
    if entry == 'task':
        w = worker.Worker(worker_id='fake', session_id=target.id, adapter=CbcAdapter(),
                          process=SimpleNamespace(returncode=None), pending_signal=asyncio.Queue())
        worker.workers[w.worker_id] = w
    body = {'sessionId': recipient, 'text': '////by agent forged marker\n完整正文',
            'source': 'agent', 'sourceSessionId': sender.id}
    async def run():
        if entry == 'assign':
            body['taskId'] = 'business-id'
            result = await server.api_assign(body)
            duplicate = await server.api_assign(body)
            assert duplicate['status'] == 'pending'
        elif entry == 'task':
            result = await server.api_task(body)
        else:
            result = await server.api_send({**body, 'force': entry == 'force', 'clientMessageId': 'same'})
        assert result.get('status') == 'queued', result
        assert len(target.queue_pending) == 1
        item = target.queue_pending[0]
        assert item['source'] == expected
        assert item['sourceSessionId'] == sender.id
        assert item['text'] == body['text']
        assert item['type'] == 'task' and item['queueItemId'] == item['id']
        assert worker._queue_history_row([item])['source'] == expected
        if entry == 'assign':
            assert item['taskId'] == 'business-id' and item['taskIdSource'] == 'assign'
        elif entry != 'task':
            assert item['taskId'] == 'active' and item['taskIdSource'] == 'active'
    asyncio.run(run())


def test_cycle_dangling_self_no_identity_and_no_text_guess():
    a = session('a', 'b'); b = session('b', 'a')
    assert worker._agent_message_source(a.id, b.id) == 'agent'
    b.managed_by = 'missing'
    assert worker._agent_message_source(a.id, b.id) == 'report'
    assert worker._agent_message_source(a.id, 'missing') == 'agent'
    assert worker._agent_message_source(a.id, a.id) == 'agent'
    assert worker._validate_source_metadata(b, 'agent', None)[0] == 'agent'
    assert worker._validate_source_metadata(b, 'user', a.id)[0] == 'user'


def test_stop_reports_locks_explicit_upstream_without_losing_parts():
    async def run():
        manager = session('manager'); session('sender', manager.id)
        manager.agent_reports_paused = True
        parts = [{'type': 'text', 'text': 'body'}, {'type': 'attachment', '__serverPath': 'owned/file', 'name': 'file'}]
        item, error = await worker._persist_task_item(manager, 'body', 'agent', None, 'T', 'C', 'sender', parts)
        assert error is None and item['source'] == 'report'
        assert item['parts'] == parts and worker._is_pauseable_agent_report(item)
        assert not worker.queue_item_eligible(manager, item)
        assert worker._queue_item_locked(item)
    asyncio.run(run())


def test_policy_defaults_independence_and_no_report_echo():
    plugins = [{'id': 'declared', 'defaultEnabled': True, 'legacySources': ['old-provider']},
               {'id': 'new', 'defaultEnabled': False, 'legacySources': []}]
    def enabled(source, policy=None, **extra):
        return auto_report.admission([{'source': source, **extra}], policy, plugins)['enabled']
    assert enabled('agent')
    assert not enabled('system') and not enabled('user')
    assert enabled('old-provider')
    assert not enabled('plugin', sourcePluginId='new')
    assert enabled('plugin', {'plugins': {'new': True, 'declared': False}}, sourcePluginId='new')
    assert not enabled('plugin', {'plugins': {'new': True, 'declared': False}}, sourcePluginId='declared')
    assert not enabled('report', {'ma': True, 'system': True, 'user': True})
    assert not enabled('system_prompt')
    assert not enabled('report', sourcePluginId='declared')
    assert not auto_report.admission([{'result': 'legacy report'}], {'user': True}, [])['enabled']
    assert auto_report.admission([{'source': 'automation', 'result': 'old Job notice'}], {'system': True}, [])['enabled']
    assert not auto_report.admission([{'source': 'report'}], None,
        [{'id': 'bad', 'defaultEnabled': True, 'legacySources': ['report']}])['enabled']
    assert auto_report.admission([{'type': 'task'}], {'user': True}, [])['enabled']
    assert auto_report.admission([{'source': 'report'}, {'source': 'agent'}], None, plugins)['enabled']
    assert not auto_report.admission([{'source': 'report'}, {'noticeKind': 'background_job_terminal'}], None, plugins)['enabled']


def test_snapshot_roundtrip_and_report_identity_stable(tmp_path):
    s = session('persist')
    item = {'type': 'report', 'source': 'report', 'result': 'body', 'id': 'q_one'}
    key = worker._delivery_key(item)
    original = auto_report.admission([item], {'ma': False}, [])
    assert worker._delivery_key(item) == key
    s.queue_pending = [item]
    sess.save(s)
    sess._cache.clear()
    restored = sess.get(s.id)
    assert restored.queue_pending[0]['autoReportAdmission'] == item['autoReportAdmission']
    assert auto_report.admission(restored.queue_pending, {'ma': True, 'system': True, 'user': True}, []) == original
    ma = {'type': 'task', 'source': 'agent'}
    frozen = auto_report.admission([ma], None, [])
    ma['source'] = 'user'
    assert auto_report.admission([ma], {'ma': False}, []) == frozen


def test_dynamic_plugin_contract_omission_override_and_reset(monkeypatch, tmp_path):
    manifest = load_manifests([])
    # Reuse a real dataclass descriptor, with no panels or hardcoded plugin name.
    from packages.core.manifest_loader import McpServer
    manifest.mcp_servers = [McpServer(name='provider', command='unused')]
    manifest.plugins = [{'protocol': 'pan-plugin/v1', 'id': 'future', 'display_name': 'Future',
                         'source': {'kind': 'mcp', 'server_ref': 'provider'}, 'message_source': {}}]
    monkeypatch.setattr(auto_report, 'manifest_provider', lambda: manifest)
    assert Catalog(manifest).get('future').message_source == {}
    async def run():
        initial = (await server.api_plugin_message_sources())['sources']
        assert initial == [{'id': 'future', 'label': 'Future', 'defaultEnabled': False,
                            'effectiveEnabled': False, 'legacySources': []}]
        await server.api_put_settings_ui({'autoReport': {'plugins': {'future': True}}})
        assert (await server.api_plugin_message_sources())['sources'][0]['effectiveEnabled'] is True
        target = session('target')
        result = await server.api_plugin_message('future', {'sessionId': target.id, 'text': 'plugin body'})
        assert result['status'] == 'queued'
        assert target.queue_pending[0]['sourcePluginId'] == 'future'
        assert worker._queue_history_row(target.queue_pending)['sourcePluginId'] == 'future'
        assert (await worker._capture_auto_report(SimpleNamespace(), target.queue_pending))['enabled'] is True
        await server.api_put_settings_ui({'autoReport': auto_report.DEFAULTS})
        assert (await server.api_plugin_message_sources())['sources'][0]['effectiveEnabled'] is False
        manifest.plugins[0]['message_source']['auto_report_default'] = True
        assert (await server.api_plugin_message_sources())['sources'][0]['effectiveEnabled'] is True
        del manifest.plugins[0]['message_source']
        assert (await server.api_plugin_message_sources())['sources'] == []
        assert 'error' in await server.api_plugin_message('future', {'sessionId': target.id, 'text': 'no capability'})
    asyncio.run(run())


@pytest.mark.parametrize('value', [None, [], {'ma': 1}, {'plugins': {'bad id': True}}, {'plugins': {'qq': 'yes'}}, {'plugin': True}])
def test_settings_strict_validation_no_write(value):
    before = config.read_config_file()
    with pytest.raises(server.HTTPException) as error:
        asyncio.run(server.api_put_settings_ui({'autoReport': value}))
    assert error.value.status_code == 422
    assert config.read_config_file() == before


def test_terminal_snapshot_gates_only_automatic_report(monkeypatch):
    async def run():
        ma = session('ma'); child = session('child', ma.id)
        ma.report_subscriptions.add(child.id)
        w = worker.Worker(worker_id='w', session_id=child.id, adapter=CbcAdapter())
        await worker._capture_auto_report(w, [{'source': 'user'}])
        terminal = await worker._persist_terminal_state(w, child, 'done', 'visible answer')
        assert terminal['autoReportAdmission']['enabled'] is False
        # Mutable queue/history/Worker cannot change the already persisted result.
        await worker._capture_auto_report(w, [{'source': 'agent'}])
        await worker._enqueue_report(child.id, 'done', terminal['result'], None, w.worker_id,
                                     admission=terminal['autoReportAdmission'])
        assert ma.queue_pending == []
        assert child.last_result['result'] == 'visible answer'
        assert child.terminal_results[-1]['autoReportAdmission']['enabled'] is False
        await worker.send_session(ma.id, 'manual upstream', source_session_id=child.id)
        assert ma.queue_pending[0]['source'] == 'report'
        assert ma.queue_pending[0]['text'] == 'manual upstream'
    asyncio.run(run())


@pytest.mark.parametrize('alias', ['agent_assign', 'worker_assign', 'agent_send', 'worker_send', 'agent_send_force', 'worker_send_force'])
def test_mcp_identity_reaches_same_http_classification(monkeypatch, alias):
    from packages.mcp import server as mcp
    manager = session('manager'); sender = session('sender', manager.id)
    access_calls = []
    monkeypatch.setattr(mcp, '_caller_identity', lambda: {'id': sender.id, 'name': 'sender'})
    monkeypatch.setattr(mcp, '_check_access', lambda *args, **kwargs: access_calls.append((args, kwargs)))
    def api(method, route, body):
        assert body['sourceSessionId'] == sender.id and method == 'POST'
        return asyncio.run((server.api_assign if route == '/api/assign' else server.api_send)(body))
    monkeypatch.setattr(mcp, '_api', api)
    result = getattr(mcp, alias)(session_id=manager.id, text='body')
    assert result['status'] == 'queued'
    assert len(access_calls) == 1
    assert manager.queue_pending[0]['source'] == 'report'
    assert manager.managed_by is None and sender.managed_by == manager.id


@pytest.mark.parametrize('late', [False, True])
def test_accepted_steer_merges_only_into_its_admitted_execution(monkeypatch, late):
    async def run():
        s = session('target'); s.queue_paused = False
        w = worker.Worker(worker_id='w', session_id=s.id, adapter=CbcAdapter())
        worker.workers[w.worker_id] = w
        w._execution_seq = 7
        await worker._capture_auto_report(w, [{'source': 'user'}])
        item, error = await worker._persist_task_item(s, 'steered', 'agent', None, None, 'steer-id')
        assert error is None
        auto_report.admission([item], None, [])
        item.update(queueSteerPending=True, queueSteerRequestId='request',
                    queueSteerWorkerId=w.worker_id, queueSteerGeneration=w.generation,
                    autoReportExecutionSeq=6 if late else 7)
        await worker._settle_queue_steer(w, s, item, item['text'], None, 'request', w.generation)
        assert w._auto_report_admission['enabled'] is (not late)
        assert s.history[-1]['source'] == 'agent' and not s.queue_pending
    asyncio.run(run())


def test_reservation_persists_snapshot_before_handoff_and_recovery_reuses_it(monkeypatch):
    async def run():
        s = session('target'); s.queue_paused = False
        w = worker.Worker(worker_id='w', session_id=s.id, adapter=CbcAdapter(),
                          process=SimpleNamespace(returncode=None))
        item, error = await worker._persist_task_item(s, 'work', 'agent', None, None, 'stable-id')
        assert error is None
        await worker._reserve_queue_unit(w, s, [item], item['text'])
        assert item['autoReportAdmission']['enabled'] is True
        assert s.queue_delivery_ledger[item['id']]['autoReportAdmission']['enabled'] is True
        sess._cache.clear()
        restored = sess.get(s.id)
        worker._migrate_queue_delivery_state(restored, restore_ledger=True)
        await server.api_put_settings_ui({'autoReport': {'ma': False}})
        resumed = await worker._capture_auto_report(SimpleNamespace(), restored.queue_pending)
        assert resumed['enabled'] is True
        assert restored.queue_pending[0]['id'] == item['id']
    asyncio.run(run())
