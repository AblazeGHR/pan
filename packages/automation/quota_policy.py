"""Durable quota policy with a fail-closed, host-only effects adapter.

Uses the existing scheduled-task registry/tick. No account CLI, provider request,
restart or Session send is performed by the default adapter.
"""
import asyncio
import copy
import hashlib
import json
import math
import threading
import time
from pathlib import Path

from packages.actions.store import atomic
from packages.core import background_jobs as jobs
from packages.plugins.lifecycle import io
from .store import Conflict
from . import quota_identity as identity

KINDS = {'codex_quota_exhausted', 'codex_capacity', 'cbc_rate_limited'}
MAX_AGE = 300
MAX_RECORDS = 4096
MAX_DISABLED_ACCOUNTS = 128
CONTRACT_VERSION = 2
_LOCK = threading.RLock()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,allow_nan=False).encode()).hexdigest()


class Unavailable(RuntimeError):
    pass


class Adapter:
    """Trusted host implementation must implement identity, gates and receipts.

    A returned confirmed receipt must identify the same durable command key.
    An exception/timeout or unknown receipt is never replayed automatically.
    Contract v2 requires fresh runtime/process/task identity and CAS of the command's
    accountConfigRevision at actual effect admission. Restart must never start an
    offline target; confirmed restart receipts include a verifiable successor.
    """
    async def query(self):
        raise Unavailable('Managed account five-hour/week query adapter unavailable')

    async def inventory(self):
        raise Unavailable('Atomic account/Session/Worker inventory unavailable')

    async def perform(self, command, key):
        raise Unavailable('Guarded account/Session/Job effects adapter unavailable')


def normalize(snapshot, now):
    """Discard arbitrary adapter fields; do not promote stale/error quota to zero."""
    unknown = {'status':'unknown','observedAt':now,'currentAccountId':None,'accounts':[]}
    if not isinstance(snapshot,dict) or snapshot.get('status')!='known':return unknown
    at=snapshot.get('observedAt')
    if type(at) not in (int,float) or not math.isfinite(at) or not 0<=now-at<=MAX_AGE:return unknown
    rows=snapshot.get('accounts');current=snapshot.get('currentAccountId')
    if not isinstance(rows,list) or not 1<=len(rows)<=128:return unknown
    accounts=[];ids=set()
    for row in rows:
        if not isinstance(row,dict):return unknown
        aid=row.get('id')
        if not isinstance(aid,str) or not 1<=len(aid)<=256 or aid in ids:return unknown
        ids.add(aid);result={'id':aid}
        for name in ('fiveHour','week'):
            window=row.get(name)
            if not isinstance(window,dict):return unknown
            used=window.get('usedPercent');reset=window.get('resetsAt')
            if (type(used) not in (int,float) or not math.isfinite(used) or not 0<=used<=100
                    or type(reset) not in (int,float) or not math.isfinite(reset) or reset<=now):return unknown
            result[name]={'usedPercent':used,'resetsAt':reset}
        accounts.append(result)
    if current not in ids:return unknown
    return {'status':'known','observedAt':at,'currentAccountId':current,'accounts':accounts}


def rank(snapshot, excluded=(), disabled=()):
    return sorted((r for r in snapshot['accounts'] if r['id'] not in excluded and r['id'] not in disabled
                   and r['fiveHour']['usedPercent']<100 and r['week']['usedPercent']<100),
                  key=lambda r:(r['week']['resetsAt'],r['week']['usedPercent'],r['fiveHour']['usedPercent'],r['id']))


