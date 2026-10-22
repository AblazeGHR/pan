"""Quota safety regressions: only isolated stores and deterministic adapters."""
import asyncio
import copy
import json

import pytest

from packages.automation.quota_policy import Store, Runtime, rank, normalize
from packages.automation.store import Conflict
from packages.core import background_jobs as jobs
from tests.test_automation_core import async_test
from tests.test_quota_policy import setup, issue


@async_test
async def test_disabled_with_quota_never_selected(tmp_path):
    rt,f=await setup(tmp_path)
    rt.store.change(lambda s:s.update(disabledAccountIds=['c']))
    await rt.consume(issue('codex_quota_exhausted'))
    switch=next(c for c in rt.store.read()['commands'].values() if c['operation']=='switch_account')
    assert switch['arguments']['targetAccountId']=='b'


@async_test
async def test_old_running_completed_before_wake_never_woken(tmp_path):
    rt,f=await setup(tmp_path)
    await rt.consume(issue('codex_quota_exhausted'));await rt.dispatch();await rt.dispatch()
    f.workers[0]['task']={'taskId':'done-task','executionSeq':1,'state':'completed'}
    await rt.progress_restarts();await rt.dispatch()
    assert not any(c['operation']=='wake_ta' for c in f.effects)


def disable(rt,ids):
    return rt.store.set_disabled(ids,rt.store.read()['revision'])


async def switched(tmp_path):
    rt,f=await setup(tmp_path)
    await rt.consume(issue('codex_quota_exhausted'));await rt.dispatch()
    return rt,f


@async_test
async def test_disabled_persistent_separate_from_capacity_exclusion_and_recovery(tmp_path):
    rt,f=await setup(tmp_path)
    disable(rt,['c']);rt.store.change(lambda s:s['excluded'].append('b'))
    before=rt.store.read();rt.store.configure('manual','ma',before['revision'])
    rt.store.configure('automatic','ma',rt.store.read()['revision'])
    await rt.query();fresh=Runtime(tmp_path/'policy',rt.root,f,rt.clock);await fresh.recover()
    state=fresh.store.read()
    assert state['disabledAccountIds']==['c'] and state['excluded']==['b']
    assert state['accountConfigRevision']==1
    assert [r['id'] for r in rank(state['quota'],state['excluded'],state['disabledAccountIds'])]==['a']
    revision=state['revision'];disable(fresh,['c'])
    assert fresh.store.read()['revision']==revision  # explicit identical update is idempotent


@pytest.mark.parametrize('invalid',[None,{},'c',['c','c'],[''],[' c'],[True],[{}],['x'*257],['x'+str(i) for i in range(129)]])
def test_disabled_configuration_rejects_invalid_without_write(tmp_path,invalid):
    store=Store(tmp_path)
    with pytest.raises(ValueError):store.set_disabled(invalid,0)
    assert not store.path.exists()


def test_disabled_revision_migration_and_malformed_disk_fail_closed(tmp_path):
    store=Store(tmp_path);store.configure('manual','ma',0)
    state=store.read();state.pop('disabledAccountIds');state.pop('accountConfigRevision');state['version']=1
    store.path.write_text(json.dumps(state),encoding='utf-8');before=store.path.read_bytes()
    assert store.read()['disabledAccountIds']==[] and store.path.read_bytes()==before
    store.set_disabled(['not-currently-in-inventory'],state['revision'])
    before=store.path.read_bytes()
    with pytest.raises(Conflict):store.set_disabled([],state['revision'])
    with pytest.raises(ValueError):store.set_disabled([],True)
    assert store.path.read_bytes()==before
    state=store.read();state['disabledAccountIds']='invalid'
    store.path.write_text(json.dumps(state),encoding='utf-8')
    with pytest.raises(ValueError):store.read()


@async_test
async def test_disabled_nearest_reset_omitted_and_all_disabled_no_reset(tmp_path):
    rt,f=await setup(tmp_path);disable(rt,['c'])
    for i,row in enumerate(f.accounts):
        row['fiveHour'].update(usedPercent=100,resetsAt=f.now+[900,500,10][i])
    await rt.consume(issue('codex_quota_exhausted'))
    command=next(c for c in rt.store.read()['commands'].values() if c['operation']=='reset_wake_job')
    assert command['arguments']['at']==f.now+500
    disable(rt,['a','b','c'])
    assert (await rt.consume(issue('codex_quota_exhausted',seq=2)))['status']=='blocked'
    assert not any(c['status']=='ready' for c in rt.store.read()['commands'].values())


