"""Receipted idle-successor recovery, using only the owned in-memory adapter."""
import copy
import asyncio

import pytest

from packages.automation.quota_policy import Runtime
from packages.core import background_jobs as jobs
from tests.test_automation_core import async_test
from tests.test_quota_policy import setup, issue


def idle_after_restart(fake):
    perform=fake.perform
    async def wrapped(command,key):
        receipt=await perform(command,key)
        if command['operation'] in ('restart_worker','restart_ma'):
            sid=command['arguments']['worker']['sessionId']
            current=next(w for w in fake.workers if w['sessionId']==sid)
            current['status']='idle'
            receipt['successor']=dict(copy.deepcopy(current),runtimeEpoch=fake.runtimeEpoch)
        return receipt
    fake.perform=wrapped


@async_test
async def test_normal_restart_idle_resumes_frozen_task_exactly_once(tmp_path):
    rt,f=await setup(tmp_path);idle_after_restart(f)
    await rt.consume(issue('codex_quota_exhausted'));await rt.dispatch();await rt.dispatch()
    assert f.workers[0]['status']=='idle'
    assert any(c['operation']=='restart_worker' and c['status']=='completed'
               and c['successor']['sessionId']=='ta' for c in rt.store.read()['commands'].values())
    await rt.progress_restarts();await rt.dispatch();await rt.dispatch()
    wakes=[c for c in f.effects if c['operation']=='wake_ta']
    assert len(wakes)==1
    assert wakes[0]['arguments']['original']['status']=='running'
    assert wakes[0]['arguments']['worker']['status']=='idle'
    assert wakes[0]['arguments']['original']['task']==wakes[0]['arguments']['worker']['task']
    assert wakes[0]['arguments']['restartCommandId'] in rt.store.read()['commands']
    fresh=Runtime(tmp_path/'policy',rt.root,f,rt.clock)
    await fresh.recover();await fresh.progress_restarts();await fresh.dispatch()
    assert len([c for c in f.effects if c['operation']=='wake_ta'])==1


async def restarted_idle(tmp_path):
    rt,f=await setup(tmp_path);idle_after_restart(f)
    await rt.consume(issue('codex_quota_exhausted'));await rt.dispatch();await rt.dispatch()
    receipt=next(c for c in rt.store.read()['commands'].values() if c['operation']=='restart_worker'
                 and c['arguments']['worker']['sessionId']=='ta')
    assert receipt['status']=='completed'
    return rt,f,receipt['id']


@pytest.mark.parametrize('phase',['before_plan','before_send'])
@pytest.mark.parametrize('bad',['missing','uncertain','wrong_case','wrong_source','same_generation',
                                'same_birth','wrong_account','old_epoch','old_config'])
@async_test
async def test_idle_requires_matching_current_confirmed_restart(tmp_path,phase,bad):
    rt,f,key=await restarted_idle(tmp_path)
    if phase=='before_send':await rt.progress_restarts()
    def corrupt(s):
        c=s['commands'][key]
        if bad=='missing':s['commands'].pop(key)
        elif bad=='uncertain':c['status']='uncertain'
        elif bad=='wrong_case':c['caseId']='not-this-recovery'
        elif bad=='wrong_source':c['arguments']['worker']['task']['executionSeq']+=1
        elif bad=='same_generation':c['successor']['generation']=c['arguments']['worker']['generation']
        elif bad=='same_birth':c['successor']['processIdentity']['birthToken']=c['arguments']['worker']['processIdentity']['birthToken']
        elif bad=='wrong_account':c['arguments']['expectedAccountId']='different-account'
        elif bad=='old_epoch':c['epoch']-=1
        else:c['accountConfigRevision']-=1
    rt.store.change(corrupt)
    await rt.progress_restarts();await rt.dispatch()
    assert not any(c['operation']=='wake_ta' for c in f.effects)


@pytest.mark.parametrize('phase',['before_plan','before_send'])
@pytest.mark.parametrize('bad',['completed','missing_task','different_task','different_execution','old_process',
                                'different_pid','generation','offline','unknown_inventory','stale_inventory','account','owner'])
