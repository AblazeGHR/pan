"""Executable successors for the legacy acceptance paths retired in R5-P7a.

The per-test inventory is evidence/r5-p7a-reds-auth-20261026/retirement-map.json.
These assert active contracts; they do not resurrect a retired execution host.
"""
import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from packages.automation.service import Service
from packages.jobs.final_contracts import ContractError
from packages.interactions.store import InteractionStore, UserPrincipal
from tests.test_automation_canonical import (
    canonical, subscription, fact, deliver, run, enabled, job_def, control, USER,
)


@pytest.mark.parametrize('path', [
    '/api/jobs/action-jobs', '/api/jobs/job-templates',
    '/api/jobs/action-jobs/old/run-now', '/api/jobs/action-jobs/old/retry',
    '/api/jobs/job-templates/old/instantiate',
])
def test_retired_http_writes_preserve_history_and_canonical_successor_runs(canonical, path):
    h = canonical
    before = copy.deepcopy(h.automation.ledger.read())
    denied = h.client.post(path, json={'expectedRevision': 1})
    assert denied.status_code == 410, denied.text
    assert denied.json()['detail']['code'] == 'retired'
    assert h.automation.ledger.read() == before
    assert not h.service.list('job', actor=USER)['items'] and not h.provider.calls
    # A complete canonical definition freezes both steps at admission. Editing
    # the definition afterward must not change the original Run's effects.
    job = enabled(h, job_def(h, steps=2))
    _, target = subscription(h, job)
    _, receipts = asyncio.run(deliver(h, fact(h)))
    rid = receipts['trigger:' + target['id']]['runId']
    updated = h.client.put('/api/jobs/' + job['id'], json={
        'definition': job_def(h, steps=1, name='new definition'),
        'expectedRevision': job['configRevision'], 'requestKey': 'edit-after-admission'})
    assert updated.status_code == 200, updated.text
    asyncio.run(h.service.dispatch_once())
    assert run(h, rid)['state'] == 'completed'
    assert [text for text, _ in h.provider.calls] == ['0', '1']
    assert len(h.service.final.runs(job['id'])) == 1
    assert h.automation.ledger.read() == before


@pytest.mark.parametrize('deleted', [False, True])
@pytest.mark.parametrize('receipt_present', [False, True])
def test_legacy_locker_choice_never_reenters_retired_execution(tmp_path, monkeypatch,
                                                            deleted, receipt_present):
    """A historical fixed choice stays immutable; unknown is original-ID lookup.

    R5 does not migrate a Card's legacy Trigger/Action binding into a Job. Its
    historical Event may be inspected but cannot execute the retired Action.
    """
    from packages.actions.store import Store
    from packages.interactions.runtime import bind
    from packages.interactions.trusted import create
    from packages.interactions.contracts import InteractionError
    from tests.test_automation_core import action, trigger
    from tests.support.canonical_owner import claim
    from packages.core import session

    owner = session.create('owner', workdir=str(tmp_path))
    target = session.create('target', workdir=str(tmp_path))
    svc = Service(tmp_path / 'automation', SimpleNamespace(workers={}, set_running_observer=lambda cb: None),
                  lambda *a: pytest.fail('Legacy Pause effect must never execute'))
    svc.engine.actions = Store(tmp_path / 'actions')
    svc.engine.actions.put('actions', action([target.id]))
    svc.ledger.put({**trigger('selected'), 'eventTypes': ['pan.interaction.responded']}, 0, True)
    store = InteractionStore(tmp_path / 'cards.sqlite')
    user = UserPrincipal('user')

    async def check():
        # Persist the historical Card and lost receipt at the old boundary,
        # before a canonical owner exists. This prepares history only: no
        # service dispatcher or legacy effect loop is started.
        svc.events.initialize()
        svc.started = True
        adapter = bind(svc)
        host = None
        try:
            request = {'ownerSessionId': owner.id, 'recipientId': 'user',
                'requestKey': 'historical-choice', 'card': {'title': 'Fixed choice', 'body': 'private',
                'choices': [{'id': 'go', 'label': 'Approved target',
                             'triggerRef': {'id': 'selected', 'revision': 1}}]}}
            card = create(svc, store, request)
            frozen = copy.deepcopy(card['definition'])
            store.respond(user, card['id'], {'requestId': 'once', 'expectedRevision': 0, 'choiceId': 'go'})
            append = svc.events.append
            calls = []
            def lost(*args, **kwargs):
                calls.append(args[0]['id'])
                append(*args, **kwargs)
                raise OSError('Lost original Event receipt')
            monkeypatch.setattr(svc.events, 'append', lost)
            assert (await store.dispatch(user, card['id'], adapter))['delivery']['state'] == 'unknown'
            assert card['trustedChoices']['go']['version'] == 1
            frozen_binding = copy.deepcopy(card['trustedChoices'])
            svc.started = False
            host = claim(svc, tmp_path / 'canonical')
            await svc.start()
            with pytest.raises(InteractionError, match='canonical graph unavailable'):
                create(svc, store, {**request, 'requestKey': 'new-after-cutover'})
            assert store.get_for_user(user, card['id'])['trustedChoices'] == frozen_binding
            with pytest.raises(ContractError, match='fence'):
                svc.ledger.put({**trigger('selected'), 'enabled': False}, 1, True)
            async def forbidden(*args):
                pytest.fail('Unknown must never reauthorize or submit')
            lookup = adapter.lookup
            async def original(eid):
                assert eid == calls[0]
                return await lookup(eid) if receipt_present else None
            adapter.lookup = original
            adapter.authorize = adapter.accept = forbidden
            monkeypatch.setattr(svc.events, 'append', lambda *a, **k: pytest.fail('No second Event'))
            if deleted:
                store.delete(user, card['id'])
            reopened = InteractionStore(store.path)
            for _ in range(2):
                if deleted:
                    await reopened.recover_detached(adapter)
                    with reopened.db() as db:
                        state = json.loads(db.execute('SELECT delivery FROM detached_deliveries').fetchone()[0])['state']
                else:
                    row = await reopened.dispatch(user, card['id'], adapter)
                    assert row['definition'] == frozen
                    assert row['trustedChoices'] == frozen_binding
                    state = row['delivery']['state']
                assert state == ('accepted' if receipt_present else 'unknown')
            assert len(calls) == 1 and svc.events.stats()['events'] == 1
            event = svc.events.lookup(calls[0])
            await svc.deliver(event)
            assert svc.events.subscription_receipts(calls[0])['trigger:selected']['status'] == 'unavailable'
            assert not host.list('job', actor=USER)['items'] and not svc.ledger.read()['runs']
        finally:
            await svc.close()
            if host is not None:
                await host.close()
    asyncio.run(check())


