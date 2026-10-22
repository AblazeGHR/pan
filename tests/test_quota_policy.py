import asyncio
import copy
import json
from pathlib import Path
import time

import pytest

from packages.automation.quota_policy import Runtime, Adapter, Unavailable, normalize, rank
from packages.core import background_jobs as jobs
from packages.core.runtime_issues import map_runtime_issue
from tests.test_automation_core import async_test


class Fake(Adapter):
    def __init__(self,now):
        self.now=now;self.effects=[];self.fail=False;self.unknown=False;self.currentAccountId="a";self.runtimeEpoch="owned-epoch"
        self.accounts=[self.row('a',10,500),self.row('b',30,100),self.row('c',20,100)]
        self.workers=[{'sessionId':'ta','workerId':'w','generation':2,'role':'ta','status':'running','adapter':'codex'},
                      {'sessionId':'idle','workerId':'wi','generation':1,'role':'ta','status':'idle','adapter':'codex'},
                      {'sessionId':'ma','workerId':'wm','generation':4,'role':'ma','status':'running','adapter':'codex'},
                      {'sessionId':'cbc','workerId':'wc','generation':1,'role':'ta','status':'running','adapter':'cbc'}]
        for index,w in enumerate(self.workers):
            w.update(accountId='a',alive=True,processIdentity={'pid':100+index,'birthToken':'birth-'+str(index)},
                     task={'taskId':'task-'+w['sessionId'],'executionSeq':1,'state':'unfinished'} if w['status']=='running' else None)
    def row(self,aid,used,week):
        return {'id':aid,'fiveHour':{'usedPercent':used,'resetsAt':self.now+200},
                'week':{'usedPercent':used,'resetsAt':self.now+week}}
    async def query(self):
        if self.fail:raise ValueError('secret upstream error')
        return {'status':'known','observedAt':self.now,'currentAccountId':self.currentAccountId,'accounts':copy.deepcopy(self.accounts),'credential':'must not persist'}
    async def inventory(self):return {'runtimeEpoch':self.runtimeEpoch,'observedAt':self.now,'currentAccountId':self.currentAccountId,'workers':copy.deepcopy(self.workers)}
    async def perform(self,command,key):
        self.effects.append(command)
        if self.unknown:return {'key':key,'status':'unknown'}
        args=command['arguments'];receipt={'key':key,'status':'confirmed','jobId':'owned-ma-wake'}
        if command['operation']=='switch_account':self.currentAccountId=args['targetAccountId']
        if command['operation'] in ('restart_worker','restart_ma'):
            target=args['worker'];w=next(w for w in self.workers if w['sessionId']==target['sessionId'])
            w['generation']+=1;w['accountId']=args['expectedAccountId']
            w['processIdentity']={'pid':w['processIdentity']['pid']+1000,'birthToken':w['processIdentity']['birthToken']+'-next'}
            receipt['successor']={**copy.deepcopy(w),'runtimeEpoch':self.runtimeEpoch}
        return receipt


def issue(kind='codex_capacity',seq=1,session='ta',worker='w',generation=2):
    return {'issueId':'i'+str(seq),'classification':kind,'confidence':'structured',
            'correlation':{'runtimeEpoch':'owned-epoch','sessionId':session,'workerId':worker,'generation':generation,'taskSeq':seq}}


async def setup(tmp_path):
    now=time.time();fake=Fake(now);rt=Runtime(tmp_path/'policy',tmp_path/'jobs',fake,clock=lambda:now)
    rt.store.configure('automatic','ma',0)
    return rt,fake


def test_actual_latest_quota_and_capacity_fixture_keep_native_fields():
    root=Path('tests/fixtures/runtime_issues')
    expected={'usageLimitExceeded':'codex_quota_exhausted','serverOverloaded':'codex_capacity'}
    for name,category in expected.items():
        row=json.loads((root/(name+'.json')).read_text(encoding='utf-8'))
        result=map_runtime_issue(row['cli']['raw'],adapter=row['adapter'],metadata=row)['issue']
        assert row['cli']['raw']['error']['codexErrorInfo']==name
        assert result['classification']==category and result['confidence']=='structured'
        assert result['correlation']['workerId']==row['workerId']
        assert 'previousMessage' not in row
    provenance=json.loads((root/'provenance.json').read_text(encoding='utf-8'))
    assert all(len(p['lineSha256'])==64 and p['providerErrorUnchanged'] for p in provenance)


