"""Host adapter contracts. All effects are injected, in-memory business fixtures."""
import asyncio
import copy
from datetime import datetime,timezone
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace as NS

import pytest

from packages.automation.quota_host import HostAdapter, ReadSource, Bindings, retained_process
from packages.automation.quota_policy import Runtime, Unavailable
from packages.core.codex_quota_store import CodexProfile,CodexQuotaStore
from packages.jobs.window import command_window_options
from tests.test_automation_core import async_test


def stamp(at):return datetime.fromtimestamp(at,timezone.utc).isoformat()


def limits(now):
    return {'primary':{'usedPercent':25,'windowDurationMins':300,'resetsAt':now+1800},
            'secondary':{'usedPercent':70,'windowDurationMins':10080,'resetsAt':now+86400},
            'access_token':'fixture-secret-must-not-escape'}


def source_fixture(tmp_path,monkeypatch):
    now=float(int(time.time()));profile=CodexProfile(tmp_path/'codex','native-account','native-account','profile-key')
    store=CodexQuotaStore(profile,root=tmp_path/'quota')
    store.update(limits(now),observed_at=stamp(now),received_at=stamp(now))
    trace=tmp_path/'cli.jsonl';monkeypatch.setenv('PAN_TEST_QUOTA_HOST_TRACE',str(trace))
    def runner(args):
        result=subprocess.run([sys.executable,'tests/fixtures/quota_host_cli.py',*args],
            capture_output=True,encoding='utf-8',errors='strict',timeout=5,stdin=subprocess.DEVNULL,
            **command_window_options())
        assert result.returncode==0,result.returncode
        return json.loads(result.stdout)
    source=ReadSource(tmp_path,bridge=NS(runner=runner),profile_resolver=lambda:profile,
                      store_factory=lambda p:store,clock=lambda:now)
    return source,store,profile,now,trace


def test_owned_fake_cli_and_native_quota_keep_identity_and_time_sources(tmp_path,monkeypatch):
    source,store,profile,now,trace=source_fixture(tmp_path,monkeypatch)
    before={p.name:p.read_bytes() for p in store.path.parent.iterdir()}
    value=source.read()
    assert value['managed']=={'status':'known','defaultAccountId':'managed-a','accountIds':['managed-a','managed-b']}
    assert value['native']['status']=='known' and value['native']['accountId']=='native-account'
    assert value['native']['windows']['fiveHour']['usedPercent']==25
    assert value['native']['windows']['week']['resetsAt']==now+86400
    assert value['native']['observedAt']==value['native']['receivedAt']==now
    assert value['native']['providerUpdatedAt'] is None
    assert value['policySnapshot']['status']=='unknown' and value['profileBindings']=={}
    assert value['binding']=='unknown' and not source.current(value)
    assert 'fixture-secret' not in json.dumps(value) and 'private-label' not in json.dumps(value)
    assert before=={p.name:p.read_bytes() for p in store.path.parent.iterdir()}
    assert json.loads(trace.read_text(encoding='utf-8'))==['auth','status','--json']


@pytest.mark.parametrize('bad',['partial','stale','stale_receipt','future','missing_reset','wrong_duration','profile_changed','missing_cache','query_failure'])
def test_read_source_partial_stale_failure_unknown_without_guess(tmp_path,monkeypatch,bad):
    source,store,profile,now,trace=source_fixture(tmp_path,monkeypatch)
    if bad=='partial':store.update({'primary':limits(now)['primary']},observed_at=stamp(now+1),received_at=stamp(now+1));source.clock=lambda:now+1
    elif bad=='stale':source.clock=lambda:now+301
    elif bad=='future':source.clock=lambda:now-1
    elif bad=='stale_receipt':store.update(limits(now),observed_at=stamp(now),received_at=stamp(now-301))
    elif bad=='missing_reset':
        raw=limits(now);raw['secondary'].pop('resetsAt');store.update(raw,observed_at=stamp(now),received_at=stamp(now))
    elif bad=='wrong_duration':
        raw=limits(now);raw['secondary']['windowDurationMins']=1440;store.update(raw,observed_at=stamp(now),received_at=stamp(now))
    elif bad=='profile_changed':
        calls=[]
        def resolve():
            calls.append(True);return profile if len(calls)==1 else CodexProfile(profile.home,'other','other','other')
        source.resolve=resolve
    elif bad=='missing_cache':store.path.unlink()
    else:
        def failed(args):raise RuntimeError('credential fixture-secret')
        source.bridge.runner=failed
    value=source.read();assert value['policySnapshot']['status']=='unknown'
    if bad!='query_failure':assert value['native']['status']=='unknown'
    else:assert value['managed']['status']=='unknown'
    assert 'fixture-secret' not in json.dumps(value)