@async_test
async def test_idle_target_rechecked_for_same_unfinished_execution(tmp_path,phase,bad):
    rt,f,key=await restarted_idle(tmp_path)
    if phase=='before_send':await rt.progress_restarts()
    w=f.workers[0]
    if bad=='completed':w['task']['state']='completed'
    elif bad=='missing_task':w['task']=None
    elif bad=='different_task':w['task']['taskId']='another-task'
    elif bad=='different_execution':w['task']['executionSeq']+=1
    elif bad=='old_process':w['processIdentity']['birthToken']='birth-0'
    elif bad=='different_pid':w['processIdentity']['pid']+=100
    elif bad=='generation':w['generation']+=1
    elif bad=='offline':w.update(status='offline',alive=False)
    elif bad=='unknown_inventory':f.inventory=lambda: asyncio.sleep(0,result={})
    elif bad=='stale_inventory':f.now-=6
    elif bad=='account':w['accountId']='other-account'
    else:f.runtimeEpoch='other-owner'
    await rt.progress_restarts();await rt.dispatch()
    assert not any(c['operation']=='wake_ta' for c in f.effects)


@pytest.mark.parametrize('original',['idle','no_task','completed','unknown_role'])
@async_test
async def test_idle_successor_does_not_promote_unqualified_original(tmp_path,original):
    rt,f=await setup(tmp_path);idle_after_restart(f)
    w=f.workers[0]
    if original=='idle':w['status']='idle'  # even with a task, original was not running
    elif original=='no_task':w['task']=None
    elif original=='completed':w['task']['state']='completed'
    else:w['role']=None;w['name']='TA running'
    await rt.consume(issue('codex_quota_exhausted'));await rt.dispatch();await rt.dispatch()
    await rt.progress_restarts();await rt.dispatch()
    assert not any(c['operation']=='wake_ta' for c in f.effects)


@pytest.mark.parametrize('status',['idle','running'])
@async_test
async def test_wake_command_without_receipt_reference_cannot_adopt_successor(tmp_path,status):
    rt,f,key=await restarted_idle(tmp_path);await rt.progress_restarts()
    f.workers[0]['status']=status
    def unlink(s):
        for c in s['commands'].values():
            if c['operation']=='wake_ta':c['arguments'].pop('restartCommandId')
    rt.store.change(unlink);await rt.dispatch()
    assert not any(c['operation']=='wake_ta' for c in f.effects)
    assert any(c['operation']=='wake_ta' and c['status']=='blocked' for c in rt.store.read()['commands'].values())


@async_test
async def test_receipt_is_read_again_after_inventory_await(tmp_path):
    rt,f,key=await restarted_idle(tmp_path);await rt.progress_restarts()
    entered=asyncio.Event();release=asyncio.Event();inventory=f.inventory
    async def waiting():
        entered.set();await release.wait();return await inventory()
    f.inventory=waiting
    task=asyncio.create_task(rt.dispatch());await entered.wait()
    rt.store.change(lambda s:s['commands'][key].update(status='uncertain'))
    release.set();await task
    assert not any(c['operation']=='wake_ta' for c in f.effects)


@async_test
async def test_unknown_idle_wake_receipt_never_replayed(tmp_path):
    rt,f,key=await restarted_idle(tmp_path);await rt.progress_restarts();f.unknown=True
    await rt.dispatch()
    assert len([c for c in f.effects if c['operation']=='wake_ta'])==1
    assert any(c['operation']=='wake_ta' and c['status']=='uncertain' for c in rt.store.read()['commands'].values())
    f.unknown=False;fresh=Runtime(tmp_path/'policy',rt.root,f,rt.clock)
    await fresh.recover();await fresh.progress_restarts();await fresh.dispatch()
    assert len([c for c in f.effects if c['operation']=='wake_ta'])==1


@pytest.mark.parametrize('stop',['manual','close','disable'])
@async_test
async def test_pending_idle_wake_obeys_policy_fences(tmp_path,stop):
    rt,f,key=await restarted_idle(tmp_path);await rt.progress_restarts()
    if stop=='manual':rt.store.configure('manual','ma',rt.store.read()['revision'])
    elif stop=='close':await rt.close()
    else:rt.store.set_disabled([f.currentAccountId],rt.store.read()['revision'])
    await rt.dispatch();assert not any(c['operation']=='wake_ta' for c in f.effects)


