"""Report prompt reference: frozen input identity and independent summary policy."""
import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from packages.core import auto_report, config, session as sess, worker
from packages.core.adapters import CbcAdapter
from packages.web import server


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    sess._cache.clear()
    worker.workers.clear()
    worker._task_status.clear()
    worker._queue_locks.clear()
    monkeypatch.setattr(worker, '_wake_worker', _async_noop)
    monkeypatch.setattr(worker, '_schedule_session_recovery', lambda *_: None)
    monkeypatch.setattr(worker, '_schedule_auto_force', lambda *_: None)
    worker.set_broadcaster(None)
    yield
    sess._cache.clear()
    worker.workers.clear()


async def _async_noop(*args, **kwargs):
    return None


def session(sid, manager=None):
    s = sess.Session(id=sid, name=sid, managed_by=manager, queue_paused=True)
    sess._cache[sid] = s
    return s


def runtime_worker(s, wid='w'):
    w = worker.Worker(worker_id=wid, session_id=s.id, adapter=CbcAdapter(),
                      process=SimpleNamespace(returncode=None))
    worker.workers[w.worker_id] = w
    return w


async def set_policy(**value):
    await server.api_put_settings_ui({'autoReport': value})


async def admit_unit(w, s, items):
    """Reserve then commit the exact provider hand-off boundary."""
    text = worker._queue_history_row(items)['content']
    # Reservation returns ``history_added`` (False for a fresh unit); the
    # durable in-memory state proves it ran.
    await worker._reserve_queue_unit(w, s, items, text, expected_revisions=[
        item.get('revision', 1) for item in items])
    assert all(worker._delivery_state(item) == 'writing' for item in items)
    assert await worker._commit_queue_handoff(w, s, items)


def test_summary_defaults_are_independent_and_never_inherit_plugin_default():
    assert auto_report.DEFAULTS['ma'] is True
    assert auto_report.DEFAULTS['summary'] == {'system': False, 'ma': False,
                                               'user': False, 'plugins': {}}
    plugins = [{'id': 'declared', 'defaultEnabled': True, 'legacySources': []}]

    def decision(source, policy=None, **extra):
        return auto_report.admission([{'source': source, **extra}], policy, plugins)['inputs'][0]

    assert decision('agent') == {'version': 1, 'origin': {'kind': 'ma'},
                                 'enabled': True, 'summary': False}
    assert decision('agent', {'ma': False, 'summary': {'ma': True}}) == {
        'version': 1, 'origin': {'kind': 'ma'}, 'enabled': False, 'summary': True}
    # A plugin declaration may enable answer reports but never prompt summaries.
    assert decision('plugin', sourcePluginId='declared')['enabled'] is True
    assert decision('plugin', sourcePluginId='declared')['summary'] is False
    assert decision('plugin', {'summary': {'plugins': {'declared': True}}},
                    sourcePluginId='declared')['summary'] is True
    assert decision('report', {'summary': {'system': True, 'ma': True, 'user': True},
                               'system': True, 'ma': True, 'user': True})['summary'] is False
    assert decision('system_prompt', {'summary': {'user': True}})['summary'] is False
    # Summary switches alone never enable answer reporting.
    assert auto_report.admission([{'source': 'agent'}], {'ma': False, 'summary': {'ma': True}},
                                 plugins)['enabled'] is False


def test_summary_excerpt_is_first_100_unicode_characters():
    body = '中' * 60 + '😀' * 60
    excerpt = auto_report.summary_excerpt(body)
    assert excerpt == body[:100]
    assert len(excerpt) == 100
    assert excerpt[-1] == '😀'
    assert auto_report.summary_excerpt('短正文') == '短正文'
    assert auto_report.summary_excerpt('') is None
    assert auto_report.summary_excerpt(None) is None
    assert auto_report.PROMPT_SUMMARY_CHARS == 100