def test_normalize_unknown_failure_stale_resets_and_rank():
    now=time.time();f=Fake(now)
    snapshot={'status':'known','observedAt':now,'currentAccountId':'a','accounts':f.accounts}
    clean=normalize(snapshot,now)
    assert [r['id'] for r in rank(clean)]==['c','b','a']
    assert [r['id'] for r in rank(clean,['c'])]==['b','a']
    assert normalize(snapshot,now+301)['status']=='unknown'
    for patch in ({'status':'error'},{'observedAt':now+1},{'accounts':[]},{'currentAccountId':'missing'}):
        assert normalize({**snapshot,**patch},now)['status']=='unknown'
    bad=copy.deepcopy(snapshot);bad['accounts'][0]['week']['usedPercent']=True
    assert normalize(bad,now)['status']=='unknown'


def test_generic_quota_capacity_and_tool_output_never_drive_policy():
    for text in ('quota exhausted','capacity exceeded in local allocator','discuss usage limit reached here'):
        assert map_runtime_issue({'error':{'message':text}},adapter='codex')['issue']['classification']=='unknown'
    raw={'type':'codex.item_error','error':{'codexErrorInfo':'usageLimitExceeded'}}
    assert map_runtime_issue(raw,adapter='codex')['issue']['classification']=='unknown'
    mapped=map_runtime_issue({'type':'error','error':{'code':'quota_exhausted'}},adapter='codex',
                             metadata={'session':object(),'session_id':'s','worker_id':'w','generation':3,'secret':'do not copy'})
    assert mapped['issue']['correlation']=={'sessionId':'s','workerId':'w','generation':3}


@async_test
async def test_concurrent_duplicate_noop_revision_and_missing_account_identity(tmp_path):
    rt,f=await setup(tmp_path)
    results=await asyncio.gather(rt.consume(issue()),rt.consume(issue()))
    assert sorted(r['status'] for r in results)==['duplicate','retry_once']
    before=rt.store.read()['revision'];await rt.progress_restarts()
    assert rt.store.read()['revision']==before
    f.workers[0].pop('accountId')
    assert (await rt.consume(issue(seq=2)))['status']=='unknown'


@async_test
async def test_capacity_retry_once_correlates_terminal_then_switches_and_orders_restarts(tmp_path):
    rt,f=await setup(tmp_path)
    assert (await rt.consume(issue()))['status']=='retry_once'
    assert (await rt.consume({**issue(),'issueId':'terminal-frame'}))['status']=='duplicate'
    await rt.dispatch()
    assert [c['operation'] for c in f.effects]==['continue_once']
    assert (await rt.consume(issue(seq=2)))['status']=='switch_planned'
    await rt.dispatch()
    switch=next(c for c in f.effects if c['operation']=='switch_account')
    assert switch['arguments']['targetAccountId']=='c'
    assert rt.store.read()['excluded']==['a']
    await rt.dispatch();await rt.progress_restarts();await rt.dispatch();await rt.dispatch();await rt.dispatch()
    ops=[c['operation'] for c in f.effects]
    assert ops.count('restart_worker')==2  # running TA + idle Codex, no CBC
    assert ops.count('wake_ta')==1 and ops.index('wake_ta')<ops.index('ma_resume_job')<ops.index('restart_ma')
    restart=next(c for c in f.effects if c['operation']=='restart_ma')
    assert restart['arguments']['resumeJobId']=='owned-ma-wake'
    assert 'credential' not in json.dumps(rt.store.read())


@async_test
async def test_unknown_or_stale_or_old_worker_never_switches(tmp_path):
    rt,f=await setup(tmp_path);f.fail=True
    assert (await rt.consume(issue()))['status']=='unknown'
    f.fail=False;f.now-=301
    assert (await rt.consume(issue()))['status']=='unknown'
    assert (await rt.consume(issue(generation=0)))['status']=='unknown'
    assert (await rt.consume(issue('unknown')))['status']=='ignored'
    await rt.dispatch();assert not f.effects


@async_test
async def test_uncertain_receipt_and_restart_never_repeat_side_effects(tmp_path):
    rt,f=await setup(tmp_path);f.unknown=True
    await rt.consume(issue());await rt.dispatch()
    assert len(f.effects)==1
    fresh=Runtime(tmp_path/'policy',tmp_path/'jobs',f,rt.clock)
    await fresh.recover();await fresh.dispatch();assert len(f.effects)==1
    assert (await fresh.consume(issue(seq=2)))['status']=='blocked'
    command=next(iter(rt.store.read()['commands']))
    rt.store.change(lambda s:s['commands'][command].update(status='dispatching'))
    await fresh.recover();assert fresh.store.read()['commands'][command]['status']=='uncertain'


@async_test
async def test_cbc_keeps_session_and_does_not_query_accounts(tmp_path):
    rt,f=await setup(tmp_path);f.fail=True
    result=await rt.consume(issue('cbc_rate_limited',session='cbc',worker='wc',generation=1))
    assert result['status']=='planned'
    await rt.dispatch();assert f.effects[0]['operation']=='cbc_next_model'
    assert f.effects[0]['arguments']['worker']['sessionId']=='cbc'


