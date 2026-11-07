"""P3 Group fixed releases, atomic reference updates and canonical invocation."""
import asyncio
import copy
import json
import os
from pathlib import Path
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from packages.actions.groups import Groups
from packages.actions import command_execution
from packages.jobs import final_contracts as fc
from packages.jobs.final_service import object_ref
from tests.test_action_unification_p1 import action_http

USER = {'type': 'user', 'id': 'local'}


def install(service, name='echo', command='echo first'):
    value = command_execution.product_definition('process.shell')
    value.update(system=False)
    value['execution']['config'] = {'command': command, 'cwd': str(service.root)}
    try:
        old = service.get('action', name, actor=USER)
    except KeyError:
        return object_ref('action', service.publish_action(name, value))
    updated = service.write('action', name, {'definition': value, 'expectedRevision': old['revision'],
        'requestKey': 'edit-' + command}, actor=USER)['result']['object']
    service.control('action', name, {'operation': 'publish', 'expectedRevision': updated['revision'],
        'expectedStateRevision': updated['stateRevision'], 'requestKey': 'version-' + command,
        'parameters': {}}, actor=USER)
    return service.action_ref(name, actor=USER)


def put(groups, ref, gid='tools', expected=0, key=None):
    return groups.put({'id': gid, 'name': gid, 'tools': [{'alias': 'echo', 'actionRef': ref}]},
        expected, key or 'put-' + gid, actor=USER)['result']['group']


def apply_body(preview, key='apply'):
    return {'selection': preview['selection'], 'previewDigest': preview['previewDigest'],
            'targets': [{'groupId': i['groupId'], 'expectedRevision': i['expectedRevision']} for i in preview['items']],
            'requestKey': key}


def test_fixed_release_schema_draft_upgrade_explicit_publish(action_http):
    client, service = action_http
    groups = Groups(service)
    first = install(service)
    put(groups, first)
    released = groups.publish('tools', 1, 'publish-1', actor=USER)['result']['release']
    original = copy.deepcopy(released)
    second = install(service, command='echo second')
    preview = groups.preview({'scope': 'group', 'groupId': 'tools'}, actor=USER)
    assert preview['items'][0]['changes'][0]['to'] == second
    body = apply_body(preview)
    applied = groups.apply(body, actor=USER)
    assert groups.apply(body, actor=USER)['operationId'] == applied['operationId']
    assert groups.release(released['version'], actor=USER) == original
    assert groups.list(actor=USER)['items'][0]['release'] == released['version']
    newer = groups.publish('tools', 3, 'publish-2', actor=USER)['result']['release']
    assert released['tools'][0]['actionRef'] == first
    assert newer['tools'][0]['actionRef'] == second
    assert groups.release(released['version'], actor=USER) == original
    assert client.get('/api/action-groups').status_code == 200


def test_batch_cas_concurrent_edit_atomic_failure_and_exact_targets(action_http):
    _, service = action_http
    groups = Groups(service)
    first = install(service)
    put(groups, first, 'one')
    put(groups, first, 'two')
    second = install(service, command='echo second')
    selection = {'scope': 'action', 'actionRef': second}
    preview = groups.preview(selection, actor=USER)
    put(groups, first, 'two', 1, 'edit-two')
    with pytest.raises(fc.ContractError, match='changed'):
        groups.apply(apply_body(preview), actor=USER)
    assert all(g['tools'][0]['actionRef'] == first for g in groups.list(actor=USER)['items'])
    preview = groups.preview(selection, actor=USER)
    wrong = apply_body(preview)
    wrong['targets'].pop()
    with pytest.raises(fc.ContractError):
        groups.apply(wrong, actor=USER)
    body = apply_body(preview)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: groups.apply(body, actor=USER), range(2)))
    assert results[0]['operationId'] == results[1]['operationId']
    assert all(g['tools'][0]['actionRef'] == second for g in groups.list(actor=USER)['items'])


def test_permission_and_version_conflicts_no_write(action_http):
    _, service = action_http
    groups = Groups(service)
    first = install(service)
    put(groups, first)
    agent = {'type': 'agent', 'id': 'agent', 'revision': 0}
    with pytest.raises(fc.ContractError) as denied:
        groups.publish('tools', 1, 'denied', actor=agent)
    assert denied.value.code == 'authorization_denied'
    corrupted = {**first, 'digest': '0' * 64}
    with pytest.raises(fc.ContractError):
        groups.preview({'scope': 'action', 'actionRef': corrupted}, actor=USER)
    assert groups.list(actor=USER)['items'][0]['revision'] == 1


