"""HTTP surface for message bookmarks: routing, validation, projection, events.

The endpoints are registered on a bare FastAPI app (the real handler
functions, no production lifespan), so routing, methods, status codes and
body parsing are exercised for real while Session storage stays
test-isolated.
"""

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from packages.core import session as store
from packages.core import session_bookmarks as bookmarks
from packages.web import server


@pytest.fixture
def client():
    app = FastAPI()
    app.add_api_route('/api/sessions/{session_id}/bookmarks',
                      server.api_get_session_bookmarks, methods=['GET'])
    app.add_api_route('/api/sessions/{session_id}/bookmarks/{message_id}',
                      server.api_add_session_bookmark, methods=['PUT'])
    app.add_api_route('/api/sessions/{session_id}/bookmarks/{message_id}',
                      server.api_remove_session_bookmark, methods=['DELETE'])
    app.add_api_route('/api/sessions/{session_id}/history',
                      server.api_session_history, methods=['GET'])
    app.add_api_route('/api/sessions/{session_id}/navigation',
                      server.api_session_navigation, methods=['GET'])
    return TestClient(app)


def _session_with_blocks(name='api'):
    session = store.create(name, adapter='cbc')
    ids = []
    for role in ('user', 'assistant', 'tool', 'thinking'):
        store.append_history(session, {'role': role, 'content': f'{role} body block'})
        ids.append(session.history[-1]['messageId'])
    store.save_full(session)
    return session, ids


def _capture_broadcast(monkeypatch):
    events = []

    async def capture(data):
        events.append(data)

    monkeypatch.setattr(server, 'broadcast', capture)
    return events


def test_get_absent_session_returns_empty_envelope(client):
    session, _ids = _session_with_blocks('empty')
    response = client.get(f'/api/sessions/{session.id}/bookmarks')
    assert response.status_code == 200
    body = response.json()
    assert body['ok'] is True and body['revision'] == 0
    assert body['bookmarks'] == {} and body['dangling'] == []


def test_get_unknown_session_reports_not_found(client):
    response = client.get('/api/sessions/ses_0000000000000000/bookmarks')
    assert response.status_code == 200
    assert response.json()['error']['code'] == 'session_not_found'


def test_put_add_get_remove_round_trip_over_http(client, monkeypatch):
    session, ids = _session_with_blocks('http round trip')
    events = _capture_broadcast(monkeypatch)

    added = client.put(f'/api/sessions/{session.id}/bookmarks/{ids[1]}')
    assert added.status_code == 200
    body = added.json()
    assert body['ok'] is True and body['changed'] is True and body['bookmarked'] is True
    assert body['revision'] == 1 and body['target']['role'] == 'assistant'
    assert events == [{
        'type': 'bookmarks.changed', 'sessionId': session.id, 'revision': 1,
        'action': 'added', 'messageIds': [ids[1]],
    }]

    fetched = client.get(f'/api/sessions/{session.id}/bookmarks').json()
    assert set(fetched['bookmarks']) == {ids[1]}

    removed = client.delete(f'/api/sessions/{session.id}/bookmarks/{ids[1]}')
    assert removed.json()['changed'] is True and removed.json()['bookmarked'] is False
    assert events[-1]['action'] == 'removed' and events[-1]['revision'] == 2
    assert client.get(f'/api/sessions/{session.id}/bookmarks').json()['bookmarks'] == {}


def test_put_is_idempotent_and_does_not_broadcast(client, monkeypatch):
    session, ids = _session_with_blocks('http idempotent')
    events = _capture_broadcast(monkeypatch)
    assert client.put(f'/api/sessions/{session.id}/bookmarks/{ids[0]}').json()['changed'] is True
    again = client.put(f'/api/sessions/{session.id}/bookmarks/{ids[0]}')
    assert again.status_code == 200
    assert again.json()['changed'] is False and again.json()['revision'] == 1
    assert len(events) == 1
    absent = client.delete(f'/api/sessions/{session.id}/bookmarks/{ids[3]}')
    assert absent.json()['changed'] is False and len(events) == 1


