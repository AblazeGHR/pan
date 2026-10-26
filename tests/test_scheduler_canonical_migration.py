"""R5-P2: canonical cutover, historical sources and compatibility fencing.

All ownership, databases and dispatch callbacks belong to tmp_path. No server,
background recovery loop, profile, Worker or protected port is used here.
"""
import asyncio
import copy
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from packages.jobs import final_contracts as fc
from packages.jobs.runtime_owner import OwnerFence
from packages.scheduler import engine, store
from packages.scheduler.composition import CompositionCoordinator, NullCompositionPort
from packages.scheduler.creators import CanonicalEventStore, CreatorRuntime, CreatorStore, EventBus
from packages.scheduler.legacy_bridge import LegacySchedulerBridge
from packages.scheduler.providers import TimeProvider, legacy_source_plan, time_definition
from packages.scheduler.time_events import Sources, Runtime
from tests.test_job_final_creators import T0, at, config, interval


NOW = datetime(2026, 1, 1)


def source(sources):
    return sources.put({'name': 'old source', 'enabled': True,
                        'schedule': {'kind': 'interval', 'intervalSec': 60, 'anchor': NOW.isoformat()},
                        'misfirePolicy': 'skip', 'graceSec': 1}, 0, at=NOW)


def claim(tmp_path, creators=None):
    owner = OwnerFence(tmp_path)
    owner.claim(0)
    owner.set_state('active')
    if creators is not None:
        creators.owner_fence = owner
    return owner


def import_history(creators, sources):
    return creators.import_legacy_sources(sources, request_key='history', expected_digest=fc.digest(sources.snapshot()))


def test_historical_reads_are_read_only_even_for_missing_stores(tmp_path):
    sources = Sources(tmp_path / 'missing')
    assert sources.list() == [] and sources.get('missing') is None
    assert sources.references('missing') == []
    assert sources.snapshot() == {'sources': [], 'links': [], 'requests': []}
    assert not sources.path.parent.parent.exists()
    item = source(sources)
    before = sources.path.read_bytes()
    owner = claim(tmp_path)
    try:
        assert sources.get(item['id'])['nextFireAt'] == '2026-01-01T00:01:00'
        assert sources.list()[0]['id'] == item['id']
        sources.snapshot()
        assert sources.path.read_bytes() == before
    finally:
        owner.release()


@pytest.mark.parametrize('state', ['active', 'closed', 'failed'])
def test_cutover_fences_old_pass_and_all_source_mutations(tmp_path, state):
    sources = Sources(tmp_path / 'jobs')
    item = source(sources)
    before = sources.path.read_bytes()
    owner = claim(tmp_path)
    if state != 'active':
        owner.release(failed=state == 'failed')
    calls = []
    async def admit(event):
        calls.append(event)
        return {'persisted': True, 'event': event}
    operations = [
        lambda: sources.put({'name': 'new', 'schedule': item['schedule']}, 0),
        lambda: sources.reserve(item['id'], NOW + timedelta(seconds=60)),
        lambda: sources.advance(item['id'], 'evt_old'),
        lambda: sources.acknowledged(item['id'], 'evt_old'),
        lambda: sources.set_next_fire(item['id'], None),
        lambda: sources.bind_link(item['id'], 'trigger', 'job'),
        lambda: sources.detach_links('job'),
        lambda: sources.composition_enabled(item['id'], False),
        lambda: sources.bind_operation(item['id'], 'operation'),
        lambda: sources.delete(item['id'], 1),
        lambda: asyncio.run(Runtime(sources, admit, []).tick({}, at=NOW + timedelta(seconds=60))),
    ]
    try:
        for operation in operations:
            with pytest.raises(fc.ContractError, match='fence'):
                operation()
        assert calls == [] and sources.path.read_bytes() == before
        assert sources.get(item['id'])['enabled']
    finally:
        owner.release()


def test_entire_legacy_publication_pass_blocks_owner_activation(tmp_path):
    sources = Sources(tmp_path / 'jobs')
    source(sources)
    async def check():
        entered, finish = asyncio.Event(), asyncio.Event()
        async def admit(event):
            entered.set()
            await finish.wait()
            return {'persisted': True, 'event': event}
        task = asyncio.create_task(Runtime(sources, admit, []).tick({}, at=NOW + timedelta(seconds=60)))
        await entered.wait()
        owner = OwnerFence(tmp_path)
        try:
            with pytest.raises(fc.ContractError, match='Legacy pass'):
                owner.claim(0)
        finally:
            finish.set()
            await task
        owner.claim(0)
        owner.release()
    asyncio.run(check())


