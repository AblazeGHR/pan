"""Existing local stdio caller bridge applied to ordinary Locker producers."""
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from packages.core import session
from packages.interactions.api import register, get_store
from packages.interactions.store import UserPrincipal
from tests.test_locker_messages import msg


def test_local_caller_source_schema_idempotency_lookup_tombstone(tmp_path):
    source = session.create('Source', workdir=str(tmp_path))
    other = session.create('Other', workdir=str(tmp_path))
    app = FastAPI(); register(app)
    client = TestClient(app, client=('127.0.0.1', 41234))
    headers = {'X-Pan-Queue-Caller': source.id}
    route = '/api/locker-message-tools/'
    for bad in ({}, {'X-Pan-Queue-Caller': 'missing'}, {**headers, 'Origin': 'http://localhost'},
                {**headers, 'Sec-Fetch-Site': 'same-origin'}, {'Authorization': 'Bearer user'}):
        assert client.post(route+'create', headers=bad, json={'message': msg()}).status_code == 403
    remote = TestClient(app, client=('192.0.2.1', 41234))
    assert remote.post(route+'create', headers=headers, json={'message': msg()}).status_code == 403
    for key, value in [('sourceSessionId', other.id), ('ownerSessionId', other.id),
                       ('recipientId', 'other'), ('operation', 'get')]:
        response = client.post(route+'create', headers=headers, json={'message': msg(), key: value})
        assert response.status_code == 422, response.text
    with get_store().db() as db:
        assert db.execute('SELECT count(*) FROM user_lockers').fetchone()[0] == 0
    document = msg(sourceSessionId=other.id, future={'keep': True})
    def create(document):
        return client.post(route+'create', headers=headers, json={'message': document})
    result = create(document).json()
    row = result['message']
    assert row['ownerSessionId'] == source.id and row['recipientId'] == 'user'
    assert row['message'] == document and row['read'] is False
    assert create(document).json() == result
    assert create({**document, 'content': 'changed'}).status_code == 409
    assert client.post(route+'get', headers=headers, json={'messageId': 'first'}).json() == result
    assert client.post(route+'get', headers={'X-Pan-Queue-Caller': other.id}, json={'messageId': 'first'}).status_code == 404
    assert not session.get(source.id).history and not session.get(source.id).queue_pending
    get_store().delete_message(UserPrincipal('user'), row['id'])
    assert create(document).status_code == 409
    assert client.post(route+'get', headers=headers, json={'messageId': 'first'}).status_code == 404


def test_mcp_transport_and_unknown_never_resends(monkeypatch):
    from packages.mcp import server
    monkeypatch.setenv('PAN_AGENT_SESSION_ID', 'source')
    monkeypatch.setattr(server, '_require_caller', lambda: ({'id': 'source'}, None))
    monkeypatch.setattr(server, '_pan_api_url', 'http://127.0.0.1:41234')
    monkeypatch.setattr(server, '_terminal_transport', 'sse')
    assert server.locker_message('create', msg())['error'] == 'local_stdio_caller_required'
    monkeypatch.setattr(server, '_terminal_transport', 'stdio')
    calls = []
    class Broken:
        def open(self, request, timeout):
            calls.append(request)
            raise TimeoutError()
    monkeypatch.setattr(server.urllib.request, 'build_opener', lambda *args: Broken())
    assert server.locker_message('create', msg())['status'] == 'unknown'
    assert len(calls) == 1
    assert server.locker_message('get', message_id='first')['status'] == 'unknown'
    assert len(calls) == 2 and calls[1].full_url.endswith('/get')
    for url in ['http://example.com', 'https://127.0.0.1', 'http://127.0.0.1/path', 'http://u@127.0.0.1']:
        monkeypatch.setattr(server, '_pan_api_url', url)
        assert server.locker_message('create', msg())['error'] == 'local_stdio_caller_required'
    assert len(calls) == 2