class Source:
    """Trusted fixture canonical binding, not claimed as an existing GUI API."""
    def __init__(self,now):self.now=now;self.account='a';self.version=0
    def read(self):
        return {'observedAt':self.now(),'binding':'verified-fixture','managed':{},'native':{},'reason':None,
                'version':self.version,'profileBindings':{'profile-a':'a','profile-b':'b'},
                'policySnapshot':{'status':'known','observedAt':self.now(),'currentAccountId':self.account,
                    'accounts':[{'id':i,'fiveHour':{'usedPercent':10,'resetsAt':self.now()+200},
                                 'week':{'usedPercent':20,'resetsAt':self.now()+300}} for i in ('a','b')]}}
    def current(self,obs):return obs['version']==self.version and obs['policySnapshot']['currentAccountId']==self.account


def objects():
    w=NS(worker_id='w',session_id='s',generation=1,offline_token='tok',status='running',
         process=NS(pid=100,birth='1000',alive=True),adapter=NS(name='codex'),codex_profile_key='profile-a',
         _current_task_id='task',_execution_seq=7,_terminal_finishing=False,_host_exit_illegal=False)
    s=NS(id='s',name='MA by misleading name',active_task_id='task',readonly_session=False,queue_paused=False,
         queue_edit_locks={},queue_pending=[],terminal_results=[],execution_operations={
             'run':{'serviceId':'owner','workerId':'w','generation':1,'token':'tok','executionSeq':7,'state':'running'}})
    module=NS(workers={'w':w},_sess=NS(_cache={'s':s}),
              _offline=NS(SERVICE_ID='owner',is_illegal=lambda session:any(op.get('state')=='illegal' for op in session.execution_operations.values())))
    return module,w,s


def probe(process):return {'pid':process.pid,'birthToken':process.birth} if process and process.alive else None


async def setup(tmp_path,port=None,enabled=True):
    module,w,s=objects();source=Source(time.time);bindings=Bindings(module,probe=probe)
    rt=Runtime(tmp_path/'automation',tmp_path/'jobs')
    rt.store.configure('automatic','ma',0)
    rt.store.set_host({'enabled':enabled,'sessionRoles':{'s':'ta'}},rt.store.read()['revision'])
    adapter=HostAdapter(rt.store,source,bindings,ports={'continue_once':port} if port else None)
    rt.adapter=adapter;adapter.runtime=rt
    event={'issueId':'i','classification':'codex_capacity','confidence':'structured',
           'correlation':{'runtimeEpoch':'owner','sessionId':'s','workerId':'w','generation':1,'taskSeq':1}}
    return rt,adapter,module,w,s,source,event


@async_test
async def test_default_off_performs_no_source_or_process_io(tmp_path):
    rt,a,module,w,s,source,event=await setup(tmp_path,enabled=False)
    def forbidden(*args):raise AssertionError('Must not reach host IO')
    source.read=forbidden;a.bindings.probe=forbidden
    assert (await rt.consume(event))['status']=='unknown'
    with pytest.raises(Unavailable):await a.query()
    assert not rt.store.read()['commands']


@async_test
async def test_real_projection_explicit_role_process_and_unfinished_task(tmp_path):
    rt,a,module,w,s,source,event=await setup(tmp_path)
    inventory=await a.inventory();row=inventory['workers'][0]
    assert row['runtimeEpoch']=='owner' and row['processIdentity']=={'pid':100,'birthToken':'1000'}
    assert row['role']=='ta' and row['task']=={'taskId':'task','executionSeq':7,'state':'unfinished'}
    rt.store.set_host({'enabled':True,'sessionRoles':{}},rt.store.read()['revision'])
    assert (await a.inventory())['workers'][0]['role'] is None  # no name inference
    s.terminal_results=[{'taskId':'task','executionSeq':7,'generation':1,'status':'done'}]
    assert (await a.inventory())['workers'][0]['task'] is None


