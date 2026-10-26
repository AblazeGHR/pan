"""Canonical admission races; no Trigger-owned runner or attempt queue."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import pytest

from tests.test_automation_canonical import canonical, subscription, fact, admit, run, operation, job_def, enabled, control


def test_same_event_concurrent_admission_has_one_run_and_operation(canonical):
    h = canonical
    job, target = subscription(h)
    ev = fact(h)
    with ThreadPoolExecutor(8) as pool:
        receipts = list(pool.map(lambda _: h.service.admit_event(ev, target), range(16)))
    assert len({r['runId'] for r in receipts}) == len({r['operationId'] for r in receipts}) == 1
    assert len(h.service.final.runs(job['id'])) == 1
    assert not h.provider.calls


def test_two_triggers_share_job_but_have_independent_cause_identities(canonical):
    h = canonical
    job, first = subscription(h)
    _, second = subscription(h, job, key='second')
    a, b = admit(h, fact(h), first), admit(h, fact(h), second)
    assert a['runId'] != b['runId'] and a['admissionId'] != b['admissionId']
    assert len(h.service.final.runs(job['id'])) == 2
    asyncio.run(h.service.dispatch_once())
    asyncio.run(h.service.dispatch_once())
    assert len(h.provider.calls) == 2


@pytest.mark.parametrize('mode,max_active', [('queue', 1), ('parallel', 2)])
def test_canonical_job_owns_concurrency_and_queued_cancel(canonical, mode, max_active):
    h = canonical
    job = enabled(h, job_def(h, overlap={'mode':mode, 'maxActive':max_active, 'maxPending':16}))
    job, target = subscription(h, job)
    receipts = [admit(h, fact(h, str(i)), target) for i in range(3)]
    entered, release = asyncio.Queue(), asyncio.Event()
    send = h.provider.send_session
    async def held(*args, **kwargs):
        await entered.put(kwargs['effect_key']['runId'])
        await release.wait()
        return await send(*args, **kwargs)
    h.provider.send_session = held
    async def check():
        dispatch = asyncio.create_task(h.service.dispatch_once())
        try:
            active = [await asyncio.wait_for(entered.get(), 2) for _ in range(max_active)]
            assert set(active) <= {r['runId'] for r in receipts}
            assert entered.empty()
            tail = receipts[-1]['runId']
            assert run(h, tail)['state'] == 'queued'
            assert operation(h, tail, 'cancel').status_code == 202
            release.set()
            await asyncio.wait_for(dispatch, 3)
            await h.service.dispatch_once()
            assert run(h, tail)['state'] == 'cancelled'
            assert len(h.provider.calls) == max_active + (mode == 'queue')
        finally:
            release.set()
            await dispatch
    asyncio.run(check())


def test_serial_cancelled_head_does_not_starve_next_admission(canonical):
    h = canonical
    job, target = subscription(h)
    first = admit(h, fact(h, 'head'), target)
    second = admit(h, fact(h, 'tail'), target)
    assert operation(h, first['runId'], 'cancel').status_code == 202
    asyncio.run(h.service.dispatch_once())
    asyncio.run(h.service.dispatch_once())
    assert run(h, first['runId'])['state'] == 'cancelled'
    assert run(h, second['runId'])['state'] == 'completed'
    assert len(h.provider.calls) == 1
