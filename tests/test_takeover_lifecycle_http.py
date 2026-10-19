"""Real HTTP and Windows process ownership; only provider storage/CLI are fake."""
import json
import sys
from concurrent.futures import ThreadPoolExecutor

import psutil
import pytest

from test_legal_offline_http import runtime, durable, paused
from test_real_queue_http_e2e import _create, _spawn, _enqueue, _wait_for

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Real Windows Job ownership and FILETIME proof')


@pytest.fixture(autouse=True)
def probe(monkeypatch):
    monkeypatch.setenv('PAN_E2E_TAKEOVER_PROBE', '1')


@pytest.fixture
def narrow_console(monkeypatch):
    monkeypatch.setenv('PYTHONUTF8', '0')
    monkeypatch.setenv('PYTHONIOENCODING', 'cp936:backslashreplace')


@pytest.mark.usefixtures('narrow_console')
def test_http_first_terminal_survives_real_reader_conflict_without_resend(runtime):
    s = _create(runtime, 'takeover-reader-conflict')
    sid = s['id']
    _spawn(runtime, sid)
    _wait_for(lambda: runtime.request('GET', f'/api/sessions/{sid}').get('cliSessionId'))
    (runtime.data_root / 'hold-terminal-reader').touch()
    _enqueue(runtime, sid, 'establish-native-session', 'first')
    _wait_for(lambda: durable(runtime, sid)['last_result'])
    value = durable(runtime, sid)
    assert value['last_result']['status'] == 'done' and value['result_cursor'] == 1
    assert value['legal_offline'] and not value['queue_pending']
    conflicts = [json.loads(line) for line in (runtime.data_root / 'reader-conflicts.jsonl').read_text().splitlines()]
    assert [row['winerror'] for row in conflicts] == [5]
    received = [row for row in runtime.fake_records() if row['kind'] == 'received' and row['cwd'] == s['workdir']]
    assert len(received) == 1 and received[0]['text'] == 'establish-native-session'
    init = [row for row in runtime.fake_records() if row['kind'] == 'init' and row['cwd'] == s['workdir']][-1]
    assert init['stdinEncoding'] == init['stdoutEncoding'] == 'gbk'
    assert not (runtime.data_root / 'terminal-exceptions.txt').exists()
    print('TERMINAL_READER_CONFLICT', runtime.base_url, conflicts, init['stdinEncoding'])


@pytest.mark.parametrize('action', ['kill', 'restart'])
@pytest.mark.parametrize('closed', [False, True])
def test_http_takeover_controls_retire_real_job(runtime, action, closed):
    s = _create(runtime, 'takeover-http')
    sid = s['id']
    _spawn(runtime, sid)
    _wait_for(lambda: runtime.request('GET', f'/api/sessions/{sid}').get('cliSessionId'))
    rejected = runtime.request('POST', f'/api/sessions/{sid}/worker/takeover')
    assert 'resume' in rejected['error'].lower()
    _enqueue(runtime, sid, 'establish-native-session', 'first')
    _wait_for(lambda: durable(runtime, sid)['last_result'])
    _wait_for(lambda: runtime.request('GET', f'/api/sessions/{sid}').get('workerStatus') == 'idle')
    paused(runtime, sid, True)
    if closed:
        (runtime.data_root / 'close-terminal').touch()
    result = runtime.request('POST', f'/api/sessions/{sid}/worker/takeover')
    assert result['status'] == 'takeover started', result
    owner = durable(runtime, sid)['offline_owner']
    assert owner['externalTakeover'] and owner['ownershipUnknown']
    path = runtime.data_root / 'takeover-processes.jsonl'
    record = _wait_for(lambda: json.loads(path.read_text().splitlines()[-1]) if path.exists() else None)
    assert record['filetime'] > 0 and psutil.pid_exists(record['child'])
    if closed:
        _wait_for(lambda: not psutil.pid_exists(record['pid']))
    before = len([r for r in runtime.fake_records() if r['kind'] == 'init' and r['cwd'] == s['workdir']])
    (runtime.data_root / 'gate-stop').touch()
    with ThreadPoolExecutor(2) as pool:
        requests = [pool.submit(runtime.request, 'POST', f'/api/sessions/{sid}/worker/{action}') for _ in range(2)]
        _wait_for(lambda: (runtime.data_root / 'stop-entered').exists())
        _wait_for(lambda: len((runtime.data_root / 'control-requests.jsonl').read_text().splitlines()) == 2)
        (runtime.data_root / 'stop-release').touch()
        outcomes = [request.result() for request in requests]
    assert all('error' not in outcome for outcome in outcomes), outcomes
    _wait_for(lambda: not psutil.pid_exists(record['child']))
    value = durable(runtime, sid)
    retirement = value['owner_retirements'][owner['ownerId']]
    assert retirement['proof']['activeProcesses'] == 0
    assert value['queue_paused'] and not value['offline_owner'].get('externalTakeover')
    if action == 'restart':
        # Exactly one replacement provider startup for simultaneous requests.
        _wait_for(lambda: len([r for r in runtime.fake_records()
                              if r['kind'] == 'init' and r['cwd'] == s['workdir']]) == before + 1)
        inits = [r for r in runtime.fake_records() if r['kind'] == 'init' and r['cwd'] == s['workdir']]
        assert len(inits) == before + 1, inits
    print('TAKEOVER_HTTP', json.dumps({'port': int(runtime.base_url.rsplit(':', 1)[1]),
          'action': action, 'closed': closed, 'process': record, 'proof': retirement['proof'],
          'dataRoot': str(runtime.data_root), 'responses': outcomes}))


