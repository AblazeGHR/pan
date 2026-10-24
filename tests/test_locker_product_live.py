"""Real UserAuth + HTTP + production browser + fakeCLI + cold restart."""
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess

import httpx
import psutil
from tests import test_real_queue_http_e2e as h
from tests.test_automation_core import action, trigger

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'audit/locker-order-manual-read-mcp/live'


def test_locker_messages_cards_real_service_browser_cli_restart(tmp_path, monkeypatch):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    assert port not in {8767, 8768}
    monkeypatch.setattr(h, 'PORT', port)
    monkeypatch.setattr(h, 'SERVER_LAUNCHER', ROOT/'tests/support/locker_product_http_server.py')
    nonce = secrets.token_hex(24)
    monkeypatch.setenv('PAN_TEST_LOCKER_NONCE', nonce)
    monkeypatch.setenv('PAN_TEST_DISABLE_QUEUE_WATCHDOG', '1')
    rt = h._PanRuntime(tmp_path/'profile/data')
    rt.data_root.mkdir(parents=True)
    (rt.data_root/'config.json').write_text(json.dumps({'scheduler': {'enabled': True},
        'memory': {'enabled': False}, 'plugin_manifests': [str(ROOT/'manifest.json'), str(ROOT/'packages/mcp/manifest.json')]}), encoding='utf-8')
    OUT.mkdir(parents=True, exist_ok=True)
    proof = {'sourceSha': subprocess.check_output(['git', 'rev-parse', 'HEAD'], encoding='utf-8').strip(),
             'port': port, 'profile': str(rt.data_root), 'apiInterception': False}
    fixture_headers = {'x-locker-fixture': nonce}
    def browser(phase, fixture, key):
        fixture_path = OUT/'fixture.json'
        fixture_path.write_text(json.dumps(fixture, indent=2), encoding='utf-8')
        result = subprocess.run(['node', str(ROOT/'packages/web/e2e/locker-product-live.mjs'),
            rt.base_url, str(OUT), str(fixture_path), phase], cwd=ROOT,
            env={**os.environ, 'PAN_TEST_JOB_KEY': key}, capture_output=True, text=True, encoding='utf-8', timeout=110)
        (OUT/f'browser-{phase}.log').write_text(result.stdout+'\n'+result.stderr, encoding='utf-8')
        assert result.returncode == 0, result.stdout+'\n'+result.stderr
    try:
        rt.start()
        ident = rt.request('GET', '/__test/job-inbox-identity')
        assert Path(ident['sessions']).parent == rt.data_root
        assert Path(ident['fakeCLI']).resolve() == h.FAKE_CLI.resolve()
        proof['identity'] = ident
        key = (rt.data_root/'automation/user.key').read_text(encoding='utf-8')
        auth = {'Authorization': 'Bearer '+rt.request('POST', '/api/automation/login', json={'key': key})['token']}
        def source(name):
            directory = rt.data_root/'workdirs'/name
            directory.mkdir(parents=True)
            obj = rt.request('POST', '/api/sessions', json={'name': name.replace(' ', '-'), 'adapter': 'cbc', 'mcpServers': [], 'workdir': str(directory)})
            assert obj.get('id'), obj
            rt.request('PATCH', f"/api/sessions/{obj['id']}/queue/paused", json={'paused': True})
            return obj
        one = source('Design agent with a long dedicated Locker source name')
        two = source('Archived review source with a long name')
        empty = source('No dedicated Locker content')
        target = source('Owned Pause target')
        workspace = rt.request('POST', '/api/workspaces', json={'name': 'Workspace A'})['workspace']
        for obj in (one, two):
            assert rt.request('PUT', f"/api/sessions/{obj['id']}/workspaces", json={'workspaceIds': [workspace['id']]})['ok']
        rt.request('POST', '/api/actions/actions', json={'item': action([target['id']])})
        rt.request('POST', '/api/automation/triggers', headers=auth,
            json={'item': {**trigger('locker-live-pause'), 'eventTypes': ['pan.interaction.responded']}, 'expectedRevision': 0})
        attachment = httpx.post(rt.base_url+f"/api/sessions/{one['id']}/attachments?filename=review.txt",
            content=b'owned attachment bytes', trust_env=False).json()
        def accept(sid, mid, ts, body, parts=None):
            message = {'messageId': mid, 'role': 'assistant', 'content': body, 'ts': ts,
                'parts': parts or [{'type': 'text', 'text': body}], 'futureMetadata': {'retained': True}}
            import asyncio
            import sys
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
            async def stdio_create():
                params = StdioServerParameters(command=sys.executable,
                    args=['-m', 'packages.mcp.server'], cwd=str(ROOT),
                    env={**os.environ, 'PAN_API_URL': rt.base_url, 'PAN_AGENT_SESSION_ID': sid})
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as client:
                        await client.initialize()
                        listing = await client.list_tools()
                        tool = next(t for t in listing.tools if t.name == 'locker_message')
                        assert 'owner_session_id' not in tool.inputSchema['properties']
                        async def call(arguments):
                            result = await client.call_tool('locker_message', arguments)
                            assert not result.isError, result
                            return json.loads(result.content[0].text)
                        created = await call({'operation': 'create', 'message': message})
                        assert created['message']['ownerSessionId'] == sid
                        assert await call({'operation': 'create', 'message': message}) == created
                        assert await call({'operation': 'get', 'message_id': mid}) == created
                        forged = await client.call_tool('locker_message', {'operation': 'create', 'message': message,
                            'owner_session_id': empty['id'], 'source_session_id': empty['id'], 'recipient_id': 'other'})
                        if not forged.isError:
                            assert json.loads(forged.content[0].text) == created, 'extra arguments must never change source/recipient'

                        conflict = await call({'operation': 'create', 'message': {**message, 'content': 'changed'}})
                        assert conflict['error']['code'] == 'conflict'
                        if sid == two['id']:
                            denied = await call({'operation': 'create', 'message': {**message, 'messageId': 'foreign-attachment',
                                'parts': [{'type': 'attachment', 'attachmentId': attachment['attachmentId'], 'displayName': 'foreign.txt'}]}})
                            assert denied['error']['code'] == 'forbidden'
                        proof.setdefault('stdioProducer', []).append({'source': sid, 'messageId': mid, 'discovered': True,
                            'idempotent': True, 'lookup': True, 'conflictRejected': True, 'sourceOverrideCannotImpersonate': True,
                            'foreignAttachmentRejected': sid == two['id']})
                        return created['message']
            return asyncio.run(stdio_create())
        ordinary = accept(one['id'], 'dedicated-one', '2026-10-23T00:00:00Z', '## Ordinary Locker message\n\n**Markdown**, no Chat copy.\n\n```ts\nconst locker = true;\n```',
            [{'type': 'text', 'text': '## Ordinary Locker message\n\n**Markdown**, no Chat copy.\n\n```ts\nconst locker = true;\n```'},
             {'type': 'attachment', 'attachmentId': attachment['attachmentId'], 'displayName': 'review.txt', 'mimeType': 'text/plain'}])
        archived = accept(two['id'], 'dedicated-two', '2026-10-23T00:01:00Z', '## Archived Locker message\n\nStill readable after source deletion.')
        def card(key_name, **fields):
            return rt.request('POST', '/api/interaction-cards', json={'ownerSessionId': one['id'], 'recipientId': 'user',
                'requestKey': key_name, 'card': {'title': key_name, 'body': 'Full body retained only in Locker.', 'summaryBody': 'Summary '*60, **fields}})['card']
        approval = card('Approve real Pause', choices=[{'id': 'pause', 'label': 'Approve Pause', 'triggerRef': {'id': 'locker-live-pause', 'revision': 1}}, {'id': 'keep', 'label': 'Keep running'}])
        reply = card('Answer without choices')
        reason = card('Reject with reason')
        silent = card('Reject without reason')
        assert rt.request('GET', '/api/user-inbox', headers=auth)['cards'][0]['definition']['body'] == 'Full body retained only in Locker.'
        assert not rt.request('GET', f"/api/sessions/{one['id']}/history")['history'], 'Dedicated records must not enter Chat'
        assert empty['id'] not in [row['sessionId'] for row in rt.request('GET', '/api/user-lockers', headers=auth)['lockers']]
        page1 = rt.request('GET', '/api/user-locker-messages?limit=1', headers=auth)
        page2 = rt.request('GET', '/api/user-locker-messages?limit=1&after='+page1['nextAfter'], headers=auth)
        assert page1['messages'][0]['id'] == ordinary['id'] and page2['messages'][0]['id'] == archived['id']
        assert len(rt.request('GET', '/api/user-locker-messages', headers=auth)['messages']) == 2
        assert not rt.request('GET', f"/api/user-locker-messages/{ordinary['id']}", headers=auth)['message']['read'], 'Paging is not consumption'
        assert httpx.get(rt.base_url+'/api/user-locker-messages', trust_env=False).status_code == 401
        download = f"/api/user-locker-messages/{ordinary['id']}/attachments/{attachment['attachmentId']}"
        assert httpx.get(rt.base_url+download, headers=auth, trust_env=False).content == b'owned attachment bytes'
        assert httpx.get(rt.base_url+download, trust_env=False).status_code == 401
        assert httpx.get(rt.base_url+f"/api/user-locker-messages/{archived['id']}/attachments/{attachment['attachmentId']}", headers=auth, trust_env=False).status_code == 404
        bad = httpx.post(rt.base_url+'/__test/locker/accept', headers=fixture_headers, trust_env=False,
            json={'sourceSessionId': two['id'], 'recipientId': 'user', 'message': {**ordinary['message'], 'messageId': 'cross-source'}})
        assert bad.status_code == 403, bad.text
        fixture = {'one': one['id'], 'two': two['id'], 'workspace': workspace['id'], 'ordinary': ordinary['id'],
                   'archived': archived['id'], 'approval': approval['id'], 'reply': reply['id'], 'reason': reason['id'], 'silent': silent['id'], 'nonce': nonce}
        browser('exercise', fixture, key)
        def completed():
            runs = rt.request('GET', '/api/automation')['runs']
            return list(runs.values()) if len(runs) == 1 and next(iter(runs.values()))['status'] == 'succeeded' else None
        runs = h._wait_for(completed, label='trusted real Trigger Pause run')
        assert runs[0]['triggerId'] == 'locker-live-pause'
        assert rt.request('GET', f"/api/sessions/{target['id']}/queue")['queuePaused']
        rt.request('POST', '/api/send', json={'sessionId': target['id'], 'text': 'held-by-approved-pause', 'source': 'user'})
        assert any(row.get('text') == 'held-by-approved-pause' for row in rt.request('GET', f"/api/sessions/{target['id']}/queue")['items'])
        # Replay the original request must neither re-notify nor re-run.
        done = rt.request('GET', f"/api/user-inbox/{approval['id']}", headers=auth)['card']
        rt.request('POST', f"/api/user-inbox/{approval['id']}/submit", headers=auth,
            json={'expectedRevision': 0, 'requestId': done['response']['requestId'], 'choiceId': 'pause', 'answer': 'Proceed with isolated Pause.'})
        notices = rt.request('GET', f"/api/sessions/{one['id']}/queue")['items']
        assert len(notices) == 3, notices
        rt.request('PATCH', f"/api/sessions/{one['id']}/queue/paused", json={'paused': False})
        def consumed():
            return [row for row in h._received(rt) if 'Interaction submitted: ' in row['text'] or 'Interaction rejected: ' in row['text']]
        def delivered():
            rows = consumed()
            text = '\n'.join(row['text'] for row in rows)
            count = text.count('Interaction submitted: ') + text.count('Interaction rejected: ')
            return rows if count == 3 else None
        received = h._wait_for(delivered, timeout=30, label='fakeCLI consumes three distinct actual notifications')
        summaries = []
        import re
        for row in received:
            text = row['text']
            for match in re.finditer(r'Interaction (?:submitted|rejected): ', text):
                payload = json.JSONDecoder().raw_decode(text[match.end():])[0]
                assert len(payload['summaryBody']) <= 200 and 'body' not in payload
                assert 'Full body retained only' not in text
                summaries.append(payload)
        assert {row['cardId'] for row in summaries} == {approval['id'], reply['id'], reason['id']}
        assert len(summaries) == 3 and len({row['cardId'] for row in summaries}) == 3
        assert all(Path(row['cwd']).resolve() == Path(one['workdir']).resolve() for row in received)
        assert not rt.request('GET', f"/api/sessions/{one['id']}/queue")['items']
        history = rt.request('GET', f"/api/sessions/{one['id']}/history?limit=100")['history']
        notice_history = [row for row in history if row['role'] == 'user' and 'Interaction ' in row['content']]
        assert len(notice_history) == 1, 'The three notifications share one actual batched CLI handoff'
        assert {qid for row in notice_history for qid in row.get('queueItemIds', [])} == {row['id'] for row in notices}
        proof['queueItemIds'] = [row['id'] for row in notices]
        proof['actualCliReceived'] = received
        proof.update(notifications=summaries, fakeCliConsumedExactlyOnce=True, realPause=True, attachmentScope=True, queryPagination=True)
        rt._capture_owned_processes()
        proof['firstOwnedProcesses'] = list(rt._owned_processes)
        rt.stop(); rt.start()
        cold_auth = {'Authorization': 'Bearer '+rt.request('POST','/api/automation/login',json={'key':key})['token']}
        assert not rt.request('GET', f"/api/user-locker-messages/{archived['id']}",headers=cold_auth)['message']['read']
        assert httpx.get(rt.base_url+'/api/user-locker-messages', headers=auth, trust_env=False).status_code == 401
        browser('cold', fixture, key)
        assert len(rt.request('GET', '/api/automation')['runs']) == 1
        proof['coldRestart'] = True
        assert h._received(rt) == received, 'Cold restart must not replay notifications'
        rt._capture_owned_processes()
        proof['coldOwnedProcesses'] = list(rt._owned_processes)
    finally:
        rt._capture_owned_processes()
        captured = [*proof.get('firstOwnedProcesses', []), *proof.get('coldOwnedProcesses', []), *rt._owned_processes]
        rt.stop()
        remaining = []
        for pid, born, command in captured:
            try:
                process = psutil.Process(pid)
                if abs(process.create_time()-born) < .01 and process.status() not in {'terminated', psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD}:
                    remaining.append({'pid':pid,'born':born,'command':command})
            except psutil.Error:
                pass
        proof['remainingOwnedProcesses'] = remaining
        proof['cleanup'] = {'portReleased': h._port_is_free(port), 'ownedProcesses': rt._owned_processes,
                            'serverExited': rt.process is None or rt.process.poll() is not None}
        (OUT/'evidence.json').write_text(json.dumps(proof, indent=2), encoding='utf-8')
        if (rt.data_root/'pan.log').exists():
            (OUT/'server.log').write_text((rt.data_root/'pan.log').read_text(encoding='utf-8', errors='replace'), encoding='utf-8')
        assert proof['cleanup']['portReleased'] and proof['cleanup']['serverExited'] and not remaining
