"""Owned Node stdio event peers and the real automation Service/runner."""
import asyncio
import copy
import json
from pathlib import Path
import shutil
import os
import sys
from types import SimpleNamespace

import psutil
import pytest

from packages.actions.store import Store
from packages.automation.service import Service
from packages.core.manifest_loader import ManifestConfig, McpServer
from packages.plugins.catalog import Catalog
from packages.plugins.event_adapter import EventRuntime
from packages.plugins.runtime import Runtime
from tests.test_automation_core import action, trigger

NODE = shutil.which('node')
PROVIDER = Path(__file__).resolve().parents[1] / 'examples/event-plugin/provider.mjs'


def manifest():
    assert NODE, 'Node is required for external non-Python transport acceptance'
    spec = {'provider': {'transport': 'stdio_jsonrpc', 'command_ref': 'event-process'},
            'definitions': [{'type': 'producer.ping', 'schemaVersion': 1, 'description': 'Owned external fact',
                             'schema': {'type': 'object', 'required': ['value'], 'properties': {'value': {'type': 'integer'}}, 'additionalProperties': False}}],
            'subscriptions': []}
    return ManifestConfig(mcp_servers=[McpServer('event-process', command=NODE, args=[str(PROVIDER)])],
        plugins=[{'protocol': 'pan-plugin/v1', 'id': pid, 'source': {'kind': 'mcp', 'server_ref': 'event-process'},
                  'events': copy.deepcopy(spec) if pid == 'producer' else {**copy.deepcopy(spec), 'definitions': [], 'subscriptions': ['producer.ping']}}
                 for pid in ('producer', 'observer', 'other')])


def session(sid, pids, mode='gateway'):
    return SimpleNamespace(id=sid, name=sid, adapter_config={'plugin_bindings': [
        {'plugin_id': p, 'enabled': True, 'mode': mode} for p in pids]})


def records(root, pid):
    file = root / 'plugins' / pid / 'observed.jsonl'
    if not file.exists():
        return []
    return [json.loads(line) for line in file.read_text().splitlines()]


async def until(fn, timeout=10):
    async with asyncio.timeout(timeout):
        while not (value := fn()):
            await asyncio.sleep(.02)
        return value


def publish(root, **fields):
    target = root / 'plugins/producer/publish.json'
    temporary = target.with_suffix('.tmp')
    temporary.write_text(json.dumps(fields))
    temporary.replace(target)


async def setup(root, sessions, m):
    calls = []
    async def pause(sid, args):
        calls.append(sid)
        return {'ok': True}
    workers = SimpleNamespace(workers={}, set_running_observer=lambda callback: None)
    svc = Service(root / 'automation', workers, pause)
    svc.engine.actions = Store(root / 'actions')
    svc.engine.actions.put('actions', action())
    svc.ledger.put({**trigger(), 'eventTypes': ['producer.ping']}, 0, True)
    await svc.start()
    manager = EventRuntime(svc, lambda: m, lambda: list(sessions.values()), root)
    return svc, manager, calls


def test_catalog_event_contract_and_legacy():
    m = manifest()
    assert Catalog(m).get('producer').events['definitions'][0]['description']
    for mutate in (
        lambda spec: spec['definitions'][0].update(type='other.ping'),
        lambda spec: spec['definitions'].append(copy.deepcopy(spec['definitions'][0])),
        lambda spec: spec['definitions'][0].update(schema={'$ref': 'file:///no'}),
        lambda spec: spec['provider'].update(command_ref='missing'),
        lambda spec: spec['definitions'][0].update(schemaVersion=0),
    ):
        bad = copy.deepcopy(m)
        mutate(bad.plugins[0]['events'])
        with pytest.raises(ValueError):
            Catalog(bad)
    old = ManifestConfig(mcp_servers=[McpServer('old', command='unused')])
    assert Catalog(old).get('old').events is None


