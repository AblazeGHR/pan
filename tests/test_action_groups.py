"""Targeted Action source, release, generic projection and lifecycle contracts."""
import asyncio
import copy
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from packages.actions.store import Store, validate_action
from packages.actions.provider import execute
from packages.core import session as sess
from packages.core.character import CharacterManager
from packages.plugins.catalog import Catalog
from packages.plugins.lifecycle import coordinator, pin, _pins
from packages.plugins.runtime import Runtime
from packages.web import server as srv


def api_action(aid='list_sessions'):
    return {'id': aid, 'name': aid, 'description': 'List sessions', 'enabled': True,
            'effect': 'read', 'output_format': 'json',
            'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
            'execution': {'kind': 'api', 'method': 'GET', 'path': '/api/sessions'}}


def command_action(tmp_path, aid='echo'):
    script = tmp_path / 'echo.py'
    script.write_text('import json,sys\nprint(json.dumps({"value":sys.argv[1]}))\n', encoding='utf-8')
    return {**api_action(aid), 'effect': 'write_local',
            'input_schema': {'type': 'object', 'properties': {'value': {'type': 'string'}}, 'required': ['value'], 'additionalProperties': False},
            'execution': {'kind': 'command', 'executable': sys.executable, 'cwd': str(tmp_path), 'args': [str(script), '{value}']}}


def test_command_cleanup_error_preserves_cancellation(tmp_path, monkeypatch):
    from packages.jobs import process_wait
    script = tmp_path / 'blocked.py'
    marker = tmp_path / 'entered'
    script.write_text('from pathlib import Path\nimport sys,time\nPath(sys.argv[1]).touch()\ntime.sleep(30)\n', encoding='utf-8')
    definition = command_action(tmp_path)
    definition['execution']['args'] = [str(script), str(marker)]
    original = process_wait.wait

    def fail_after_owned_exit(owned, timeout):
        original(owned, timeout)
        raise RuntimeError('synthetic cleanup confirmation failure')

    monkeypatch.setattr(process_wait, 'wait', fail_after_owned_exit)

    async def run():
        task = asyncio.create_task(execute(definition, {'value':'unused'}))
        try:
            async with asyncio.timeout(5):
                while not marker.exists():
                    await asyncio.sleep(.01)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


@pytest.fixture
def setup(tmp_path, monkeypatch):
    manager = CharacterManager(str(tmp_path / 'manager'))
    manager.load_manifest(['manifest.json', 'packages/mcp/manifest.json'])
    monkeypatch.setattr(srv, '_character_manager', manager)
    monkeypatch.setattr(srv.worker, 'find_alive_worker_by_session', lambda _: None)
    monkeypatch.setattr(srv, 'broadcast', AsyncMock())
    monkeypatch.setattr(srv.worker, 'request_delayed_restart', Mock())
    from packages.automation.auth import UserAuth
    auth = UserAuth(tmp_path / 'authorization')
    token = auth.login(auth.key())
    monkeypatch.setattr(srv.app.state, 'automation', SimpleNamespace(auth=auth), raising=False)
    # These are user management regression tests, not delegated MCP calls.
    # Default grants remain off; authenticate each owned ASGI client as user.
    original_client = httpx.AsyncClient
    def user_client(*args, **kwargs):
        kwargs.setdefault('headers', {'Authorization': 'Bearer ' + token})
        return original_client(*args, **kwargs)
    monkeypatch.setattr(httpx, 'AsyncClient', user_client)
    _pins.clear()
    yield Store(), manager._manifest_config
    _pins.clear()


def group(store, gid='tools', ids=None):
    store.put('groups', {'id': gid, 'name': gid.title(), 'action_ids': ids or ['echo']})
    return store.build(gid)


def binding(pid, mode='gateway'):
    return {'plugin_id': pid, 'enabled': True, 'mode': mode}


def test_persistence_shared_references_and_optimistic_revision(setup):
    store, _ = setup
    store.put('actions', api_action())
    group(store, 'first', ['list_sessions'])
    group(store, 'second', ['list_sessions'])
    assert len(Store().read()['actions']) == 1
    before = store.path.read_bytes()
    with pytest.raises(ValueError, match='referenced'):
        store.delete('actions', 'list_sessions')
    with pytest.raises(ValueError, match='changed'):
        store.put('actions', api_action(), 0)
    assert store.path.read_bytes() == before
    updated = api_action()
    updated['description'] = 'Changed draft only'
    store.put('actions', updated)
    first = store.read()['groups']['first']['build']
    assert json.loads((Path(first['directory']) / 'snapshot.json').read_text())['actions'][0]['description'] == 'List sessions'


@pytest.mark.parametrize('alter', [
    lambda a: a.update(id='../escape'), lambda a: a.update(enabled='true'),
    lambda a: a.update(unknown=True), lambda a: a['input_schema'].update(additionalProperties=True),
    lambda a: a['input_schema'].update(properties={'x': {'$ref': 'https://example.org/schema'}}),
    lambda a: a['execution'].update(path='http://evil/api/sessions'),
    lambda a: a['execution'].update(path='/api/sessions/../settings'),
    lambda a: a['execution'].update(path='/api/send', method='POST'),
    lambda a: a.update(effect='write_external'),
    lambda a: a.update(effect=[]), lambda a: a.update(output_format={}),
    lambda a: a['execution'].update(path=[]),
])
def test_rejects_invalid_definitions_without_write(setup, alter):
    store, _ = setup
    action = api_action()
    alter(action)
    with pytest.raises(ValueError):
        store.put('actions', action)
    assert not store.path.exists()


@pytest.mark.parametrize('args', [['prefix-{value}'], ['{missing}'], ['{value!r}']])
def test_command_placeholders_are_whole_known_argv(tmp_path, args):
    action = command_action(tmp_path)
    action['execution']['args'] = args
    with pytest.raises(ValueError, match='placeholders'):
        validate_action(action)


