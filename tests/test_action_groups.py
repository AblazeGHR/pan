"""Targeted Action source, release, generic projection and lifecycle contracts."""
import asyncio
import copy
import json
import os
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from packages.actions.store import Store, validate_action
from packages.actions.groups import Groups
from packages.jobs import final_contracts as fc
from tests.test_job_final_http_service import h, USER
from tests.test_action_unification_p3 import install, put
import uuid
from packages.actions.provider import execute
from packages.actions.group_migration import command_definition
from packages.jobs.final_service import object_ref
from packages.actions.family_primitives import canonical_api_get
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


def test_command_cleanup_error_preserves_cancellation(h, tmp_path, monkeypatch):
    from packages.actions import command_execution as commands
    marker = tmp_path / 'entered'
    script = tmp_path / 'blocked.py'
    script.write_text('from pathlib import Path\nimport sys,time\nPath(sys.argv[1]).touch()\ntime.sleep(30)\n', encoding='utf-8')
    action = command_action(tmp_path)
    action['execution']['args'] = [str(script), str(marker)]
    ref = publish_definition(h.service, action)
    owner = commands.default_process_owner()
    original = owner.kill_tree
    def fail_after_owned_exit(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError('synthetic cleanup confirmation failure')
    monkeypatch.setattr(owner, 'kill_tree', fail_after_owned_exit)
    monkeypatch.setattr(commands, 'default_process_owner', lambda: owner)
    body = {'actionRef': ref, 'arguments': {'value': 'unused'}, 'requestKey': 'cancel-original'}
    async def run():
        task = asyncio.create_task(h.service.invoke_action('echo', body, actor=USER))
        try:
            async with asyncio.timeout(5):
                while not marker.exists():
                    await asyncio.sleep(.01)
            task.cancel()
            return await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    receipt = asyncio.run(run())
    row = receipt['result']['commands'][0]
    assert row['code'] == 'cancelled'
    assert row['status'] == 'uncertain' and row['cleanupProof']['confirmed'] is False
    assert row['cleanupError'] == 'synthetic cleanup confirmation failure'
    assert receipt['status'] == 'unknown'
    replay = asyncio.run(h.service.invoke_action('echo', body, actor=USER))
    assert replay['replayed'] and replay['operationId'] == receipt['operationId']
    assert h.service.operation_for_key('invoke', body['requestKey'], actor=USER)['result'] == receipt['result']


@pytest.fixture
def setup(tmp_path, monkeypatch, h):
    manager = CharacterManager(str(tmp_path / 'manager'))
    manager.load_manifest(['manifest.json', 'packages/mcp/manifest.json'])
    monkeypatch.setattr(srv, '_character_manager', manager)
    monkeypatch.setattr(srv.worker, 'find_alive_worker_by_session', lambda _: None)
    monkeypatch.setattr(srv, 'broadcast', AsyncMock())
    monkeypatch.setattr(srv.worker, 'request_delayed_restart', Mock())
    auth = h.automation.auth
    token = h.token
    monkeypatch.setattr(srv.app.state, 'automation', h.automation, raising=False)
    monkeypatch.setattr(srv.app.state, 'job_final_service', h.service, raising=False)
    monkeypatch.setattr('packages.actions.group_catalog.canonical_path', lambda: h.service.creators.path)
    # These are user management regression tests, not delegated MCP calls.
    # Default grants remain off; authenticate each owned ASGI client as user.
    original_client = httpx.AsyncClient
    def user_client(*args, **kwargs):
        kwargs.setdefault('headers', {'Authorization': 'Bearer ' + token})
        return original_client(*args, **kwargs)
    monkeypatch.setattr(httpx, 'AsyncClient', user_client)
    from tests.test_authorization_mcp import grants
    h.automation.auth.change_permissions(grants(), 0)
    _pins.clear()
    yield h.service, manager._manifest_config
    _pins.clear()


def group(service, gid='tools', ids=None):
    groups = Groups(service)
    refs = []
    for aid in (ids or ['echo']):
        try:
            ref = service.action_ref(aid, actor=USER)
        except KeyError:
            ref = install(service, name=aid)
        refs.append(ref)
    groups.put({'id': gid, 'name': gid.title(), 'tools': [
        {'alias': aid, 'actionRef': ref} for aid, ref in zip(ids or ['echo'], refs)]},
        0, 'create-' + gid, actor=USER)
    return publish_group(service, gid)


def group_body(service, gid='tools'):
    current = next(g for g in Groups(service).list(actor=USER)['items'] if g['id'] == gid)
    return {'expectedRevision': current['revision'], 'requestKey': uuid.uuid4().hex}


def publish_group(service, gid='tools'):
    body = group_body(service, gid)
    return Groups(service).publish(gid, body['expectedRevision'], body['requestKey'], actor=USER)['result']['release']


def current_release(service, gid='tools'):
    current = next(g for g in Groups(service).list(actor=USER)['items'] if g['id'] == gid)
    return Groups(service).release(current['release'], actor=USER) if current.get('release') else None


def publish_definition(service, raw):
    if service.owner.token is None:
        service.owner.claim(0)
    value = command_definition(raw)
    aid = raw['id']
    try:
        old = service.get('action', aid, actor=USER)
    except KeyError:
        return object_ref('action', service.publish_action(aid, value))
    item = service.write('action', aid, {'definition': value, 'expectedRevision': old['revision'],
        'requestKey': uuid.uuid4().hex}, actor=USER)['result']['object']
    item = service.control('action', aid, {'operation': 'publish', 'expectedRevision': item['revision'],
        'expectedStateRevision': item['stateRevision'], 'requestKey': uuid.uuid4().hex,
        'parameters': {}}, actor=USER)['result']['object']
    return object_ref('action', item)


async def invoke_definition(service, raw, arguments, key=None):
    ref = publish_definition(service, raw)
    return await service.invoke_action(ref['id'], {'actionRef': ref, 'arguments': arguments,
        'requestKey': key or uuid.uuid4().hex}, actor=USER)


def command_group(service, raw, gid='tools', expected=0):
    ref = publish_definition(service, raw)
    groups = Groups(service)
    groups.put({'id': gid, 'name': gid, 'tools': [{'alias': raw['id'], 'actionRef': ref}]},
        expected, uuid.uuid4().hex, actor=USER)
    return publish_group(service, gid)


def canonical_runtime(service, manifest):
    runtime = Runtime(lambda: manifest, sess.get)
    runtime.group_service = lambda: service
    return runtime


def canonical_state(service):
    with service.transaction(readonly=True) as db:
        return (db.execute('SELECT * FROM final_documents ORDER BY kind,id').fetchall(),
                db.execute('SELECT * FROM final_operations ORDER BY id').fetchall())


def plugin_id(release):
    return 'action-group-' + release['groupId'].replace('_', '-')


def binding(pid, mode='gateway'):
    return {'plugin_id': pid, 'enabled': True, 'mode': mode}


def test_persistence_shared_references_and_optimistic_revision(setup):
    service, _ = setup
    first = group(service, 'first', ['echo'])
    second = group(service, 'second', ['echo'])
    ref = first['tools'][0]['actionRef']
    assert second['tools'][0]['actionRef'] == ref
    with service.transaction(readonly=True) as db:
        assert db.execute("SELECT count(*) FROM final_documents WHERE kind='action' AND id='echo'").fetchone()[0] == 1
    with pytest.raises(fc.ContractError) as stale:
        Groups(service).put({'id': 'first', 'name': 'Changed', 'tools': [{k: t[k] for k in ('alias', 'actionRef')} for t in first['tools']]}, 0, 'stale', actor=USER)
    assert stale.value.code == 'revision_conflict'
    next_ref = install(service, command='echo changed')
    assert next_ref['revision'] > ref['revision']
    assert Groups(service).release(first['version'], actor=USER) == first
    assert Groups(service).release(second['version'], actor=USER) == second
    assert current_release(service, 'first')['tools'][0]['actionRef'] == ref


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
        validate_action(action)
    assert not Store().path.exists()
    with store.transaction(readonly=True) as db:
        assert db.execute("SELECT count(*) FROM final_documents WHERE kind='action' AND id='list_sessions'").fetchone()[0] == 0


@pytest.mark.parametrize('args', [['prefix-{value}'], ['{missing}'], ['{value!r}']])
def test_command_placeholders_are_whole_known_argv(tmp_path, args):
    action = command_action(tmp_path)
    action['execution']['args'] = args
    with pytest.raises(ValueError, match='placeholders'):
        validate_action(action)


def test_real_command_scalar_and_no_shell_injection(h, tmp_path):
    action = command_action(tmp_path)
    sentinel = tmp_path / 'injected.txt'
    value = f'hello & echo BAD > "{sentinel}"; $(whoami) `x`'
    result = asyncio.run(invoke_definition(h.service, action, {'value': value}))
    assert result['result']['status'] == 'completed'
    assert json.loads(result['result']['commands'][0]['stdout']) == {'value': value}
    assert not sentinel.exists()
    ref = h.service.action_ref('echo', actor=USER)
    for arguments in [{}, {'value': 1}, {'value': 'ok', 'extra': True}]:
        with pytest.raises(fc.ContractError) as bad:
            asyncio.run(h.service.invoke_action('echo', {'actionRef': ref, 'arguments': arguments,
                'requestKey': uuid.uuid4().hex}, actor=USER))
        assert bad.value.code == 'invalid_schema'


def test_high_risk_and_disabled_cannot_build(setup, tmp_path):
    service, _ = setup
    ref = publish_definition(service, command_action(tmp_path))
    head = service.get('action', 'echo', actor=USER)
    service.control('action', 'echo', {'operation': 'disable', 'expectedRevision': head['revision'],
        'expectedStateRevision': head['stateRevision'], 'requestKey': 'disable', 'parameters': {}}, actor=USER)
    with pytest.raises(fc.ContractError) as blocked:
        put(Groups(service), ref)
    assert blocked.value.code == 'reference_conflict'
    # A protected canonical Action may be described; execution still requires
    # its supervisor rather than an old build-time approval bypass.
    from packages.actions.command_execution import product_definition
    value = product_definition('process.command')
    value.update(system=False, effectClass='high_risk')
    value['execution']['config'] = {'argv': [sys.executable, '-c', 'print("forbidden")'], 'cwd': str(tmp_path)}
    ref = object_ref('action', service.publish_action('protected', value))
    with pytest.raises(fc.ContractError) as denied:
        asyncio.run(service.invoke_action('protected', {'actionRef': ref, 'arguments': {}, 'requestKey': 'protected'}, actor=USER))
    assert denied.value.code == 'authorization_denied'
    with service.transaction(readonly=True) as db:
        assert db.execute("SELECT count(*) FROM final_operations WHERE json_extract(document,'$.operation')='invoke'").fetchone()[0] == 0


def test_api_scope_is_fixed_local_and_no_redirect(tmp_path, monkeypatch):
    action = api_action()
    for base in ['https://example.org', 'http://127.0.0.1:8768/path', 'http://user@127.0.0.1', 'http://localhost:1234']:
        monkeypatch.setenv('PAN_API_URL', base)
        with pytest.raises(ValueError, match='local Pan'):
            asyncio.run(canonical_api_get(action, {}))


@pytest.mark.parametrize('mode,view', [('standalone', 'standalone'), ('pan_native', 'native'), ('gateway', 'gateway')])
def test_generic_modes_isolate_sessions_and_freeze_rebuild(setup, tmp_path, mode, view):
    async def run():
        service, manifest = setup
        action = command_action(tmp_path)
        release = command_group(service, action)
        owner = sess.create(name='Enabled', adapter='codex')
        other = sess.create(name='Other', adapter='codex')
        srv._set_plugin_config(owner.adapter_config, [binding(plugin_id(release), mode)])
        srv._set_plugin_config(other.adapter_config, [])
        pin(owner, Catalog(manifest))
        runtime = canonical_runtime(service, manifest)
        try:
            descriptors = await runtime.directory(owner.id, view)
            assert len(descriptors) == 1
            assert await runtime.directory(other.id, view) == []
            assert await runtime.directory(owner.id, 'native' if view != 'native' else 'gateway') == []
            name = descriptors[0]['native_name' if view == 'native' else 'source_name' if view == 'standalone' else 'name']
            async def call(value):
                reply = await runtime.invoke(owner.id, view, name,
                    {'arguments': {'value': value}, 'requestKey': uuid.uuid4().hex})
                receipt = reply['structuredContent']
                assert receipt['result']['status'] == 'completed', receipt
                assert receipt['result']['actionRef']
                return json.loads(receipt['result']['commands'][0]['stdout'])
            assert await call('first') == {'value': 'first'}
            in_flight = asyncio.create_task(call('in flight'))
            await asyncio.sleep(0)
            action['description'] = 'New description'
            action['execution']['args'][-1] = 'new release'
            new = command_group(service, action, expected=group_body(service)['expectedRevision'])
            assert new['version'] != release['version']
            assert await in_flight == {'value': 'in flight'}
            assert (await runtime.directory(owner.id, view))[0]['contract_version'] == release['version']
            assert await call('still pinned') == {'value': 'still pinned'}
            pin(owner, Catalog(manifest))
            assert (await runtime.directory(owner.id, view))[0]['contract_version'] == new['version']
            assert await call('ignored') == {'value': 'new release'}
            assert (await runtime.invoke(other.id, view, name,
                {'arguments': {'value': 'not allowed'}, 'requestKey': 'forbidden'}))['isError']
            with service.transaction(readonly=True) as db:
                assert db.execute("SELECT count(*) FROM final_documents WHERE kind='job'").fetchone()[0] == 0
        finally:
            await runtime.close()
    asyncio.run(run())


def test_http_crud_drafts_running_rejection_idle_unbuild(setup, tmp_path, monkeypatch):
    async def run():
        service, manifest = setup
        release = group(service)
        owner = sess.create(name='Busy MA', adapter='codex')
        srv._set_plugin_config(owner.adapter_config, [binding(plugin_id(release))])
        sess.save(owner)
        live = SimpleNamespace(status='running')
        monkeypatch.setattr(srv.worker, 'find_alive_worker_by_session', lambda sid: live if sid == owner.id else None)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url='http://isolated') as client:
            rejected = await client.post('/api/action-groups/tools/unbuild', json=group_body(service))
            assert rejected.status_code == 409 and rejected.json()['error']['code'] == 'reference_conflict'
            assert current_release(service) == release
            live.status = 'idle'
            body = group_body(service)
            allowed = await client.post('/api/action-groups/tools/unbuild', json=body)
            assert allowed.status_code == 200, allowed.text
            assert (await client.post('/api/action-groups/tools/unbuild', json=body)).json()['operationId'] == allowed.json()['operationId']
            assert owner.adapter_config['plugin_bindings'] == []
            saved = json.loads((sess.SESSION_DIR / f'{owner.id}.json').read_text())
            assert saved['adapter_config']['mcp_servers'] == []
            assert plugin_id(release) not in Catalog(manifest).plugins
            assert Catalog(manifest).compile([binding(plugin_id(release))]) == []
            assert current_release(service) is None
            assert Groups(service).release(release['version'], actor=USER) == release
            deleted = await client.request('DELETE', '/api/action-groups/tools', json=group_body(service))
            assert deleted.status_code == 200, deleted.text
            assert Groups(service).list(actor=USER)['items'] == []
    asyncio.run(run())


