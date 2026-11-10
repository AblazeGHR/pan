"""Named P6 legacy Action/configuration successors, without source double writes."""
import asyncio
import copy
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from packages.actions.api import install
from packages.actions.groups import Groups
from packages.actions.group_migration import LegacyGroupMigration, LegacyGroupCompatibility, command_definition
from packages.jobs import final_contracts as fc
from packages.jobs.final_service import object_ref
from tests.test_action_unification_p1 import action_http
from tests.test_action_unification_p6 import source, own
from tests.test_action_groups import command_action
from tests.test_action_unification_p3 import USER, apply_body
from tests.test_authorization_mcp import grants


def adopted(action_http, tmp_path, monkeypatch):
    client, service = action_http
    old, build, maps, ref = source(tmp_path, service, monkeypatch)
    migration = LegacyGroupMigration(service, old.root)
    own(service)
    migration.apply(migration.preview(maps, actor=USER), 'adopt', actor=USER)
    install(SimpleNamespace(app=client.app, sess=SimpleNamespace(list_all=lambda **kw: [])))
    compat = LegacyGroupCompatibility(service, old.root)
    return client, service, old, build, ref, compat


def test_P6_ACTION_01_http_crud_lifecycle_and_new_group_reference(action_http, tmp_path, monkeypatch):
    client, service, old, build, ref, compat = adopted(action_http, tmp_path, monkeypatch)
    raw_source = old.path.read_bytes()
    original_release = Groups(service).release(build['version'], actor=USER)
    with service.transaction(readonly=True) as db:
        before = db.execute('SELECT count(*) FROM final_operations').fetchone()[0]
    value = command_action(tmp_path, 'added')
    routes = [('POST', '/api/actions/actions', {'item': value}),
              ('PUT', '/api/actions/groups/tools', {'item': {'id': 'tools', 'name': 'With added', 'action_ids': ['echo', 'added']}}),
              ('POST', '/api/actions/actions/added/lifecycle', {'enabled': True}),
              ('POST', '/api/actions/groups/tools/build', {}),
              ('DELETE', '/api/actions/actions/added', None)]
    for method, path, body in routes:
        response = client.request(method, path, json=body)
        assert response.status_code == 410, response.text
        assert response.json()['detail']['code'] == 'retired'
    assert old.path.read_bytes() == raw_source
    assert Groups(service).release(build['version'], actor=USER) == original_release
    assert not (tmp_path / 'effects').exists()
    with service.transaction(readonly=True) as db:
        assert db.execute('SELECT count(*) FROM final_operations').fetchone()[0] == before


def test_P6_ACTION_02_original_grants_drafts_and_frozen_lifecycle(action_http, tmp_path, monkeypatch):
    _, service, old, build, ref, compat = adopted(action_http, tmp_path, monkeypatch)
    state = grants(action={'lifecycle': False})
    service.auth.change_permissions(state, 0)
    agent = {'type': 'agent', 'id': 'agent', 'revision': 1}
    raw = compat.projection(old.read())['actions']['echo']
    changed = {**raw, 'name': 'Enabled config edit'}
    saved = compat.put_action(changed, None, 'config-only', actor=agent)
    assert saved['result']['item']['enabled'] is True
    assert saved['result']['actionRef']['id'] == ref['id']
    assert saved['result']['actionRef']['revision'] > ref['revision']
    service.auth.change_permissions(grants(action={'updateConfig': False}), 1)
    agent['revision'] = 2
    revision = compat.projection(old.read())['revision']
    disabled = compat.lifecycle_action('echo', False, revision, 'disable-current', actor=agent)
    assert disabled['result']['item']['enabled'] is False
    frozen = asyncio.run(Groups(service).invoke(build['version'], 'echo', {'value': 'original'}, 'original-still-enabled', actor=USER))
    assert frozen['result']['status'] == 'completed'
    with pytest.raises(fc.ContractError):
        compat.put_action({**changed, 'name': 'denied'}, None, 'bad-edit', actor=agent)
    service.auth.change_permissions(grants(action={'lifecycle': False}), 2)
    agent['revision'] = 3
    draft = compat.put_action(command_action(tmp_path, 'drafted'), None, 'draft', actor=agent, draft=True)
    binding = draft['result']['actionRef']
    assert service.get('action', binding['id'], actor=USER)['state'] == 'draft'
    assert draft['result']['item']['enabled'] is False
    with pytest.raises(fc.ContractError):
        compat.lifecycle_action('drafted', True, compat.projection(old.read())['revision'], 'denied-enable', actor=agent)