def test_store_engine_bridge_imports_and_old_api_reads_survive_cutover(tmp_path, monkeypatch):
    monkeypatch.setenv('PAN_SCHEDULER_DIR', str(tmp_path / 'jobs'))
    task = store.create_task({'name': 'old task', 'text': 'old', 'target_session_id': 'selected',
                              'schedule': {'kind': 'interval', 'intervalSec': 60}})
    store.append_run({'run_id': 'old_run', 'task_id': task['id'], 'status': 'completed'})
    root = store.data_root()
    before = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
    owner = claim(tmp_path)
    bridge = LegacySchedulerBridge(SimpleNamespace())
    try:
        assert store.get_task(task['id'])['name'] == 'old task'
        assert store.list_tasks()[0]['id'] == task['id']
        assert store.list_runs(task['id'])[0]['run_id'] == 'old_run'
        for operation in (
            lambda: store.create_task({}), lambda: store.update_task(task['id'], {'enabled': True}),
            lambda: store.delete_task(task['id']), lambda: store.replace_task_schedule(task['id'], []),
            lambda: store.set_task_misfire_policy(task['id'], 'skip'), lambda: store.append_run({}),
            store.migrate_legacy_tasks, store._merge_legacy_runs,
            lambda: asyncio.run(bridge.switch(task['id'])), lambda: asyncio.run(bridge.update(task['id'], {})),
            lambda: asyncio.run(bridge.run_now(task['id'])), lambda: asyncio.run(bridge.delete(task['id'])),
            lambda: bridge.project_cursor('old'),
        ):
            with pytest.raises(fc.ContractError, match='fence'):
                operation()
        for name in ('_ensure_registered', 'start_loop', 'tick_once', 'run_now'):
            with pytest.raises(AttributeError, match='retired'):
                getattr(engine, name)
        assert all(path.read_bytes() == value for path, value in before.items())
    finally:
        owner.release()


def test_old_http_reads_remain_available_after_canonical_owner_activation(tmp_path, monkeypatch):
    import httpx
    from fastapi import FastAPI
    from packages.automation.auth import UserAuth
    from packages.scheduler.api import router

    monkeypatch.setenv('PAN_SCHEDULER_DIR', str(tmp_path / 'jobs'))
    task = store.create_task({'name': 'historical task', 'text': 'old', 'target_session_id': 'selected',
                              'schedule': {'kind': 'interval', 'intervalSec': 60}})
    store.append_run({'run_id': 'old_run', 'task_id': task['id'], 'status': 'completed'})
    sources = Sources(tmp_path / 'jobs')
    item = source(sources)
    sources.reserve(item['id'], NOW + timedelta(seconds=60))
    before = sources.path.read_bytes()
    auth = UserAuth(tmp_path / 'auth')
    token = auth.login(auth.key())
    app = FastAPI()
    app.state.automation = SimpleNamespace(auth=auth)
    app.include_router(router)
    owner = claim(tmp_path)
    async def check():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://owned',
                                     headers={'Authorization': 'Bearer ' + token}) as client:
            response = await client.get('/api/scheduler/tasks/' + task['id'])
            assert response.status_code == 200 and task['id'] in response.text
            response = await client.get('/api/scheduler/tasks/' + task['id'] + '/runs')
            assert response.status_code == 200 and 'old_run' in response.text
            response = await client.get('/api/scheduler/event-sources')
            assert response.status_code == 200 and response.json()['sources'][0]['id'] == item['id']
            assert 'pending' not in response.json()['sources'][0]
    try:
        asyncio.run(check())
        assert sources.path.read_bytes() == before
    finally:
        owner.release()