@pytest.mark.parametrize('mode,view', [('gateway', 'gateway'), ('standalone', 'standalone'), ('pan_native', 'native')])
def test_real_mcp_stdio_pinned_release_original_identity_and_revocation(tmp_path, mode, view):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from packages.plugins.lifecycle import pin, _pins
    from tests.test_authorization_mcp import harness, close, grants, result

    async def exercise():
        h = harness(tmp_path)
        groups = Groups(h.service)
        first = install(h.service, command='echo first')
        put(groups, first)
        release1 = groups.publish('tools', 1, 'build-one', actor=USER)['result']['release']
        h.session.adapter_config['plugin_bindings'].append({'plugin_id': 'action-group-tools', 'enabled': True, 'mode': mode})
        h.svc.auth.change_permissions(grants(), 0)
        pin(h.session, h.bridge.runtime.context(h.session.id)[1])
        params = StdioServerParameters(command=sys.executable, args=['-m', 'packages.plugins.mcp', '--view', view,
            *(['--plugin', 'action-group-tools'] if view == 'standalone' else [])],
            cwd=str(Path.cwd()), env={**os.environ, 'PAN_PLUGIN_IPC': h.bridge.address,
                'PAN_AGENT_SESSION_ID': h.session.id, 'PAN_API_URL': 'http://127.0.0.1:1'})
        try:
            async with stdio_client(params) as streams, ClientSession(*streams) as client:
                await client.initialize()
                async def discover():
                    if view == 'gateway':
                        return result(await client.call_tool('api_discover', {'include_schema': True}))['tools'][0]
                    tools = (await client.list_tools()).tools
                    name = 'pan_action_group_tools_echo' if view == 'native' else 'action-group-tools.echo'
                    tool = next(t for t in tools if t.name == name)
                    descriptor = (await h.bridge.runtime.directory(h.session.id, view, 'action-group-tools'))[0]
                    return {'contract_version': descriptor['contract_version'], 'inputSchema': tool.inputSchema}
                async def call(key='once', args=None):
                    envelope = {'arguments': args or {}, 'requestKey': key}
                    if view == 'gateway':
                        return result(await client.call_tool('api_call', {'name': 'action-group-tools.echo', 'arguments': envelope}))
                    return result(await client.call_tool('pan_action_group_tools_echo' if view == 'native' else 'action-group-tools.echo', envelope))
                descriptor = await discover()
                assert descriptor['contract_version'] == release1['version']
                assert descriptor['inputSchema']['properties']['arguments'] == release1['tools'][0]['inputSchema']
                initial = await call()
                assert 'result' in initial, initial
                assert initial['result']['commands'][0]['stdout'].strip() == 'first', initial
                assert initial['result']['actionRef'] == first
                assert (await call())['operationId'] == initial['operationId']
                assert (await call(args={'command': 'echo must-not-run'}))['error']['code'] in {'idempotency_conflict', 'invalid_arguments'}
                second = install(h.service, command='echo second')
                groups.apply(apply_body(groups.preview({'scope': 'group', 'groupId': 'tools'}, actor=USER)), actor=USER)
                release2 = groups.publish('tools', 3, 'build-two', actor=USER)['result']['release']
                assert (await discover())['contract_version'] == release1['version']
                assert (await call('old-worker'))['result']['commands'][0]['stdout'].strip() == 'first'
                # A real start captures the new catalog. No running pin mutation
                # is part of publication or reference apply.
                _pins.pop(h.session.id, None)
                pin(h.session, h.bridge.runtime.context(h.session.id)[1])
                assert (await discover())['contract_version'] == release2['version']
                assert (await call('new-worker'))['result']['commands'][0]['stdout'].strip() == 'second'
                h.svc.auth.change_permissions(grants(action={'invoke': False}), 1)
                assert (await call('revoked'))['error']['code'] == 'authorization_denied'
                assert (await call('new-worker'))['operationId']
                with h.service.transaction(readonly=True) as db:
                    assert db.execute("SELECT count(*) FROM final_documents WHERE kind='job'").fetchone()[0] == 0
        finally:
            _pins.pop(h.session.id, None)
            await close(h)
    asyncio.run(exercise())


def test_unknown_after_effect_queries_original_identity_without_replay(action_http, monkeypatch):
    _, service = action_http
    groups = Groups(service)
    first = install(service)
    put(groups, first)
    release = groups.publish('tools', 1, 'unknown-build', actor=USER)['result']['release']
    service.owner.claim(0)
    original = service.executor.execute_commands
    calls = []
    async def lose_receipt(**kwargs):
        calls.append(kwargs['context'].identity)
        await original(**kwargs)
        raise RuntimeError('injected crash after effect before final receipt')
    monkeypatch.setattr(service.executor, 'execute_commands', lose_receipt)
    with pytest.raises(RuntimeError, match='injected crash'):
        asyncio.run(groups.invoke(release['version'], 'echo', {}, 'original', actor=USER))
    replay = asyncio.run(groups.invoke(release['version'], 'echo', {}, 'original', actor=USER))
    assert replay['result']['status'] == 'uncertain'
    assert len(calls) == 1
    assert service.operation_for_key('invoke', 'original', actor=USER)['operationId'] == replay['operationId']


