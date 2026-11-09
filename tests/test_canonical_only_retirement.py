"""Canonical-only policy: refusal is independent of owner activation or history shape."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from packages.actions.store import Store
from packages.jobs import final_contracts as fc
from tests.test_action_unification_p1 import action_http, create_draft, control
from tests.test_action_definition_preview import draft

@pytest.mark.parametrize('method', ['put', 'build', 'set_enabled'])
def test_retired_store_writers_preserve_original_bytes_without_owner(tmp_path, method):
    store = Store(tmp_path)
    store.path.write_text('{"revision":7,"actions":{},"groups":{}}', encoding='utf-8')
    before = store.path.read_bytes()
    with pytest.raises(PermissionError, match='retired'):
        if method == 'put': store.put('actions', {'name': 'must not write'}, 7)
        elif method == 'build': store.build('old', 7)
        else: store.set_enabled('old', True, 7)
    assert store.path.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ['registry.json']

@pytest.mark.parametrize('entry', ['provider', 'dynamic'])
def test_legacy_executor_refuses_even_unrecognizable_history_before_effect(entry):
    from packages.actions.provider import execute
    from packages.actions.executor import execute_dynamic
    async def effect(*args, **kwargs):
        pytest.fail('Retired executor reached an effect')
    with pytest.raises(PermissionError, match='retired'):
        asyncio.run(execute({}, {}, before_effect=effect) if entry == 'provider'
                    else execute_dynamic({}, {}, {}, effect, effect))

@pytest.mark.parametrize('method,path,body', [
    ('POST', '/api/actions/actions', {'item': {}}),
    ('POST', '/api/actions/groups', {'item': {}}),
    ('PUT', '/api/actions/actions/old', {'item': {'id': 'old'}}),
    ('POST', '/api/actions/actions/old/lifecycle', {'enabled': True, 'expectedRevision': 0}),
    ('POST', '/api/actions/groups/old/build', {}),
    ('POST', '/api/action-drafts/actions/put', {'item': {}}),
    ('POST', '/api/action-drafts/groups/put', {'item': {}}),
])
def test_old_http_auth_precedes_retired_and_never_falls_back(action_http, monkeypatch, method, path, body):
    from packages.actions.api import install
    client, service = action_http
    install(SimpleNamespace(app=client.app))
    def forbidden(*args, **kwargs): pytest.fail('Retired HTTP entered a store')
    monkeypatch.setattr(Store, 'read', forbidden)
    monkeypatch.setattr(Store, 'put', forbidden)
    denied = client.request(method, path, json=body, headers={'Authorization': ''})
    assert denied.status_code == 401
    retired = client.request(method, path, json=body)
    assert retired.status_code == 410, retired.text
    assert retired.json()['detail']['code'] == 'retired'


def test_new_action_group_and_invocation_use_only_canonical_authority(action_http, monkeypatch):
    from packages.jobs.final_service import object_ref
    from packages.actions.groups import Groups
    from tests.test_action_unification_p6 import own
    from tests.test_action_unification_p3 import USER
    client, service = action_http
    own(service)
    monkeypatch.setattr(Store, 'put', lambda *a, **k: pytest.fail('canonical create fell back'))
    definition = client.post('/api/jobs/actions/definition-preview', json={'draft': draft(
        input_schema={'type': 'object', 'properties': {}, 'additionalProperties': False})}).json()['definition']
    created = create_draft(client, definition)['result']['object']
    published = control(client, created['id'], 'publish', created['revision'], created['stateRevision'], 'publish-owned')['result']['object']
    ref = object_ref('action', published)
    group = client.put('/api/action-groups/owned_tools', json={'group': {'id': 'owned_tools', 'name': 'Owned tools', 'tools': [{'alias': 'read', 'actionRef': ref}]}, 'expectedRevision': 0, 'requestKey': 'create-owned-group'})
    assert group.status_code in (200, 201), group.text
    assert group.json()['result']['group']['tools'][0]['actionRef'] == ref
    # A canonical call crosses its existing before-effect boundary and retains a receipt.
    import packages.actions.family_primitives as primitives
    calls=[]
    async def read(action, arguments, *, before_effect):
        await before_effect(); calls.append(action['execution']['path'])
        return {'content': [{'type': 'text', 'text': 'owned'}], 'isError': False}
    monkeypatch.setattr(primitives, 'canonical_api_get', read)
    result=asyncio.run(service.invoke_action(ref['id'], {'actionRef': ref, 'arguments': {}, 'requestKey': 'owned-invoke'}, actor=USER))
    assert result['result']['status'] == 'completed', result
    again=asyncio.run(service.invoke_action(ref['id'], {'actionRef': ref, 'arguments': {}, 'requestKey': 'owned-invoke'}, actor=USER))
    assert again['operationId'] == result['operationId'] and again['replayed']
    assert calls == ['/api/sessions']


def test_arbitrary_store_mutation_cannot_recreate_legacy_definitions(tmp_path):
    store = Store(tmp_path)
    store.path.write_text('{"revision":0,"actions":{},"groups":{}}', encoding='utf-8')
    before = store.path.read_bytes()
    with pytest.raises(PermissionError, match='retired'):
        store.mutate(lambda state: state['actions'].update({'old': {'enabled': True}}))
    assert store.path.read_bytes() == before


def test_unrecognizable_action_and_group_are_same_catalog_history_without_writes(action_http, tmp_path, monkeypatch):
    from packages.actions import store as module
    from packages.actions.api import install
    client, _ = action_http
    root = tmp_path / 'source'; root.mkdir()
    source = root / 'registry.json'
    source.write_text(json.dumps({'revision': 9, 'actions': {'alien': 123}, 'groups': {'old_group': {'name': 'User 名称'}}}), encoding='utf-8')
    before = source.read_bytes()
    monkeypatch.setattr(module, 'root', lambda: root)
    install(SimpleNamespace(app=client.app, sess=SimpleNamespace(list_all=lambda **kw: [])))
    response = client.get('/api/actions')
    assert response.status_code == 200, response.text
    assert response.json()['actions'][0]['id'] == 'alien'
    assert response.json()['actions'][0]['retirementStatus'] == 'Invalid'
    assert response.json()['groups'][0]['name'] == 'User 名称'
    assert response.json()['groups'][0]['retirementStatus'] == 'Retired'
    assert source.read_bytes() == before
    assert sorted(p.name for p in root.iterdir()) == ['registry.json']


def test_old_group_snapshot_never_registers_a_plugin_but_files_remain(tmp_path, monkeypatch):
    from packages.actions import store as module
    root = tmp_path / 'source'; root.mkdir()
    source = root / 'registry.json'
    source.write_text(json.dumps({'revision': 1, 'actions': {}, 'groups': {'old': {'id': 'old', 'name': 'Old', 'build': {'plugin_id': 'action-group-old', 'directory': str(root), 'version': 'old'}}}}), encoding='utf-8')
    before = source.read_bytes()
    monkeypatch.setattr(module, 'root', lambda: root)
    catalog = SimpleNamespace(plugins={}, servers={}, _add=lambda *a: pytest.fail('legacy plugin registered'))
    module.extend_catalog(catalog)
    assert catalog.plugins == {} and catalog.servers == {}
    assert source.read_bytes() == before
