"""Closed Session query grammar, frozen execution and existing authority gates."""
import asyncio
import copy
from functools import partial
import json

import httpx
import jsonschema
import pytest

from packages.actions import session_query as q
from packages.actions.executor import canonical_session_pause
from packages.actions.provider import execute as legacy_execute
from packages.actions.store import Store, validate_action
from packages.core import session
from packages.automation.engine import Engine
from tests.test_automation_core import action, trigger, event, setup, settled, async_test
from tests.test_job_final_http_service import h, USER


def leaf(field='name', op='eq', value='Alpha'):
    return dict(field=field, op=op, value=value)


def row(sid='a', **kw):
    result = dict(id=sid, name='Alpha', adapter='cbc', managedBy=None, readonlySession=False,
                  pinned=False, workspaceIds=['w'])
    result.update(kw)
    return result


def query_action(predicate=None):
    a = action()
    a['execution']['selector'] = {'mode': 'query', 'query': leaf() if predicate is None else predicate}
    return a


@pytest.mark.parametrize('predicate', [leaf(), leaf('managedBy', 'eq', None), leaf('managedBy', 'in', [None, 'm']),
    leaf('readonlySession', 'in', [True, False]), leaf('workspaceIds', 'eq', ['w']), leaf('workspaceIds', 'contains', 'w'),
    leaf('name', 'contains', 'lph'), {'and': [leaf(), {'not': leaf('adapter', 'ne', 'cbc')}]},
    {'or': [leaf('pinned', 'eq', True), leaf('id', 'in', ['a'])]}])
def test_valid_grammar_schema_and_semantics(predicate):
    jsonschema.Draft202012Validator(q.predicate_schema()).validate(predicate)
    q.validate_query(predicate)
    assert q.select(predicate, [row()]) == {'ids': ['a'], 'candidateCount': 1}


@pytest.mark.parametrize('predicate', [{}, {'and': []}, {'or': []}, {'not': None},
    leaf('history.content'), leaf('adapter.name'), leaf('queuePaused'), leaf('name', 'regex', '.*'),
    leaf('name', 'contains', ''), leaf('id', 'contains', 'a'), leaf('pinned', 'eq', 1),
    leaf('readonlySession', 'in', [False, 0]), leaf('workspaceIds', 'in', [['w']]),
    leaf('workspaceIds', 'contains', ['w']), leaf('workspaceIds', 'eq', 'w'),
    leaf('name', 'eq', None), leaf('managedBy', 'eq', False), leaf('id', 'in', []),
    {'or': [leaf(), leaf('unknown')]}, {**leaf(), 'sql': 'SELECT *'},
    leaf('name', 'eq', 'x' * 513)])
def test_invalid_rejected_in_schema_and_store(predicate):
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft202012Validator(q.predicate_schema()).validate(predicate)
    with pytest.raises(ValueError):
        validate_action(query_action(predicate))


def test_depth_nodes_values_candidates_results_and_order():
    p = leaf()
    for _ in range(7):
        p = {'not': p}
    q.validate_query(p)
    with pytest.raises(ValueError, match='depth/node'):
        q.validate_query({'not': p})
    q.validate_query({'and': [leaf()] * 63})
    with pytest.raises(ValueError):
        q.validate_query({'and': [leaf()] * 64})
    q.validate_query(leaf('name', 'in', ['a'] * 128))
    with pytest.raises(ValueError):
        q.validate_query(leaf('name', 'in', ['a'] * 129))
    assert q.select(leaf('id', 'eq', 'absent'), (row(str(i)) for i in range(2048)))['ids'] == []
    with pytest.raises(ValueError, match='candidate limit'):
        q.select(leaf('id', 'eq', 'absent'), (row(str(i)) for i in range(2049)))
    assert len(q.select(leaf(), [row(str(i)) for i in range(256)])['ids']) == 256
    with pytest.raises(ValueError, match='result limit'):
        q.select(leaf(), [row(str(i)) for i in range(257)])
    assert q.select(leaf(), [row('z'), row('a'), row('z')])['ids'] == ['a', 'z']
    assert not q.matches(leaf('name', 'contains', 'alpha'), row())
    assert not q.matches(leaf('workspaceIds', 'contains', 'w-substring'), row())


