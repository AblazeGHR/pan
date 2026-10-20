"""Filtered MCP history reads against real storage and HTTP handlers."""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from packages.core import session as store, history_read
from packages.web import server
from packages.mcp import server as mcp


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.add_api_route('/api/sessions/{session_id}', server.api_get_session, methods=['GET'])
    app.add_api_route('/api/sessions/{session_id}/messages/{message_id}', server.api_session_message_get, methods=['GET'])
    client = TestClient(app)
    monkeypatch.setattr(mcp, '_api', lambda method, path: client.request(method, path).json())
    monkeypatch.delenv('PAN_AGENT_SESSION_ID', raising=False)
    return client


def mixed():
    s = store.create('mixed', adapter='codex')
    for role, content in [
        ('user', 'u0'), ('thinking', 'PRIVATE_THINKING'),
        ('tool', 'Command({"command":"echo hi","output":"PRIVATE_OUTPUT","nested":{"result":"PRIVATE_OUTPUT"}})'),
        ('assistant', 'a0'), ('tool', 'opaque PRIVATE_OUTPUT'),
        ('user', 'u1'), ('thinking', 'PRIVATE_THINKING'), ('assistant', 'a1'),
    ]:
        store.append_history(s, {'role': role, 'content': content,
            'parts': [{'text': 'PRIVATE_OUTPUT PRIVATE_THINKING'}],
            'rawContent': 'PRIVATE_OUTPUT PRIVATE_THINKING', 'output': 'PRIVATE_OUTPUT'})
    store.save_full(s)
    return s


@pytest.mark.parametrize('cold', [False, True])
def test_default_and_filtered_limit_paging(client, cold):
    s = mixed()
    expected = [r['messageId'] for r in s.history if r['role'] in ('user', 'assistant', 'tool')]
    if cold:
        store._cache.clear()
    before = None
    pages = []
    while True:
        page = mcp.session_get(s.id, limit=2, before=before)
        assert len(page['history']) <= 2
        assert page['historyTotal'] == 8
        assert page['historyFilteredTotal'] == 6
        assert 'PRIVATE_OUTPUT' not in json.dumps(page)
        assert 'PRIVATE_THINKING' not in json.dumps(page)
        pages.insert(0, page['history'])
        before = page['nextBefore']
        if before is None:
            break
    assert [r['messageId'] for p in pages for r in p] == expected
    if cold:
        assert store.get_cached(s.id) is None or not store.get_cached(s.id)._history_loaded
    user = mcp.session_get(s.id, limit=1, roles=['user'])
    assert [r['content'] for r in user['history']] == ['u1']
    assert user['nextBefore'] == 5
    assert mcp.session_get(s.id, limit=1, roles=['user'], before=5)['history'][0]['content'] == 'u0'
    assert mcp.session_get(s.id, roles=[])['history'] == []
    assert len(mcp.session_get(s.id)['history']) == 6  # legacy limit=0


@pytest.mark.parametrize('cold', [False, True])
def test_single_identity_optins_and_session_isolation(client, cold):
    s = mixed()
    tool, thinking = s.history[2]['messageId'], s.history[1]['messageId']
    other = store.create('other')
    if cold:
        store._cache.clear()
    row = mcp.session_message_get(s.id, tool)['message']
    assert row['messageId'] == tool and row['content'] == 'Command({"command":"echo hi"})'
    assert 'PRIVATE_OUTPUT' not in json.dumps(row)
    assert 'PRIVATE_OUTPUT' in mcp.session_message_get(s.id, tool, tool_output='include_output')['message']['content']
    assert mcp.session_message_get(s.id, thinking)['error']['code'] == 'message_filtered'
    assert mcp.session_message_get(s.id, thinking, roles=['thinking'])['message']['content'] == 'PRIVATE_THINKING'
    assert mcp.session_message_get(other.id, tool)['error']['code'] == 'message_not_found'
    assert mcp.session_message_get(s.id, 'legacy:0')['error']['code'] == 'invalid_message_id'
    assert mcp.session_message_get('ses_0000000000000000', tool)['error']['code'] == 'session_not_found'
    selected = mcp.session_get(s.id, roles=['thinking'], limit=1)
    assert selected['history'][0]['role'] == 'thinking'
    assert 'PRIVATE_OUTPUT' not in json.dumps(selected)


