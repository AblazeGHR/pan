"""P6 named gates; all sources/processes/profiles are synthetic and isolated."""
import asyncio
import copy
import json
import os
from pathlib import Path
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from packages.actions import store, group_catalog
from packages.actions.group_migration import LegacyGroupMigration, enroll_fresh_source, command_definition
from packages.actions.groups import Groups
from packages.jobs import final_contracts as fc
from packages.jobs.final_service import object_ref
from packages.jobs.runtime_owner import OwnerFence
from tests.test_action_unification_p1 import action_http
from tests.test_action_unification_p3 import USER, apply_body
from tests.test_action_groups import command_action


def source(tmp_path, service, monkeypatch, *, enrolled=True, output_format='json'):
    root = tmp_path / 'legacy-source'
    if enrolled:
        enroll_fresh_source(root)
    monkeypatch.setattr(store, 'root', lambda: root)
    monkeypatch.setattr(group_catalog, 'canonical_path', lambda: service.creators.path)
    old = store.Store(root)
    action = command_action(tmp_path)
    action['output_format'] = output_format
    # A counter is a real side effect; stdout still has the old JSON contract.
    script = tmp_path / 'effect.py'
    script.write_text('import json,sys\nfrom pathlib import Path\np=Path(sys.argv[1]);p.write_text(p.read_text()+"x" if p.exists() else "x")\nprint(json.dumps({"value":sys.argv[2],"requestKey":sys.argv[3]}))\n', 'utf-8')
    action['input_schema']['properties']['requestKey'] = {'type': 'string', 'default': 'business-default'}
    action['execution']['args'] = [str(script), str(tmp_path / 'effects'), '{value}', '{requestKey}']
    old.put('actions', action)
    old.put('groups', {'id': 'tools', 'name': 'Original', 'action_ids': ['echo']})
    build = old.build('tools')
    action = old.read()['actions']['echo']
    ref = object_ref('action', service.publish_action('old_echo', command_definition(action)))
    mappings = [{'groupId': 'tools', 'version': v, 'actionId': 'echo',
                 'definitionDigest': fc.digest(action), 'actionRef': ref} for v in ('draft', build['version'])]
    return old, build, mappings, ref


def own(service):
    service.owner.claim(0)
    service.owner.set_state('active')


def test_P6_GROUP_01_whitelist_export(action_http, tmp_path, monkeypatch):
    _, service = action_http
    old, build, mappings, ref = source(tmp_path, service, monkeypatch)
    migration = LegacyGroupMigration(service, old.root)
    before = {p: p.read_bytes() for p in old.root.rglob('*') if p.is_file()}
    plan = migration.preview(mappings, actor=USER)
    assert plan['blocked'] == []
    assert plan['groups'][0]['versions'][build['version']]['tools'][0]['actionRef'] == ref
    assert {p: p.read_bytes() for p in before} == before
    assert not (tmp_path / 'effects').exists()


def test_P6_GROUP_02_owner_writer_handoff(action_http, tmp_path, monkeypatch):
    _, service = action_http
    old, build, mappings, _ = source(tmp_path, service, monkeypatch)
    migration = LegacyGroupMigration(service, old.root)
    plan = migration.preview(mappings, actor=USER)
    own(service)
    owner = OwnerFence(old.root)
    owner.lock.acquire()
    try:
        with pytest.raises(fc.ContractError, match='owns this profile'):
            migration.apply(plan, 'busy', actor=USER)
    finally:
        owner.lock.release()
    with owner.legacy_pass():
        with pytest.raises(fc.ContractError, match='unknown'):
            migration.apply(plan, 'leased', actor=USER)
    raw = old.path.read_bytes()
    result = migration.apply(plan, 'adoption', actor=USER)
    assert result['result']['generation'] == 1
    assert old.path.read_bytes() == raw
    for call in (lambda: old.build('tools'), lambda: old.unbuild('tools'),
                 lambda: old.delete('groups', 'tools'), lambda: old.mutate(lambda s: s['groups'].clear())):
        with pytest.raises(fc.ContractError, match='epoch fence'):
            call()
    assert Groups(service).list(actor=USER)['items'][0]['release'] == build['version']
    assert migration.apply(plan, 'adoption', actor=USER)['operationId'] == result['operationId']