def test_put_rejects_positional_legacy_and_foreign_identities(client):
    session, ids = _session_with_blocks('http identity')
    other, other_ids = _session_with_blocks('http other')
    cases = {
        f'legacy:{session.id}:epoch:0': 'invalid_message_id',
        'msg_legacy': 'invalid_message_id',
        'pan:' + 'a' * 32: 'message_not_found',
        other_ids[0]: 'message_not_found',
    }
    for message_id, code in cases.items():
        response = client.put(f'/api/sessions/{session.id}/bookmarks/{message_id}')
        assert response.status_code == 200
        assert response.json()['error']['code'] == code, message_id
    assert client.get(f'/api/sessions/{session.id}/bookmarks').json()['bookmarks'] == {}
    assert not bookmarks._path(other.id).exists()


def test_put_conflict_attaches_current_envelope(client):
    session, ids = _session_with_blocks('http conflict')
    client.put(f'/api/sessions/{session.id}/bookmarks/{ids[0]}')
    response = client.put(
        f'/api/sessions/{session.id}/bookmarks/{ids[1]}',
        json={'baseRevision': 0},
    )
    assert response.status_code == 200
    body = response.json()
    assert body['error']['code'] == 'bookmark_conflict'
    assert body['revision'] == 1 and set(body['bookmarks']) == {ids[0]}
    ok = client.put(
        f'/api/sessions/{session.id}/bookmarks/{ids[1]}', json={'baseRevision': 1})
    assert ok.json()['changed'] is True


def test_invalid_bodies_are_rejected(client):
    session, ids = _session_with_blocks('http invalid')
    for body in ({'baseRevision': '1'}, {'baseRevision': -1}, {'baseRevision': True}):
        response = client.put(
            f'/api/sessions/{session.id}/bookmarks/{ids[0]}', json=body)
        assert response.json()['error']['code'] == 'invalid_params'
    assert client.get(f'/api/sessions/{session.id}/bookmarks').json()['revision'] == 0


def test_history_and_navigation_projections_carry_the_flag(client, monkeypatch):
    session, ids = _session_with_blocks('http projection')
    client.put(f'/api/sessions/{session.id}/bookmarks/{ids[1]}')

    history = client.get(f'/api/sessions/{session.id}/history', params={'limit': 50})
    assert history.status_code == 200
    rows = history.json()['history']
    assert [row.get('bookmarked') for row in rows] == [None, True, None, None]

    navigation = client.get(f'/api/sessions/{session.id}/navigation', params={'limit': 500})
    rows = navigation.json()['history']
    assert [row.get('bookmarked') for row in rows] == [None, True, None, None]


def test_unreadable_sidecar_is_surfaced_and_history_keeps_serving(client):
    session, ids = _session_with_blocks('http damaged')
    path = bookmarks._path(session.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    damaged = b'{"version": 1, "revision": 1, "bookmarks": {"pan:'
    path.write_bytes(damaged)

    for method, url in (
        ('GET', f'/api/sessions/{session.id}/bookmarks'),
        ('PUT', f'/api/sessions/{session.id}/bookmarks/{ids[0]}'),
        ('DELETE', f'/api/sessions/{session.id}/bookmarks/{ids[0]}'),
    ):
        response = client.request(method, url)
        assert response.json()['error']['code'] == 'bookmarks_unreadable'
    assert path.read_bytes() == damaged      # no write may destroy the file

    history = client.get(f'/api/sessions/{session.id}/history', params={'limit': 50})
    assert history.status_code == 200 and len(history.json()['history']) == 4


def test_delete_removes_dangling_entry_without_requiring_the_row(client):
    session, ids = _session_with_blocks('http dangling')
    bookmarks.add_bookmark(session.id, ids[0])
    path = bookmarks._path(session.id)
    envelope = json.loads(path.read_text(encoding='utf-8'))
    forged = 'pan:' + 'f' * 32
    envelope['bookmarks'][forged] = {'messageId': forged, 'createdAt': '2026-10-14T00:00:00'}
    envelope['revision'] += 1
    path.write_text(json.dumps(envelope), encoding='utf-8')
    bookmarks._CACHE.pop(session.id, None)

    fetched = client.get(f'/api/sessions/{session.id}/bookmarks').json()
    assert fetched['dangling'] == [forged]
    removed = client.delete(f'/api/sessions/{session.id}/bookmarks/{forged}')
    assert removed.json()['changed'] is True
    assert client.get(f'/api/sessions/{session.id}/bookmarks').json()['dangling'] == []


def test_mutation_on_missing_session_reports_not_found(client):
    response = client.put('/api/sessions/ses_0000000000000000/bookmarks/pan:' + 'a' * 32)
    assert response.json()['error']['code'] in {'session_not_found', 'message_not_found'}
