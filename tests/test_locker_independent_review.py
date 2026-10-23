"""Independent safety barriers. Red assertions deliberately remain blocking."""
import asyncio
import json
import hashlib
import shutil
from unittest.mock import AsyncMock

import pytest

from packages.automation.service import Service
from packages.core import worker, session
from packages.interactions.trusted import create as create_bound
from packages.interactions.contracts import InteractionError
from packages.interactions.runtime import bind
from packages.interactions.store import InteractionStore, UserPrincipal
from tests.test_automation_core import action, trigger, until


def test_old_user_and_unknown_recipient_copy_migration(tmp_path):
    source = InteractionStore(tmp_path/'original.sqlite')
    cards = []
    for key, recipient in [('old-open', 'user'), ('old-completed', 'user'), ('foreign', 'unmapped')]:
        cards.append(source.create({'ownerSessionId': 'source', 'recipientId': recipient, 'requestKey': key,
            'card': {'title': key, 'body': 'body', 'answerInput': {}}}))
    source.respond(UserPrincipal('user'), cards[1]['id'], {'requestId': 'old', 'expectedRevision': 0, 'answer': 'done'})
    with source.db() as db:
        db.execute('DROP TABLE response_outbox')
        original_rows = [tuple(row) for row in db.execute('SELECT * FROM cards ORDER BY id')]
    digest = hashlib.sha256(source.path.read_bytes()).hexdigest()
    shutil.copy2(source.path, tmp_path/'copy.sqlite')
    migrated = InteractionStore(tmp_path/'copy.sqlite')
    assert {c['id'] for c in migrated.list_for_user(UserPrincipal('user'))} == {cards[0]['id'], cards[1]['id']}
    assert migrated.get_for_user(UserPrincipal('unmapped'), cards[2]['id'])['recipientId'] == 'unmapped'
    with migrated.db() as db:
        assert [tuple(row) for row in db.execute('SELECT * FROM cards ORDER BY id')] == original_rows
        assert db.execute('SELECT count(*) FROM response_outbox').fetchone()[0] == 0
    assert hashlib.sha256(source.path.read_bytes()).hexdigest() == digest


@pytest.mark.parametrize('change', ['unpinned_trigger', 'pinned_action', 'after_admission'])
def test_fixed_choice_must_not_execute_changed_action(tmp_path, monkeypatch, change):
    """No timing sleeps: stop exactly before the executor reads its Action."""
    monkeypatch.setattr(worker, '_wake_worker', AsyncMock())
    async def run():
        calls = []
        owner=session.create('source',workdir=str(tmp_path))
        approved=session.create('approved-target',workdir=str(tmp_path))
        foreign=session.create('foreign-target',workdir=str(tmp_path))
        async def pause(sid, args):
            calls.append(sid)
            return {'ok': True}
        svc = Service(tmp_path/'automation', worker, pause)
        await svc.start()
        gate, entered = asyncio.Event(), asyncio.Event()
        original = svc.engine.run_attempt
        async def held(run):
            entered.set()
            await gate.wait()
            await original(run)
        monkeypatch.setattr(svc.engine, 'run_attempt', held)
        try:
            adapter = bind(svc)
            svc.engine.actions.put('actions', action([approved.id]))
            spec = {**trigger('selected'), 'eventTypes': ['pan.interaction.responded']}
            svc.ledger.put(spec, 0, True)
            ref = {'id': 'selected'}
            if change != 'unpinned_trigger':
                ref['revision'] = 1
            store = InteractionStore(tmp_path/'cards.sqlite')
            card = create_bound(svc,store,{'ownerSessionId': owner.id, 'recipientId': 'user', 'requestKey': 'fixed',
                'card': {'title': 'Pause approved-target', 'body': 'Pause approved-target only',
                         'choices': [{'id': 'pause', 'label': 'Pause approved-target', 'triggerRef': ref}]}})
            frozen = card['definition']
            def mutate():
                if change == 'unpinned_trigger':
                    svc.engine.actions.put('actions', {**action([foreign.id]), 'id': 'foreign_action'})
                    svc.ledger.put({**spec, 'actionId': 'foreign_action'}, 1, True)
                else:
                    svc.engine.actions.put('actions', action([foreign.id]))
            if change != 'after_admission':
                mutate()
            user = UserPrincipal('user')
            store.respond(user, card['id'], {'requestId': 'once', 'expectedRevision': 0, 'choiceId': 'pause'})
            if change != 'after_admission':
                with pytest.raises(InteractionError,match='changed') as rejected:
                    await store.dispatch(user,card['id'],adapter)
                assert rejected.value.code=='stale'
                assert svc.events.page()['events']==[]
                assert calls==[]
                assert store.get_for_user(user,card['id'])['definition']==frozen
                return
            result = await store.dispatch(user, card['id'], adapter)
            await asyncio.wait_for(entered.wait(), 5)
            if change == 'after_admission':
                mutate()
            gate.set()
            async def done():
                runs = svc.ledger.read()['runs']
                return bool(runs) and all(r['status'] == 'failed' for r in runs.values())
            await until(done)
            assert store.get_for_user(user, card['id'])['definition'] == frozen
            print(json.dumps({'change': change, 'fixedChoice': frozen['choices'],
                'delivery': result['delivery'], 'actualPauseCalls': calls,
                'runActions': [r['actionId'] for r in svc.ledger.read()['runs'].values()]}))
            assert foreign.id not in calls, 'Fixed visible choice executed a different Action target'
            assert calls==[]
            assert 'stale' in str(svc.ledger.read()['runs']) or 'changed' in str(svc.ledger.read()['runs'])
        finally:
            gate.set()
            await svc.close()
    asyncio.run(run())