def test_explicit_history_import_preserves_pending_links_cursor_and_never_replays(tmp_path):
    sources = Sources(tmp_path / 'jobs')
    item = source(sources)
    sources.bind_link(item['id'], 'old_trigger', 'old_job')
    pending = sources.reserve(item['id'], NOW + timedelta(seconds=60))
    snapshot = sources.snapshot()
    before = sources.path.read_bytes()
    creators = CreatorStore(tmp_path / 'canonical')
    with pytest.raises(fc.ContractError, match='Canonical owner required'):
        import_history(creators, sources)
    owner = claim(tmp_path, creators)
    events = CanonicalEventStore(tmp_path / 'canonical')
    events.owner_fence = owner
    try:
        record = import_history(creators, sources)
        assert record['snapshot'] == snapshot and record['recordType'] == 'legacy_scheduler_history'
        assert record['snapshot']['sources'][0]['pending'] == pending
        assert import_history(creators, sources) == record
        assert creators.list() == []
        assert asyncio.run(CreatorRuntime(creators, EventBus(events, creators.get)).tick(at=at(600))) == 0
        assert events.stats()['events'] == 0
        with pytest.raises(fc.ContractError, match='snapshot changed'):
            creators.import_legacy_sources(sources, request_key='history', expected_digest='0' * 64)
        with pytest.raises(fc.ContractError, match='identity changed'):
            creators.import_legacy_sources(sources, request_key='different', expected_digest=fc.digest(snapshot))
        assert sources.path.read_bytes() == before
    finally:
        owner.release()
    reopened = CreatorStore(tmp_path / 'canonical')
    assert reopened.legacy_history('history') == record
    with pytest.raises(fc.ContractError):
        import_history(reopened, sources)


def test_import_refuses_foreign_profile_and_rolls_back_on_fault(tmp_path, monkeypatch):
    sources = Sources(tmp_path / 'jobs')
    source(sources)
    clone = Sources(tmp_path / 'clone')
    clone.path.parent.mkdir(parents=True)
    clone.path.write_bytes(sources.path.read_bytes())
    creators = CreatorStore(tmp_path / 'canonical')
    owner = claim(tmp_path, creators)
    try:
        with pytest.raises(fc.ContractError, match='another profile'):
            import_history(creators, Sources(tmp_path / 'foreign' / 'jobs'))
        original = sources.snapshot
        def broken():
            raise OSError('owned snapshot read fault')
        monkeypatch.setattr(sources, 'snapshot', broken)
        with pytest.raises(OSError):
            creators.import_legacy_sources(sources, request_key='history', expected_digest='0' * 64)
        monkeypatch.setattr(sources, 'snapshot', original)
        assert creators.legacy_history('history') is None
        assert import_history(creators, sources)['snapshot'] == original()
        with pytest.raises(fc.ContractError, match='identity changed'):
            import_history(creators, clone)
    finally:
        owner.release()


class CursorFaultPort(NullCompositionPort):
    def __init__(self):
        self.fail = True

    def verify_links(self, operation):
        if self.fail:
            raise OSError('owned fault after cursor adoption before activation')


def migration_request(item, *, trigger_keys=None):
    definition, _ = legacy_source_plan(item, timezone_name='UTC')
    return dict(request_key='migrate-source', desired_graph={
        'creators': [{'mode': 'create', 'key': 'source', 'definition': definition}],
        'triggers': [{'key': 'new_trigger', 'definition': {'name': 'new trigger'},
                      'targetJobKey': 'job', 'creatorKeys': ['source']}],
    }, activate=True, legacy_sources=[{'creatorKey': 'source', 'importKey': 'history',
        'sourceId': item['id'], 'timezone': 'UTC', 'triggerKeys': trigger_keys or {}}])


