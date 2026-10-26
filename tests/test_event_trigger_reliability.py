"""Six regression barriers against the integrated product; no real profile/provider."""
import asyncio
import copy
import json
import shutil
import threading
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from packages.automation.api import install
from packages.automation.service import Service
from packages.automation.event_store import RETENTION_SECONDS
from packages.plugins.event_adapter import EventConnection
from tests.test_automation_core import action, trigger, event, setup, async_test
from tests.test_event_store import fact
from tests.test_automation_canonical import canonical, subscription, fact as canonical_fact, deliver


async def wait(signal):
    await asyncio.wait_for(signal.wait(), 3)


def local_service(root):
    # Deliberately only the Event/Engine layer. Real lifespan is covered by HTTP.
    svc = Service(root, SimpleNamespace(workers={}), None)
    svc.events.initialize()
    svc.started = True
    return svc


def test_pending_unavailable_subscriptions_do_not_starve_next_page(canonical):
    h = canonical
    job, target = subscription(h)
    svc = h.automation
    for i in range(65):
        svc.events.append(canonical_fact(h, 'blocked-' + str(i)),
            [{'id':'missing-historical-trigger', '_final':True, 'revision':1}])
    async def check():
        await svc.start()
        try:
            await svc.bus.publish_async(svc.core, 'workers.all_idle', {'runningCount':0}, 'ready')
            async def done():
                row = svc.events.lookup('ready')
                return row and row['status'] == 'delivered'
            from tests.test_automation_core import until
            await asyncio.wait_for(until(done), 5)
            assert svc.events.stats()['pending'] == 65
            assert len(h.service.final.runs(job['id'])) == 1
            assert h.service.final.runs(job['id'])[0]['cause']['eventId'] == 'ready'
            assert not h.provider.calls
        finally:
            await svc.close()
    asyncio.run(check())


def test_dispatch_cancellation_flushes_failure_page_and_keeps_unfinished_event(canonical, monkeypatch):
    h = canonical
    svc = h.automation
    for eid in ('failed', 'blocked'):
        svc.events.append(canonical_fact(h, eid), [])
    entered = asyncio.Event()
    async def held(row, errors_batch=None):
        if row['event']['id'] == 'failed':
            raise OSError('first subscription fault')
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(svc, 'deliver', held)
    async def check():
        await svc.start()
        await asyncio.wait_for(entered.wait(), 3)
        await svc.close()
        assert svc.events.lookup('failed')['deliveryCount'] == 1
        assert svc.events.lookup('failed')['lastError'] == 'first subscription fault'
        assert svc.events.lookup('blocked')['deliveryCount'] == 0
        assert svc.events.stats()['pending'] == 2
    asyncio.run(check())


def test_retained_legacy_fact_identity_blocks_conflicting_republication_after_inbox_expiry(canonical):
    h = canonical
    svc = h.automation
    # Load a serialized pre-cutover fixture through an offline fixture writer,
    # never through a runtime mutation API.
    from packages.actions.store import atomic
    old = canonical_fact(h, 'old')
    atomic(svc.ledger.path, {'revision':1, 'triggers':{}, 'runs':{'history':{
        'id':'history', 'event':old, 'status':'uncertain', 'triggerId':'old', 'revision':1}}})
    before = svc.ledger.path.read_bytes()
    svc.started = True
    changed = copy.deepcopy(old)
    changed['payload']['runningCount'] = 1
    with pytest.raises(ValueError, match='different fact'):
        asyncio.run(svc.accept(changed, svc.core))
    assert not svc.events.page()['events'] and svc.ledger.path.read_bytes() == before


