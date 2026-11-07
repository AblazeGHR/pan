"""MA review: legacy coverage stays blocked; direct stdio schema is explicit."""
import asyncio
import json
import os
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import pytest

from packages.actions.groups import Groups
from packages.jobs import final_contracts as fc
from packages.jobs.final_service import object_ref
from tests.test_action_unification_p1 import action_http
from tests.test_action_unification_p3 import USER, install, put, apply_body


def test_all_groups_unmapped_built_legacy_is_not_silently_excluded(action_http, tmp_path, monkeypatch):
    from packages.actions import store
    from tests.test_action_groups import command_action
    _, service = action_http
    monkeypatch.setattr(store, 'root', lambda: tmp_path / 'legacy')
    legacy = store.Store()
    legacy.put('actions', command_action(tmp_path, 'old_echo'))
    legacy.put('groups', {'id': 'legacy_tools', 'name': 'Original', 'action_ids': ['old_echo']})
    build = legacy.build('legacy_tools')
    source = legacy.path.read_bytes()
    artifacts = {p.name: p.read_bytes() for p in Path(build['directory']).iterdir()}
    groups = Groups(service)
    ref = install(service)
    put(groups, ref)
    preview = groups.preview({'scope': 'action', 'actionRef': ref}, actor=USER)
    assert preview['excluded'] == [{'groupId': 'legacy_tools', 'reason': 'legacy_action_identity_unresolved'}]
    with pytest.raises(fc.ContractError) as blocked:
        groups.apply(apply_body(preview), actor=USER)
    assert blocked.value.code == 'reference_conflict'
    # Even a P1 alias proves identity only, not stopped writer, schema/output
    # compatibility or adoption of the original release snapshot.
    service.register_action_alias('old_echo', ref['id'], ref['revision'], ref['digest'], actor=USER)
    preview = groups.preview({'scope': 'action', 'actionRef': ref}, actor=USER)
    assert preview['excluded'] == [{'groupId': 'legacy_tools', 'reason': 'legacy_json_preview_only'}]
    with pytest.raises(fc.ContractError):
        groups.apply(apply_body(preview, 'after-alias'), actor=USER)
    assert groups.list(actor=USER)['items'][0]['revision'] == 1
    assert legacy.path.read_bytes() == source
    assert artifacts == {p.name: p.read_bytes() for p in Path(build['directory']).iterdir()}


def test_explicit_alias_to_other_action_proves_legacy_group_out_of_scope(action_http, tmp_path, monkeypatch):
    from packages.actions import store
    from tests.test_action_groups import api_action
    _, service = action_http
    monkeypatch.setattr(store, 'root', lambda: tmp_path / 'legacy')
    legacy = store.Store()
    legacy.put('actions', api_action('old_other'))
    legacy.put('groups', {'id': 'legacy_tools', 'name': 'Other', 'action_ids': ['old_other']})
    ref = install(service)
    other = install(service, name='other')
    service.register_action_alias('old_other', other['id'], other['revision'], other['digest'], actor=USER)
    groups = Groups(service)
    put(groups, ref)
    preview = groups.preview({'scope': 'action', 'actionRef': ref}, actor=USER)
    assert preview['excluded'] == []
    assert groups.apply(apply_body(preview), actor=USER)['status'] == 'completed'


def test_direct_canonical_mcp_advertised_schema_and_request_key_namespaces(tmp_path):
    asyncio.run(_canonical_stdio(tmp_path))