@pytest.mark.parametrize('deleted', [False, True])
@pytest.mark.parametrize('receipt_present', [False, True])
def test_unknown_receipt_reconciles_after_trigger_revocation(tmp_path, monkeypatch, deleted, receipt_present):
    """Revocation blocks new side effects, not lookup of the original receipt."""
    monkeypatch.setattr(worker, '_wake_worker', AsyncMock())
    async def run():
        owner=session.create('source',workdir=str(tmp_path))
        approved=session.create('approved-target',workdir=str(tmp_path))
        svc = Service(tmp_path/'automation', worker, AsyncMock(return_value={'ok': True}))
        await svc.start()
        try:
            adapter = bind(svc)
            svc.engine.actions.put('actions', action([approved.id]))
            spec = {**trigger('selected'), 'eventTypes': ['pan.interaction.responded']}
            svc.ledger.put(spec, 0, True)
            store = InteractionStore(tmp_path/'cards.sqlite')
            user = UserPrincipal('user')
            card = create_bound(svc,store,{'ownerSessionId': owner.id, 'recipientId': 'user', 'requestKey': 'receipt',
                'card': {'title': 'choice', 'body': 'body', 'choices': [{'id': 'go', 'label': 'Go',
                         'triggerRef': {'id': 'selected', 'revision': 1}}]}})
            store.respond(user, card['id'], {'requestId': 'once', 'expectedRevision': 0, 'choiceId': 'go'})
            append = svc.events.append
            def commit_then_raise(*args):
                append(*args)
                raise OSError('receipt lost after EventStore commit')
            monkeypatch.setattr(svc.events, 'append', commit_then_raise)
            assert (await store.dispatch(user, card['id'], adapter))['delivery']['state'] == 'unknown'
            svc.ledger.put({**spec, 'enabled': False}, 1, True)
            def forbidden_append(*args):
                pytest.fail('Unknown must never append again')
            monkeypatch.setattr(svc.events, 'append', forbidden_append)
            if deleted:
                store.delete(user, card['id'])
            lookup = adapter.lookup
            lookups = []
            async def original_receipt(eid):
                lookups.append(eid)
                return await lookup(eid) if receipt_present else None
            async def forbidden(*args):
                pytest.fail('Unknown must neither authorize current Trigger nor admit')
            adapter.lookup = original_receipt
            adapter.authorize = adapter.accept = forbidden
            reopened = InteractionStore(store.path)
            for _ in range(2):
                if deleted:
                    await reopened.recover_detached(adapter)
                    with reopened.db() as db:
                        state = json.loads(db.execute('SELECT delivery FROM detached_deliveries').fetchone()[0])['state']
                else:
                    state = (await reopened.dispatch(user, card['id'], adapter))['delivery']['state']
                assert state == ('accepted' if receipt_present else 'unknown')
            assert len(lookups) == (1 if receipt_present else 2)
            assert len(set(lookups)) == 1
            assert len(svc.events.page()['events']) == 1
        finally:
            await svc.close()
    asyncio.run(run())