def test_P6_ACTION_03_standalone_mapping_then_old_group_add(action_http, tmp_path, monkeypatch):
    client, service = action_http
    old, _, maps, _ = source(tmp_path, service, monkeypatch)
    spare = command_action(tmp_path, 'spare')
    old.put('actions', spare)
    migration = LegacyGroupMigration(service, old.root)
    assert migration.preview(maps, actor=USER)['blocked'][0]['actionId'] == 'spare'
    spare = old.read()['actions']['spare']
    ref = object_ref('action', service.publish_action('canonical_spare', command_definition(spare)))
    maps.append({'groupId': None, 'version': 'draft', 'actionId': 'spare', 'definitionDigest': fc.digest(spare), 'actionRef': ref})
    plan = migration.preview(maps, actor=USER)
    assert plan['blocked'] == []
    own(service)
    migration.apply(plan, 'adopt-with-spare', actor=USER)
    compat = LegacyGroupCompatibility(service, old.root)
    result = compat.mutate('tools', 'put', None, 'add-mapped', actor=USER,
        raw={'id': 'tools', 'name': 'Mapped spare', 'action_ids': ['echo', 'spare']})
    assert result['result']['group']['tools'][1]['actionRef'] == ref
    created = compat.mutate(None, 'put', None, 'new-group', actor=USER, raw={'name': 'New', 'action_ids': []}, create=True)
    assert created['result']['item']['id'].startswith('group_')
    assert created['result']['group']['tools'] == []


def test_P6_ACTION_04_canonical_update_invalidates_legacy_cas_and_old_adoption_backfill(action_http, tmp_path, monkeypatch):
    _, service, old, _, ref, compat = adopted(action_http, tmp_path, monkeypatch)
    revision = compat.projection(old.read())['revision']
    # Reproduce the exact metadata shape of 267287c, with no guessed aliases.
    with service.transaction() as db:
        db.execute("DELETE FROM final_documents WHERE kind='legacy_action_binding'")
        Groups.save(db, 'action_group_registry', compat.root, {'revision': revision})
    compat.put_action(command_action(tmp_path, 'new'), revision, 'backfill', actor=USER, draft=True)
    view = compat.projection(old.read())
    assert set(view['actions']) == {'echo', 'new'}
    value = command_definition(view['actions']['echo'])
    value['name'] = 'Canonical changed'
    head = service.get('action', ref['id'], actor=USER)
    service.write('action', ref['id'], {'definition': value, 'expectedRevision': head['revision'], 'requestKey': 'canonical-edit'}, actor=USER)
    with pytest.raises(fc.ContractError, match='registry changed'):
        compat.put_action(view['actions']['echo'], view['revision'], 'stale-edit', actor=USER)
    assert compat.projection(old.read())['actions']['echo']['name'] == 'Canonical changed'