@pytest.mark.parametrize('phase', ['source-frozen', 'before-commit', 'after-commit'])
def test_P6_GROUP_03_cutover_crash(action_http, tmp_path, monkeypatch, phase):
    _, service = action_http
    old, build, mappings, _ = source(tmp_path, service, monkeypatch)
    migration = LegacyGroupMigration(service, old.root)
    plan = migration.preview(mappings, actor=USER)
    own(service)
    def crash(value):
        if value == phase:
            raise RuntimeError('injected crash')
    with pytest.raises(RuntimeError, match='injected'):
        migration.apply(plan, 'original-adopt', actor=USER, fault=crash)
    with pytest.raises(fc.ContractError):
        old.build('tools')
    if phase != 'after-commit':
        assert Groups(service).list(actor=USER)['items'] == []
        assert migration.status('original-adopt', actor=USER)['phase'] == 'source-frozen'
        with pytest.raises(fc.ContractError, match='different original'):
            migration.apply(plan, 'changed-identity', actor=USER)
    result = migration.apply(plan, 'original-adopt', actor=USER)
    assert service.operation_for_key('update', 'original-adopt', actor=USER)['operationId'] == result['operationId']
    assert Groups(service).release(build['version'], actor=USER)['version'] == build['version']
    assert not (tmp_path / 'effects').exists()


def test_P6_GROUP_01_unknown_writer_preserved(action_http, tmp_path, monkeypatch):
    _, service = action_http
    old, _, mappings, _ = source(tmp_path, service, monkeypatch, enrolled=False)
    migration = LegacyGroupMigration(service, old.root)
    raw = old.path.read_bytes()
    plan = migration.preview(mappings, actor=USER)
    assert {'reason': 'unknown_writer'} in plan['blocked']
    own(service)
    with pytest.raises(fc.ContractError, match='Unknown'):
        migration.apply(plan, 'blocked', actor=USER)
    assert old.path.read_bytes() == raw
    with pytest.raises(FileExistsError):
        enroll_fresh_source(old.root)


@pytest.mark.parametrize('damage', ['unknown', 'mapping', 'snapshot'])
def test_P6_GROUP_07_complex_or_mismatched_preserve(action_http, tmp_path, monkeypatch, damage):
    _, service = action_http
    old, build, mappings, _ = source(tmp_path, service, monkeypatch)
    if damage == 'unknown':
        old.mutate(lambda state: state['groups']['tools'].update(unknown={'lease': 'unresolved'}))
    elif damage == 'mapping':
        mappings[1]['definitionDigest'] = '0' * 64
    else:
        Path(build['directory'], 'capabilities.json').write_text('{}')
    migration = LegacyGroupMigration(service, old.root)
    plan = migration.preview(mappings, actor=USER)
    assert plan['blocked']
    raw = old.path.read_bytes()
    own(service)
    with pytest.raises(fc.ContractError):
        migration.apply(plan, 'blocked', actor=USER)
    assert old.path.read_bytes() == raw
    assert OwnerFence(old.root).status() is None