def test_snapshot_current_pin_effective_workspace_and_no_history(tmp_path, monkeypatch):
    manager = session.create('Manager', adapter='cbc', workspace_ids=['root-w'])
    child = session.create('Child', adapter='codex', workspace_ids=['stale-w'])
    child.managed_by = manager.id
    child.readonly_session = True
    session.save(child)
    assert session.set_session_pinned(child.id, True)[1] is None
    def no_history(*args, **kw):
        raise AssertionError('history must not hydrate')
    monkeypatch.setattr(session, '_hydrate_cached_session', no_history)
    monkeypatch.setattr(session, '_from_data_with_history', no_history)
    session._cache.clear()
    session._all_loaded = False
    rows = q.snapshot()
    snapshot = next(r for r in rows if r['id'] == child.id)
    assert snapshot == dict(id=child.id, name='Child', adapter='codex', managedBy=manager.id,
                           readonlySession=True, pinned=True, workspaceIds=['root-w'])
    assert q.select({'and': [leaf('workspaceIds', 'contains', 'root-w'), leaf('pinned', 'eq', True)]}, rows)['ids'] == [child.id]


def test_candidate_cap_precedes_cold_hydration(tmp_path, monkeypatch):
    session.SESSION_DIR.mkdir()
    for i in range(4):
        (session.SESSION_DIR / f'{i}.json').write_text('{}')
    def refuse(*args, **kwargs):
        raise AssertionError('must reject before loading a Session')
    monkeypatch.setattr(session, '_from_data_without_history', refuse)
    with pytest.raises(ValueError, match='candidate limit'):
        session.list_all(load_history=False, limit=3)
    assert not session._all_loaded


@async_test
async def test_freezes_once_before_effect_and_reports_partial_failure():
    rows = [row('b'), row('a')]
    reads, calls, checkpoints = [], [], []
    async def query(predicate):
        reads.append(copy.deepcopy(predicate))
        return q.select(predicate, rows)
    async def progress(**fields):
        checkpoints.append(copy.deepcopy(fields))
        if 'targetIds' in fields:
            # Rename, workspace move and new matching Session after durable freeze.
            rows[0]['name'] = 'Changed'
            rows[1]['workspaceIds'] = ['moved']
            rows.append(row('new'))
    async def pause(sid, args):
        assert checkpoints[0]['targetIds'] == ['a', 'b']
        calls.append(sid)
        return {'ok': sid != 'b', **({'error': 'Session removed'} if sid == 'b' else {})}
    result = await canonical_session_pause(query_action(), {}, {}, progress, pause, query)
    assert len(reads) == 1 and calls == ['a', 'b']
    assert result['matched'] == 2 and result['failed'] == 1 and not result['ok']
    assert checkpoints[0]['selection']['candidateCount'] == 2
    assert checkpoints[0]['selection']['matchedAt']


@async_test
async def test_zero_results_diagnostic_and_invalid_never_queries():
    async def query(predicate):
        return q.select(predicate, [])
    checkpoints = []
    async def progress(**kw):
        checkpoints.append(kw)
    async def pause(*args):
        raise AssertionError('no targets')
    result = await canonical_session_pause(query_action(), {}, {}, progress, pause, query)
    assert result['ok'] and result['matched'] == 0
    assert checkpoints[0]['targetIds'] == [] and checkpoints[0]['selection']['candidateCount'] == 0
    async def forbidden(*args):
        raise AssertionError('invalid must fail before querying')
    with pytest.raises(ValueError):
        await canonical_session_pause(query_action(leaf('unknown')), {}, {}, progress, pause, forbidden)




