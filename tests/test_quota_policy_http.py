"""Owned real HTTP only: no quota provider, account switch or model request."""
import json
from pathlib import Path
import socket

import httpx

from tests import test_real_queue_http_e2e as h


def test_quota_policy_owned_http_default_unavailable_is_unknown(tmp_path,monkeypatch):
    assert h.PORT not in (8767,8768)
    monkeypatch.setattr(h,'SERVER_LAUNCHER',Path(__file__).parent/'support/automation_http_server.py')
    rt=h._PanRuntime(tmp_path/'owned-quota-http');rt.data_root.mkdir()
    (rt.data_root/'config.json').write_text(json.dumps({'plugin_manifests':[],'scheduler':{'enabled':True}}),encoding='utf-8')
    evidence={'port':h.PORT,'profile':str(rt.data_root),'providerCalls':0,'switches':0,'restarts':0}
    try:
        rt.start();evidence['serverPid']=rt.process.pid
        key=(rt.data_root/'automation/user.key').read_text(encoding='utf-8')
        token=rt.request('POST','/api/automation/login',json={'key':key})['token']
        headers={'Authorization':'Bearer '+token};url=rt.base_url+'/api/automation/quota-policy'
        with httpx.Client(timeout=10,trust_env=False) as client:
            state=client.get(url).json();assert state['mode']=='manual' and state['adapter']=='unavailable'
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
            job=h._wait_for(done,label='persistent quota query Job terminal')
            assert job['quotaResult']=={'status':'unknown','thresholdReached':False}
            state=client.get(url).json();assert state['quota']['status']=='unknown' and not state['commands']
            assert client.put(url,json={'mode':'manual','maSessionId':'owned-ma','expectedRevision':state['revision']},headers=headers).status_code==200
            evidence.update(job=job,policy=client.get(url).json())
    finally:
        rt.stop()
        with socket.socket() as probe:
            probe.bind(('127.0.0.1',h.PORT));evidence['portReleased']=True
        evidence['ownedServerStopped']=rt.process is None or rt.process.poll() is not None
        out=Path('artifacts/quota-policy');out.mkdir(parents=True,exist_ok=True)
        (out/'http-evidence.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