def test_real_command_scalar_and_no_shell_injection(tmp_path):
    action = command_action(tmp_path)
    sentinel = tmp_path / 'injected.txt'
    value = f'hello & echo BAD > "{sentinel}"; $(whoami) `x`'
    result = asyncio.run(execute(action, {'value': value}))
    assert result['structuredContent'] == {'value': value}
    assert not sentinel.exists()
    for arguments in [{}, {'value': 1}, {'value': 'ok', 'extra': True}]:
        with pytest.raises(Exception, match='required|type|Additional'):
            asyncio.run(execute(action, arguments))


def test_high_risk_and_disabled_cannot_build(setup, tmp_path):
    store, _ = setup
    action = command_action(tmp_path)
    store.put('actions', {**action, 'enabled': False})
    store.put('groups', {'id': 'tools', 'name': 'Tools', 'action_ids': ['echo']})
    with pytest.raises(ValueError, match='enabled'):
        store.build('tools')
    store.put('actions', {**action, 'effect': 'high_risk'})
    with pytest.raises(ValueError, match='approval'):
        store.build('tools')


def test_api_scope_is_fixed_local_and_no_redirect(tmp_path, monkeypatch):
    action = api_action()
    for base in ['https://example.org', 'http://127.0.0.1:8768/path', 'http://user@127.0.0.1', 'http://localhost:1234']:
        monkeypatch.setenv('PAN_API_URL', base)
        with pytest.raises(ValueError, match='local Pan'):
            asyncio.run(execute(action, {}))


@pytest.mark.parametrize('mode,view', [('standalone', 'standalone'), ('pan_native', 'native'), ('gateway', 'gateway')])
def test_generic_modes_isolate_sessions_and_freeze_rebuild(setup, tmp_path, mode, view):
    async def run():
        store, manifest = setup
        action = command_action(tmp_path)
        store.put('actions', action)
        release = group(store)
        sid = sess.create(name='Enabled', adapter='codex')
        other = sess.create(name='Other', adapter='codex')
        srv._set_plugin_config(sid.adapter_config, [binding(release['plugin_id'], mode)])
        srv._set_plugin_config(other.adapter_config, [])
        pin(sid, Catalog(manifest))
        runtime = Runtime(lambda: manifest, sess.get)
        try:
            descriptors = await runtime.directory(sid.id, view)
            assert len(descriptors) == 1
            assert await runtime.directory(other.id, view) == []
            assert await runtime.directory(sid.id, 'native' if view != 'native' else 'gateway') == []
            name = descriptors[0]['native_name' if view == 'native' else 'source_name' if view == 'standalone' else 'name']
            assert (await runtime.invoke(sid.id, view, name, {'value': 'first'}))['structuredContent']['value'] == 'first'
            in_flight = asyncio.create_task(runtime.invoke(sid.id, view, name, {'value': 'in flight'}))
            await asyncio.sleep(0)
            action['description'] = 'New description'
            action['execution']['args'][-1] = 'new release'
            store.put('actions', action)
            new = store.build('tools')
            assert new['version'] != release['version']
            assert (await in_flight)['structuredContent'] == {'value': 'in flight'}
            assert (await runtime.directory(sid.id, view))[0]['contract_version'] == release['version']
            assert (await runtime.invoke(sid.id, view, name, {'value': 'still pinned'}))['structuredContent'] == {'value': 'still pinned'}
            pin(sid, Catalog(manifest))
            assert (await runtime.directory(sid.id, view))[0]['contract_version'] == new['version']
            assert (await runtime.invoke(sid.id, view, name, {'value': 'ignored'}))['structuredContent'] == {'value': 'new release'}
            assert (await runtime.invoke(other.id, view, name, {'value': 'not allowed'}))['isError']
        finally:
            await runtime.close()
    asyncio.run(run())


def test_http_crud_drafts_running_rejection_idle_unbuild(setup, tmp_path, monkeypatch):
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url='http://isolated') as client:
            action = command_action(tmp_path)
            response = await client.post('/api/action-drafts/actions/put', json={'item': action})
            assert response.status_code == 200
            assert response.json()['actions'][0]['enabled'] is False
            await client.put('/api/actions/actions/echo', json={'item': action})
            await client.put('/api/actions/groups/tools', json={'item': {'id': 'tools', 'name': 'My Tools', 'action_ids': ['echo']}})
            built = await client.post('/api/actions/groups/tools/build', json={})
            assert built.status_code == 200, built.text
            release = built.json()['groups'][0]['build']
            s = sess.create(name='Busy MA', adapter='codex')
            srv._set_plugin_config(s.adapter_config, [binding(release['plugin_id'])])
            sess.save(s)
            live = SimpleNamespace(status='running')
            monkeypatch.setattr(srv.worker, 'find_alive_worker_by_session', lambda sid: live if sid == s.id else None)
            rejected = await client.post('/api/actions/groups/tools/unbuild', json={})
            assert rejected.status_code == 409
            assert 'Busy MA' in rejected.text and s.id in rejected.text
            assert Store().read()['groups']['tools'].get('build')
            live.status = 'idle'
            allowed = await client.post('/api/actions/groups/tools/unbuild', json={})
            assert allowed.status_code == 200, allowed.text
            assert s.adapter_config['plugin_bindings'] == []
            assert sess.Session._from_data(json.loads((sess.SESSION_DIR / f'{s.id}.json').read_text())).adapter_config['mcp_servers'] == []
            assert release['plugin_id'] not in Catalog(setup[1]).plugins
            assert Catalog(setup[1]).compile([binding(release['plugin_id'])]) == []
            assert not (Store().root / 'builds' / 'tools').exists()
            assert (await client.delete('/api/actions/groups/tools')).status_code == 200
            assert (await client.delete('/api/actions/actions/echo')).status_code == 200
    asyncio.run(run())


def test_spawn_registration_gate_serializes_unbuild_and_concurrent_builds(setup, tmp_path):
    async def run():
        store, _ = setup
        store.put('actions', command_action(tmp_path))
        group(store)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url='http://isolated') as client:
            revision = store.read()['revision']
            async with coordinator().claim(exclusive={'action-group-tools'}):
                pending = asyncio.create_task(client.post('/api/actions/groups/tools/unbuild', json={}))
                await asyncio.sleep(0)
                assert not pending.done()
                assert store.read()['groups']['tools'].get('build')
            assert (await pending).status_code == 200
            revision = store.read()['revision']
            results = await asyncio.gather(*(client.post('/api/actions/groups/tools/build', json={'expectedRevision': revision}) for _ in range(2)))
            assert sorted(r.status_code for r in results) == [200, 400]
            assert store.read()['revision'] == revision + 1
    asyncio.run(run())


