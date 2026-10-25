"""Recoverable user-authorized Job + managed Trigger + shared Source edits.

The ledger intent precedes all writes. Drafts precede activation. Retries reuse
one key and stable resource identities; failure quarantines this intent's drafts.
"""
import asyncio
import copy
import hashlib
import json
import re
import threading

from packages.jobs.action_templates import Conflict, config_value, digest
from packages.plugins.lifecycle import io
from packages.scheduler.time_events import EVENT_TYPE


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,allow_nan=False).encode()).hexdigest()


class Links:
    def __init__(self,runtime,ledger,sources):
        self.runtime,self.ledger,self.sources=runtime,ledger,sources
        self.lock=asyncio.Lock()
        self.gate_lock=threading.RLock()
        self.gates={key:(op.get('jobId'),op['status']) for key,op in ledger.read().get('linkOperations',{}).items()}
        runtime.managed_cleanup=self.remove_owned
        runtime.setup_pending=self.job_pending

    def job_pending(self,jid):
        with self.gate_lock:return any(owner==jid and status not in {'complete','cancelled'} for owner,status in self.gates.values())

    def source_ready(self,key):
        with self.gate_lock:return self.gates.get(key,(None,None))[1] in {'complete','cancelled'}

    def cache_gate(self,op):
        # Cache becomes permissive only after the durable ledger commit returns.
        with self.gate_lock:self.gates[op['key']]=(op.get('jobId'),op['status'])
        return op

    def remove_owned(self,jid):
        def change(state):
            for tid,t in list(state['triggers'].items()):
                if t.get('ownerJobId')==jid:
                    del state['triggers'][tid]
                    for run in state['runs'].values():
                        if run['triggerId']==tid and run['status'] in {'queued','retry_wait'}:
                            run.update(status='cancelled',revision=run['revision']+1)
                            self.ledger.sync_attempt(run)
        self.ledger.mutate(change)
        self.sources.detach_links(jid)

    def references(self,sid):
        refs={r['triggerId']:{**r,'name':'Pending setup','enabled':False} for r in self.sources.references(sid)}
        for t in self.ledger.read()['triggers'].values():
            if EVENT_TYPE in t['eventTypes'] and t.get('eventFilter',{}).get('payload.scheduleId',sid)==sid:
                refs[t['id']]={'triggerId':t['id'],'name':t['name'],'jobId':t.get('targetJobId'), 'enabled':t['enabled'] and not (t.get('ownerJobId') and self.job_pending(t['ownerJobId']))}
        return list(refs.values())

    def operations(self):
        ops=self.ledger.read().get('linkOperations',{}).values()
        for op in ops:self.cache_gate(op)
        return [{k:copy.deepcopy(v) for k,v in op.items() if k not in {'request','config'}}
                for op in ops if op['status'] not in {'complete','cancelled'}]

    def reserve(self,raw):
        if not isinstance(raw,dict) or set(raw)-{'key','jobId','expectedRevision','config','schedulers'}:raise ValueError('Invalid Job composition request')
        key=raw.get('key')
        if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}',key):raise ValueError('Stable operation key required')
        previous=self.ledger.read().get('linkOperations',{}).get(key)
        if previous:
            if previous['fingerprint']!=fingerprint(raw):raise Conflict('Operation key already belongs to another draft')
            if previous['status']=='cancelled':raise Conflict('Operation was cancelled; create a new key')
            return self.cache_gate(previous)
        configs=raw.get('schedulers',[])
        if not isinstance(configs,list) or len(configs)>8:raise ValueError('At most8 Scheduler links per Job')
        seen=set()
        for link in configs:
            if not isinstance(link,dict) or set(link)-{'linkId','mode','sourceId','sourceRevision','config'}:raise ValueError('Invalid Scheduler link')
            lid=link.get('linkId')
            if not isinstance(lid,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',lid) or lid in seen:raise ValueError('Unique stable linkId required')
            seen.add(lid)
            if link.get('mode') not in {'existing','new'}:raise ValueError('Choose existing or new Scheduler')
        existing=self.runtime.get(raw['jobId']) if raw.get('jobId') else None
        if existing and existing['config']['trigger']['kind']!='manual':
            if configs or raw['config'].get('trigger')!=existing['config']['trigger'] or raw['config'].get('schedule')!=existing['config']['schedule']:
                raise Conflict('Existing trigger ownership is preserved; use existing Job controls for timing changes')
            cfg=config_value(raw['config'],self.runtime.actions.read())
        else:cfg=config_value({**raw['config'],'trigger':{'kind':'manual'},'schedule':[]},self.runtime.actions.read())
        def change(state):
            ops=state.setdefault('linkOperations',{})
            old=ops.get(key)
            if old:
                if old['fingerprint']!=fingerprint(raw):raise Conflict('Operation key already belongs to another draft')
                if old['status']=='cancelled':raise Conflict('Operation was cancelled; create a new key')
                return old
            if len(ops)>=10000:raise ValueError('Job composition history capacity reached')
            jid=raw.get('jobId') or 'job_'+digest('automation:'+str(self.runtime.root)+':'+key)[:24]
            if raw.get('jobId'):
                job=existing
                if type(raw.get('expectedRevision')) is not int or job['configRevision']!=raw['expectedRevision']:raise Conflict('Job revision changed')
                if any(o.get('jobId')==jid and o['status'] not in {'complete','cancelled'} for o in ops.values()):raise Conflict('Resume or discard the existing pending Job edit first')
            op=dict(key=key,fingerprint=fingerprint(raw),request=copy.deepcopy(raw),config=cfg,jobId=jid,
                expectedRevision=raw.get('expectedRevision') if raw.get('jobId') else 1,status='pending',links=[],staged=False)
            ops[key]=op
            return op
        return self.cache_gate(self.ledger.mutate(change))

    def checkpoint(self,key,**fields):
        def change(state):state['linkOperations'][key].update(copy.deepcopy(fields));return state['linkOperations'][key]
        return self.cache_gate(self.ledger.mutate(change))

    def quarantine(self,op):
        jid=op.get('jobId')
        if not jid:return
        def change(state):
            for t in state['triggers'].values():
                if t.get('ownerJobId')==jid:t.update(enabled=False,revision=t['revision']+1)
        self.ledger.mutate(change)
        if op.get('staged'):
            try:current=self.runtime.get(jid)
            except KeyError:current=None
            desired={**op['config'],'enabled':False}
            if current and {**current['config'],'enabled':False}==desired and current['enabled']:
                self.runtime.set_enabled(jid,False,current['configRevision'],{'type':'user'})
        for link in op.get('links',[]):
            if link['mode']=='new':
                src=next((s for s in self.sources.list() if s['id']==link['sourceId']),None)
                if src:
                    self.sources.composition_enabled(src['id'],False)

    async def compose(self,raw):
        async with self.lock:
            op=await io(self.reserve,raw)
            if op['status']=='complete':return {'job':await io(self.runtime.view,await io(self.runtime.get,op['jobId'])),'operation':{'key':op['key'],'status':'complete'}}
            try:
                op=await self._compose(op)
                return {'job':await io(self.runtime.view,await io(self.runtime.get,op['jobId'])),'operation':{'key':op['key'],'status':'complete'}}
            except Exception as exc:
                latest=(await io(self.ledger.read))['linkOperations'][op['key']]
                try:await self.fence(latest)
                except Exception as fence_error:
                    self.runtime.diagnostics.append({'phase':'job-setup-quarantine','jobId':latest.get('jobId'),'error':str(fence_error)[:1000]})
                await io(self.checkpoint,op['key'],status='failed',error=str(exc)[:1000])
                raise

    async def _compose(self,op):
        key=op['key']; cfg=op['config']; draft={**cfg,'enabled':False}
        if not op['request'].get('jobId'):
            job=await io(self.runtime.create,draft,True,{'type':'user'},creation_key=key)
            op=await io(self.checkpoint,key,jobId=job['jobId'])
        jid=op['jobId']
        state=await io(self.ledger.read)
        # Disable managed admissions before changing their target Job config.
        for t in state['triggers'].values():
            if t.get('ownerJobId')==jid and t['enabled']:
                fields={k:v for k,v in t.items() if k not in {'revision','createdBy','createdAt','updatedAt','authorization'}}
                await io(self.ledger.put,{**fields,'enabled':False},t['revision'],True,managed=True)
        job=await io(self.runtime.get,jid)
        if job['config']!=draft:
            # Only an identical staged config can recover a lost checkpoint.
            if job['configRevision']!=op['expectedRevision'] and {**job['config'],'enabled':False}!=draft:raise Conflict('Job revision changed during composition')
            job=await io(self.runtime.update,jid,draft,job['configRevision'],True,{'type':'user'})
        op=await io(self.checkpoint,key,staged=True,expectedRevision=job['configRevision'])
        links=[]
        for item in op['request'].get('schedulers',[]):
            if item['mode']=='new':
                src=await io(self.sources.put,{**item['config'],'enabled':False},0,creation_key=key+':'+item['linkId'])
                await io(self.sources.bind_operation,src['id'],key)
            else:
                src=next((s for s in await io(self.sources.list) if s['id']==item.get('sourceId')),None)
                if not src:raise Conflict('Selected Scheduler was deleted')
                if src['revision']!=item.get('sourceRevision'):raise Conflict('Selected Scheduler revision changed')
            if any(l['sourceId']==src['id'] for l in links):raise ValueError('A Job cannot subscribe to the same source twice')
            links.append({**item,'sourceId':src['id'],'sourceRevision':src['revision'],
                          'triggerId':'trg_job_'+fingerprint([jid,item['linkId']])[:24]})
            op=await io(self.checkpoint,key,links=links)
            await io(self.sources.bind_link,src['id'],links[-1]['triggerId'],jid)
            t= (await io(self.ledger.read))['triggers'].get(links[-1]['triggerId'])
            await io(self.ledger.put,dict(id=links[-1]['triggerId'],name=cfg['name']+' · '+src['name'],enabled=False,
                eventTypes=[EVENT_TYPE],eventFilter={'payload.scheduleId':src['id']},actionId=cfg['actionId'],arguments={},
                targetJobId=jid,ownerJobId=jid,ownerLinkId=item['linkId']),t['revision'] if t else 0,True,managed=True)
        keep={l['triggerId'] for l in links}
        def remove_stale(state):
            for tid,t in list(state['triggers'].items()):
                if t.get('ownerJobId')==jid and tid not in keep:del state['triggers'][tid]
        await io(self.ledger.mutate,remove_stale)
        await io(self.sources.detach_links,jid,keep)
        for link in links:
            if not any(s['id']==link['sourceId'] for s in await io(self.sources.list)):raise Conflict('Selected Scheduler was deleted')
        if cfg['enabled']:
            job=await io(self.runtime.get,jid)
            if not job['enabled']:await io(self.runtime.set_enabled,jid,True,job['configRevision'],{'type':'user'})
        for link in links:
            t=(await io(self.ledger.read))['triggers'][link['triggerId']]
            fields={k:v for k,v in t.items() if k not in {'revision','createdBy','createdAt','updatedAt','authorization'}}
            await io(self.ledger.put,{**fields,'enabled':cfg['enabled']},t['revision'],True,managed=True)
        for link in links:
            if cfg['enabled'] and link['mode']=='new' and link['config'].get('enabled',True):
                src=next(s for s in await io(self.sources.list) if s['id']==link['sourceId'])
                if not src['enabled'] or src.get('compositionBlocked'):await io(self.sources.composition_enabled,src['id'],True)
        return await io(self.checkpoint,key,status='complete',error='')

    async def recover(self):
        for op in self.ledger.read().get('linkOperations',{}).values():
            if op['status'] not in {'complete','cancelled'}:await self.fence(op)

    async def fence(self,op):
        timer=getattr(self.runtime,'time_events',None)
        if timer:
            async with timer.lock:await io(self.quarantine,op)
        else:await io(self.quarantine,op)

    async def cancel(self,key):
        async with self.lock:
            op=self.ledger.read()['linkOperations'][key]
            if op['status']=='complete':raise Conflict('Completed edit cannot be discarded')
            await self.fence(op)
            if op.get('jobId'):await io(self.sources.detach_links,op['jobId'],[t['id'] for t in self.ledger.read()['triggers'].values() if t.get('ownerJobId')==op['jobId']])
            return await io(self.checkpoint,key,status='cancelled')
