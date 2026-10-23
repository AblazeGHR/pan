"""Production routes with an injected auth boundary; never logs into a provider."""
from fastapi import FastAPI
from fastapi.testclient import TestClient
from packages.core import session
from packages.interactions.api import register, get_store
from packages.interactions.store import UserPrincipal
from packages.interactions.contracts import validate


def setup(tmp_path):
    owner=session.create('interaction-owner',workdir=str(tmp_path))
    app=FastAPI()
    register(app)
    return app,TestClient(app),owner


def make(client,owner,**card):
    result=client.post('/api/interaction-cards',json={'ownerSessionId':owner.id,'recipientId':'user',
                       'requestKey':'key','card':{'title':'Question','body':'text',**card}})
    assert result.status_code==200,result.text
    return result.json()['card']


def test_auth_fail_closed_user_isolation_and_body_spoofing(tmp_path):
    app,client,owner=setup(tmp_path)
    card=make(client,owner,answerInput={})
    assert client.get('/api/user-inbox').status_code==503
    assert client.post(f"/api/user-inbox/{card['id']}/respond",json={}).status_code==503
    app.state.user_inbox_principal=lambda request: UserPrincipal('user') if request.headers.get('authorization')=='Bearer synthetic' else None
    assert client.get('/api/user-inbox').status_code==401
    headers={'Authorization':'Bearer synthetic'}
    assert len(client.get('/api/user-inbox',headers=headers).json()['cards'])==1
    payload={'requestId':'response','expectedRevision':0,'answer':'reply'}
    spoof={**payload,'userId':'user','triggerRef':{'id':'forged'}}
    assert client.post(f"/api/user-inbox/{card['id']}/respond",headers=headers,json=spoof).status_code==422
    result=client.post(f"/api/user-inbox/{card['id']}/respond",headers=headers,json=payload)
    assert result.status_code==200 and result.json()['card']['response']['userId']=='user'
    assert client.post(f"/api/user-inbox/{card['id']}/respond",headers=headers,json=payload).status_code==200
    assert client.post(f"/api/user-inbox/{card['id']}/respond",headers=headers,json={**payload,'answer':'changed'}).status_code==409
    app.state.user_inbox_principal=lambda request:UserPrincipal('someone-else')
    assert client.get(f"/api/user-inbox/{card['id']}",headers=headers).status_code==404
    assert client.get('/api/user-inbox',headers=headers).json()['cards']==[]
    view=client.get(f"/api/interaction-cards/{card['id']}",params={'ownerSessionId':owner.id}).json()['card']
    validate('Card',view)
    assert view['response']['answer']=='reply'


def test_trigger_unavailable_is_not_fake_execution_success(tmp_path):
    app,client,owner=setup(tmp_path)
    app.state.user_inbox_principal=lambda request:UserPrincipal('user')
    request={'ownerSessionId':owner.id,'recipientId':'user','requestKey':'legacy',
             'card':{'title':'Legacy','body':'text','choices':[{'id':'go','label':'Go','triggerRef':{'id':'existing-trigger'}}]}}
    assert client.post('/api/interaction-cards',json=request).status_code==409
    card=get_store().create(request)  # Historical unbound row remains readable.
    result=client.post(f"/api/user-inbox/{card['id']}/respond",json={'requestId':'response','expectedRevision':0,'choiceId':'go'})
    assert result.status_code==409 and result.json()['detail']['code']=='incomplete'
    assert client.get(f"/api/user-inbox/{card['id']}").json()['card']['status']=='open'
    assert client.post(f"/api/user-inbox/{card['id']}/reject",json={'requestId':'reject','expectedRevision':0}).status_code==200
    schema=client.get('/api/interaction-cards/schema').json()
    assert schema['$id']=='pan:interaction-card:v1'


def test_mcp_producer_permissions_and_no_user_response_tool(monkeypatch):
    from packages.mcp import server
    calls=[]
    caller={'id':'owner','managed':[],'panAccess':{'restrictToManaged':True}}
    monkeypatch.setattr(server,'_require_caller',lambda:(caller,None))
    monkeypatch.setattr(server,'_api',lambda *args:calls.append(args) or {})
    assert server.interaction_card('respond')['error']
    denied=server.interaction_card('create',owner_session_id='unmanaged',card={'title':'x','body':''})
    assert denied['error']['code']=='permission_denied' and not calls
    server.interaction_card('create',recipient_id='user',request_key='key',card={'title':'x','body':''})
    assert calls[0][2]['ownerSessionId']=='owner'
    server.interaction_card('get',card_id='../id')
    assert '%2F' in calls[-1][1]

def test_user_api_cursor_and_trigger_policy_failure_are_explicit(tmp_path):
    app,client,owner=setup(tmp_path)
    app.state.user_inbox_principal=lambda request:UserPrincipal('user')
    first=make(client,owner)
    second_payload={'ownerSessionId':owner.id,'recipientId':'user','requestKey':'second',
                    'card':{'title':'second','body':'','choices':[{'id':'go','label':'Go','triggerRef':{'id':'blocked'}}]}}
    assert client.post('/api/interaction-cards',json=second_payload).status_code==409
    second=get_store().create(second_payload)
    page=client.get('/api/user-inbox',params={'limit':1}).json()
    assert page['nextAfter']==first['id']
    assert client.get('/api/user-inbox',params={'after':page['nextAfter']}).json()['cards'][0]['id']==second['id']
    class Denied:
        async def authorize(self,event):
            return False
        async def accept(self,event):
            raise AssertionError('must never submit an unauthorized Trigger')
    app.state.interaction_trigger_adapter=Denied()
    done=client.post(f"/api/user-inbox/{second['id']}/respond",json={'requestId':'once','expectedRevision':0,'choiceId':'go'})
    assert done.status_code==409 and done.json()['detail']['code']=='incomplete'
    assert client.get(f"/api/user-inbox/{second['id']}").json()['card']['response'] is None
