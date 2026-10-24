"""Owned real HTTP/fakeCLI manager chain and production Behavior browser proof."""
import json
import os
from pathlib import Path

import psutil
import pytest
from playwright.sync_api import expect, sync_playwright

from tests.test_real_queue_http_e2e import (
    ROOT, PORT, _PanRuntime, _EventRecorder, _create, _wait_for, _received,
)


@pytest.mark.timeout(180)
def test_report_prompt_reference_real_chain_and_browser(tmp_path):
    assert PORT not in {8767, 8768}, 'run via scripts/run_tests_isolated.py with a dynamic port'
    data = tmp_path / 'owned-prompt-reference'
    data.mkdir()
    declaration = data / 'prompt-plugin.json'
    declaration.write_text(json.dumps({
        'mcp_servers': [{'name': 'prompt-source', 'command': 'unused-test-provider'}],
        'plugins': [{'protocol': 'pan-plugin/v1', 'id': 'promptsrc',
                     'display_name': 'Prompt plugin', 'source': {'kind': 'mcp',
                     'server_ref': 'prompt-source'},
                     'message_source': {'auto_report_default': True}}],
    }), encoding='utf-8')
    (data / 'config.json').write_text(json.dumps({
        'plugin_manifests': [str(ROOT / 'manifest.json'),
                             str(ROOT / 'packages/mcp/manifest.json'),
                             str(declaration)],
        'worker': {'idle_sec': 300}, 'scheduler': {'enabled': False},
    }), encoding='utf-8')
    runtime = _PanRuntime(data)
    recorder = None
    created = []
    body = '真实输入 中文与表情 😀🎯 ' + '尾' * 120
    evidence = {'port': PORT, 'runtime': str(data), 'browser': {}, 'sessions': []}

    def req(method, path, body=None):
        return runtime.request(method, path, **({'json': body} if body is not None else {}))

    def stored(sid):
        return json.loads((data / 'sessions' / f'{sid}.json').read_text(encoding='utf-8'))

    def done(sid, count):
        return _wait_for(lambda: len(stored(sid).get('terminal_results', [])) >= count,
                         label=f'{sid} durable terminal {count}')

    try:
        runtime.start()
        evidence['pidIdentities'] = list(runtime._owned_processes)
        recorder = _EventRecorder(runtime.base_url.replace('http', 'ws', 1) + '/ws/agent')
        recorder.start()
        sources = req('GET', '/api/plugins/message-sources')['sources']
        # Declarations may enable answer reports; prompt summaries never inherit.
        assert next(p for p in sources if p['id'] == 'promptsrc')['effectiveEnabled'] is True
        assert next(p for p in sources if p['id'] == 'promptsrc')[
            'effectiveSummaryEnabled'] is False

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={'width': 1280, 'height': 1000})
            page_errors = []
            page.on('pageerror', lambda error: page_errors.append(str(error)))
            page.goto(runtime.base_url + '/react/')
            page.get_by_title('App settings', exact=True).click()
            page.get_by_role('tab', name='Behavior', exact=True).click()
            summary = page.get_by_role('region', name='Prompt summary', exact=True)
            report = page.get_by_role('region', name='Auto report', exact=True)
            for name in ['Prompt summary System', 'Prompt summary MA',
                         'Prompt summary Real user', 'Prompt summary plugin QQ',
                         'Prompt summary plugin Prompt plugin']:
                expect(summary.get_by_role('checkbox', name=name, exact=True)).not_to_be_checked()
            expect(report.get_by_role('checkbox', name='Auto report plugin QQ',
                                      exact=True)).to_be_checked()
            summary_ma = summary.get_by_role('checkbox', name='Prompt summary MA', exact=True)
            summary_ma.click()
            expect(summary_ma).to_be_checked()
            _wait_for(lambda: req('GET', '/api/settings/ui')['autoReport']['summary']['ma'] is True,
                      label='summary browser PUT persisted')
            # The two policies stay independent in both directions.
            expect(report.get_by_role('checkbox', name='Auto report MA',
                                      exact=True)).to_be_checked()
            page.reload()
            page.get_by_title('App settings', exact=True).click()
            page.get_by_role('tab', name='Behavior', exact=True).click()
            summary = page.get_by_role('region', name='Prompt summary', exact=True)
            report = page.get_by_role('region', name='Auto report', exact=True)
            expect(summary.get_by_role('checkbox', name='Prompt summary MA',
                                       exact=True)).to_be_checked()
            expect(report.get_by_role('checkbox', name='Auto report plugin Prompt plugin',
                                      exact=True)).to_be_checked()
            report.get_by_role('button', name='Reset auto report defaults').click()
            expect(summary.get_by_role('checkbox', name='Prompt summary MA',
                                       exact=True)).to_be_checked()
            summary.get_by_role('button', name='Reset prompt summary defaults').click()
            expect(summary.get_by_role('checkbox', name='Prompt summary MA',
                                       exact=True)).not_to_be_checked()
            assert not page_errors, page_errors
            evidence['browser'] = {
                'summaryDefaultsOff': True, 'pluginSummaryNoInherit': True,
                'independentReset': True, 'persistReload': True,
                'pageErrors': page_errors,
            }
            output = os.environ.get('PYTEST_REPORT_PROMPT_EVIDENCE')
            if output:
                Path(output).mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(Path(output) / 'prompt-summary-settings.png'),
                                full_page=True)
            browser.close()

        # Enable only the MA summary switch for the real chain.
        req('PUT', '/api/settings/ui', {'autoReport': {
            'ma': True, 'user': False, 'system': False,
            'summary': {'ma': True, 'user': False, 'system': False}}})
        root = _create(runtime, 'prompt-root')
        manager = _create(runtime, 'prompt-manager')
        child = _create(runtime, 'prompt-child')
        created = [root['id'], manager['id'], child['id']]
        evidence['sessions'] = created
        for parent, descendant in [(root, manager), (manager, child)]:
            result = req('POST', '/api/claim',
                         {'managerId': parent['id'], 'sessionId': descendant['id']})
            assert 'error' not in result, result
        for session in (root, manager):
            assert req('PATCH', f"/api/sessions/{session['id']}/queue/paused",
                       {'paused': True})['ok']

        req('POST', '/api/send', {'sessionId': child['id'],
                                 'sourceSessionId': manager['id'], 'text': body})
        _wait_for(lambda: _received(runtime, cwd=child['workdir'], text=lambda text: body in text),
                  label='real fake CLI admitted prompt reference')
        done(child['id'], 1)
        child_detail = req('GET', f"/api/sessions/{child['id']}")
        input_row = next(row for row in reversed(child_detail['history'])
                         if row.get('role') == 'user' and body in str(row.get('content')))
        terminal = stored(child['id'])['terminal_results'][-1]
        assert terminal['promptMessageId'] == input_row['messageId'], (
            terminal['promptMessageId'], input_row['messageId'])
        assert terminal['promptSummary'] == body[:100]
        assert child_detail['lastResult']['promptMessageId'] == input_row['messageId']
        assert child_detail['lastResult']['promptSummary'] == body[:100]

        _wait_for(lambda: len(stored(manager['id'])['queue_pending']) == 1,
                  label='report with prompt reference delivered')
        report_item = stored(manager['id'])['queue_pending'][0]
        assert report_item['source'] == 'report'
        assert report_item['promptMessageId'] == input_row['messageId']
        assert report_item['promptSummary'] == body[:100]

        # Real manager consumption: the manager's own input is that report batch,
        # a Report origin never adds a prompt summary, and it cannot echo upward.
        req('PATCH', f"/api/sessions/{manager['id']}/queue/paused", {'paused': False})
        req('POST', '/api/spawn', {'sessionId': manager['id']})
        done(manager['id'], 1)
        manager_detail = req('GET', f"/api/sessions/{manager['id']}")
        report_row = next(row for row in reversed(manager_detail['history'])
                          if row.get('role') == 'user')
        manager_terminal = stored(manager['id'])['terminal_results'][-1]
        assert manager_terminal['promptMessageId'] == report_row['messageId']
        # A Report origin never enables a summary, even with MA summary on.  The
        # report body itself (and the answer quoting it) is a separate delivery,
        # so only the frozen summary field proves no input body was attached.
        assert manager_terminal['promptSummary'] is None
        assert stored(root['id'])['queue_pending'] == []
        assert any(e['type'] == 'queue.item_delivered' and e['sessionId'] == manager['id']
                   for e in recorder.events)
        evidence.update(childPromptFrozen=True, reportDeliveredWithReference=True,
                        managerConsumedReport=True, reportOriginNoSummary=True,
                        noReportEcho=True, wsEvents=len(recorder.events))
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
        for pid, started in fake_identities:
            def gone():
                try:
                    return psutil.Process(pid).create_time() != started
                except psutil.NoSuchProcess:
                    return True
            _wait_for(gone, label=f'owned fake CLI {pid} exit')
        evidence['fakeCliIdentitiesCleaned'] = fake_identities
        evidence['ownedPortReleased'] = True
        output = os.environ.get('PYTEST_REPORT_PROMPT_EVIDENCE')
        if output:
            Path(output).mkdir(parents=True, exist_ok=True)
            (Path(output) / 'real-chain.json').write_text(
                json.dumps(evidence, indent=2), encoding='utf-8')
    print('REPORT_PROMPT_REFERENCE_REAL ' + json.dumps(evidence))
