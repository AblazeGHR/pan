"""Owned Node -> durable Event Store -> query -> Pause, replay and restart HTTP."""
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3

import httpx
import psutil

from tests import test_real_queue_http_e2e as h
from tests.test_plugin_events import manifest, publish, records
from tests.test_session_query import query_action, leaf
from tests.test_automation_core import trigger


def test_actual_running_event_persists_before_query_pause(tmp_path,monkeypatch):
    from tests.test_automation_http import run_automation_http
    run_automation_http(tmp_path,monkeypatch,selector_mode='query',output=Path('artifacts/event-store/core-running'))


def test_node_durable_event_http_replay_restart_and_uncertain(tmp_path,monkeypatch):
    assert h.PORT not in {8767,8768}
    monkeypatch.setattr(h,'SERVER_LAUNCHER',Path(__file__).parent/'support/automation_http_server.py')
    rt=h._PanRuntime(tmp_path/'owned-store-profile');rt.data_root.mkdir()
    m=manifest();mf=tmp_path/'plugins.json'
    mf.write_text(json.dumps({'mcp_servers':[{k:v for k,v in asdict(s).items() if v is not None} for s in m.mcp_servers],'plugins':m.plugins}),encoding='utf-8')
    (rt.data_root/'config.json').write_text(json.dumps({'plugin_manifests':[str(mf)],'scheduler':{'enabled':False}}),encoding='utf-8')
    evidence={'port':h.PORT,'root':str(rt.data_root),'processes':[]}
    def capture():
        if rt.process and rt.process.poll() is None:
            for p in [psutil.Process(rt.process.pid),*psutil.Process(rt.process.pid).children(recursive=True)]:
                evidence['processes'].append(dict(pid=p.pid,created=p.create_time(),command=p.cmdline()))
    def stopped():
        return all(not psutil.pid_exists(p['pid']) or psutil.Process(p['pid']).create_time()!=p['created'] for p in evidence['processes'])
    def view():return rt.request('GET','/api/automation')
    def events():return rt.request('GET','/api/automation/events')
    def login():
        token=rt.request('POST','/api/automation/login',json={'key':(rt.data_root/'automation/user.key').read_text(encoding='utf-8')})['token']
        return {'Authorization':'Bearer '+token}
    def published_count():return len([r for r in records(rt.data_root,'producer') if r['kind']=='publication'])
    try:
        rt.start()
        def create(name):return rt.request('POST','/api/sessions',json={'name':name,'adapter':'cbc','model':'deepseek-v4-flash','mcpServers':[]})
        target,control,owner=[create(n) for n in ('durable-target','durable-control','durable-owner')]
        for s in (target,control):assert 'error' not in h._spawn(rt,s['id'])
        pending=rt.request('POST',f"/api/sessions/{target['id']}/queue",json={'text':'durable-blocked','locked':True,'clientMessageId':'durable'})['item']
        a=query_action(leaf('name','eq','durable-target'))
        rt.request('POST','/api/actions/actions',json={'item':a})
        rt.request('POST','/api/automation/triggers',json={'item':{**trigger(),'eventTypes':['producer.ping']},'expectedRevision':0},headers=login())
        rt.request('PATCH',f"/api/sessions/{owner['id']}",json={'pluginBindings':[{'plugin_id':'producer','enabled':True,'mode':'gateway'}]})
        h._wait_for(lambda:any(d['type']=='producer.ping' and d['available'] for d in view()['definitions']),label='Node ready')
        capture()
        publish(rt.data_root,type='producer.ping',payload={'value':1},event_id='durable-node')
        h._wait_for(lambda:any(r['status']=='succeeded' for r in view()['runs'].values()),label='durable Event -> query -> Pause')
        h._wait_for(lambda:any(r['event']['id']=='durable-node' and r['status']=='delivered' for r in events()['events']),label='durable matching acknowledged')
        row=next(iter(view()['runs'].values()))
        assert row['targetIds']==[target['id']] and row['attemptCount']==1 and row['attempts'][0]['status']=='succeeded'
        rt.request('PATCH',f"/api/sessions/{target['id']}/queue/{pending['queueItemId']}/lock",json={'locked':False})
        h._enqueue(rt,control['id'],'durable-control-delivered','control')
        h._wait_for(lambda:h._received(rt,text='durable-control-delivered'),label='unselected real fakeCLI consumption')
        assert rt.request('GET',f"/api/sessions/{target['id']}/queue")['queuePaused'] and not h._received(rt,text='durable-blocked')
        with sqlite3.connect('file:'+str(rt.data_root/'automation/events.sqlite3')+'?mode=ro',uri=True) as db:
            persisted=db.execute('SELECT id,done,deliveries FROM events WHERE id=?',('durable-node',)).fetchone()
            assert persisted[1]==1
        receipt=[r for r in records(rt.data_root,'producer') if r['kind']=='publication'][-1]
        assert receipt['result']['persisted'] is True
        denied=httpx.post(rt.base_url+'/api/automation/events/replay',json={'eventIds':['durable-node']},trust_env=False)
        assert denied.status_code==401
        auth=login()
        rt.request('POST','/api/automation/events/replay',json={'eventIds':['durable-node']},headers=auth)
        h._wait_for(lambda:next(r for r in events()['events'] if r['event']['id']=='durable-node')['deliveryCount']>=2,label='explicit bounded replay')
        assert len(view()['runs'])==1
        original_attempt=row['activeAttemptId']
        capture();rt.stop();h._wait_for(stopped,label='owned first shutdown')
        rt.start();capture()
        assert len(view()['runs'])==1 and next(iter(view()['runs'].values()))['activeAttemptId']==original_attempt
        count=published_count()
        publish(rt.data_root,type='producer.ping',payload={'value':1},event_id='durable-node')
        h._wait_for(lambda:published_count()>count,label='duplicate durable receipt after restart')
        duplicate=[r for r in records(rt.data_root,'producer') if r['kind']=='publication'][-1]
        assert duplicate['result']['duplicate'] is True and len(view()['runs'])==1
        # Own offline legacy interruption fixture, not a real-user data migration.
        capture();rt.stop();h._wait_for(stopped,label='owned second shutdown')
        file=rt.data_root/'automation/registry.json';state=json.loads(file.read_text(encoding='utf-8'))
        state['runs'][row['id']]['status']='running'
        state['runs'][row['id']]['attempts'][-1]['status']='running'
        file.write_text(json.dumps(state),encoding='utf-8')
        rt.start();capture()
        recovered=next(iter(view()['runs'].values()))
        assert recovered['status']=='uncertain' and recovered['attempts'][0]['status']=='uncertain'
        refused=httpx.post(rt.base_url+'/api/automation/events/replay',json={'eventIds':['durable-node']},headers=login(),trust_env=False)
        assert refused.status_code==409 and len(view()['runs'])==1
        assert events()['storage']['retentionSeconds']==7*24*60*60
        evidence.update(state=view(),eventPage=events(),receipt=receipt,duplicateReceipt=duplicate,persistedRow=persisted,queue=rt.request('GET',f"/api/sessions/{target['id']}/queue"))
    finally:
        capture();rt.stop();h._wait_for(stopped,label='final owned cleanup')
        evidence.update(ownedProcessesRemaining=[],portReleased=h._port_is_free(h.PORT))
        out=Path('artifacts/event-store');out.mkdir(parents=True,exist_ok=True)
        (out/'http-evidence.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
        (out/'http-server.log').write_text((rt.data_root/'pan.log').read_text(encoding='utf-8',errors='replace'),encoding='utf-8')
