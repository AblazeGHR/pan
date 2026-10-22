"""Action-reference dispatch within the existing generic Job registry/lifecycle."""
import asyncio
import copy
from collections import deque
from datetime import datetime
import time
import uuid

from packages.core import background_jobs as jobs
from packages.plugins.lifecycle import io
from .action_templates import Templates, Conflict, CreationPending, config_value, digest, generation, integrity, revision

RUNTIMES = {}


class SnapshotPersistenceError(RuntimeError):
    pass


def is_action_job(job):
    return isinstance(job,dict) and isinstance(job.get('action'),dict) and job['action'].get('api')=='action_reference'


def is_protected_job(job):
    from packages.automation.quota_policy import protected
    return is_action_job(job) or protected(job)


def for_root(root):
    from pathlib import Path
    return RUNTIMES.get(str(Path(root).resolve()))


def config_job(root, intent):
    from packages.scheduler.store import _job_from_payload
    cfg=intent['config']
    job=_job_from_payload(dict(name=cfg['name'],description=cfg['description'],enabled=cfg['enabled'],
        action={'api':'action_reference','actionId':cfg['actionId'],'arguments':copy.deepcopy(cfg['arguments'])},
        _schedule_specs=cfg['schedule'],_misfire=cfg['misfirePolicy'],_max_runs=cfg['maxRuns']))
    job.update(jobId=intent['jobId'],config=copy.deepcopy(cfg),configRevision=1,
               referenceGeneration=intent['referenceGeneration'],creationFingerprint=intent['fingerprint'],
               status='scheduled' if cfg['enabled'] else 'pending',creationEventPending=True)
    if intent.get('templateId'):
        job.update(templateId=intent['templateId'],templateRevision=intent['templateRevision'])
    return job