@async_test
async def test_disabled_current_capacity_skips_retry_and_switches_off_account(tmp_path):
    rt,f=await setup(tmp_path);disable(rt,['a'])
    assert (await rt.consume(issue()))['status']=='switch_planned'
    await rt.dispatch()
    assert [c['operation'] for c in f.effects]==['switch_account']
    assert f.currentAccountId=='c' and rt.store.read()['disabledAccountIds']==['a']


@async_test
async def test_disable_during_switch_preflight_cannot_dispatch_or_override_cancel(tmp_path):
    rt,f=await setup(tmp_path);await rt.consume(issue('codex_quota_exhausted'))
    entered=asyncio.Event();release=asyncio.Event();inventory=f.inventory
    async def waiting():
        entered.set();await release.wait();return await inventory()
    f.inventory=waiting
    task=asyncio.create_task(rt.dispatch());await entered.wait()
    disable(rt,['c']);release.set();await task
    assert not f.effects
    assert all(c['status']=='cancelled' for c in rt.store.read()['commands'].values())


@async_test
async def test_switch_preflight_rechecks_disabled_and_fresh_quota(tmp_path):
    rt,f=await setup(tmp_path);await rt.consume(issue('codex_quota_exhausted'))
    # Even an offline host configuration write without epoch cancellation is rechecked.
    rt.store.change(lambda s:s.update(disabledAccountIds=['c']))
    await rt.dispatch();assert not f.effects
    assert any(c.get('diagnostic')=='account_disabled' for c in rt.store.read()['commands'].values())
    rt,f=await setup(tmp_path/'quota-change');await rt.consume(issue('codex_quota_exhausted'))
    f.accounts[2]['fiveHour']['usedPercent']=100
    await rt.dispatch();assert not f.effects


@pytest.mark.parametrize('phase',['before_plan','before_send'])
@pytest.mark.parametrize('change',['completed','idle','offline','unknown_task','different_task','different_execution','new_generation','new_process','role_changed','wrong_account'])
@async_test
async def test_wake_rechecks_current_task_process_role_and_successor(tmp_path,phase,change):
    rt,f=await switched(tmp_path);await rt.dispatch()
    if phase=='before_send':await rt.progress_restarts()
    w=f.workers[0]
    if change=='completed':w['task']['state']='completed'
    elif change=='idle':w['status']='idle'
    elif change=='offline':w.update(status='offline',alive=False)
    elif change=='unknown_task':w['task']=None
    elif change=='different_task':w['task']['taskId']='unrelated'
    elif change=='different_execution':w['task']['executionSeq']+=1
    elif change=='new_generation':w['generation']+=1
    elif change=='new_process':w['processIdentity']['birthToken']='unrelated-process'
    elif change=='wrong_account':w['accountId']='not-the-selected-account'
    else:w['role']='ma'
    await rt.progress_restarts();await rt.dispatch()
    wakes=[c for c in f.effects if c['operation']=='wake_ta']
    if change=='idle':
        # Revised contract: this exact successor has a confirmed restart receipt.
        assert len(wakes)==1 and wakes[0]['arguments']['original']['task']==w['task']
    else:assert not wakes
    if phase=='before_send':
        expected='completed' if change=='idle' else 'skipped'
        assert any(c['operation']=='wake_ta' and c['status']==expected for c in rt.store.read()['commands'].values())


@pytest.mark.parametrize('missing',['task','taskId','executionSeq','role'])
@async_test
async def test_missing_frozen_unfinished_task_or_explicit_role_never_guessed(tmp_path,missing):
    rt,f=await setup(tmp_path)
    if missing=='task':f.workers[0].pop('task')
    elif missing=='role':
        f.workers[0].pop('role');f.workers[0]['name']='TA running task'
    else:f.workers[0]['task'].pop(missing)
    await rt.consume(issue('codex_quota_exhausted'));await rt.dispatch();await rt.dispatch()
    await rt.progress_restarts();await rt.dispatch()
    assert not any(c['operation']=='wake_ta' for c in f.effects)