def test_spawn_registration_gate_serializes_unbuild_and_concurrent_builds(setup, tmp_path):
    async def run():
        service, _ = setup
        group(service)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url='http://isolated') as client:
            revision = group_body(service)['expectedRevision']
            async with coordinator().claim(exclusive={'action-group-tools'}):
                pending = asyncio.create_task(client.post('/api/action-groups/tools/unbuild', json=group_body(service)))
                await asyncio.sleep(0)
                assert not pending.done()
                assert current_release(service)
            assert (await pending).status_code == 200
            revision = group_body(service)['expectedRevision']
            results = await asyncio.gather(*(client.post('/api/action-groups/tools/build', json={'expectedRevision': revision, 'requestKey': uuid.uuid4().hex}) for _ in range(2)))
            assert sorted(r.status_code for r in results) == [200, 409]
            assert group_body(service)['expectedRevision'] == revision + 1
    asyncio.run(run())


def test_unbuild_write_failure_keeps_registration_and_bindings(setup, tmp_path, monkeypatch):
    async def run():
        service, manifest = setup
        release = group(service)
        s = sess.create(name='Idle', adapter='codex')
        srv._set_plugin_config(s.adapter_config, [binding(plugin_id(release))])
        sess.save(s)
        before = copy.deepcopy(s.adapter_config)
        durable_before = canonical_state(service)
        original = Groups.unbuild
        def fail(*args, **kwargs):
            raise OSError('simulated registry failure')
        monkeypatch.setattr(Groups, 'unbuild', fail)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app, raise_app_exceptions=False), base_url='http://isolated') as client:
            response = await client.post('/api/action-groups/tools/unbuild', json=group_body(service))
            assert response.status_code == 503
            assert response.json()['error']['code'] == 'storage_unavailable'
        assert canonical_state(service) == durable_before
        assert current_release(service) == release
        assert s.adapter_config == before
        assert json.loads((sess.SESSION_DIR / f'{s.id}.json').read_text())['adapter_config'] == before
        assert plugin_id(release) in Catalog(manifest).plugins
        monkeypatch.setattr(Groups, 'unbuild', original)
    asyncio.run(run())