class Runtime:
    def __init__(self, root, actions, pause):
        from pathlib import Path
        self.root=Path(root).resolve();self.actions=actions;self.templates=Templates(actions);self.pause=pause
        from packages.jobs.independent_service import ServiceLock
        self.ownership=ServiceLock(self.actions.root/'job-runtime');self.owned=False;self.lifecycle=asyncio.Lock()
        self.tasks={};self.admissions={};self.closed=False;self.diagnostics=deque(maxlen=100);self.limit=5;self.cancelled=set()

    async def start(self):
        async with self.lifecycle:
            await self._start()

    async def _start(self):
        if self.closed:return
        if str(self.root) in RUNTIMES:raise Conflict('Job runtime already owns this profile')
        await io(self.ownership.acquire)
        self.owned=True
        if self.closed:
            await io(self.ownership.release);self.owned=False;return
        RUNTIMES[str(self.root)]=self
        try:
            await io(self.recover)
            await self.publish_events()
        except BaseException:
            if RUNTIMES.get(str(self.root)) is self:RUNTIMES.pop(str(self.root),None)
            await io(self.ownership.release);self.owned=False;raise
        if self.closed:
            if RUNTIMES.get(str(self.root)) is self:RUNTIMES.pop(str(self.root),None)
            await io(self.ownership.release);self.owned=False

    def view(self, job, state=None):
        if not is_action_job(job):return jobs.job_public_view(job)
        state=self.actions.read() if state is None else state
        result=jobs.job_public_view(copy.deepcopy(job))
        result['integrity']=integrity(state,job['config'],job['referenceGeneration'])
        result['enabled']=(job['enabled'] and result['integrity']['status']=='complete' and not result['integrity']['approvalRequired'])
        result['config']['enabled']=result['enabled']
        return result

    def list(self):
        state=self.actions.read()
        return [self.view(j,state) for j in jobs.list_jobs(self.root) if is_action_job(j)]

    def get(self,jid):
        job=jobs.get(jid,self.root)
        if not is_action_job(job):raise KeyError(jid)
        return job

    def publish_intent(self,intent):
        try:return self._publish_intent(intent)
        except Conflict:raise
        except (OSError,ValueError) as exc:
            raise CreationPending(intent['jobId'],exc) from exc

    def _publish_intent(self,intent):
        old=jobs.get(intent['jobId'],self.root)
        if not old:
            if intent['phase']!='reserved':raise KeyError('Created Job was deleted; key cannot recreate it')
            job=config_job(self.root,intent)
            try:jobs._create(job,self.root)
            except ValueError:
                old=jobs.get(intent['jobId'],self.root)
                if not old:raise
            else:old=job
        if old.get('creationFingerprint')!=intent['fingerprint']:
            raise Conflict('Reserved Job ID belongs to another creation')
        if intent['phase']=='reserved':self.templates.complete_intent(intent['key'])
        return self.view(old)

    def create(self,raw,authorized=False):
        def change(state):
            cfg=config_value(raw,state)
            if cfg['enabled'] and not authorized:raise PermissionError('Agent may create disabled Job draft only')
            jid='job_'+uuid.uuid4().hex[:24];key=digest(jid)
            intent=dict(key=key,jobId=jid,config=cfg,referenceGeneration=generation(state,cfg['actionId']),
                        fingerprint=digest({'jobId':jid,'config':cfg}),phase='reserved',jobRoot=str(self.root))
            if len(state.setdefault('jobInstantiations',{}))>=10000:raise ValueError('Instantiation capacity reached')
            state['jobInstantiations'][key]=intent
            state.setdefault('jobReferences',{})[jid]={'actionId':cfg['actionId'],'position':'/config/actionId','jobRoot':str(self.root)}
            return copy.deepcopy(intent)
        return self.publish_intent(self.templates.transaction(change))

    def instantiate(self,tid,expected,key,overrides=None,admitted_template=None):
        scoped=digest({'root':str(self.root),'key':key})
        intent=self.templates.reserve(tid,expected,scoped,overrides,job_root=str(self.root),admitted_template=admitted_template)
        return self.publish_intent(intent)

    async def instantiate_action(self,action,key,fixed=None):
        ex=action['execution']
        if not action['enabled'] or ex['kind']!='job_template' or action['effect']!='write_local':
            raise PermissionError('Fixed template capability unavailable')
        try:job=await io(self.instantiate,ex['templateId'],ex['templateRevision'],'action:'+action['id']+':'+key,None,fixed)
        except CreationPending as exc:
            from packages.automation.engine import PersistenceError
            raise PersistenceError(str(exc)) from exc
        return {'ok':True,'jobId':job['jobId'],'created':True,'jobCompleted':False,
                'templateId':ex['templateId'],'templateRevision':ex['templateRevision']}

    def update(self,jid,raw,expected,authorized=False):
        # Action reference validation/index is committed first; Job publication
        # validates its current revision under its own lock. Stale index entries
        # only conservatively require delete confirmation, never grant execution.
        old=self.get(jid);revision(old['configRevision'],expected)
        def validate(state):
            cfg=config_value(raw,state);aid=cfg['actionId'];gen=generation(state,aid)
            repair=old['config']['actionId']!=aid or old['referenceGeneration']!=gen
            if not authorized and (cfg['enabled'] or old['enabled'] or repair):
                raise PermissionError('User Bearer required for Job enable/edit/repair')
            if repair:cfg['enabled']=False
            refs=state.setdefault('jobReferences',{})
            previous=refs.get(jid,{})
            refs[jid]={'actionId':aid,'actionIds':list(dict.fromkeys([*previous.get('actionIds',[old['config']['actionId']]),aid])),
                       'position':'/config/actionId','jobRoot':str(self.root),'configRevision':expected+1}
            return cfg,gen
        cfg,gen=self.templates.transaction(validate)
        with jobs._lock,jobs._job_lock(jid,self.root):
            old=self.get(jid);revision(old['configRevision'],expected)
            intent={'jobId':jid,'config':cfg,'fingerprint':old['creationFingerprint'],'referenceGeneration':gen}
            replacement=config_job(self.root,intent)
            for key in ('taskId','createdAt','runCount','templateId','templateRevision','execution','executionFinalization','lastExecution','lastStatus'):
                if key in old:replacement[key]=copy.deepcopy(old[key])
            if cfg['schedule']==old['config']['schedule']:
                replacement['schedule']=copy.deepcopy(old['schedule'])
                replacement['nextFireAt']=old.get('nextFireAt')
            if old['status'] in {'running','cleaning'}:replacement['status']=old['status']
            replacement['configRevision']=expected+1
            jobs._write_job(jobs._job_path(jid,self.root),replacement)
        def narrow(state):
            ref=state['jobReferences'].get(jid,{})
            if ref.get('configRevision')==expected+1 and ref.get('actionId')==cfg['actionId']:
                ref['actionIds']=[cfg['actionId']]
        self.templates.transaction(narrow)
        return self.view(replacement)

    def claim(self,jid,eid,expected=None,entry=None):
        with jobs._lock,jobs._job_lock(jid,self.root):
            job=self.get(jid)
            if expected is not None:revision(job['configRevision'],expected)
            if job['status'] in {'running','cleaning'}:raise Conflict('Job already running or cleaning')
            if not job['enabled']:raise Conflict('Job disabled')
            context={'id':eid,'type':'jobs.execute','payload':copy.deepcopy(job['config']['arguments']),
                     'jobId':jid,'dispatchKey':eid,'fireAt':datetime.now().isoformat()}
            execution={'id':eid,'jobRoot':str(self.root),'phase':'preparing','entryId':entry,'context':context}
            job.update(status='running',execution=execution,runStartedAt=time.time())
            jobs._write_job(jobs._job_path(jid,self.root),job)
            return copy.deepcopy(job)

    def finalize(self,jid,eid,status,audit=None):
        # A durable finalization intent precedes destructive snapshot cleanup.
        with jobs._lock,jobs._job_lock(jid,self.root):
            job=self.get(jid)
            if job.get('execution',{}).get('id')!=eid:return
            prior=job.get('executionFinalization')
            outcome=prior or {'status':status,'audit':audit or {}}
            job.update(status='cleaning',executionFinalization=outcome)
            job['execution']['phase']='cleaning'
            jobs._write_job(jobs._job_path(jid,self.root),job)
        self.templates.clear_snapshot(eid)
        with jobs._lock,jobs._job_lock(jid,self.root):
            job=self.get(jid)
            if job.get('execution',{}).get('id')!=eid:return
            job.pop('execution',None);job.pop('executionFinalization',None)
            summary={'id':eid,'status':outcome['status'],**outcome['audit']}
            job.update(lastExecution=summary,lastStatus=outcome['status'],runStartedAt=None,
                       runCount=int(job.get('runCount',0))+1,terminalEventId=eid)
            cfg=job['config'];once=all(e['kind']=='once' and not e.get('enabled',True) for e in job['schedule'])
            # The claimed entry is consumed at dispatch admission, not recovery.
            exhausted=not any(e.get('enabled',True) and e.get('nextFireAt') for e in job['schedule'])
            if outcome['status'] in {'uncertain','cancelled'} or exhausted or (cfg['maxRuns'] and job['runCount']>=cfg['maxRuns']):
                job['enabled']=False;job['config']['enabled']=False
            job['status']='scheduled' if job['enabled'] else outcome['status']
            record={'run_id':eid,'task_id':job['taskId'],'dispatch_key':eid,
                    'status':outcome['status'],'result':summary,'actual_at':datetime.now().isoformat()}
            jobs._append_run_record_once(record,registry_root=self.root)
            jobs._write_job(jobs._job_path(jid,self.root),job)

    async def publish_events(self):
        for job in await io(jobs.list_jobs,self.root):
            if not is_action_job(job):continue
            if job.get('creationEventPending'):
                if await jobs._scheduled_task_emit_strict({'type':'job.created','jobId':job['jobId'],'job':await io(self.view,job)}):
                    await io(jobs._update,job['jobId'],{'creationEventPending':False},registry_root=self.root)
            if not job.get('lastExecution'):continue
            eid=job['lastExecution']['id']
            if job.get('terminalEventEmittedFor')==eid:continue
            view=await io(self.view,job)
            if not await jobs._scheduled_task_emit_strict({'type':'job.updated','jobId':job['jobId'],'job':view}):continue
            if not await jobs._scheduled_task_emit_strict({'type':'scheduler.task.fired','taskId':job['taskId'],
                    'dispatchKey':eid,'status':job['lastExecution']['status'],'terminal':True}):continue
            def mark():
                with jobs._lock,jobs._job_lock(job['jobId'],self.root):
                    current=self.get(job['jobId'])
                    if current.get('lastExecution',{}).get('id')==eid:
                        current['terminalEventEmittedFor']=eid
                        jobs._write_job(jobs._job_path(job['jobId'],self.root),current)
            await io(mark)

    async def execute(self,job):
        from packages.actions.executor import execute_dynamic
        eid=job['execution']['id'];audit={};outcome='uncertain'
        try:
            snapshot=await io(self.templates.snapshot,job,job['execution'])
            audit={k:snapshot[k] for k in ('actionId','actionRevision','registryRevision','digest','argumentsDigest','contextDigest')}
            async def progress(**fields):
                try:await io(self.templates.checkpoint,eid,fields)
                except Exception as exc:raise SnapshotPersistenceError(str(exc)) from exc
            progress.checkpoint={'id':eid}
            progress.instantiate_template=lambda a,key:self.instantiate_action(a,key,snapshot.get('fixedTemplate'))
            result=await execute_dynamic(snapshot['definition'],snapshot['arguments'],snapshot['context'],progress,self.pause)
            outcome='completed' if result.get('ok',True) and not result.get('isError',False) else 'failed'
            for key in ('jobId','matched','succeeded','failed'):
                if key in result:audit[key if key!='jobId' else 'createdJobId']=result[key]
        except asyncio.CancelledError:
            outcome='cancelled' if eid in self.cancelled else 'uncertain'
        except Exception as exc:
            from packages.automation.engine import PersistenceError
            outcome='uncertain' if isinstance(exc,(SnapshotPersistenceError,PersistenceError,TimeoutError)) else 'failed';audit['error']=str(exc)[:1000]
        finally:
            try:
                await io(self.finalize,job['jobId'],eid,outcome,audit)
                await self.publish_events()
            except Exception as exc:self.diagnostics.append({'jobId':job['jobId'],'phase':'cleanup','error':str(exc)})
            self.cancelled.discard(eid)

    def consume_entry(self,job,entry_id):
        if entry_id is None:return job
        def patch():
            with jobs._lock,jobs._job_lock(job['jobId'],self.root):
                latest=self.get(job['jobId'])
                for entry in latest['schedule']:
                    if entry['id']!=entry_id:continue
                    fire=jobs._job_cron.parse_datetime(entry['nextFireAt'])
                    next_fire=None if entry['kind']=='once' else jobs._entry_next_fire(entry,max(fire,datetime.now().replace(microsecond=0)))
                    entry['nextFireAt']=jobs._iso_local(next_fire)
                    if entry['kind']=='once':entry['enabled']=False
                latest['nextFireAt']=jobs._job_next_fire(latest)
                jobs._write_job(jobs._job_path(job['jobId'],self.root),latest)
                return job
        return patch()

    async def launch(self,jid,expected=None,entry=None):
        if self.closed:raise Conflict('Job runtime closed')
        if jid in self.admissions or (jid in self.tasks and not self.tasks[jid].done()):
            raise Conflict('Job already admitted')
        if len(self.admissions)+sum(not t.done() for t in self.tasks.values())>=self.limit:
            raise Conflict('Shared scheduler dispatch capacity reached')
        self.admissions[jid]=asyncio.current_task()
        eid='jexec_'+uuid.uuid4().hex
        try:
            before=await io(self.get,jid)
            if not (await io(self.view,before))['enabled']:raise Conflict('Job disabled, incomplete or requires user repair')
            job=await io(self.claim,jid,eid,expected,entry)
            job=await io(self.consume_entry,job,entry)
            public=await io(self.view,job)
            if self.closed or eid in self.cancelled:raise asyncio.CancelledError()
            task=asyncio.create_task(self.execute(job),name='job-action-'+eid)
            self.tasks[jid]=task
            return public
        except BaseException:
            current=await io(self.get,jid)
            if current.get('execution',{}).get('id')==eid:
                await io(self.finalize,jid,eid,'cancelled' if eid in self.cancelled else 'uncertain')
            raise
        finally:
            self.admissions.pop(jid,None)
            if jid not in self.tasks:self.cancelled.discard(eid)

    async def tick(self,cfg):
        if self.closed:return
        policy=getattr(self,'quota_policy',None)
        if policy is not None:
            try:await policy.tick()
            except Exception as exc:self.diagnostics.append({'phase':'quota-policy','error':type(exc).__name__})
        for jid,task in list(self.tasks.items()):
            if task.done():
                await task;self.tasks.pop(jid,None)
        # Reconcile cleanup failures without ever rerunning effects.
        try:
            await io(self.recover,False)
            await self.publish_events()
        except Exception as exc:
            self.diagnostics.append({'phase':'recovery','error':str(exc)})
            return  # This extension must not starve legacy scheduler/message Jobs.
        if not cfg.get('enabled',True):return
        limit=max(1,min(32,int(cfg.get('max_concurrent_dispatch',5))));self.limit=limit
        for job in await io(jobs.list_jobs,self.root):
            if len(self.tasks)>=limit:break
            if not is_action_job(job) or job['jobId'] in self.tasks or not job.get('enabled') or job.get('paused'):continue
            if job['status'] in {'running','cleaning'}:continue
            for entry in job.get('schedule',[]):
                if entry.get('enabled',True) and jobs._entry_is_due(entry,datetime.now().replace(microsecond=0)):
                    if await io(self.skip_misfire,job,entry,cfg):
                        break
                    try:await self.launch(job['jobId'],entry=entry['id'])
                    except (Conflict,ValueError) as exc:self.diagnostics.append({'jobId':job['jobId'],'error':str(exc)})
                    break

    def skip_misfire(self,job,entry,cfg):
        now=datetime.now().replace(microsecond=0)
        fire=jobs._job_cron.parse_datetime(entry['nextFireAt'])
        late=(now-fire).total_seconds()
        if late<=jobs._scheduled_task_grace(cfg,entry):return False
        expired=entry['kind']=='once'
        if not expired and entry.get('misfirePolicy',job.get('misfirePolicy'))!='skip':return False
        status='expired' if expired else 'skipped'
        next_fire=None if expired else jobs._entry_next_fire(entry,max(fire,now))
        patch={'lastStatus':status,'lastError':'Schedule misfire '+status}
        if expired:patch.update(enabled=False,status='completed')
        jobs._apply_entry_change(job['jobId'],entry['id'],
            {'nextFireAt':jobs._iso_local(next_fire),**({'enabled':False} if expired else {})},patch,self.root)
        key=digest({'jobId':job['jobId'],'entryId':entry['id'],'fireAt':entry['nextFireAt']})
        jobs._append_run_record_once({'run_id':key,'task_id':job['taskId'],'dispatch_key':key,
            'status':status,'actual_at':now.isoformat()},registry_root=self.root)
        jobs._update(job['jobId'],{'lastExecution':{'id':key,'status':status,'actionId':job['config']['actionId']},
                                  'terminalEventId':key},registry_root=self.root)
        return True

    def recover(self,initial=True):
        # Recovery consumes no executable historical definition. It only removes
        # snapshots and finalizes known in-flight identities once.
        state=self.actions.read()
        for intent in state.get('jobInstantiations',{}).values():
            if intent.get('jobRoot')==str(self.root) and intent['phase']=='reserved':self.publish_intent(intent)
        for job in jobs.list_jobs(self.root):
            if not is_action_job(job) or not job.get('execution') or job['jobId'] in self.tasks or job['jobId'] in self.admissions:continue
            if job['status'] in {'running','cleaning'}:
                eid=job['execution']['id'];snap=state.get('jobExecutions',{}).get(eid,{})
                audit={k:snap[k] for k in ('actionId','actionRevision','registryRevision','digest','argumentsDigest','contextDigest') if k in snap}
                self.finalize(job['jobId'],eid,'uncertain',audit)
        state=self.actions.read()
        for eid,snap in state.get('jobExecutions',{}).items():
            if snap.get('jobRoot')==str(self.root) and snap.get('jobId') not in self.tasks and snap.get('jobId') not in self.admissions:
                audit={k:snap[k] for k in ('actionId','digest','argumentsDigest','contextDigest') if k in snap}
                jobs._append_run_record_once({'run_id':eid,'task_id':snap.get('taskId',snap['jobId']),
                    'dispatch_key':eid,'status':'uncertain','result':audit},registry_root=self.root)
                self.templates.clear_snapshot(eid)
        if initial:
            actual={j['jobId']:j['config']['actionId'] for j in jobs.list_jobs(self.root) if is_action_job(j)}
            state=self.actions.read()
            existing=state.get('jobReferences',{})
            needs=any(existing.get(jid,{}).get('actionIds',[existing.get(jid,{}).get('actionId')])!=[aid] for jid,aid in actual.items())
            needs=needs or any(r.get('jobRoot')==str(self.root) and jid not in actual for jid,r in existing.items())
            if needs:
                def reconcile(state):
                    refs=state.setdefault('jobReferences',{})
                    for jid,r in list(refs.items()):
                        if r.get('jobRoot')==str(self.root) and jid not in actual:refs.pop(jid)
                    for jid,aid in actual.items():
                        refs[jid]={'actionId':aid,'actionIds':[aid],'position':'/config/actionId','jobRoot':str(self.root)}
                self.templates.transaction(reconcile)

    def delete(self,jid,expected):
        with jobs._lock,jobs._job_lock(jid,self.root):
            job=self.get(jid);revision(job['configRevision'],expected)
            if job.get('execution') or jid in self.admissions:raise Conflict('Job running or cleaning')
            jobs._job_path(jid,self.root).unlink()
        def detach(state):state.setdefault('jobReferences',{}).pop(jid,None)
        self.templates.transaction(detach)
        return {'deleted':True,'jobId':jid}

    async def cancel(self,jid,expected):
        def disable():
            with jobs._lock,jobs._job_lock(jid,self.root):
                job=self.get(jid);revision(job['configRevision'],expected)
                job['enabled']=False;job['config']['enabled']=False;job['configRevision']+=1
                if not job.get('execution'):job['status']='cancelled'
                jobs._write_job(jobs._job_path(jid,self.root),job)
                return job
        job=await io(disable)
        if job.get('execution'):self.cancelled.add(job['execution']['id'])
        task=self.tasks.get(jid) or self.admissions.get(jid)
        if task and task is not asyncio.current_task():
            task.cancel();await asyncio.gather(task,return_exceptions=True)
            self.tasks.pop(jid,None)
        elif job.get('execution'):
            await io(self.finalize,jid,job['execution']['id'],'cancelled')
        return self.view(await io(self.get,jid))

    async def close(self):
        self.closed=True
        async with self.lifecycle:
            await self._close()

    async def _close(self):
        self.closed=True
        if RUNTIMES.get(str(self.root)) is self:RUNTIMES.pop(str(self.root),None)
        if not self.owned:return
        try:
            pending=set(self.tasks.values()) | set(self.admissions.values())
            pending.discard(asyncio.current_task())
            for task in pending:task.cancel()
            await asyncio.gather(*pending,return_exceptions=True)
            self.tasks.clear()
            await io(self.recover)
        finally:
            await io(self.ownership.release)
            self.owned=False
