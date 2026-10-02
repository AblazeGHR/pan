import asyncio
import copy
import json
import threading
from unittest.mock import AsyncMock

import pytest

from packages.core import session as store
from packages.core import history_identity_backfill as repair
from packages.web import server


def legacy(name='old'):
    session = store.create(name, adapter='cbc')
    rows = [{'role': role, 'content': 'needle needle', 'source': 'qq' if role == 'user' else 'custom',
             'pluginField': {'preserve': True}}
            for role in ('user', 'assistant', 'tool', 'thinking', 'error', 'system')]
    rows.append({'role': 'user', 'content': 'needle', 'source': 'system_prompt'})
    store.replace_history(session, copy.deepcopy(rows))
    store.save_full(session)
    return session, rows


def test_loaded_repair_is_idempotent_and_preserves_rows():
    session, original = legacy()
    revision = session.history_revision
    assert repair.prepare_history_identities(session) == 4
    identities = [row.get('messageId') for row in session.history]
    assert len(set(identities[:4])) == 4
    assert all(store.is_pan_message_id(value) for value in identities[:4])
    assert identities[4:] == [None, None, None]
    assert [{k: v for k, v in row.items() if k != 'messageId'} for row in session.history] == original
    assert session.history_revision == revision + 1
    stamp = store._history_path(session.id).stat().st_mtime_ns
    assert repair.prepare_history_identities(session) == 0
    assert store._history_path(session.id).stat().st_mtime_ns == stamp
    store.clear_cache()
    assert [row.get('messageId') for row in store.get(session.id).history] == identities


def test_cold_repair_preserves_invalid_lines_and_does_not_hydrate():
    session, original = legacy()
    path = store._history_path(session.id)
    with path.open('ab') as output:
        output.write(b'not-json\n')
    store.clear_cache()
    cold = store.list_all(load_history=False)[0]
    assert not cold._history_loaded
    assert repair.prepare_history_identities(cold) == 4
    assert not cold._history_loaded and cold.history == []
    assert path.read_bytes().endswith(b'not-json\n')
    loaded = store.get(session.id)
    assert [{k: v for k, v in row.items() if k != 'messageId'} for row in loaded.history] == original


def test_loaded_repair_preserves_corrupt_lines_and_delivery_text():
    session, _ = legacy()
    path = store._history_path(session.id)
    raw = path.read_bytes().replace(b'needle needle', b'needle needle [delivered:qq]', 1)
    path.write_bytes(raw + b'broken\n')
    store.clear_cache()
    loaded = store.get(session.id)
    assert repair.prepare_history_identities(loaded) == 4
    assert b'[delivered:qq]' in path.read_bytes()
    assert path.read_bytes().endswith(b'broken\n')


def test_metadata_failure_is_recoverable_without_changing_ids(monkeypatch):
    session, _ = legacy()
    replace = repair.os.replace
    def fail_metadata(source, destination):
        if str(destination).endswith('.json'):
            raise OSError('metadata fault')
        replace(source, destination)
    monkeypatch.setattr(repair.os, 'replace', fail_metadata)
    with pytest.raises(OSError):
        repair.prepare_history_identities(session)
    ids = [row.get('messageId') for row in session.history]
    monkeypatch.setattr(repair.os, 'replace', replace)
    assert repair.prepare_history_identities(session) == 0
    store.clear_cache()
    assert [row.get('messageId') for row in store.get(session.id).history] == ids


