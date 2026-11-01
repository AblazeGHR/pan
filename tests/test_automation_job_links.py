"""Owned-profile composition, lifecycle, retry and real downstream admission."""
import asyncio
import copy
from datetime import datetime

import pytest

from packages.automation.job_links import Links
from packages.jobs.action_runtime import Runtime as Jobs
from packages.scheduler.time_events import Sources,Runtime as Timers
from packages.jobs.action_templates import Conflict
from packages.automation.store import Conflict as LedgerConflict
from tests.test_scheduler_time_events import service
from tests.test_job_action_chain import _action,_config,_steps
from tests.test_automation_core import async_test,settled,until
from tests.test_automation_canonical import canonical, job_def, run, admit, fact, control, USER
from packages.jobs.final_service import FinalService
from packages.jobs.final_contracts import ContractError
from packages.scheduler.providers import time_definition
from tests.test_job_final_creators import config, interval


async def setup(tmp_path):
    svc=await service(tmp_path, with_runtime=False)
    for aid in ('one','two'):svc.engine.actions.put('actions',_action(aid))
    async def pause(*args):return {'ok':True}
    jobs=Jobs(tmp_path/'jobs',svc.engine.actions,pause);await jobs.start()
    sources=Sources(tmp_path/'jobs')
    svc.job_runtime=jobs;svc.engine.job_runtime=jobs
    svc.links=Links(jobs,svc.ledger,sources)
    jobs.time_events=Timers(sources,svc.accept_time,[],source_ready=svc.links.source_ready)
    return svc,jobs,sources


async def close(svc,jobs):
    await svc.engine.close();await jobs.close();await svc.bus.close()


def graph(h, *, shared=None):
    creator = ({'mode':'reference', 'key':'clock', 'ref':shared} if shared else
        {'mode':'create', 'key':'clock', 'definition':time_definition('clock', config([interval()]))})
    return {'job':job_def(h), 'creators':[creator], 'triggers':[{
        'key':'tick', 'definition':{'name':'tick', 'eventTypes':['scheduler.time.v1'],
            'creatorSelector':{'mode':'ids', 'ids':[]}},
        'targetJobKey':'job', 'creatorKeys':['clock']}]}


def compose(h, value=None, key='composition', **fields):
    return h.service.compositions.compose(request_key=key, desired_graph=value or graph(h), activate=True,
        authorization_evidence={'principal':USER}, **fields)


def test_canonical_composition_replays_ids_and_never_merges_equal_sources(canonical):
    h = canonical
    first = compose(h)
    again = compose(h)
    second = compose(h, key='separate')
    assert first['objectIds'] == again['objectIds']
    assert first['objectIds']['creators'] != second['objectIds']['creators']
    assert len(h.service.creators.list()) == 2
    assert len(h.service.list('job', actor=USER)['items']) == 2
    assert not h.provider.calls


@pytest.mark.parametrize('phase', ['links', 'activation'])
def test_canonical_partial_composition_is_fenced_and_resume_reuses_ids(canonical, monkeypatch, phase):
    h = canonical
    name = 'verify_links' if phase == 'links' else 'commit_activation'
    original = getattr(h.service, name)
    def fail(*args, **kwargs):
        raise OSError('composition boundary fault')
    monkeypatch.setattr(h.service, name, fail)
    with pytest.raises(OSError, match='boundary fault'):
        compose(h)
    failed = h.service.creators.composition_for_key('composition')
    creator_id = failed['objectIds']['creators']['clock']
    assert h.service.creators.get(creator_id)['state'] == 'disabled'
    assert h.service.get('job', failed['objectIds']['job'], actor=USER)['state'] == 'disabled'
    assert not h.provider.calls
    monkeypatch.setattr(h.service, name, original)
    done = compose(h)
    assert done['phase'] == 'complete' and done['objectIds'] == failed['objectIds']
    assert len(h.service.creators.list()) == 1


def test_shared_creator_survives_other_composition_failure_and_revision_conflicts(canonical, monkeypatch):
    h = canonical
    first = compose(h)
    cid = first['objectIds']['creators']['clock']
    shared = h.service.creators.ref(cid)
    original = h.service.verify_links
    def fail(*args):
        raise OSError('shared graph fault')
    monkeypatch.setattr(h.service, 'verify_links', fail)
    with pytest.raises(OSError):
        compose(h, graph(h, shared=shared), key='shared', expected_refs=[shared])
    assert h.service.creators.get(cid)['state'] == 'enabled'
    monkeypatch.setattr(h.service, 'verify_links', original)
    done = compose(h, graph(h, shared=shared), key='shared', expected_refs=[shared])
    assert done['objectIds']['creators']['clock'] == cid
    assert len(h.service.creators.list()) == 1
    with pytest.raises(ContractError, match='changed'):
        compose(h, graph(h), key='shared')


def test_disabling_creator_preserves_already_admitted_canonical_run(canonical):
    h = canonical
    composed = compose(h)
    ids = composed['objectIds']
    target = h.service.get('trigger', ids['triggers']['tick'], actor=USER)
    # Graph activation is separate from a user's execution authorization.
    # Exercise the public lifecycle to bind that authority, never inject it.
    disabled = control(h, 'triggers', target, 'disable', 'user-disable').json()['result']['object']
    authorized = control(h, 'triggers', disabled, 'enable', 'user-enable')
    assert authorized.status_code == 200, authorized.text
    target = authorized.json()['result']['object']
    event = {**fact(h), 'type':'scheduler.time.v1', 'creatorId':ids['creators']['clock']}
    receipt = admit(h, event, target)
    creator = h.service.creators.get(ids['creators']['clock'])
    h.service.creators.set_state(creator['id'], 'disabled', expected_state_revision=creator['stateRevision'])
    asyncio.run(h.service.dispatch_once())
    assert run(h, receipt['runId'])['state'] == 'completed'
    assert len(h.provider.calls) == 1
    assert len(h.service.final.runs(ids['job'])) == 1


def test_canonical_composition_reopens_by_original_operation_and_source_links(canonical):
    h = canonical
    first = compose(h)
    restored = FinalService(h.root, h.automation.auth, executor=h.service.executor, owner=h.service.owner)
    again = restored.compositions.compose(request_key='composition', desired_graph=graph(h), activate=True,
        authorization_evidence={'principal':USER})
    assert again['operationId'] == first['operationId']
    assert again['objectIds'] == first['objectIds']
    trigger = restored.get('trigger', first['objectIds']['triggers']['tick'], actor=USER)
    assert trigger['targetJobId'] == first['objectIds']['job']
    assert trigger['creatorSelector']['ids'] == [first['objectIds']['creators']['clock']]
