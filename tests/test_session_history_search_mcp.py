"""Agent search reuses indexed matching over safe projected tool text."""
import asyncio
import json
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from packages.core import session as store
from packages.mcp import server as mcp
from packages.web import server


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.add_api_route('/api/history/search', server.api_history_search, methods=['GET'])
    app.add_api_route('/api/sessions/{session_id}', server.api_get_session, methods=['GET'])
    app.add_api_route('/api/sessions/{session_id}/messages/{message_id}', server.api_session_message_get, methods=['GET'])
    client = TestClient(app)
    monkeypatch.setattr(mcp, '_api', lambda method, path: client.request(method, path).json())
    monkeypatch.delenv('PAN_AGENT_SESSION_ID', raising=False)
    return client


def mixed():
    s = store.create('search', adapter='codex')
    for role, content in (
        ('user', 'needle user'), ('assistant', 'NEEDLE assistant'),
        ('tool', 'Command({"command":"echo needle","output":"HIDDEN_OUTPUT"})'),
        ('tool', 'Command({"command":"echo other","output":"needle OUTPUT_ONLY"})'),
        ('thinking', 'needle HIDDEN_THINKING'),
    ):
        store.append_history(s, {'role':role, 'content':content,
            'parts':[{'text':'HIDDEN_PARTS needle'}], 'rawContent':'HIDDEN_RAW needle'})
    store.save_full(s)
    return s


def search(s, query='needle', **options):
    return mcp.session_history_search(s.id, query, **options)


@pytest.mark.parametrize('cold', [False, True])
def test_default_matches_and_snippets_use_safe_projection(client, cold):
    s = mixed()
    expected = [r['messageId'] for r in s.history[:3]]
    if cold:
        store._cache.clear()
        store._all_loaded = False
    result = search(s)
    assert [h['messageId'] for h in result['hits']] == expected
    assert [h['role'] for h in result['hits']] == ['user','assistant','tool']
    assert not result['hasMore'] and result['nextCursor'] is None
    assert 'HIDDEN_' not in json.dumps(result) and 'OUTPUT_ONLY' not in json.dumps(result)
    assert search(s, 'OUTPUT_ONLY')['hits'] == []
    assert search(s, 'HIDDEN_OUTPUT')['hits'] == []
    assert search(s, 'HIDDEN_PARTS')['hits'] == []
    assert search(s, 'HIDDEN_THINKING')['hits'] == []
    assert search(s, roles=[])['hits'] == []
    assert search(s, '  ')['hits'] == []
    assert search(s, roles=['thinking'])['hits'][0]['role'] == 'thinking'
    for hit in result['hits']:
        assert mcp.session_message_get(s.id, hit['messageId'])['message']['messageId'] == hit['messageId']
    raw = search(s, 'OUTPUT_ONLY', roles=['tool'], tool_output='include_output')
    assert len(raw['hits']) == 1 and 'OUTPUT_ONLY' in raw['hits'][0]['snippet']
    # Enabling the raw partition must not contaminate later projected queries.
    assert search(s, 'OUTPUT_ONLY')['hits'] == []
    assert len(search(s)['hits']) == 3


def test_pagination_binds_session_query_roles_limit_and_projection(client):
    s, other = mixed(), mixed()
    first = search(s, limit=1)
    hits = first['hits'][:]
    cursor = first['nextCursor']
    while cursor:
        page = search(s, limit=1, cursor=cursor)
        hits.extend(page['hits'])
        cursor = page['nextCursor']
    assert [h['messageIndex'] for h in hits] == [0,1,2]
    assert len({h['messageId'] for h in hits}) == 3
    for changed in ({'query':'other'}, {'roles':['tool']}, {'limit':2},
                    {'tool_output':'include_output'}):
        args = {'query':'needle', 'limit':1, 'cursor':first['nextCursor'], **changed}
        assert search(s, **args)['detail']['code'] == 'history_search_cursor_expired'
    assert search(other, limit=1, cursor=first['nextCursor'])['detail']['code'] == 'history_search_cursor_expired'
    assert search(s, limit=1, cursor=first['nextCursor']+'x')['detail']['code'] == 'history_search_cursor_expired'
    store.append_history(s, {'role':'user', 'content':'needle new'})
    store.save(s)
    assert search(s, limit=1, cursor=first['nextCursor'])['detail']['code'] == 'history_search_cursor_expired'