def test_composition_adopts_cursor_links_and_restarts_without_historical_replay(tmp_path):
    sources = Sources(tmp_path / 'jobs')
    item = source(sources)
    # A settled historical occurrence must never receive a fresh v2 event ID.
    pending = sources.reserve(item['id'], NOW + timedelta(seconds=60))
    sources.advance(item['id'], pending['event']['id'])
    sources.acknowledged(item['id'], pending['event']['id'])
    sources.bind_link(item['id'], 'old_trigger', 'old_job')
    item = sources.get(item['id'])
    creators = CreatorStore(tmp_path / 'canonical', clock=lambda: T0)
    owner = claim(tmp_path, creators)
    try:
        record = import_history(creators, sources)
        request = migration_request(item, trigger_keys={'old_trigger': 'new_trigger'})
        port = CursorFaultPort()
        coordinator = CompositionCoordinator(creators, port)
        with pytest.raises(OSError):
            coordinator.compose(**request)
        operation = creators.composition_for_key(request['request_key'])
        cid = operation['objectIds']['creators']['source']
        current = creators.get(cid)
        assert current['state'] == 'disabled' and current['cursor']['rules']['legacy']['nextFireAt'] == at(120)
        assert current['legacyHistory']['sourceDigest'] == record['sourceDigest']
        port.fail = False
        operation = coordinator.compose(**request)
        assert operation['phase'] == 'complete'
        assert creators.references(cid)[0]['triggerId'] == operation['objectIds']['triggers']['new_trigger']
        events = CanonicalEventStore(tmp_path / 'canonical', clock=lambda: at(120))
        events.owner_fence = owner
        runtime = CreatorRuntime(creators, EventBus(events, creators.get))
        assert asyncio.run(runtime.tick(at=at(60))) == 0
        assert asyncio.run(runtime.tick(at=at(120))) == 1
        assert events.page()[0]['payload']['scheduledAt'] == at(120)
        assert events.page()[0]['eventId'] != pending['event']['id']
        assert creators.get(cid)['producedCount'] == 1
        assert coordinator.compose(**request)['operationId'] == operation['operationId']
        with pytest.raises(fc.ContractError, match='already has a canonical owner'):
            coordinator.compose(**{**request, 'request_key': 'second-source-owner'})
        assert creators.get(cid)['state'] == 'enabled'
        assert creators.get(cid)['cursor']['rules']['legacy']['nextFireAt'] == at(180)
    finally:
        owner.release()
    replacement = OwnerFence(tmp_path)
    replacement.claim(1)
    replacement.set_state('active')
    try:
        reopened = CreatorStore(tmp_path / 'canonical')
        reopened.owner_fence = replacement
        retained = CanonicalEventStore(tmp_path / 'canonical')
        retained.owner_fence = replacement
        assert asyncio.run(CreatorRuntime(reopened, EventBus(retained, reopened.get)).tick(at=at(120))) == 0
        assert retained.stats()['events'] == 1 and reopened.get(cid)['producedCount'] == 1
        with pytest.raises(fc.ContractError):
            creators.set_state(cid, 'enabled', expected_state_revision=0)
    finally:
        replacement.release()


@pytest.mark.parametrize('case', ['pending', 'pin', 'extension', 'blocked', 'setup', 'cursor', 'shared', 'missing_link'])
def test_ambiguous_migrations_stay_history_and_cannot_activate(tmp_path, case):
    sources = Sources(tmp_path / 'jobs')
    item = source(sources)
    request = migration_request(item)
    if case in {'pending', 'pin'}:
        pending = sources.reserve(item['id'], NOW + timedelta(seconds=60))
        if case == 'pin':
            sources.advance(item['id'], pending['event']['id'])
    if case in {'extension', 'blocked', 'setup'}:
        item = sources.get(item['id'])
        item.update({'extension': {'opaque': True}} if case == 'extension' else
                    {'compositionBlocked': True} if case == 'blocked' else {'setupOperation': 'old-operation'})
        with sources.connection() as db:
            sources.write(db, item)
    if case == 'cursor':
        sources.set_next_fire(item['id'], '2026-01-01T00:01:01')
    if case in {'shared', 'missing_link'}:
        sources.bind_link(item['id'], 'old_trigger', 'old_job')
    if case == 'shared':
        sources.bind_link(item['id'], 'other_trigger', 'other_job')
    # Build the intended graph from the clean definition. Imported actual facts
    # are independently checked before links/activation commit.
    creators = CreatorStore(tmp_path / 'canonical', clock=lambda: T0)
    owner = claim(tmp_path, creators)
    try:
        record = import_history(creators, sources)
        with pytest.raises((fc.ContractError, ValueError)):
            CompositionCoordinator(creators).compose(**request)
        assert all(c['state'] == 'disabled' and not c['activation']['committed'] for c in creators.list())
        assert creators.legacy_history('history') == record
    finally:
        owner.release()


def test_revision_edits_retain_cursor_on_every_update(tmp_path):
    creators = CreatorStore(tmp_path / 'canonical', clock=lambda: at(600))
    definition = time_definition('one', config([interval(seconds='0.125')]))
    item = creators.create(definition, request_key='one', at=T0)
    for index in range(1, 4):
        definition = {**definition, 'name': str(index)}
        item = creators.update(item['id'], definition, expected_revision=index, request_key='edit-' + str(index), at=at(600))
        assert item['cursor']['rules']['daily']['nextFireAt'] == at('0.125')
        assert item['cursor']['rules']['daily']['rule'] == interval(seconds='0.125')


