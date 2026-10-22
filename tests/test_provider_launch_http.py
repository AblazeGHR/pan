"""Real HTTP spawn/restart with owned config-reading app-server and provider."""
import json
from pathlib import Path
import socket

from tests import test_real_queue_http_e2e as h


def test_http_spawn_restart_fixed_config_and_private_cleanup(tmp_path,monkeypatch):
    monkeypatch.setattr(h,'SERVER_LAUNCHER',Path(__file__).parent/'support/provider_launch_http_server.py')
    rt=h._PanRuntime(tmp_path/'launch-http');evidence={'port':h.PORT,'realProviderCalls':0}
    try:
        rt.start();evidence['serverPid']=rt.process.pid
        key=(rt.data_root/'automation/user.key').read_text(encoding='utf-8')
        token=rt.request('POST','/api/automation/login',json={'key':key})['token']
        headers={'Authorization':'Bearer '+token}
        state=rt.request('GET','/api/automation/quota-policy')
        rt.request('PUT','/api/automation/quota-policy/host',json={'enabled':True,'sessionRoles':{},'expectedRevision':state['revision']},headers=headers)
        s=rt.request('POST','/api/sessions',json={'name':'owned-fixed-launch','adapter':'codex','model':'owned-model','outputMode':'stream'})
        sid=s.get('id') or s['session']['id']
        result=rt.request('POST','/api/spawn',json={'sessionId':sid})
        def bindings():
            body=json.loads((rt.data_root/'sessions'/f'{sid}.json').read_text(encoding='utf-8'))
            return [op['providerLaunchBinding'] for op in body['execution_operations'].values() if 'providerLaunchBinding' in op]
        first=h._wait_for(lambda:bindings(),label='first durable fixed launch')[0]
        assert first['providerId']=='provider-a'
        (rt.data_root/'selected-provider.json').write_text(json.dumps('provider-b'),encoding='utf-8')
        # Explicit test lifecycle operation, never a policy automatic restart.
        rt.request('POST',f'/api/worker/{first["workerId"]}/restart')
        rows=h._wait_for(lambda: (v if len(v:=bindings())==2 else None),label='restart durable launch')
        second=next(r for r in rows if r['launchId']!=first['launchId'])
        assert second['providerId']=='provider-b' and second['generation']>first['generation']
        assert second['processIdentity']!=first['processIdentity'] and second['nativeProcessIdentity']!=first['nativeProcessIdentity']
        hits=[json.loads(s) for s in (rt.data_root/'provider-hits.jsonl').read_text(encoding='utf-8').splitlines()]
        assert hits==['/provider-a','/provider-b']
        rt.request('POST',f'/api/kill/{first["workerId"]}')
        h._wait_for(lambda:not list((rt.data_root/'automation/private-launches').glob('*/config.toml')),label='private launch cleanup')
        evidence.update(bindings=rows,providerHits=hits,privateConfigsRemaining=[])
    finally:
        provider_port=json.loads((rt.data_root/'provider-port.json').read_text(encoding='utf-8')) if (rt.data_root/'provider-port.json').exists() else None
        rt.stop()
        for port in filter(None,[h.PORT,provider_port]):
            with socket.socket() as probe:probe.bind(('127.0.0.1',port))
        evidence.update(portReleased=True,providerPortReleased=True,ownedServerStopped=rt.process is None or rt.process.poll() is not None)
        out=Path('artifacts/provider-launch');out.mkdir(exist_ok=True,parents=True)
        (out/'http-evidence.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