def test_actual_spawn_path_pins_release_and_fences_unbuild(setup, tmp_path, monkeypatch):
    async def run():
        from packages.core import config, worker
        from packages.plugins.lifecycle import pinned
        service, _ = setup
        release = group(service)
        config.save_config({'plugin_manifests': []})
        owner = sess.create(name='Spawning MA', adapter='codex')
        srv._set_plugin_config(owner.adapter_config, [binding(plugin_id(release))])
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
        assert pinned(owner.id)[0][plugin_id(release)].version == release['version']
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url='http://isolated') as client:
            pending = asyncio.create_task(client.post('/api/action-groups/tools/unbuild', json=group_body(service)))
            await asyncio.sleep(0)
            assert not pending.done()
            proceed.set()
            assert (await started).pid == 12345
            response = await pending
            assert response.status_code == 409
            assert response.json()['error']['code'] == 'reference_conflict'
        assert current_release(service)['version'] == release['version']
    asyncio.run(run())


def test_api_action_draft_defaults_and_unknown_plugin_errors(setup, tmp_path):
    service, manifest = setup
    action = command_action(tmp_path)
    action['input_schema']['required'] = []
    action['input_schema']['properties']['value']['default'] = 'default value'
    result = asyncio.run(invoke_definition(service, action, {}))
    assert result['result']['status'] == 'completed'
    assert json.loads(result['result']['commands'][0]['stdout']) == {'value': 'default value'}
    action['input_schema']['properties']['value']['default'] = 4
    with pytest.raises(ValueError, match='default'):
        publish_definition(service, action)
    with pytest.raises(ValueError, match='Unknown plugin'):
        Catalog(manifest).bindings([binding('action-group-typo')])


