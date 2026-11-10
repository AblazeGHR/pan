import asyncio
import copy

import pytest
from packages.actions.store import Store
from packages.jobs.action_runtime import Runtime, Conflict
from tests.test_automation_core import action
from tests.test_job_final_http_service import h


def test_parallel_claim_bound_independent_jobs_and_terminal_cleanup(h):
    from tests.test_job_final_http_service import enabled, create, control, job_def, launch, USER
    from packages.jobs.final_runtime.executor import TypedExecutor
    from packages.jobs.final_contracts import ContractError
    async def run():
        release, all_started = asyncio.Event(), asyncio.Event()
        calls = []
        class Provider:
            async def send_session(self, session_id, text, *, effect_key, source):
                calls.append(dict(effect_key))
                if len(calls) == 3:
                    all_started.set()
                await release.wait()
                return {'status': 'queued', 'messageId': effect_key['runId']}
        h.service.executor = TypedExecutor(session_provider=Provider())
        cfg = job_def(h, overlap={'mode': 'parallel', 'maxActive': 2, 'maxPending': 0})
        job = enabled(h, cfg)
        one, two = launch(h, job, 'parallel-one'), launch(h, job, 'parallel-two')
        denied = h.client.post('/api/jobs/' + job['id'] + '/runs', json={
            'expectedRevision': job['configRevision'], 'requestKey': 'parallel-full',
            'input': {}, 'cause': {'type': 'manual'}})
        assert denied.status_code == 429 and denied.json()['error']['code'] == 'capacity_exceeded', denied.text
        other = create(h, value={**cfg, 'name': 'independent'}, key='independent-job')
        response = control(h, 'jobs', other, 'enable', 'enable-independent')
        assert response.status_code == 200, response.text
        other = response.json()['result']['object']
        third = launch(h, other, 'parallel-third')
        dispatch = asyncio.create_task(h.service.dispatch_once())
        try:
            await asyncio.wait_for(all_started.wait(), 4)
            states = h.service.final.get(job['id'])
            assert len([r for r in states['runs'].values() if r['state'] == 'running']) == 2
            assert h.service.final.get(other['id'])['runs'][third['runId']]['state'] == 'running'
            release.set()
            await asyncio.wait_for(dispatch, 5)
        finally:
            release.set()
            if not dispatch.done():
                dispatch.cancel()
                await asyncio.gather(dispatch, return_exceptions=True)
        done = h.service.final.get(job['id'])
        assert len(done['attempts']) == len(done['effects']) == 2
        assert {a['runId'] for a in done['attempts'].values()} == {one['runId'], two['runId']}
        assert all(a['state'] == 'succeeded' for a in done['attempts'].values())
        assert all(r['state'] == 'completed' for r in done['runs'].values())
        assert h.service.final.get(other['id'])['runs'][third['runId']]['state'] == 'completed'
        assert len(calls) == 3 and 'execution' not in done
        await h.service.close()
        before_close = copy.deepcopy(h.service.final.get(job['id']))
        with pytest.raises(ContractError) as stopped:
            h.service.create_run(job['id'], {'expectedRevision': job['configRevision'],
                'requestKey': 'closed-host', 'input': {}, 'cause': {'type': 'manual'}}, actor=USER)
        assert stopped.value.code == 'provider_unavailable'
        assert h.service.final.get(job['id']) == before_close and len(calls) == 3
    asyncio.run(run())