def test_unbuild_write_failure_keeps_registration_and_bindings(setup, tmp_path, monkeypatch):
    async def run():
        store, manifest = setup
        store.put('actions', command_action(tmp_path))
        release = group(store)
        s = sess.create(name='Idle', adapter='codex')
        srv._set_plugin_config(s.adapter_config, [binding(release['plugin_id'])])
        sess.save(s)
        before = copy.deepcopy(s.adapter_config)
        original = Store.unbuild
        def fail(*args, **kwargs):
            raise OSError('simulated registry failure')
        monkeypatch.setattr(Store, 'unbuild', fail)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app, raise_app_exceptions=False), base_url='http://isolated') as client:
            assert (await client.post('/api/actions/groups/tools/unbuild', json={})).status_code == 500
        assert s.adapter_config == before
        assert release['plugin_id'] in Catalog(manifest).plugins
        monkeypatch.setattr(Store, 'unbuild', original)
    asyncio.run(run())


def test_actual_spawn_path_pins_release_and_fences_unbuild(setup, tmp_path, monkeypatch):
    async def run():
        from packages.core import config, worker
        from packages.plugins.lifecycle import pinned
        store, _ = setup
        store.put('actions', command_action(tmp_path))
        release = group(store)
        config.save_config({'plugin_manifests': []})
        owner = sess.create(name='Spawning MA', adapter='codex')
        srv._set_plugin_config(owner.adapter_config, [binding(release['plugin_id'])])
        sess.save(owner)
        entered, proceed = asyncio.Event(), asyncio.Event()
        live = SimpleNamespace(status='running')
        async def spawn(*args, **kwargs):
            entered.set()
            await proceed.wait()
            monkeypatch.setattr(worker, 'find_alive_worker_by_session', lambda sid: live if sid == owner.id else None)
            return SimpleNamespace(pid=12345)
        monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
        adapter = SimpleNamespace(build_spawn_args=lambda *args: ['fake-cli'], name='codex')
        started = asyncio.create_task(worker._spawn_process(owner.id, adapter))
        await asyncio.wait_for(entered.wait(), 2)
        assert pinned(owner.id)[0][release['plugin_id']].version == release['version']
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url='http://isolated') as client:
            pending = asyncio.create_task(client.post('/api/actions/groups/tools/unbuild', json={}))
            await asyncio.sleep(0)
            assert not pending.done()
            proceed.set()
            assert (await started).pid == 12345
            response = await pending
            assert response.status_code == 409
            assert owner.id in response.text and 'Spawning MA' in response.text
        assert store.read()['groups']['tools']['build']['version'] == release['version']
    asyncio.run(run())


def test_api_action_draft_defaults_and_unknown_plugin_errors(setup, tmp_path):
    store, manifest = setup
    action = command_action(tmp_path)
    action['input_schema']['required'] = []
    action['input_schema']['properties']['value']['default'] = 'default value'
    assert asyncio.run(execute(action, {}))['structuredContent'] == {'value': 'default value'}
    action['input_schema']['properties']['value']['default'] = 4
    with pytest.raises(ValueError, match='default'):
        store.put('actions', action)
    with pytest.raises(ValueError, match='Unknown plugin'):
        Catalog(manifest).bindings([binding('action-group-typo')])


def test_build_publication_failure_preserves_old_release(setup, tmp_path, monkeypatch):
    from packages.actions import store as module
    store, _ = setup
    store.put('actions', command_action(tmp_path))
    release = group(store)
    original = module.atomic
    def fail_registry(path, value):
        if path.name == 'registry.json':
            raise OSError('registry publication failed')
        original(path, value)
    monkeypatch.setattr(module, 'atomic', fail_registry)
    with pytest.raises(OSError, match='publication'):
        store.build('tools')
    assert store.read()['groups']['tools']['build'] == release


@pytest.mark.parametrize('script,problem', [
    ('import sys; sys.exit(7)', 'exited 7'),
    ('print("not JSON")', 'Expecting value'),
    ('print("x" * 600000)', 'exceeds'),
    ('import time; time.sleep(5)', None),
])
def test_command_failure_output_bound_and_timeout(tmp_path, script, problem):
    action = command_action(tmp_path)
    Path(action['execution']['args'][0]).write_text(script, encoding='utf-8')
    action['execution']['timeout_sec'] = 1
    with pytest.raises(Exception, match=problem):
        asyncio.run(execute(action, {'value': 'safe'}))


def test_internal_api_never_follows_redirects(monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    hits = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(302)
            self.send_header('Location', '/outside-fixed-scope')
            self.end_headers()
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv('PAN_API_URL', f'http://127.0.0.1:{server.server_port}')
    try:
        with pytest.raises(httpx.HTTPStatusError):
            asyncio.run(execute(api_action(), {}))
        assert hits == ['/api/sessions']
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_cleanup_failure_is_reported_after_safe_unregister(setup, tmp_path, monkeypatch):
    async def run():
        import packages.actions.api as api
        store, manifest = setup
        store.put('actions', command_action(tmp_path))
        release = group(store)
        def fail(*args, **kwargs):
            raise PermissionError('release file busy')
        monkeypatch.setattr(api.shutil, 'rmtree', fail)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url='http://isolated') as client:
            response = await client.post('/api/actions/groups/tools/unbuild', json={})
        assert response.status_code == 500
        assert 'unregistered' in response.text and 'cleanup failed' in response.text
        assert not store.read()['groups']['tools'].get('build')
        assert release['plugin_id'] not in Catalog(manifest).plugins
    asyncio.run(run())