def test_P6_ACTION_05_real_stdio_drafts_original_receipt_and_no_hidden_notifications(tmp_path, monkeypatch):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from tests.test_authorization_mcp import harness, close, result
    async def exercise():
        h = harness(tmp_path)
        try:
            old, _, maps, _ = source(tmp_path, h.service, monkeypatch)
            migration = LegacyGroupMigration(h.service, old.root)
            migration.apply(migration.preview(maps, actor=USER), 'adopt', actor=USER)
            install(SimpleNamespace(app=h.app, sess=SimpleNamespace(list_all=lambda **kw: [])))
            raw_source = old.path.read_bytes()
            params = StdioServerParameters(command=sys.executable, args=['-m', 'packages.mcp.server'],
                cwd=str(Path.cwd()), env={**os.environ, 'PAN_PLUGIN_IPC': h.bridge.address,
                    'PAN_AGENT_SESSION_ID': h.session.id, 'PAN_API_URL': 'http://127.0.0.1:1'})
            async with stdio_client(params) as streams, ClientSession(*streams) as client:
                await client.initialize()
                raw = command_action(tmp_path, 'via_mcp')
                denied = result(await client.call_tool('action_drafts', {'operation': 'put', 'item': raw}))
                assert denied['error']['code'] == 403
                h.svc.auth.change_permissions(grants(action={'lifecycle': False}), 0)
                arguments = {'operation': 'put', 'item': raw, 'request_key': 'draft-once'}
                created = result(await client.call_tool('action_drafts', arguments))
                assert created['error']['code'] == 410, created
                repeated = result(await client.call_tool('action_drafts', arguments))
                assert repeated == created
                grouped = result(await client.call_tool('action_drafts', {'operation': 'put', 'kind': 'groups',
                    'item': {'name': 'MCP draft', 'action_ids': ['via_mcp']}, 'request_key': 'group-once'}))
                assert grouped['error']['code'] == 410, grouped
                with h.service.transaction(readonly=True) as db:
                    assert db.execute("SELECT count(*) FROM final_operations WHERE requestKey IN ('draft-once','group-once')").fetchone()[0] == 0
                forbidden = result(await client.call_tool('action_drafts', {'operation': 'build', 'kind': 'groups'}))
                assert forbidden['error'] == 'Invalid draft operation or kind'
                with h.service.transaction(readonly=True) as db:
                    assert db.execute("SELECT count(*) FROM final_documents WHERE kind='job'").fetchone()[0] == 0
                assert h.service._pending_outbox_snapshot() == []
                assert old.path.read_bytes() == raw_source
        finally:
            await close(h)
    asyncio.run(exercise())


def test_P6_COMPAT_01_owned_cancel_history_and_notification_identity(action_http, tmp_path, monkeypatch):
    from packages.jobs.final_service import FinalService
    _, service, old, build, ref, compat = adopted(action_http, tmp_path, monkeypatch)
    marker = tmp_path / 'cancel-effect'
    (tmp_path / 'effect.py').write_text('from pathlib import Path\nimport time\nPath(' + repr(str(marker)) + ').write_text("x")\ntime.sleep(60)\n', 'utf-8')
    original_release = Groups(service).release(build['version'], actor=USER)
    async def cancel():
        task = asyncio.create_task(Groups(service).invoke(build['version'], 'echo', {'value': 'long'}, 'original-cancel', actor=USER))
        try:
            async with asyncio.timeout(10):
                while not marker.exists():
                    await asyncio.sleep(.02)
            task.cancel()
            return await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    receipt = asyncio.run(cancel())
    assert receipt['result']['source'] == {'groupRelease': build['version']}
    assert receipt['result']['status'] == 'cancelled'
    assert receipt['result']['commands'][0]['cleanupProof']['confirmed'] is True
    repeated = asyncio.run(Groups(service).invoke(build['version'], 'echo', {'value': 'long'}, 'original-cancel', actor=USER))
    assert repeated['replayed'] and repeated['operationId'] == receipt['operationId']
    cold = FinalService(service.root, service.auth)
    assert cold.operation_for_key('invoke', 'original-cancel', actor=USER)['operationId'] == receipt['operationId']
    assert cold.operation_for_key('invoke', 'original-cancel', actor=USER)['result']['source'] == {'groupRelease': build['version']}
    assert Groups(cold).release(build['version'], actor=USER) == original_release
    assert marker.read_text() == 'x'
    assert service._pending_outbox_snapshot() == []
    with service.transaction(readonly=True) as db:
        assert db.execute("SELECT count(*) FROM final_documents WHERE kind='job'").fetchone()[0] == 0


