"""Default-off host projection; production mutation ports deliberately unbound.

No import-time CLI, auth, process scan, provider call or background loop.
"""
import asyncio
import copy
import inspect
import json
import math
import threading
import time
from datetime import datetime
from pathlib import Path

from packages.plugins.lifecycle import io
from .quota_policy import Adapter, Unavailable, normalize
from . import quota_identity as identity

OPERATIONS = frozenset({'switch_account','cbc_next_model','restart_worker','restart_ma',
                        'continue_once','wake_ta','wake_ma','ma_resume_job'})


def timestamp(value):
    if type(value) in (int,float):return value if math.isfinite(value) else None
    if not isinstance(value,str):return None
    try:
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        return parsed.timestamp() if parsed.tzinfo else None
    except (ValueError,OverflowError):return None


class ReadSource:
    """Only existing read interfaces. Never infer managed-ID/native-ID binding."""
    def __init__(self, root, bridge=None, profile_resolver=None, store_factory=None, clock=time.time):
        from packages.cc_switch_bridge.provider import Bridge
        from packages.core.codex_quota_store import resolve_profile_identity, CodexQuotaStore
        root=Path(root).resolve();directory=(root/'plugins'/'cc-switch-bridge').resolve()
        if directory.parent!=(root/'plugins').resolve():raise ValueError('Bridge data path escaped profile')
        self.bridge=bridge or Bridge(directory)
        self.resolve=profile_resolver or resolve_profile_identity
        self.store_factory=store_factory or CodexQuotaStore
        self.clock=clock

    def read(self):
        from packages.core.codex_quota import normalize_codex_rate_limits
        at=self.clock();managed={'status':'unknown','defaultAccountId':None,'accountIds':[]}
        try:
            self.bridge._deadline=time.monotonic()+8
            # Deliberately not provider quota, auth default, or guessed auth quota.
            raw=self.bridge.runner(['auth','status','--json'])
            rows=raw.get('accounts')
            ids=[r.get('id') for r in rows] if isinstance(rows,list) else []
            if (0<len(ids)<=128 and all(identity.identifier(i) for i in ids) and len(set(ids))==len(ids)
                    and raw.get('default_account_id') in ids):
                managed={'status':'known','defaultAccountId':raw['default_account_id'],'accountIds':ids}
        except Exception:pass  # No raw CLI errors, login labels or credentials.
        native={'status':'unknown','accountId':None,'profileKey':None,'windows':{}}
        try:
            profile=self.resolve();store=self.store_factory(profile)
            # Existing store format, atomic replace writer: no cross-process lock
            # creation/wait and no credential/cache write on this read path.
            from packages.core.codex_quota_store import _normalize_legacy_record
            try:
                with store.path.open('rb') as stream:payload=stream.read(2*1024*1024+1)
                record=_normalize_legacy_record(json.loads(payload),profile.profile_key) if len(payload)<=2*1024*1024 else None
            except (OSError,ValueError):record=None
            after=self.resolve()
            if (profile.profile_key==after.profile_key and identity.identifier(profile.account_id)
                    and identity.identifier(profile.profile_key) and isinstance(record,dict)
                    and record.get('profileKey')==profile.profile_key):
                observed=timestamp(record.get('observedAt'));received=timestamp(record.get('receivedAt'))
                fresh=(type(observed) in (int,float) and 0<=at-observed<=300
                       and type(received) in (int,float) and 0<=at-received<=300)
                # The latest raw snapshot, not merged last-good old windows.
                projected=normalize_codex_rate_limits(record.get('raw'))['windows']
                if len(projected)>32:raise ValueError('Window capacity')
                windows={}
                for w in projected.values():
                    kind={'five_hour':'fiveHour','weekly':'week'}.get(w['kind'])
                    if kind:
                        if kind in windows:raise ValueError('Ambiguous window')
                        used=w['usage']['usedPercent']
                        used=used if type(used) in (int,float) and math.isfinite(used) and 0<=used<=100 else None
                        windows[kind]={'usedPercent':used,'resetsAt':timestamp(w['resetsAt']),
                                       'durationSeconds':w['windowDurationSeconds'],'providerKey':w['providerKey'][:64]}
                check=normalize({'status':'known','observedAt':observed,'currentAccountId':profile.account_id,
                                 'accounts':[{'id':profile.account_id,**windows}]},at)
                native={'status':'known' if fresh and check['status']=='known' else 'unknown',
                        'accountId':profile.account_id,'profileKey':profile.profile_key,'windows':windows,
                        'observedAt':observed,'receivedAt':received,
                        'source':record.get('source') if record.get('source') in ('app-server-push','wham-usage') else 'cache',
                        'providerUpdatedAt':None,'timestampMeaning':'local Pan observation/receipt, not provider update'}
        except Exception:pass
        return {'observedAt':at,'managed':managed,'native':native,'binding':'unknown',
                'reason':'managed_native_binding_and_all_account_windows_unavailable',
                'policySnapshot':{'status':'unknown'},'profileBindings':{}}

    def current(self, observation):
        # No external write CAS/account binding in the existing production API.
        return False