def test_legacy_no_generated_identity_or_read_writes(client):
    s = store.create('legacy')
    path = store._path(s.id)
    data = json.loads(path.read_text(encoding='utf-8'))
    data['history'] = [{'role':'user', 'content':'old', 'messageId':'native-id'}]
    path.write_text(json.dumps(data), encoding='utf-8')
    store._history_path(s.id).unlink(missing_ok=True)
    store._cache.clear()
    original = path.read_bytes()
    page = mcp.session_get(s.id, limit=1)
    row = page['history'][0]
    assert row['identityKind'] == 'legacy_unaddressable' and 'messageId' not in row
    assert mcp.session_message_get(s.id, 'native-id')['error']['code'] == 'invalid_message_id'
    assert path.read_bytes() == original


@pytest.mark.parametrize('tool', [mcp.session_get, mcp.session_message_get])
def test_managed_checks_before_read(monkeypatch, tool):
    monkeypatch.setattr(mcp, '_caller_identity', lambda: {'id':'manager', 'panAccess':{'restrictToManaged':True}, 'managed':['allowed']})
    calls = []
    monkeypatch.setattr(mcp, '_api', lambda *args: calls.append(args) or {})
    args = ('blocked',) if tool is mcp.session_get else ('blocked', 'pan:' + 'a'*32)
    assert tool(*args)['error']['code'] == 'permission_denied'
    assert calls == []
    args = ('allowed',) if tool is mcp.session_get else ('allowed', 'pan:' + 'a'*32)
    tool(*args)
    assert len(calls) == 1


@pytest.mark.parametrize('content', [
    'Tool({"output":"SECRET"})', 'Tool({"args":{"result":"SECRET"}})',
    'Tool({"command":"ok","output":"SECRET"})', 'Tool({"command":"broken SECRET',
    'SECRET', 'Tool({"result":"SECRET"})\nSECRET',
])
def test_command_only_fail_closed(content):
    row = history_read.project({'role':'tool', 'content':content, 'parts':[{'text':'SECRET'}]}, 0, {'tool'}, 'command_only')
    assert 'SECRET' not in json.dumps(row)


def test_invalid_options(client):
    s = mixed()
    assert mcp.session_get(s.id, roles=['bad'])['error']['code'] == 'invalid_params'
    assert mcp.session_get(s.id, tool_output='bad')['error']['code'] == 'invalid_params'
    assert mcp.session_get(s.id, limit=-1)['error']['code'] == 'invalid_params'


def test_duplicate_identity_refused(client):
    s = mixed()
    identity = s.history[0]['messageId']
    s.history[3]['messageId'] = identity
    assert mcp.session_message_get(s.id, identity)['error']['code'] == 'message_not_found'


def test_large_cold_stream_is_bounded(client, monkeypatch):
    s = store.create('large')
    path = store._history_path(s.id)
    path.write_text(''.join(json.dumps({'role':'thinking' if i % 2 else 'user',
        'content':str(i), 'messageId':'pan:' + format(i, '032x')}) + '\n' for i in range(20000)), encoding='utf-8')
    store._cache.clear()
    monkeypatch.setattr(store, '_from_data_with_history', lambda *a: pytest.fail('must not hydrate'))
    page = mcp.session_get(s.id, limit=3, roles=['user'])
    assert [r['content'] for r in page['history']] == ['19994','19996','19998']
    assert page['historyFilteredTotal'] == 10000
    assert store.get_cached(s.id) is None or not store.get_cached(s.id)._history_loaded
    assert len(mcp.session_get(s.id, limit=1000)['history']) == 200