@pytest.mark.parametrize('origin,text,extra,summary_switch', [
    ('user', '用户输入' * 40, {}, 'user'),
    ('agent', '代理输入' * 40, {'source_session_id': 'sender'}, 'ma'),
    ('automation', '系统通知' * 40, {'noticeKind': 'background_job_terminal'}, 'system'),
    ('plugin', '插件输入' * 40, {'source_plugin_id': 'future'}, 'plugin'),
])
def test_handoff_freezes_real_input_identity_per_origin(monkeypatch, origin, text, extra,
                                                        summary_switch):
    async def run():
        session('sender')
        s = session('origin-target', 'sender' if origin == 'agent' else None)
        s.queue_paused = False
        w = runtime_worker(s)
        switch = {'plugins': {'future': True}} if summary_switch == 'plugin' else {
            summary_switch: True}
        await set_policy(ma=True, summary=switch)
        if origin == 'plugin':
            from packages.core.manifest_loader import McpServer, load_manifests
            manifest = load_manifests([])
            manifest.mcp_servers = [McpServer(name='provider', command='unused')]
            manifest.plugins = [{'protocol': 'pan-plugin/v1', 'id': 'future',
                                 'display_name': 'Future',
                                 'source': {'kind': 'mcp', 'server_ref': 'provider'},
                                 'message_source': {}}]
            monkeypatch.setattr(auto_report, 'manifest_provider', lambda: manifest)
        if origin == 'automation':
            notices = await worker.enqueue_notice(s.id, text, source='automation',
                                                 notice_kind='background_job_terminal',
                                                 job_id='job-1')
            assert notices['ok']
            items = [s.queue_pending[-1]]
        else:
            item, error = await worker._persist_task_item(
                s, text, origin, None, None, f'client-{origin}', **extra)
            assert error is None, error
            items = [item]
        queue_id = worker._queue_item_id(items[-1])
        await admit_unit(w, s, items)
        row = s.history[-1]
        assert row['role'] == 'user'
        if origin == 'automation':
            # A notice's provider-visible body is its formatted batch text.
            assert text in row['content']
            assert row['content'].startswith('////by pan system')
        else:
            assert row['content'] == text
        # The summary is always a prefix of the canonical row the id resolves to.
        assert w._prompt_ref == {'promptMessageId': row['messageId'],
                                 'promptSummary': row['content'][:100]}
        # The queue transaction id is not a message identity.
        assert w._prompt_ref['promptMessageId'] != queue_id
        assert sess.is_pan_message_id(w._prompt_ref['promptMessageId'])
        # A later history append cannot retarget the frozen reference.
        frozen = copy.deepcopy(w._prompt_ref)
        sess.append_history(s, {'role': 'assistant', 'content': 'answer'})
        sess.append_history(s, {'role': 'user', 'content': 'later queued input'})
        assert w._prompt_ref == frozen
        terminal = await worker._persist_terminal_state(w, s, 'done', 'answer')
        assert terminal['promptMessageId'] == frozen['promptMessageId']
        assert terminal['promptSummary'] == frozen['promptSummary']
        assert s.last_result['promptMessageId'] == frozen['promptMessageId']
        assert s.terminal_results[-1]['promptSummary'] == frozen['promptSummary']
    asyncio.run(run())


def test_summary_off_never_carries_input_body_into_report():
    async def run():
        manager = session('summary-manager'); child = session('summary-child', manager.id)
        manager.report_subscriptions.add(child.id)
        child.queue_paused = False
        w = runtime_worker(child)
        await set_policy(ma=True, summary={'ma': False})
        body = '机密输入' * 30
        item, error = await worker._persist_task_item(
            child, body, 'agent', None, None, 'client-private', source_session_id=manager.id)
        assert error is None
        await admit_unit(w, child, [item])
        terminal = await worker._persist_terminal_state(w, child, 'done', 'answer')
        await worker._enqueue_report(child.id, terminal['status'], terminal['result'],
                                     terminal['taskId'], w.worker_id,
                                     admission=terminal['autoReportAdmission'],
                                     prompt=terminal)
        report = manager.queue_pending[0]
        assert report['promptMessageId'] == terminal['promptMessageId']
        assert report['promptMessageId'] is not None
        assert 'promptSummary' in report and report['promptSummary'] is None
        assert body[:100] not in json.dumps(report, ensure_ascii=False)
        assert child.last_result['promptSummary'] is None
        assert body[:100] not in json.dumps(child.last_result, ensure_ascii=False)
        # Turning the switch on is a separate, later policy for later inputs.
        await set_policy(summary={'ma': True})
        assert child.last_result['promptSummary'] is None
    asyncio.run(run())


