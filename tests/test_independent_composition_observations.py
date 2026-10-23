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


def test_unicode_answer_capacity_rejection_does_not_record_response(tmp_path):
    from fastapi import FastAPI
    from packages.core import session
    from packages.interactions.api import register
    from packages.interactions.store import UserPrincipal
    owner=session.create('audit-card',workdir=str(tmp_path))
    app=FastAPI(); register(app)
    app.state.user_inbox_principal=lambda request: UserPrincipal('user')
    client=TestClient(app)
    card=client.post('/api/interaction-cards',json={'ownerSessionId':owner.id,'recipientId':'user','requestKey':'cap',
        'card':{'title':'Question','body':'body','answerInput':{'maxLength':32000}}}).json()['card']
    path=f"/api/user-inbox/{card['id']}/submit"
    # Within the HTML/declared character limit, but exceeds finite JSON bytes.
    response=client.post(path,json={'requestId':'first','expectedRevision':0,'answer':'答'*24000})
    assert response.status_code==409 and response.json()['detail']['code']=='capacity'
    assert client.get(f"/api/user-inbox/{card['id']}").json()['card']['response'] is None
    corrected=client.post(path,json={'requestId':'corrected','expectedRevision':0,'answer':'short'})
    assert corrected.status_code==200 and corrected.json()['card']['response']['answer']=='short'
