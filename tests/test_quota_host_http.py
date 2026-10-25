"""Real owned HTTP and fake CLI; no real auth/profile/provider or Worker."""
import json
from pathlib import Path
import socket
import time

import httpx

from packages.core.codex_quota_store import CodexProfile,CodexQuotaStore
from tests import test_real_queue_http_e2e as h
from tests.test_quota_host import limits,stamp


def test_owned_host_configuration_read_projection_and_restart(tmp_path,monkeypatch):
    assert h.PORT not in (8767,8768)
    monkeypatch.setattr(h,'SERVER_LAUNCHER',Path(__file__).parent/'support/quota_host_http_server.py')
    rt=h._PanRuntime(tmp_path/'host-http');rt.data_root.mkdir()
    now=float(int(time.time()))
    profile=CodexProfile(rt.data_root/'fixture-codex','native-fixture','native-fixture','fixture-profile')
    store=CodexQuotaStore(profile,root=rt.data_root/'quota-fixture')
    store.update(limits(now),observed_at=stamp(now),received_at=stamp(now))
    before=store.path.read_bytes();evidence={'port':h.PORT,'profile':str(rt.data_root),'realProviderCalls':0,'realEffects':0}
    try:
        rt.start();evidence['serverPid']=rt.process.pid
        key=(rt.data_root/'automation/user.key').read_text(encoding='utf-8')
        token=rt.request('POST','/api/automation/login',json={'key':key})['token']
        url=rt.base_url+'/api/automation/quota-policy';headers={'Authorization':'Bearer '+token}
        with httpx.Client(timeout=10,trust_env=False) as client:
            state=client.get(url).json()
            assert state['host']=={'enabled':False,'sessionRoles':{}} and state['adapter']=='unavailable'
            assert not (rt.data_root/'host-cli.jsonl').exists()
            body={'enabled':True,'sessionRoles':{'explicit-target':'ta'},'expectedRevision':state['revision']}
            assert client.put(url+'/host',json=body).status_code==401
            for bad in ({**body,'effectsEnabled':True},{**body,'enabled':'true'},
                        {**body,'sessionRoles':{'explicit-target':'from-name'}}):
                assert client.put(url+'/host',json=bad,headers=headers).status_code==400
            assert client.put(url+'/host',json=body,headers=headers).status_code==200
            assert client.put(url+'/host',json=body,headers=headers).status_code==409
            state=client.get(url).json()
            assert client.put(url,json={'mode':'automatic','maSessionId':'fixture-ma','expectedRevision':state['revision']},headers=headers).status_code==200
            state=client.get(url).json()
            job=client.post(url+'/query-jobs',json={'kind':'once','expectedRevision':state['revision']},headers=headers)
            assert job.status_code==200,job.text
            def done():
                value=client.get(url).json()
                return value if value.get('hostObservation') else None
            state=h._wait_for(done,label='host read-only projection')
            state=h._wait_for(lambda: (v if (v:=client.get(url).json())['quota']['status']=='known' else None),label='provider quota persisted')
            assert state['adapter']=='host-readonly' and state['quota']['identityDomain']=='cc-switch-provider:codex'
            assert state['quota']['currentAccountId']=='provider-a'
            assert state['hostObservation']['native']['status']=='known'
            assert state['hostObservation']['native']['windows']['week']['usedPercent']==70
            assert state['hostObservation']['binding']=='unknown' and not state['commands']
            trace=rt.data_root/'host-cli.jsonl';lines=trace.read_text(encoding='utf-8')
            commands=[json.loads(line) for line in lines.splitlines()]
            assert all(c==['auth','status','--json'] or (c[:3]==['--app','codex','provider'] and c[3] in ('list','quota')) for c in commands)
            assert {c[4] for c in commands if c[3:4]==['quota']}=={'provider-a','provider-b'}
            body={'enabled':False,'sessionRoles':{'explicit-target':'ta'},'expectedRevision':state['revision']}
            # Wait for query bookkeeping before using its exact revision.
            def disable():
                body['expectedRevision']=client.get(url).json()['revision']
                result=client.put(url+'/host',json=body,headers=headers)
                return result if result.status_code==200 else None
            h._wait_for(disable,label='user host disable after query')
            rt.stop();rt.start();evidence['restartedServerPid']=rt.process.pid
            recovered=client.get(url).json()
            assert recovered['host']=={'enabled':False,'sessionRoles':{'explicit-target':'ta'}}
            assert client.get(rt.base_url+'/api/automation/session',headers=headers).json()['authenticated'] is True
            with httpx.Client(timeout=10,trust_env=False) as anonymous:
                assert anonymous.put(url+'/host',json=body).status_code==401
            assert client.put(url+'/host',json=body,headers=headers).status_code==409
            assert client.post(rt.base_url+'/api/automation/logout',headers=headers).json()=={'ok':True}
            assert client.put(url+'/host',json=body,headers=headers).status_code==401
            assert trace.read_text(encoding='utf-8')==lines
            assert store.path.read_bytes()==before
            evidence.update(policy=recovered,cliCommands=[json.loads(line) for line in lines.splitlines()])
            assert 'fixture-secret' not in json.dumps(evidence) and 'private-label' not in json.dumps(evidence)
    finally:
        rt.stop()
        with socket.socket() as probe:probe.bind(('127.0.0.1',h.PORT));evidence['portReleased']=True
        evidence['ownedServerStopped']=rt.process is None or rt.process.poll() is not None
        out=Path('artifacts/quota-provider');out.mkdir(exist_ok=True,parents=True)
        (out/'http-evidence.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