def test_batch_uses_last_admitted_input_and_queued_newcomers_do_not_cross_turns():
    async def run():
        s = session('batch-target'); s.queue_paused = False
        w = runtime_worker(s)
        await set_policy(system=True, summary={'system': True})
        first = await worker.enqueue_notice(s.id, 'first notice ' + '甲' * 20,
                                            source='automation',
                                            notice_kind='background_job_terminal',
                                            job_id='job-a')
        second = await worker.enqueue_notice(s.id, 'last notice ' + '乙' * 20,
                                             source='automation',
                                             notice_kind='background_job_terminal',
                                             job_id='job-b')
        assert first['ok'] and second['ok']
        unit = worker._select_queue_unit(s)
        assert [worker._queue_item_id(row) for row in unit] == [
            worker._queue_item_id(item) for item in s.queue_pending[:2]]
        # A message enqueued after selection but before hand-off is not admitted
        # by this execution and must not supply the reference.
        late, error = await worker._persist_task_item(
            s, 'late user input', 'user', None, None, 'late-client')
        assert error is None
        await admit_unit(w, s, unit)
        row = s.history[-1]
        assert row['queueItemIds'] == [worker._queue_item_id(item) for item in unit]
        assert w._prompt_ref['promptMessageId'] == row['messageId']
        # One provider-visible batch message: the summary is that row's body
        # prefix, not a single member's raw result.
        assert w._prompt_ref['promptSummary'] == row['content'][:100]
        assert row['content'].startswith('////by pan system')
        assert 'last notice' in row['content']
        # The queued newcomer remains untouched for its own later turn.
        assert any(item is late for item in s.queue_pending)
        assert late.get('autoReportAdmission') is None or \
            late['autoReportAdmission'].get('summary') is False
    asyncio.run(run())


def test_mixed_batch_summary_requires_every_contributing_source():
    async def run():
        s = session('mixed-batch'); s.queue_paused = False
        w = runtime_worker(s)
        # The live policy is deliberately all-on: the frozen per-input decisions
        # are authoritative and must not be re-derived at hand-off.
        await set_policy(summary={'system': True})
        secret = '关闭来源的机密正文' * 8
        first = await worker.enqueue_notice(s.id, secret, source='automation',
                                            notice_kind='background_job_terminal',
                                            job_id='job-off')
        second = await worker.enqueue_notice(s.id, '开启来源的正文' * 8, source='automation',
                                             notice_kind='background_job_terminal',
                                             job_id='job-on')
        assert first['ok'] and second['ok']
        rows = s.queue_pending[-2:]
        rows[0]['autoReportAdmission'] = {'version': 1, 'origin': {'kind': 'system'},
                                          'enabled': True, 'summary': False}
        rows[1]['autoReportAdmission'] = {'version': 1, 'origin': {'kind': 'system'},
                                          'enabled': True, 'summary': True}
        await admit_unit(w, s, worker._select_queue_unit(s))
        row = s.history[-1]
        # The canonical id is still reported, but the disabled contributor's
        # body must not be previewed through the enabled one.
        assert w._prompt_ref['promptMessageId'] == row['messageId']
        assert w._prompt_ref['promptSummary'] is None
        assert secret[:100] not in json.dumps(w._prompt_ref, ensure_ascii=False)
        terminal = await worker._persist_terminal_state(w, s, 'done', 'answer')
        assert terminal['promptSummary'] is None
    asyncio.run(run())


def test_all_enabled_batch_summary_is_the_canonical_row_prefix():
    async def run():
        s = session('all-on-batch'); s.queue_paused = False
        w = runtime_worker(s)
        await set_policy(summary={'system': True})
        for index, text in enumerate(['第一条正文 ' + '甲' * 60, '第二条正文 ' + '乙' * 60]):
            result = await worker.enqueue_notice(
                s.id, text, source='automation',
                notice_kind='background_job_terminal', job_id=f'job-{index}')
            assert result['ok']
        await admit_unit(w, s, worker._select_queue_unit(s))
        row = s.history[-1]
        assert w._prompt_ref['promptMessageId'] == row['messageId']
        assert w._prompt_ref['promptSummary'] == row['content'][:100]
        assert w._prompt_ref['promptSummary'] == auto_report.summary_excerpt(row['content'])
        # Looking up the reported id yields exactly the summarized body.
        found = next(item for item in s.history if item['messageId'] == row['messageId'])
        assert found['content'][:100] == w._prompt_ref['promptSummary']
    asyncio.run(run())