def retained_process(process):
    """Probe only the Worker-owned retained Popen handle; never trust bare PID."""
    from packages.core.terminal.identity import probe_handle
    from packages.core.terminal.contracts import ProcessStatus
    try:
        popen=process
        if not getattr(popen,'_handle',None):
            transport=getattr(process,'_transport',None)
            popen=transport.get_extra_info('subprocess') if transport else None
        handle=getattr(popen,'_handle',None);pid=getattr(process,'pid',None)
        if not handle or type(pid) is not int:return None
        probe=probe_handle(int(handle),pid)
        if probe.status is ProcessStatus.ALIVE and probe.identity and probe.identity.created_at_filetime is not None:
            return {'pid':pid,'birthToken':str(probe.identity.created_at_filetime)}
    except Exception:pass
    return None


class Bindings:
    """Projection of the existing owner/registered Workers and active Sessions."""
    def __init__(self, worker, probe=retained_process, clock=time.time):
        self.worker=worker;self.probe=probe;self.clock=clock

    @property
    def epoch(self):return self.worker._offline.SERVICE_ID

    def task(self,w,s):
        task_id=getattr(w,'_current_task_id',None);seq=getattr(w,'_execution_seq',None)
        if not identity.identifier(task_id) or task_id!=getattr(s,'active_task_id',None) or type(seq) is not int or seq<1:return None
        if getattr(w,'_terminal_finishing',False):return None
        if any(r.get('taskId')==task_id and r.get('executionSeq')==seq and r.get('generation')==w.generation
               for r in getattr(s,'terminal_results',[])):return None
        for op in getattr(s,'execution_operations',{}).values():
            if (op.get('serviceId')==self.epoch and op.get('workerId')==w.worker_id
                    and op.get('generation')==w.generation and op.get('token')==w.offline_token
                    and op.get('executionSeq')==seq and op.get('state')=='running'
                    and not op.get('safeTerminal') and not op.get('retirement',{}).get('safe')):
                return {'taskId':task_id,'executionSeq':seq,'state':'unfinished'}
        return None

    def row(self,w,s,roles,accounts,process):
        return {'runtimeEpoch':self.epoch,'sessionId':w.session_id,'workerId':w.worker_id,
                'generation':w.generation,'adapter':getattr(w.adapter,'name',None),
                'accountId':accounts.get(getattr(w,'codex_profile_key',None)),
                'role':roles.get(w.session_id),'status':w.status,'alive':process is not None,
                'processIdentity':process,'task':self.task(w,s)}

    def session(self,sid):return self.worker._sess._cache.get(sid)

    async def inventory(self, roles, accounts, current):
        epoch=self.epoch;rows=[];semaphore=asyncio.Semaphore(8)
        async def capture(w):
            s=self.session(w.session_id)
            if s is None:return None
            process=w.process;generation=w.generation;token=w.offline_token
            async with semaphore:proof=await io(self.probe,process)
            if (self.epoch!=epoch or self.worker.workers.get(w.worker_id) is not w or w.process is not process
                    or w.generation!=generation or w.offline_token!=token):return None
            return self.row(w,s,roles,accounts,proof)
        values=list(self.worker.workers.values())
        if len(values)>256:raise Unavailable('worker_inventory_capacity')
        for result in await asyncio.gather(*(capture(w) for w in values)):
            if result:rows.append(result)
        if self.epoch!=epoch:raise Unavailable('runtime_owner_changed')
        return {'runtimeEpoch':epoch,'observedAt':self.clock(),'currentAccountId':current,'workers':rows}

    def validate_now(self, inventory, command, roles, accounts):
        """At terminal admission: native zero-time probe + in-owner state, no await."""
        if self.epoch!=inventory['runtimeEpoch']:raise Unavailable('runtime_owner_changed')
        args=command['arguments'];target=args.get('worker')
        scope=[target] if target else args.get('workers',[])
        if not scope:raise Unavailable('effect_scope_unknown')
        for frozen in scope:
            expected=identity.current(inventory,frozen)
            if expected is None:raise Unavailable('frozen_scope_not_in_inventory')
            if expected['role']!=frozen['role'] or expected['task']!=frozen['task']:
                raise Unavailable('frozen_task_or_role_changed')
            w=self.worker.workers.get(frozen['workerId']);s=self.session(frozen['sessionId'])
            if w is None or s is None:raise Unavailable('worker_offline')
            current=self.row(w,s,roles,accounts,self.probe(w.process))
            if (not identity.frozen(current) or not identity.same_worker(expected,current)
                    or current['role']!=expected['role'] or current['task']!=expected['task']
                    or current['status']!=expected['status']):raise Unavailable('worker_task_changed')
            if (getattr(s,'readonly_session',False) or getattr(s,'queue_paused',False)
                    or self.worker._offline.is_illegal(s) or getattr(w,'_host_exit_illegal',False)
                    or w.status in ('held','restarting')):raise Unavailable('business_gate')
            if any(isinstance(v,dict) and v.get('expiresAt',0)>self.clock()
                   for v in getattr(s,'queue_edit_locks',{}).values()):raise Unavailable('edit_lease')
            if any(r.get('queueLockSource') or r.get('locked') or r.get('queueSteerPending')
                   for r in getattr(s,'queue_pending',[])):raise Unavailable('queue_gate')


