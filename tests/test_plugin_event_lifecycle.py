"""Deterministic lifecycle barriers: a peer must never hold other peers hostage."""
import asyncio
import psutil

import pytest
from starlette.requests import Request
from starlette.responses import Response

from packages.plugins import event_adapter as adapter
from tests.test_plugin_events import manifest, session, setup, publish, until, PROVIDER, records


async def within(event):
    await asyncio.wait_for(event.wait(), .5)


@pytest.mark.parametrize('operation', ['register', 'revoke', 'session_write'])
def test_hung_start_does_not_block_other_lifecycles_or_http(tmp_path, monkeypatch, operation):
    async def run():
        sessions = {'a': session('a', ['producer'])}
        svc, manager, _ = await setup(tmp_path, sessions, manifest())
        entered, release, b_started = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def start(connection):
            if connection.plugin.id == 'producer':
                entered.set()
                await release.wait()
            connection.handle = await svc.plugin_enabled(connection.plugin.id)
            connection.ready = True
            if connection.plugin.id == 'observer':
                b_started.set()
        monkeypatch.setattr(adapter.EventConnection, 'start', start)
        initial = None
        pending = None
        try:
            if operation == 'revoke':
                sessions['b'] = session('b', ['observer'])
                # Establish B before admitting the blocked A.
                saved = sessions.pop('a')
                await manager.reconcile()
                await within(b_started)
                sessions['a'] = saved
            initial = asyncio.create_task(manager.reconcile())
            await within(entered)
            if operation == 'register':
                sessions['b'] = session('b', ['observer'])
                pending = asyncio.create_task(manager.reconcile())
                await within(b_started)
            elif operation == 'revoke':
                old = manager.connections['observer']
                handle = old.handle
                sessions.pop('b')
                pending = asyncio.create_task(manager.reconcile())
                await asyncio.wait_for(asyncio.shield(pending), .5)
                assert handle not in svc.bus.handles
                assert not await manager.eligible(old)
            else:
                from packages.web import server
                monkeypatch.setattr(server.app.state, 'plugin_events', manager, raising=False)
                completed = asyncio.Event()
                async def next_handler(request):
                    completed.set()
                    return Response('saved')
                request = Request({'type': 'http', 'method': 'PATCH', 'path': '/api/sessions/unrelated', 'headers': []})
                pending = asyncio.create_task(server._reconcile_plugin_event_bindings(request, next_handler))
                await within(completed)
                response = await asyncio.wait_for(asyncio.shield(pending), .5)
                assert response.status_code == 200
            assert not release.is_set()
        finally:
            release.set()
            await asyncio.gather(*(t for t in (initial, pending) if t), return_exceptions=True)
            await manager.close()
            await svc.close()
    asyncio.run(run())


def test_hung_peer_close_does_not_block_other_peer_close(tmp_path, monkeypatch):
    async def run():
        sessions = {'a': session('a', ['producer', 'observer'])}
        svc, manager, _ = await setup(tmp_path, sessions, manifest())
        entered, release, b_closed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def start(c):
            c.ready = True
        original = adapter.EventConnection.close
        async def close(c):
            if c.plugin.id == 'producer':
                entered.set()
                await release.wait()
            await original(c)
            if c.plugin.id == 'observer':
                b_closed.set()
        monkeypatch.setattr(adapter.EventConnection, 'start', start)
        monkeypatch.setattr(adapter.EventConnection, 'close', close)
        pending = None
        try:
            await manager.reconcile()
            await until(lambda: len(manager.connections) == 2)
            sessions.clear()
            pending = asyncio.create_task(manager.reconcile())
            await within(entered)
            await within(b_closed)
            assert not release.is_set()
        finally:
            release.set()
            if pending:
                await pending
            await manager.close()
            await svc.close()
    asyncio.run(run())


def test_close_fences_inflight_reconcile_snapshot(tmp_path, monkeypatch):
    async def run():
        sessions = {'a': session('a', ['producer'])}
        svc, manager, _ = await setup(tmp_path, sessions, manifest())
        entered, release = asyncio.Event(), asyncio.Event()
        starts = []
        original = adapter.io
        async def io(fn, *args):
            if fn == manager.desired:
                snapshot = fn()
                entered.set()
                await release.wait()
                return snapshot
            return await original(fn, *args)
        async def start(c):
            starts.append(c.generation)
            c.ready = True
        monkeypatch.setattr(adapter, 'io', io)
        monkeypatch.setattr(adapter.EventConnection, 'start', start)
        pending = asyncio.create_task(manager.reconcile())
        await within(entered)
        closing = asyncio.create_task(manager.close())
        await until(lambda: manager.stopping)
        release.set()
        try:
            await asyncio.gather(pending, closing)
            assert starts == []
            assert not manager.connections
        finally:
            await manager.close()
            await svc.close()
    asyncio.run(run())


