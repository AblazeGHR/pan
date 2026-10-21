"""Server-owned Action/Group identities; no migration or Worker execution."""
import asyncio
import re
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import httpx
import pytest

from packages.actions import store as module
from packages.web import server as srv
from test_action_groups import setup, api_action


@pytest.mark.parametrize('kind', ['actions', 'groups'])
def test_generated_ids_retry_collisions_and_rename_stays_stable(setup, monkeypatch, kind):
    store, _ = setup
    raw = api_action() if kind == 'actions' else {'name': 'Same name', 'action_ids': []}
    raw.pop('id', None)
    ids = iter(['a' * 32, 'a' * 32, 'b' * 32])
    monkeypatch.setattr(module.uuid, 'uuid4', lambda: SimpleNamespace(hex=next(ids)))
    first = store.put(kind, raw)
    second = store.put(kind, raw)
    assert re.fullmatch(r'[a-z][a-z0-9_]{0,31}', first['id'])
    assert first['id'] != second['id']
    assert 'id' not in raw
    renamed = store.put(kind, {**first, 'name': 'Renamed'})
    assert renamed['id'] == first['id']
    assert len(store.read()[kind]) == 2


def test_generated_group_id_never_reuses_retired_plugin_identity(setup, monkeypatch):
    store, _ = setup
    pid = 'action-group-group-' + 'a' * 24
    store.mutate(lambda state: state['retired_plugins'].append(pid))
    ids = iter(['a' * 32, 'b' * 32])
    monkeypatch.setattr(module.uuid, 'uuid4', lambda: SimpleNamespace(hex=next(ids)))
    created = store.put('groups', {'name': 'New', 'action_ids': []})
    assert created['id'] == 'group_' + 'b' * 24


def test_concurrent_generated_creates_do_not_overwrite_same_names(setup):
    store, _ = setup
    raw = api_action()
    raw.pop('id')
    with ThreadPoolExecutor(max_workers=6) as pool:
        items = list(pool.map(lambda _: store.put('actions', raw), range(24)))
    assert len({item['id'] for item in items}) == 24
    assert len(store.read()['actions']) == 24


def test_collision_exhaustion_keeps_registry_bytes(setup, monkeypatch):
    store, _ = setup
    monkeypatch.setattr(module.uuid, 'uuid4', lambda: SimpleNamespace(hex='a' * 32))
    raw = api_action()
    raw.pop('id')
    store.put('actions', raw)
    before = store.path.read_bytes()
    with pytest.raises(ValueError, match='Could not allocate a unique ID'):
        store.put('actions', raw)
    assert store.path.read_bytes() == before


def test_rename_preserves_shared_references_release_and_plugin_identity(setup):
    store, _ = setup
    raw = api_action()
    raw.pop('id')
    action = store.put('actions', raw)
    first = store.put('groups', {'name': 'First', 'action_ids': [action['id']]})
    second = store.put('groups', {'name': 'Second', 'action_ids': [action['id']]})
    built = store.build(first['id'])
    store.put('actions', {**action, 'name': 'Renamed Action'})
    store.put('groups', {**first, 'name': 'Renamed Group'})
    state = store.read()
    assert state['groups'][first['id']]['build'] == built
    assert state['groups'][first['id']]['action_ids'] == [action['id']]
    assert state['groups'][second['id']]['action_ids'] == [action['id']]
    assert state['groups'][first['id']]['id'] == first['id']


@pytest.mark.parametrize('bad_id', ['', None, 'Bad-ID'])
def test_explicit_invalid_ids_still_reject_without_write(setup, bad_id):
    store, _ = setup
    before = store.read()
    with pytest.raises(ValueError, match='ID must'):
        store.put('actions', {**api_action(), 'id': bad_id})
    assert store.read() == before


def test_create_http_and_agent_drafts_return_authoritative_identity(setup):
    async def run():
        raw = api_action()
        raw.pop('id')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url='http://isolated') as client:
            created = await client.post('/api/actions/actions', json={'item': raw, 'expectedRevision': 0})
            assert created.status_code == 200
            value = created.json()['item']
            assert value['id'] and value['enabled'] is True
            duplicate = await client.post('/api/actions/actions', json={'item': value})
            assert duplicate.status_code == 400
            updated = await client.put('/api/actions/actions/' + value['id'], json={'item': {**value, 'name': 'Renamed'}})
            assert updated.status_code == 200 and updated.json()['actions'][0]['id'] == value['id']
            mismatch = await client.put('/api/actions/actions/other', json={'item': value})
            assert mismatch.status_code == 400
            draft = await client.post('/api/action-drafts/actions/put', json={'item': raw})
            assert draft.status_code == 200
            draft_item = draft.json()['item']
            assert draft_item['enabled'] is False and draft_item['id'] != value['id']
            grouped = await client.post('/api/action-drafts/groups/put', json={'item': {'name': 'Draft Group', 'action_ids': [draft_item['id']]}})
            assert grouped.status_code == 200 and grouped.json()['item']['id']
            assert not grouped.json()['item'].get('build')
            forbidden = await client.post('/api/action-drafts/groups/build', json={'id': grouped.json()['item']['id']})
            assert forbidden.status_code == 400
            stale = await client.post('/api/actions/groups', json={'item': {'name': 'Stale', 'action_ids': []}, 'expectedRevision': 0})
            assert stale.status_code == 400
            assert len((await client.get('/api/actions')).json()['groups']) == 1
    asyncio.run(run())
