"""Durable time-based Session messages through canonical Job/Creator/Trigger."""
import asyncio
import copy
from datetime import datetime, timedelta, timezone
import pytest
from packages.core import background_jobs as jobs, session as sess, worker
from packages.jobs.final_service import FinalService, definition
from packages.scheduler.providers import time_definition, timestamp
from tests.test_job_final_creators import config as time_config
from tests.test_session_product_closure import h, config
from tests.test_job_final_http_service import enabled, control


def point(seconds):
    return timestamp(datetime.now(timezone.utc) + timedelta(seconds=seconds))


def scheduled(h, rules, *, targets=None, text='hello', source='agent', description=''):
    operation = 'session-broadcast' if isinstance(targets, list) else 'session-message'
    args = {'sessionIds':targets} if isinstance(targets,list) else {'sessionId':targets or 'ses_a'}
    value = config(h, operation, **args, text=text, source=source, sourceSessionId='ses_source')
    value['description'] = description
    job = enabled(h, value)
    response = h.client.post('/api/event-creators', json={'definition':time_definition('original clock',time_config(rules)),
        'expectedRevision':0,'requestKey':'original-clock'})
    assert response.status_code == 201, response.text
    creator = control(h,'event-creators',response.json()['result']['object'],'enable','enable-clock').json()['result']['object']
    response = h.client.post('/api/triggers',json={'definition':{'name':'original subscription','targetJobId':job['id'],
        'creatorSelector':{'mode':'ids','ids':[creator['id']]},'eventTypes':['scheduler.time.v2']},
        'expectedRevision':0,'requestKey':'original-subscription'})
    assert response.status_code == 201,response.text
    trigger = control(h,'triggers',response.json()['result']['object'],'enable','enable-subscription').json()['result']['object']
    h.service.refresh_subscriptions()
    assert not h.service.final.get(job['id'])['runs']
    return job,creator,trigger


def fire(service, at):
    async def drive():
        service.refresh_subscriptions()
        await service.creator_runtime.tick(at=at)
        await service.tick()
    asyncio.run(drive())


def replace_clock(h, creator, rules):
    cfg=definition('event_creator',creator);cfg['config']['rules']=rules
    response=h.client.put('/api/event-creators/'+creator['id'],json={'definition':cfg,
        'expectedRevision':creator['revision'],'requestKey':'replace-clock'})
    assert response.status_code==200,response.text
    return response.json()['result']['object']


def test_once_delay_persists_and_dispatches_via_send_semantics(h):
    at=point(1);text='////by agent : ses_source | caller\nrun report'
    job,creator,_=scheduled(h,[{'ruleId':'once','type':'once','at':at}],text=text,description='T-043 once')
    assert h.client.get('/api/jobs/'+job['id']).json()['description']=='T-043 once'
    assert h.service.creators.get(creator['id'])['config']['rules'][0]['at']==at
    fire(h.service,at)
    state=h.service.final.get(job['id'])
    assert len(state['runs'])==len(state['attempts'])==len(state['effects'])==1
    assert next(iter(state['runs'].values()))['state']=='completed'
    row=sess.get('ses_a').queue_pending[0]
    assert row['text']==text and row['source']=='agent' and row['sourceSessionId']=='ses_source'
    assert not sess.get('ses_source').queue_pending
    fire(h.service,at)
    assert h.service.final.get(job['id'])==state and len(sess.get('ses_a').queue_pending)==1


def test_absolute_once_and_schedule_edit_are_persisted(h):
    old,new=point(300),point(600)
    job,creator,_=scheduled(h,[{'ruleId':'once','type':'once','at':old}])
    rule={'ruleId':'interval','type':'interval','seconds':'60','anchor':new,'advanceMode':'grid'}
    updated=replace_clock(h,creator,[rule])
    assert updated['id']==creator['id'] and updated['revision']==creator['revision']+1
    assert h.service.creators.get(creator['id'])['config']['rules']==[rule]
    cfg=definition('job',job);cfg['description']='recurring'
    response=h.client.put('/api/jobs/'+job['id'],json={'definition':cfg,'expectedRevision':job['configRevision'],'requestKey':'description'})
    assert response.status_code==200 and response.json()['result']['object']['description']=='recurring'
    fire(h.service,old);assert not h.service.final.get(job['id'])['runs']
    fire(h.service,new);assert len(h.service.final.get(job['id'])['runs'])==1