def test_legacy_time_source_write_retired_canonical_creator_owns_revision(canonical):
    from packages.scheduler.api import router
    from packages.scheduler.providers import time_definition
    from tests.test_job_final_creators import config, interval
    h = canonical
    # Install on a fresh HTTP app before any request, preserving production auth.
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.state.automation = h.automation
    app.include_router(router)
    with TestClient(app) as client:
        assert client.post('/api/scheduler/event-sources', json={}).status_code == 401
        response = client.post('/api/scheduler/event-sources', headers={
            'Authorization': 'Bearer ' + h.token}, json={})
        assert response.status_code == 410, response.text
    created = h.client.post('/api/event-creators', json={'definition': time_definition('owned-time', config([interval()])),
        'expectedRevision': 0, 'requestKey': 'time-create'})
    assert created.status_code == 201, created.text
    item = created.json()['result']['object']
    assert control(h, 'event-creators', item, 'enable').status_code == 200
    assert control(h, 'event-creators', item, 'disable', 'stale').status_code == 409


def test_query_snapshot_executor_survives_without_trigger_execution_host():
    from packages.actions.executor import execute_dynamic
    from packages.actions import session_query as query
    from packages.automation.engine import Engine
    from tests.test_session_query import query_action, row
    async def check():
        calls, saved, reads = [], {}, []
        async def select(predicate):
            reads.append(predicate)
            return query.select(predicate, [row('a'), row('b')])
        async def progress(**fields):
            saved.update(fields)
        async def pause(sid, args):
            calls.append(sid)
            return {'ok': True}
        result = await execute_dynamic(query_action(), {}, {}, progress, pause, select)
        assert result['ok'] and saved['targetIds'] == ['a', 'b'] and calls == ['a', 'b']
        assert len(reads) == 1
        async def forbidden(*a):
            pytest.fail('Frozen recovery cannot select again')
        calls.clear()
        progress.checkpoint = {'targetIds': saved['targetIds'], 'targetResults': [
            {'sessionId': 'a', 'ok': True}]}
        await execute_dynamic(query_action(), {}, {}, progress, pause, forbidden)
        assert calls == ['b']
    assert not hasattr(Engine, 'execute')
    asyncio.run(check())


def test_retained_plugin_event_subscription_never_reexecutes_history(canonical):
    from tests.test_job_final_admission import test_fa03_retained_legacy_subscription_is_read_only_after_cutover
    test_fa03_retained_legacy_subscription_is_read_only_after_cutover(canonical)


@pytest.mark.parametrize('trusted', [True, False])
def test_trusted_core_binding_crosses_canonical_admission(canonical, trusted):
    from tests.test_eta_final_combined import test_runtime_issue_trusted_system_binding_and_no_payload_identity_fallback
    test_runtime_issue_trusted_system_binding_and_no_payload_identity_fallback(canonical, trusted)


def test_retirement_inventory_has_executable_successors():
    """A retired ID must name a real collected function, never a prose promise."""
    import ast
    root = Path(__file__).resolve().parents[1]
    inventory = json.loads((root / 'evidence/r5-p7a-reds-auth-20261026/retirement-map.json').read_text())
    for entry in inventory['retired']:
        assert entry['reason'] and entry['successors'], entry
        for successor in entry['successors']:
            file, name = successor.split('::')
            functions = {n.name for n in ast.parse((root / file).read_text(encoding='utf-8')).body
                         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
            assert name in functions, successor