@async_test
async def test_publication_does_not_block_same_peer_delivery_ack(tmp_path):
    blocked, release = asyncio.Event(), asyncio.Event()
    async def publish(*args):
        blocked.set()
        await release.wait()
        return {'persisted': True}
    async def eligible(c):
        return True
    diagnostics = []
    manager = SimpleNamespace(service=SimpleNamespace(bus=SimpleNamespace(publish_async=publish)),
        eligible=eligible, stopping=False, diagnostic=lambda *x: diagnostics.append(x))
    c = EventConnection(manager, SimpleNamespace(id='dual', events={'subscriptions':['x']}), None, {}, 'r')
    c.handle, c.ready = object(), True
    stream = asyncio.StreamReader()
    c.process = SimpleNamespace(stdout=stream)
    writes = []
    sent = asyncio.Event()
    async def write(message):
        writes.append(message)
        sent.set()
    c.write = write
    # Avoid OS cleanup: this test owns only in-memory transport tasks.
    c.retire = lambda **kw: c.invalidate()
    reader = asyncio.create_task(c.read())
    delivery = asyncio.create_task(c.deliver({'type':'x','id':'delivery'}))
    try:
        await wait(sent)
        rid = writes[0]['id']
        stream.feed_data((json.dumps({'jsonrpc':'2.0','id':'pub','method':'events.publish',
            'params':{'generation':c.generation,'type':'dual.x','payload':{}}})+'\n').encode())
        await wait(blocked)
        stream.feed_data((json.dumps({'jsonrpc':'2.0','id':rid,'result':{'accepted':True}})+'\n').encode())
        # This timeout is a watchdog, not a scheduling sleep. The release remains held.
        await asyncio.wait_for(asyncio.shield(delivery), .5)
        assert c.ready and not release.is_set()
    finally:
        release.set()
        reader.cancel()
        await asyncio.gather(reader, delivery, return_exceptions=True)
        publications = getattr(c, 'publisher', None)
        if publications:
            publications.cancel()
            await asyncio.gather(publications, return_exceptions=True)


@async_test
async def test_publication_queue_is_bounded_and_cancel_settles_commit(tmp_path, monkeypatch):
    svc = local_service(tmp_path)
    handle = svc.bus.activate('plugin', 'dual')
    svc.bus.register(handle, 'dual.x', {'type':'object'})
    entered, release = threading.Event(), threading.Event()
    append = svc.events.append
    def held(*args):
        entered.set()
        assert release.wait(5)
        return append(*args)
    monkeypatch.setattr(svc.events, 'append', held)
    async def eligible(c):
        return True
    diagnostics, writes = [], []
    full = asyncio.Event()
    manager = SimpleNamespace(service=svc, eligible=eligible, stopping=False,
        diagnostic=lambda *x: diagnostics.append(x))
    c = EventConnection(manager, SimpleNamespace(id='dual'), None, {}, 'r')
    c.handle, c.ready = handle, True
    stream = asyncio.StreamReader()
    c.process = SimpleNamespace(stdout=stream)
    async def write(message):
        writes.append(message)
        if 'queue full' in message.get('error',{}).get('message',''):
            full.set()
    c.write, c.retire = write, lambda **kw: c.invalidate()
    def frame(n):
        return (json.dumps({'jsonrpc':'2.0','id':n,'method':'events.publish','params':{
            'generation':c.generation,'type':'dual.x','payload':{},'event_id':str(n)}})+'\n').encode()
    reader = asyncio.create_task(c.read())
    try:
        stream.feed_data(frame(0))
        assert await asyncio.to_thread(entered.wait, 2)
        for i in range(1,130):
            stream.feed_data(frame(i))
        await wait(full)
        assert c.publications.qsize() == 128 and len(writes) == 1
        assert diagnostics and 'queue full' in diagnostics[-1][1]
        c.publisher.cancel()
        # Observe cancellation delivered while the disk writer is still held.
        await asyncio.sleep(0)
        assert not c.publisher.done()
        release.set()
        await asyncio.gather(c.publisher, return_exceptions=True)
        assert svc.events.page()['events'][0]['event']['id'] == '0'
        assert len(svc.events.page()['events']) == 1
    finally:
        release.set()
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)
        if c.publisher:
            c.publisher.cancel()
            await asyncio.gather(c.publisher, return_exceptions=True)