@pytest.mark.parametrize('schedule',[
    {'type':'once','delaySeconds':1,'at':'2030-01-01T00:00:00Z'},
    {'type':'interval','intervalSeconds':0},
    {'type':'weekly','weekday':7,'time':'09:00'},
    {'type':'weekly','weekday':1,'time':'25:00'},
])
def test_invalid_time_schedule_is_rejected(h,schedule):
    rule={'ruleId':'invalid',**schedule}
    response=h.client.post('/api/event-creators',json={'definition':time_definition('invalid',time_config([rule])),
        'expectedRevision':0,'requestKey':'invalid'})
    assert response.status_code in {400,422},response.text
    assert not h.service.creators.list()
    assert not h.service.list('job',actor={'type':'user','id':'local'})['items']


def interval_recovery(h,targets=None):
    first,second=point(60),point(120)
    job,creator,_=scheduled(h,[{'ruleId':'interval','type':'interval','seconds':'60','anchor':first,'advanceMode':'grid'}],targets=targets,text='tick')
    fire(h.service,first)
    original=h.service.final.get(job['id']);assert len(original['runs'])==1
    asyncio.run(h.service.close())
    cold=FinalService(h.root,h.automation.auth)
    try:
        cold.recover();fire(cold,second)
        state=cold.final.get(job['id']);assert len(state['runs'])==len(state['attempts'])==len(state['effects'])==2
        assert len(set(r['cause']['eventId'] for r in state['runs'].values()))==2
        fire(cold,second);assert cold.final.get(job['id'])==state
        for sid in targets or ['ses_a']:assert len(sess.get(sid).queue_pending)==2
        current=cold.creators.get(creator['id'])
        changed=cold.control('event_creator',creator['id'],{'operation':'disable','expectedRevision':current['revision'],
            'expectedStateRevision':current['stateRevision'],'requestKey':'stop-clock','parameters':{}},actor={'type':'user','id':'local'})
        fire(cold,point(3600));assert cold.final.get(job['id'])==state
        for sid in targets or ['ses_a']:assert len(sess.get(sid).queue_pending)==2
    finally:asyncio.run(cold.close())


def test_interval_repeats_after_restart_recovery_and_cancel_stops_future_runs(h):
    interval_recovery(h)


def test_weekly_schedule_accepts_weekday_and_local_time(h):
    rule={'ruleId':'weekly','type':'weekly','weekday':2,'time':'09:30:00','timezone':'UTC','dstPolicy':'gap-skip-fold-first'}
    _,creator,_=scheduled(h,[rule])
    saved=h.service.creators.get(creator['id'])
    assert saved['config']['rules']==[rule]
    assert saved['cursor']['rules']['weekly']['nextFireAt']


def test_stale_running_job_is_requeued_after_service_restart(h,monkeypatch):
    # A lost response is queried under the original queue identity. A stale
    # running flag alone no longer authorizes a second message invocation.
    from tests.test_session_product_closure import test_lost_worker_ack_reconciles_original_queue_after_cold_reopen
    test_lost_worker_ack_reconciles_original_queue_after_cold_reopen(h,monkeypatch,'session-message')


def test_message_job_never_enters_process_runner_retry_path(h):
    at=point(60);job,_,_=scheduled(h,[{'ruleId':'once','type':'once','at':at}])
    fire(h.service,at);state=h.service.final.get(job['id']);rid=next(iter(state['runs']))
    response=h.client.post('/api/job-runs/'+rid+'/operations',json={'operation':'retry','requestKey':'no-process-retry',
        'expectedStateRevision':state['runs'][rid]['stateRevision']})
    assert response.status_code==409,response.text
    assert h.service.final.get(job['id'])==state and len(sess.get('ses_a').queue_pending)==1