def test_build_publication_failure_preserves_old_release(setup, tmp_path, monkeypatch):
    service, _ = setup
    release = group(service)
    original = Groups.save
    def fail_registry(self, db, kind, identity, value):
        if kind == 'action_group':
            raise OSError('registry publication failed')
        return original(db, kind, identity, value)
    monkeypatch.setattr(Groups, 'save', fail_registry)
    with pytest.raises(OSError, match='publication'):
        publish_group(service)
    assert current_release(service) == release
    assert Groups(service).releases('tools', actor=USER, cursor=None, limit=100)['items'] == [release]


@pytest.mark.parametrize('script,problem', [
    ('import sys; sys.exit(7)', 'exited 7'),
    ('print("not JSON")', 'Expecting value'),
    ('print("x" * 600000)', 'exceeds'),
    ('import time; time.sleep(5)', None),
])
def test_command_failure_output_bound_and_timeout(h, tmp_path, script, problem):
    action = command_action(tmp_path)
    Path(action['execution']['args'][0]).write_text(script, encoding='utf-8')
    action['execution']['timeout_sec'] = 1
    receipt = asyncio.run(invoke_definition(h.service, action, {'value': 'safe'}))
    row = receipt['result']['commands'][0]
    assert row['cleanupProof']['confirmed'] is True
    if 'exit(7)' in script:
        assert row['status'] == 'failed' and row['exitCode'] == 7
        assert receipt['result']['status'] == 'failed'
    elif 'not JSON' in script:
        # Canonical command receipts retain exact stdout; JSON is a caller's
        # output contract, not a second command execution protocol.
        assert row['status'] == 'completed' and row['stdout'] == 'not JSON' + os.linesep
        with pytest.raises(json.JSONDecodeError):
            json.loads(row['stdout'])
    elif '600000' in script:
        from packages.actions.command_execution import OUTPUT_LIMIT
        assert row['stdoutTruncated'] and len(row['stdout'].encode()) <= OUTPUT_LIMIT
        assert row['status'] == 'completed'
    else:
        assert row['status'] == 'failed' and row['code'] == 'timeout'
        assert receipt['result']['status'] == 'failed'
    assert h.service.operation_for_key('invoke', receipt['requestKey'], actor=USER)['operationId'] == receipt['operationId']


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
            asyncio.run(canonical_api_get(api_action(), {}))
        assert hits == ['/api/sessions']
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_cleanup_failure_is_reported_after_safe_unregister(setup, tmp_path, monkeypatch):
    service, manifest = setup
    release = group(service)
    # Canonical unbuild removes only the live pointer. Immutable release files
    # and audit history are retained, so file deletion is never an admission step.
    import shutil
    monkeypatch.setattr(shutil, 'rmtree', lambda *a, **k: pytest.fail('Unbuild must retain release history'))
    body = group_body(service)
    receipt = Groups(service).unbuild('tools', body['expectedRevision'], body['requestKey'], actor=USER)
    assert receipt['result']['group'].get('release') is None
    assert current_release(service) is None
    assert plugin_id(release) not in Catalog(manifest).plugins
    assert Groups(service).release(release['version'], actor=USER) == release
    assert (Path(release['directory']) / 'capabilities.json').is_file()