def test_real_http_and_mcp_protocol(monkeypatch):
    import asyncio
    import socket
    import threading
    import time
    import uvicorn
    from mcp.shared.memory import create_connected_server_and_client_session

    s = mixed()
    target = s.history[2]['messageId']
    manager = store.create('manager')
    manager.restrict_to_managed = True
    manager.managed = [s.id]
    store.save_full(manager)
    outsider = store.create('outsider')
    monkeypatch.setenv('PAN_AGENT_SESSION_ID', manager.id)
    # Use real application routes with no production lifespan/background jobs.
    app = FastAPI()
    for route in server.app.routes:
        if getattr(route, 'path', '') in (
            '/api/sessions/{session_id}',
            '/api/sessions/{session_id}/messages/{message_id}',
        ):
            app.router.routes.append(route)
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    assert port not in (8767, 8768)
    monkeypatch.setattr(mcp, '_pan_api_url', f'http://127.0.0.1:{port}')
    http_server = uvicorn.Server(uvicorn.Config(app, log_level='error', lifespan='off'))
    thread = threading.Thread(target=http_server.run, kwargs={'sockets':[sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not http_server.started and thread.is_alive() and time.monotonic() < deadline:
            thread.join(.01)
        assert http_server.started

        async def exercise():
            async with create_connected_server_and_client_session(mcp.mcp) as session:
                tools = {t.name: t for t in (await session.list_tools()).tools}
                assert 'roles' in tools['session_get'].inputSchema['properties']
                assert 'tool_output' in tools['session_message_get'].inputSchema['properties']
                async def call(name, args):
                    result = await session.call_tool(name, args)
                    assert not result.isError
                    return json.loads(next(c.text for c in result.content if c.type == 'text'))
                result = await call('session_get', {'session_id':s.id, 'limit':2})
                assert len(result['history']) == 2
                assert 'PRIVATE_THINKING' not in json.dumps(result)
                result = await call('session_message_get', {'session_id':s.id, 'message_id':target})
                assert result['message']['content'] == 'Command({"command":"echo hi"})'
                result = await call('session_message_get', {'session_id':s.id, 'message_id':target, 'tool_output':'include_output'})
                assert 'PRIVATE_OUTPUT' in result['message']['content']
                for name, args in (
                    ('session_get', {'session_id':outsider.id}),
                    ('session_message_get', {'session_id':outsider.id, 'message_id':target}),
                ):
                    assert (await call(name, args))['error']['code'] == 'permission_denied'
        asyncio.run(exercise())
        print(f'Owned HTTP listener 127.0.0.1:{port}; MCP initialize/list_tools/call_tool passed')
    finally:
        http_server.should_exit = True
        thread.join(5)
        sock.close()
        assert not thread.is_alive()
    with socket.socket() as probe:
        assert probe.connect_ex(('127.0.0.1', port)) != 0


def test_append_does_not_move_older_page_and_sparse_filters(client):
    s = mixed()
    first = mcp.session_get(s.id, limit=1, roles=['user'])
    store.append_history(s, {'role':'user', 'content':'new'})
    older = mcp.session_get(s.id, limit=1, roles=['user'], before=first['nextBefore'])
    assert older['history'][0]['content'] == 'u0'
    assert older['nextBefore'] is None
    assert older['historyFilteredTotal'] == 3


def test_last_result_and_summary_cannot_bypass_body_projection(client):
    s = mixed()
    s.last_result = {'status':'done', 'result':'final', 'thinking':'PRIVATE_THINKING',
                     'parts':[{'text':'PRIVATE_OUTPUT'}], 'output':'PRIVATE_OUTPUT'}
    result = mcp.session_get(s.id, limit=2)
    assert result['lastResult'] == {'status':'done', 'result':'final'}
    assert 'PRIVATE_OUTPUT' not in json.dumps(result)
    result = mcp.session_get(s.id, roles=['user'], limit=1)
    assert result['lastResult'] == {'status':'done'}
    assert 'lastAssistantPreview' not in result
    s.last_result['result'] = {'parts':[{'text':'PRIVATE_OUTPUT'}]}
    assert 'result' not in mcp.session_get(s.id, limit=1)['lastResult']


@pytest.mark.parametrize('role', ['tool', 'thinking'])
def test_auxiliary_only_history_has_no_default_preview_leak(client, role):
    s = store.create('auxiliary-only')
    store.append_history(s, {'role':role, 'content':'PRIVATE_AUXILIARY'})
    store.save_full(s)
    store._cache.clear()
    result = mcp.session_get(s.id, limit=5)
    assert 'PRIVATE_AUXILIARY' not in json.dumps(result)
    assert bool(result['history']) == (role == 'tool')


def test_cold_mutation_is_explicit_error(client, monkeypatch):
    from contextlib import contextmanager
    s = mixed()
    store._cache.clear()
    original = server._history_search_scan_source
    @contextmanager
    def changed(session):
        with original(session) as source:
            yield source
            with store._history_path(s.id).open('ab') as handle:
                handle.write(b'{"role":"user","content":"appended"}\n')
    monkeypatch.setattr(server, '_history_search_scan_source', changed)
    assert mcp.session_get(s.id, limit=2)['error']['code'] == 'history_changed'


def test_cold_legacy_jsonl_crash_tail_and_ambiguous_id(client):
    s = mixed()
    identity = s.history[0]['messageId']
    path = store._history_path(s.id)
    with path.open('ab') as handle:
        handle.write(json.dumps({'role':'user', 'content':'legacy'}).encode() + b'\n')
        handle.write(json.dumps({'role':'assistant', 'content':'duplicate', 'messageId':identity}).encode() + b'\n')
        handle.write(b'{broken')
    store._cache.clear()
    original = path.read_bytes()
    result = mcp.session_get(s.id, limit=1, roles=['user'])
    assert result['history'][0]['content'] == 'legacy'
    assert 'messageId' not in result['history'][0]
    assert result['historyTotal'] == 10
    assert mcp.session_message_get(s.id, identity)['error']['code'] == 'message_not_found'
    assert path.read_bytes() == original