@pytest.mark.parametrize('change',['offline','generation','birth','runtime_epoch'])
@async_test
async def test_restart_never_starts_offline_or_retargets_new_identity(tmp_path,change):
    rt,f=await switched(tmp_path)
    if change=='offline':f.workers[0].update(status='offline',alive=False)
    elif change=='generation':f.workers[0]['generation']+=1
    elif change=='birth':f.workers[0]['processIdentity']['birthToken']='new-unrelated-process'
    else:f.runtimeEpoch='different-owner'
    await rt.dispatch();await rt.progress_restarts();await rt.dispatch()
    assert not any(c['operation']=='restart_worker' and c['arguments']['worker']['sessionId']=='ta' for c in f.effects)
    assert not any(c['operation']=='wake_ta' for c in f.effects)


@pytest.mark.parametrize('bad',['missing','same_generation','wrong_successor','wrong_epoch'])
@async_test
async def test_unverified_restart_successor_uncertain_and_never_replayed(tmp_path,bad):
    rt,f=await switched(tmp_path);perform=f.perform
    async def invalid(command,key):
        receipt=await perform(command,key)
        if command['operation']=='restart_worker' and command['arguments']['worker']['sessionId']=='ta':
            if bad=='missing':receipt.pop('successor')
            elif bad=='same_generation':receipt['successor']['generation']=command['arguments']['worker']['generation']
            elif bad=='wrong_successor':receipt['successor']['processIdentity']['birthToken']='not-current'
            else:receipt['successor']['runtimeEpoch']='other-owner'
        return receipt
    f.perform=invalid
    await rt.dispatch()
    uncertain=[c for c in rt.store.read()['commands'].values() if c['operation']=='restart_worker' and c['status']=='uncertain']
    assert len(uncertain)==1
    before=copy.deepcopy(f.effects);fresh=Runtime(tmp_path/'policy',rt.root,f,rt.clock)
    await fresh.recover();await fresh.progress_restarts();await fresh.dispatch()
    assert f.effects==before


@async_test
async def test_legacy_commands_jobs_blocked_without_identity_upgrade(tmp_path):
    rt,f=await setup(tmp_path);await rt.consume(issue())
    def old(s):
        for c in s['commands'].values():
            c.pop('contractVersion');c['arguments'].pop('worker');c['arguments'].pop('runtimeEpoch')
    rt.store.change(old);await rt.recover();await rt.dispatch()
    assert not f.effects and all(c['status']=='blocked' for c in rt.store.read()['commands'].values())
    job=await rt.create_monitor()
    j=jobs.get(job['jobId'],rt.root);j['quotaPolicy'].pop('contractVersion')
    jobs._write_job(jobs._job_path(j['jobId'],rt.root),j)
    await rt.tick_jobs()
    assert jobs.get(job['jobId'],rt.root)['status']=='blocked'
    assert not jobs.get(job['jobId'],rt.root)['enabled']


@pytest.mark.parametrize('mode',['manual','close'])
@async_test
async def test_manual_close_during_identity_preflight_no_effect(tmp_path,mode):
    rt,f=await setup(tmp_path);await rt.consume(issue())
    entered=asyncio.Event();release=asyncio.Event();inventory=f.inventory
    async def waiting():
        entered.set();await release.wait();return await inventory()
    f.inventory=waiting
    task=asyncio.create_task(rt.dispatch());await entered.wait()
    if mode=='manual':rt.store.configure('manual','ma',rt.store.read()['revision'])
    else:rt.closed=True
    release.set();await task
    assert not f.effects


@async_test
async def test_identity_unknown_before_wake_blocks_without_replay(tmp_path):
    rt,f=await switched(tmp_path);await rt.dispatch();await rt.progress_restarts()
    f.inventory=lambda: asyncio.sleep(0,result={'workers':f.workers})
    await rt.dispatch()
    assert not any(c['operation']=='wake_ta' for c in f.effects)
    assert any(c['operation']=='wake_ta' and c['status']=='blocked' for c in rt.store.read()['commands'].values())
    before=copy.deepcopy(f.effects);fresh=Runtime(tmp_path/'policy',rt.root,f,rt.clock)
    await fresh.recover();await fresh.dispatch();assert f.effects==before