def test_confirmed_command_failure_does_not_poison_provider_connection(setup, tmp_path):
    async def run():
        store, manifest = setup
        action = command_action(tmp_path)
        Path(action['execution']['args'][0]).write_text(
            'import sys,json\nif sys.argv[1] == "fail": sys.exit(3)\nprint(json.dumps({"value":sys.argv[1]}))', encoding='utf-8')
        store.put('actions', action)
        release = group(store)
        owner = sess.create(name='Explicit retry with different input', adapter='codex')
        srv._set_plugin_config(owner.adapter_config, [binding(release['plugin_id'])])
        runtime = Runtime(lambda: manifest, sess.get)
        try:
            name = release['plugin_id'] + '.echo'
            failed = await runtime.invoke(owner.id, 'gateway', name, {'value': 'fail'})
            assert failed['isError'] and 'exited 3' in failed['content'][0]['text']
            succeeded = await runtime.invoke(owner.id, 'gateway', name, {'value': 'new explicit input'})
            assert succeeded['structuredContent'] == {'value': 'new explicit input'}
        finally:
            await runtime.close()
    asyncio.run(run())


def test_command_timeout_retires_owned_descendants(tmp_path):
    import psutil
    action = command_action(tmp_path)
    marker = tmp_path / 'child-identity.json'
    script = Path(action['execution']['args'][0])
    script.write_text('import sys,subprocess,time,json,psutil\nfrom pathlib import Path\n'
        'child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"])\n'
        f'Path({str(marker)!r}).write_text(json.dumps([child.pid,psutil.Process(child.pid).create_time()]))\n'
        'time.sleep(10)\n', encoding='utf-8')
    action['execution']['timeout_sec'] = 2
    try:
        with pytest.raises((TimeoutError, RuntimeError)):
            asyncio.run(execute(action, {'value': 'safe'}))
        assert marker.is_file(), 'Owned command never launched its descendant'
        pid, created = json.loads(marker.read_text())
        try:
            process = psutil.Process(pid)
            assert process.create_time() != created or process.status() == psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            pass  # Owned child disappeared between the PID/identity observations.
    finally:
        if marker.exists():
            pid, created = json.loads(marker.read_text())
            try:
                process = psutil.Process(pid)
                if process.create_time() == created and process.status() != psutil.STATUS_ZOMBIE:
                    process.kill()
                    process.wait(timeout=3)
            except psutil.NoSuchProcess:
                pass


def test_boolean_property_schema_is_valid_for_api_arguments(monkeypatch):
    action = api_action()
    action['input_schema']['properties'] = {'filter': True}
    monkeypatch.setenv('PAN_API_URL', 'http://127.0.0.1:1')
    # A valid boolean subschema must reach the HTTP boundary, not fail default expansion.
    with pytest.raises(httpx.ConnectError):
        asyncio.run(execute(action, {}))


def test_unbuild_rollback_attempts_every_session_even_when_a_restore_write_fails(setup, tmp_path, monkeypatch):
    async def run():
        store, manifest = setup
        store.put('actions', command_action(tmp_path))
        release = group(store)
        owners = [sess.create(name=f'Idle {i}', adapter='codex') for i in range(3)]
        for owner in owners:
            srv._set_plugin_config(owner.adapter_config, [binding(release['plugin_id'])])
            sess.save(owner)
        before = [copy.deepcopy(owner.adapter_config) for owner in owners]
        monkeypatch.setattr(srv.sess, 'list_all', lambda **kwargs: owners)
        original_save = sess.commit_adapter_config_async
        calls = []
        async def save(owner, config, **kwargs):
            calls.append(owner.id)
            if len(calls) in (3, 4):
                raise OSError('simulated save and rollback failure')
            return await original_save(owner, config, **kwargs)
        monkeypatch.setattr(sess, 'commit_adapter_config_async', save)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app, raise_app_exceptions=False), base_url='http://isolated') as client:
            response = await client.post('/api/actions/groups/tools/unbuild', json={})
        assert response.status_code == 500
        assert 'rollback persistence failed' in response.text
        assert calls == [owner.id for owner in owners] * 2
        assert [owner.adapter_config for owner in owners] == before
        assert release['plugin_id'] in Catalog(manifest).plugins
        # The failed first restore is explicitly reported; later restores really landed.
        for owner, config in zip(owners[1:], before[1:]):
            saved = json.loads((sess.SESSION_DIR / f'{owner.id}.json').read_text())
            assert saved['adapter_config'] == config
    asyncio.run(run())


def test_api_response_has_streaming_output_bound(monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({'value': 'x' * 600000}).encode())
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv('PAN_API_URL', f'http://127.0.0.1:{server.server_port}')
    try:
        with pytest.raises(ValueError, match='output exceeds'):
            asyncio.run(execute(api_action(), {}))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_escaped_output_error_does_not_poison_real_provider(setup, tmp_path):
    async def run():
        store, manifest = setup
        action = command_action(tmp_path)
        action['output_format'] = 'text'
        Path(action['execution']['args'][0]).write_text(
            'import sys\nsys.stdout.write(chr(0)*400000 if sys.argv[1] == "large" else "ok")', encoding='utf-8')
        store.put('actions', action)
        release = group(store)
        owner = sess.create(name='Escaped output', adapter='codex')
        srv._set_plugin_config(owner.adapter_config, [binding(release['plugin_id'])])
        runtime = Runtime(lambda: manifest, sess.get)
        try:
            failed = await runtime.invoke(owner.id, 'gateway', release['plugin_id'] + '.echo', {'value': 'large'})
            assert failed['isError'] and 'output exceeds' in failed['content'][0]['text']
            succeeded = await runtime.invoke(owner.id, 'gateway', release['plugin_id'] + '.echo', {'value': 'small'})
            assert succeeded['content'][0]['text'] == 'ok'
        finally:
            await runtime.close()
    asyncio.run(run())