def test_retired_target_blocks_entire_batch(action_http):
    _, service = action_http
    groups = Groups(service)
    first = install(service)
    put(groups, first, 'one')
    put(groups, first, 'two')
    second = install(service, command='echo second')
    preview = groups.preview({'scope': 'action', 'actionRef': second}, actor=USER)
    head = service.get('action', 'echo', actor=USER)
    service.control('action', 'echo', {'operation': 'retire', 'expectedRevision': head['revision'],
        'expectedStateRevision': head['stateRevision'], 'requestKey': 'retire', 'parameters': {'revision': second['revision']}}, actor=USER)
    with pytest.raises(fc.ContractError):
        groups.apply(apply_body(preview), actor=USER)
    assert all(g['tools'][0]['actionRef'] == first for g in groups.list(actor=USER)['items'])


def test_changed_schema_has_explicit_diff_and_blocks_all_groups(action_http):
    _, service = action_http
    groups = Groups(service)
    first = install(service)
    put(groups, first, 'one')
    put(groups, first, 'two')
    value = command_execution.product_definition('process.shell')
    value.update(system=False)
    value['inputSchema'].setdefault('properties', {})['newField'] = {'type': 'string'}
    item = service.write('action', first['id'], {'definition': value, 'expectedRevision': 1,
        'requestKey': 'schema-edit'}, actor=USER)['result']['object']
    service.control('action', first['id'], {'operation': 'publish', 'expectedRevision': item['revision'],
        'expectedStateRevision': item['stateRevision'], 'requestKey': 'schema-publish', 'parameters': {}}, actor=USER)
    target = service.action_ref(first['id'], actor=USER)
    preview = groups.preview({'scope': 'action', 'actionRef': target}, actor=USER)
    assert all('inputSchema' in i['changes'][0]['differences'] and i['blockers'] for i in preview['items'])
    with pytest.raises(fc.ContractError):
        groups.apply(apply_body(preview), actor=USER)
    assert all(g['tools'][0]['actionRef'] == first for g in groups.list(actor=USER)['items'])


def test_http_cas_authorization_explicit_publish_and_history(action_http, monkeypatch):
    client, service = action_http
    first = install(service)
    payload = {'group': {'id': 'tools', 'name': 'Tools', 'tools': [{'alias': 'echo', 'actionRef': first}]},
        'expectedRevision': 0, 'requestKey': 'http-put'}
    assert client.put('/api/action-groups/tools', json=payload).status_code == 200
    assert client.put('/api/action-groups/tools', json={**payload, 'requestKey': 'stale'}).status_code == 409
    client.headers.pop('Authorization')
    assert client.get('/api/action-groups').status_code == 401
    client.headers['Authorization'] = 'Bearer ' + service.auth.login(service.auth.key())
    from packages.core import session
    monkeypatch.setattr(session, 'list_all', lambda **kwargs: [])
    response = client.post('/api/action-groups/tools/build', json={'expectedRevision': 1, 'requestKey': 'http-build'})
    assert response.status_code == 200, response.text
    release = response.json()['result']['release']
    assert client.get('/api/action-groups/releases/' + release['version']).json() == release
    unbuilt = client.post('/api/action-groups/tools/unbuild', json={'expectedRevision': 2, 'requestKey': 'http-unbuild'})
    assert unbuilt.status_code == 200, unbuilt.text
    assert 'release' not in unbuilt.json()['result']['group']
    assert client.get('/api/action-groups/releases/' + release['version']).json() == release


def test_atomic_rollback_on_second_group_storage_failure(action_http, monkeypatch):
    _, service = action_http
    groups = Groups(service)
    first = install(service)
    put(groups, first, 'one')
    put(groups, first, 'two')
    second = install(service, command='echo second')
    preview = groups.preview({'scope': 'action', 'actionRef': second}, actor=USER)
    original = groups.save
    def fail_second(db, kind, identity, value):
        if identity == 'two':
            raise OSError('injected persistence failure')
        original(db, kind, identity, value)
    monkeypatch.setattr(groups, 'save', fail_second)
    with pytest.raises(OSError):
        groups.apply(apply_body(preview), actor=USER)
    assert all(g['revision'] == 1 and g['tools'][0]['actionRef'] == first for g in groups.list(actor=USER)['items'])
    with pytest.raises(KeyError):
        service.operation_for_key('update', 'apply', actor=USER)