def test_confirmed_command_failure_does_not_poison_provider_connection(setup, tmp_path):
    async def run():
        service, manifest = setup
        action = command_action(tmp_path)
        Path(action['execution']['args'][0]).write_text(
            'import sys,json\nif sys.argv[1] == "fail": sys.exit(3)\nprint(json.dumps({"value":sys.argv[1]}))', encoding='utf-8')
        release = command_group(service, action)
        owner = sess.create(name='Explicit retry with different input', adapter='codex')
        srv._set_plugin_config(owner.adapter_config, [binding(plugin_id(release))])
        runtime = canonical_runtime(service, manifest)
        try:
            name = plugin_id(release) + '.echo'
            failed = await runtime.invoke(owner.id, 'gateway', name,
                {'arguments': {'value': 'fail'}, 'requestKey': 'failed'})
            assert failed['isError']
            assert failed['structuredContent']['result']['commands'][0]['exitCode'] == 3
            succeeded = await runtime.invoke(owner.id, 'gateway', name,
                {'arguments': {'value': 'new explicit input'}, 'requestKey': 'succeeded'})
            assert succeeded['isError'] is False
            assert json.loads(succeeded['structuredContent']['result']['commands'][0]['stdout']) == {'value': 'new explicit input'}
            replay = await runtime.invoke(owner.id, 'gateway', name,
                {'arguments': {'value': 'fail'}, 'requestKey': 'failed'})
            assert replay['structuredContent']['operationId'] == failed['structuredContent']['operationId']
        finally:
            await runtime.close()
    asyncio.run(run())


def test_command_timeout_retires_owned_descendants(h, tmp_path):
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
        receipt = asyncio.run(invoke_definition(h.service, action, {'value': 'safe'}))
        row = receipt['result']['commands'][0]
        assert row['status'] == 'failed' and row['code'] == 'timeout'
        assert row['cleanupProof']['confirmed'] is True
        assert row['cleanupProof']['ownedTree']['remaining'] == []
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
        asyncio.run(canonical_api_get(action, {}))


def test_unbuild_rollback_attempts_every_session_even_when_a_restore_write_fails(setup, tmp_path, monkeypatch):
    async def run():
        service, manifest = setup
        release = group(service)
        owners = [sess.create(name=f'Idle {i}', adapter='codex') for i in range(3)]
        for owner in owners:
            srv._set_plugin_config(owner.adapter_config, [binding(plugin_id(release))])
            sess.save(owner)
        durable_before = canonical_state(service)
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
            response = await client.post('/api/action-groups/tools/unbuild', json=group_body(service))
        assert response.status_code == 503
        assert response.json()['error']['code'] == 'storage_unavailable'
        assert response.json()['error']['message'] == 'storage unavailable'
        assert canonical_state(service) == durable_before
        assert current_release(service) == release
        assert calls == [owner.id for owner in owners] * 2
        assert [owner.adapter_config for owner in owners] == before
        assert plugin_id(release) in Catalog(manifest).plugins
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
            asyncio.run(canonical_api_get(api_action(), {}))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def test_escaped_output_error_does_not_poison_real_provider(setup, tmp_path):
    async def run():
        service, manifest = setup
        action = command_action(tmp_path)
        action['output_format'] = 'text'
        Path(action['execution']['args'][0]).write_text(
            'import sys\nsys.stdout.write(chr(0)*400000 if sys.argv[1] == "large" else "ok")', encoding='utf-8')
        release = command_group(service, action)
        owner = sess.create(name='Escaped output', adapter='codex')
        srv._set_plugin_config(owner.adapter_config, [binding(plugin_id(release))])
        runtime = canonical_runtime(service, manifest)
        try:
            large = await runtime.invoke(owner.id, 'gateway', plugin_id(release) + '.echo',
                {'arguments': {'value': 'large'}, 'requestKey': 'large'})
            receipt = large['structuredContent']
            assert receipt['result']['status'] == 'completed'
            assert receipt['result']['commands'][0]['stdout'] == chr(0) * 400000
            assert receipt['result']['commands'][0]['stdoutTruncated'] is False
            # Canonical transport returns structured durable receipts and is
            # not constrained by the retired provider's JSON-lines envelope.
            assert len(json.dumps(receipt)) > 2 * 1024 * 1024
            small = await runtime.invoke(owner.id, 'gateway', plugin_id(release) + '.echo',
                {'arguments': {'value': 'small'}, 'requestKey': 'small'})
            assert small['structuredContent']['result']['commands'][0]['stdout'] == 'ok'
            assert small['isError'] is False
        finally:
            await runtime.close()
    asyncio.run(run())