@pytest.mark.parametrize('change',['missing_epoch','missing_process','not_alive','stale','missing_event_epoch'])
@async_test
async def test_observation_requires_verified_runtime_process_identity(tmp_path,change):
    rt,f=await setup(tmp_path);event=issue();inventory=f.inventory
    async def malformed():
        result=await inventory()
        if change=='missing_epoch':result.pop('runtimeEpoch')
        elif change=='missing_process':result['workers'][0].pop('processIdentity')
        elif change=='not_alive':result['workers'][0]['alive']=None
        elif change=='stale':result['observedAt']-=6
        return result
    f.inventory=malformed
    if change=='missing_event_epoch':event['correlation'].pop('runtimeEpoch')
    assert (await rt.consume(event))['status']=='unknown'
    assert not rt.store.read()['commands']


@async_test
async def test_disable_persistence_failure_does_not_publish_new_configuration(tmp_path,monkeypatch):
    rt,f=await setup(tmp_path);disable(rt,['c']);before=rt.store.path.read_bytes()
    import packages.automation.quota_policy as module
    def fail(*args):raise OSError('owned failed policy write')
    monkeypatch.setattr(module,'atomic',fail)
    with pytest.raises(OSError):disable(rt,[])
    assert rt.store.path.read_bytes()==before and rt.store.read()['disabledAccountIds']==['c']


@async_test
async def test_concurrent_disabled_updates_require_one_current_revision(tmp_path):
    from packages.plugins.lifecycle import io
    rt,f=await setup(tmp_path);revision=rt.store.read()['revision']
    results=await asyncio.gather(io(rt.store.set_disabled,['b'],revision),
                                 io(rt.store.set_disabled,['c'],revision),return_exceptions=True)
    assert sum(isinstance(result,Conflict) for result in results)==1
    assert rt.store.read()['disabledAccountIds'] in (['b'],['c'])
    assert rt.store.read()['accountConfigRevision']==1


@async_test
async def test_monitor_does_not_retarget_a_new_ma_task_after_creation(tmp_path):
    rt,f=await setup(tmp_path);job=await rt.create_monitor()
    ma=next(w for w in f.workers if w['role']=='ma')
    ma['task']['taskId']='new-unrelated-ma-task'
    f.accounts[0]['fiveHour']['usedPercent']=95
    j=jobs.get(job['jobId'],rt.root);j['schedule'][0]['nextFireAt']='2000-01-01T00:00:00'
    jobs._write_job(jobs._job_path(j['jobId'],rt.root),j)
    await rt.tick_jobs();await rt.dispatch()
    assert not any(c['operation']=='wake_ma' for c in f.effects)


@async_test
async def test_monitor_follows_only_verified_ma_successor_with_same_task(tmp_path):
    rt,f=await switched(tmp_path);await rt.dispatch();await rt.progress_restarts()
    for _ in range(4):await rt.dispatch()
    assert any(c['operation']=='restart_ma' for c in f.effects)
    monitor=next(j for j in jobs.list_jobs(rt.root) if j['quotaPolicy']['purpose']=='monitor95')
    assert monitor['quotaPolicy']['wakeTarget']['generation']==4
    next(r for r in f.accounts if r['id']==f.currentAccountId)['fiveHour']['usedPercent']=95
    monitor['schedule'][0]['nextFireAt']='2000-01-01T00:00:00'
    jobs._write_job(jobs._job_path(monitor['jobId'],rt.root),monitor)
    await rt.tick_jobs();await rt.dispatch()
    wake=next(c for c in f.effects if c['operation']=='wake_ma')
    assert wake['arguments']['original']['generation']==4
    assert wake['arguments']['worker']['generation']==5
    assert wake['arguments']['worker']['task']==wake['arguments']['original']['task']


@async_test
async def test_monitor_missing_ma_task_cannot_create_implicit_wake(tmp_path):
    rt,f=await setup(tmp_path)
    next(w for w in f.workers if w['role']=='ma')['task']=None
    with pytest.raises(ValueError,match='unfinished task identity unknown'):await rt.create_monitor()
    assert not jobs.list_jobs(rt.root)


def test_runtime_epoch_is_host_correlation_not_provider_assertion():
    from packages.core.runtime_issues import map_runtime_issue
    raw={'type':'error','runtimeEpoch':'payload-claim','error':{'codexErrorInfo':'usageLimitExceeded'}}
    assert 'runtimeEpoch' not in map_runtime_issue(raw,adapter='codex')['issue']['correlation']
    assert map_runtime_issue(raw,adapter='codex',metadata={'runtime_epoch':'host-owner'})['issue']['correlation']['runtimeEpoch']=='host-owner'
