"""Negative controls for the retired host and immutable historical evidence."""
import asyncio
import copy
from types import SimpleNamespace

import pytest

from packages.automation import chain_engine
from packages.automation.engine import Engine, DependencyUnavailable
from packages.automation.events import Bus
from packages.automation.job_links import Links
from packages.automation.quota_policy import Runtime as QuotaRuntime
from packages.automation.service import Service
from packages.automation.store import Ledger, Conflict
from packages.jobs.final_contracts import ContractError
from packages.jobs.runtime_owner import OwnerFence
from tests.test_automation_core import trigger, event
from tests.test_automation_canonical import canonical


@pytest.mark.parametrize('owner,method', [
    (Engine, 'execute'), (Engine, 'drain'), (Engine, 'run_attempt'),
    (Ledger, 'claim'), (Ledger, 'finish'), (Ledger, 'recover'),
    (Ledger, 'prepare_action'), (Ledger, 'sync_attempt'),
    (Ledger, 'save_chain'), (Ledger, 'defer_unavailable'),
    (chain_engine, 'run_chain'),
])
def test_legacy_execution_api_is_absent(owner, method):
    assert not hasattr(owner, method)


@pytest.mark.parametrize('status', ['queued', 'running', 'succeeded', 'retry_wait', 'uncertain', 'unknown'])
def test_history_is_read_only_and_never_recovered(tmp_path, status):
    ledger = Ledger(tmp_path / 'automation')
    ledger.put(trigger(), 0, True)
    ledger.append(dict(id='old', triggerId='t', triggerRevision=1, event=event(), status=status))
    before = ledger.path.read_bytes()
    engine = Engine(ledger, Bus(), None, execute=lambda *a: pytest.fail('retired executor called'))
    asyncio.run(engine.start())
    asyncio.run(engine.kick())
    with pytest.raises(Conflict, match='read only'):
        ledger.update('old', status='queued')
    with pytest.raises(Conflict, match='read only'):
        ledger.cancel('old', 1)
    with pytest.raises(Conflict, match='original Job admission'):
        ledger.confirm_uncertain('old', 1)
    assert ledger.maintenance(set()) == {'deletedRuns': 0}
    # Maintenance may bump ledger revision, but it must preserve the exact Run.
    assert ledger.read()['runs']['old']['status'] == status
    assert not engine.tasks
    assert b'old' in before


@pytest.mark.parametrize('write', [
    lambda l: l.mutate(lambda s: s.clear()),
    lambda l: l.put(trigger('new'), 0, True),
    lambda l: l.set_enabled('t', False, 1, {'type':'user'}),
    lambda l: l.delete('t', 1),
    lambda l: l.append(dict(id='new', triggerId='t', triggerRevision=1, event=event('new'), status='queued')),
    lambda l: l.update('old', status='queued'),
    lambda l: l.cancel('old', 1),
    lambda l: l.release_event('e'),
    lambda l: l.bind_job('t', 1, 'job_new', {}),
    lambda l: l.import_subscription({'jobId':'job_new'}),
    lambda l: l.record_final_admission('e', 't', {'runId':'new'}),
    lambda l: l.maintenance(set()),
])
def test_owner_fences_every_legacy_ledger_write(tmp_path, write):
    ledger = Ledger(tmp_path / 'automation')
    ledger.put(trigger(), 0, True)
    ledger.append(dict(id='old', recordType='triggerAdmission', triggerId='t', triggerRevision=1,
        event=event(), status='queued', admitted=False))
    before = ledger.path.read_bytes()
    owner = OwnerFence(tmp_path)
    owner.claim(0)
    owner.set_state('active')
    try:
        with pytest.raises(ContractError, match='fence'):
            write(ledger)
        assert ledger.path.read_bytes() == before
        assert ledger.read()['runs']['old']['id'] == 'old'
    finally:
        owner.release()
    with pytest.raises(ContractError):
        write(ledger)  # Closing canonical ownership never reopens legacy writes.
    assert ledger.path.read_bytes() == before


def test_no_owner_service_cannot_start_legacy_host(tmp_path):
    svc = Service(tmp_path / 'automation', SimpleNamespace(workers={}), None)
    with pytest.raises(DependencyUnavailable, match='retired'):
        asyncio.run(svc.start())
    assert not svc.started and svc.job_runtime is None
    assert not svc.ledger.path.exists() and not svc.events.path.exists()