def test_new_gateway_group_waits_for_worker_pin_even_when_descriptor_is_unchanged(setup, monkeypatch):
    async def run():
        service, manifest = setup
        first = group(service, 'first', ['list_sessions'])
        second = group(service, 'second', ['list_sessions'])
        owner = sess.create(name='Existing gateway Worker', adapter='codex')
        srv._set_plugin_config(owner.adapter_config, [binding(plugin_id(first))])
        sess.save(owner)
        physical = copy.deepcopy(owner.adapter_config['mcp_servers'])
        pin(owner, Catalog(manifest))
        monkeypatch.setattr(srv.worker, 'find_alive_worker_by_session', lambda sid: SimpleNamespace(status='running') if sid == owner.id else None)
        monkeypatch.setattr(srv, '_session_summary', lambda session: {'id': session.id})
        monkeypatch.setattr(srv, '_session_to_api', lambda session: {'id': session.id})
        srv.worker.request_delayed_restart.return_value = {'state': 'pending'}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url='http://isolated') as client:
            response = await client.patch('/api/sessions/' + owner.id,
                json={'pluginBindings': [binding(plugin_id(first)), binding(plugin_id(second))]})
        assert response.status_code == 200
        assert owner.adapter_config['mcp_servers'] == physical
        runtime = Runtime(lambda: manifest, sess.get)
        try:
            exposed = await runtime.directory(owner.id, 'gateway')
            assert {d['plugin'] for d in exposed} == {plugin_id(first)}
            assert response.json()['requireRestart'] is True
            assert srv.worker.request_delayed_restart.called
            next_release = publish_group(service, 'second')
            assert await runtime.directory(owner.id, 'gateway', plugin_id(second)) == []
            pin(owner, Catalog(manifest))
            loaded = await runtime.directory(owner.id, 'gateway', plugin_id(second))
            assert loaded[0]['contract_version'] == next_release['version']
        finally:
            await runtime.close()
    asyncio.run(run())


def test_slow_start_allows_unrelated_settings_and_heartbeat(setup, tmp_path, monkeypatch):
    async def run():
        from packages.core import worker, config
        service, _ = setup
        release = group(service, ids=['list_sessions'])
        config.save_config({'plugin_manifests': []})
        owner = sess.create(name='Slow startup', adapter='codex')
        other = sess.create(name='Independent', adapter='codex')
        srv._set_plugin_config(owner.adapter_config, [binding(plugin_id(release))])
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
        service, _ = setup
        group(service, ids=['list_sessions'])
        other = sess.create(name='Independent disk', adapter='codex')
        entered, proceed = threading.Event(), threading.Event()
        original = Groups.save
        def save(db, kind, identity, value):
            if kind == 'action_group':
                entered.set()
                if not proceed.wait(5):
                    raise TimeoutError('test barrier was not released')
            return original(db, kind, identity, value)
        monkeypatch.setattr(Groups, 'save', staticmethod(save))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url='http://isolated') as client:
            body = group_body(service)
            if operation == 'save':
                body['group'] = {'id': 'tools', 'name': 'Changed', 'tools': [{k: t[k] for k in ('alias', 'actionRef')} for t in current_release(service)['tools']]}
                request = client.put('/api/action-groups/tools', json=body)
            else:
                request = client.post('/api/action-groups/tools/' + operation, json=body)
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
        service, manifest = setup
        release = group(service, ids=['list_sessions'])
        owner = sess.create(name='Idle selected', adapter='codex')
        srv._set_plugin_config(owner.adapter_config, [binding(plugin_id(release), mode)])
        sess.save(owner)
        pin(owner, Catalog(manifest))
        entered, proceed = asyncio.Event(), asyncio.Event()
        original_commit = sess.commit_adapter_config_async
        async def commit(session, config, **kwargs):
            if session.id == owner.id and not any(b.get('plugin_id') == plugin_id(release) for b in config.get('plugin_bindings', [])):
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
            releasing = asyncio.create_task(client.post('/api/action-groups/tools/unbuild', json=group_body(service)))
            await asyncio.wait_for(entered.wait(), 3)
            handoff = asyncio.create_task(worker._queue_handoff(live, owner, write, None))
            settings = asyncio.create_task(client.patch('/api/sessions/' + owner.id, json={'pluginBindings': [binding(plugin_id(release), mode)]}))
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
        service, manifest = setup
        release = group(service, ids=['list_sessions'])
        owner = sess.create(name='Handoff owner', adapter='codex')
        srv._set_plugin_config(owner.adapter_config, [binding(plugin_id(release))])
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
            releasing = asyncio.create_task(client.post('/api/action-groups/tools/unbuild', json=group_body(service)))
            await asyncio.sleep(0)
            assert not releasing.done()
            proceed.set()
            await handing
            result = await releasing
            assert result.status_code == 409 and result.json()['error']['code'] == 'reference_conflict'
        assert current_release(service) == release
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
        service, manifest = setup
        release = group(service, ids=['list_sessions'])
        owner = sess.create(name='Rollback and cancel', adapter='codex')
        srv._set_plugin_config(owner.adapter_config, [binding(plugin_id(release))])
        sess.save(owner)
        before = copy.deepcopy(owner.adapter_config)
        durable_before = canonical_state(service)
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
            monkeypatch.setattr(Groups, 'unbuild', fail)
        live = SimpleNamespace(status='idle', pending_restart=False, native_control_mode='pan',
                               native_input_uncertain=False, _current_queue_item=None, _current_report_items=[],
                               session_id=owner.id)
        writes = []
        async def write():
            writes.append('input')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app, raise_app_exceptions=False), base_url='http://isolated') as client:
            release_task = asyncio.create_task(client.post('/api/action-groups/tools/unbuild', json=group_body(service)))
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
                assert not current_release(service)
                assert owner.adapter_config['plugin_bindings'] == []
            else:
                response = await release_task
                assert response.status_code == 503
                assert response.json()['error']['code'] == 'storage_unavailable'
                assert canonical_state(service) == durable_before
                await handoff
                assert writes == ['input']
                assert owner.adapter_config == before
                assert current_release(service) == release
                saved = json.loads((sess.SESSION_DIR / f'{owner.id}.json').read_text())
                assert saved['adapter_config'] == before
        assert not coordinator().active
    asyncio.run(run())