class Store:
    def __init__(self, root):self.path=Path(root)/'quota-policy.json'

    def read(self):
        if not self.path.exists():
            return {'version':2,'revision':0,'epoch':0,'mode':'manual','maSessionId':None,
                    'seen':{},'cases':{},'excluded':[],'capacityRetried':[], 'commands':{},
                    'quota':{'status':'unknown','accounts':[]},'jobs':{},
                    'disabledAccountIds':[],'accountConfigRevision':0}
        state=json.loads(self.path.read_text(encoding='utf-8'))
        state.setdefault('disabledAccountIds',[]);state.setdefault('accountConfigRevision',0)
        self.validate_disabled(state['disabledAccountIds'])
        if type(state['accountConfigRevision']) is not int or state['accountConfigRevision']<0:
            raise ValueError('Invalid account configuration revision')
        state['version']=2
        return state

    @staticmethod
    def validate_disabled(ids):
        if (not isinstance(ids,list) or len(ids)>MAX_DISABLED_ACCOUNTS
                or not all(identity.identifier(aid) for aid in ids) or len(set(ids))!=len(ids)):
            raise ValueError('Expected unique disabledAccountIds, at most128 nonempty IDs <=256 chars')

    def set_disabled(self, ids, expected):
        self.validate_disabled(ids)
        if type(expected) is not int:raise ValueError('Expected integer expectedRevision')
        def update(s):
            if expected!=s['revision']:raise Conflict('Quota policy revision changed')
            if sorted(ids)!=sorted(s['disabledAccountIds']):
                s['disabledAccountIds']=sorted(ids);s['accountConfigRevision']+=1;s['epoch']+=1
                for c in s['commands'].values():
                    if c['status'] in ('ready','blocked'):c['status']='cancelled'
            return {'disabledAccountIds':s['disabledAccountIds'],'accountConfigRevision':s['accountConfigRevision'],
                    'epoch':s['epoch']}
        return self.change(update)

    def change(self, fn):
        with _LOCK:
            state=self.read();before=copy.deepcopy(state);result=fn(state)
            self.validate_disabled(state['disabledAccountIds'])
            if any(len(state[k])>MAX_RECORDS for k in ('seen','cases','commands','excluded','capacityRetried')):
                raise ValueError('Quota policy ledger capacity reached')
            if state==before:return copy.deepcopy(result)
            state['revision']+=1;atomic(self.path,state)
            return copy.deepcopy(result)

    def configure(self, mode, ma, expected):
        if mode not in ('manual','automatic') or not isinstance(ma,str) or not 1<=len(ma)<=128:
            raise ValueError('Expected mode manual/automatic and MA Session ID')
        def update(s):
            if type(expected) is not int or s['revision']!=expected:raise ValueError('Quota policy revision changed')
            s.update(mode=mode,maSessionId=ma,epoch=s['epoch']+1)
            for c in s['commands'].values():
                if c['status'] in ('ready','blocked'):c['status']='cancelled'
            return {'mode':mode,'epoch':s['epoch']}
        return self.change(update)


def protected(job):
    return isinstance(job,dict) and (job.get('action') or {}).get('api')=='quota_query'