@async_test
async def test_monitor_job_persistent_unique_95_and_manual_late_notice_fenced(tmp_path):
    rt,f=await setup(tmp_path)
    job=await rt.create_monitor();same=await rt.create_monitor()
    assert job['jobId']==same['jobId'] and jobs.get(job['jobId'],rt.root)['action']['api']=='quota_query'
    # Own schedule entry deliberately due; actual generic registry is exercised.
    def due():
        j=jobs.get(job['jobId'],rt.root);j['schedule'][0]['nextFireAt']='2000-01-01T00:00:00';jobs._write_job(jobs._job_path(j['jobId'],rt.root),j)
    due();f.accounts[0]['fiveHour']['usedPercent']=95
    await rt.tick_jobs();assert not jobs.get(job['jobId'],rt.root)['enabled']
    assert len([c for c in rt.store.read()['commands'].values() if c['operation']=='wake_ma'])==1
    state=rt.store.read();rt.store.configure('manual','ma',state['revision'])
    await rt.dispatch();assert not f.effects
    fresh=Runtime(tmp_path/'policy',rt.root,f,rt.clock);await fresh.tick_jobs()
    assert jobs.get(job['jobId'],rt.root)['status']=='cancelled'


@async_test
async def test_all_exhausted_has_one_durable_reset_query_job_and_unknown_is_not_threshold(tmp_path):
    rt,f=await setup(tmp_path)
    for r in f.accounts:r['fiveHour']['usedPercent']=100
    assert (await rt.consume(issue('codex_quota_exhausted')))['status']=='waiting_reset'
    await rt.dispatch()
    reset=[j for j in jobs.list_jobs(rt.root) if (j.get('quotaPolicy') or {}).get('purpose')=='reset']
    assert len(reset)==1 and reset[0]['schedule'][0]['kind']=='once'
    assert not f.effects
    # A failed query is persisted unknown and never emits a wake/threshold fact.
    state=rt.store.read();rt.store.configure('automatic','ma',state['revision'])
    job=await rt.create_monitor()
    f.fail=True
    j=jobs.get(job['jobId'],rt.root);j['schedule'][0]['nextFireAt']='2000-01-01T00:00:00';jobs._write_job(jobs._job_path(j['jobId'],rt.root),j)
    await rt.tick_jobs();assert jobs.get(job['jobId'],rt.root)['quotaResult']=={'status':'unknown','thresholdReached':False}
    assert jobs.get(job['jobId'],rt.root)['enabled']


@async_test
async def test_default_adapter_fail_closed_and_snapshot_write_failure_no_effect(tmp_path,monkeypatch):
    rt=Runtime(tmp_path/'policy',tmp_path/'jobs');rt.store.configure('automatic','ma',0)
    assert (await rt.consume(issue()))['status']=='unknown'
    assert (await rt.query())['status']=='unknown'
    rt,f=await setup(tmp_path/'other')
    import packages.automation.quota_policy as module
    def fail(*args):raise OSError('owned disk failure')
    monkeypatch.setattr(module,'atomic',fail)
    with pytest.raises(OSError):await rt.consume(issue())
    assert not f.effects


@async_test
async def test_parallel_query_job_creation_and_partial_link_recovery(tmp_path, monkeypatch):
    rt,f=await setup(tmp_path)
    import threading
    from concurrent.futures import ThreadPoolExecutor
    state=rt.store.read();barrier=threading.Barrier(2);original=jobs._create
    def simultaneous(job,root):
        barrier.wait(timeout=5)
        return original(job,root)
    monkeypatch.setattr(jobs,'_create',simultaneous)
    with ThreadPoolExecutor(2) as pool:
        futures=[pool.submit(rt.ensure_job,state,'once',rt.clock()+1) for _ in range(2)]
        result=[future.result(timeout=10) for future in futures]
    assert result[0]['jobId']==result[1]['jobId']
    assert len(jobs.list_jobs(rt.root))==1
    rt.store.change(lambda s:s['jobs'].clear())
    recovered=rt.ensure_job(state,'once',rt.clock()+1)
    assert rt.store.read()['jobs']['once']==recovered['jobId']


