"""Slow storage reads must not occupy the canonical pulse's event loop."""
import asyncio
import threading
import time

from tests.test_job_final_http_service import h  # noqa: F401


def test_idle_pulse_scans_yield_to_heartbeat_without_writes(h, monkeypatch):
    service = h.service
    # Recovery/provider work has independent contracts; this test isolates the
    # three pulse snapshot scans against an actual initialized canonical store.
    async def no_recovery():
        return None
    monkeypatch.setattr(service, 'recover_session_receipts', no_recovery)
    monkeypatch.setattr(service, 'recover_external_receipts', no_recovery)
    async def unexpected_notification(_row):
        raise AssertionError('idle snapshot must not send a notification')
    monkeypatch.setattr(service, 'outbox_sender', unexpected_notification)
    scans = []
    original_all = service._all
    main_thread = threading.get_ident()
    def slow_all(db, kind):
        scans.append((kind, threading.get_ident(), db.execute('PRAGMA query_only').fetchone()[0]))
        time.sleep(.08)
        return original_all(db, kind)
    monkeypatch.setattr(service, '_all', slow_all)
    async def run():
        loop = asyncio.get_running_loop()
        samples = []
        stop = asyncio.Event()
        async def heartbeat():
            previous = loop.time()
            while not stop.is_set():
                await asyncio.sleep(.005)
                current = loop.time()
                samples.append(current - previous)
                previous = current
        task = asyncio.create_task(heartbeat())
        await asyncio.sleep(.01)
        try:
            await service.tick()
        finally:
            stop.set()
            await task
        assert max(samples) < .06, samples
    asyncio.run(run())
    assert [kind for kind, _, _ in scans] == ['trigger', 'job', 'job']
    assert all(tid != main_thread and readonly == 1 for _, tid, readonly in scans), scans
    assert h.provider.calls == []
    with service.transaction(readonly=True) as db:
        assert original_all(db, 'job') == []


def test_dispatch_snapshot_cannot_execute_after_owner_release(h, monkeypatch):
    import pytest
    from packages.jobs import final_contracts as fc
    from tests.test_job_final_http_service import enabled, launch
    service = h.service
    item = enabled(h)
    launch(h, item)
    service.owner.claim(0)
    service.owner.set_state('active')
    before = service.final.store.get(item['id'])
    original = service._dispatch_candidates
    def lose_owner():
        candidates = original()
        assert len(candidates) == 1
        service.owner.release()
        return candidates
    monkeypatch.setattr(service, '_dispatch_candidates', lose_owner)
    with pytest.raises(fc.ContractError, match='fence'):
        asyncio.run(service.dispatch_once())
    assert service.final.store.get(item['id']) == before
    assert h.provider.calls == []


def test_outbox_snapshot_cannot_send_after_owner_release(h, monkeypatch):
    import pytest
    from packages.jobs import final_contracts as fc
    from tests.test_job_final_http_service import enabled, launch
    service = h.service
    item = enabled(h)
    launch(h, item)
    asyncio.run(service.dispatch_once())
    assert len(h.provider.calls) == 1
    service.owner.claim(0)
    service.owner.set_state('active')
    before = service.final.store.get(item['id'])
    sent = []
    async def sender(row):
        sent.append(row)
        return {'outcome': 'succeeded'}
    monkeypatch.setattr(service, 'outbox_sender', sender)
    original = service._pending_outbox_snapshot
    def lose_owner():
        pending = original()
        assert len(pending) == 1
        service.owner.release()
        return pending
    monkeypatch.setattr(service, '_pending_outbox_snapshot', lose_owner)
    with pytest.raises(fc.ContractError, match='fence'):
        asyncio.run(service.deliver_outbox())
    assert service.final.store.get(item['id']) == before
    assert len(h.provider.calls) == 1 and sent == []