@pytest.mark.parametrize("mode,view", [("standalone", "standalone"), ("pan_native", "native"), ("gateway", "gateway")])
def test_P6_GROUP_05_old_to_upgraded_stdio(tmp_path, monkeypatch, mode, view):
    from tests.test_authorization_mcp import harness, close, grants, result
    from types import SimpleNamespace
    from packages.plugins.catalog import Catalog
    from packages.plugins.lifecycle import pin, _pins
    async def exercise():
        h = harness(tmp_path)
        try:
            old, build, mappings, first = source(tmp_path, h.service, monkeypatch)
            pid = build['plugin_id']
            h.session.adapter_config['plugin_bindings'].append({'plugin_id': pid, 'enabled': True, 'mode': mode})
            h.svc.auth.change_permissions(grants(), 0)
            pin(h.session, Catalog(h.bridge.runtime.get_manifest()))
            params = StdioServerParameters(command=sys.executable,
                args=['-m', 'packages.plugins.mcp', '--view', view, *(['--plugin', pid] if view == 'standalone' else [])],
                cwd=str(Path.cwd()), env={**os.environ, 'PAN_PLUGIN_IPC': h.bridge.address,
                    'PAN_AGENT_SESSION_ID': h.session.id, 'PAN_API_URL': 'http://127.0.0.1:1'})
            async def discover(client):
                if view == 'gateway':
                    info = result(await client.call_tool('api_discover', {'include_schema': True}))['tools'][0]
                    return SimpleNamespace(name=info['name'], inputSchema=info['inputSchema'])
                return (await client.list_tools()).tools[0]
            async def call(client, name, arguments, **kwargs):
                if view == 'gateway':
                    return await client.call_tool('api_call', {'name': name, 'arguments': arguments}, **kwargs)
                return await client.call_tool(name, arguments, **kwargs)
            async with stdio_client(params) as streams, ClientSession(*streams) as client:
                await client.initialize()
                tool = await discover(client)
                schema = copy.deepcopy(tool.inputSchema)
                before = await call(client, tool.name, {'value': 'old "quote" \\ 雪\n'})
                assert before.structuredContent == {'value': 'old "quote" \\ 雪\n', 'requestKey': 'business-default'}
                migration = LegacyGroupMigration(h.service, old.root)
                plan = migration.preview(mappings, actor=USER)
                artifacts = {p: p.read_bytes() for p in Path(build['directory']).iterdir()}
                adopted = migration.apply(plan, 'adopt', actor=USER)
                assert adopted['status'] == 'completed'
                definition = command_definition(old.read()['actions']['echo'])
                definition['execution']['config']['args'][-2] = 'v2'
                action = h.service.get('action', first['id'], actor=USER)
                changed = h.service.write('action', first['id'], {'definition': definition,
                    'expectedRevision': action['revision'], 'requestKey': 'edit-v2'}, actor=USER)['result']['object']
                h.service.control('action', first['id'], {'operation': 'publish', 'expectedRevision': changed['revision'],
                    'expectedStateRevision': changed['stateRevision'], 'requestKey': 'publish-v2', 'parameters': {}}, actor=USER)
                second = h.service.action_ref(first['id'], actor=USER)
                groups = Groups(h.service)
                preview = groups.preview({'scope': 'action', 'actionRef': second}, actor=USER)
                assert preview['excluded'] == []
                groups.apply(apply_body(preview), actor=USER)
                release = groups.publish('tools', 2, 'build-v2', actor=USER)['result']['release']
                # Loaded old Worker retains the original provider and v1 schema.
                frozen = await call(client, tool.name, {'value': 'still-old'})
                assert frozen.structuredContent['value'] == 'still-old'
                assert _pins[h.session.id][0][pid].version == build['version']
                assert {p: p.read_bytes() for p in artifacts} == artifacts
            _pins.pop(h.session.id)
            pin(h.session, Catalog(h.bridge.runtime.get_manifest()))
            async with stdio_client(params) as streams, ClientSession(*streams) as client:
                await client.initialize()
                tool = await discover(client)
                assert tool.inputSchema == schema
                args = {'value': 'new', 'requestKey': 'business-key'}
                called = await call(client, tool.name, args, meta={'panRequestKey': 'outer-once'})
                assert not called.isError, called
                assert called.structuredContent == {'value': 'v2', 'requestKey': 'business-key'}
                repeated = await call(client, tool.name, args, meta={'panRequestKey': 'outer-once'})
                assert repeated.meta['panOperation']['operationId'] == called.meta['panOperation']['operationId']
                assert called.meta['panOperation']['groupRelease'] == release['version']
                conflict = await call(client, tool.name, {**args, 'value': 'changed'}, meta={'panRequestKey': 'outer-once'})
                assert conflict.isError
                assert (tmp_path / 'effects').read_text() == 'xxx'
                original = h.service.executor.execute_commands
                async def lost_receipt(**kwargs):
                    await original(**kwargs)
                    raise RuntimeError('injected response loss after checkpoint')
                monkeypatch.setattr(h.service.executor, 'execute_commands', lost_receipt)
                unknown = await call(client, tool.name, args, meta={'panRequestKey': 'unknown-once'})
                assert unknown.isError and unknown.meta['panOperation']['requestKey'] == 'unknown-once'
                assert unknown.meta['panOperation']['status'] == 'uncertain'
                repeated = await call(client, tool.name, args, meta={'panRequestKey': 'unknown-once'})
                assert repeated.isError
                assert repeated.meta['panOperation']['operationId'] == unknown.meta['panOperation']['operationId']
                assert (tmp_path / 'effects').read_text() == 'xxxx'
                with h.service.transaction(readonly=True) as db:
                    assert db.execute("SELECT count(*) FROM final_documents WHERE kind='job'").fetchone()[0] == 0
                h.svc.auth.change_permissions(grants(action={'invoke': False}), 1)
                revoked = await call(client, tool.name, args, meta={'panRequestKey': 'revoked'})
                assert revoked.isError
                assert (tmp_path / 'effects').read_text() == 'xxxx'
        finally:
            _pins.clear()
            await close(h)
    asyncio.run(exercise())