class Runtime:
    def __init__(self, root, job_root, adapter=None, clock=time.time):
        self.store=Store(root);self.root=Path(job_root);self.adapter=adapter or Adapter();self.clock=clock
        self.pending=asyncio.Queue(128);self.closed=False;self.gate=asyncio.Lock()
        self.diagnostics=[];self.monitor_lock=threading.RLock()

    def observe(self, mapping):
        if self.closed:return
        try:self.pending.put_nowait(mapping['payload'] if mapping.get('type')=='runtime.issue.v1' else mapping['issue'])
        except (asyncio.QueueFull,KeyError):
            self.diagnostics=(self.diagnostics+[{'phase':'issue-ingress','status':'full'}])[-100:]

    async def query(self):
        try:raw=await asyncio.wait_for(self.adapter.query(),10);snapshot=normalize(raw,self.clock())
        except Exception:snapshot={'status':'unknown','observedAt':self.clock(),'currentAccountId':None,'accounts':[]}
        await io(self.store.change,lambda s:s.update(quota=snapshot))
        return snapshot

    def command(self,s,case,operation,**args):
        key=fingerprint([CONTRACT_VERSION,s['epoch'],case,operation,args])
        s['commands'].setdefault(key,{'id':key,'caseId':case,'epoch':s['epoch'],'operation':operation,
                                     'arguments':args,'status':'ready','contractVersion':CONTRACT_VERSION,
                                     'accountConfigRevision':s['accountConfigRevision']})
        return key

    async def consume(self, issue):
        # Only native/adapter parser classifications enter this trusted method.
        kind=issue.get('classification');corr=issue.get('correlation') or {}
        if self.closed:return {'status':'closed'}
        if kind not in KINDS or issue.get('confidence') not in ('structured','adapter_text'):return {'status':'ignored'}
        if not corr.get('sessionId') or not corr.get('workerId') or type(corr.get('generation')) is not int:
            return {'status':'unknown','reason':'missing runtime identity'}
        # Provider error frame + terminal result are one operation observation.
        correlation={k:corr.get(k) for k in ('runtimeEpoch','sessionId','workerId','generation','taskSeq','executionSeq','queueItemId')}
        if not any(correlation[k] is not None for k in ('taskSeq','executionSeq','queueItemId')):
            correlation['turnId']=corr.get('turnId')
            if not correlation['turnId']:return {'status':'unknown','reason':'missing operation identity'}
        cid=fingerprint([correlation,kind])
        state=await io(self.store.read)
        if cid in state['seen']:return {'status':'duplicate','caseId':cid}
        if state['mode']!='automatic':return {'status':'manual'}
        inventory=await self.inventory()
        if inventory is None:return {'status':'unknown','reason':'inventory unavailable/identity unknown'}
        if not self.matches(inventory,corr,kind):return {'status':'unknown','reason':'stale/unbound Worker generation or account'}
        try:workers=self.workers(inventory)
        except ValueError:return {'status':'unknown','reason':'unverifiable live Worker inventory'}
        emitter=next(identity.worker(w,inventory['runtimeEpoch']) for w in inventory['workers'] if w.get('sessionId')==corr['sessionId'])
        snapshot=await self.query() if kind!='cbc_rate_limited' else None
        def admit(s):
            if self.closed:return {'status':'closed'}
            if s['mode']!='automatic' or s['epoch']!=state['epoch']:return {'status':'manual'}
            if cid in s['seen']:return {'status':'duplicate','caseId':cid}
            # An unknown read is not remembered as actionable and can be explicitly refreshed.
            if snapshot is not None and snapshot['status']!='known':return {'status':'unknown','reason':'quota unavailable/stale'}
            account=inventory.get('currentAccountId')
            if snapshot is not None and account!=snapshot['currentAccountId']:return {'status':'unknown','reason':'account changed during query'}
            s['seen'][cid]=issue['issueId'];s['cases'][cid]={'kind':kind,'correlation':correlation,'accountId':account,'status':'admitted'}
            if kind=='cbc_rate_limited':
                self.command(s,cid,'cbc_next_model',worker=emitter,runtimeEpoch=inventory['runtimeEpoch'])
                return {'status':'planned','caseId':cid}
            handover=[c for c in s['commands'].values() if c['operation'] in ('restart_worker','post_restart','wake_ta','ma_resume_job','restart_ma')
                      and c['status'] in ('ready','dispatching','uncertain','blocked') and c['epoch']==s['epoch']]
            if handover:
                for c in handover:
                    if c['operation'] in ('ma_resume_job','restart_ma') and c['status']=='ready':c['status']='blocked_new_capacity'
                s['cases'][cid]['status']='blocked_handover_review'
                return {'status':'blocked','caseId':cid}
            current=next(r for r in snapshot['accounts'] if r['id']==account)
            if any(c['operation']=='continue_once' and c['arguments'].get('expectedAccountId')==account
                   and c['status']!='completed' and c['epoch']==s['epoch'] for c in s['commands'].values()):
                s['cases'][cid]['status']='blocked_retry_unconfirmed';return {'status':'blocked','caseId':cid}
            if (kind=='codex_capacity' and account not in s['capacityRetried']
                    and account not in s['disabledAccountIds']
                    and current['fiveHour']['usedPercent']<100 and current['week']['usedPercent']<100):
                s['capacityRetried'].append(account)
                self.command(s,cid,'continue_once',worker=emitter,runtimeEpoch=inventory['runtimeEpoch'],expectedAccountId=account)
                return {'status':'retry_once','caseId':cid}
            if kind=='codex_capacity' and account not in s['excluded']:s['excluded'].append(account)
            candidates=rank(snapshot,[account,*s['excluded']],s['disabledAccountIds'])
            if not candidates:
                # The reset wake is a recheck, never a promise of capacity recovery.
                resets=[max(r['fiveHour']['resetsAt'] if r['fiveHour']['usedPercent']>=100 else self.clock(),
                            r['week']['resetsAt'] if r['week']['usedPercent']>=100 else self.clock())
                        for r in snapshot['accounts'] if r['id'] not in s['disabledAccountIds']]
                future=[at for at in resets if at>self.clock()]
                if future:
                    self.command(s,cid,'reset_wake_job',sessionId=s['maSessionId'],at=min(future),expectedAccountId=account,blockedWorkers=workers,runtimeEpoch=inventory['runtimeEpoch'])
                else:s['cases'][cid]['status']='blocked_no_candidate'
                return {'status':'waiting_reset' if future else 'blocked','caseId':cid}
            # One global switch at a time; no unrelated Session gate is held.
            if any(c['operation']=='switch_account' and c['status'] in ('ready','dispatching','uncertain','blocked') for c in s['commands'].values()):
                s['cases'][cid]['status']='blocked_switch_in_progress';return {'status':'blocked','caseId':cid}
            self.command(s,cid,'switch_account',expectedAccountId=account,targetAccountId=candidates[0]['id'],
                         workers=workers,maSessionId=s['maSessionId'],runtimeEpoch=inventory['runtimeEpoch'])
            return {'status':'switch_planned','caseId':cid}
        return await io(self.store.change,admit)

    @staticmethod
    def workers(inventory):
        result=[]
        for w in inventory.get('workers',[]):
            if w.get('adapter')!='codex':continue
            if not isinstance(w.get('status'),str):raise ValueError('Unknown Worker status')
            if w['status'] not in identity.LIVE:continue
            frozen=identity.worker(w,inventory.get('runtimeEpoch'))
            if frozen is None:raise ValueError('Unverifiable live Worker inventory')
            result.append(frozen)
        if len(result)>256:raise ValueError('Worker inventory capacity exceeded')
        return result

    @staticmethod
    def matches(inventory,corr,kind):
        adapter='cbc' if kind=='cbc_rate_limited' else 'codex'
        return corr.get('runtimeEpoch')==inventory.get('runtimeEpoch') and any(
            identity.worker(w,inventory['runtimeEpoch']) is not None
            and w.get('adapter')==adapter and all(w.get(k)==corr.get(k) for k in ('sessionId','workerId','generation'))
            and (adapter=='cbc' or (isinstance(w.get('accountId'),str) and w['accountId']==inventory.get('currentAccountId')))
            for w in inventory.get('workers',[]))

    async def inventory(self):
        try:value=await asyncio.wait_for(self.adapter.inventory(),5)
        except Exception:return None
        return value if identity.valid_inventory(value,self.clock()) else None

    async def preflight(self, command, state):
        """Read-only checks; the host must repeat CAS at actual effect admission."""
        if command.get('contractVersion')!=CONTRACT_VERSION:
            return 'blocked','legacy_identity_missing'
        args=command['arguments'];op=command['operation']
        if command.get('accountConfigRevision')!=state['accountConfigRevision']:
            return 'blocked','account_configuration_changed'
        target=args.get('targetAccountId') if op=='switch_account' else args.get('expectedAccountId')
        if target in state['disabledAccountIds'] and op!='reset_wake_job':
            return 'blocked','account_disabled'
        if op in ('reset_wake_job','monitor95_job'):
            if op=='monitor95_job' and not identity.wake_eligible(args.get('wakeTarget'),args.get('wakeTarget')):
                return 'blocked','frozen_ma_task_missing'
            return 'ok',''
        inventory=await self.inventory()
        if inventory is None:return 'blocked','inventory_unknown'
        if args.get('runtimeEpoch')!=inventory['runtimeEpoch']:
            return 'blocked','runtime_epoch_changed'
        if op!='cbc_next_model' and inventory.get('currentAccountId')!=args.get('expectedAccountId'):
            return 'blocked','account_changed'
        if op=='switch_account':
            snapshot=await self.query()
            refreshed=await self.inventory()
            latest=await io(self.store.read)
            if (snapshot['status']!='known' or snapshot['currentAccountId']!=args['expectedAccountId']
                    or refreshed is None or refreshed['runtimeEpoch']!=args['runtimeEpoch']
                    or refreshed.get('currentAccountId')!=args['expectedAccountId']
                    or target not in {r['id'] for r in rank(snapshot,latest['excluded'],latest['disabledAccountIds'])}):
                return 'blocked','switch_candidate_unavailable'
            if not args.get('workers') or not all(identity.frozen(w) for w in args['workers']):
                return 'blocked','frozen_worker_identity_missing'
            return 'ok',''
        frozen=args.get('worker')
        if not identity.frozen(frozen):return 'blocked','frozen_worker_identity_missing'
        current=identity.current(inventory,frozen)
        if current is None:return 'skipped','worker_not_current_live_identity'
        if current['role']!=frozen['role']:return 'skipped','role_changed'
        if op in ('restart_worker','restart_ma'):
            if op=='restart_ma' and (frozen['role']!='ma' or frozen['sessionId']!=state['maSessionId']):
                return 'blocked','explicit_ma_identity_missing'
            return 'ok',''
        original=args.get('original',frozen)
        if not identity.unfinished((original or {}).get('task')):
            return 'blocked','frozen_unfinished_task_missing'
        if op=='wake_ta' and (original.get('role')!='ta' or frozen['role']!='ta'):
            return 'blocked','explicit_ta_role_missing'
        if op in ('ma_resume_job','wake_ma') and (original.get('role')!='ma' or frozen['sessionId']!=state['maSessionId']):
            return 'blocked','explicit_ma_identity_missing'
        if op not in ('wake_ta','wake_ma','ma_resume_job','continue_once','cbc_next_model'):
            return 'blocked','unknown_operation'
        if not identity.wake_eligible(original,current):return 'skipped','task_completed_idle_or_changed'
        return 'ok',''

    def reject(self,key,status,reason):
        def update(s):
            c=s['commands'][key]
            if c['status']=='ready':c.update(status=status,diagnostic=reason)
        return self.store.change(update)

    async def dispatch(self):
        if self.closed:return
        state=await io(self.store.read)
        if state['mode']!='automatic':return
        for key,command in state['commands'].items():
            if command['status']!='ready' or command['epoch']!=state['epoch']:continue
            if command['operation']=='post_restart':continue
            if command['operation']=='ma_resume_job' and any(c['caseId']==command['caseId'] and c['operation']=='wake_ta'
                    and c['status'] not in ('completed','skipped') for c in state['commands'].values()):continue
            if command['operation']=='ma_resume_job' and any(c['caseId']==command['caseId'] and c['operation']=='monitor95_job'
                    and c['status']!='completed' for c in state['commands'].values()):continue
            status,reason=await self.preflight(command,state)
            if status!='ok':
                await io(self.reject,key,status,reason);continue
            def claim(s):
                c=s['commands'][key]
                if self.closed or s['mode']!='automatic' or c['epoch']!=s['epoch'] or c['status']!='ready':return False
                target=c['arguments'].get('targetAccountId') if c['operation']=='switch_account' else c['arguments'].get('expectedAccountId')
                if (c.get('accountConfigRevision')!=s['accountConfigRevision']
                        or (target in s['disabledAccountIds'] and c['operation']!='reset_wake_job')):
                    c.update(status='blocked',diagnostic='account_configuration_changed');return False
                c['status']='dispatching';return True
            if not await io(self.store.change,claim):continue
            latest=await io(self.store.read)
            if self.closed or latest['mode']!='automatic' or latest['epoch']!=command['epoch']:
                await io(self.finish,key,'cancelled');continue
            try:
                if command['operation']=='reset_wake_job':
                    args=command['arguments']
                    ma=next((w for w in args['blockedWorkers'] if w['sessionId']==args['sessionId'] and w['role']=='ma'),None)
                    job=await io(self.ensure_job,latest,'reset',args['at'],None,ma)
                    receipt={'key':key,'status':'confirmed','jobId':job['jobId']}
                elif command['operation']=='monitor95_job':
                    job=await io(self.ensure_job,latest,'monitor95',None,command['arguments']['expectedAccountId'],command['arguments']['wakeTarget'])
                    receipt={'key':key,'status':'confirmed','jobId':job['jobId']}
                else:receipt=await asyncio.wait_for(self.adapter.perform(copy.deepcopy(command),key),10)
            except Unavailable:
                await io(self.finish,key,'blocked');continue
            except BaseException:
                await io(self.finish,key,'uncertain');raise
            if not isinstance(receipt,dict) or receipt.get('key')!=key or receipt.get('status')!='confirmed':
                await io(self.finish,key,'uncertain');continue
            if command['operation']=='ma_resume_job' and not identity.identifier(receipt.get('jobId')):
                await io(self.finish,key,'uncertain');continue
            successor=None
            if command['operation'] in ('restart_worker','restart_ma'):
                successor=identity.frozen(receipt.get('successor'))
                inventory=await self.inventory();old=command['arguments']['worker']
                if (successor is None or inventory is None or successor['runtimeEpoch']!=old['runtimeEpoch']
                        or successor['sessionId']!=old['sessionId'] or successor['generation']<=old['generation']
                        or successor['processIdentity']==old['processIdentity'] or successor['role']!=old['role']
                        or successor['adapter']!='codex' or successor['accountId']!=command['arguments']['expectedAccountId']
                        or identity.current(inventory,successor) is None):
                    await io(self.finish,key,'uncertain');continue
            await io(self.finish,key,'completed',receipt.get('jobId'),successor)

    def finish(self,key,status,job_id=None,successor=None):
        def update(s):
            c=s['commands'][key];c['status']=status
            if job_id:c['jobId']=job_id
            if successor:c['successor']=successor
            if status!='completed' or c['epoch']!=s['epoch'] or s['mode']!='automatic':return
            args=c['arguments'];op=c['operation'];cid=c['caseId']
            if op=='switch_account':
                for w in args['workers']:
                    if w['sessionId']!=args['maSessionId']:
                        self.command(s,cid,'restart_worker',worker=w,runtimeEpoch=args['runtimeEpoch'],expectedAccountId=args['targetAccountId'])
                self.command(s,cid,'post_restart',workers=args['workers'],maSessionId=args['maSessionId'],
                             runtimeEpoch=args['runtimeEpoch'],expectedAccountId=args['targetAccountId'])
            elif op=='ma_resume_job':
                self.command(s,cid,'restart_ma',worker=args['worker'],runtimeEpoch=args['runtimeEpoch'],
                             resumeJobId=job_id,expectedAccountId=args['expectedAccountId'])
        return self.store.change(update)

    async def progress_restarts(self):
        if self.closed:return
        state=await io(self.store.read)
        if state['mode']!='automatic' or not any(c['operation']=='post_restart' and c['status']=='ready' for c in state['commands'].values()):return
        inventory=await self.inventory()
        def update(s):
            if self.closed or s['mode']!='automatic':return
            for c in list(s['commands'].values()):
                if c['operation']!='post_restart' or c['status']!='ready' or c['epoch']!=s['epoch']:continue
                args=c['arguments'];cid=c['caseId']
                if (c.get('contractVersion')!=CONTRACT_VERSION or c.get('accountConfigRevision')!=s['accountConfigRevision']
                        or not args.get('workers') or not all(identity.frozen(w) for w in args['workers'])):
                    c.update(status='blocked',diagnostic='legacy_identity_missing');continue
                peers=[v for v in s['commands'].values() if v['caseId']==cid and v['operation']=='restart_worker']
                if not all(v['status'] in ('completed','skipped') for v in peers):continue
                if any(v.get('contractVersion')!=CONTRACT_VERSION or (v['status']=='completed' and not identity.frozen(v.get('successor'))) for v in peers):
                    c.update(status='blocked',diagnostic='verified_successor_missing');continue
                if inventory is None or inventory['runtimeEpoch']!=args.get('runtimeEpoch'):
                    c.update(status='blocked',diagnostic='runtime_identity_unknown');continue
                c['status']='completed';outcomes={}
                for w in args['workers']:
                    if w['role']!='ta' or w['status']!='running':continue
                    if not identity.unfinished(w.get('task')):
                        outcomes[w['sessionId']]='blocked_missing_frozen_task';continue
                    peer=next((v for v in peers if v['arguments']['worker']['sessionId']==w['sessionId']),None)
                    target=peer.get('successor') if peer and peer['status']=='completed' else None
                    current=identity.current(inventory,target) if target else None
                    if current is None or not identity.wake_eligible(w,current):
                        outcomes[w['sessionId']]='skipped_completed_idle_offline_or_changed';continue
                    self.command(s,cid,'wake_ta',worker=target,original=w,runtimeEpoch=args['runtimeEpoch'],expectedAccountId=args['expectedAccountId'])
                    outcomes[w['sessionId']]='planned'
                s['cases'][cid]['wakeOutcomes']=outcomes
                ma=next((w for w in args['workers'] if w['sessionId']==args['maSessionId'] and w['role']=='ma'),None)
                current=identity.current(inventory,ma) if ma else None
                if current and identity.wake_eligible(ma,current):
                    self.command(s,cid,'ma_resume_job',worker=ma,runtimeEpoch=args['runtimeEpoch'],expectedAccountId=args['expectedAccountId'])
                else:s['cases'][cid]['maRecovery']='blocked_missing_current_unfinished_ma'
                self.command(s,cid,'monitor95_job',wakeTarget=ma,runtimeEpoch=args['runtimeEpoch'],expectedAccountId=args['expectedAccountId'])
        await io(self.store.change,update)

    async def recover(self):
        def update(s):
            for c in s['commands'].values():
                if c['status']=='dispatching':c['status']='uncertain'
                elif c['status']=='ready' and c.get('contractVersion')!=CONTRACT_VERSION:
                    c.update(status='blocked',diagnostic='legacy_identity_missing')
        await io(self.store.change,update)

    async def create_monitor(self, kind='monitor95', expected=None):
        state=await io(self.store.read)
        if self.closed:raise ValueError('Quota policy closed')
        if state['mode']!='automatic':raise ValueError('Automatic policy required')
        if expected is not None and (type(expected) is not int or expected!=state['revision']):raise ValueError('Quota policy revision changed')
        if kind not in ('monitor95','once'):raise ValueError('Unknown quota query Job kind')
        account=None;wake_target=None
        if kind=='monitor95':
            quota=await self.query()
            if quota['status']!='known':raise ValueError('Cannot bind monitor: quota/current account unknown')
            account=quota['currentAccountId']
            inventory=await self.inventory()
            wake_target=next((identity.worker(w,inventory['runtimeEpoch']) for w in inventory['workers']
                              if w.get('sessionId')==state['maSessionId'] and w.get('role')=='ma'),None) if inventory else None
            if not identity.wake_eligible(wake_target,wake_target):raise ValueError('Cannot bind monitor: MA/unfinished task identity unknown')
        latest=await io(self.store.read)
        if self.closed or latest['mode']!='automatic' or latest['epoch']!=state['epoch']:
            raise ValueError('Quota policy changed during query')
        if account in latest['disabledAccountIds']:raise ValueError('Cannot monitor disabled account')
        return await io(self.ensure_job,state,kind,None if kind=='monitor95' else self.clock()+1,account,wake_target)

    def ensure_job(self,state,purpose,at,account=None,wake_target=None):
        if purpose=='monitor95':
            # Profile-local query Job admission only; called off the event loop.
            with self.monitor_lock:return self._ensure_job(state,purpose,at,account,wake_target)
        return self._ensure_job(state,purpose,at,account,wake_target)

    def _ensure_job(self,state,purpose,at,account=None,wake_target=None):
        from packages.scheduler.store import _job_from_payload
        jid='job_'+fingerprint([str(self.root),state['epoch'],purpose,account])[:24]
        existing=jobs.get(jid,self.root)
        if existing:
            if existing.get('quotaPolicy')!={'contractVersion':CONTRACT_VERSION,'epoch':state['epoch'],'purpose':purpose,'maSessionId':state['maSessionId'],'expectedAccountId':account,'wakeTarget':wake_target}:
                raise ValueError('Quota query Job identity conflict')
            self.store.change(lambda s:s['jobs'].update({purpose:jid}))
            return jobs.job_public_view(existing)
        if purpose=='monitor95':
            for old in jobs.list_jobs(self.root):
                if protected(old) and old['quotaPolicy']['purpose']=='monitor95' and old.get('enabled'):
                    jobs._update(old['jobId'],{'enabled':False,'status':'cancelled'},registry_root=self.root)
        from datetime import datetime
        specs=[{'kind':'interval','intervalSec':60}] if at is None else [{'kind':'once','at':datetime.fromtimestamp(at).isoformat()}]
        job=_job_from_payload({'name':'Quota '+purpose,'enabled':True,'action':{'api':'quota_query'},'_schedule_specs':specs,'_misfire':'fire_now','_max_runs':None})
        job.update(jobId=jid,quotaPolicy={'contractVersion':CONTRACT_VERSION,'epoch':state['epoch'],'purpose':purpose,'maSessionId':state['maSessionId'],'expectedAccountId':account,'wakeTarget':wake_target})
        try:jobs._create(job,self.root)
        except ValueError:
            existing=jobs.get(jid,self.root)
            if not existing or existing.get('quotaPolicy')!=job['quotaPolicy']:raise
            job=existing
        self.store.change(lambda s:s['jobs'].update({purpose:jid}))
        return jobs.job_public_view(job)

    async def cancel_obsolete_jobs(self):
        # Configuration writes must not run a second scheduler/query concurrently.
        state=await io(self.store.read)
        for job in await io(jobs.list_jobs,self.root):
            if protected(job) and (state['mode']!='automatic' or job['quotaPolicy']['epoch']!=state['epoch']):
                await io(jobs._update,job['jobId'],{'enabled':False,'status':'cancelled'},registry_root=self.root)

    async def tick_jobs(self):
        if self.closed:return
        from datetime import datetime
        state=await io(self.store.read)
        for job in await io(jobs.list_jobs,self.root):
            if not protected(job):continue
            policy=job['quotaPolicy']
            if policy.get('contractVersion')!=CONTRACT_VERSION or (policy['purpose']!='once' and not identity.wake_eligible(policy.get('wakeTarget'),policy.get('wakeTarget'))):
                await io(jobs._update,job['jobId'],{'enabled':False,'status':'blocked','quotaResult':{'status':'unknown','reason':'legacy_identity_missing'}},registry_root=self.root);continue
            if state['mode']!='automatic' or policy['epoch']!=state['epoch']:
                await io(jobs._update,job['jobId'],{'enabled':False,'status':'cancelled'},registry_root=self.root);continue
            if job['status']=='running':
                # Query is read-only, but a possibly sent wake is never re-sent.
                await io(jobs._update,job['jobId'],{'enabled':False,'status':'uncertain'},registry_root=self.root);continue
            if not job.get('enabled'):continue
            entry=next((e for e in job['schedule'] if e.get('enabled',True) and jobs._entry_is_due(e,datetime.now().replace(microsecond=0))),None)
            if not entry:continue
            await io(jobs._update,job['jobId'],{'status':'running'},registry_root=self.root)
            snapshot=await self.query()
            latest=await io(self.store.read)
            if self.closed:return
            if latest['mode']!='automatic' or latest['epoch']!=policy['epoch']:
                await io(jobs._update,job['jobId'],{'enabled':False,'status':'cancelled'},registry_root=self.root)
                continue
            trigger=False
            if snapshot['status']=='known':
                row=next(r for r in snapshot['accounts'] if r['id']==snapshot['currentAccountId'])
                trigger=policy['purpose']=='reset' or (policy['purpose']=='monitor95' and row['fiveHour']['usedPercent']>=95)
                if policy.get('expectedAccountId') and policy['expectedAccountId']!=snapshot['currentAccountId']:
                    await io(jobs._update,job['jobId'],{'status':'cancelled','enabled':False,'quotaResult':{'status':'unknown','reason':'account_changed'}},registry_root=self.root)
                    continue
            if trigger:
                inventory=await self.inventory()
                original=policy.get('wakeTarget');target=original
                ma=identity.current(inventory,target) if inventory and target else None
                if inventory and original and ma is None:
                    # Only a persisted verified restart can transfer this exact task.
                    for c in latest['commands'].values():
                        old=c['arguments'].get('worker');successor=c.get('successor')
                        if (c['operation']=='restart_ma' and c['status']=='completed' and c.get('contractVersion')==CONTRACT_VERSION
                                and identity.frozen(old) and identity.same_worker(old,original)
                                and identity.wake_eligible(original,successor)):
                            target=successor;ma=identity.current(inventory,target)
                            break
                def wake(s):
                    if s['mode']=='automatic' and s['epoch']==policy['epoch'] and snapshot['currentAccountId'] not in s['disabledAccountIds']:
                        if ma and identity.wake_eligible(original,ma):
                            self.command(s,job['jobId'],'wake_ma',worker=target,original=original,runtimeEpoch=inventory['runtimeEpoch'],reason=policy['purpose'],expectedAccountId=snapshot['currentAccountId'])
                        else:s['lastWakeDiagnostic']={'jobId':job['jobId'],'reason':'blocked_missing_current_unfinished_ma'}
                await io(self.store.change,wake)
            next_fire=jobs._entry_next_fire(entry,datetime.now().replace(microsecond=0)) if entry['kind']!='once' else None
            await io(jobs._apply_entry_change,job['jobId'],entry['id'],{'nextFireAt':jobs._iso_local(next_fire)},
                     {'status':'completed' if trigger or next_fire is None else 'scheduled','enabled':not trigger and next_fire is not None,
                      'quotaResult':{'status':snapshot['status'],'thresholdReached':trigger}},self.root)
            await io(jobs._append_run_record_once,{'run_id':fingerprint([job['jobId'],entry['nextFireAt']]),
                     'task_id':job['taskId'],'dispatch_key':fingerprint([job['jobId'],entry['nextFireAt']]),
                     'status':'completed','result':{'quotaStatus':snapshot['status'],'thresholdReached':trigger}},registry_root=self.root)

    async def tick(self):
        if self.closed or self.gate.locked():return
        async with self.gate:
            while not self.closed and not self.pending.empty():
                issue=self.pending.get_nowait()
                try:await self.consume(issue)
                finally:self.pending.task_done()
            await self.tick_jobs()
            await self.progress_restarts()
            await self.dispatch()


    async def close(self):
        self.closed=True
        # Settle the owned scheduler tick before releasing the profile owner.
        # Adapter awaits are bounded; no global or unrelated Session gate is held.
        async with self.gate:
            pass