@async_test
async def test_probe_await_fences_replaced_worker(tmp_path):
    rt,a,module,w,s,source,event=await setup(tmp_path);entered=threading.Event();release=threading.Event()
    def waiting(process):entered.set();assert release.wait(5);return probe(process)
    a.bindings.probe=waiting;task=asyncio.create_task(a.inventory())
    assert await asyncio.to_thread(entered.wait,5)
    w.generation+=1;release.set()
    assert (await task)['workers']==[]


@async_test
async def test_guarded_business_admission_positive_and_unknown_not_replayed(tmp_path):
    effects=[]
    async def port(command,key,admission):
        return admission.commit(lambda:effects.append(key) or {'status':'unknown','key':key})
    rt,a,module,w,s,source,event=await setup(tmp_path,port)
    await rt.consume(event);await rt.dispatch()
    assert len(effects)==1 and next(iter(rt.store.read()['commands'].values()))['status']=='uncertain'
    await rt.recover();await rt.dispatch();assert len(effects)==1


@pytest.mark.parametrize('change',['generation','birth','pid','task','execution','completed','owner','account',
                                   'pause','illegal','lease','readonly','queue_lock','disabled','manual','close','host_off',
                                   'offline','missing_task','role'])
@async_test
async def test_terminal_admission_rechecks_after_business_entry_await(tmp_path,change):
    entered=asyncio.Event();release=asyncio.Event();effects=[]
    async def port(command,key,admission):
        entered.set();await release.wait()
        return admission.commit(lambda:effects.append(key) or {'status':'confirmed','key':key})
    rt,a,module,w,s,source,event=await setup(tmp_path,port)
    await rt.consume(event);task=asyncio.create_task(rt.dispatch());await asyncio.wait_for(entered.wait(),5)
    if change=='generation':w.generation+=1
    elif change=='birth':w.process.birth='new'
    elif change=='pid':w.process.pid=999
    elif change=='task':w._current_task_id=s.active_task_id='different'
    elif change=='execution':w._execution_seq+=1
    elif change=='completed':s.active_task_id=None
    elif change=='owner':module._offline.SERVICE_ID='another-owner'
    elif change=='account':source.account='b';source.version+=1
    elif change=='pause':s.queue_paused=True
    elif change=='illegal':s.execution_operations['run']['state']='illegal'
    elif change=='lease':s.queue_edit_locks['x']={'expiresAt':time.time()+100}
    elif change=='readonly':s.readonly_session=True
    elif change=='queue_lock':s.queue_pending=[{'locked':True}]
    elif change=='disabled':rt.store.set_disabled(['a'],rt.store.read()['revision'])
    elif change=='manual':rt.store.configure('manual','ma',rt.store.read()['revision'])
    elif change=='close':rt.closed=True
    elif change=='offline':module.workers.clear()
    elif change=='missing_task':s.execution_operations.clear()
    elif change=='role':rt.store.set_host({'enabled':True,'sessionRoles':{}},rt.store.read()['revision'])
    else:rt.store.set_host({'enabled':False,'sessionRoles':{'s':'ta'}},rt.store.read()['revision'])
    release.set();await task
    assert not effects


@async_test
async def test_unbound_production_operations_do_not_call_legacy_entries(tmp_path):
    rt,a,module,w,s,source,event=await setup(tmp_path)
    await rt.consume(event);await rt.dispatch()
    assert next(iter(rt.store.read()['commands'].values()))['status']=='blocked'
    with pytest.raises(ValueError):HostAdapter(rt.store,source,a.bindings,ports={'arbitrary_api':lambda:None})


@async_test
async def test_unrelated_session_progresses_while_entry_waits(tmp_path):
    entered=asyncio.Event();release=asyncio.Event();effects=[]
    async def port(command,key,admission):
        entered.set();await release.wait()
        return admission.commit(lambda:effects.append(key) or {'status':'confirmed','key':key})
    rt,a,module,w,s,source,event=await setup(tmp_path,port)
    await rt.consume(event);task=asyncio.create_task(rt.dispatch());await asyncio.wait_for(entered.wait(),5)
    from packages.core.worker import queue_lock
    async with queue_lock('unrelated-fixture-session'):unrelated=True
    assert unrelated and not effects
    release.set();await task;assert len(effects)==1