def test_failed_reader_retires_real_process_without_watcher(tmp_path):
    async def run():
        sessions = {'a': session('a', ['producer'])}
        svc, manager, _ = await setup(tmp_path, sessions, manifest())
        try:
            await manager.reconcile()
            await until(lambda: manager.connections.get('producer') and manager.connections['producer'].ready)
            c = manager.connections['producer']
            publish(tmp_path, type='producer.ping', payload={'value': 'x' * 150000})
            await asyncio.wait_for(asyncio.shield(c.reader), 1)
            # No watcher was started. Reader failure must own teardown itself.
            await asyncio.wait_for(c.process.wait(), .5)
            assert not c.ready and c.handle not in svc.bus.handles
            assert c.stderr.done()
        finally:
            await manager.close()
            await svc.close()
    asyncio.run(run())


@pytest.mark.parametrize('stage', ['spawn', 'provider.initialize', 'events.initialize', 'plugin_ready', 'events.ready'])
def test_close_intersects_real_start_without_ready_or_process_leak(tmp_path, monkeypatch, stage):
    async def run():
        sessions = {'a': session('a', ['producer'])}
        svc, manager, _ = await setup(tmp_path, sessions, manifest())
        entered, release = asyncio.Event(), asyncio.Event()
        owned = []
        original_rpc = adapter.EventConnection.request
        original_spawn = asyncio.create_subprocess_exec
        original_ready = svc.plugin_ready
        async def rpc(c, method, params):
            if method == stage:
                entered.set()
                await release.wait()
            return await original_rpc(c, method, params)
        async def spawn(*args, **kwargs):
            process = await original_spawn(*args, **kwargs)
            owned.append((process.pid, psutil.Process(process.pid).create_time()))
            if stage == 'spawn':
                entered.set()
                await release.wait()
            return process
        async def ready():
            if stage == 'plugin_ready':
                entered.set()
                await release.wait()
            await original_ready()
        monkeypatch.setattr(adapter.EventConnection, 'request', rpc)
        monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
        monkeypatch.setattr(svc, 'plugin_ready', ready)
        closing = None
        try:
            await manager.reconcile()
            await within(entered)
            c = manager.connections['producer']
            closing = asyncio.create_task(manager.close())
            await until(lambda: manager.stopping)
            assert not c.ready
            release.set()
            await asyncio.wait_for(asyncio.shield(closing), 2)
            assert not manager.connections and not manager.runners and not manager.cleanups
            assert not records(tmp_path, 'producer') or not any(r['kind'] == 'ready' for r in records(tmp_path, 'producer'))
            assert not [s for s in svc.bus.handles.values() if s['type'] == 'plugin']
            assert all(not psutil.pid_exists(pid) or psutil.Process(pid).create_time() != created for pid, created in owned)
        finally:
            release.set()
            if closing:
                await closing
            await manager.close()
            await svc.close()
    asyncio.run(run())


def test_real_hung_node_handshake_allows_other_registration_and_revocation(tmp_path):
    async def run():
        # Hold a real external provider's response, not its Python host task.
        peer = tmp_path / 'held-peer.mjs'
        peer.write_text(PROVIDER.read_text().replace('const params = message.params;',
            "record({kind: 'request', method: message.method});\n"
            "  if (fs.existsSync(path.join(root, 'hold-' + message.method))) return;\n"
            "  const params = message.params;"))
        root = tmp_path / 'plugins/producer'
        root.mkdir(parents=True)
        (root / 'hold-provider.initialize').touch()
        m = manifest()
        m.mcp_servers[0].args = [str(peer)]
        m.plugins[1]['events']['definitions'] = [{'type': 'observer.alive', 'schemaVersion': 1,
            'description': 'Test peer ready fact', 'schema': {'type': 'object'}}]
        sessions = {'a': session('a', ['producer']), 'b': session('b', ['observer'])}
        svc, manager, _ = await setup(tmp_path, sessions, m)
        try:
            await manager.reconcile()
            await until(lambda: any(r.get('method') == 'provider.initialize' for r in records(tmp_path, 'producer')))
            await until(lambda: svc.bus.available('observer.alive'))
            c = manager.connections['producer']
            assert not c.ready
            b = manager.connections['observer']
            old = b.handle
            sessions.pop('b')
            await asyncio.wait_for(manager.reconcile(), .5)
            assert old not in svc.bus.handles and not svc.bus.available('observer.alive')
            assert not c.ready and c.process.returncode is None
        finally:
            await manager.close()
            await svc.close()
            assert not manager.connections and not manager.cleanups and not manager.runners
    asyncio.run(run())