def test_new_gateway_group_waits_for_worker_pin_even_when_descriptor_is_unchanged(setup, monkeypatch):
    async def run():
        store, manifest = setup
        store.put('actions', api_action())
        first = group(store, 'first', ['list_sessions'])
        second = group(store, 'second', ['list_sessions'])
        owner = sess.create(name='Existing gateway Worker', adapter='codex')
        srv._set_plugin_config(owner.adapter_config, [binding(first['plugin_id'])])
        sess.save(owner)
        physical = copy.deepcopy(owner.adapter_config['mcp_servers'])
        pin(owner, Catalog(manifest))
        monkeypatch.setattr(srv.worker, 'find_alive_worker_by_session', lambda sid: SimpleNamespace(status='running') if sid == owner.id else None)
        monkeypatch.setattr(srv, '_session_summary', lambda session: {'id': session.id})
        monkeypatch.setattr(srv, '_session_to_api', lambda session: {'id': session.id})
        srv.worker.request_delayed_restart.return_value = {'state': 'pending'}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url='http://isolated') as client:
            response = await client.patch('/api/sessions/' + owner.id,
                json={'pluginBindings': [binding(first['plugin_id']), binding(second['plugin_id'])]})
        assert response.status_code == 200
        assert owner.adapter_config['mcp_servers'] == physical
        runtime = Runtime(lambda: manifest, sess.get)
        try:
            exposed = await runtime.directory(owner.id, 'gateway')
            assert {d['plugin'] for d in exposed} == {first['plugin_id']}
            assert response.json()['requireRestart'] is True
            assert srv.worker.request_delayed_restart.called
            next_release = store.build('second')
            assert await runtime.directory(owner.id, 'gateway', second['plugin_id']) == []
            pin(owner, Catalog(manifest))
            loaded = await runtime.directory(owner.id, 'gateway', second['plugin_id'])
            assert loaded[0]['contract_version'] == next_release['version']
        finally:
            await runtime.close()
    asyncio.run(run())


def test_slow_start_allows_unrelated_settings_and_heartbeat(setup, tmp_path, monkeypatch):
    async def run():
        from packages.core import worker, config
        store, _ = setup
        store.put('actions', api_action())
        release = group(store, ids=['list_sessions'])
        config.save_config({'plugin_manifests': []})
        owner = sess.create(name='Slow startup', adapter='codex')
        other = sess.create(name='Independent', adapter='codex')
        srv._set_plugin_config(owner.adapter_config, [binding(release['plugin_id'])])
        entered, proceed, pulse = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def spawn(*args, **kwargs):
            entered.set()
            await proceed.wait()
            return SimpleNamespace(pid=12345)
        monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
        adapter = SimpleNamespace(build_spawn_args=lambda *args: ['never-launched'], name='codex')
        task = asyncio.create_task(worker._spawn_process(owner.id, adapter))
        try:
            await asyncio.wait_for(entered.wait(), 3)
            asyncio.get_running_loop().call_soon(pulse.set)
            await asyncio.wait_for(pulse.wait(), 2)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url='http://isolated') as client:
                result = await asyncio.wait_for(client.patch('/api/sessions/' + other.id, json={'effort': 'low'}), 2)
                assert result.status_code == 200 and 'error' not in result.json()
            assert not task.done() and not proceed.is_set()
        finally:
            proceed.set()
            await task
        assert not coordinator().active
    asyncio.run(run())


@pytest.mark.parametrize('operation', ['save', 'build', 'unbuild'])
def test_slow_action_disk_does_not_block_heartbeat_or_unrelated_settings(setup, tmp_path, monkeypatch, operation):
    async def run():
        import threading
        from packages.actions import store as module
        store, _ = setup
        store.put('actions', api_action())
        group(store, ids=['list_sessions'])
        other = sess.create(name='Independent disk', adapter='codex')
        entered, proceed = threading.Event(), threading.Event()
        original = module.atomic
        def atomic(path, value):
            if path == store.path:
                entered.set()
                if not proceed.wait(5):
                    raise TimeoutError('test barrier was not released')
            return original(path, value)
        monkeypatch.setattr(module, 'atomic', atomic)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url='http://isolated') as client:
            request = (client.put('/api/actions/actions/list_sessions', json={'item': api_action()})
                       if operation == 'save' else client.post('/api/actions/groups/tools/' + operation, json={}))
            task = asyncio.create_task(request)
            try:
                assert await asyncio.wait_for(asyncio.to_thread(entered.wait, 3), 4)
                pulse = asyncio.Event()
                asyncio.get_running_loop().call_soon(pulse.set)
                await asyncio.wait_for(pulse.wait(), 2)
                response = await asyncio.wait_for(client.patch('/api/sessions/' + other.id, json={'effort': 'low'}), 2)
                assert response.status_code == 200 and 'error' not in response.json()
                assert not task.done() and not proceed.is_set()
            finally:
                proceed.set()
                result = await task
            assert result.status_code == 200, result.text
        assert not coordinator().active
    asyncio.run(run())


@pytest.mark.parametrize('mode', ['standalone', 'pan_native', 'gateway'])
def test_unbuild_claim_fences_real_handoff_startup_and_settings(setup, monkeypatch, mode):
    async def run():
        from packages.core import worker
        from packages.plugins.lifecycle import capture_async
        store, manifest = setup
        store.put('actions', api_action())
        release = group(store, ids=['list_sessions'])
        owner = sess.create(name='Idle selected', adapter='codex')
        srv._set_plugin_config(owner.adapter_config, [binding(release['plugin_id'], mode)])
        sess.save(owner)
        pin(owner, Catalog(manifest))
        entered, proceed = asyncio.Event(), asyncio.Event()
        original_commit = sess.commit_adapter_config_async
        async def commit(session, config, **kwargs):
            if session.id == owner.id and not any(b.get('plugin_id') == release['plugin_id'] for b in config.get('plugin_bindings', [])):
                entered.set()
                await proceed.wait()
            return await original_commit(session, config, **kwargs)
        monkeypatch.setattr(sess, 'commit_adapter_config_async', commit)
        live = SimpleNamespace(status='idle', session_id=owner.id)
        writes = []
        async def write():
            writes.append('CLI input')
        # Exercise the production final handoff entry, not only the Coordinator.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url='http://isolated') as client:
            releasing = asyncio.create_task(client.post('/api/actions/groups/tools/unbuild', json={}))
            await asyncio.wait_for(entered.wait(), 3)
            handoff = asyncio.create_task(worker._queue_handoff(live, owner, write, None))
            settings = asyncio.create_task(client.patch('/api/sessions/' + owner.id, json={'pluginBindings': [binding(release['plugin_id'], mode)]}))
            # A startup has to reserve the same Session before it may repin.
            async def startup():
                from packages.plugins.lifecycle import session_claim
                async with session_claim(owner):
                    await capture_async(owner)
            starting = asyncio.create_task(startup())
            await asyncio.sleep(0)
            assert not handoff.done() and not settings.done() and not starting.done()
            assert writes == []
            proceed.set()
            assert (await releasing).status_code == 200
            # Check stale loaded pins before allowing another startup to clear
            # them: the waiting handoff was queued first and must reject them.
            with pytest.raises(worker._QueuePausedBeforeHandoff, match='detached'):
                await handoff
            assert (await settings).status_code == 200
            await starting
            assert writes == []
            assert owner.adapter_config['plugin_bindings'] == []
        assert not coordinator().active
    asyncio.run(run())