def test_stream_consumer_cannot_cross_handoff_after_idle_unbuild_check(setup, monkeypatch):
    async def run():
        from packages.core import worker
        service, manifest = setup
        release = group(service, ids=['list_sessions'])
        owner = sess.create(name='Real stream entry', adapter='codex')
        srv._set_plugin_config(owner.adapter_config, [binding(plugin_id(release))])
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
            releasing = asyncio.create_task(client.post('/api/action-groups/tools/unbuild', json=group_body(service)))
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
        service, _ = setup
        release = group(service, ids=['list_sessions'])
        config.save_config({'plugin_manifests': []})
        owner = sess.create(name='Failed startup', adapter='codex')
        srv._set_plugin_config(owner.adapter_config, [binding(plugin_id(release))])
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
            releasing = asyncio.create_task(client.post('/api/action-groups/tools/unbuild', json=group_body(service)))
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
        service, _ = setup
        release = group(service, ids=['list_sessions'])
        import threading
        prepared = threading.Event()
        original = srv._build_session_params
        def prepare(data):
            result = original(data)
            prepared.set()
            return result
        monkeypatch.setattr(srv, '_build_session_params', prepare)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=srv.app), base_url='http://isolated') as client:
            async with coordinator().claim(exclusive={plugin_id(release)}):
                creating = asyncio.create_task(client.post('/api/sessions', json={'name': 'late-reference', 'adapter': 'codex',
                    'pluginBindings': [binding(plugin_id(release))]}))
                # Drain the validation thread; admission must still be pending.
                assert await asyncio.wait_for(asyncio.to_thread(prepared.wait, 2), 3)
                await asyncio.sleep(0)
                assert not creating.done()
                Groups(service).unbuild('tools', **dict(expected=group_body(service)['expectedRevision'], key=uuid.uuid4().hex), actor=USER)
            response = await asyncio.wait_for(creating, 3)
            assert response.status_code == 200, response.text
            assert 'error' not in response.json(), response.text
            owner = sess.get(response.json()['id'])
            assert owner.adapter_config['plugin_bindings'] == []
        assert not coordinator().active
    asyncio.run(run())


def test_session_branch_waits_for_unbuild_before_copying_plugin_settings(setup, monkeypatch):
    async def run():
        service, _ = setup
        release = group(service, ids=['list_sessions'])
        parent = sess.create(name='branch-source', adapter='codex', cli_session_id='synthetic-native')
        srv._set_plugin_config(parent.adapter_config, [binding(plugin_id(release))])
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
            releasing = asyncio.create_task(client.post('/api/action-groups/tools/unbuild', json=group_body(service)))
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
        service, _ = setup
        release = group(service, ids=['list_sessions'])
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
        updating = asyncio.create_task(srv.api_update_session(owner.id, {'pluginBindings': [binding(plugin_id(release))]}))
        await asyncio.wait_for(entered.wait(), 3)
        saved = json.loads((sess.SESSION_DIR / f'{owner.id}.json').read_text())
        assert saved['adapter_config']['plugin_bindings'] == [binding(plugin_id(release))]
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
        service, manifest = setup
        release = group(service, ids=['list_sessions'])
        owner = sess.create(name='Native-admission-owner', adapter='codex')
        srv._set_plugin_config(owner.adapter_config, [binding(plugin_id(release))])
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
                return await client.post('/api/action-groups/tools/unbuild', json=group_body(service))
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
                assert current_release(service) == release
        assert not coordinator().active
    asyncio.run(run())


@pytest.mark.parametrize('historical', [False, True])
def test_retired_writer_and_executor_never_mutate_or_spawn(tmp_path, monkeypatch, historical):
    from tests.support.legacy_action_source import LegacySource
    root = tmp_path / 'historical-actions'
    old = Store(root)
    if historical:
        source = LegacySource(root)
        source.put('actions', command_action(tmp_path))
        source.put('groups', {'id': 'tools', 'name': 'Tools', 'action_ids': ['echo']})
        source.build('tools')
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    monkeypatch.setattr('subprocess.Popen', lambda *a, **k: pytest.fail('Retired executor spawned a process'))
    for operation in [lambda: old.put('actions', command_action(tmp_path)),
                      lambda: old.put('groups', {'id': 'tools', 'name': 'Tools', 'action_ids': ['echo']}),
                      lambda: old.build('tools')]:
        with pytest.raises(PermissionError, match='retired'):
            operation()
    with pytest.raises(PermissionError, match='retired'):
        asyncio.run(execute(command_action(tmp_path), {'value': 'forbidden'}))
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()} == before


