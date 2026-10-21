"""Owned dynamic-port HTTP/WS, deterministic CLI, and built-app browser proof."""
import json
import os
from pathlib import Path

import pytest
import psutil
from playwright.sync_api import expect, sync_playwright

from tests.test_real_queue_http_e2e import (
    ROOT, PORT, _PanRuntime, _EventRecorder, _create, _wait_for, _received,
)


@pytest.mark.timeout(120)
def test_source_policy_real_http_ws_browser(tmp_path):
    assert PORT not in {8767, 8768}, 'run via scripts/run_tests_isolated.py with a dynamic port'
    data = tmp_path / 'owned-source-policy'
    data.mkdir()
    declaration = data / 'future-plugin.json'
    declaration.write_text(json.dumps({
        'mcp_servers': [{'name': 'future-source', 'command': 'unused-test-provider'}],
        'plugins': [{'protocol': 'pan-plugin/v1', 'id': 'future', 'display_name': 'Future plugin',
                     'source': {'kind': 'mcp', 'server_ref': 'future-source'}, 'message_source': {}}],
    }), encoding='utf-8')
    (data / 'config.json').write_text(json.dumps({
        'plugin_manifests': [str(ROOT / 'manifest.json'), str(ROOT / 'packages/mcp/manifest.json'), str(declaration)],
        'worker': {'idle_sec': 300}, 'scheduler': {'enabled': False},
    }), encoding='utf-8')
    runtime = _PanRuntime(data)
    recorder = None
    created = []
    evidence = {'port': PORT, 'runtime': str(data), 'browser': {}, 'sessions': []}

    def req(method, path, body=None):
        return runtime.request(method, path, **({'json': body} if body is not None else {}))

    def stored(sid):
        return json.loads((data / 'sessions' / f'{sid}.json').read_text(encoding='utf-8'))

    def done(sid, count):
        return _wait_for(lambda: len(stored(sid).get('terminal_results', [])) >= count,
                         label=f'{sid} durable terminal {count}')

    def pause(sid, value):
        assert req('PATCH', f'/api/sessions/{sid}/queue/paused', {'paused': value})['ok']

    try:
        runtime.start()
        evidence['pidIdentities'] = list(runtime._owned_processes)
        recorder = _EventRecorder(runtime.base_url.replace('http', 'ws', 1) + '/ws/agent')
        recorder.start()
        sources = req('GET', '/api/plugins/message-sources')['sources']
        assert next(p for p in sources if p['id'] == 'qq')['defaultEnabled'] is True
        assert next(p for p in sources if p['id'] == 'future')['effectiveEnabled'] is False

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 1280, 'height': 900})
            page_errors = []
            page.on('pageerror', lambda error: page_errors.append(str(error)))
            page.goto(runtime.base_url + '/react/')
            page.get_by_title('App settings', exact=True).click()
            page.get_by_role('tab', name='Behavior', exact=True).click()
            group = page.get_by_role('region', name='Auto report', exact=True)
            expect(group.get_by_role('checkbox', name='Auto report MA', exact=True)).to_be_checked()
            expect(group.get_by_role('checkbox', name='Auto report System', exact=True)).not_to_be_checked()
            expect(group.get_by_role('checkbox', name='Auto report Real user', exact=True)).not_to_be_checked()
            future = group.get_by_role('checkbox', name='Auto report plugin Future plugin', exact=True)
            expect(future).not_to_be_checked()
            future.click()
            expect(future).to_be_checked()
            _wait_for(lambda: req('GET', '/api/settings/ui')['autoReport']['plugins'].get('future') is True,
                      label='browser PUT persisted')
            page.reload()
            page.get_by_title('App settings', exact=True).click()
            page.get_by_role('tab', name='Behavior', exact=True).click()
            expect(future).to_be_checked()
            group.get_by_role('button', name='Reset auto report defaults').click()
            expect(future).not_to_be_checked()
            expect(group.get_by_role('checkbox', name='Auto report plugin QQ', exact=True)).to_be_checked()
            assert not page_errors, page_errors
            evidence['browser'] = {'defaults': True, 'dynamicUndeclaredPlugin': True, 'persistReload': True, 'reset': True, 'pageErrors': page_errors}
            output = os.environ.get('PYTEST_SOURCE_AUTO_REPORT_EVIDENCE')
            if output:
                Path(output).mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(Path(output) / 'auto-report-settings.png'), full_page=True)
            browser.close()

        root = _create(runtime, 'source-root'); manager = _create(runtime, 'source-manager')
        child = _create(runtime, 'real-fifo')
        created = [root['id'], manager['id'], child['id']]
        evidence['sessions'] = created
        for parent, descendant in [(root, manager), (manager, child)]:
            result = req('POST', '/api/claim', {'managerId': parent['id'], 'sessionId': descendant['id']})
            assert 'error' not in result, result
        pause(root['id'], True); pause(manager['id'], True)

        # MA admitted under defaults; change policy and enqueue a different origin
        # while the first real CLI execution is blocked. Neither may change it.
        req('POST', '/api/assign', {'sessionId': child['id'], 'sourceSessionId': manager['id'],
                                   'text': 'hold-A', 'taskId': 'source-snapshot'})
        _wait_for(lambda: _received(runtime, cwd=child['workdir'], text='hold-A'), label='actual fake CLI admission')
        req('PUT', '/api/settings/ui', {'autoReport': {'ma': False}})
        req('POST', '/api/send', {'sessionId': child['id'], 'source': 'user', 'text': 'later-user'})
        runtime.gates['hold-A'].touch()
        done(child['id'], 2)
        _wait_for(lambda: len(stored(manager['id'])['queue_pending']) == 1, label='only admitted MA answer report')
        terminals = stored(child['id'])['terminal_results']
        assert [t['autoReportAdmission']['enabled'] for t in terminals] == [True, False]
        assert [t['autoReportAdmission']['inputs'][0]['origin']['kind'] for t in terminals] == ['ma', 'user']

        # Processing a real Report produces a normal answer/WS terminal but no
        # auto-report to the next ancestor, even with every Core switch enabled.
        req('PUT', '/api/settings/ui', {'autoReport': {'system': True, 'ma': True, 'user': True}})
        pause(manager['id'], False)
        req('POST', '/api/spawn', {'sessionId': manager['id']})
        done(manager['id'], 1)
        assert stored(manager['id'])['terminal_results'][-1]['autoReportAdmission']['enabled'] is False
        assert stored(root['id'])['queue_pending'] == []
        pause(manager['id'], True)
        for entry in ['assign', 'send', 'force']:
            body = {'sessionId': manager['id'], 'sourceSessionId': child['id'], 'text': 'upstream-' + entry}
            if entry == 'assign': body['taskId'] = 'upstream-task'
            if entry == 'force': body['force'] = True
            response = req('POST', '/api/assign' if entry == 'assign' else '/api/send', body)
            assert response['status'] == 'queued', response
        pending = stored(manager['id'])['queue_pending']
        assert len(pending) == 3 and all(row['source'] == 'report' for row in pending)
        assert [row['text'] for row in pending] == ['upstream-assign', 'upstream-send', 'upstream-force']

        # New registered plugin (no default field) can be explicitly enabled.
        req('PUT', '/api/settings/ui', {'autoReport': {'plugins': {'future': True}}})
        req('POST', '/api/plugins/future/messages', {'sessionId': child['id'], 'text': 'dynamic-plugin-input'})
        done(child['id'], 3)
        _wait_for(lambda: len(stored(manager['id'])['queue_pending']) == 4, label='enabled plugin answer report')
        assert stored(child['id'])['terminal_results'][-1]['autoReportAdmission']['inputs'][0]['origin'] == {'kind': 'plugin', 'pluginId': 'future'}
        assert any(e['type'] == 'worker.result' and e['sessionId'] == child['id'] for e in recorder.events)
        assert any(e['type'] == 'queue.item_delivered' and e['sessionId'] == manager['id'] for e in recorder.events)
        evidence.update(snapshotUnaffectedByPolicyAndQueue=True, automaticReportNoEcho=True,
                        threeUpstreamEntries=True, dynamicPluginAnswer=True, wsEvents=len(recorder.events))
    finally:
        fake_identities = []
        for pid in {r['pid'] for r in runtime.fake_records() if r.get('pid')}:
            try:
                process = psutil.Process(pid)
                fake_identities.append((pid, process.create_time()))
            except psutil.NoSuchProcess:
                pass
        try:
            if runtime.process is not None and runtime.process.poll() is None:
                for sid in created:
                    req('POST', f'/api/sessions/{sid}/worker/kill')
        finally:
            try:
                if recorder is not None:
                    recorder.stop()
            finally:
                runtime.stop()
        for pid, created_at in fake_identities:
            def gone():
                try:
                    return psutil.Process(pid).create_time() != created_at
                except psutil.NoSuchProcess:
                    return True
            _wait_for(gone, label=f'owned fake CLI {pid} exit')
        evidence['fakeCliIdentitiesCleaned'] = fake_identities
        evidence['ownedPortReleased'] = True
        output = os.environ.get('PYTEST_SOURCE_AUTO_REPORT_EVIDENCE')
        if output:
            Path(output).mkdir(parents=True, exist_ok=True)
            (Path(output) / 'real-chain.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
    print('SOURCE_AUTO_REPORT_REAL ' + json.dumps(evidence))