@async_test
async def test_restart_receipt_cannot_reuse_old_birth_token_on_different_pid(tmp_path):
    rt,f=await setup(tmp_path);perform=f.perform
    async def invalid(command,key):
        receipt=await perform(command,key)
        if command['operation']=='restart_worker' and command['arguments']['worker']['sessionId']=='ta':
            token=command['arguments']['worker']['processIdentity']['birthToken']
            f.workers[0]['processIdentity']['birthToken']=token
            receipt['successor']['processIdentity']['birthToken']=token
        return receipt
    f.perform=invalid
    await rt.consume(issue('codex_quota_exhausted'));await rt.dispatch();await rt.dispatch()
    assert any(c['operation']=='restart_worker' and c['status']=='uncertain' for c in rt.store.read()['commands'].values())
    await rt.progress_restarts();await rt.dispatch();assert not any(c['operation']=='wake_ta' for c in f.effects)


@pytest.mark.parametrize('receipted',[False,True])
@async_test
async def test_ma_reminder_idle_requires_its_own_restart_receipt(tmp_path,receipted):
    rt,f=await setup(tmp_path)
    if receipted:
        idle_after_restart(f)
        await rt.consume(issue('codex_quota_exhausted'));await rt.dispatch();await rt.dispatch()
        await rt.progress_restarts()
        for _ in range(4):await rt.dispatch()
        monitor=next(j for j in jobs.list_jobs(rt.root) if j['quotaPolicy']['purpose']=='monitor95')
    else:
        job=await rt.create_monitor();monitor=jobs.get(job['jobId'],rt.root)
        next(w for w in f.workers if w['role']=='ma')['status']='idle'
    next(r for r in f.accounts if r['id']==f.currentAccountId)['fiveHour']['usedPercent']=95
    monitor['schedule'][0]['nextFireAt']='2000-01-01T00:00:00'
    jobs._write_job(jobs._job_path(monitor['jobId'],rt.root),monitor)
    await rt.tick_jobs();await rt.dispatch();await rt.dispatch()
    wakes=[c for c in f.effects if c['operation']=='wake_ma']
    assert len(wakes)==int(receipted)
    if receipted:
        assert wakes[0]['arguments']['worker']['status']=='idle'
        assert wakes[0]['arguments']['original']['task']==wakes[0]['arguments']['worker']['task']
        assert rt.store.read()['commands'][wakes[0]['arguments']['restartCommandId']]['operation']=='restart_ma'


@async_test
async def test_idle_ma_cannot_become_new_monitor_original(tmp_path):
    rt,f=await setup(tmp_path);next(w for w in f.workers if w['role']=='ma')['status']='idle'
    with pytest.raises(ValueError,match='unfinished task identity unknown'):await rt.create_monitor()


@async_test
async def test_ma_idle_reminder_rechecks_restart_proof_before_send(tmp_path):
    rt,f=await setup(tmp_path);idle_after_restart(f)
    await rt.consume(issue('codex_quota_exhausted'));await rt.dispatch();await rt.dispatch()
    await rt.progress_restarts()
    for _ in range(4):await rt.dispatch()
    monitor=next(j for j in jobs.list_jobs(rt.root) if j['quotaPolicy']['purpose']=='monitor95')
    next(r for r in f.accounts if r['id']==f.currentAccountId)['fiveHour']['usedPercent']=95
    monitor['schedule'][0]['nextFireAt']='2000-01-01T00:00:00'
    jobs._write_job(jobs._job_path(monitor['jobId'],rt.root),monitor)
    await rt.tick_jobs()
    wake=next(c for c in rt.store.read()['commands'].values() if c['operation']=='wake_ma')
    rt.store.change(lambda s:s['commands'][wake['arguments']['restartCommandId']].update(status='uncertain'))
    await rt.dispatch()
    assert not any(c['operation']=='wake_ma' for c in f.effects)
    assert rt.store.read()['commands'][wake['id']]['status']=='skipped'
