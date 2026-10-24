"""A Windows sharing denial is storage failure, not lost authorization."""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from packages.actions import store
from packages.actions.api import install


def test_atomic_replace_retries_transient_denial_and_preserves_on_exhaustion(tmp_path,monkeypatch):
    path=tmp_path/'registry.json';path.write_text('{"old":true}',encoding='utf-8')
    replace=store.os.replace;calls=[]
    def transient(src,dst):
        calls.append(1)
        if len(calls)<3:raise PermissionError('owned sharing denial')
        return replace(src,dst)
    monkeypatch.setattr(store.os,'replace',transient)
    store.atomic(path,{'new':True})
    assert len(calls)==3 and json.loads(path.read_text(encoding='utf-8'))=={'new':True}
    def denied(*_):raise PermissionError('owned persistent denial')
    monkeypatch.setattr(store.os,'replace',denied)
    with pytest.raises(store.PersistenceError):store.atomic(path,{'mustNotPublish':True})
    assert json.loads(path.read_text(encoding='utf-8'))=={'new':True}
    assert list(tmp_path.glob('*.tmp'))==[]


def test_api_distinguishes_storage_and_user_authorization(monkeypatch):
    async def run():
        app=FastAPI()
        class Auth:
            def authorized(self, header): return header == 'Bearer test'
            def permission_allowed(self, kind, capability): return True, 0
        app.state.automation=SimpleNamespace(auth=Auth())
        install(SimpleNamespace(app=app))
        def fail(*a,**k):raise store.PersistenceError('owned disk failure')
        monkeypatch.setattr(store.Store,'put',fail)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://fixture') as client:
            headers={'Authorization':'Bearer test'}
            response=await client.post('/api/actions/actions',headers=headers,json={'item':{}})
            assert response.status_code==503
            def unauthorized(*a,**k):raise PermissionError('user capability required')
            monkeypatch.setattr(store.Store,'put',unauthorized)
            response=await client.post('/api/actions/actions',headers=headers,json={'item':{}})
            assert response.status_code==401
    asyncio.run(run())