def test_legacy_json_group_preview_only_preserves_bytes(action_http, tmp_path, monkeypatch):
    from packages.actions import store
    from tests.test_action_groups import api_action
    _, service = action_http
    monkeypatch.setattr(store, 'root', lambda: tmp_path / 'legacy')
    legacy = store.Store()
    legacy.put('actions', api_action('old'))
    legacy.put('groups', {'id': 'legacy_tools', 'name': 'Legacy tools', 'action_ids': ['old']})
    before = legacy.path.read_bytes()
    groups = Groups(service)
    first = install(service)
    service.register_action_alias('old', first['id'], first['revision'], first['digest'], actor=USER)
    preview = groups.preview({'scope': 'action', 'actionRef': first}, actor=USER)
    assert preview['excluded'] == [{'groupId': 'legacy_tools', 'reason': 'legacy_json_preview_only'}]
    with pytest.raises(fc.ContractError):
        groups.apply(apply_body(preview), actor=USER)
    single = groups.preview({'scope': 'group', 'groupId': 'legacy_tools'}, actor=USER)
    assert single['excluded'][0]['reason'] == 'legacy_json_preview_only'
    with pytest.raises(fc.ContractError):
        groups.apply(apply_body(single, 'legacy-single'), actor=USER)
    assert legacy.path.read_bytes() == before


def test_live_auth_revision_invalidates_preview_and_no_hidden_scope_shrink(action_http):
    _, service = action_http
    from tests.test_authorization_mcp import grants
    groups = Groups(service)
    first = install(service)
    put(groups, first, 'one')
    put(groups, first, 'two')
    second = install(service, command='echo second')
    service.auth.change_permissions(grants(), 0)
    agent = {'type': 'agent', 'id': 'agent', 'revision': 1}
    preview = groups.preview({'scope': 'action', 'actionRef': second}, actor=agent)
    service.auth.change_permissions(grants(action={'updateConfig': False}), 1)
    with pytest.raises(fc.ContractError) as denied:
        groups.apply(apply_body(preview), actor=agent)
    assert denied.value.code == 'authorization_denied'
    with pytest.raises(fc.ContractError):
        groups.preview({'scope': 'action', 'actionRef': second}, actor=agent)
    assert all(g['revision'] == 1 for g in groups.list(actor=USER)['items'])


def test_late_canonical_binding_resolves_current_app_service(action_http):
    from types import SimpleNamespace
    from packages.automation.authorization import bind_objects
    client, service = action_http
    runtime = SimpleNamespace()
    app = client.app
    app.state.job_final_service = None
    bind_objects(runtime, app)
    assert runtime.group_service() is None
    app.state.job_final_service = service
    assert runtime.group_service() is service
    app.state.job_final_service = None
    assert runtime.group_service() is None
    app.state.job_final_service = service


def test_legacy_writer_cannot_shadow_canonical_group(action_http, tmp_path, monkeypatch):
    from packages.actions import store, group_catalog
    from tests.test_action_groups import api_action
    _, service = action_http
    monkeypatch.setattr(store, 'root', lambda: tmp_path / 'legacy')
    monkeypatch.setattr(group_catalog, 'canonical_path', lambda: service.creators.path)
    first = install(service)
    put(Groups(service), first)
    legacy = store.Store()
    legacy.put('actions', api_action('old'))
    before = legacy.path.read_bytes()
    with pytest.raises(ValueError, match='canonical-owned'):
        legacy.put('groups', {'id': 'tools', 'name': 'Shadow', 'action_ids': ['old']})
    assert legacy.path.read_bytes() == before


def test_new_latest_after_preview_conflicts_and_explicit_batch_is_exact(action_http):
    _, service = action_http
    groups = Groups(service)
    first = install(service)
    put(groups, first, 'one')
    put(groups, first, 'two')
    second = install(service, command='echo second')
    latest_preview = groups.preview({'scope': 'group', 'groupId': 'one'}, actor=USER)
    install(service, command='echo third')
    with pytest.raises(fc.ContractError):
        groups.apply(apply_body(latest_preview), actor=USER)
    explicit = groups.preview({'scope': 'action', 'actionRef': second, 'groupIds': ['one']}, actor=USER)
    groups.apply(apply_body(explicit), actor=USER)
    by_id = {g['id']: g for g in groups.list(actor=USER)['items']}
    assert by_id['one']['tools'][0]['actionRef'] == second
    assert by_id['two']['tools'][0]['actionRef'] == first


def test_unbuild_before_delete_keeps_release_audit(action_http):
    _, service = action_http
    groups = Groups(service)
    first = install(service)
    put(groups, first)
    release = groups.publish('tools', 1, 'delete-build', actor=USER)['result']['release']
    with pytest.raises(fc.ContractError):
        groups.delete('tools', 2, 'deny-delete', actor=USER)
    groups.unbuild('tools', 2, 'delete-unbuild', actor=USER)
    groups.delete('tools', 3, 'delete', actor=USER)
    assert groups.list(actor=USER)['items'] == []
    assert groups.release(release['version'], actor=USER) == release