@pytest.mark.parametrize('mode', ['standalone', 'pan_native', 'gateway'])
def test_real_external_fanout_action_and_shared_session_lifetime(tmp_path, mode):
    async def run():
        sessions = {'a': session('a', ['observer', 'other', 'producer'], mode),
                    'b': session('b', ['producer'], mode)}
        svc, manager, calls = await setup(tmp_path, sessions, manifest())
        owned = []
        try:
            await manager.start()
            await manager.settle()
            assert len(manager.connections) == 3, list(svc.bus.diagnostics)
            assert all(c.ready for c in manager.connections.values()), list(svc.bus.diagnostics)
            for c in manager.connections.values():
                owned.extend((p.pid, p.create_time()) for p in [psutil.Process(c.process.pid), *psutil.Process(c.process.pid).children(recursive=True)])
            old = manager.connections['producer']
            assert set(old.members) == {'a', 'b'}
            # No directory/list/tool request was made: events are already alive.
            publish(tmp_path, type='producer.ping', payload={'value': 7}, event_id='owned-event')
            await until(lambda: calls == ['selected'])
            await until(lambda: all(any(r['kind'] == 'delivery' and r['event']['id'] == 'owned-event' for r in records(tmp_path, pid)) for pid in ('observer', 'other')))
            row = next(iter(svc.ledger.read()['runs'].values()))
            await until(lambda: svc.ledger.read()['runs'][row['id']]['status'] == 'succeeded')
            assert row['event']['source'] == {'type': 'plugin', 'id': 'producer'}
            assert svc.bus.definitions['producer.ping']['description'] == 'Owned external fact'
            sessions['a'].adapter_config['plugin_bindings'] = []
            sessions['a'].adapter_config['plugin_selection_revision'] = 1
            await manager.reconcile()
            await manager.settle()
            assert manager.connections['producer'] is old and old.ready
            assert 'observer' not in manager.connections
            sessions['b'].adapter_config['plugin_bindings'] = []
            sessions['b'].adapter_config['plugin_selection_revision'] = 1
            # Check at admission even before the watch/reconciliation fires.
            assert not await manager.eligible(old)
            await manager.reconcile()
            await manager.settle()
            assert not svc.bus.available('producer.ping') and not manager.connections
            with pytest.raises(ValueError):
                svc.bus.publish(old.handle, 'producer.ping', {'value': 8})
            sessions['b'] = session('b', ['producer'], mode)
            sessions['b'].adapter_config['plugin_selection_revision'] = 2
            await manager.reconcile()
            await manager.settle()
            new = manager.connections['producer']
            assert new.ready and new.generation != old.generation
            assert svc.ledger.read()['triggers']['t']['enabled']
            publish(tmp_path, type='producer.ping', payload={'value': 8}, event_id='after-enable')
            await until(lambda: len(calls) == 2)
            await until(lambda: len([r for r in svc.ledger.read()['runs'].values() if r['status'] == 'succeeded']) == 2)
            evidence = {'mode': mode, 'runs': svc.ledger.read()['runs'], 'oldGeneration': old.generation,
                        'newGeneration': new.generation, 'records': {pid: records(tmp_path, pid) for pid in ('producer', 'observer', 'other')}}
            out = Path('artifacts/event-plugin-adapter')
            out.mkdir(parents=True, exist_ok=True)
            (out / f'{mode}-transport.json').write_text(json.dumps(evidence, indent=2))
        finally:
            await manager.close()
            await svc.close()
            assert not manager.connections and not svc.bus.handles and not svc.engine.tasks
            assert all(not psutil.pid_exists(pid) or psutil.Process(pid).create_time() != created for pid, created in owned)
    asyncio.run(run())


def test_external_rejections_disconnect_and_queue_restore(tmp_path):
    async def run():
        sessions = {'a': session('a', ['producer'])}
        svc, manager, calls = await setup(tmp_path, sessions, manifest())
        try:
            await manager.reconcile()
            await manager.settle()
            for fields in (
                {'source': {'type': 'core', 'id': 'workers'}, 'type': 'producer.ping', 'payload': {'value': 1}},
                {'type': 'workers.all_idle', 'payload': {'runningCount': 0}},
                {'type': 'producer.ping', 'payload': {'value': 'wrong'}},
                {'type': 'producer.ping', 'payload': {'value': 1}, 'generation': 'old'},
                {'type': 'producer.ping', 'payload': {'value': 'x' * 66000}},
            ):
                before = len([r for r in records(tmp_path, 'producer') if r['kind'] == 'publication'])
                publish(tmp_path, **fields)
                await until(lambda: len([r for r in records(tmp_path, 'producer') if r['kind'] == 'publication']) > before)
                assert [r for r in records(tmp_path, 'producer') if r['kind'] == 'publication'][-1].get('error')
            assert not calls and not svc.ledger.read()['runs']
            publish(tmp_path, type='producer.ping', payload={'value': 'x' * 150000})
            await until(lambda: not svc.bus.available('producer.ping'))
            assert any('limit' in d['error'].lower() for d in svc.bus.diagnostics)
            await manager.reconcile()
            await manager.settle()
            assert manager.connections['producer'].ready
            # External EOF revokes the actual handle, not a payload credential.
            connection = manager.connections['producer']
            if connection.process.returncode is None:
                connection.process.kill()
            await until(lambda: not svc.bus.available('producer.ping'))
            assert not connection.ready
            # Persist an admitted queued Run with an unavailable dependency.
            run = {'id': 'waiting', 'triggerId': 't', 'triggerRevision': 1, 'actionId': 'pause',
                   'event': {'id': 'waiting-event', 'type': 'producer.ping', 'payload': {'value': 1}},
                   'arguments': {}, 'state': {}, 'status': 'queued', 'admitted': True}
            svc.ledger.append(run)
            await svc.engine.kick()
            assert svc.ledger.read()['runs']['waiting']['status'] == 'queued'
            await manager.reconcile()
            await manager.settle()
            await until(lambda: svc.ledger.read()['runs']['waiting']['status'] == 'succeeded')
            assert calls == ['selected']
            assert list(svc.bus.diagnostics)
        finally:
            await manager.close()
            await svc.close()
    asyncio.run(run())


