"""Opt-in hy3 production Worker + HTTP/WS + built Chromium UI proof.

One private Session/task; direct, queue and self-queue inputs share that run.
Invoke through an owned hidden background Job. No protected ports or lifespan.
"""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


async def main():
    # Fail before any CLI launch if the validation environment is incomplete.
    from playwright.async_api import async_playwright
    assert (ROOT / 'packages/web/dist/index.html').is_file(), 'Build the frontend first'
    base = ROOT / 'research/cbc_steer' / ('production-' + uuid.uuid4().hex[:10])
    for name in ('profile', 'cache', 'tmp', 'cwd', 'sessions'):
        (base / name).mkdir(parents=True, exist_ok=True)
    os.environ.update(CODEBUDDY_CONFIG_DIR=str(base / 'profile'), XDG_CACHE_HOME=str(base / 'cache'),
                      TEMP=str(base / 'tmp'), TMP=str(base / 'tmp'), DISABLE_TELEMETRY='1', DISABLE_GALILEO='1')
    for key in ('CODEBUDDY_API_KEY', 'CODEBUDDY_AUTH_TOKEN', 'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN'):
        os.environ.pop(key, None)
    from packages.core import session as sess, worker
    from packages.core.adapters.cbc.adapter import CbcAdapter
    from packages.core.terminal.identity import probe_process, identity_json
    sess.SESSION_DIR = base / 'sessions'
    sess._cache.clear(); sess._all_loaded = True
    worker._DEFAULTS_INITIALIZED = True
    worker._WORKER_IDLE_SEC = 600
    native_sid = str(uuid.uuid4())
    # Isolation-only argv, leaving production wire encoding, Worker, routes,
    # receipt handling, queue, self-queue and frontend implementation intact.
    CbcAdapter.mcp_args = lambda self, s: ['--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
        '--setting-sources', '', '--session-id', native_sid, '--tools', 'Bash',
        '--settings', '{"trustAll":true,"autoUpdates":false,"alwaysThinkingEnabled":false}']
    CbcAdapter.enrich_after_result = lambda self, s: None
    argv = CbcAdapter().resolved_cli_argv()
    entry = Path(argv[-1]); bundle = entry.parent.parent / 'dist/codebuddy-headless.js'
    facts = {'base': str(base), 'cliEntry': str(entry), 'headlessSHA256': hashlib.sha256(bundle.read_bytes()).hexdigest(),
             'model': 'hy3', 'nativeSessionId': native_sid, 'events': [], 'wire': [], 'http': []}
    version = await asyncio.create_subprocess_exec(*argv, '--version', stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT, cwd=base / 'cwd')
    try:
        version_text = (await asyncio.wait_for(version.communicate(), 12))[0].decode('utf-8', 'replace')
        facts['cliVersion'] = re.search(r'\d+\.\d+\.\d+', version_text).group()
        assert version.returncode == 0
    finally:
        if version.returncode is None:
            version.kill(); await version.wait()
    from packages.web import server as srv
    srv.DATA_DIR = base; srv.WORKDIRS_DIR = base / 'cwd'; srv.ATTACHMENTS_DIR = base / 'attachments'
    tool_started = asyncio.Event(); result_ready = asyncio.Event()
    async def observe(event):
        facts['events'].append(event)
        if event.get('type') == 'worker.stream':
            payload = event.get('event', {})
            if any(item.get('type') == 'tool_use' for item in (payload.get('message') or {}).get('content', []) if isinstance(item, dict)):
                tool_started.set()
        if event.get('type') == 'worker.result': result_ready.set()
        await srv.broadcast(event)
    worker.set_broadcaster(observe)
    sid = 'ses-cbc-steer-' + uuid.uuid4().hex
    s = sess.Session(id=sid, name='Isolated CBC stream Steer proof', adapter='cbc', model='hy3',
                     permission_mode='bypassPermissions', workdir=str(base / 'cwd'),
                     adapter_config={'output_mode': 'stream'})
    sess._cache[sid] = s; sess.save(s)
    facts['sessionId'] = sid
    listener = socket.socket(); listener.bind(('127.0.0.1', 0))
    port = listener.getsockname()[1]; assert port not in (8767, 8768)
    import uvicorn
    http_server = uvicorn.Server(uvicorn.Config(srv.app, host='127.0.0.1', port=port,
                                               lifespan='off', access_log=False, log_level='warning',
                                               timeout_graceful_shutdown=2))
    server_task = asyncio.create_task(http_server.serve(sockets=[listener]))
    w = browser = None
    try:
        w = await worker.create_worker(sid); assert isinstance(w, worker.Worker), w
        identity = probe_process(w.process.pid).identity; assert identity
        facts['identityBefore'] = identity_json(identity)
        real_write = w.process.stdin.write
        def trace_write(data):
            try:
                value = json.loads(data)
                if value.get('type') == 'control_request': facts['wire'].append(value)
            except ValueError: pass
            return real_write(data)
        w.process.stdin.write = trace_write
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            context = await browser.new_context(viewport={'width': 1440, 'height': 1000})
            page = await context.new_page()
            page.set_default_timeout(7000)
            facts['pageErrors'] = []
            page.on('pageerror', lambda error: facts['pageErrors'].append(str(error)))
            await page.goto(f'http://127.0.0.1:{port}/react/?session={sid}')
            composer = page.locator('[contenteditable="true"][data-placeholder]').first
            await composer.wait_for(timeout=25000)
            # Fresh browser state starts collapsed. CSS grid clipping does not
            # make descendant is_visible() false, so open via the real toggle.
            await page.locator('#send-queue-button').click()
            await composer.fill('Isolated protocol proof. Use Bash once to run python -c "import time; time.sleep(30)". '
                                'Then reply BASELINE_19 only. Do not read files, credentials, environment, history or settings.')
            async with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith(f'/sessions/{sid}/queue')) as sent:
                await page.get_by_role('button', name='Send', exact=True).click()
            initial = await (await sent.value).json(); assert initial.get('ok'), initial
            await asyncio.wait_for(tool_started.wait(), 30)
            run = w._steer_run_id; assert run
            facts['originalRunId'] = run; facts['originalTaskId'] = w._current_task_id
            await composer.fill('For this current task, replace the final answer with one line including DIRECT_CBC_73 and all markers subsequently supplied while running.')
            async with page.expect_response(lambda response: response.url.endswith(f'/sessions/{sid}/worker/steer')) as direct:
                await page.get_by_role('button', name='Steer', exact=True).click()
            direct_response = await direct.value
            facts['http'].append({'kind': 'direct-ui', 'response': await direct_response.json()})
            assert facts['http'][-1]['response'].get('outcome') == 'accepted'
            assert facts['http'][-1]['response'].get('consumption') == 'unconfirmed'
            await composer.fill('Also include QUEUE_CBC_73 in the current task final answer.')
            async with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith(f'/sessions/{sid}/queue')) as queued:
                await page.get_by_role('button', name='Send', exact=True).click()
            queue_response = await (await queued.value).json()
            assert queue_response.get('ok'), queue_response
            facts['queuedItemId'] = queue_response['item']['queueItemId']
            async with page.expect_response(lambda response: response.url.endswith(f"/queue/{facts['queuedItemId']}/steer")) as queue_steer:
                await page.get_by_role('button', name='Steer queued message 1', exact=True).click()
            facts['http'].append({'kind': 'queue-ui', 'response': await (await queue_steer.value).json()})
            assert facts['http'][-1]['response'].get('ok')
            own = await context.request.post(f'http://127.0.0.1:{port}/api/sessions/{sid}/queue',
                data={'text': 'Also include SELF_CBC_73 in the current task final answer.', 'clientMessageId': 'cbc-self-proof'})
            own_item = await own.json(); assert own_item.get('ok'), own_item
            facts['selfItemId'] = own_item['item']['queueItemId']
            own_steer = await context.request.post(f'http://127.0.0.1:{port}/api/self-queue/consume',
                headers={'X-Pan-Queue-Caller': sid}, data={'itemId': facts['selfItemId']})
            facts['http'].append({'kind': 'self-queue-http', 'response': await own_steer.json()})
            assert facts['http'][-1]['response'].get('ok'), facts['http'][-1]
            assert facts['http'][-1]['response']['steer']['consumption'] == 'unconfirmed'
            force = await context.request.post(f'http://127.0.0.1:{port}/api/send', data={
                'sessionId': sid, 'text': 'Also include FORCE_CBC_73 in the current task final answer.',
                'source': 'user', 'force': True, 'clientMessageId': 'cbc-force-proof'})
            facts['http'].append({'kind': 'force-http', 'response': await force.json()})
            assert facts['http'][-1]['response']['outcome'] == 'accepted', facts['http'][-1]
            facts['forceItemId'] = facts['http'][-1]['response']['queueItemId']
            await asyncio.wait_for(result_ready.wait(), 50)
            facts['identityAfter'] = identity_json(probe_process(w.process.pid).identity)
            assert facts['identityBefore'] == facts['identityAfter']
            terminal = next(event for event in facts['events'] if event.get('type') == 'worker.result')
            facts['terminal'] = terminal
            text = str(terminal.get('result') or '')
            # Compliance with the requested wording is a model behavior check,
            # separate from native safe-point consumption of the four inputs.
            facts['modelFollowedMarkerInstruction'] = all(marker in text for marker in
                ('DIRECT_CBC_73', 'QUEUE_CBC_73', 'SELF_CBC_73', 'FORCE_CBC_73'))
            assert len(facts['wire']) == 4
            assert all(frame['request']['expected_request_id'] == run for frame in facts['wire'])
            assert all(frame['request']['session_id'] == native_sid for frame in facts['wire'])
            await sess.save_async(s)
            native = list((base / 'profile/projects').rglob(native_sid + '.jsonl')); assert len(native) == 1
            native_rows = [json.loads(line) for line in native[0].read_text(encoding='utf-8').splitlines()]
            injected = [row for row in native_rows if row.get('role') == 'user'
                        and row.get('providerData', {}).get('startsNewUserRequest') is False]
            assert len(injected) == 4 and all(row['providerData']['conversationRequestId'] == run for row in injected)
            assert native_rows[-1].get('role') == 'assistant'
            assert native_rows[-1]['providerData']['conversationRequestId'] == run
            facts['nativeHistoryPath'] = str(native[0])
            facts['nativeInjectedIds'] = [row['id'] for row in injected]
            assert not s.queue_pending and len(s.adapter_config['_steer_requests']) == 1
            for item_id in (facts['queuedItemId'], facts['selfItemId'], facts['forceItemId']):
                assert s.queue_delivery_ledger[item_id]['steer']['runId'] == run
            facts['passed'] = True
            await page.screenshot(path=str(base / 'browser.png'), full_page=True)
            await browser.close(); browser = None
    except BaseException as error:
        facts['failure'] = {'type': type(error).__name__, 'detail': str(error)}
        # async_playwright has unwound here; retain the original failure.
        raise
    finally:
        if browser: await browser.close()
        if isinstance(w, worker.Worker):
            facts['cleanupError'] = await worker.kill_worker(w.worker_id, report_abnormal=False)
            facts['processExitCode'] = w.process.returncode
        http_server.should_exit = True
        (base / 'result.json').write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding='utf-8')
        try:
            await asyncio.wait_for(server_task, 10)
        except TimeoutError:
            facts['serverCleanupTimeout'] = True
        (base / 'result.json').write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'result': str(base / 'result.json'), 'passed': facts.get('passed', False),
                          'cliVersion': facts.get('cliVersion'), 'identity': facts.get('identityBefore'),
                          'cleanupError': facts.get('cleanupError')}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    asyncio.run(main())