def test_handoff_admitted_first_blocks_unbuild_until_real_write_retires(setup, monkeypatch):
    async def run():
        from packages.core import worker
        store, manifest = setup
        store.put('actions', api_action())
        release = group(store, ids=['list_sessions'])
        owner = sess.create(name='Handoff owner', adapter='codex')
        srv._set_plugin_config(owner.adapter_config, [binding(release['plugin_id'])])
        sess.save(owner)
        pin(owner, Catalog(manifest))
        live = SimpleNamespace(status='idle', pending_restart=False, native_control_mode='pan',
                               native_input_uncertain=False, _current_queue_item=None, _current_report_items=[],
                               session_id=owner.id)
        monkeypatch.setattr(worker, 'find_alive_worker_by_session', lambda sid: live if sid == owner.id else None)
        entered, proceed = asyncio.Event(), asyncio.Event()
        async def write():
            entered.set()
            await proceed.wait()
            live.status = 'running'
        handing = asyncio.create_task(worker._queue_handoff(live, owner, write, None))
        await asyncio.wait_for(entered.wait(), 3)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url='http://isolated') as client:
            releasing = asyncio.create_task(client.post('/api/actions/groups/tools/unbuild', json={}))
            await asyncio.sleep(0)
            assert not releasing.done()
            proceed.set()
            await handing
            result = await releasing
            assert result.status_code == 409 and owner.id in result.text
        assert store.read()['groups']['tools']['build'] == release
        assert not coordinator().active
    asyncio.run(run())


def test_slow_session_persistence_allows_other_session_and_heartbeat(setup, monkeypatch):
    async def run():
        import threading
        first = sess.create(name='Slow settings save', adapter='codex')
        other = sess.create(name='Unrelated settings save', adapter='codex')
        entered, proceed = threading.Event(), threading.Event()
        original = sess._save_body
        def save(session, *args, **kwargs):
            if session.id == first.id:
                entered.set()
                if not proceed.wait(5):
                    raise TimeoutError('settings test barrier not released')
            return original(session, *args, **kwargs)
        monkeypatch.setattr(sess, '_save_body', save)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url='http://isolated') as client:
            task = asyncio.create_task(client.patch('/api/sessions/' + first.id, json={'effort': 'low'}))
            try:
                assert await asyncio.wait_for(asyncio.to_thread(entered.wait, 3), 4)
                pulse = asyncio.Event()
                asyncio.get_running_loop().call_soon(pulse.set)
                await asyncio.wait_for(pulse.wait(), 2)
                result = await asyncio.wait_for(client.patch('/api/sessions/' + other.id, json={'effort': 'low'}), 2)
                assert result.status_code == 200 and 'error' not in result.json()
                assert not task.done()
            finally:
                proceed.set()
                assert (await task).status_code == 200
        assert not coordinator().active
    asyncio.run(run())


@pytest.mark.parametrize('cancel_request', [False, True])
def test_unbuild_failure_or_cancellation_keeps_admission_until_transaction_retires(setup, monkeypatch, cancel_request):
    async def run():
        from packages.core import worker
        store, manifest = setup
        store.put('actions', api_action())
        release = group(store, ids=['list_sessions'])
        owner = sess.create(name='Rollback and cancel', adapter='codex')
        srv._set_plugin_config(owner.adapter_config, [binding(release['plugin_id'])])
        sess.save(owner)
        before = copy.deepcopy(owner.adapter_config)
        pin(owner, Catalog(manifest))
        entered, proceed = asyncio.Event(), asyncio.Event()
        original_commit = sess.commit_adapter_config_async
        async def commit(session, config, **kwargs):
            if not config.get('plugin_bindings'):
                entered.set()
                await proceed.wait()
            return await original_commit(session, config, **kwargs)
        monkeypatch.setattr(sess, 'commit_adapter_config_async', commit)
        if not cancel_request:
            def fail(*args, **kwargs):
                raise OSError('registry failure after durable detach')
            monkeypatch.setattr(Store, 'unbuild', fail)
        live = SimpleNamespace(status='idle', pending_restart=False, native_control_mode='pan',
                               native_input_uncertain=False, _current_queue_item=None, _current_report_items=[],
                               session_id=owner.id)
        writes = []
        async def write():
            writes.append('input')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app, raise_app_exceptions=False), base_url='http://isolated') as client:
            release_task = asyncio.create_task(client.post('/api/actions/groups/tools/unbuild', json={}))
            await asyncio.wait_for(entered.wait(), 3)
            handoff = asyncio.create_task(worker._queue_handoff(live, owner, write, None))
            if cancel_request:
                release_task.cancel()
            await asyncio.sleep(0)
            assert not release_task.done() and not handoff.done() and writes == []
            proceed.set()
            if cancel_request:
                with pytest.raises(asyncio.CancelledError):
                    await release_task
                with pytest.raises(worker._QueuePausedBeforeHandoff):
                    await handoff
                assert not store.read()['groups']['tools'].get('build')
                assert owner.adapter_config['plugin_bindings'] == []
            else:
                assert (await release_task).status_code == 500
                await handoff
                assert writes == ['input']
                assert owner.adapter_config == before
                assert store.read()['groups']['tools']['build'] == release
                saved = json.loads((sess.SESSION_DIR / f'{owner.id}.json').read_text())
                assert saved['adapter_config'] == before
        assert not coordinator().active
    asyncio.run(run())