def test_slow_external_subscriber_isolated_and_bounded(tmp_path, monkeypatch):
    async def run():
        from packages.plugins import event_adapter
        sessions = {'a': session('a', ['observer', 'other', 'producer'])}
        svc, manager, calls = await setup(tmp_path, sessions, manifest())
        try:
            await manager.reconcile()
            await manager.settle()
            assert 'producer' in manager.connections, list(svc.bus.diagnostics)
            monkeypatch.setattr(event_adapter, 'TIMEOUT', .3)
            (tmp_path / 'plugins/observer/stall').touch()
            publish(tmp_path, type='producer.ping', payload={'value': 1})
            await until(lambda: calls and any(r['kind'] == 'delivery' for r in records(tmp_path, 'other')))
            await until(lambda: not manager.connections['observer'].ready)
            assert manager.connections['other'].ready and manager.connections['producer'].ready
            await until(lambda: 'plugin.observer' not in svc.bus.subscribers)
            assert any('delivery' in d['error'] for d in svc.bus.diagnostics)
            # Queue-full admission is visible and cannot grow the per-peer queue.
            slow = manager.connections['other']
            (tmp_path / 'plugins/other/stall').touch()
            for i in range(200):
                result = svc.bus.publish(manager.connections['producer'].handle, 'producer.ping', {'value': i})
            assert 'plugin.other' in result['rejected']
            assert svc.bus.subscribers['plugin.other'][1].qsize() == svc.bus.capacity
        finally:
            await manager.close()
            await svc.close()
    asyncio.run(run())


def test_service_restart_restores_queued_only_after_actual_plugin_ready(tmp_path):
    async def run():
        sessions = {}
        m = manifest()
        svc, manager, calls = await setup(tmp_path, sessions, m)
        svc.ledger.append({'id': 'persisted', 'triggerId': 't', 'triggerRevision': 1, 'actionId': 'pause',
            'event': {'id': 'persisted-event', 'type': 'producer.ping', 'payload': {'value': 1}},
            'arguments': {}, 'state': {}, 'status': 'queued', 'admitted': True})
        await svc.engine.kick()
        await manager.close()
        await svc.close()
        assert svc.ledger.read()['runs']['persisted']['status'] == 'queued'
        workers = SimpleNamespace(workers={}, set_running_observer=lambda callback: None)
        restored = Service(tmp_path / 'automation', workers, svc.engine.execute.keywords['pause'])
        restored.engine.actions = Store(tmp_path / 'actions')
        events = EventRuntime(restored, lambda: m, lambda: list(sessions.values()), tmp_path)
        try:
            await restored.start()
            await events.start()
            assert restored.ledger.read()['runs']['persisted']['status'] == 'queued' and not calls
            sessions['a'] = session('a', ['producer'])
            await events.reconcile()
            await events.settle()
            await until(lambda: restored.ledger.read()['runs']['persisted']['status'] == 'succeeded')
            assert calls == ['selected']
        finally:
            await events.close()
            await restored.close()
    asyncio.run(run())


@pytest.mark.parametrize('mode,view', [('standalone', 'standalone'), ('pan_native', 'native'), ('gateway', 'gateway')])
def test_example_real_mcp_projection_and_events_share_lifecycle(tmp_path, mode, view):
    async def run():
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        from packages.core.manifest_loader import load_manifests
        from packages.plugins.ipc import Bridge
        m = load_manifests([str(PROVIDER.parent / 'manifest.json')])
        sessions = {'a': session('a', ['observer', 'producer'], mode)}
        svc, unused, calls = await setup(tmp_path, sessions, m)
        runtime = Runtime(lambda: m, sessions.get, data_root=tmp_path)
        bridge = Bridge(runtime, tmp_path)
        try:
            await runtime.bind_events(svc, lambda: list(sessions.values()))
            await runtime.events.settle()
            connection = runtime.events.connections['producer']
            assert connection.ready
            async with stdio_client(StdioServerParameters(command=sys.executable,
                args=['-m', 'packages.plugins.mcp', '--view', view, '--plugin', 'producer'],
                cwd=str(PROVIDER.parents[2]), env={**os.environ, 'PAN_PLUGIN_IPC': bridge.address,
                                                 'PAN_AGENT_SESSION_ID': 'a'})) as streams:
                async with ClientSession(*streams) as client:
                    await client.initialize()
                    names = {t.name for t in (await client.list_tools()).tools}
                    name = 'api_call' if view == 'gateway' else 'pan_producer_echo' if view == 'native' else 'producer.echo'
                    assert name in names
                    args = {'name': 'producer.echo', 'arguments': {'value': 9}} if view == 'gateway' else {'value': 9}
                    result = await client.call_tool(name, args)
                    assert not result.isError and result.structuredContent == {'echo': {'value': 9}}
                    assert runtime.events.connections['producer'] is connection
                    publish(tmp_path, type='producer.ping', payload={'value': 3})
                    await until(lambda: calls == ['selected'])
                    await until(lambda: any(r['kind'] == 'delivery' for r in records(tmp_path, 'observer')))
            assert runtime.events.connections['producer'] is connection and connection.ready
        finally:
            await bridge.close()
            await svc.close()
    asyncio.run(run())