class Admission:
    def __init__(self, adapter, command, observation, inventory, host):
        self.adapter=adapter;self.command=command;self.observation=observation
        self.inventory=inventory;self.host=host;self.used=False;self.thread=threading.get_ident()

    def commit(self, effect):
        if self.used or threading.get_ident()!=self.thread or inspect.iscoroutinefunction(effect):
            raise Unavailable('invalid_or_reused_admission')
        a=self.adapter;c=self.command
        def validate(state):
            a.validate_policy(c,state)
            if state['host']!=self.host or not a.source.current(self.observation):raise Unavailable('host_account_changed')
            if not identity.valid_inventory(self.inventory,a.clock()):raise Unavailable('inventory_expired')
            a.bindings.validate_now(self.inventory,c,self.host['sessionRoles'],self.observation['profileBindings'])
        def execute():
            self.used=True
            result=effect()
            if inspect.isawaitable(result):
                if inspect.iscoroutine(result):result.close()
                raise RuntimeError('Business admission must not await')
            return result
        return a.store.admit_now(validate,execute)


class HostAdapter(Adapter):
    """Effects are host-injected fixed business ports, never API/config payloads."""
    def __init__(self, store, source, bindings, ports=None, clock=time.time):
        self.store=store;self.source=source;self.bindings=bindings;self.ports=dict(ports or {})
        if set(self.ports)-OPERATIONS:raise ValueError('Unknown host operation')
        self.clock=clock;self.runtime=None;self.observation=None;self.last_inventory=None

    async def enabled(self):
        state=await io(self.store.read)
        if not state['host']['enabled']:raise Unavailable('quota_host_disabled')
        return state

    async def sample(self):
        state=await self.enabled();observed=await io(self.source.read)
        def publish(s):
            if s['host']!=state['host'] or s['epoch']!=state['epoch']:raise Unavailable('host_configuration_changed')
            # ReadSource returns only whitelisted non-secret fields. Custom sources are trusted host code.
            s['hostObservation']={k:copy.deepcopy(observed[k]) for k in ('observedAt','managed','native','binding','reason') if k in observed}
        await io(self.store.change,publish)
        self.observation=observed
        return observed

    async def query(self):return (await self.sample())['policySnapshot']

    async def inventory(self):
        state=await self.enabled()
        observation=self.observation
        if observation is None or not 0<=self.clock()-observation['observedAt']<=5:observation=await self.sample()
        snapshot=normalize(observation['policySnapshot'],self.clock())
        self.last_inventory=await self.bindings.inventory(state['host']['sessionRoles'],observation['profileBindings'],snapshot['currentAccountId'])
        return self.last_inventory

    def validate_policy(self,command,state):
        current=state['commands'].get(command['id'])
        if (self.runtime is None or self.runtime.closed or not state['host']['enabled'] or state['mode']!='automatic'
                or command.get('contractVersion')!=2 or not current or current.get('contractVersion')!=2
                or command['epoch']!=state['epoch'] or command.get('accountConfigRevision')!=state['accountConfigRevision']
                or current['status']!='dispatching' or current['operation']!=command['operation']
                or current['arguments']!=command['arguments']):raise Unavailable('policy_cas_changed')
        account=command['arguments'].get('targetAccountId') if command['operation']=='switch_account' else command['arguments'].get('expectedAccountId')
        if account in state['disabledAccountIds']:raise Unavailable('account_disabled')
        args=command['arguments'];reference=args.get('restartCommandId')
        if reference is not None:
            receipt=self.runtime.restart_proof(state,reference)
            if (not identity.recovery_eligible(args.get('original'),receipt,args.get('worker'),args.get('expectedAccountId'))
                    or (command['operation']=='wake_ta' and receipt['caseId']!=command['caseId'])):
                raise Unavailable('restart_receipt_changed')
        elif command['operation']=='wake_ta':raise Unavailable('restart_receipt_missing')

    async def perform(self,command,key):
        if command['operation'] not in self.ports:raise Unavailable('production_business_admission_unbound')
        if key!=command['id']:raise Unavailable('command_key_mismatch')
        state=await io(self.store.refresh_admission);self.validate_policy(command,state)
        status,_=await self.runtime.preflight(command,state)
        if status!='ok':raise Unavailable('host_preflight_rejected')
        inventory=copy.deepcopy(self.last_inventory);observation=copy.deepcopy(self.observation)
        if inventory is None or observation is None:raise Unavailable('host_identity_unknown')
        admission=Admission(self,command,observation,inventory,copy.deepcopy(state['host']))
        try:receipt=await self.ports[command['operation']](copy.deepcopy(command),key,admission)
        except BaseException as exc:
            if admission.used:raise RuntimeError('host_effect_outcome_unknown') from None
            if isinstance(exc,asyncio.CancelledError):raise
            raise Unavailable('business_admission_rejected') from None
        finally:
            # Even a confirmed account change invalidates the pre-effect sample.
            # A following restart must observe the new account, never the cache.
            if admission.used:self.observation=None
        if not admission.used:raise Unavailable('business_entry_did_not_admit')
        return receipt


def production(store, worker, root):
    return HostAdapter(store,ReadSource(root),Bindings(worker))