def test_stream_consumer_cannot_cross_handoff_after_idle_unbuild_check(setup, monkeypatch):
    async def run():
        from packages.core import worker
        store, manifest = setup
        store.put('actions', api_action())
        release = group(store, ids=['list_sessions'])
        owner = sess.create(name='Real stream entry', adapter='codex')
        srv._set_plugin_config(owner.adapter_config, [binding(release['plugin_id'])])
        sess.save(owner)
        pin(owner, Catalog(manifest))
        pipe = SimpleNamespace(write=Mock(), drain=AsyncMock())
        live = worker.Worker(worker_id='synthetic-stream', session_id=owner.id,
            adapter=SimpleNamespace(name='codex', encode_user_message=lambda text: text.encode()),
            status='idle', process=SimpleNamespace(returncode=None, stdin=pipe),
            pending_signal=asyncio.Queue(), _task_done=asyncio.Event())
        monkeypatch.setattr(worker, 'find_alive_worker_by_session', lambda sid: live if sid == owner.id else None)
        monkeypatch.setattr(worker, '_begin_offline_run', AsyncMock())
        entered, proceed, preparing = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def record(w, state, reason):
            if state == 'running':
                preparing.set()
        monkeypatch.setattr(worker, '_record_legal_worker_state', record)
        original_commit = sess.commit_adapter_config_async
        async def commit(session, config, **kwargs):
            entered.set()
            await proceed.wait()
            return await original_commit(session, config, **kwargs)
        monkeypatch.setattr(sess, 'commit_adapter_config_async', commit)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url='http://isolated') as client:
            releasing = asyncio.create_task(client.post('/api/actions/groups/tools/unbuild', json={}))
            await asyncio.wait_for(entered.wait(), 3)  # running check already saw idle
            consumer = asyncio.create_task(worker._consumer_stream(live, 'never deliver old tools', 'user', owner))
            await asyncio.wait_for(preparing.wait(), 2)
            assert live.status == 'running' and not consumer.done()
            pipe.write.assert_not_called()
            proceed.set()
            assert (await releasing).status_code == 200
            with pytest.raises(worker._QueuePausedBeforeHandoff, match='detached'):
                await consumer
            pipe.write.assert_not_called()
        assert not coordinator().active
    asyncio.run(run())


@pytest.mark.parametrize('cancel_start', [False, True])
def test_failed_or_cancelled_startup_releases_claim_for_waiting_unbuild(setup, monkeypatch, cancel_start):
    async def run():
        from packages.core import worker, config
        store, _ = setup
        store.put('actions', api_action())
        release = group(store, ids=['list_sessions'])
        config.save_config({'plugin_manifests': []})
        owner = sess.create(name='Failed startup', adapter='codex')
        srv._set_plugin_config(owner.adapter_config, [binding(release['plugin_id'])])
        entered, proceed = asyncio.Event(), asyncio.Event()
        async def spawn(*args, **kwargs):
            entered.set()
            await proceed.wait()
            raise FileNotFoundError('synthetic missing executable')
        monkeypatch.setattr(asyncio, 'create_subprocess_exec', spawn)
        adapter = SimpleNamespace(build_spawn_args=lambda *args: ['never-launched'], name='codex')
        starting = asyncio.create_task(worker._spawn_process(owner.id, adapter))
        await asyncio.wait_for(entered.wait(), 3)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url='http://isolated') as client:
            releasing = asyncio.create_task(client.post('/api/actions/groups/tools/unbuild', json={}))
            await asyncio.sleep(0)
            assert not releasing.done()
            if cancel_start:
                starting.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await starting
            else:
                proceed.set()
                assert isinstance(await starting, str)
            assert (await asyncio.wait_for(releasing, 3)).status_code == 200
        assert not coordinator().active
    asyncio.run(run())


def test_session_creation_cannot_publish_new_group_reference_during_unbuild(setup, monkeypatch):
    async def run():
        store, _ = setup
        store.put('actions', api_action())
        release = group(store, ids=['list_sessions'])
        import threading
        prepared = threading.Event()
        original = srv._build_session_params
        def prepare(data):
            result = original(data)
            prepared.set()
            return result
        monkeypatch.setattr(srv, '_build_session_params', prepare)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url='http://isolated') as client:
            async with coordinator().claim(exclusive={release['plugin_id']}):
                creating = asyncio.create_task(client.post('/api/sessions', json={'name': 'late-reference', 'adapter': 'codex',
                    'pluginBindings': [binding(release['plugin_id'])]}))
                # Drain the validation thread; admission must still be pending.
                assert await asyncio.wait_for(asyncio.to_thread(prepared.wait, 2), 3)
                await asyncio.sleep(0)
                assert not creating.done()
                store.unbuild('tools')
            response = await asyncio.wait_for(creating, 3)
            assert response.status_code == 200, response.text
            assert 'error' not in response.json(), response.text
            owner = sess.get(response.json()['id'])
            assert owner.adapter_config['plugin_bindings'] == []
        assert not coordinator().active
    asyncio.run(run())


def test_session_branch_waits_for_unbuild_before_copying_plugin_settings(setup, monkeypatch):
    async def run():
        store, _ = setup
        store.put('actions', api_action())
        release = group(store, ids=['list_sessions'])
        parent = sess.create(name='branch-source', adapter='codex', cli_session_id='synthetic-native')
        srv._set_plugin_config(parent.adapter_config, [binding(release['plugin_id'])])
        sess.save(parent)
        provider = SimpleNamespace(fork_session=Mock(return_value='synthetic-child'),
                                   parse_history=Mock(return_value=[]), get_raw_usage=Mock(return_value=[]))
        monkeypatch.setattr(srv, '_sessions_provider', lambda adapter: provider)
        entered, proceed = asyncio.Event(), asyncio.Event()
        original = sess.commit_adapter_config_async
        async def commit(session, config, **kwargs):
            entered.set()
            await proceed.wait()
            return await original(session, config, **kwargs)
        monkeypatch.setattr(sess, 'commit_adapter_config_async', commit)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url='http://isolated') as client:
            releasing = asyncio.create_task(client.post('/api/actions/groups/tools/unbuild', json={}))
            await asyncio.wait_for(entered.wait(), 3)
            branching = asyncio.create_task(client.post('/api/sessions/' + parent.id + '/branch', json={'name': 'branch-child'}))
            await asyncio.sleep(0)
            assert not branching.done()
            provider.fork_session.assert_not_called()
            proceed.set()
            assert (await releasing).status_code == 200
            response = await branching
            assert response.status_code == 200 and 'error' not in response.json(), response.text
            child = sess.get(response.json()['id'])
            assert child.adapter_config.get('plugin_bindings', []) == []
        assert not coordinator().active
    asyncio.run(run())