def test_concurrent_repair_has_only_one_id_assignment():
    session, _ = legacy()
    results = []
    threads = [threading.Thread(target=lambda: results.append(repair.prepare_history_identities(session))) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert sorted(results) == [0, 4]


def test_search_off_and_empty_queries_do_not_repair():
    session, _ = legacy()
    assert asyncio.run(server.api_history_search(q='', sessionId=session.id, countMode='content', prepareLegacy=True))['hits'] == []
    assert asyncio.run(server.api_history_search(q='needle', sessionId=session.id, countMode='content', roles='', prepareLegacy=True))['hits'] == []
    assert all('messageId' not in row for row in session.history)


def test_main_file_only_history_is_preserved(monkeypatch):
    session, original = legacy()
    data = session.to_dict()
    data.pop('system_prompt', None)
    store._path(session.id).write_text(json.dumps(data), encoding='utf-8')
    store._history_path(session.id).unlink()
    store.clear_cache()
    cold = store.list_all(load_history=False)[0]
    assert repair.prepare_history_identities(cold) == 4
    assert not cold._history_loaded
    assert store.get(session.id).history[0]['pluginField'] == original[0]['pluginField']


def test_failed_atomic_replace_leaves_original_disk_and_memory(monkeypatch):
    session, _ = legacy()
    before = copy.deepcopy(session.history)
    disk = store._history_path(session.id).read_bytes()
    monkeypatch.setattr(repair.os, 'replace', lambda *_: (_ for _ in ()).throw(OSError('fault')))
    with pytest.raises(OSError):
        repair.prepare_history_identities(session)
    assert session.history == before
    assert store._history_path(session.id).read_bytes() == disk
    assert not list(store.SESSION_DIR.glob('*.ids.tmp'))


def test_append_during_bulk_write_is_not_lost(monkeypatch):
    session, _ = legacy()
    started, release = threading.Event(), threading.Event()
    encode = store._encode_line
    first = True
    def slow(row):
        nonlocal first
        if first:
            first = False
            started.set()
            assert release.wait(5)
        return encode(row)
    monkeypatch.setattr(store, '_encode_line', slow)
    errors = []
    def run():
        try:
            repair.prepare_history_identities(session)
        except Exception as exc:
            errors.append(exc)
    thread = threading.Thread(target=run)
    thread.start()
    assert started.wait(5)
    store.append_history(session, {'role': 'assistant', 'content': 'late needle'})
    release.set()
    thread.join(5)
    assert not thread.is_alive() and not errors
    store.save(session)
    store.clear_cache()
    assert store.get(session.id).history[-1]['content'] == 'late needle'


def test_scoped_search_opt_in_repairs_without_import():
    session, _ = legacy()
    params = dict(q='needle', sessionId=session.id, countMode='content', roles='user,assistant,tool,thinking')
    assert asyncio.run(server.api_history_search(**params))['totalMatches'] == 0
    result = asyncio.run(server.api_history_search(**params, prepareLegacy=True))
    assert result['totalMatches'] == 8 and result['preparedIdentities'] == 4


def progressive(disconnect=None):
    request = type('Request', (), {'is_disconnected': disconnect or AsyncMock(return_value=False)})()
    async def run():
        response = await server.api_prepare_history_search(request, {'q': 'needle', 'roles': 'user,assistant,tool,thinking', 'limit': 1})
        return [json.loads(chunk) async for chunk in response.body_iterator]
    return asyncio.run(run())


def test_global_preview_then_complete_snapshot_and_paging():
    existing = store.create('ready', adapter='cbc')
    store.append_history(existing, {'role': 'user', 'content': 'needle'})
    store.save(existing)
    old, _ = legacy()
    events = progressive()
    assert events[0]['result']['totalMatches'] == 1
    assert not events[0]['done'] and events[0]['result']['nextCursor'] is None
    final = events[-1]
    assert final['done'] and final['failedSessions'] == []
    assert final['result']['totalMatches'] == 9
    cursor = final['result']['nextCursor']
    assert cursor
    page = asyncio.run(server.api_history_search(q='needle', roles='user,assistant,tool,thinking', countMode='content', limit=1, cursor=cursor))
    assert page['hits'][0]['sessionId'] == old.id


def test_failure_is_explicit_and_disconnect_stops_next_session(monkeypatch):
    old, _ = legacy()
    monkeypatch.setattr(repair, 'prepare_history_identities', lambda _: (_ for _ in ()).throw(OSError('fault')))
    events = progressive()
    assert events[-1]['failedSessions'] == [old.id]
    assert events[-1]['done'] and events[-1]['result']['nextCursor'] is None
    events = progressive(AsyncMock(return_value=True))
    assert len(events) == 1 and not events[0]['done']
