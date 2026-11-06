"""Cooperative active v1 clock -> existing WP04/canonical owner only."""
import asyncio
from datetime import datetime

import pytest

from packages.jobs import final_contracts as fc
from packages.jobs.final_api import CanonicalUserAuth
from packages.jobs.final_service import FinalService, definition, object_ref
from packages.jobs.runtime_owner import OwnerFence, enroll_fresh_profile
from packages.scheduler.providers import legacy_source_plan
from packages.scheduler.source_migration import SourceMigration
from packages.scheduler.time_events import Sources
from tests.test_automation_canonical import SessionProvider, USER


@pytest.fixture
def original_clock(tmp_path):
    root = tmp_path / 'cooperative-clock'
    enroll_fresh_profile(root)
    sources = Sources(root / 'jobs')
    old = sources.put({'name': 'active original clock', 'enabled': True,
        'schedule': {'kind': 'interval', 'intervalSec': 60, 'anchor': '2026-01-01T00:00:00'},
        'misfirePolicy': 'skip', 'graceSec': 1}, 0, at=datetime(2026, 1, 1))
    service = FinalService(root / 'canonical', CanonicalUserAuth(root / 'automation'))
    product = next(a for a in service.actions(actor=USER)['items'] if a['id'] == 'product_session_message')
    action_def = definition('action', product)
    action_def['system'] = False
    action = service.publish_action('owned_clock_message', action_def)
    creator_def, _ = legacy_source_plan(old, timezone_name='UTC')
    graph = {'job': {'name': 'clock target', 'steps': [{'stepId': 's0',
        'actionRef': object_ref('action', action), 'arguments': {
            'sessionId': {'from': 'literal', 'value': 'owned-session'},
            'text': {'from': 'literal', 'value': 'clock'}},
        'completion': {'contract': 'durable-queue-accepted'}}], 'retention': {'policyRef': 'test'}},
        'creators': [{'mode': 'create', 'key': 'clock', 'definition': creator_def}],
        'triggers': [{'key': 'trigger', 'definition': {'name': 'clock trigger',
            'eventTypes': creator_def['eventTypes'], 'creatorSelector': {'mode': 'ids', 'ids': ['clock']},
            'targetJobId': 'job'}, 'targetJobKey': 'job', 'creatorKeys': ['clock']}]}
    source_maps = [{'creatorKey': 'clock', 'sourceId': old['id'], 'timezone': 'UTC', 'triggerKeys': {}}]
    service.executor.session_provider = SessionProvider()
    yield service, sources, old, graph, source_maps
    asyncio.run(service.close())


def migrate(data):
    service, sources, old, graph, mappings = data
    return SourceMigration(service, None).adopt_settled_source_graph(sources,
        expected_digest=fc.digest(sources.snapshot()), desired_graph=graph,
        legacy_sources=mappings, request_key='original-clock-handoff', actor=USER)


def test_P6_CLOCK_01_unique_writer_original_cursor_cold_no_replay(original_clock):
    service, sources, old, graph, mappings = original_clock
    raw = sources.path.read_bytes()
    digest = fc.digest(sources.snapshot())
    service.owner.claim(0)
    service.owner.set_state('active')
    result = migrate(original_clock)
    assert migrate(original_clock)['operationId'] == result['operationId']
    cid = result['result']['objectIds']['creators']['clock']
    item = service.creators.get(cid)
    assert item['state'] == 'enabled'
    assert item['legacyHistory']['sourceId'] == old['id']
    assert item['cursor']['rules']['legacy']['nextFireAt'] == '2026-01-01T00:01:00Z'
    with pytest.raises(fc.ContractError):
        sources.put({key: old[key] for key in ('name', 'schedule', 'enabled', 'misfirePolicy', 'graceSec')},
                    old['revision'], old['id'])
    assert sources.path.read_bytes() == raw and fc.digest(sources.snapshot()) == digest
    assert asyncio.run(service.creator_runtime.tick(at='2026-01-01T00:00:59Z')) == 0
    assert asyncio.run(service.creator_runtime.tick(at='2026-01-01T00:01:00Z')) == 1
    assert len(service.events.page()) == 1
    cold = FinalService(service.root, service.auth)
    try:
        current = cold.creators.get(cid)
        assert current['cursor']['rules']['legacy']['nextFireAt'] == '2026-01-01T00:02:00Z'
        assert current['producedCount'] == 1
        assert cold.operation_for_key('create', 'original-clock-handoff', actor=USER)['operationId'] == result['operationId']
    finally:
        asyncio.run(cold.close())
    assert asyncio.run(service.creator_runtime.tick(at='2026-01-01T00:01:00Z')) == 0
    assert len(service.events.page()) == 1


def test_P6_CLOCK_02_active_unknown_lease_blocks_claim_before_graph(original_clock):
    service, sources, old, *_ = original_clock
    owner = OwnerFence(service.owner.root)
    with owner.legacy_pass():
        with pytest.raises(fc.ContractError, match='Legacy pass'):
            service.owner.claim(0)
    with owner.transaction() as db:
        db.execute("INSERT INTO legacy_leases VALUES('unknown-clock-writer')")
    with pytest.raises(fc.ContractError, match='Legacy pass'):
        service.owner.claim(0)
    with pytest.raises(fc.ContractError, match='unproven'):
        migrate(original_clock)
    assert service.creators.list() == []


def test_P6_CLOCK_03_pending_and_graph_mismatch_roll_back(original_clock):
    service, sources, old, graph, mappings = original_clock
    sources.reserve(old['id'], datetime(2026, 1, 1, 0, 1))
    before = sources.snapshot()
    service.owner.claim(0)
    service.owner.set_state('active')
    with pytest.raises(fc.ContractError, match='unsettled'):
        migrate(original_clock)
    assert service.creators.list() == [] and sources.snapshot() == before
    assert service.creators.composition_for_key('original-clock-handoff') is None
