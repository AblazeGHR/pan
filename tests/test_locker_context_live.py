"""Actual HTTP server + actual MCP stdio protocol, isolated profile/port."""
import asyncio
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from tests import test_real_queue_http_e2e as h

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/locker-related-context'


def test_real_stdio_http_context(tmp_path, monkeypatch):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    assert port not in {8767, 8768}
    monkeypatch.setattr(h, 'PORT', port)
    monkeypatch.setattr(h, 'SERVER_LAUNCHER', ROOT / 'tests/support/locker_context_http_server.py')
    nonce = secrets.token_hex(24)
    monkeypatch.setenv('PAN_TEST_CONTEXT_NONCE', nonce)
    monkeypatch.setenv('PAN_TEST_DISABLE_QUEUE_WATCHDOG', '1')
    rt = h._PanRuntime(tmp_path / 'profile/data')
    rt.data_root.mkdir(parents=True)
    (rt.data_root / 'config.json').write_text(json.dumps({'memory': {'enabled': False},
        'plugin_manifests': [str(ROOT/'manifest.json'), str(ROOT/'packages/mcp/manifest.json')]}), encoding='utf-8')
    OUT.mkdir(parents=True, exist_ok=True)
    proof = {'port': port, 'dataRoot': str(rt.data_root), 'providerWorkersStarted': False}
    try:
        rt.start()
        proof['identity'] = rt.request('GET', '/__test/job-inbox-identity')
        assert Path(proof['identity']['sessions']).parent == rt.data_root
        def create(name):
            workdir = rt.data_root / 'workdirs' / name; workdir.mkdir(parents=True)
            result = rt.request('POST', '/api/sessions', json={'name': name, 'adapter': 'cbc', 'mcpServers': [], 'workdir': str(workdir)})
            assert result.get('id'), result
            rt.request('PATCH', f"/api/sessions/{result['id']}/queue/paused", json={'paused': True})
            return result['id']
        ma, child = create('MA'), create('Child')
        def history(sid, rows, **fields):
            return rt.request('POST', '/__test/locker-context', headers={'x-context-fixture': nonce},
                json={'owner': sid, 'history': rows, **fields})['ids']
        ma_mid = history(ma, [{'role': 'user', 'content': 'MA question'}], managed=[child])[0]
        child_mid = history(child, [{'role': 'user', 'content': 'Child question'},
            {'role': 'user', 'source': 'report', 'content': 'Notification'},
            {'role': 'user', 'source': 'agent', 'content': 'Assign'}])[0]
        document = {'messageId': 'ordinary', 'role': 'assistant', 'content': 'Locker reply', 'ts': '2026-10-25T00:00:00Z'}
        async def invoke(sid, name, arguments):
            params = StdioServerParameters(command=sys.executable, args=['-m', 'packages.mcp.server'], cwd=str(ROOT),
                env={**os.environ, 'PAN_API_URL': rt.base_url, 'PAN_AGENT_SESSION_ID': sid})
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    result = await client.call_tool(name, arguments)
                    assert not result.isError, result
                    return json.loads(result.content[0].text)
        args = {'operation': 'create', 'owner_session_id': child, 'recipient_id': 'user', 'request_key': 'question',
            'card': {'title': 'Question', 'body': 'Body'}}
        card = asyncio.run(invoke(ma, 'interaction_card', args))['card']
        message_args = {'operation': 'create', 'message': document}
        message = asyncio.run(invoke(child, 'locker_message', message_args))['message']
        assert card['relatedMessageId'] == message['relatedMessageId'] == child_mid != ma_mid
        history(child, [{'role': 'user', 'content': 'Later user'}])
        assert asyncio.run(invoke(ma, 'interaction_card', args))['card'] == card
        assert asyncio.run(invoke(child, 'locker_message', message_args))['message'] == message
        key = (rt.data_root / 'automation/user.key').read_text(encoding='utf-8')
        auth = {'Authorization': 'Bearer ' + rt.request('POST', '/api/automation/login', json={'key': key})['token']}
        assert card in rt.request('GET', '/api/user-inbox', headers=auth)['cards']
        assert message in rt.request('GET', '/api/user-locker-messages', headers=auth)['messages']
        with sqlite3.connect(rt.data_root / 'user_inbox/cards.sqlite') as db:
            for table, item in [('cards', card), ('locker_messages', message)]:
                assert db.execute(f'SELECT related_message_id FROM {table} WHERE id=?', (item['id'],)).fetchone()[0] == child_mid
        proof.update(card=card, message=message, ownerUserId=child_mid, maUserId=ma_mid, listsAndSqlite=True, immutableRetry=True)
    finally:
        proof['ownedProcesses'] = [{'pid': pid, 'birth': birth, 'command': command} for pid, birth, command in rt._owned_processes]
        log = rt.data_root / 'pan.log'
        if log.exists():
            (OUT / 'live-server.log').write_text(log.read_text(encoding='utf-8', errors='replace'), encoding='utf-8')
        rt.stop()
        proof['portReleased'] = h._port_is_free(port)
        (OUT / 'live-evidence.json').write_text(json.dumps(proof, indent=2), encoding='utf-8')
        assert proof['portReleased']
