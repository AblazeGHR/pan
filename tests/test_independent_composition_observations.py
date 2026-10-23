"""Audit observations on the unchanged composition baseline, not product auth changes."""
from fastapi.testclient import TestClient
from packages.automation.auth import UserAuth

def test_production_composition_does_not_bind_locker_identity(tmp_path):
    from packages.web import server
    # Real existing auth validates an owned temporary key/session. Never run
    # lifespan or open the production profile. ASGI read fails before storage.
    auth=UserAuth(tmp_path/'auth')
    token=auth.login(auth.key())
    assert auth.authorized('Bearer '+token)
    assert getattr(server.app.state,'user_inbox_principal',None) is None
    assert getattr(server.app.state,'interaction_trigger_adapter',None) is None
    response=TestClient(server.app).get('/api/user-lockers',headers={'Authorization':'Bearer '+token})
    assert response.status_code==503
    assert response.json()['detail']['code']=='user_auth_unavailable'