def test_native_birth_probe_only_owned_retained_process(tmp_path):
    if sys.platform!='win32':pytest.skip('retained FILETIME requires Windows')
    proc=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,**command_window_options())
    try:
        first=retained_process(proc);second=retained_process(proc)
        assert first==second and first['pid']==proc.pid and int(first['birthToken'])>0
        assert retained_process(NS(pid=proc.pid)) is None  # no PID-only inference
    finally:proc.terminate();proc.wait(timeout=5)
    assert retained_process(proc) is None


@pytest.mark.parametrize('mode',['double','exception','no_commit'])
@async_test
async def test_business_port_single_acceptance_and_uncertain_nonreplay(tmp_path,mode):
    effects=[]
    async def port(command,key,ticket):
        if mode=='no_commit':return {'key':key,'status':'confirmed'}
        ticket.commit(lambda:effects.append(key))
        if mode=='double':
            with pytest.raises(Unavailable):ticket.commit(lambda:effects.append(key))
        else:raise OSError('sensitive fixture error')
        return {'key':key,'status':'confirmed'}
    rt,a,module,w,s,source,event=await setup(tmp_path,port)
    await rt.consume(event)
    if mode=='exception':
        with pytest.raises(RuntimeError,match='host_effect_outcome_unknown'):await rt.dispatch()
    else:await rt.dispatch()
    c=next(iter(rt.store.read()['commands'].values()))
    assert c['status']=={'double':'completed','exception':'uncertain','no_commit':'blocked'}[mode]
    await rt.recover();await rt.dispatch()
    assert len(effects)==(0 if mode=='no_commit' else 1)


@async_test
async def test_terminal_store_contention_rejects_without_blocking_event_loop(tmp_path):
    from packages.automation.quota_policy import _LOCK
    entered=asyncio.Event();release=asyncio.Event();held=threading.Event();unlock=threading.Event();effects=[]
    async def port(command,key,ticket):
        entered.set();await release.wait()
        return ticket.commit(lambda:effects.append(key) or {'key':key,'status':'confirmed'})
    rt,a,module,w,s,source,event=await setup(tmp_path,port)
    await rt.consume(event);task=asyncio.create_task(rt.dispatch());await asyncio.wait_for(entered.wait(),5)
    def writer():
        with _LOCK:
            held.set();assert unlock.wait(5)
    thread=threading.Thread(target=writer);thread.start()
    try:
        assert await asyncio.to_thread(held.wait,5)
        release.set()
        # dispatch's durable finish waits off-loop; terminal admission must return
        # immediately so this callback still runs while the writer owns the gate.
        await asyncio.wait_for(asyncio.sleep(.01),.5)
        assert not effects
    finally:unlock.set();await asyncio.to_thread(thread.join,5)
    await task
    assert next(iter(rt.store.read()['commands'].values()))['status']=='blocked'


@async_test
async def test_injected_switch_restart_idle_continue_chain(tmp_path):
    effects=[]
    rt,a,module,w,s,source,event=await setup(tmp_path)
    async def port(command,key,ticket):
        op=command['operation'];args=command['arguments']
        def accept():
            effects.append(op)
            if op=='switch_account':
                source.account=args['targetAccountId'];source.version+=1
            elif op=='restart_worker':
                # The trusted fixture business entry supplies actual successor
                # execution evidence. Production restart currently cannot do so.
                w.generation+=1;w.offline_token='successor';w.status='idle';w.codex_profile_key='profile-b'
                w.process=NS(pid=101,birth='1001',alive=True)
                s.execution_operations['run'].update(generation=w.generation,token=w.offline_token)
            return {'key':key,'status':'confirmed'}
        receipt=ticket.commit(accept)
        if op=='restart_worker':receipt['successor']=(await a.inventory())['workers'][0]
        return receipt
    a.ports={op:port for op in ('switch_account','restart_worker','wake_ta')}
    event['classification']='codex_quota_exhausted'
    await rt.consume(event);await rt.dispatch();await rt.dispatch()
    assert effects==['switch_account','restart_worker'] and w.status=='idle'
    await rt.progress_restarts();await rt.dispatch()
    assert effects==['switch_account','restart_worker','wake_ta']
    await rt.recover();await rt.progress_restarts();await rt.dispatch()
    assert effects.count('wake_ta')==1