def test_single_formatted_notice_reference_resolves_to_summary_prefix():
    async def run():
        s = session('notice-single'); s.queue_paused = False
        w = runtime_worker(s)
        await set_policy(summary={'system': True})
        await worker.enqueue_notice(s.id, '单条通知正文 ' + '丙' * 90,
                                    source='automation',
                                    notice_kind='background_job_terminal',
                                    job_id='job-single')
        await admit_unit(w, s, worker._select_queue_unit(s))
        row = s.history[-1]
        assert row['content'].startswith('////by pan system')
        assert w._prompt_ref['promptSummary'] == row['content'][:100]
        assert w._prompt_ref['promptSummary'].startswith('////by pan system')
        found = next(item for item in s.history
                     if item['messageId'] == w._prompt_ref['promptMessageId'])
        assert found['content'].startswith(w._prompt_ref['promptSummary'])
    asyncio.run(run())


def test_disabled_and_report_inputs_never_attach_a_summary():
    async def run():
        s = session('never-summary'); s.queue_paused = False
        w = runtime_worker(s)
        await set_policy(system=True, ma=True, user=True,
                         summary={'system': True, 'ma': True, 'user': True})
        await worker.enqueue_notice(s.id, '关闭摘要的通知', source='automation',
                                    notice_kind='background_job_terminal', job_id='job-off')
        s.queue_pending[-1]['autoReportAdmission'] = {
            'version': 1, 'origin': {'kind': 'system'}, 'enabled': True, 'summary': False}
        await admit_unit(w, s, worker._select_queue_unit(s))
        assert w._prompt_ref['promptSummary'] is None

        # A Report batch never gets a summary even with every Core switch on.
        reports = []
        for index in range(2):
            reports.append({
                'type': 'report', 'kind': 'report', 'id': f'q_report_{index}',
                'queueItemId': f'q_report_{index}', 'source': 'report',
                'status': 'done', 'result': f'child answer {index}',
                'sessionId': s.id, 'taskId': None, 'workerId': 'w',
                'deliveryState': 'queued', 'dispatchState': 'queued', 'revision': 1,
                'createdAt': index,
            })
        s.queue_pending = reports
        s.queue_paused = False
        await admit_unit(w, s, worker._select_queue_unit(s))
        assert w._prompt_ref['promptMessageId'] == s.history[-1]['messageId']
        assert w._prompt_ref['promptSummary'] is None
    asyncio.run(run())


def test_delivery_recovery_requires_every_key_on_the_same_row():
    items = [
        {'type': 'task', 'id': 'q_a', 'queueItemId': 'q_a', 'text': 'A', 'source': 'user'},
        {'type': 'task', 'id': 'q_b', 'queueItemId': 'q_b', 'text': 'B', 'source': 'user'},
    ]
    for item in items:
        item['autoReportAdmission'] = {'version': 1, 'origin': {'kind': 'user'},
                                       'enabled': False, 'summary': True}
    keys = [worker._delivery_key(item) for item in items]
    s = session('recovery-proof')
    partial = {'role': 'user', 'content': 'partial body',
               'messageId': sess._new_pan_message_id(), 'delivered_keys': [keys[0]]}
    s.history = [partial]
    assert worker._delivered_prompt_row(s, items, {}) is None
    # A partial row after the complete one must not lend its identity either.
    complete = {'role': 'user', 'content': 'complete canonical body',
                'messageId': sess._new_pan_message_id(), 'delivered_keys': keys}
    s.history = [complete, partial]
    assert worker._delivered_prompt_row(s, items, {}) is complete
    frozen = worker._freeze_prompt_ref(items, worker._delivered_prompt_row(s, items, {}))
    assert frozen == {'promptMessageId': complete['messageId'],
                      'promptSummary': complete['content'][:100]}


