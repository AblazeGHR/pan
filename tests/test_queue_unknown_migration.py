"""Unknown legacy receipts survive migration without authorizing another handoff."""
import asyncio
import copy
import json

import pytest

from packages.core import session, worker

STATES = ('unknown_after_crash', 'unknown', 'uncertain')


def row(state):
    return {'id': 'q_unknown', 'queueItemId': 'q_unknown', 'type': 'task',
            'kind': 'task', 'source': 'user', 'text': 'synthetic content',
            'createdAt': 1, 'deliveryState': state, 'dispatchState': 'queued',
            'clientMessageId': 'client_unknown',
            'futureField': {'preserve': [1, 2]}, 'nextAttemptAt': 0}


@pytest.mark.parametrize('state', STATES)
@pytest.mark.parametrize('location', ('pending', 'ledger', 'conflict'))
def test_unknown_preserved_across_recovery_save_reload(state, location):
    s = session.Session(id='ses_unknown_migration', name='Unknown migration')
    item = row(state)
    if location == 'pending':
        s.queue_pending = [copy.deepcopy(item)]
    else:
        s.queue_delivery_ledger = {item['id']: copy.deepcopy(item)}
        s.queue_delivery_ledger[item['id']]['ledgerExtension'] = {'preserve': True}
        if location == 'conflict':
            s.queue_pending = [row('queued')]
    worker._recover_delivery_states(s)
    assert len(s.queue_pending) == 1
    assert s.queue_pending[0]['deliveryState'] == state
    assert s.queue_pending[0]['dispatchState'] == state
    assert s.queue_pending[0]['queueItemId'] == item['id']
    assert s.queue_pending[0]['futureField'] == item['futureField']
    assert not worker._is_dispatchable(s.queue_pending[0])
    assert not worker._has_dispatchable_items(s)
    assert worker._select_queue_unit(s) is None
    session.save(s)
    session._cache.clear()
    loaded = session.get(s.id)
    before = copy.deepcopy(loaded.queue_pending)
    before_ledger = copy.deepcopy(loaded.queue_delivery_ledger)
    assert worker._recover_delivery_states(loaded) is False
    assert loaded.queue_pending == before
    assert loaded.queue_delivery_ledger == before_ledger
    if location != 'pending':
        assert loaded.queue_delivery_ledger[item['id']]['ledgerExtension'] == {'preserve': True}
    assert not worker._has_dispatchable_items(loaded)


@pytest.mark.parametrize('state', STATES)
def test_snapshot_does_not_restore_detached_unknown_but_recovery_retains_it(state):
    s = session.Session(id='ses_unknown_snapshot', name='Unknown snapshot')
    original = row(state)
    s.queue_delivery_ledger = {original['id']: copy.deepcopy(original)}
    assert worker._migrate_queue_delivery_state(s) is False
    assert s.queue_pending == []
    assert s.queue_delivery_ledger[original['id']] == original
    worker._recover_delivery_states(s)
    assert s.queue_pending[0]['deliveryState'] == state
    assert not worker._is_dispatchable(s.queue_pending[0])


@pytest.mark.parametrize('state', STATES)
def test_unknown_retry_and_duplicate_enqueue_do_not_signal_or_spawn(monkeypatch, state):
    s = session.Session(id='ses_unknown_retry', name='Unknown retry')
    s.queue_delivery_ledger = {'q_unknown': row(state)}
    session.save(s)
    session._cache[s.id] = s
    def forbidden(*args, **kwargs):
        pytest.fail('unknown must not start recovery or provider effects')
    monkeypatch.setattr(worker, '_schedule_session_recovery', forbidden)
    monkeypatch.setattr(worker, 'find_alive_worker_by_session', forbidden)
    result = asyncio.run(worker.retry_pending_item(s.id, 'q_unknown'))
    assert result['status'] == state
    duplicate = asyncio.run(worker.enqueue_user_message(s.id, 'synthetic content', 'client_unknown'))
    assert duplicate['status'] == state
    assert duplicate['duplicate'] is True
    assert duplicate['queueItemId'] == 'q_unknown'
    assert s.queue_pending[0]['deliveryState'] == state


def test_ordinary_legacy_pending_and_sent_receipts_keep_existing_contract():
    s = session.Session(id='ses_queue_ordinary', name='Ordinary compatibility')
    s.queue_pending = [{'text': 'legacy pending', 'source': 'user'},
                       {**row('queued'), 'id': 'q_queued', 'queueItemId': 'q_queued'},
                       {**row('sent_to_cli'), 'id': 'q_sent', 'queueItemId': 'q_sent'}]
    worker._recover_delivery_states(s)
    assert len(s.queue_pending) == 2
    assert all(worker._is_dispatchable(item) for item in s.queue_pending)
    assert s.queue_pending[1]['futureField'] == {'preserve': [1, 2]}
    assert s.queue_delivery_ledger['q_sent']['deliveryState'] == 'sent_to_cli'
    assert worker._recover_delivery_states(s) is False


@pytest.mark.parametrize('state', STATES)
def test_cli_migration_repeats_without_losing_unknown_fields(tmp_path, state):
    # Exercise the operator's actual migration entry without launching a server.
    from scripts import migrate_queue_delivery
    directory = tmp_path / 'operator-copy'
    directory.mkdir()
    s = session.Session(id='ses_unknown_cli', name='Operator copy')
    s.queue_pending = [row(state)]
    path = directory / (s.id + '.json')
    path.write_text(json.dumps(s.to_dict()), encoding='utf-8')
    from unittest.mock import patch
    with patch('sys.argv', ['migrate_queue_delivery.py', '--session-dir', str(directory), '--apply']):
        assert migrate_queue_delivery.main() == 0
        first = path.read_bytes()
        assert migrate_queue_delivery.main() == 0
    assert path.read_bytes() == first
    loaded = json.loads(first)
    assert loaded['queue_pending'][0]['deliveryState'] == state
    assert loaded['queue_pending'][0]['futureField'] == {'preserve': [1, 2]}


@pytest.mark.parametrize('state', STATES)
def test_unknown_migration_never_reaches_actual_claim(monkeypatch, state):
    from types import SimpleNamespace
    s = session.Session(id='ses_unknown_claim', name='Unknown claim')
    s.queue_pending = [row(state)]
    worker._recover_delivery_states(s)
    w = SimpleNamespace(session_id=s.id)
    monkeypatch.setattr(worker, '_session', lambda _: s)
    monkeypatch.setattr(worker, '_process_alive', lambda _: True)
    async def forbidden(*args, **kwargs):
        pytest.fail('unknown must not reach durable reservation')
    monkeypatch.setattr(worker, '_save_receipt', forbidden)
    assert asyncio.run(worker._claim_pending_task(w, 'q_unknown')) is None
    assert s.queue_pending[0]['deliveryState'] == state