@async_test
async def test_legacy_and_draft_authority_gates(h, monkeypatch):
    from packages.web import server
    from packages.actions.groups import Groups
    from packages.jobs.final_contracts import ContractError
    from packages.jobs.final_service import object_ref
    from tests.test_action_groups import canonical_state
    monkeypatch.setattr(server.app.state, 'automation', h.automation, raising=False)
    monkeypatch.setattr(server.app.state, 'job_final_service', h.service, raising=False)
    a = query_action()
    store = Store()
    before = store.read()
    for kwargs in ({'draft': True}, {}):
        with pytest.raises(PermissionError, match='retired'):
            store.put('actions', a, **kwargs)
    with pytest.raises(PermissionError, match='retired'):
        store.build('g')
    assert store.read() == before
    with pytest.raises(PermissionError, match='retired'):
        await legacy_execute(a, {})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(server.app), base_url='http://test',
                                 headers={'Authorization': 'Bearer ' + h.token}) as client:
        response = await client.get('/api/actions/session-operation-template')
        assert response.status_code == 200 and response.json()['item']['enabled'] is False
        for raw in (a, query_action(leaf('history'))):
            retired = await client.post('/api/action-drafts/actions/put', json={'item': raw})
            assert retired.status_code == 410 and retired.json()['detail']['code'] == 'retired'
        preview = await client.post('/api/jobs/actions/definition-preview', json={'draft': a})
        assert preview.status_code == 200, preview.text
        definition = preview.json()['definition']
        draft = await client.post('/api/jobs/actions', json={
            'definition': definition, 'expectedRevision': 0, 'requestKey': 'canonical-query-draft'})
        assert draft.status_code == 201, draft.text
        item = draft.json()['result']['object']
        assert item['state'] == 'draft' and item['enabled'] is False
        invalid = await client.post('/api/jobs/actions/definition-preview', json={'draft': query_action(leaf('history'))})
        assert invalid.status_code == 400 and invalid.json()['error']['code'] == 'invalid_schema'
    durable = canonical_state(h.service)
    with pytest.raises(ContractError) as blocked:
        Groups(h.service).put({'id': 'query_draft_group', 'name': 'Draft Group',
            'tools': [{'alias': 'pause', 'actionRef': object_ref('action', item)}]}, 0, 'query-draft-group', actor=USER)
    assert blocked.value.code == 'reference_conflict'
    assert canonical_state(h.service) == durable



def test_extreme_depth_is_rejected_before_draft_deepcopy():
    predicate = leaf()
    for _ in range(1500):
        predicate = {'not': predicate}
    a = action()
    a['execution']['selector'] = {'mode': 'query', 'query': predicate}
    with pytest.raises(PermissionError, match='retired'):
        Store().put('actions', a, draft=True)
    with pytest.raises(ValueError, match='depth/node'):
        validate_action(a)


@async_test
async def test_disabled_query_draft_never_enumerates_other_sessions():
    draft = query_action(leaf('managedBy', 'ne', 'agent-a'))
    draft['enabled'] = False
    async def forbidden(*args, **kwargs):
        raise AssertionError('disabled drafts cannot enumerate or operate across caller scope')
    with pytest.raises(ValueError, match='disabled'):
        await canonical_session_pause(draft, {}, {}, forbidden, forbidden, forbidden)
    for predicate in [leaf('scope'), {**leaf(), 'agentId': 'agent-a'}, leaf('managedBy.id')]:
        with pytest.raises(ValueError):
            validate_action(query_action(predicate))


@pytest.mark.parametrize('metadata', [row(pinned=1), row(readonlySession='false'), row(workspaceIds='w'), row(managedBy=False)])
def test_bad_metadata_is_explicit_failure(metadata):
    with pytest.raises(ValueError, match='metadata field'):
        q.select(leaf(), [metadata])

@async_test
async def test_real_session_mutations_after_checkpoint_do_not_reselect(monkeypatch):
    first = session.create('Alpha first', adapter='cbc', workspace_ids=['w'])
    second = session.create('Alpha second', adapter='cbc', workspace_ids=['w'])
    predicate = {'and': [leaf('name', 'contains', 'Alpha'), leaf('workspaceIds', 'contains', 'w')]}
    snapshots, calls, checkpoints = [], [], []
    original = q.snapshot
    def snapshot():
        rows = original()
        snapshots.append(rows)
        return rows
    monkeypatch.setattr(q, 'snapshot', snapshot)
    created = []
    async def progress(**fields):
        checkpoints.append(copy.deepcopy(fields))
        if 'targetIds' in fields:
            def mutate():
                first.name = 'Renamed'
                session.save(first)
                second.workspace_ids = ['moved']
                session.save(second)
                created.append(session.create('Alpha new', adapter='cbc', workspace_ids=['w']).id)
            await asyncio.to_thread(mutate)
    async def pause(sid, args):
        calls.append(sid)
        return {'ok': True}
    result = await canonical_session_pause(query_action(predicate), {}, {}, progress, pause)
    assert len(snapshots) == 1
    assert checkpoints[0]['targetIds'] == calls == sorted([first.id, second.id])
    assert not set(created) & set(calls)
    assert result['ok'] and result['matched'] == 2

@async_test
async def test_cancel_query_settles_owned_metadata_read(monkeypatch):
    import threading
    entered, release, retired = threading.Event(), threading.Event(), threading.Event()
    def snapshot():
        entered.set()
        try:
            assert release.wait(3)
            return [row()]
        finally:
            retired.set()
    monkeypatch.setattr(q, 'snapshot', snapshot)
    task = asyncio.create_task(q.resolve(leaf()))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert retired.is_set()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