def test_P6_COMPAT_02_real_stdio_peer_timeout_does_not_cancel_or_replay(tmp_path, monkeypatch):
    from datetime import timedelta
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.shared.exceptions import McpError
    from tests.test_authorization_mcp import harness, close
    async def exercise():
        h = harness(tmp_path)
        try:
            old, build, maps, _ = source(tmp_path, h.service, monkeypatch)
            migration = LegacyGroupMigration(h.service, old.root)
            migration.apply(migration.preview(maps, actor=USER), 'adopt', actor=USER)
            (tmp_path / 'effect.py').write_text('from pathlib import Path\nimport time\np=Path(' + repr(str(tmp_path / 'once')) + ')\np.write_text(p.read_text()+"x" if p.exists() else "x")\ntime.sleep(1)\nprint("{}")\n', 'utf-8')
            h.svc.auth.change_permissions(grants(), 0)
            h.session.adapter_config['plugin_bindings'].append({'plugin_id': build['plugin_id'], 'mode': 'standalone', 'enabled': True})
            params = StdioServerParameters(command=sys.executable, args=['-m', 'packages.plugins.mcp', '--view', 'standalone', '--plugin', build['plugin_id']],
                cwd=str(Path.cwd()), env={**os.environ, 'PAN_PLUGIN_IPC': h.bridge.address,
                    'PAN_AGENT_SESSION_ID': h.session.id, 'PAN_API_URL': 'http://127.0.0.1:1'})
            async with stdio_client(params) as streams, ClientSession(*streams) as client:
                await client.initialize()
                name = (await client.list_tools()).tools[0].name
                with pytest.raises(McpError):
                    await client.call_tool(name, {'value': 'once'}, read_timeout_seconds=timedelta(seconds=.2),
                                           meta={'panRequestKey': 'timed-out-original'})
                agent = {'type': 'agent', 'id': 'agent', 'revision': 1}
                async with asyncio.timeout(10):
                    while True:
                        receipt = h.service.operation_for_key('invoke', 'timed-out-original', actor=agent)
                        if receipt['result'].get('phase') == 'settled':
                            break
                        await asyncio.sleep(.02)
                assert receipt['result']['status'] == 'completed'
                repeated = await client.call_tool(name, {'value': 'once'}, meta={'panRequestKey': 'timed-out-original'})
                assert not repeated.isError
                assert repeated.meta['panOperation']['operationId'] == receipt['operationId']
                assert (tmp_path / 'once').read_text() == 'x'
                assert h.service._pending_outbox_snapshot() == []
        finally:
            await close(h)
    asyncio.run(exercise())


def test_P6_COMPAT_03_build_restart_notification_is_not_replayed(action_http, tmp_path, monkeypatch):
    client, service = action_http
    old, build, maps, _ = source(tmp_path, service, monkeypatch)
    own(service)
    migration = LegacyGroupMigration(service, old.root)
    migration.apply(migration.preview(maps, actor=USER), 'adopt', actor=USER)
    calls = []
    session = SimpleNamespace(id='owned_notify_session', name='Owned', adapter_config={
        'plugin_bindings': [{'plugin_id': build['plugin_id'], 'mode': 'standalone', 'enabled': True}]})
    install(SimpleNamespace(app=client.app, sess=SimpleNamespace(list_all=lambda **kw: [session]),
        _session_plugin_fields=lambda s: {'pluginBindings': s.adapter_config['plugin_bindings']},
        worker=SimpleNamespace(find_alive_worker_by_session=lambda sid: SimpleNamespace(status='idle'),
                               request_delayed_restart=lambda sid: calls.append(sid))))
    expected = LegacyGroupCompatibility(service, old.root).projection(old.read())['revision']
    headers = {'x-pan-request-key': 'build-once'}
    first = client.post('/api/actions/groups/tools/build', json={'expectedRevision': expected}, headers=headers)
    assert first.status_code == 410, first.text
    assert first.json()['detail']['code'] == 'retired'
    repeated = client.post('/api/actions/groups/tools/build', json={'expectedRevision': expected}, headers=headers)
    assert repeated.status_code == 410 and repeated.json() == first.json()
    assert calls == []
    assert LegacyGroupCompatibility(service, old.root).projection(old.read())['revision'] == expected
    assert Groups(service).release(build['version'], actor=USER)['version'] == build['version']


def test_P6_ACTION_06_deleting_one_reviewed_alias_does_not_retire_other_alias(action_http, tmp_path, monkeypatch):
    _, service = action_http
    old, _, maps, ref = source(tmp_path, service, monkeypatch)
    alias = {**old.read()['actions']['echo'], 'id': 'other_alias'}
    old.put('actions', alias)
    alias = old.read()['actions']['other_alias']
    maps.append({'groupId': None, 'version': 'draft', 'actionId': 'other_alias',
                 'definitionDigest': fc.digest(alias), 'actionRef': ref})
    migration = LegacyGroupMigration(service, old.root)
    plan = migration.preview(maps, actor=USER)
    assert plan['blocked'] == []
    own(service)
    migration.apply(plan, 'adopt-two-aliases', actor=USER)
    compat = LegacyGroupCompatibility(service, old.root)
    compat.delete_action('other_alias', None, 'delete-one-alias', actor=USER)
    view = compat.projection(old.read())
    assert 'echo' in view['actions'] and 'other_alias' not in view['actions']
    assert service.get('action', ref['id'], actor=USER)['state'] == 'published'