@pytest.mark.parametrize('notification_failure', ['cancel', 'error'])
def test_committed_group_settings_register_restart_before_notification(setup, monkeypatch, notification_failure):
    async def run():
        store, _ = setup
        store.put('actions', api_action())
        release = group(store, ids=['list_sessions'])
        owner = sess.create(name='Committed-before-notify', adapter='codex')
        monkeypatch.setattr(srv.worker, 'find_alive_worker_by_session', lambda sid: SimpleNamespace(status='running') if sid == owner.id else None)
        monkeypatch.setattr(srv, '_session_summary', lambda s: {'id': s.id})
        monkeypatch.setattr(srv, '_session_to_api', lambda s: {'id': s.id})
        entered, proceed = asyncio.Event(), asyncio.Event()
        async def notify(event):
            entered.set()
            await proceed.wait()
            raise OSError('notification unavailable')
        monkeypatch.setattr(srv, 'broadcast', notify)
        updating = asyncio.create_task(srv.api_update_session(owner.id, {'pluginBindings': [binding(release['plugin_id'])]}))
        await asyncio.wait_for(entered.wait(), 3)
        saved = json.loads((sess.SESSION_DIR / f'{owner.id}.json').read_text())
        assert saved['adapter_config']['plugin_bindings'] == [binding(release['plugin_id'])]
        try:
            srv.worker.request_delayed_restart.assert_called_once_with(owner.id)
        finally:
            if notification_failure == 'cancel':
                updating.cancel()
            else:
                proceed.set()
            with pytest.raises(asyncio.CancelledError if notification_failure == 'cancel' else OSError):
                await updating
        assert not coordinator().active
    asyncio.run(run())


@pytest.mark.parametrize('entry', ['core_enabled', 'watch'])
def test_core_enabled_slow_catalog_allows_event_loop_progress(setup, monkeypatch, entry):
    async def run():
        runtime = Runtime(lambda: None, lambda sid: object())
        entered, proceed = threading.Event(), threading.Event()
        def context(sid):
            entered.set()
            assert proceed.wait(3), 'catalog reader was not released'
            return None, None, [{'plugin_id': 'pan', 'enabled': True}]
        monkeypatch.setattr(runtime, 'context', context)
        if entry == 'watch':
            runtime.providers[('synthetic', 'pan')] = (None, SimpleNamespace(close=AsyncMock()))
            monkeypatch.setattr(runtime, 'snapshot', AsyncMock())
            runtime.start()
            reading = runtime.watcher
        else:
            reading = asyncio.create_task(runtime.handle({'session_id': 'synthetic', 'operation': 'core_enabled'}))
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            heartbeat = asyncio.Event()
            asyncio.get_running_loop().call_soon(heartbeat.set)
            await asyncio.wait_for(heartbeat.wait(), 1)
            assert not reading.done() and not proceed.is_set()
        finally:
            proceed.set()
        if entry == 'core_enabled':
            assert await reading == {'enabled': True}
        await runtime.close()
    asyncio.run(run())


@pytest.mark.parametrize('unbuild_first', [True, False])
def test_native_admission_and_unbuild_share_actual_session_boundary(setup, monkeypatch, unbuild_first):
    async def run():
        from packages.core import worker
        store, manifest = setup
        store.put('actions', api_action())
        release = group(store, ids=['list_sessions'])
        owner = sess.create(name='Native-admission-owner', adapter='codex')
        srv._set_plugin_config(owner.adapter_config, [binding(release['plugin_id'])])
        sess.save(owner)
        pin(owner, Catalog(manifest))
        live = worker.Worker(worker_id='native-boundary', session_id=owner.id,
            adapter=SimpleNamespace(name='codex'), status='idle',
            process=SimpleNamespace(returncode=None, stdin=Mock()))
        owner.cli_session_id = 'synthetic-thread'
        live.native_control_mode, live.native_control_generation = 'native', 1
        monkeypatch.setattr(worker, '_control_runtime_matches', lambda *args: True)
        monkeypatch.setattr(worker, 'find_alive_worker_by_session', lambda sid: live if sid == owner.id else None)
        monkeypatch.setattr(worker, '_record_legal_worker_state', AsyncMock())
        entered, proceed = asyncio.Event(), asyncio.Event()
        original_commit = sess.commit_adapter_config_async
        original_offline = worker._commit_offline
        async def detach(*args, **kwargs):
            entered.set()
            await proceed.wait()
            return await original_commit(*args, **kwargs)
        async def admit(*args, **kwargs):
            entered.set()
            await proceed.wait()
            return await original_offline(*args, **kwargs)
        monkeypatch.setattr(sess, 'commit_adapter_config_async', detach if unbuild_first else original_commit)
        monkeypatch.setattr(worker, '_commit_offline', original_offline if unbuild_first else admit)
        async def native():
            return await worker._admit_native_turn(live, object(), request_id='native-request',
                thread_id=owner.cli_session_id, control_generation=1)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url='http://isolated') as client:
            async def unbuild():
                return await client.post('/api/actions/groups/tools/unbuild', json={})
            first = asyncio.create_task(unbuild() if unbuild_first else native())
            await asyncio.wait_for(entered.wait(), 3)
            second = asyncio.create_task(native() if unbuild_first else unbuild())
            await asyncio.sleep(0)
            assert not second.done() and live.status == 'idle'
            proceed.set()
            if unbuild_first:
                assert (await first).status_code == 200
                with pytest.raises(RuntimeError, match='detached'):
                    await second
                assert owner.task_seq == 0
            else:
                receipt = await first
                assert receipt['requestId'] == 'native-request' and live.status == 'running'
                assert (await second).status_code == 409
                assert store.read()['groups']['tools']['build'] == release
        assert not coordinator().active
    asyncio.run(run())