def test_browser_empty_to_established_and_failed_kill_is_visible(runtime, tmp_path):
    from playwright.sync_api import sync_playwright, expect
    s = _create(runtime, 'takeover-browser')
    sid = s['id']
    _spawn(runtime, sid)
    _wait_for(lambda: runtime.request('GET', f'/api/sessions/{sid}').get('cliSessionId'))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={'width': 1440, 'height': 960})
            page.on('pageerror', lambda error: print('BROWSER_ERROR', str(error)))
            page.goto(runtime.base_url + '/react/', wait_until='networkidle')
            page.get_by_text('takeover-browser', exact=True).first.click()
            top = page.get_by_test_id('topbar')
            unavailable = top.get_by_title('CLI resume data is not available yet', exact=False)
            expect(unavailable).to_be_disabled()
            _enqueue(runtime, sid, 'establish-native-session', 'first')
            _wait_for(lambda: durable(runtime, sid)['last_result'])
            takeover = top.get_by_title('Takeover', exact=True)
            expect(takeover).to_be_enabled()
            paused(runtime, sid, True)
            takeover.click()
            _wait_for(lambda: durable(runtime, sid)['offline_owner'].get('externalTakeover'))
            # Browser reload must preserve controls for a detached external owner.
            page.reload(wait_until='networkidle')
            page.get_by_text('takeover-browser', exact=True).first.click()
            (runtime.data_root / 'fail-stop').touch()
            page.on('dialog', lambda dialog: dialog.accept())
            top.get_by_title('Kill worker', exact=True).click()
            expect(page.get_by_text('Worker/Takeover stop failed', exact=False).first).to_be_visible()
            expect(top.get_by_label('Illegal offline: previous execution did not finalize safely')).to_be_visible()
            value = durable(runtime, sid)
            assert value['illegal_batches'] and value['offline_owner']['externalTakeover']
            assert runtime.request('GET', f'/api/sessions/{sid}')['workerStatus'] == 'error'
            page.screenshot(path=str(tmp_path / 'takeover-kill-illegal.png'))
            (runtime.data_root / 'fail-stop').unlink()
            top.get_by_title('Kill worker', exact=True).click()
            expect(page.get_by_text('Worker stopped', exact=True)).to_be_visible()
            assert not durable(runtime, sid)['offline_owner'].get('externalTakeover')
            print('TAKEOVER_BROWSER', runtime.base_url, browser.version, str(tmp_path))
        finally:
            browser.close()