def test_P6_ACTION_07_reserved_business_fields_keep_fixed_argv_semantics(action_http, tmp_path, monkeypatch):
    _, service, old, _, _, compat = adopted(action_http, tmp_path, monkeypatch)
    raw = command_action(tmp_path, 'reserved')
    raw['input_schema']['properties'].update(cwd={'type': 'string'}, commands={'type': 'array'})
    raw['input_schema']['required'].append('cwd')
    raw['execution']['args'][-1] = '{cwd}'
    created = compat.put_action(raw, None, 'reserved', actor=USER)
    assert created['result']['item']['input_schema'] == raw['input_schema']
    compat.mutate('tools', 'put', None, 'reserved-tools', actor=USER,
        raw={'id': 'tools', 'name': 'Reserved fields', 'action_ids': ['reserved']})
    published = compat.mutate('tools', 'build', None, 'reserved-build', actor=USER)['result']['release']
    arguments = {'value': 'unused', 'cwd': 'business-only "雪"', 'commands': [
        {'argv': [sys.executable, '-c', 'from pathlib import Path;Path("must-not-run").touch()']} ]}
    result = asyncio.run(Groups(service).invoke(published['version'], 'reserved', arguments, 'reserved-once', actor=USER))
    assert result['result']['status'] == 'completed', result
    import json
    assert json.loads(result['result']['commands'][0]['stdout']) == {'value': arguments['cwd']}
    assert not (tmp_path / 'must-not-run').exists()
    assert len(result['result']['commands']) == 1


def test_P6_COMPAT_04_single_argv_projects_but_multiple_outputs_block_atomically(action_http, tmp_path, monkeypatch):
    _, service, old, _, ref, compat = adopted(action_http, tmp_path, monkeypatch)
    value = command_definition(old.read()['actions']['echo'])
    original = value['execution']['config']
    value['execution']['config'] = {'argv': [original['executable'], *original['args']],
        'cwd': original['cwd'], 'timeout': 30, 'hideWindow': True, 'env': {}}
    def publish(definition, key):
        head = service.get('action', ref['id'], actor=USER)
        head = service.write('action', ref['id'], {'definition': definition, 'expectedRevision': head['revision'],
            'requestKey': 'edit-' + key}, actor=USER)['result']['object']
        service.control('action', ref['id'], {'operation': 'publish', 'expectedRevision': head['revision'],
            'expectedStateRevision': head['stateRevision'], 'requestKey': 'publish-' + key, 'parameters': {}}, actor=USER)
        return service.action_ref(ref['id'], actor=USER)
    second = publish(value, 'single-argv')
    assert compat.projection(old.read())['actions']['echo']['execution']['executable'] == original['executable']
    groups = Groups(service)
    preview = groups.preview({'scope': 'action', 'actionRef': second}, actor=USER)
    assert not preview['items'][0]['blockers']
    groups.apply(apply_body(preview, 'single-upgrade'), actor=USER)
    groups.publish('tools', 2, 'single-build', actor=USER)
    before = copy.deepcopy(groups.list(actor=USER))
    value['execution']['config'] = {'cwd': str(tmp_path), 'commands': [
        {'argv': [sys.executable, '-c', 'print("first")']}, {'argv': [sys.executable, '-c', 'print("second")']}]}
    third = publish(value, 'multiple')
    preview = groups.preview({'scope': 'action', 'actionRef': third}, actor=USER)
    assert any('legacy_flat_output_contract' in b['fields'] for b in preview['items'][0]['blockers'])
    with pytest.raises(fc.ContractError):
        groups.apply(apply_body(preview, 'must-not-upgrade'), actor=USER)
    assert groups.list(actor=USER) == before
    assert compat.projection(old.read())['preservedActions'][0]['id'] == 'echo'
    assert not (tmp_path / 'effects').exists()