def test_canonical_service_start_close_does_not_drive_old_runtime(canonical):
    h = canonical
    class Retired:
        def __getattr__(self, name):
            pytest.fail('legacy host accessed: ' + name)
    h.automation.job_runtime = Retired()
    h.automation.engine.job_runtime = Retired()
    async def check():
        await h.automation.start()
        first = (h.automation.ingester, h.automation.dispatcher)
        await h.automation.start()
        assert first == (h.automation.ingester, h.automation.dispatcher)
        await h.automation.plugin_ready()
        await h.automation.close()
        assert all(task.done() for task in first)
    asyncio.run(check())


def test_owner_blocks_match_and_old_admission_adapter(canonical):
    eng = canonical.automation.engine
    async def check():
        with pytest.raises(ContractError, match='fence'):
            await eng.match(event())
        with pytest.raises(ContractError, match='fence'):
            await eng.admission_port.target({}, {})
        with pytest.raises(ContractError, match='fence'):
            await eng.admission_port.admit({}, {})
    asyncio.run(check())


def test_pre_cutover_matching_only_calls_admission_port_never_runtime_tick(tmp_path):
    ledger = Ledger(tmp_path / 'automation')
    ledger.put(trigger(), 0, True)
    bus = Bus()
    handle = bus.activate('core', 'workers')
    bus.register(handle, 'workers.all_idle', {'type':'object'})
    engine = Engine(ledger, bus, None, execute=lambda *a: pytest.fail('retired effect'))
    calls = []
    class Runtime:
        async def tick(self, *a):
            pytest.fail('Automation drove an execution host')
    async def target(trigger, row):
        calls.append(('target', row['event']['id']))
        return {'jobId':'job_original'}
    async def admit(event, target):
        calls.append(('admit', event['id']))
        return {'runId':'canonical-original', 'admissionId':'canonical-admission'}
    engine.job_runtime = Runtime()
    engine.admission_port = SimpleNamespace(target=target, admit=admit)
    asyncio.run(engine.match(event()))
    row = next(iter(ledger.read()['runs'].values()))
    assert row['status'] == 'admitted'
    assert row['result']['runId'] == 'canonical-original'
    assert calls == [('target','e'), ('admit','e')] and not engine.tasks


def test_closed_service_rejects_new_events_without_resurrecting_inbox(canonical):
    svc = canonical.automation
    async def check():
        await svc.start()
        await svc.close()
        with pytest.raises(ValueError, match='closed'):
            await svc.accept(event(), svc.core)
        assert not svc.events.page()['events']
        assert svc.ingester is svc.dispatcher is None
    asyncio.run(check())


@pytest.mark.parametrize('method', ['ensure_job', 'tick_jobs', 'create_monitor'])
def test_owner_blocks_legacy_quota_query_host_before_query_or_effect(canonical, method):
    h = canonical
    quota = QuotaRuntime(h.automation.ledger.path.parent, h.root.parent / 'background_jobs')
    before = quota.store.read()
    with pytest.raises(ContractError, match='fence'):
        if method == 'ensure_job':
            quota.ensure_job(before, 'once', 0)
        else:
            asyncio.run(getattr(quota, method)())
    assert quota.store.read() == before


@pytest.mark.parametrize('method', ['reserve', 'checkpoint', 'quarantine', 'remove_owned', 'compose', 'recover', 'cancel'])
def test_legacy_links_refuse_mutation_without_touching_runtime(tmp_path, method):
    ledger = Ledger(tmp_path / 'automation')
    ledger.mutate(lambda s: s.update(linkOperations={'key': {'key':'key', 'jobId':'old', 'status':'pending',
        'request': {'private':'draft'}, 'config': {}}}))
    before = ledger.path.read_bytes()
    runtime = SimpleNamespace()
    links = Links(runtime, ledger, SimpleNamespace(references=lambda sid: []))
    with pytest.raises(Conflict, match='read only'):
        if method in {'compose', 'recover', 'cancel'}:
            asyncio.run(getattr(links, method)())
        else:
            getattr(links, method)()
    assert ledger.path.read_bytes() == before
    assert not vars(runtime)
    assert links.job_pending('old') and not links.source_ready('key')
    public = links.operations()
    assert public == [{'key':'key', 'jobId':'old', 'status':'pending'}]
    public[0]['status'] = 'complete'
    assert links.operations()[0]['status'] == 'pending'
