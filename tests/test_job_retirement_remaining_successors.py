"""Exact successors for two remaining legacy scheduler semantics."""
import asyncio

import pytest

from packages.jobs import final_contracts as contracts
from packages.scheduler.creators import CreatorStore
from packages.scheduler.providers import TimeProvider, time_definition
from tests.test_job_final_creators import config, interval, stack, at
from tests.test_job_final_http_service import USER, control, create, enabled, h, job_def


def test_scheduled_fire_without_matching_candidates_is_noop_and_not_backlogged(h):
    """A canonical trigger with no matching event type creates no Run or backlog."""
    job = enabled(h)
    trigger = create(h, 'triggers', {
        'name': 'only-workers', 'eventTypes': ['workers.all_idle'],
        'creatorSelector': {'mode': 'registeredType', 'providerType': 'core'},
        'targetJobId': job['id']}, 'no-candidate-trigger')
    active = control(h, 'triggers', trigger, 'enable', 'enable-no-candidate').json()['result']['object']
    result = h.service.admit_event({'id': 'scheduler-event', 'type': 'scheduler.tick',
        'source': {'type': 'scheduler'}, 'payload': {}}, active)
    assert result['status'] == 'skipped'
    assert h.service.final.runs(job['id']) == []
    assert not h.provider.calls


def test_invalid_schedule_spec_is_rejected_before_creator_persistence(tmp_path):
    provider = TimeProvider()
    invalid = config([interval(seconds='not-a-duration')])
    with pytest.raises(contracts.ContractError):
        provider.validate_config(invalid)
    creators = CreatorStore(tmp_path / 'creators')
    before = list((tmp_path / 'creators').rglob('*'))
    with pytest.raises(contracts.ContractError):
        creators.create(time_definition('invalid', invalid), request_key='invalid', state='enabled', at=at(0))
    assert list((tmp_path / 'creators').rglob('*')) == before