@async_test
async def test_new_capacity_during_handover_blocks_ma_steps_and_restart_dedup(tmp_path):
    rt,f=await setup(tmp_path)
    await rt.consume(issue('codex_quota_exhausted'));await rt.dispatch()
    await rt.dispatch();await rt.progress_restarts()
    assert (await rt.consume(issue(seq=2,generation=f.workers[0]['generation'])))['status']=='blocked'
    state=rt.store.read()
    assert any(c['status']=='blocked_handover_review' for c in state['cases'].values())
    assert any(c['operation']=='ma_resume_job' and c['status']=='blocked_new_capacity' for c in state['commands'].values())
    fresh=Runtime(tmp_path/'policy',rt.root,f,rt.clock);await fresh.recover()
    assert (await fresh.consume(issue('codex_quota_exhausted')))['status']=='duplicate'
    await fresh.dispatch();await fresh.dispatch()
    assert not any(c['operation'] in ('ma_resume_job','restart_ma') for c in f.effects)
    fresh.closed=True;fresh.observe({'issue':issue(seq=3)})
    assert fresh.pending.empty()


def test_ledger_capacity_rejects_without_erasing_dedup(tmp_path,monkeypatch):
    import packages.automation.quota_policy as module
    monkeypatch.setattr(module,'MAX_RECORDS',1)
    store=module.Store(tmp_path)
    store.change(lambda s:s['seen'].update({'first':'native'}))
    before=store.path.read_bytes()
    with pytest.raises(ValueError,match='capacity'):
        store.change(lambda s:s['seen'].update({'second':'native'}))
    assert store.path.read_bytes()==before


@async_test
async def test_monitor_concurrent_create_keeps_one_enabled(tmp_path):
    rt,f=await setup(tmp_path)
    results=await asyncio.gather(rt.create_monitor(),rt.create_monitor())
    assert results[0]['jobId']==results[1]['jobId']
    assert len([j for j in jobs.list_jobs(rt.root) if j['enabled']])==1


@async_test
async def test_logged_native_issue_enters_bounded_policy_observer(tmp_path):
    from packages.core import runtime_issues, session_error_log
    rt,f=await setup(tmp_path)
    log=tmp_path/'owned-session-errors.jsonl'
    session_error_log.reset_state();session_error_log.configure(log)
    runtime_issues.set_observer(rt.observe)
    mapping=map_runtime_issue({'type':'error','error':{'codexErrorInfo':'usageLimitExceeded'}},
                              adapter='codex',metadata={'runtimeEpoch':'owned-epoch','sessionId':'ta','workerId':'w','generation':2,'taskSeq':1})
    try:
        event_id=await session_error_log.record_session_error_async(kind='terminal_error',
             error_text='owned native sample',error_text_source='test',terminal=True,runtime_issue=mapping)
        assert event_id and log.exists() and rt.pending.qsize()==1
        await rt.tick()
        assert any(c['operation']=='switch_account' for c in f.effects)
        assert json.loads(log.read_text(encoding='utf-8'))['runtimeIssue']['issue']['classification']=='codex_quota_exhausted'
    finally:
        runtime_issues.set_observer(None);session_error_log.reset_state()


@async_test
async def test_manual_change_during_monitor_query_cannot_create_or_wake(tmp_path):
    rt,f=await setup(tmp_path);entered=asyncio.Event();release=asyncio.Event()
    query=f.query
    async def waiting():
        entered.set();await release.wait();return await query()
    f.query=waiting
    task=asyncio.create_task(rt.create_monitor())
    await entered.wait()
    state=rt.store.read();rt.store.configure('manual','ma',state['revision'])
    await rt.cancel_obsolete_jobs();release.set()
    with pytest.raises(ValueError,match='changed during query'):await task
    assert not jobs.list_jobs(rt.root) and not f.effects


@async_test
async def test_close_during_policy_query_stops_new_effects(tmp_path):
    rt,f=await setup(tmp_path);entered=asyncio.Event();release=asyncio.Event();query=f.query
    async def waiting():
        entered.set();await release.wait();return await query()
    f.query=waiting
    rt.observe({'issue':issue('codex_quota_exhausted')})
    task=asyncio.create_task(rt.tick());await entered.wait()
    rt.closed=True  # Service.close sets this before awaiting runtime shutdown.
    release.set();await task
    assert not f.effects
    assert not rt.store.read()['commands']


@async_test
async def test_close_waits_for_inflight_receipt_and_preserves_result(tmp_path):
    rt,f=await setup(tmp_path);entered=asyncio.Event();release=asyncio.Event();perform=f.perform
    async def waiting(command,key):
        entered.set();await release.wait();return await perform(command,key)
    f.perform=waiting
    rt.observe({'issue':issue()});tick=asyncio.create_task(rt.tick());await entered.wait()
    closing=asyncio.create_task(rt.close());await asyncio.sleep(0)
    assert not closing.done()
    release.set();await tick;await closing
    assert len(f.effects)==1
    assert next(iter(rt.store.read()['commands'].values()))['status']=='completed'
    await rt.tick();assert len(f.effects)==1
