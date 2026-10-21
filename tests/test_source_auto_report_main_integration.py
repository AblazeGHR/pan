"""Cross-contract coverage on main: explicit reports, admission, IO and Force."""
import asyncio
import copy
import json
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from packages.core import auto_report, config, queue_locks, self_queue, session as sess, worker
from packages.plugins.lifecycle import coordinator, session_claim
from packages.web import server
from tests.test_report_reads import isolated, setup, runtime, report, assert_private


@pytest.mark.parametrize('formal', [False, True])
@pytest.mark.parametrize('disposition', ['accepted', 'unknown', 'rejected'])
def test_upstream_report_consume_keeps_original_task_body_parts_and_identity(tmp_path, monkeypatch, formal, disposition):
    async def run():
        s, a, b, _ = setup()
        s.queue_pending = []
        child = sess.create('child')
        child.managed_by = s.id
        sess.save(child)
        attachment = tmp_path / 'attachment.txt'
        attachment.write_bytes(b'owned attachment')
        parts = [{'type': 'text', 'text': 'UPSTREAM_SECRET'},
                 {'type': 'attachment', 'displayName': 'attachment.txt', '__serverPath': str(attachment)}]
        w, stdin = runtime(s, disposition=disposition)
        w._execution_seq = 17
        await worker._capture_auto_report(w, [{'source': 'user'}])
        task, error = await worker._persist_task_item(s, 'UPSTREAM_SECRET fallback', 'agent', None,
            'FORMAL' if formal else None, 'upstream-original', child.id, parts,
            idempotent_task_id=formal, auto_force=False)
        assert error is None
        original = copy.deepcopy(task)
        s.queue_pending += [a, b]
        sess.save(s)
        before = await self_queue.read(s.id)
        assert_private(before)
        assert 'UPSTREAM_SECRET' not in json.dumps(before) and 'attachment.txt' not in json.dumps(before)
        assert worker.classify_auto_force_source(task, s) == (None if formal else 'report')
        result = await self_queue.report_consume(s.id)
        assert result['queueItemIds'] == [task['id']]
        assert result['status'] == disposition
        assert_private(result)
        assert stdin.write.call_count == 1
        wire = json.loads(stdin.write.call_args.args[0])
        assert wire['text'] == worker.project_message_parts(parts, original['text'])
        assert 'SECRET-a' not in wire['text'] and 'result:' not in wire['text']
        # Reports behind the explicit task remain untouched, never folded into it.
        assert all(member in s.queue_pending for member in [a, b])
        stored = worker._ledger_record(s, task['id']) if disposition == 'accepted' else task
        for key in ['id', 'queueItemId', 'type', 'source', 'sourceSessionId', 'taskId', 'taskIdSource', 'parts', 'text']:
            assert stored.get(key) == original.get(key)
        assert (await self_queue.report_consume(s.id, task['id']))['status'] == disposition
        assert stdin.write.call_count == 1
        assert w._auto_report_admission['enabled'] is False
        if disposition == 'accepted':
            assert s.history[-1]['content'] == original['text']
            assert s.history[-1]['source'] == 'report'
            if formal:
                assert worker._durable_task_id_seen(s, 'FORMAL')
    asyncio.run(run())


def test_invalid_mixed_frozen_unit_cannot_drop_explicit_task_body():
    s, a, b, _ = setup()
    task = {'id': 't', 'queueItemId': 't', 'type': 'task', 'source': 'report',
            'explicitReport': True, 'text': 'must not disappear'}
    s.queue_pending = [a, task, b]
    for row in s.queue_pending:
        row['reportConsumeIds'] = ['a', 't', 'b']
    assert worker._report_consume_unit(s, a) is None


