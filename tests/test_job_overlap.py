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


def test_queue_is_persistent_bounded_and_disable_with_explicit_cancel_preserves_active(h):
    from packages.jobs.final_service import FinalService
    from packages.jobs.final_runtime.executor import TypedExecutor
    from tests.test_job_final_http_service import enabled, job_def, launch, control, run_control, USER
    async def run():
        release, entered = asyncio.Event(), asyncio.Event()
        calls = []
        class Provider:
            async def send_session(self, session_id, text, *, effect_key, source):
                calls.append(copy.deepcopy(effect_key))
                entered.set()
                await release.wait()
                return {'status':'queued', 'messageId':'original'}
        h.service.executor = TypedExecutor(session_provider=Provider())
        job = enabled(h, job_def(h, overlap={'mode':'queue', 'maxActive':1, 'maxPending':1}))
        first = launch(h, job, 'queue-active')['runId']
        task = asyncio.create_task(h.service.dispatch_once())
        try:
            await asyncio.wait_for(entered.wait(), 3)
            second = launch(h, job, 'queue-pending')['runId']
            full = h.client.post('/api/jobs/' + job['id'] + '/runs', json={
                'expectedRevision':job['configRevision'], 'requestKey':'queue-full',
                'input':{}, 'cause':{'type':'manual'}})
            assert full.status_code == 429 and full.json()['error']['code'] == 'capacity_exceeded'
            cold = FinalService(h.root, h.automation.auth)
            try:
                assert cold.get_run(first, actor=USER)['state'] == 'running'
                assert cold.get_run(second, actor=USER)['state'] == 'queued'
                assert len(cold.final.get(job['id'])['runs']) == 2
            finally:
                await cold.close()
            disabled = control(h, 'jobs', job, 'disable', 'disable-queue')
            assert disabled.status_code == 200, disabled.text
            denied = h.client.post('/api/jobs/' + job['id'] + '/runs', json={
                'expectedRevision':job['configRevision'], 'requestKey':'disabled-queue',
                'input':{}, 'cause':{'type':'manual'}})
            assert denied.status_code == 409 and denied.json()['error']['code'] == 'reference_conflict'
            # Definition disable only changes admission. Cancelling a frozen
            # pending Run is an explicit canonical lifecycle operation.
            cancelled = run_control(h, second, 'cancel', 'cancel-waiting')
            assert cancelled.status_code == 202, cancelled.text
            await h.service._drive_run(job['id'], second)
            state = h.service.final.get(job['id'])
            assert state['runs'][second]['state'] == 'cancelled'
            assert state['runs'][first]['state'] == 'running'
            assert not any(a['runId'] == second for a in state['attempts'].values())
            release.set()
            await asyncio.wait_for(task, 5)
            await h.service.dispatch_once()
            done = h.service.final.get(job['id'])
            assert done['runs'][first]['state'] == 'completed'
            assert done['runs'][second]['state'] == 'cancelled'
            assert len(calls) == len(done['attempts']) == len(done['effects']) == 1
        finally:
            release.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    asyncio.run(run())


def test_reduce_parallel_limit_rechecks_claim_after_reference_validation(h, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager
    import threading
    from packages.jobs.final_runtime.executor import TypedExecutor
    from packages.jobs.final_contracts import ContractError
    from tests.test_job_final_http_service import enabled, job_def, launch, USER
    from tests.test_action_groups import canonical_state
    async def run():
        release, started = asyncio.Event(), asyncio.Event()
        calls = []
        class Provider:
            async def send_session(self, session_id, text, *, effect_key, source):
                calls.append(effect_key['runId'])
                started.set()
                await release.wait()
                return {'status':'queued', 'messageId':effect_key['runId']}
        h.service.executor = TypedExecutor(session_provider=Provider())
        cfg = job_def(h, overlap={'mode':'parallel', 'maxActive':3, 'maxPending':0})
        job = enabled(h, cfg)
        ids = [launch(h, job, 'limit-' + str(i))['runId'] for i in range(2)]
        tasks = [asyncio.create_task(h.service._drive_run(job['id'], rid)) for rid in ids]
        gate, proceed = threading.Event(), threading.Event()
        writer_thread = []
        original = h.service.owner.writer
        @contextmanager
        def writer(*args, **kwargs):
            if writer_thread and threading.get_ident() == writer_thread[0]:
                gate.set()
                assert proceed.wait(4), 'limit update writer barrier timed out'
            with original(*args, **kwargs):
                yield
        monkeypatch.setattr(h.service.owner, 'writer', writer)
        changed = copy.deepcopy(cfg)
        changed['overlap']['maxActive'] = 2
        def update():
            writer_thread.append(threading.get_ident())
            return h.service.write('job', job['id'], {'definition':changed,
                'expectedRevision':job['configRevision'], 'requestKey':'reduce-concurrent-limit'}, actor=USER)
        try:
            while len(calls) < 2:
                started.clear()
                await asyncio.wait_for(started.wait(), 3)
            # A preflight reference observation is not authority to commit.
            with h.service.transaction(readonly=True) as db:
                h.service._references(db, 'job', changed, object_id=job['id'])
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(update)
                assert await asyncio.to_thread(gate.wait, 3)
                third = launch(h, job, 'limit-third')['runId']
                ids.append(third)
                tasks.append(asyncio.create_task(h.service._drive_run(job['id'], third)))
                while len(calls) < 3:
                    started.clear()
                    await asyncio.wait_for(started.wait(), 3)
                before = canonical_state(h.service)
                proceed.set()
                with pytest.raises(ContractError) as denied:
                    await asyncio.wait_for(asyncio.wrap_future(pending), 4)
                assert denied.value.code == 'reference_conflict'
            assert canonical_state(h.service) == before
            current = h.service.get('job', job['id'], actor=USER)
            assert current['configRevision'] == 1 and current['overlap']['maxActive'] == 3
            state = h.service.final.get(job['id'])
            assert all(state['runs'][rid]['state'] == 'running' for rid in ids)
            assert len(state['attempts']) == len(calls) == 3
            release.set()
            await asyncio.wait_for(asyncio.gather(*tasks), 5)
            assert all(r['state'] == 'completed' for r in h.service.final.get(job['id'])['runs'].values())
        finally:
            proceed.set()
            release.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    asyncio.run(run())


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
