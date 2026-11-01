"""Owned native process and original public terminal-notice chain."""
import asyncio
import copy
import json
from pathlib import Path
from packages.core import session
from packages.jobs.final_service import FinalService, object_ref
from packages.jobs.independent_executor import ExecutionStore, effect_id
from tests.test_job_automatic_recovery import job_definition, create, drive_until
from tests.test_job_hidden_production import wait_value


def native_process_notice(h, monkeypatch, *, external=False, scheduled=False):
    from packages.web import server
    target = session.Session(id='ses_target', name='Owned notice target', workdir=str(h.tmp), queue_paused=True)
    session.save(target)
    grants = h.auth._default_permissions()
    grants['job'].update({k: True for k in grants['job']})
    grants['action'].update({k: True for k in grants['action']})
    h.auth.change_permissions(grants, 0)
    creator = {'type': 'agent', 'id': 'ses_creator', 'revision': 1}
    cwd = h.tmp / 'outside Unicode worktree' if external else h.tmp
    cwd.mkdir(exist_ok=True)
    gate, counter = h.tmp / 'release', h.tmp / 'native-once'
    code = ("import os,json,time,sys;from pathlib import Path; "
        f"Path({str(counter)!r}).write_text('once'); "
        "print('JOB_EXTERNAL_CWD_OK',flush=True);print('NATIVE_STDIN_EOF='+str(sys.stdin.read()==''),flush=True);print(json.dumps(os.getcwd()),flush=True)\n"
        f"while not Path({str(gate)!r}).exists(): time.sleep(.02)")
    cfg = job_definition(h, {'automatic': False}, code=code)
    cfg['steps'][0]['arguments']['cwd']['value'] = str(cwd)
    action = next(a for a in h.service.actions(actor={'type':'user','id':'local'})['items'] if a['id']=='product_process_logs_v1')
    cfg['steps'][0]['actionRef'] = object_ref('action', action)
    cfg['notification'] = {'on':['completed'], 'recipients':[
        {'recipientId':'target', 'channelId':'session', 'targetRef':target.id}]}
    if scheduled:
        from tests.test_job_final_http_service import enabled, control
        from tests.test_job_final_creators import config as time_config
        from tests.test_retirement_rev16_behavior import instant
        from packages.scheduler.providers import time_definition
        job = enabled(h, cfg)
        jid = job['id']
        at = instant(30)
        response = h.client.post('/api/event-creators', json={'definition':time_definition('owned native timer',
            time_config([{'ruleId':'native','type':'once','at':at}])), 'expectedRevision':0,'requestKey':'native-clock'})
        assert response.status_code == 201, response.text
        source = control(h,'event-creators',response.json()['result']['object'],'enable','native-clock-enable').json()['result']['object']
        response = h.client.post('/api/triggers',json={'definition':{'name':'native subscription','targetJobId':jid,
            'creatorSelector':{'mode':'ids','ids':[source['id']]},'eventTypes':['scheduler.time.v2']},
            'expectedRevision':0,'requestKey':'native-trigger'})
        assert response.status_code == 201, response.text
        assert control(h,'triggers',response.json()['result']['object'],'enable','native-trigger-enable').status_code==200
        h.service.refresh_subscriptions()
        asyncio.run(h.service.creator_runtime.tick(at=at))
        asyncio.run(h.service.tick())
        rid, = h.service.final.get(jid)['runs']
        creator = {'type':'user','id':'local'}
    else:
        jid, rid = create(h, cfg, actor=creator)
        asyncio.run(h.service.dispatch_once())
    try:
        wait_value(lambda: counter.exists(), bool)
        pending = h.service.final.store.get(jid)['finalRuntime']
        assert pending['runs'][rid]['state'] == 'running'
        aid, = pending['attempts']
        assert pending['attempts'][aid]['state'] == 'running' and not pending['effects'] and not pending['outbox']
        admission, = pending['effectAdmissions'].values()
        store = ExecutionStore(h.root / 'executions')
        doc = wait_value(lambda:store.get(effect_id(admission['effectKey'])), lambda d: bool(d.get('evidence',{}).get('processIdentity')))
        assert doc['result'] is None and doc['jobId'] == jid and doc['attempt']['attemptId'] == aid
        assert doc['supervisorIdentity']['pid'] and doc['supervisorIdentity']['birth']
        assert doc['evidence']['processIdentity']['pid'] != doc['supervisorIdentity']['pid']
        assert doc['frozenPlan']['arguments']['cwd'] == str(cwd)
    finally:
        gate.write_text('release')
    asyncio.run(drive_until(h,jid,rid,'completed'))
    state = h.service.final.get(jid)
    assert len(state['runs']) == len(state['attempts']) == len(state['effects']) == 1
    log = h.client.get(f'/api/job-runs/{rid}/attempts/{aid}/log')
    assert log.status_code == 200, log.text
    assert 'JOB_EXTERNAL_CWD_OK' in log.json()['text'] and json.dumps(str(cwd)) in log.json()['text']
    assert 'NATIVE_STDIN_EOF=True' in log.json()['text']
    rows = h.service.final.history(jid)['outbox']
    assert len(rows) == 2 and {r['channel'] for r in rows} == {'ws','session'}
    assert all(r['summary']['exitCode']==0 and r['summary']['creator']==creator for r in rows)
    original_broadcast = server.broadcast
    snapshots = []
    async def broadcast(data):
        snapshots.append(copy.deepcopy(data))
        await original_broadcast(data)
    monkeypatch.setattr(server,'ws_clients',set())
    monkeypatch.setattr(server,'agent_clients',set())
    monkeypatch.setattr(server,'broadcast',broadcast)
    h.service.outbox_sender = server._send_final_notice
    asyncio.run(h.service.deliver_outbox())
    delivered = h.service.final.history(jid)
    assert all(r['status']=='acked' for r in delivered['outbox'])
    assert len(snapshots)==1 and snapshots[0]['type']=='job.updated' and snapshots[0]['jobId']==jid
    queued = session.get(target.id).queue_pending
    assert len(queued)==1 and queued[0]['jobId']==jid and queued[0]['noticeKind']=='job_terminal'
    summary = json.loads(queued[0]['result'])
    assert summary['runId']==rid and summary['exitCode']==0
    if creator['type']=='agent':
        assert queued[0]['creatorSessionId']=='ses_creator' and summary['creatorSessionId']=='ses_creator'
    cold = FinalService(h.root,h.auth,owner=h.service.owner)
    cold.outbox_sender=server._send_final_notice
    try:
        asyncio.run(cold.recover_external_receipts())
        asyncio.run(cold.deliver_outbox())
        asyncio.run(h.service.deliver_outbox())
        assert cold.final.history(jid)==delivered and len(snapshots)==1
        assert len(session.get(target.id).queue_pending)==1 and counter.read_text()=='once'
        items = h.client.get('/api/jobs').json()['items']
        assert [j['id'] for j in items]==[jid]
        assert all(event['jobId']==jid and event['jobId']!=doc['effectId'] for event in snapshots)
    finally:
        asyncio.run(cold.close())
    return {'jobId':jid,'runId':rid,'attemptId':aid,'native':doc,'history':delivered,'snapshots':snapshots}
