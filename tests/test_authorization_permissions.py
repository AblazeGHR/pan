import json

import pytest
import asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from packages.automation.auth import UserAuth
from packages.automation.api import install
from packages.automation.service import Service


def full_permissions(value=True):
    result = {kind: {name: value for name in names}
              for kind, names in UserAuth.PERMISSION_SCHEMA.items()}
    result['action']['manageSystemActions'] = value
    return result


def test_profile_permissions_are_persistent_fail_closed_and_cas(tmp_path):
    auth = UserAuth(tmp_path)
    first = auth.permissions_status()
    assert first['revision'] == 0
    assert all(not enabled for item in first['permissions'].values() for enabled in item.values())

    saved = auth.change_permissions(full_permissions(), 0)
    assert saved['revision'] == 1
    assert auth.permission_allowed('job', 'execute') == (True, 1)
    assert json.loads((tmp_path / 'authorization.json').read_text())['revision'] == 1

    with pytest.raises(RuntimeError, match='revision conflict'):
        auth.change_permissions(full_permissions(False), 0)


def test_permission_schema_rejects_unknown_capability_without_logging_secret(tmp_path):
    auth = UserAuth(tmp_path)
    value = full_permissions()
    value['job']['secret'] = True
    with pytest.raises(ValueError):
        auth.change_permissions(value, 0)
    assert not (tmp_path / 'authorization.json').exists()


def test_permission_http_cas_and_denial(tmp_path):
    async def run():
        svc = Service(tmp_path, type('Worker', (), {'workers': {}})(), None)
        app = FastAPI(); app.state.automation = svc
        install(type('Server', (), {'app': app})())
        token = svc.auth.login(svc.auth.key())
        headers = {'Authorization': 'Bearer ' + token}
        async with AsyncClient(transport=ASGITransport(app), base_url='http://test') as client:
            denied = await client.get('/api/automation', headers=headers)
            assert denied.status_code == 200
            assert (await client.get('/api/automation')).status_code == 401
            state = (await client.get('/api/automation/authorization', headers=headers)).json()
            response = await client.put('/api/automation/authorization/permissions', headers=headers,
                                         json={'permissions': full_permissions(), 'expectedRevision': state['authorizationRevision']})
            assert response.status_code == 200 and response.json()['revision'] == 1
    asyncio.run(run())



def test_permission_cas_concurrency_and_storage_rollback(tmp_path,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    auth=UserAuth(tmp_path)
    def save(enabled):
        try:
            auth.change_permissions(full_permissions(enabled),0)
            return 'saved'
        except RuntimeError:return 'conflict'
    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(save,[True,False]))==['conflict','saved']
    before=auth.permissions_status()
    monkeypatch.setattr('packages.automation.auth.os.replace',lambda *_:(_ for _ in ()).throw(OSError('synthetic failure')))
    with pytest.raises(OSError,match='storage failed'):
        auth.change_permissions(full_permissions(),1)
    assert auth.permissions_status()==before
    assert not list(tmp_path.glob('authorization.json.tmp-*'))


def test_template_schema_has_no_lifecycle_or_execute_and_malformed_state_fails_closed(tmp_path):
    auth=UserAuth(tmp_path)
    for field in ('lifecycle','execute','enabled'):
        grants=full_permissions();grants['jobTemplate'][field]=True
        with pytest.raises(ValueError):auth.change_permissions(grants,0)
    auth.permissions_path.write_text('{invalid',encoding='utf-8')
    with pytest.raises(OSError,match='invalid or unreadable'):auth.permission_allowed('job','execute')