def test_permissions_are_rechecked_before_each_search(client, monkeypatch):
    s, other = mixed(), mixed()
    manager = store.create('manager')
    manager.restrict_to_managed = True
    manager.managed = [s.id]
    store.save_full(manager)
    monkeypatch.setenv('PAN_AGENT_SESSION_ID', manager.id)
    first = search(s, limit=1)
    assert first['hits']
    assert search(other)['error']['code'] == 'permission_denied'
    manager.managed = []
    store.save(manager)
    assert search(s, limit=1, cursor=first['nextCursor'])['error']['code'] == 'permission_denied'
    # Authorization wins even over malformed options/cursors.
    assert search(s, roles=['bad'], cursor='broken')['error']['code'] == 'permission_denied'


def test_existing_index_cache_append_proof_and_mode_coexistence(client, monkeypatch):
    s = mixed()
    assert len(search(s)['hits']) == 3
    assert len(search(s, tool_output='include_output')['hits']) == 4
    path = store.SESSION_DIR.parent / 'history_search.sqlite3'
    with sqlite3.connect(path) as db:
        original = db.execute('SELECT rowid FROM history_search_messages WHERE session_id=? AND role=?', (s.id,'tool_command')).fetchall()
        assert db.execute('SELECT indexed_roles FROM history_search_sessions WHERE session_id=?', (s.id,)).fetchone()[0] == 'user,assistant,tool,tool_command'
    store._cache.clear()
    store._all_loaded = False
    with monkeypatch.context() as scoped:
        scoped.setattr(server, '_load_history_search_snapshot', lambda *a: pytest.fail('unchanged index must be reused'))
        result = search(s)
        assert len(result.get('hits', [])) == 3, result
    s = store.get(s.id)
    store.append_history(s, {'role':'tool', 'content':'Command({"command":"needle appended","output":"HIDDEN_OUTPUT"})'})
    store.save(s)
    assert len(search(s)['hits']) == 4
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT rowid FROM history_search_messages WHERE session_id=? AND role=? ORDER BY rowid LIMIT 2', (s.id,'tool_command')).fetchall() == original
        for table in ('history_search_tool_fts','history_search_tool_command_fts'):
            db.execute(f"INSERT INTO {table}({table}, rank) VALUES ('integrity-check', 1)")
    assert len(search(s, tool_output='include_output')['hits']) == 5


def test_legacy_ids_are_skipped_without_history_writes(client):
    s = mixed()
    path = store._history_path(s.id)
    with path.open('ab') as handle:
        handle.write(b'{"role":"user","content":"LEGACY_ONLY","messageId":"native-item"}\n')
    # Persist a consistent canonical total hint, without assigning a new ID.
    meta = store._path(s.id)
    data = json.loads(meta.read_text(encoding='utf-8'))
    data['summary_projection']['history_total'] += 1
    meta.write_text(json.dumps(data), encoding='utf-8')
    store._cache.clear()
    store._all_loaded = False
    before = (meta.read_bytes(), path.read_bytes())
    assert search(s, 'LEGACY_ONLY')['hits'] == []
    assert (meta.read_bytes(), path.read_bytes()) == before


def test_validation_bounds_and_missing_session(client):
    s = store.create('bounded')
    for i in range(130):
        store.append_history(s, {'role':'user', 'content':'prefix '*100 + 'needle' + ' suffix'*100})
    store.save_full(s)
    result = search(s, limit=10000)
    assert result['limit'] == 100 and len(result['hits']) == 100
    assert all(len(h['snippet']) <= 92 for h in result['hits'])
    assert search(s, roles=['bad'])['error']['code'] == 'invalid_params'
    assert search(s, tool_output='bad')['error']['code'] == 'invalid_params'
    assert 'detail' in search(s, 'a'*513)
    assert mcp.session_history_search('', 'needle')['error']['code'] == 'invalid_params'
    assert mcp.session_history_search('ses_0000000000000000', 'needle')['error'] == 'Session not found'