def test_P6_GROUP_08_old_http_crud_build_unbuild_catalog(action_http, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from packages.actions.api import install
    from packages.actions.group_migration import LegacyGroupCompatibility
    client, service = action_http
    old, build, mappings, _ = source(tmp_path, service, monkeypatch)
    own(service)
    migration = LegacyGroupMigration(service, old.root)
    migration.apply(migration.preview(mappings, actor=USER), 'adopt', actor=USER)
    install(SimpleNamespace(app=client.app, sess=SimpleNamespace(list_all=lambda **kw: [])))
    compat = LegacyGroupCompatibility(service, old.root)
    revision = compat.projection(old.read())['revision']
    response = client.put('/api/actions/groups/tools', json={'item': {'id': 'tools', 'name': 'Renamed', 'action_ids': ['echo']},
                                                          'expectedRevision': revision})
    assert response.status_code == 200, response.text
    assert response.json()['groups'][0]['name'] == 'Renamed'
    assert response.json()['revision'] == revision + 1
    stale = client.post('/api/actions/groups/tools/build', json={'expectedRevision': revision})
    # The old HTTP Group release route reports stale registry CAS as 400.
    assert stale.status_code == 400 and 'changed' in stale.text
    response = client.post('/api/actions/groups/tools/build', json={'expectedRevision': revision + 1})
    assert response.status_code == 200, response.text
    assert response.json()['groups'][0]['build']['version'] != build['version']
    response = client.post('/api/actions/groups/tools/unbuild', json={'expectedRevision': revision + 2})
    assert response.status_code == 200, response.text
    assert not response.json()['groups'][0].get('build')
    response = client.delete('/api/actions/groups/tools', params={'expectedRevision': revision + 3})
    assert response.status_code == 200, response.text
    assert response.json()['groups'] == []
    assert Path(build['directory'], 'snapshot.json').exists()


def test_P6_GROUP_02_real_process_writer_excluded(action_http, tmp_path, monkeypatch):
    import subprocess
    _, service = action_http
    old, _, mappings, _ = source(tmp_path, service, monkeypatch)
    own(service)
    migration = LegacyGroupMigration(service, old.root)
    plan = migration.preview(mappings, actor=USER)
    code = 'from packages.actions.group_migration import legacy_writer\nfrom pathlib import Path\nimport sys\nwith legacy_writer(Path(sys.argv[1])):\n print("leased",flush=True)\n input()\n'
    child = subprocess.Popen([sys.executable, '-c', code, str(old.root)], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == 'leased'
        with pytest.raises(fc.ContractError, match='owns this profile'):
            migration.apply(plan, 'competing', actor=USER)
    finally:
        child.communicate('\n', timeout=10)
    assert child.returncode == 0
    assert migration.apply(plan, 'after-writer', actor=USER)['status'] == 'completed'


def test_P6_GROUP_01_offline_cli_dryrun_apply_status(action_http, tmp_path, monkeypatch):
    import subprocess
    _, service = action_http
    old, _, mappings, _ = source(tmp_path, service, monkeypatch)
    own(service)
    maps, plan = tmp_path / 'mapping.json', tmp_path / 'plan.json'
    maps.write_text(json.dumps(mappings), 'utf-8')
    args = [sys.executable, 'scripts/migrate_action_groups.py', '--source-root', str(old.root),
            '--canonical-root', str(service.root), '--authorization-root', str(service.auth.path.parent)]
    roots = [old.root, service.root, service.auth.path.parent]
    before = {p: p.read_bytes() for root in roots for p in root.rglob('*') if p.is_file()}
    result = subprocess.run(args + ['--mappings', str(maps), '--output', str(plan)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert {p: p.read_bytes() for p in before} == before
    service.owner.release()
    result = subprocess.run(args + ['--apply', '--plan', str(plan), '--request-key', 'offline-adopt',
        '--expected-owner-epoch', '1'], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    status = subprocess.run(args + ['--status-key', 'offline-adopt'], capture_output=True, text=True, timeout=20)
    assert status.returncode == 0, status.stderr
    assert json.loads(status.stdout)['operationId'] == receipt['operationId']
    assert not (tmp_path / 'effects').exists()


@pytest.mark.parametrize('phase', ['source-frozen', 'before-commit', 'after-commit'])
def test_P6_GROUP_03_actual_process_death_cold_recovery(action_http, tmp_path, monkeypatch, phase):
    import subprocess
    _, service = action_http
    old, _, mappings, _ = source(tmp_path, service, monkeypatch)
    migration = LegacyGroupMigration(service, old.root)
    own(service)
    plan = migration.preview(mappings, actor=USER)
    path = tmp_path / 'plan.json'
    path.write_text(json.dumps(plan), 'utf-8')
    service.owner.release()
    code = ('import json,os,sys\nfrom pathlib import Path\nfrom scripts.migrate_action_groups import OfflineAuthorization,USER\n'
            'from packages.jobs.final_service import FinalService\nfrom packages.actions.group_migration import LegacyGroupMigration\n'
            's=FinalService(Path(sys.argv[1]),OfflineAuthorization(Path(sys.argv[2])))\ns.owner.claim(1)\n'
            'm=LegacyGroupMigration(s,Path(sys.argv[3]))\n'
            'm.apply(json.loads(Path(sys.argv[4]).read_text()),"cold-original",actor=USER,fault=lambda p:os._exit(73) if p==sys.argv[5] else None)\n')
    result = subprocess.run([sys.executable, '-c', code, str(service.root), str(service.auth.path.parent),
                             str(old.root), str(path), phase], capture_output=True, text=True, timeout=20)
    assert result.returncode == 73, result.stderr
    with pytest.raises(fc.ContractError):
        old.build('tools')
    if phase != 'after-commit':
        assert Groups(service).list(actor=USER)['items'] == []
    result = subprocess.run([sys.executable, 'scripts/migrate_action_groups.py', '--source-root', str(old.root),
        '--canonical-root', str(service.root), '--authorization-root', str(service.auth.path.parent),
        '--apply', '--plan', str(path), '--request-key', 'cold-original', '--expected-owner-epoch', '2'],
        capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    receipt = service.operation_for_key('update', 'cold-original', actor=USER)
    assert json.loads(result.stdout)['operationId'] == receipt['operationId']
    assert not (tmp_path / 'effects').exists()


@pytest.mark.parametrize('kind', ['text', 'invalid-json', 'nonzero'])
def test_P6_GROUP_06_output_contracts(action_http, tmp_path, monkeypatch, kind):
    from types import SimpleNamespace
    from tests.test_authorization_mcp import grants
    _, service = action_http
    old, build, mappings, _ = source(tmp_path, service, monkeypatch, output_format='text' if kind == 'text' else 'json')
    own(service)
    service.auth.change_permissions(grants(), 0)
    migration = LegacyGroupMigration(service, old.root)
    migration.apply(migration.preview(mappings, actor=USER), 'adopt', actor=USER)
    if kind != 'text':
        # External resources deliberately retain old mutable-path semantics.
        (tmp_path / 'effect.py').write_text('print("not JSON")\n' if kind == 'invalid-json' else 'import sys;sys.exit(7)\n', 'utf-8')
    descriptor = {'plugin': build['plugin_id'], 'action_group_release': build['version'],
                  'source_name': build['plugin_id'] + '.echo'}
    result = asyncio.run(group_catalog.invoke(SimpleNamespace(group_service=lambda: service), descriptor,
                         {'value': 'test'}, transport_request_key='output-once'))
    if kind == 'text':
        assert result['isError'] is False and 'structuredContent' not in result
        assert json.loads(result['content'][0]['text'])['value'] == 'test'
    else:
        assert result['isError'] is True
        error = json.loads(result['content'][0]['text'])
        assert error['message'] == 'Action failed; do not automatically replay side effects.'
        assert ('Expecting value' if kind == 'invalid-json' else 'Command exited 7') in error['error']
    repeated = asyncio.run(group_catalog.invoke(SimpleNamespace(group_service=lambda: service), descriptor,
                         {'value': 'test'}, transport_request_key='output-once'))
    assert repeated['_meta']['panOperation']['operationId'] == result['_meta']['panOperation']['operationId']


def test_P6_GROUP_02_unknown_lease_and_generation_are_not_reset(action_http, tmp_path, monkeypatch):
    import subprocess
    _, service = action_http
    old, _, mappings, _ = source(tmp_path, service, monkeypatch)
    own(service)
    migration = LegacyGroupMigration(service, old.root)
    plan = migration.preview(mappings, actor=USER)
    foreign = OwnerFence(old.root)
    foreign.claim(0)
    foreign.release()
    with pytest.raises(fc.ContractError, match='generation changed'):
        migration.apply(plan, 'wrong-generation', actor=USER)
    assert Groups(service).list(actor=USER)['items'] == []
    # An actual abandoned source lease is never fabricated as stopped/released.
    # Use a second fresh source so this case is independent of the epoch conflict.
    other = tmp_path / 'abandoned'
    enroll_fresh_source(other)
    replica = store.Store(other)
    replica.put('actions', old.read()['actions']['echo'])
    replica.put('groups', {'id': 'orphan', 'name': 'Orphan', 'action_ids': ['echo']})
    action = replica.read()['actions']['echo']
    copied = [{**mappings[0], 'groupId': 'orphan'}]
    m = LegacyGroupMigration(service, other)
    reviewed = m.preview(copied, actor=USER)
    code = 'import os,sys\nfrom pathlib import Path\nfrom packages.actions.group_migration import legacy_writer\nwith legacy_writer(Path(sys.argv[1])):\n os._exit(71)\n'
    child = subprocess.run([sys.executable, '-c', code, str(other)], capture_output=True, timeout=15)
    assert child.returncode == 71
    with pytest.raises(fc.ContractError, match='ownership unknown'):
        m.apply(reviewed, 'abandoned', actor=USER)
    with OwnerFence(other).transaction() as db:
        assert db.execute('SELECT count(*) FROM legacy_leases').fetchone()[0] == 1


def test_P6_GROUP_07_all_scope_does_not_drop_remaining_legacy(action_http, tmp_path, monkeypatch):
    _, service = action_http
    old, _, mappings, ref = source(tmp_path, service, monkeypatch)
    own(service)
    migration = LegacyGroupMigration(service, old.root)
    migration.apply(migration.preview(mappings, actor=USER), 'adopt', actor=USER)
    remaining = store.Store(tmp_path / 'remaining-unknown-writer')
    remaining.put('actions', command_action(tmp_path, 'other'))
    remaining.put('groups', {'id': 'other_tools', 'name': 'Still legacy', 'action_ids': ['other']})
    monkeypatch.setattr(store, 'root', lambda: remaining.root)
    adapter = Groups(service)
    preview = adapter.preview({'scope': 'action', 'actionRef': ref}, actor=USER)
    assert preview['excluded'] == [{'groupId': 'other_tools', 'reason': 'legacy_action_identity_unresolved'}]
    with pytest.raises(fc.ContractError):
        adapter.apply(apply_body(preview, 'not-all'), actor=USER)
    assert adapter.list(actor=USER)['items'][0]['revision'] == 1
