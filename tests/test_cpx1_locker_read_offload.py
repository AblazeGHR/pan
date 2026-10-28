"""Bounded read mutations preserve the original per-item SQLite contract."""
import asyncio
from contextlib import contextmanager
import threading
from unittest.mock import AsyncMock, Mock

import httpx
from fastapi import FastAPI

from packages.interactions import api
from packages.interactions.store import InteractionStore, UserPrincipal


def _app():
    app = FastAPI()
    app.state.user_inbox_principal = lambda request: UserPrincipal(request.headers.get("x-test-user", "user"))
    api.register(app)

    @app.get("/__test/ping")
    async def ping():
        return {"alive": True}
    return app


def test_event_loop_and_cancellation_keep_one_writer(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls = []
    class Store:
        def mark_message_read(self, principal, item_id, read):
            calls.append((item_id, threading.current_thread() is threading.main_thread()))
            if item_id == "held":
                entered.set()
                assert release.wait(5)
            return {"id": item_id, "read": read}
    monkeypatch.setattr(api, "get_store", Store)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app()), base_url="http://test") as client:
            first = asyncio.create_task(client.put('/api/user-locker-messages/held/read', json={'read': True}))
            try:
                assert await asyncio.to_thread(entered.wait, 2)
                assert (await asyncio.wait_for(client.get('/__test/ping'), 1)).json() == {'alive': True}
                queued = asyncio.create_task(client.put('/api/user-locker-messages/queued/read', json={'read': True}))
                await asyncio.sleep(0)  # let the queued request reach the bounded gate
                queued.cancel()
                try:
                    await queued
                except asyncio.CancelledError:
                    pass
                assert calls == [('held', False)]
                release.set()
                assert (await first).json()['message']['id'] == 'held'
                final = await client.put('/api/user-locker-messages/final/read', json={'read': True})
                assert final.json()['message']['id'] == 'final'
                assert calls == [('held', False), ('final', False)]
            finally:
                release.set()
    asyncio.run(run())


def test_real_store_concurrent_identity_authorization_and_durability(monkeypatch, tmp_path):
    path = tmp_path/'cards.sqlite'
    store = InteractionStore(path)
    rows = [store.accept_message('source', 'user', {'messageId': str(i), 'role': 'assistant',
            'content': 'fixture', 'ts': '2026-10-23T12:00:00Z'}) for i in range(12)]
    cards = [store.create({'ownerSessionId': 'source', 'recipientId': 'user', 'requestKey': str(i),
             'card': {'title': 'fixture', 'body': 'fixture'}}) for i in range(3)]
    monkeypatch.setattr(api, 'get_store', lambda: InteractionStore(path))

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app()), base_url='http://test') as client:
            requests = [client.put(f"/api/user-locker-messages/{r['id']}/read", json={'read': True}) for r in rows]
            requests += [client.put(f"/api/user-inbox/{r['id']}/read", json={'read': True}) for r in cards]
            responses = await asyncio.gather(*requests)
            assert all(r.status_code == 200 for r in responses)
            assert [r.json()['message']['id'] for r in responses[:len(rows)]] == [r['id'] for r in rows]
            assert [r.json()['card']['id'] for r in responses[len(rows):]] == [r['id'] for r in cards]
            duplicates = await asyncio.gather(*[
                client.put(f"/api/user-locker-messages/{rows[0]['id']}/read", json={'read': True})
                for _ in range(2)
            ])
            assert all(r.status_code == 200 and r.json()['message']['id'] == rows[0]['id'] for r in duplicates)
            assert sorted(r.json()['message']['revision'] for r in duplicates) == [rows[0]['revision'] + 2, rows[0]['revision'] + 3]
            denied = await client.put(f"/api/user-locker-messages/{rows[0]['id']}/read", headers={'x-test-user':'other'}, json={'read': False})
            assert denied.status_code == 404
            denied = await client.put(f"/api/user-inbox/{cards[0]['id']}/read", headers={'x-test-user':'other'}, json={'read': False})
            assert denied.status_code == 404
    asyncio.run(run())
    reopened = InteractionStore(path)
    for index, row in enumerate(rows):
        value = reopened.get_message(UserPrincipal('user'), row['id'])
        assert value['read'] and value['revision'] == row['revision'] + (3 if index == 0 else 1)
    for card in cards:
        assert reopened.get_for_user(UserPrincipal('user'), card['id'])['read']
    with reopened.db() as db:
        assert db.execute('SELECT count(*) FROM response_outbox').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM rejection_outbox').fetchone()[0] == 0


def test_cancelled_started_write_keeps_slot_until_thread_finishes(monkeypatch):
    entered, release, overlap = threading.Event(), threading.Event(), threading.Event()
    class Store:
        def mark_message_read(self, principal, item_id, read):
            if item_id == 'held':
                entered.set()
                assert release.wait(5)
            elif not release.is_set():
                overlap.set()
            return {'id': item_id, 'read': read}
    monkeypatch.setattr(api, 'get_store', Store)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=_app()), base_url='http://test') as client:
            first = asyncio.create_task(client.put('/api/user-locker-messages/held/read', json={'read': True}))
            try:
                assert await asyncio.to_thread(entered.wait, 2)
                first.cancel()
                try:
                    await first
                except asyncio.CancelledError:
                    pass
                second = asyncio.create_task(client.put('/api/user-locker-messages/next/read', json={'read': True}))
                assert (await client.get('/__test/ping')).status_code == 200
                assert not overlap.is_set()
                release.set()
                assert (await second).json()['message']['id'] == 'next'
                assert not overlap.is_set()
            finally:
                release.set()
    asyncio.run(run())


def test_commit_failure_is_not_success_and_rolls_back_both_kinds(monkeypatch, tmp_path):
    from packages.core import notifications, worker
    broadcasts, reminders = AsyncMock(), Mock()
    monkeypatch.setattr(worker, '_broadcast', broadcasts)
    monkeypatch.setattr(notifications, 'dispatch_reminder', reminders)
    path = tmp_path/'cards.sqlite'
    store = InteractionStore(path)
    row = store.accept_message('source', 'user', {'messageId':'failure', 'role':'assistant',
            'content':'fixture', 'ts':'2026-10-23T12:00:00Z'})
    card = store.create({'ownerSessionId':'source', 'recipientId':'user', 'requestKey':'failure',
                         'card':{'title':'fixture','body':'fixture'}})
    class Failure(InteractionStore):
        fail = False
        @contextmanager
        def db(self):
            with super().db() as conn:
                yield conn
                if self.fail:
                    raise OSError('controlled commit failure')
    failing = Failure(path)
    failing.fail = True
    monkeypatch.setattr(api, 'get_store', lambda: failing)

    async def run():
        transport = httpx.ASGITransport(app=_app(), raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
            for path_ in [f"/api/user-locker-messages/{row['id']}/read", f"/api/user-inbox/{card['id']}/read"]:
                assert (await client.put(path_, json={'read': True})).status_code == 500
    asyncio.run(run())
    broadcasts.assert_not_awaited()
    reminders.assert_not_called()
    reopened = InteractionStore(path)
    assert not reopened.get_message(UserPrincipal('user'), row['id'])['read']
    assert not reopened.get_for_user(UserPrincipal('user'), card['id'])['read']
    with reopened.db() as db:
        assert db.execute('SELECT count(*) FROM response_outbox').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM rejection_outbox').fetchone()[0] == 0