def test_accepted_steer_updates_reference_rejected_and_unknown_do_not():
    async def run():
        s = session('steer-target'); s.queue_paused = False
        w = runtime_worker(s)
        await set_policy(ma=True, summary={'ma': True})
        original, error = await worker._persist_task_item(
            s, 'original ' + '首' * 10, 'agent', None, None, 'original-client')
        assert error is None
        await admit_unit(w, s, [original])
        before = copy.deepcopy(w._prompt_ref)
        assert before['promptSummary'].startswith('original')

        async def steer_item(text, execution_seq):
            item, problem = await worker._persist_task_item(
                s, text, 'agent', None, None, f'steer-{text[:4]}')
            assert problem is None
            auto_report.admission([item], {'ma': True, 'summary': {'ma': True}}, [])
            item.update(autoReportExecutionSeq=execution_seq, queueSteerPending=True,
                        queueSteerRequestId='request', queueSteerWorkerId=w.worker_id,
                        queueSteerGeneration=w.generation)
            return item

        rejected = await steer_item('rejected ' + '否' * 10, w._execution_seq)
        await worker._settle_queue_steer(w, s, rejected, rejected['text'],
                                         'provider rejected', 'request', w.generation,
                                         disposition='rejected')
        assert w._prompt_ref == before

        unknown = await steer_item('unknown ' + '疑' * 10, w._execution_seq)
        await worker._settle_queue_steer(w, s, unknown, unknown['text'],
                                         'Steer outcome unknown', 'request', w.generation,
                                         disposition='unknown')
        assert w._prompt_ref == before

        accepted = await steer_item('accepted ' + '受' * 10, w._execution_seq)
        await worker._settle_queue_steer(w, s, accepted, accepted['text'], None,
                                         'request', w.generation, disposition='accepted')
        assert w._prompt_ref['promptMessageId'] == s.history[-1]['messageId']
        assert w._prompt_ref['promptSummary'].startswith('accepted')
        assert w._prompt_ref['promptMessageId'] != before['promptMessageId']

        # A late acceptance for another execution settles its receipt but must
        # not attach its summary to the current turn.
        current = copy.deepcopy(w._prompt_ref)
        late = await steer_item('late ' + '迟' * 10, w._execution_seq - 1)
        await worker._settle_queue_steer(w, s, late, late['text'], None,
                                         'request', w.generation, disposition='accepted')
        assert w._prompt_ref == current
    asyncio.run(run())


def test_native_tui_and_untracked_inputs_keep_null_reference():
    async def run():
        s = session('native-target')
        w = runtime_worker(s)
        w._prompt_ref = {'promptMessageId': 'pan_stale', 'promptSummary': 'stale'}
        # Native admission is a separate branch; it cannot borrow a Pan input id.
        w._prompt_ref = None
        terminal = await worker._persist_terminal_state(w, s, 'done', 'native answer')
        assert terminal['promptMessageId'] is None
        assert terminal['promptSummary'] is None
        assert s.last_result['promptMessageId'] is None
        # A hand-off whose history row has no Pan messageId never invents one.
        assert worker._freeze_prompt_ref([{'type': 'report'}], {'messageId': None}) is None
        assert worker._freeze_prompt_ref([], {}) is None
    asyncio.run(run())


def test_report_and_manual_paths_share_one_source_and_stay_null_without_evidence():
    async def run():
        manager = session('manual-manager'); child = session('manual-child', manager.id)
        manager.report_subscriptions.add(child.id)
        terminal = {
            'status': 'done', 'result': 'answer', 'taskId': 'task',
            'taskSeq': 1, 'workerId': 'w', 'promptMessageId': 'pan_input',
            'promptSummary': '正文' * 10,
        }
        await worker._enqueue_report(child.id, 'done', 'answer', 'task', 'w',
                                     admission={'enabled': True},
                                     prompt=terminal)
        report = manager.queue_pending[0]
        assert (report['promptMessageId'], report['promptSummary']) == (
            'pan_input', '正文' * 10)
        await worker._enqueue_report(child.id, 'done', 'answer', 'task', 'w',
                                     admission={'enabled': True})
        second = manager.queue_pending[1]
        assert second['promptMessageId'] is None and second['promptSummary'] is None

        # Manual explicit report reuses only the frozen terminal evidence.
        tail = sess._new_pan_message_id()
        child.history = [{'role': 'assistant', 'content': 'answer', 'messageId': tail}]
        child.history_epoch = 'epoch'; child.history_revision = 1
        run_terminal = {'status': 'done', 'result': 'answer', 'taskId': 'task-m',
                        'taskSeq': 3, 'workerId': 'w', 'resultCursor': 1,
                        'terminalKey': 'k', 'historyEpoch': 'epoch', 'historyRevision': 1,
                        'promptMessageId': 'pan_input', 'promptSummary': '正文' * 10}
        child.terminal_results = [dict(run_terminal)]
        result, error = await worker.enqueue_manual_report(child.id, 'task-m', 3, tail)
        assert error is None, error
        manual = next(row for row in manager.queue_pending if row.get('manual'))
        assert manual['promptMessageId'] == 'pan_input'
        assert manual['promptSummary'] == '正文' * 10

        # A legacy run row without the fields stays null instead of borrowing.
        child.history = [{'role': 'assistant', 'content': 'answer2',
                          'messageId': sess._new_pan_message_id()}]
        child.history_revision = 2
        legacy_terminal = {'status': 'done', 'result': 'answer2', 'taskId': 'task-l',
                           'taskSeq': 4, 'workerId': 'w', 'resultCursor': 2,
                           'terminalKey': 'k2', 'historyEpoch': 'epoch',
                           'historyRevision': 2}
        child.terminal_results = [dict(legacy_terminal)]
        legacy_result, legacy_error = await worker.enqueue_manual_report(
            child.id, 'task-l', 4, child.history[0]['messageId'])
        assert legacy_error is None, legacy_error
        legacy = next(row for row in manager.queue_pending
                      if row.get('manualReportKey', '').endswith(':task-l:4'))
        assert legacy['promptMessageId'] is None
        assert legacy['promptSummary'] is None
    asyncio.run(run())