def test_close_while_admission_waits_for_writer_is_zero_write(h, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager
    import threading
    from packages.jobs.final_contracts import ContractError
    from tests.test_job_final_http_service import enabled, USER
    from tests.test_action_groups import canonical_state
    job = enabled(h)
    before = canonical_state(h.service)
    entered, proceed = threading.Event(), threading.Event()
    original = h.service.owner.writer
    @contextmanager
    def blocked_writer(*args, **kwargs):
        entered.set()
        assert proceed.wait(4), 'writer admission barrier timed out'
        with original(*args, **kwargs):
            yield
    monkeypatch.setattr(h.service.owner, 'writer', blocked_writer)
    def admit():
        return h.service.create_run(job['id'], {'expectedRevision': job['configRevision'],
            'requestKey': 'close-waiting-writer', 'input': {}, 'cause': {'type': 'manual'}}, actor=USER)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(admit)
        try:
            assert entered.wait(3), 'write transaction never reached owner barrier'
            asyncio.run(h.service.close())
        finally:
            proceed.set()
        with pytest.raises(ContractError) as stopped:
            pending.result(timeout=4)
    assert stopped.value.code == 'provider_unavailable'
    assert canonical_state(h.service) == before
    assert h.service.final.get(job['id'])['runs'] == {} and h.provider.calls == []


def test_queue_is_persistent_bounded_and_disable_cancels_waiting(tmp_path):
    async def run():
        release=asyncio.Event();entered=asyncio.Event()
        async def pause(*_):entered.set();await release.wait();return {'ok':True}
        store=Store(tmp_path/'actions');store.put('actions',action())
        rt=Runtime(tmp_path/'jobs',store,pause);await rt.start()
        cfg={'name':'queued','actionId':'pause','arguments':{},'enabled':True,'schedule':[{'kind':'interval','intervalSec':60}],
             'overlap':{'mode':'queue','parallelLimit':3}}
        job=rt.create(cfg,True)
        try:
            await rt.launch(job['jobId'],1);await asyncio.wait_for(entered.wait(),2)
            await rt.launch(job['jobId'],1)
            current=rt.get(job['jobId']);assert len(current['pendingRuns'])==1
            disabled=rt.update(job['jobId'],{**cfg,'enabled':False},1,True)
            assert not disabled['pendingRuns']
            assert len(rt.active(rt.get(job['jobId'])))==1
            release.set();await asyncio.gather(*list(rt.tasks.values()))
            await rt.tick({'enabled':True})
            assert len(rt.get(job['jobId'])['attempts'])==1
        finally:release.set();await rt.close()
    asyncio.run(run())


def test_reduce_parallel_limit_rechecks_claim_after_reference_validation(tmp_path,monkeypatch):
    store=Store(tmp_path/'actions');store.put('actions',action())
    rt=Runtime(tmp_path/'jobs',store,None)
    cfg={'name':'race','actionId':'pause','arguments':{},'enabled':True,
         'schedule':[{'kind':'interval','intervalSec':60}], 'overlap':{'mode':'parallel','parallelLimit':3}}
    job=rt.create(cfg,True)
    rt.claim(job['jobId'],'first',1);rt.claim(job['jobId'],'second',1)
    original=rt.templates.transaction
    def interleave(change):
        result=original(change)
        rt.claim(job['jobId'],'third',1)
        return result
    monkeypatch.setattr(rt.templates,'transaction',interleave)
    with pytest.raises(Conflict,match='Active execution count'):
        rt.update(job['jobId'],{**cfg,'overlap':{'mode':'parallel','parallelLimit':2}},1,True)
    current=rt.get(job['jobId'])
    assert current['configRevision']==1 and len(rt.active(current))==3
    assert current['config']['overlap']['parallelLimit']==3


def test_parallel_out_of_order_failure_confirmation_preserves_selected_run(h):
    from tests.test_job_final_http_service import enabled, job_def, launch, run_control
    from packages.jobs.final_runtime.executor import TypedExecutor
    async def run():
        second_finished = asyncio.Event()
        calls = []
        failed_id = None
        class Provider:
            async def send_session(self, session_id, text, *, effect_key, source):
                rid = effect_key['runId']
                calls.append(rid)
                if rid == failed_id and calls.count(rid) == 1:
                    await second_finished.wait()
                    return {'status': 'rejected'}
                second_finished.set()
                return {'status': 'queued', 'messageId': rid}
        h.service.executor = TypedExecutor(session_provider=Provider())
        job = enabled(h, job_def(h, overlap={'mode': 'parallel', 'maxActive': 2, 'maxPending': 0}))
        failed_id = launch(h, job, 'older-failure')['runId']
        newer = launch(h, job, 'newer-success')['runId']
        await asyncio.wait_for(h.service.dispatch_once(), 5)
        before = h.service.final.get(job['id'])
        assert before['runs'][failed_id]['state'] == 'failed'
        assert before['runs'][newer]['state'] == 'completed'
        assert before['runs'][newer]['finishedAt'] <= before['runs'][failed_id]['finishedAt']
        original_newer = copy.deepcopy(before['runs'][newer])
        response = run_control(h, failed_id, 'retry', 'retry-selected-original')
        assert response.status_code == 202 and response.json()['runId'] == failed_id, response.text
        await h.service.dispatch_once()
        state = h.service.final.get(job['id'])
        assert state['runs'][failed_id]['state'] == 'completed'
        assert state['runs'][newer] == original_newer
        assert len([a for a in state['attempts'].values() if a['runId'] == failed_id]) == 2
        assert len([a for a in state['attempts'].values() if a['runId'] == newer]) == 1
        assert calls.count(failed_id) == 2 and calls.count(newer) == 1
        assert set(state['runs']) == {failed_id, newer}
    asyncio.run(run())