def test_cron_fold_second_remains_due_after_first_wall_time_has_passed():
    rule = {'ruleId': 'fold', 'type': 'cron', 'expression': '30 1 * * *',
            'timezone': 'America/New_York', 'dstPolicy': 'gap-skip-fold-second'}
    assert TimeProvider().next_after(rule, '2026-11-01T05:45:00Z')['at'] == '2026-11-01T06:30:00Z'
    gap = {**rule, 'expression': '30 2 * * *', 'dstPolicy': 'strict'}
    assert TimeProvider().next_after(gap, '2026-03-08T16:00:00Z')['at'] == '2026-03-09T06:30:00Z'
    shifted = {**gap, 'dstPolicy': 'gap-shift-fold-first'}
    assert TimeProvider().next_after(shifted, '2026-03-08T07:15:00Z')['at'] == '2026-03-08T07:30:00Z'


def test_publication_requires_frozen_fence_and_reserved_occurrence(tmp_path):
    creators = CreatorStore(tmp_path / 'canonical')
    events = CanonicalEventStore(tmp_path / 'canonical', clock=lambda: at(60))
    bus = EventBus(events, creators.get)
    item = creators.create(time_definition('one', config([interval()])), request_key='one', state='enabled', at=T0)
    pending = creators.reserve(item['id'], 1, 1, 'time:daily:' + at(60), at(60))
    for field in ('revision', 'epoch', 'generation'):
        fence = {**pending['fence'], field: pending['fence'][field] + 1}
        with pytest.raises(fc.ContractError, match='fence'):
            asyncio.run(bus.publish(pending['envelope'], fence))
    with pytest.raises(fc.ContractError):
        creators.reserve(item['id'], 1, 2, 'time:daily:' + at(60), at(60))
    forged = copy.deepcopy(pending['envelope'])
    forged['eventId'] = 'evt_unreserved'
    with pytest.raises(fc.ContractError, match='reserved drive'):
        asyncio.run(bus.publish(forged, pending['fence']))
    assert events.stats()['events'] == 0
    assert asyncio.run(CreatorRuntime(creators, bus).tick(at=at(60))) == 1
    # A late identical publisher can read the durable receipt, never append a
    # second Event or count a second occurrence.
    assert asyncio.run(bus.publish(pending['envelope'], pending['fence']))['duplicate']
    assert events.stats()['events'] == 1 and creators.get(item['id'])['producedCount'] == 1


def test_one_hundred_decimal_occurrences_multi_rule_contenders_and_disk_restarts(tmp_path):
    creators = CreatorStore(tmp_path / 'canonical')
    item = creators.create(time_definition('multi', config([
        interval('a', seconds='0.125'), interval('b', seconds='0.125'),
    ])), request_key='hundred', state='enabled', at=T0)
    async def check():
        current_creators = creators
        for step in range(1, 51):
            if step % 10 == 0:
                current_creators = CreatorStore(tmp_path / 'canonical')
            point = at(str(step * .125))
            events = CanonicalEventStore(tmp_path / 'canonical', clock=lambda: point)
            bus = EventBus(events, current_creators.get)
            await asyncio.gather(*(CreatorRuntime(current_creators, bus).tick(at=point) for _ in range(2)))
            assert events.stats()['events'] == step * 2
            assert current_creators.get(item['id'])['producedCount'] == step * 2
        rows = events.page()
        assert len({(r['payload']['ruleId'], r['payload']['scheduledAt']) for r in rows}) == 100
        assert events.stats()['pins'] == 0
        assert current_creators.get(item['id'])['pendingPublication'] is None
    asyncio.run(check())


def test_legacy_zone_conversion_is_explicit_and_dst_ambiguity_stays_historical(tmp_path):
    sources = Sources(tmp_path / 'jobs')
    item = source(sources)
    definition, occurrence = legacy_source_plan(item, timezone_name='+08:00')
    assert definition['config']['rules'][0]['anchor'] == '2025-12-31T16:00:00Z'
    assert occurrence['at'] == '2025-12-31T16:01:00Z'
    with pytest.raises(fc.ContractError, match='DST'):
        legacy_source_plan(item, timezone_name='America/New_York')
    ambiguous = {**item, 'schedule': {'kind': 'once', 'at': '2026-11-01T01:30:00'},
                 'nextFireAt': '2026-11-01T01:30:00'}
    with pytest.raises(ValueError, match='Ambiguous'):
        legacy_source_plan(ambiguous, timezone_name='America/New_York')


def test_compatibility_consumers_and_web_server_still_import():
    import packages.automation.service
    import packages.web.server
    from packages.scheduler import cron
    assert packages.automation.service.Service is not None
    assert packages.web.server.app is not None
    assert cron.next_fire_after is not None