def test_cold_load_keeps_reference_and_legacy_rows_stay_compatible(tmp_path):
    s = session('cold')
    s.last_result = {'status': 'done', 'result': 'answer', 'promptMessageId': 'pan_mid',
                     'promptSummary': '前' * 100}
    s.terminal_results = [{'status': 'done', 'result': 'answer',
                           'promptMessageId': 'pan_mid', 'promptSummary': '前' * 100},
                          {'status': 'done', 'result': 'legacy'}]
    report = {'type': 'report', 'id': 'q_1', 'queueItemId': 'q_1', 'source': 'report',
              'status': 'done', 'result': 'answer', 'promptMessageId': 'pan_mid',
              'promptSummary': '前' * 100, 'deliveryState': 'queued'}
    legacy_report = {key: value for key, value in report.items()
                     if key not in {'promptMessageId', 'promptSummary'}}
    s.queue_pending = [report, legacy_report]
    sess.save(s)
    sess._cache.clear()
    restored = sess.get(s.id)
    assert restored.last_result['promptMessageId'] == 'pan_mid'
    assert restored.last_result['promptSummary'] == '前' * 100
    assert restored.terminal_results[0]['promptSummary'] == '前' * 100
    assert 'promptMessageId' not in restored.terminal_results[1]
    assert restored.queue_pending[0]['promptMessageId'] == 'pan_mid'
    assert 'promptSummary' not in restored.queue_pending[1]
    # Reading legacy rows never invents a reference or rewrites them.
    assert worker._prompt_report_fields(restored.queue_pending[1]) == {
        'promptMessageId': None, 'promptSummary': None}
    assert restored.queue_pending[1] == legacy_report


def test_summary_settings_http_validation_and_effective_plugin_values(monkeypatch, tmp_path):
    from packages.core.manifest_loader import load_manifests, McpServer
    manifest = load_manifests([])
    manifest.mcp_servers = [McpServer(name='provider', command='unused')]
    manifest.plugins = [{'protocol': 'pan-plugin/v1', 'id': 'future', 'display_name': 'Future',
                         'source': {'kind': 'mcp', 'server_ref': 'provider'},
                         'message_source': {'auto_report_default': True}}]
    monkeypatch.setattr(auto_report, 'manifest_provider', lambda: manifest)

    async def run():
        sources = (await server.api_plugin_message_sources())['sources']
        assert sources[0]['effectiveEnabled'] is True
        assert sources[0]['effectiveSummaryEnabled'] is False
        await server.api_put_settings_ui({'autoReport': {'summary': {'plugins': {'future': True}}}})
        sources = (await server.api_plugin_message_sources())['sources']
        assert sources[0]['effectiveSummaryEnabled'] is True
        assert sources[0]['effectiveEnabled'] is True
        await server.api_put_settings_ui({'autoReport': {'summary': {'plugins': {}}}})
        assert (await server.api_plugin_message_sources())['sources'][0][
            'effectiveSummaryEnabled'] is False
        # GET sanitizes malformed persisted values without rewriting them.
        raw = config.read_config_file()
        config.save_config({**raw, 'ui': {**(raw.get('ui') or {}),
                                          'autoReport': {'summary': 'broken'}}})
        ui = await server.api_get_settings_ui()
        assert ui['autoReport']['summary'] == auto_report.SUMMARY_DEFAULTS
    asyncio.run(run())

    before = config.read_config_file()
    for value in [{'summary': []}, {'summary': {'ma': 1}}, {'summary': {'bad': True}},
                  {'summary': {'plugins': {'qq': 'yes'}}}, {'summary': {'unknown': True}}]:
        with pytest.raises(server.HTTPException) as error:
            asyncio.run(server.api_put_settings_ui({'autoReport': value}))
        assert error.value.status_code == 422
        assert config.read_config_file() == before
