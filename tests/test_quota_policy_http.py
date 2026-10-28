"""Policy config remains HTTP-managed; the retired query Job writer is fenced."""
from types import SimpleNamespace
from packages.automation.api import install
from packages.automation.quota_policy import Store
from tests.test_automation_canonical import canonical


def test_quota_policy_owned_http_default_unavailable_is_unknown(canonical):
    h = canonical
    install(SimpleNamespace(app=h.app))
    url = '/api/automation/quota-policy'
    state = h.client.get(url).json()
    assert state['mode'] == 'manual' and state['adapter'] == 'unavailable'
    assert state['disabledAccountIds'] == []
    disabled = {'disabledAccountIds':['owned-disabled'], 'expectedRevision':state['revision']}
    assert h.client.put(url + '/disabled-accounts', json=disabled,
        headers={'Authorization':'Bearer invalid'}).status_code == 401
    assert h.client.put(url + '/disabled-accounts', json={**disabled,
        'disabledAccountIds':['duplicate', 'duplicate']}).status_code == 400
    assert h.client.put(url + '/disabled-accounts', json=disabled).status_code == 200
    assert h.client.put(url + '/disabled-accounts', json=disabled).status_code == 409
    state = h.client.get(url).json()
    response = h.client.put(url, json={'mode':'automatic', 'maSessionId':'owned-ma',
        'expectedRevision':state['revision']})
    assert response.status_code == 200, response.text
    state = h.client.get(url).json()
    before = h.automation.quota_policy.store.read()
    response = h.client.post(url + '/query-jobs', json={'kind':'once', 'expectedRevision':state['revision']})
    assert response.status_code == 409 and 'fence' in response.text
    assert h.automation.quota_policy.store.read() == before
    assert h.automation.quota_policy.adapter.__class__.__name__ == 'Adapter'
    restored = Store(h.automation.ledger.path.parent).read()
    assert restored['disabledAccountIds'] == ['owned-disabled'] and restored['mode'] == 'automatic'
    assert not h.provider.calls

# Main-side real HTTP imports retained after conflict resolution.
import json
from pathlib import Path
import socket
import httpx
from tests import test_real_queue_http_e2e as real_http

def test_quota_policy_owned_http_default_unavailable_is_unknown_real_http(tmp_path,monkeypatch):
    assert real_http.PORT not in (8767,8768)
    monkeypatch.setattr(real_http,'SERVER_LAUNCHER',Path(__file__).parent/'support/automation_http_server.py')
    rt=real_http._PanRuntime(tmp_path/'owned-quota-http');rt.data_root.mkdir()
    (rt.data_root/'config.json').write_text(json.dumps({'plugin_manifests':[],'scheduler':{'enabled':True}}),encoding='utf-8')
    evidence={'port':real_http.PORT,'profile':str(rt.data_root),'providerCalls':0,'switches':0,'restarts':0}
    try:
        rt.start();evidence['serverPid']=rt.process.pid
        key=(rt.data_root/'automation/user.key').read_text(encoding='utf-8')
        token=rt.request('POST','/api/automation/login',json={'key':key})['token']
        headers={'Authorization':'Bearer '+token};url=rt.base_url+'/api/automation/quota-policy'
        with httpx.Client(timeout=10,trust_env=False) as client:
            state=client.get(url).json();assert state['mode']=='manual' and state['adapter']=='unavailable'
            assert state['disabledAccountIds']==[] and state['accountConfigRevision']==0
            disabled_url=url+'/disabled-accounts'
            disabled={'disabledAccountIds':['disabled-account-three'],'expectedRevision':state['revision']}
            assert client.put(disabled_url,json=disabled).status_code==401
            assert client.put(disabled_url,json={**disabled,'enabled':True},headers=headers).status_code==400
            assert client.put(disabled_url,json={**disabled,'disabledAccountIds':['x','x']},headers=headers).status_code==400
            assert client.put(disabled_url,json={**disabled,'expectedRevision':True},headers=headers).status_code==400
            assert client.put(disabled_url,json=disabled,headers=headers).status_code==200
            assert client.put(disabled_url,json=disabled,headers=headers).status_code==409
            state=client.get(url).json()
            assert state['disabledAccountIds']==['disabled-account-three'] and state['accountConfigRevision']==1
            body={'mode':'automatic','maSessionId':'owned-ma','expectedRevision':state['revision']}
            assert client.put(url,json=body).status_code==401
            assert client.put(url,json={**body,'script':'denied'},headers=headers).status_code==400
            assert client.put(url,json=body,headers=headers).status_code==200
            state=client.get(url).json()
            monitor=client.post(url+'/query-jobs',json={'kind':'monitor95','expectedRevision':state['revision']},headers=headers)
            assert monitor.status_code==409  # unknown account cannot bind a monitor
            state=client.get(url).json()
            response=client.post(url+'/query-jobs',json={'kind':'once','expectedRevision':state['revision']},headers=headers)
            assert response.status_code==200,response.text
            jid=response.json()['job']['jobId']
            for method,path,body in [('PATCH','/api/jobs/'+jid,{'enabled':True}),
                                     ('POST','/api/jobs/'+jid+'/run-now',{}),
                                     ('DELETE','/api/jobs/'+jid,None)]:
                denied=client.request(method,rt.base_url+path,json=body).json()
                assert denied['ok'] is False and denied['error']['code']=='user_confirmation_required'
            def done():
                rows=rt.request('GET','/api/jobs')['jobs']
                return next((j for j in rows if j['jobId']==jid and j.get('quotaResult')),None)
            job=real_http._wait_for(done,label='persistent quota query Job terminal')
            assert job['quotaResult']=={'status':'unknown','thresholdReached':False}
            state=client.get(url).json();assert state['quota']['status']=='unknown' and not state['commands']
            assert client.put(url,json={'mode':'manual','maSessionId':'owned-ma','expectedRevision':state['revision']},headers=headers).status_code==200
            evidence.update(job=job,policy=client.get(url).json())
            # Restart only this owned server; no provider or Worker exists.
            rt.stop();rt.start();evidence['restartedServerPid']=rt.process.pid
            recovered=client.get(url).json()
            assert recovered['disabledAccountIds']==['disabled-account-three'] and recovered['mode']=='manual'
            assert client.get(rt.base_url+'/api/automation/session',headers=headers).json()['authenticated'] is True
            with httpx.Client(timeout=10,trust_env=False) as anonymous:
                assert anonymous.put(disabled_url,json={'disabledAccountIds':[],'expectedRevision':recovered['revision']}).status_code==401
            assert client.post(rt.base_url+'/api/automation/logout',headers=headers).json()=={'ok':True}
            assert client.put(disabled_url,json={'disabledAccountIds':[],'expectedRevision':recovered['revision']},headers=headers).status_code==401
            evidence['recovered']=recovered
    finally:
        rt.stop()
        with socket.socket() as probe:
            probe.bind(('127.0.0.1',real_http.PORT));evidence['portReleased']=True
        evidence['ownedServerStopped']=rt.process is None or rt.process.poll() is not None
        out=Path('artifacts/quota-policy-safety');out.mkdir(parents=True,exist_ok=True)
        (out/'http-evidence.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')

