"""Real query + original Wake + original receipt without replacing Worker ports."""
import asyncio

from packages.core import session, worker, legal_offline
from packages.actions.session_resume import client_message_id
from tests.job_retirement_native_fixture import h  # noqa: F401
from tests.test_session_resume_product import config, illegal
from tests.test_job_final_http_service import enabled, launch


def test_native_rescan_wake_per_fire_identity_and_empty_noop_without_provider_doubles(h):
    workers = set(worker.workers)
    a, b = session.create('owned resume a'), session.create('owned resume b')
    for s in (a, b):
        s.queue_paused = True
        session.save(s)
    illegal(a)
    job = enabled(h, config(h))
    first = launch(h, job, 'first')
    asyncio.run(h.service.dispatch_once())
    history = h.service.final.history(job['id'])
    receipt = history['effects'][0]['receipt']
    assert receipt['targetSelection']['sessionIds'] == [a.id]
    assert receipt['receipt']['matchedCount'] == 1
    row = a.queue_pending[0]
    assert row['text'] == worker.configured_wake_message() and row['source'] == 'automation'
    assert a.queue_paused and not b.queue_pending
    key = receipt['receipt']['results'][0]['effectKey']
    assert h.service.final.query_original(row['id'])[0]['runs'][0]['runId'] == first['runId']
    assert h.service.final.query_original(client_message_id(key))[0]['runs'][0]['runId'] == first['runId']
    assert legal_offline.clear_illegal(a, remember=lambda _: None)
    session.save(a)
    illegal(b)
    second = launch(h, job, 'second')
    asyncio.run(h.service.dispatch_once())
    receipt2 = h.service.final.history(job['id'])['effects'][-1]['receipt']
    assert receipt2['targetSelection']['sessionIds'] == [b.id]
    assert receipt2['receipt']['results'][0]['effectKey']['runId'] == second['runId']
    assert len(a.queue_pending) == len(b.queue_pending) == 1
    assert legal_offline.clear_illegal(b, remember=lambda _: None)
    session.save(b)
    empty = launch(h, job, 'empty')
    asyncio.run(h.service.dispatch_once())
    after = h.service.final.get(job['id'])
    assert after['runs'][empty['runId']]['state'] == 'completed'
    assert after['effects'][-1]['receipt']['receipt'] == {'matchedCount': 0, 'results': []}
    asyncio.run(h.service.dispatch_once())
    assert h.service.final.get(job['id']) == after
    assert len(a.queue_pending) == len(b.queue_pending) == 1
    assert set(worker.workers) == workers