def test_upstream_plugin_and_automatic_reports_stay_illegal_until_explicit_wake(monkeypatch):
    from tests.test_illegal_queue_wake import incident
    async def run():
        s, batch = incident()
        child = sess.create('child'); child.managed_by = s.id; sess.save(child)
        monkeypatch.setattr(worker, '_schedule_session_recovery', lambda *_: None)
        monkeypatch.setattr(worker, '_wake_worker', AsyncMock())
        monkeypatch.setattr(auto_report, 'plugin_sources', lambda: [
            {'id': 'future', 'label': 'Future', 'defaultEnabled': True, 'legacySources': []}])
        await server.api_put_settings_ui({'autoForceSend': {'system': True, 'ma': True, 'report': True}})
        s.report_subscriptions.add(child.id)
        await worker.send_session(s.id, 'UPSTREAM', source_session_id=child.id, force=True, client_message_id='one')
        await worker.enqueue_plugin_message('future', s.id, 'PLUGIN', client_message_id='two')
        await worker._enqueue_report(child.id, 'done', 'AUTO', None, None)
        await worker.wait_auto_force(s.id)
        assert len(s.queue_pending) == 3
        originals = [row['id'] for row in s.queue_pending]
        assert all(queue_locks.source(row) == 'illegal' for row in s.queue_pending)
        assert set(s.illegal_batches[batch]['messages']) == set(originals)
        assert not worker._select_queue_unit(s)
        assert (await self_queue.read(s.id))['items'] == []
        assert (await self_queue.report_consume(s.id)).get('error') == 'item_unavailable'
        result = await worker.wake_session(s.id, client_message_id='wake-once')
        assert result['status'] == 'queued'
        assert s.queue_pending[0]['wakeMessage'] and queue_locks.source(s.queue_pending[0]) is None
        assert [row['id'] for row in s.queue_pending[1:]] == originals
        assert all(queue_locks.source(row) == 'illegal' for row in s.queue_pending[1:])
    asyncio.run(run())


def test_registry_io_is_off_loop_and_cancellation_keeps_session_claim(monkeypatch):
    async def run():
        s, _, _, _ = setup()
        entered, release = threading.Event(), threading.Event()
        loop_thread = threading.get_ident()
        def sources():
            assert threading.get_ident() != loop_thread
            entered.set()
            assert release.wait(5)
            return []
        monkeypatch.setattr(auto_report, 'plugin_sources', sources)
        async def admission():
            async with session_claim(s):
                await auto_report.context()
        task = asyncio.create_task(admission())
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done() and coordinator().active
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not coordinator().active
    asyncio.run(run())


def test_settings_report_force_independence_and_failed_write(monkeypatch):
    async def run():
        force = {'system': True, 'ma': False, 'report': True}
        await server.api_put_settings_ui({'autoForceSend': force})
        await server.api_put_settings_ui({'autoReport': {'ma': False, 'plugins': {'qq': False}}})
        assert (await server.api_get_settings_ui())['autoForceSend'] == force
        await server.api_put_settings_ui({'autoReport': auto_report.DEFAULTS})
        before = config.CONFIG_FILE.read_bytes()
        monkeypatch.setattr(server, 'save_config', lambda *_: (_ for _ in ()).throw(OSError('disk failed')))
        with pytest.raises(OSError):
            await server.api_put_settings_ui({'autoReport': {'ma': False}})
        assert config.CONFIG_FILE.read_bytes() == before
        values = await server.api_get_settings_ui()
        assert values['autoReport'] == auto_report.DEFAULTS and values['autoForceSend'] == force
    asyncio.run(run())


def test_failed_reservation_does_not_publish_admission_on_item_or_worker(monkeypatch):
    async def run():
        s, _, _, _ = setup()
        s.queue_pending = []
        item, error = await worker._persist_task_item(s, 'work', 'agent', None, None, 'one', auto_force=False)
        assert error is None
        w, _ = runtime(s)
        previous = {'version': 1, 'inputs': [], 'enabled': False}
        w._auto_report_admission = previous
        saved = sess._path(s.id).read_bytes()
        monkeypatch.setattr(worker, '_save_receipt', AsyncMock(side_effect=OSError('disk failed')))
        assert not await worker._reserve_queue_unit(w, s, [item], item['text'])
        assert 'autoReportAdmission' not in item
        assert 'autoReportAdmission' not in s.queue_delivery_ledger[item['id']]
        assert w._auto_report_admission is previous
        assert sess._path(s.id).read_bytes() == saved
    asyncio.run(run())


def test_infrastructure_error_report_bypasses_answer_policy(monkeypatch):
    async def run():
        manager = sess.create('manager')
        child = sess.create('child'); child.managed_by = manager.id
        manager.report_subscriptions.add(child.id)
        sess.save(child); sess.save(manager)
        w, _ = runtime(child)
        w._auto_report_admission = {'version': 1, 'inputs': [], 'enabled': False}
        monkeypatch.setattr(worker, '_wake_worker', AsyncMock())
        await worker._finish_task_error(w, child, 'infrastructure handoff failed')
        assert manager.queue_pending[0]['status'] == 'error'
        assert manager.queue_pending[0]['result'] == 'infrastructure handoff failed'
        assert child.last_result['status'] == 'error'
    asyncio.run(run())