def test_old_http_defaults_and_disposable_v3_upgrade(client):
    s = mixed()
    raw = client.get('/api/history/search', params={'sessionId':s.id, 'q':'OUTPUT_ONLY', 'roles':'tool'}).json()
    assert len(raw['hits']) == 1  # existing HTTP compatibility mode
    path = store.SESSION_DIR.parent / 'history_search.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA user_version=3')
    original = store._history_path(s.id).read_bytes()
    assert search(s, 'OUTPUT_ONLY')['hits'] == []
    with sqlite3.connect(path) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 4
    assert store._history_path(s.id).read_bytes() == original


@pytest.mark.parametrize('raw_first', [False, True])
def test_replacement_refreshes_both_projections_and_expires_cursor(client, raw_first):
    s = mixed()
    if raw_first:
        search(s, tool_output='include_output')
    first = search(s, limit=1)
    replacement = [dict(r) for r in s.history]
    replacement[3]['content'] = 'Command({"command":"echo OUTPUT_ONLY","output":"needle hidden"})'
    store.replace_history(s, replacement)
    store.save_full(s)
    assert search(s, limit=1, cursor=first['nextCursor'])['detail']['code'] == 'history_search_cursor_expired'
    assert len(search(s, 'OUTPUT_ONLY')['hits']) == 1
    assert len(search(s)['hits']) == 3
    assert len(search(s, tool_output='include_output')['hits']) == 4


def test_real_http_and_mcp_protocol(monkeypatch, record_property):
    import os
    import socket
    import threading
    import time
    import uvicorn
    from mcp.shared.memory import create_connected_server_and_client_session

    s, other = mixed(), mixed()
    manager = store.create('manager')
    manager.restrict_to_managed = True
    manager.managed = [s.id]
    store.save_full(manager)
    monkeypatch.setenv('PAN_AGENT_SESSION_ID', manager.id)
    app = FastAPI()
    for route in server.app.routes:
        if getattr(route, 'path', '') in ('/api/history/search', '/api/sessions/{session_id}',
                                         '/api/sessions/{session_id}/messages/{message_id}'):
            app.router.routes.append(route)
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    assert port not in (8767,8768)
    record_property('owned_http_port', port)
    record_property('owned_http_pid', os.getpid())
    monkeypatch.setattr(mcp, '_pan_api_url', f'http://127.0.0.1:{port}')
    http_server = uvicorn.Server(uvicorn.Config(app, lifespan='off', log_level='error', ws='none'))
    thread = threading.Thread(target=http_server.run, kwargs={'sockets':[sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not http_server.started and thread.is_alive() and time.monotonic() < deadline:
            thread.join(.01)
        assert http_server.started
        async def exercise():
            async with create_connected_server_and_client_session(mcp.mcp) as protocol:
                tools = {t.name:t for t in (await protocol.list_tools()).tools}
                assert 'session_history_search' in tools
                schema = tools['session_history_search'].inputSchema
                assert {'session_id','query'} <= set(schema['required'])
                assert schema['properties']['tool_output']['default'] == 'command_only'
                async def call(**args):
                    result = await protocol.call_tool('session_history_search', {'session_id':s.id, 'query':'needle', **args})
                    assert not result.isError
                    return json.loads(next(c.text for c in result.content if c.type == 'text'))
                first = await call(limit=1)
                second = await call(limit=1, cursor=first['nextCursor'])
                assert first['hits'][0]['messageIndex'] == 0
                assert second['hits'][0]['messageIndex'] == 1
                assert (await call(query='OUTPUT_ONLY'))['hits'] == []
                assert len((await call(query='OUTPUT_ONLY', tool_output='include_output'))['hits']) == 1
                assert (await call(session_id=other.id))['error']['code'] == 'permission_denied'
                assert (await call(limit=1, cursor=first['nextCursor'], tool_output='include_output'))['detail']['code'] == 'history_search_cursor_expired'
                assert len((await call(roles=['thinking']))['hits']) == 1
        asyncio.run(exercise())
    finally:
        http_server.should_exit = True
        thread.join(5)
        sock.close()
        assert not thread.is_alive()
    with socket.socket() as probe:
        assert probe.connect_ex(('127.0.0.1', port)) != 0