def test_scheduled_broadcast_deduplicates_and_isolates_target_failures(h,monkeypatch):
    at=point(60);calls=[];original=worker.send_session
    async def send(sid,text,**kwargs):
        calls.append((sid,text,kwargs))
        if sid=='ses_b':return {'status':'error','result':'target unavailable'}
        return await original(sid,text,**kwargs)
    monkeypatch.setattr(worker,'send_session',send)
    job,_,_=scheduled(h,[{'ruleId':'once','type':'once','at':at}],targets=['ses_a','ses_b','ses_a','ses_new'],text='scheduled fan-out')
    fire(h.service,at);state=h.service.final.get(job['id'])
    assert len(state['runs'])==len(state['effects'])==1
    receipt=state['effects'][0]['receipt'];assert receipt['outcome']=='partial'
    rows=receipt['receipt']['targets']
    assert [r['effectKey']['targetId'] for r in rows]==['ses_a','ses_b','ses_new']
    assert [r['outcome'] for r in rows]==['succeeded','rejected','succeeded']
    assert [c[0] for c in calls]==['ses_a','ses_b','ses_new']
    assert all(c[2]['source']=='agent' and c[2]['source_session_id']=='ses_source' for c in calls)
    assert len(sess.get('ses_a').queue_pending)==len(sess.get('ses_new').queue_pending)==1
    fire(h.service,point(3600));assert h.service.final.get(job['id'])==state
    assert len(calls)==3


def test_scheduled_broadcast_interval_recovery_and_no_burst(h):
    interval_recovery(h,['ses_a','ses_b'])


def test_scheduled_broadcast_edit_and_cancel_prevent_future_send(h):
    old,new=point(60),point(120)
    job,creator,_=scheduled(h,[{'ruleId':'once','type':'once','at':old}],targets=['ses_a','ses_b'])
    cfg=definition('job',job);cfg['steps'][0]['arguments']['sessionIds']['value']=['ses_new','ses_new','ses_a']
    response=h.client.put('/api/jobs/'+job['id'],json={'definition':cfg,'expectedRevision':job['configRevision'],'requestKey':'targets'})
    assert response.status_code==200,response.text
    updated=replace_clock(h,creator,[{'ruleId':'replacement','type':'once','at':new}])
    assert h.service.get('job',job['id'],actor={'type':'user','id':'local'})['steps'][0]['arguments']['sessionIds']['value']==['ses_new','ses_new','ses_a']
    response=control(h,'event-creators',updated,'disable','cancel-future')
    assert response.status_code==200,response.text
    fire(h.service,point(3600));assert not h.service.final.get(job['id'])['runs']
    assert all(not sess.get(sid).queue_pending for sid in ['ses_a','ses_b','ses_new'])


def _session(tmp_path,sid='ses_target'):
    result=sess.Session(id=sid,name=sid,workdir=str(tmp_path))
    sess._cache[sid]=result
    return result


# ── name / description 规范（所有 kind 统一，2026-09-25 用户拍板）──


def test_message_job_default_name_min_vacant(h):
    def post(h,key,**fields):
        value=config(h,'session-message',sessionId='ses_a',text='hi');value.pop('name');value.update(fields)
        return h.client.post('/api/jobs',json={'definition':value,'expectedRevision':0,'requestKey':key})
    first=post(h,'first');assert first.status_code==201,first.text
    second=post(h,'explicit',name='  周报  ');assert second.status_code==201,second.text
    third=post(h,'third');assert third.status_code==201,third.text
    assert first.json()['result']['object']['name']=='job-1'
    assert second.json()['result']['object']['name']=='周报'
    assert third.json()['result']['object']['name']=='job-2'
    assert first.json()['result']['object']['description']==''