def test_concurrent_plugin_data_parent_resolution(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path
    import threading
    from packages.plugins.data import plugin_data_dir
    barrier = threading.Barrier(3, timeout=2)
    original = Path.resolve
    def resolve(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        if path.name == 'plugins':
            barrier.wait()
        return result
    monkeypatch.setattr(Path, 'resolve', resolve)
    with ThreadPoolExecutor(max_workers=3) as pool:
        result = list(pool.map(lambda pid: plugin_data_dir(tmp_path, pid), ['producer', 'observer', 'other']))
    assert {p.name for p in result} == {'producer', 'observer', 'other'}
    assert all(p.parent == tmp_path / 'plugins' and p.is_dir() for p in result)


@pytest.mark.parametrize('cause', ['reader_cancel', 'slow_consumer'])
def test_real_peer_self_cleanup_without_watch_cycle(tmp_path, monkeypatch, cause):
    async def run():
        sessions = {'a': session('a', ['observer', 'producer'])}
        svc, manager, _ = await setup(tmp_path, sessions, manifest())
        try:
            await manager.reconcile()
            await manager.settle()
            c = manager.connections['observer']
            handle = c.handle
            identities = [(p.pid, p.create_time()) for p in [psutil.Process(c.process.pid), *psutil.Process(c.process.pid).children(recursive=True)]]
            if cause == 'reader_cancel':
                c.reader.cancel()
            else:
                monkeypatch.setattr(adapter, 'TIMEOUT', .2)
                (tmp_path / 'plugins/observer/stall').touch()
                publish(tmp_path, type='producer.ping', payload={'value': 1})
            # No EventRuntime watcher exists in this test. Cleanup must start
            # on the actual terminal condition rather than a later reconcile.
            await until(lambda: c.cleanup is not None)
            await asyncio.wait_for(asyncio.shield(c.cleanup), .5)
            assert c.closed and not c.ready and c.stderr.done() and c.reader.done()
            assert handle not in svc.bus.handles and 'plugin.observer' not in svc.bus.subscribers
            assert all(not psutil.pid_exists(pid) or psutil.Process(pid).create_time() != created for pid, created in identities)
        finally:
            await manager.close()
            await svc.close()
    asyncio.run(run())


def test_new_snapshot_cannot_be_overwritten_by_slow_old_snapshot(tmp_path, monkeypatch):
    async def run():
        sessions = {'a': session('a', ['producer'])}
        svc, manager, _ = await setup(tmp_path, sessions, manifest())
        entered, release = asyncio.Event(), asyncio.Event()
        original = adapter.io
        first = True
        async def io(fn, *args):
            nonlocal first
            if fn == manager.desired and first:
                first = False
                snapshot = fn()
                entered.set()
                await release.wait()
                return snapshot
            return await original(fn, *args)
        monkeypatch.setattr(adapter, 'io', io)
        pending = asyncio.create_task(manager.reconcile())
        try:
            await within(entered)
            sessions.clear()
            await manager.reconcile()
            release.set()
            await pending
            await manager.settle()
            assert not manager.targets and not manager.connections and not manager.runners
        finally:
            release.set()
            await pending
            await manager.close()
            await svc.close()
    asyncio.run(run())


def test_real_startup_eof_does_not_hot_loop_respawns(tmp_path):
    async def run():
        peer = tmp_path / 'exit-peer.mjs'
        peer.write_text('process.exit(0);')
        m = manifest()
        m.mcp_servers[0].args = [str(peer)]
        svc, manager, _ = await setup(tmp_path, {'a': session('a', ['producer'])}, m)
        settling = None
        try:
            await manager.reconcile()
            settling = asyncio.create_task(manager.settle())
            await asyncio.wait_for(asyncio.shield(settling), 1)
            assert not manager.connections and not manager.runners and not manager.cleanups
            assert any('stdout closed' in d['error'] for d in svc.bus.diagnostics)
        finally:
            await manager.close()
            if settling:
                await settling
            await svc.close()
    asyncio.run(run())