def test_only_identified_unpublished_canonical_group_is_inactive_and_rebuild_restores_binding(setup):
    from packages.actions.group_catalog import inactive
    from packages.actions.store import inactive_plugin
    service, manifest = setup
    release = group(service)
    pid = plugin_id(release)
    assert inactive('tools') is False and inactive_plugin(pid) is False
    assert Catalog(manifest).bindings([binding(pid)]) == [binding(pid)]
    with pytest.raises(ValueError, match='Unknown plugin'):
        Catalog(manifest).bindings([binding('action-group-truly-unknown')])
    assert inactive('truly_unknown') is False
    assert inactive_plugin('action-group-truly-unknown') is False
    assert inactive_plugin('other-tools') is False
    body = group_body(service)
    Groups(service).unbuild('tools', body['expectedRevision'], body['requestKey'], actor=USER)
    before = canonical_state(service)
    assert inactive('tools') is True and inactive_plugin(pid) is True
    assert Catalog(manifest).bindings([binding(pid)]) == []
    with pytest.raises(ValueError, match='Unknown plugin'):
        Catalog(manifest).bindings([binding('action-group-truly-unknown')])
    assert canonical_state(service) == before
    rebuilt = publish_group(service)
    assert rebuilt['version'] != release['version']
    assert inactive('tools') is False and inactive_plugin(pid) is False
    assert Catalog(manifest).bindings([binding(pid)]) == [binding(pid)]
    assert Catalog(manifest).plugins[pid].version == rebuilt['version']
    assert Groups(service).release(release['version'], actor=USER) == release


def test_inactive_projection_observes_only_committed_canonical_unbuild(setup, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from packages.actions.group_catalog import inactive
    service, manifest = setup
    release = group(service)
    before = canonical_state(service)
    entered, proceed = threading.Event(), threading.Event()
    original = Groups.save
    def save(db, kind, identity, value):
        original(db, kind, identity, value)
        if kind == 'action_group' and not value.get('release'):
            entered.set()
            if not proceed.wait(5):
                raise TimeoutError('canonical unbuild barrier not released')
            raise OSError('rollback unpublished pointer')
    monkeypatch.setattr(Groups, 'save', staticmethod(save))
    body = group_body(service)
    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(Groups(service).unbuild, 'tools', body['expectedRevision'], body['requestKey'], actor=USER)
        try:
            assert entered.wait(3)
            assert not pending.done()
            assert inactive('tools') is False
            assert Catalog(manifest).bindings([binding(plugin_id(release))]) == [binding(plugin_id(release))]
        finally:
            proceed.set()
        with pytest.raises(OSError, match='rollback unpublished pointer'):
            pending.result(timeout=3)
    assert inactive('tools') is False
    assert canonical_state(service) == before
    assert current_release(service) == release


def test_inactive_plugin_requires_exact_canonical_projection_identity(setup):
    from packages.actions.store import inactive_plugin
    service, manifest = setup
    release = group(service, gid='under_score')
    body = group_body(service, 'under_score')
    Groups(service).unbuild('under_score', body['expectedRevision'], body['requestKey'], actor=USER)
    assert plugin_id(release) == 'action-group-under-score'
    assert inactive_plugin(plugin_id(release)) is True
    assert Catalog(manifest).bindings([binding(plugin_id(release))]) == []
    for unknown in ['action-group-under_score', 'action-group-UNDER-SCORE', 'other-under-score']:
        assert inactive_plugin(unknown) is False
        with pytest.raises(ValueError, match='Unknown plugin'):
            Catalog(manifest).bindings([binding(unknown)])


@pytest.mark.parametrize('response_kind', ['redirect', 'oversized', 'success'])
def test_canonical_api_group_receipt_preserves_scope_bounds_and_original_identity(h, tmp_path, monkeypatch, response_kind):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    hits = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)
            self.send_response(302 if response_kind == 'redirect' else 200)
            if response_kind == 'redirect':
                self.send_header('Location', '/outside-fixed-scope')
            self.end_headers()
            self.wfile.write(json.dumps({'value': 'x' * 600000 if response_kind == 'oversized' else 'owned'}).encode())
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv('PAN_API_URL', f'http://127.0.0.1:{server.server_port}')
    try:
        release = command_group(h.service, api_action())
        groups = Groups(h.service)
        async def call():
            return await groups.invoke(release['version'], 'list_sessions', {}, 'api-original', actor=USER)
        receipt = asyncio.run(call())
        assert receipt['result']['source']['groupRelease'] == release['version']
        assert receipt['result']['actionRef'] == release['tools'][0]['actionRef']
        if response_kind == 'success':
            assert receipt['result']['status'] == 'completed'
            assert receipt['result']['output']['structuredContent'] == {'value': 'owned'}
        else:
            assert receipt['result']['status'] == 'uncertain'
            assert ('302' if response_kind == 'redirect' else 'output exceeds') in receipt['result']['error']
        replay = asyncio.run(call())
        assert replay['replayed'] and replay['operationId'] == receipt['operationId']
        assert hits == ['/api/sessions']
        assert h.service.operation_for_key('invoke', 'api-original', actor=USER)['operationId'] == receipt['operationId']
        with h.service.transaction(readonly=True) as db:
            assert db.execute("SELECT count(*) FROM final_documents WHERE kind='job'").fetchone()[0] == 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