async def _canonical_stdio(tmp_path):
    from packages.actions.command_execution import product_definition
    from tests.test_authorization_mcp import harness, close, grants, result
    h = harness(tmp_path)
    groups = Groups(h.service)
    definition = product_definition('process.command')
    definition['system'] = False
    definition['inputSchema'] = {'type': 'object', 'additionalProperties': False,
        'properties': {'value': {'type': 'string'}, 'requestKey': {'type': 'string'}},
        'required': ['value', 'requestKey']}
    definition['execution']['config'] = {'cwd': str(tmp_path), 'argv': [sys.executable, '-c',
        'import json,sys;print(json.dumps(sys.argv[1:]))', '{value}', '{requestKey}']}
    ref = object_ref('action', h.service.publish_action('parameterized', definition))
    put(groups, ref)
    release = groups.publish('tools', 1, 'parameterized-build', actor=USER)['result']['release']
    h.session.adapter_config['plugin_bindings'].append({'plugin_id': 'action-group-tools', 'enabled': True, 'mode': 'standalone'})
    h.svc.auth.change_permissions(grants(), 0)
    params = StdioServerParameters(command=sys.executable,
        args=['-m', 'packages.plugins.mcp', '--view', 'standalone', '--plugin', 'action-group-tools'],
        cwd=str(Path.cwd()), env={**os.environ, 'PAN_PLUGIN_IPC': h.bridge.address,
            'PAN_AGENT_SESSION_ID': h.session.id, 'PAN_API_URL': 'http://127.0.0.1:1'})
    try:
        async with stdio_client(params) as streams, ClientSession(*streams) as client:
            await client.initialize()
            tool = next(t for t in (await client.list_tools()).tools if t.name == 'action-group-tools.echo')
            assert tool.inputSchema == groups.document(release)['capabilities'][0]['input_schema']
            assert tool.inputSchema['properties']['arguments'] == definition['inputSchema']
            business = {'value': 'literal & ; $() value', 'requestKey': 'business-key'}
            envelope = {'arguments': business, 'requestKey': 'transport-key'}
            first = result(await client.call_tool(tool.name, envelope))
            assert json.loads(first['result']['commands'][0]['stdout']) == list(business.values())
            same = result(await client.call_tool(tool.name, envelope))
            assert same['operationId'] == first['operationId']
            conflict = result(await client.call_tool(tool.name,
                {'arguments': {**business, 'value': 'changed'}, 'requestKey': 'transport-key'}))
            assert conflict['error']['code'] == 'idempotency_conflict'
            for invalid in [business, {'arguments': business},
                    {'arguments': {'value': 'missing business requestKey'}, 'requestKey': 'missing-field'}]:
                rejected = await client.call_tool(tool.name, invalid)
                assert rejected.isError is True
            receipt = h.service.operation_for_key('invoke', 'transport-key',
                actor={'type': 'agent', 'id': 'agent', 'revision': 1})
            assert receipt['operationId'] == first['operationId']
            with h.service.transaction(readonly=True) as db:
                assert db.execute("SELECT count(*) FROM final_operations WHERE scope=?",
                    (h.service._scope({'type': 'agent', 'id': 'agent'}, 'invoke'),)).fetchone()[0] == 1
    finally:
        await close(h)


def test_legacy_direct_mcp_keeps_original_flat_schema_and_parameters(tmp_path, monkeypatch):
    from packages.actions import store
    from packages.core.session import Session
    from packages.core.manifest_loader import ManifestConfig
    from packages.plugins.runtime import Runtime
    from packages.plugins.ipc import Bridge
    from tests.test_action_groups import command_action
    monkeypatch.setattr(store, 'root', lambda: tmp_path / 'actions')
    legacy = store.Store()
    action = command_action(tmp_path)
    legacy.put('actions', action)
    legacy.put('groups', {'id': 'legacy_tools', 'name': 'Legacy', 'action_ids': ['echo']})
    build = legacy.build('legacy_tools')
    owner = Session(id='ses_legacy_direct', name='Legacy direct', adapter_config={
        'plugin_bindings': [{'plugin_id': build['plugin_id'], 'enabled': True, 'mode': 'standalone'}]})
    async def exercise():
        runtime = Runtime(lambda: ManifestConfig(), lambda sid: owner if sid == owner.id else None)
        bridge = Bridge(runtime, tmp_path)
        params = StdioServerParameters(command=sys.executable,
            args=['-m', 'packages.plugins.mcp', '--view', 'standalone', '--plugin', build['plugin_id']],
            cwd=str(Path.cwd()), env={**os.environ, 'PAN_PLUGIN_IPC': bridge.address,
                'PAN_AGENT_SESSION_ID': owner.id, 'PAN_API_URL': 'http://127.0.0.1:1'})
        try:
            async with stdio_client(params) as streams, ClientSession(*streams) as client:
                await client.initialize()
                tool = (await client.list_tools()).tools[0]
                assert tool.inputSchema == action['input_schema']
                flat = await client.call_tool(tool.name, {'value': 'original-flat-argument'})
                assert flat.isError is False
                assert flat.structuredContent == {'value': 'original-flat-argument'}
                wrapped = await client.call_tool(tool.name, {'arguments': {'value': 'wrong-shape'}, 'requestKey': 'new-protocol'})
                assert wrapped.isError is True
        finally:
            await bridge.close()
    asyncio.run(exercise())
