"""Action-reference dispatch within the existing generic Job registry/lifecycle."""
import asyncio
import copy
from collections import deque
from datetime import datetime
import time
import uuid

from packages.core import background_jobs as jobs
from packages.plugins.lifecycle import io
from .action_templates import Templates, Conflict, CreationPending, config_value, digest, generation, integrity, revision, conservative_config, reference_generation
from . import chains
from .final_runtime import FinalRuntime
from .runtime_owner import legacy_writer
from .final_runtime.ledger import admission_for_cause, cause as canonical_cause, new_id as canonical_id

RUNTIMES = {}


class SnapshotPersistenceError(RuntimeError):
    pass


class MonitoringIncomplete(RuntimeError):
    """A monitoring Action exited without its declared structured completion signal."""


class RecoveryBlocked(RuntimeError):
    pass


class AuthorizationRevoked(RuntimeError):
    """Capability was withdrawn before an effect was admitted."""


def is_action_job(job):
    return isinstance(job,dict) and isinstance(job.get('action'),dict) and job['action'].get('api')=='action_reference'


def is_protected_job(job):
    from packages.automation.quota_policy import protected
    return is_action_job(job) or protected(job)


def for_root(root):
    from pathlib import Path
    return RUNTIMES.get(str(Path(root).resolve()))


def config_job(root, intent):
    # Action Jobs own their record construction. Scheduler retains only the
    # temporary entry-calculator adapter; S04's Job builder is not used here.
    from packages.scheduler.store import _entries_next_fire, _spec_to_entry
    cfg=conservative_config(intent['config']);created_iso=datetime.now().astimezone().isoformat();now_ts=time.time()
    specs=cfg['schedule'] or [{'kind':'interval','intervalSec':3600}]
    entries=[_spec_to_entry(raw,default_misfire=cfg['misfirePolicy'],created_iso=created_iso) for raw in specs]
    job={'jobId':intent['jobId'],'kind':jobs.SCHEDULED_TASK_KIND,'taskId':'sch_'+uuid.uuid4().hex[:24],
         'name':cfg['name'],'description':cfg['description'],'targetSessionId':None,
         'sourceStruct':jobs.normalize_source({'type':'system'}),'targetStruct':jobs.normalize_target(None),'text':'',
         'action':{'api':'action_reference','actionId':cfg['actionId'],'arguments':copy.deepcopy(cfg['arguments'])},
         'source':'automation','creatorSessionId':None,'enabled':cfg['enabled'],'paused':False,
         'schedule':entries,'misfirePolicy':cfg['misfirePolicy'],'nextFireAt':_entries_next_fire(entries),
         'lastFireAt':None,'lastStatus':None,'lastError':None,'runCount':0,'maxRuns':cfg['maxRuns'],
         'status':'pending','runStartedAt':None,'undeliveredFires':[],
         'createdAt':datetime.fromisoformat(created_iso).timestamp(),'updatedAt':now_ts}
    if cfg.get('trigger',{}).get('kind') in ('manual','immediate','event'):
        job.update(schedule=[],nextFireAt=None)
    job.update(config=copy.deepcopy(cfg),configRevision=1,
               referenceGeneration=intent['referenceGeneration'],creationFingerprint=intent['fingerprint'],
               status='scheduled' if cfg['enabled'] else 'pending',creationEventPending=True,
               templateId=intent.get('templateId'), authorization=copy.deepcopy(intent.get('authorization', {'type':'user'})))
    legacy=intent.get('legacy')
    if isinstance(legacy,dict):
        job.update(copy.deepcopy(legacy.get('fields',{})), legacyCompat=legacy.get('kind'))
        job['kind']=legacy['kind']
    if intent.get('templateId'):
        job.update(templateId=intent['templateId'],templateRevision=intent['templateRevision'])
    return job


class Runtime:
    def __init__(self, root, actions, pause):
        from pathlib import Path
        self.root=Path(root).resolve();self.actions=actions;self.templates=Templates(actions);self.pause=pause
        self.final=FinalRuntime(self.root)
        from packages.jobs.independent_service import ServiceLock
        self.ownership=ServiceLock(self.actions.root/'job-runtime');self.owned=False;self.lifecycle=asyncio.Lock()
        self.tasks={};self.admissions={};self.closed=False;self.diagnostics=deque(maxlen=100);self.limit=5;self.cancelled=set()
        self.task_jobs={};self.admission_jobs={};self.authorization=None

    async def _check_authorization(self, source, action=None, expected_revision=None, *, creating=False, enabled=False):
        if source.get('type') != 'agent':
            return None
        if self.authorization is None:
            raise AuthorizationRevoked('Delegated authorization unavailable')
        capabilities = [('job','execute')]
        if creating:
            capabilities = [('jobTemplate','use'), ('job','create')]
            if enabled:capabilities.append(('job','lifecycle'))
        if action and action.get('system'):
            capabilities.append(('action','manageSystemActions'))
        revision_value = None
        for kind, capability in capabilities:
            allowed, current = await io(self.authorization, kind, capability)
            if not allowed or (expected_revision is not None and current != expected_revision):
                raise AuthorizationRevoked('Delegated authorization changed before effect admission')
            if revision_value is not None and current != revision_value:
                raise AuthorizationRevoked('Delegated authorization changed during admission')
            revision_value = current
        return revision_value

    @staticmethod
    def active(job):
        value=copy.deepcopy(job.get('activeExecutions',{}))
        if job.get('execution'):value.setdefault(job['execution']['id'],copy.deepcopy(job['execution']))
        return value

    def live_for(self,jid):
        return [key for key,task in self.tasks.items() if self.task_jobs.get(key,key)==jid and not task.done()]

    def admitting(self,jid):
        return any(owner==jid for owner in self.admission_jobs.values())

    @legacy_writer(lambda self, *args, **kwargs: self.root.parent, reservation=True)
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
            from .system_actions import install
            await io(install,self.actions)
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
        job=copy.deepcopy(job);job['config']=conservative_config(job['config'])
        state=self.actions.read() if state is None else state
        result=jobs.job_public_view(copy.deepcopy(job))
        result['integrity']=integrity(state,job['config'],job['referenceGeneration'])
        result['enabled']=(job['enabled'] and result['integrity']['status']=='complete' and not result['integrity']['approvalRequired'])
        if getattr(self,'setup_pending',None) and self.setup_pending(job['jobId']):result['enabled']=False
        result['config']['enabled']=result['enabled']
        from .unified import project
        result.pop('unified',None)
        result['unified']=project(result)
        return result

    def list(self):
        state=self.actions.read()
        return [self.view(j,state) for j in jobs.list_jobs(self.root) if is_action_job(j)]

    def get(self,jid):
        job=jobs.get(jid,self.root)
        if not is_action_job(job):raise KeyError(jid)
        job['config']=conservative_config(job['config'])
        return job

    @legacy_writer(lambda self, *args, **kwargs: self.root.parent, reservation=True)
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

    @legacy_writer(lambda self, *args, **kwargs: self.root.parent, reservation=True)
    def create(self,raw,authorized=False,authority=None,*,creation_key=None,legacy=None):
        def change(state):
            cfg=config_value(raw,state)
            if any(not state['actions'][aid]['enabled'] or state.get('actionLifecycle',{}).get(aid) is False for aid in chains.action_ids(cfg)):
                raise Conflict('Action disabled; new Job refused')
            if cfg['enabled'] and not authorized:raise PermissionError('Agent may create disabled Job draft only')
            jid='job_'+(digest('automation:'+str(self.root)+':'+creation_key)[:24] if creation_key else uuid.uuid4().hex[:24]);key=digest(jid)
            prior=state.get('jobInstantiations',{}).get(key)
            if prior:return copy.deepcopy(prior)
            intent=dict(key=key,jobId=jid,config=cfg,referenceGeneration=reference_generation(state,cfg),
                        fingerprint=digest({'jobId':jid,'config':cfg}),phase='reserved',jobRoot=str(self.root),
                        authorization=copy.deepcopy(authority or {'type':'user'}),
                        legacy=copy.deepcopy(legacy) if legacy else None)
            if len(state.setdefault('jobInstantiations',{}))>=10000:raise ValueError('Instantiation capacity reached')
            state['jobInstantiations'][key]=intent
            state.setdefault('jobReferences',{})[jid]={'actionId':cfg['actionId'],'actionIds':chains.action_ids(cfg),'position':'/config/actionId','jobRoot':str(self.root)}
            return copy.deepcopy(intent)
        intent=self.templates.transaction(change)
        if intent['phase']=='created':return self.view(self.get(intent['jobId']))
        return self.publish_intent(intent)

    @legacy_writer(lambda self, *args, **kwargs: self.root.parent, reservation=True)
    def create_legacy(self, kind, fields, authority=None, *, creation_key=None):
        """Create a legacy-shaped record whose only executor is a system Action."""
        from .legacy_compat import config_for
        config, storage = config_for(kind, fields)
        return self.create(config, True, authority or {'type': 'user'},
                           creation_key=creation_key,
                           legacy={'kind': kind, 'fields': storage})

    @legacy_writer(lambda self, *args, **kwargs: self.root.parent, reservation=True)
    def instantiate(self,tid,expected,key,overrides=None,admitted_template=None,authority=None):
        scoped=digest({'root':str(self.root),'key':key})
        intent=self.templates.reserve(tid,expected,scoped,overrides,job_root=str(self.root),admitted_template=admitted_template,authority=authority)
        return self.publish_intent(intent)

    @legacy_writer(lambda self, *args, **kwargs: self.root.parent, reservation=True)
    async def instantiate_action(self,action,key,fixed=None,authority=None,authorization_revision=None):
        ex=action['execution']
        if not action['enabled'] or ex['kind']!='job_template' or action['effect']!='write_local':
            raise PermissionError('Fixed template capability unavailable')
        template=fixed or (await io(self.actions.read)).get('jobTemplates',{}).get(ex['templateId'],{})
        await self._check_authorization(authority or {'type':'user'}, action, authorization_revision,
                                        creating=True,enabled=template.get('config',{}).get('enabled',False))
        try:job=await io(self.instantiate,ex['templateId'],ex['templateRevision'],'action:'+action['id']+':'+key,None,fixed,authority)
        except CreationPending as exc:
            from packages.automation.engine import PersistenceError
            raise PersistenceError(str(exc)) from exc
        return {'ok':True,'jobId':job['jobId'],'created':True,'jobCompleted':False,
                'templateId':ex['templateId'],'templateRevision':ex['templateRevision']}

    @legacy_writer(lambda self, *args, **kwargs: self.root.parent, reservation=True)
    def update(self,jid,raw,expected,authorized=False,authority=None):
        # Action reference validation/index is committed first; Job publication
        # validates its current revision under its own lock. Stale index entries
        # only conservatively require delete confirmation, never grant execution.
        old=self.get(jid);revision(old['configRevision'],expected)
        def validate(state):
            cfg=config_value(raw,state);aid=cfg['actionId'];gen=reference_generation(state,cfg)
            if cfg['overlap']!=old['config']['overlap'] and not authorized:
                raise PermissionError('User Bearer required to change overlap policy')
            maximum=cfg['overlap']['parallelLimit'] if cfg['overlap']['mode']=='parallel' else 1
            if len(self.active(old))>maximum:raise Conflict('Active execution count exceeds new overlap limit')
            repair=old['config']['actionId']!=aid or old['referenceGeneration']!=gen
            if not authorized and (cfg['enabled'] or old['enabled'] or repair):
                raise PermissionError('User Bearer required for Job enable/edit/repair')
            if repair:cfg['enabled']=False
            refs=state.setdefault('jobReferences',{})
            previous=refs.get(jid,{})
            refs[jid]={'actionId':aid,'actionIds':list(dict.fromkeys([*previous.get('actionIds',chains.action_ids(old['config'])),*chains.action_ids(cfg)])),
                       'position':'/config/actionId','jobRoot':str(self.root),'configRevision':expected+1}
            return cfg,gen
        cfg,gen=self.templates.transaction(validate)
        with jobs._lock,jobs._job_lock(jid,self.root):
            old=self.get(jid);revision(old['configRevision'],expected)
            maximum=cfg['overlap']['parallelLimit'] if cfg['overlap']['mode']=='parallel' else 1
            if len(self.active(old))>maximum:raise Conflict('Active execution count exceeds new overlap limit')
            intent={'jobId':jid,'config':cfg,'fingerprint':old['creationFingerprint'],'referenceGeneration':gen,
                    'authorization':copy.deepcopy(authority or {'type':'user'})}
            replacement=config_job(self.root,intent)
            for key in ('taskId','createdAt','runCount','templateId','templateRevision','execution','executionFinalization','lastExecution','lastStatus','attempts','runId','nextAttemptAt','snapshotCleanup','activeExecutions','executionFinalizations','runAuthorization','paused','terminalOutbox','pendingRuns','eventReceipts','recoveryFailures','runPolicy','runRecoveryEligible','chainRecovery','runRecoveries','runSummaries','finalRuntime'):
                if key in old:replacement[key]=copy.deepcopy(old[key])
            if cfg['schedule']==old['config']['schedule']:
                replacement['schedule']=copy.deepcopy(old['schedule'])
                replacement['nextFireAt']=old.get('nextFireAt')
            if old['status'] in {'running','cleaning'}:replacement['status']=old['status']
            if old['status']=='restarting' and old.get('chainRecovery'):replacement['status']='restarting'
            if old.get('legacyCompat'):
                replacement['kind']=old['kind']
                replacement['legacyCompat']=old['legacyCompat']
                args=cfg.get('arguments',{})
                if old['legacyCompat']== 'background-process':
                    replacement.update(targetSessionId=args.get('sessionId'),argv=copy.deepcopy(args.get('argv',[])),
                                       cwd=args.get('cwd'),hideWindow=args.get('hideWindow',True))
                elif old['legacyCompat']=='session-message':
                    replacement.update(targetSessionId=args.get('sessionId'),targetSessionIds=None,text=args.get('text',''))
                elif old['legacyCompat']=='session-broadcast':
                    replacement.update(targetSessionId=None,targetSessionIds=copy.deepcopy(args.get('sessionIds',[])),text=args.get('text',''))
            if authority is not None and not self.active(old):
                replacement['runAuthorization']=copy.deepcopy(authority)
            replacement['configRevision']=expected+1
            if not cfg['enabled']:
                for waiting in replacement.get('pendingRuns',[]):
                    run_id=waiting.get('runId')
                    if run_id and replacement.get('finalRuntime',{}).get('runs',{}).get(run_id,{}).get('state')=='queued':
                        self.final.ledger.request_cancel(replacement,self.root,run_id,request_key='legacy-disable:'+run_id+':'+uuid.uuid4().hex,authority=replacement.get('authorization'))
                        self.final.ledger.transition_run(replacement,self.root,run_id,'cancelled')
                        operation_id=waiting.get('operationId')
                        if operation_id in replacement.get('finalRuntime',{}).get('operations',{}):
                            self.final.ledger.complete_operation(replacement,self.root,operation_id,{'jobId':jid,'runId':run_id,'state':'cancelled'},run_id=run_id,admission_id=waiting.get('admissionId'))
                replacement['pendingRuns']=[]
            jobs._write_job(jobs._job_path(jid,self.root),replacement)
        def narrow(state):
            ref=state['jobReferences'].get(jid,{})
            if ref.get('configRevision')==expected+1 and ref.get('actionId')==cfg['actionId']:
                ref['actionIds']=chains.action_ids(cfg)
        self.templates.transaction(narrow)
        return self.view(replacement)

    def _freeze_new(self,job,eid,waiting,run_id):
        """Freeze all executable inputs at Admission, including queued Runs."""
        config=job['config']
        context={'id':run_id,'type':'jobs.execute','payload':copy.deepcopy((waiting or {}).get('jobInput',config['arguments'])),
                 'jobRoot':str(self.root),'jobName':job['name'],'jobId':job['jobId'],
                 'dispatchKey':run_id,'fireAt':datetime.now().isoformat()}
        if waiting and waiting.get('event'):
            event=copy.deepcopy(waiting['event'])
            context={**event,**context,'type':event['type'],'payload':event['payload'],'eventId':event['id']}
        input_value=(waiting or {}).get('jobInput',config['arguments'])
        if 'steps' in config:
            state=self.actions.read()
            checked=integrity(state,config,job['referenceGeneration'])
            if checked['status']!='complete' or checked['approvalRequired']:raise Conflict('Action chain requires reference repair')
            snapshot=chains.freeze(config['steps'],state,input_value,context,
                completion=config['completion'],config_revision=job['configRevision'])
            snapshot['trigger']=copy.deepcopy(config['trigger'])
            snapshot['digest']=digest({k:v for k,v in snapshot.items() if k!='digest'})
            return snapshot,snapshot,context
        from packages.actions.store import validate_action
        from .action_templates import filled_arguments
        state=self.actions.read();action=validate_action(state['actions'][config['actionId']])
        args=filled_arguments(action,input_value)
        snapshot=dict(definition=copy.deepcopy(action),arguments=args,context=copy.deepcopy(context),
            actionId=config['actionId'],actionRevision=state.get('actionRevisions',{}).get(config['actionId'],0),
            registryRevision=state['revision'],contextDigest=digest(context),digest=digest(action),
            argumentsDigest=digest(args),checkpoint={},jobId=job['jobId'],taskId=job['taskId'],jobRoot=str(self.root),
            trigger=copy.deepcopy(config['trigger']))
        if action['execution']['kind']=='job_template':
            fixed=state['jobTemplates'][action['execution']['templateId']]
            view=self.templates.view(fixed,state)
            if not view['usable']:raise Conflict('Fixed Job Template unavailable')
            snapshot['fixedTemplate']=copy.deepcopy(view)
        return snapshot,None,context

    @legacy_writer(lambda self, *args, **kwargs: self.root.parent, reservation=True)
    def claim(self,jid,eid,expected=None,entry=None,queued=None,confirmed=False,authority=None,authorization_revision=None,rerun_of=None,operation_id=None,provenance=None):
        with jobs._lock,jobs._job_lock(jid,self.root):
            job=self.get(jid)
            if expected is not None:revision(job['configRevision'],expected)
            active=self.active(job)
            policy=job['config'].get('overlap',{})
            maximum=policy.get('parallelLimit',3) if policy.get('mode')=='parallel' else 1
            if len(active)>=maximum:raise Conflict('Job overlap capacity reached')
            if not job['enabled'] and not confirmed:raise Conflict('Job disabled')
            if job.get('paused'):raise Conflict('Job paused')
            confirmation=job.get('recoveryConfirmation') if confirmed else None
            if confirmed and (not confirmation or confirmation.get('status')!='reserved' or confirmation['configRevision']!=job['configRevision']):
                raise Conflict('Explicit recovery confirmation no longer current')
            if queued is not None:
                waiting=next((r for r in job.get('pendingRuns',[]) if r['id']==queued),None)
                if waiting is None:raise Conflict('Queued Run no longer current')
                if waiting['configRevision']!=job['configRevision'] and not waiting.get('runId'):raise Conflict('Queued Run configuration changed')
                job['pendingRuns']=[r for r in job.get('pendingRuns',[]) if r['id']!=queued]
            config=job['config']
            recovering=job.get('status')=='restarting' or confirmed
            if recovering and not confirmed and job.get('runRecoveryEligible') is not True:raise Conflict('Original Run lacks frozen recovery eligibility')
            chain_recovery=copy.deepcopy(job.get('chainRecovery')) if recovering else None
            run_id=confirmation['runId'] if confirmed else job['runId'] if recovering else waiting.get('runId') if queued is not None else canonical_id('run_')
            if not run_id:run_id=canonical_id('run_')
            frozen=job.get('finalRuntime',{}).get('plans',{}).get(run_id,{}).get('frozen')
            if frozen is not None:
                plan=copy.deepcopy(frozen)
                chain_snapshot=copy.deepcopy(plan) if 'steps' in plan else None
                context=copy.deepcopy(plan.get('event',plan.get('context',{})))
            elif chain_recovery:
                chain_snapshot=chain_recovery['snapshot'];plan=copy.deepcopy(chain_snapshot)
                context=copy.deepcopy(chain_snapshot['event'])
            else:
                plan,chain_snapshot,context=self._freeze_new(job,eid,waiting if queued is not None else None,run_id)
            source=copy.deepcopy(authority or (job.get('runAuthorization') if recovering else None) or job.get('authorization', {'type':'user'}))
            job['runAuthorization']=source
            if not recovering:
                job['runPolicy']={key:copy.deepcopy(config[key]) for key in ('recovery','completion','notification')}
                job['recoveryFailures']=0
            policy=copy.deepcopy(job.get('runPolicy') or {key:config[key] for key in ('recovery','completion','notification')})
            attempt_count=1+sum(a.get('runId')==run_id for a in job.get('attempts',[]))
            if len(job.get('attempts',[]))>=10000:raise Conflict('Attempt audit capacity reached')
            attempt_id='attempt_'+uuid.uuid4().hex[:24]
            execution={'id':eid,'runId':run_id,'attemptId':attempt_id,'attemptCount':attempt_count,
                       'startedAt':time.time(),
                       'policy':policy,'authorization':source,'authorizationRevision':authorization_revision,
                       'jobRoot':str(self.root),'phase':'preparing','entryId':entry,'context':context,
                       'actionId':config['actionId'],'actionRevision':job.get('referenceGeneration',0),
                       'templateRevision':job.get('templateRevision'),'backoffSec':0,
                       'receipt':None,'sideEffectUncertainty':False}
            if queued is not None and 'jobInput' in waiting:execution['inputArguments']=copy.deepcopy(waiting['jobInput'])
            if queued is not None and waiting.get('policy'):execution['policy']=copy.deepcopy(waiting['policy'])
            if not chain_snapshot:execution['canonicalPlan']=copy.deepcopy(plan)
            if chain_snapshot:
                execution.update(chainSnapshot=chain_snapshot,configRevision=chain_snapshot['configRevision'],
                    stepExecutions=copy.deepcopy(chain_recovery['steps']) if chain_recovery else chains.records(chain_snapshot,run_id))
                execution['context']=copy.deepcopy(context)
                execution['context']['id']=eid
                execution['context']['dispatchKey']=eid
            job.pop('chainRecovery',None)
            if confirmed:
                execution['recoveryCheckpoint']=copy.deepcopy(confirmation['checkpoint'])
                job['recoveryConfirmation']={**confirmation,'status':'consumed','attemptId':attempt_id}
            elif recovering:
                execution['recoveryCheckpoint']=self.selection_audit(job.get('lastExecution',{}))
            if confirmed and operation_id is None:operation_id=confirmation.get('operationId')
            if run_id in job.get('finalRuntime',{}).get('runs',{}):
                self.final.ledger.start(job,self.root,run_id,retry=recovering)
                admission_id=job['finalRuntime']['runs'][run_id]['admissionId']
            else:
                run_cause=copy.deepcopy(waiting.get('cause')) if queued is not None and waiting.get('cause') else canonical_cause()
                admission=self.final.ledger.admit(job,self.root,plan=plan,cause=run_cause,authority=source,
                    admission_id='adm_'+eid,run_id=run_id,rerun_of=rerun_of,operation_id=operation_id,notification=execution['policy'].get('notification',{}),
                    authorization_revision=authorization_revision)
                admission_id=admission['admissionId']
                self.final.ledger.start(job,self.root,run_id)
            execution['admissionId']=admission_id
            if queued is not None and waiting.get('receiptKey'):execution['eventReceiptKey']=waiting['receiptKey']
            if provenance:job['finalRuntime']['plans'][run_id].update(copy.deepcopy(provenance))
            if operation_id:
                self.final.ledger.complete_operation(job,self.root,operation_id,{'jobId':jid,'runId':run_id},run_id=run_id,admission_id=admission_id)
            job.setdefault('attempts',[]).append({'id':attempt_id,'runId':run_id,'count':attempt_count,'status':'running',
                         'actionId':config['actionId'],'templateRevision':job.get('templateRevision'),
                         'backoffSec':0,'receipt':None,'sideEffectUncertainty':False})
            job['runId']=run_id
            active[eid]=execution
            job.update(status='running',activeExecutions=active,execution=next(iter(active.values())),runStartedAt=time.time())
            jobs._write_job(jobs._job_path(jid,self.root),job)
            result=copy.deepcopy(job);result['execution']=copy.deepcopy(execution)
            result['config'].update(copy.deepcopy(policy))
            return result

    @legacy_writer(lambda self, *args, **kwargs: self.root.parent, reservation=True)
    def finalize(self,jid,eid,status,audit=None):
        # A durable finalization intent precedes destructive snapshot cleanup.
        with jobs._lock,jobs._job_lock(jid,self.root):
            job=self.get(jid)
            active=self.active(job)
            if eid not in active:return
            execution=active[eid]
            prior=job.get('executionFinalizations',{}).get(eid) or (job.get('executionFinalization') if job.get('execution',{}).get('id')==eid else None)
            outcome=prior or {'status':status,'audit':audit or {}}
            attempt_id=execution.get('attemptId')
            canonical_attempt=job.get('finalRuntime',{}).get('attempts',{}).get(attempt_id)
            if canonical_attempt and canonical_attempt['state']=='running':
                self.final.ledger.record_effect(job,self.root,attempt_id,outcome['status'],outcome.get('audit',{}))
                self.final.ledger.finish_attempt(job,self.root,attempt_id,outcome['status'])
            canonical_run=job.get('finalRuntime',{}).get('runs',{}).get(execution.get('runId'))
            if canonical_run:
                if outcome['status']=='restarting' and canonical_run['state']=='running':
                    self.final.ledger.transition_run(job,self.root,execution['runId'],'retry_wait')
                elif outcome['status']!='restarting' and canonical_run['state'] in {'queued','running','cancelling'}:
                    target='cancelling' if canonical_run['state']=='cancelling' else 'settling'
                    if target=='settling':self.final.ledger.transition_run(job,self.root,execution['runId'],'settling')
            for attempt in job.get('attempts',[]):
                if attempt.get('id')==attempt_id:
                    attempt.update(status='cleaning',terminalStatus=outcome['status'],receipt=outcome.get('audit',{}).get('receipt'),
                                   sideEffectUncertainty=outcome['status'] in {'unknown','uncertain'})
                    break
            job.setdefault('executionFinalizations',{})[eid]=outcome
            job.setdefault('terminalOutbox',{})['cleaning:'+eid]={'id':'cleaning:'+eid,'runId':execution.get('runId'),
                'attemptId':attempt_id,'status':'cleaning','notificationPolicy':copy.deepcopy(execution.get('policy',{}).get('notification',{}))}
            execution['phase']='cleaning';active[eid]=execution
            job.update(status='cleaning',snapshotCleanup='pending',activeExecutions=active,execution=next(iter(active.values())))
            if job['execution']['id']==eid:job['executionFinalization']=outcome
            jobs._write_job(jobs._job_path(jid,self.root),job)
        self.templates.clear_snapshot(eid)
        with jobs._lock,jobs._job_lock(jid,self.root):
            job=self.get(jid)
            active=self.active(job)
            if eid not in active:return
            execution_audit=active.pop(eid)
            job.pop('execution',None);job.pop('executionFinalization',None)
            job.get('executionFinalizations',{}).pop(eid,None)
            job['activeExecutions']=active
            summary={'id':eid,'runId':execution_audit.get('runId') or job.get('runId'),'attemptId':execution_audit.get('attemptId'),
                     'authorization':copy.deepcopy(execution_audit.get('authorization',{'type':'user'})),
                     'executionKind':execution_audit.get('executionKind'),'status':outcome['status'],'notificationPolicy':copy.deepcopy(execution_audit.get('policy',{}).get('notification',{})),**outcome['audit']}
            if execution_audit.get('chainSnapshot'):
                summary['stepExecutions']=copy.deepcopy(execution_audit['stepExecutions'])
                summary['configRevision']=execution_audit.get('configRevision')
                if outcome['status'] in {'failed','restarting'}:
                    recovery={'snapshot':copy.deepcopy(execution_audit['chainSnapshot']),
                        'steps':copy.deepcopy(execution_audit['stepExecutions']),
                        'safeBoundary':bool(outcome['audit'].get('safeBoundary'))}
                    job['chainRecovery']=recovery
                    job.setdefault('runRecoveries',{})[summary['runId']]=copy.deepcopy(recovery)
                elif outcome['status'] in {'completed','cancelled'}:
                    job.setdefault('runRecoveries',{}).pop(summary['runId'],None)
            job.setdefault('runSummaries',{})[summary['runId']]=copy.deepcopy(summary)
            event_id=execution_audit.get('context',{}).get('eventId')
            if event_id:
                summary['eventId']=event_id
                job.setdefault('eventReceipts',{})[execution_audit.get('eventReceiptKey',event_id)]={'runId':summary['runId'],'status':outcome['status']}
            for attempt in job.get('attempts',[]):
                if attempt.get('id')==execution_audit.get('attemptId'):
                    attempt.update(status=outcome['status'],snapshotCleanup='cleaned',finishedAt=time.time())
            job['snapshotCleanup']='pending' if job.get('executionFinalizations') else 'active' if active else 'cleaned'
            self.final.ledger.project_summary(job,summary)
            job.update(runStartedAt=None,
                       runCount=int(job.get('runCount',0))+(1 if execution_audit.get('attemptCount',1)==1 else 0),terminalEventId=eid)
            policy=execution_audit.get('policy',{}).get('recovery',job.get('config',{}).get('recovery',{}))
            safe_boundary=outcome.get('audit',{}).get('safeBoundary') is True
            if outcome['status']=='restarting' and policy.get('enabled') and not safe_boundary:
                reset=policy.get('stableResetSec',300)
                stable=reset>0 and time.time()-execution_audit.get('startedAt',time.time())>=reset
                count=(0 if stable else job.get('recoveryFailures',0))+(0 if outcome.get('audit',{}).get('gateBlocked') else 1)
                job['recoveryFailures']=count
                backoff=policy.get('backoffSec',[5,15,60])[min(max(count-1,0),2)]
                job['nextAttemptAt']=time.time()+backoff
                for attempt in job.get('attempts',[]):
                    if attempt.get('id')==execution_audit.get('attemptId'):
                        attempt.update(backoffSec=backoff,nextAttemptAt=job['nextAttemptAt'])
            elif outcome['status']!='restarting' or safe_boundary:
                job.pop('nextAttemptAt',None)
            cfg=job['config'];once=all(e['kind']=='once' and not e.get('enabled',True) for e in job['schedule'])
            # The claimed entry is consumed at dispatch admission, not recovery.
            exhausted=not any(e.get('enabled',True) and e.get('nextFireAt') for e in job['schedule'])
            if outcome['status'] in {'uncertain','cancelled'} or (not job.get('pendingRuns') and outcome['status']!='restarting' and
                    ((exhausted and (cfg['trigger']['kind'] not in ('event','manual') or job.get('legacySchedulerCompat'))) or (cfg['maxRuns'] and job['runCount']>=cfg['maxRuns']))):
                job['enabled']=False;job['config']['enabled']=False
            job['status']=('restarting' if outcome['status']=='restarting' else ('scheduled' if job['enabled'] else outcome['status']))
            if active:
                job['execution']=next(iter(active.values()))
                job['status']='running' if any(ex['phase']!='cleaning' for ex in active.values()) else 'cleaning'
            canonical_run=job.get('finalRuntime',{}).get('runs',{}).get(summary['runId'])
            if canonical_run and canonical_run['state']!='retry_wait':
                target={'completed':'completed','failed':'failed','cancelled':'cancelled','uncertain':'uncertain','unknown':'uncertain'}.get(outcome['status'])
                if target and canonical_run['state']!=target:
                    self.final.ledger.transition_run(job,self.root,summary['runId'],target)
            if canonical_run:
                self.final.ledger.ensure_outbox(job,self.root,summary['runId'],eid,summary)
            job.setdefault('terminalOutbox',{})[eid]=copy.deepcopy(summary)
            record={'run_id':eid,'task_id':job['taskId'],'dispatch_key':eid,
                    'status':outcome['status'],'result':summary,'actual_at':datetime.now().isoformat()}
            jobs._append_run_record_once(record,registry_root=self.root)
            jobs._write_job(jobs._job_path(jid,self.root),job)

    @legacy_writer(lambda self, *args, **kwargs: self.root.parent, reservation=True)
    async def publish_events(self):
        for job in await io(jobs.list_jobs,self.root):
            if not is_action_job(job):continue
            if job.get('creationEventPending'):
                if await jobs._scheduled_task_emit_strict({'type':'job.created','jobId':job['jobId'],'job':await io(self.view,job)}):
                    await io(jobs._update,job['jobId'],{'creationEventPending':False},registry_root=self.root)
            outbox=job.get('terminalOutbox',{})
            if not outbox and job.get('lastExecution') and job.get('terminalEventEmittedFor')!=job['lastExecution']['id']:
                outbox={job['lastExecution']['id']:job['lastExecution']}
            for eid,summary in outbox.items():
                view=await io(self.view,await io(self.get,job['jobId']))
                if not await jobs._scheduled_task_emit_strict({'type':'job.updated','jobId':job['jobId'],'job':view}):continue
                if not await jobs._scheduled_task_emit_strict({'type':'scheduler.task.fired','taskId':job['taskId'],
                        'dispatchKey':eid,'status':summary['status'],'terminal':True}):continue
                configured=summary.get('notificationPolicy',job.get('config',{}).get('notification',{}))
                suppressed=set();delivery_receipts={}
                if summary['status'] in configured.get('on',[]) and configured.get('target')=='job':
                    if not await jobs._scheduled_task_emit_strict({'type':'job.notification','jobId':job['jobId'],
                            'runId':summary.get('runId'),'attemptId':summary.get('attemptId'),
                            'status':summary['status']}):continue
                    delivered=True
                    for sid in configured.get('sessionIds',[]):
                        try:
                            await self._check_authorization(summary.get('authorization',job.get('authorization',{'type':'user'})))
                        except AuthorizationRevoked:
                            suppressed.add(sid)
                            continue  # Terminal suppression, not a delivery acknowledgement.
                        import json
                        result=await jobs._worker.enqueue_notice(sid,json.dumps({'jobId':job['jobId'],'runId':summary.get('runId'),
                            'attemptId':summary.get('attemptId'),'status':summary['status']}),source='automation',
                            event_id='job-result:'+eid+':'+sid,notice_kind='job_result',job_id=job['jobId'],notice_status=summary['status'])
                        if not result.get('ok'):delivered=False
                        else:delivery_receipts[sid]=copy.deepcopy(result)
                    if not delivered:continue
                def mark():
                    with jobs._lock,jobs._job_lock(job['jobId'],self.root):
                        current=self.get(job['jobId'])
                        current.get('terminalOutbox',{}).pop(eid,None)
                        self.final.ledger.ack_outbox(current,self.root,eid,suppressed=suppressed,receipts=delivery_receipts)
                        if current.get('lastExecution',{}).get('id')==eid:current['terminalEventEmittedFor']=eid
                        jobs._write_job(jobs._job_path(job['jobId'],self.root),current)
                await io(mark)

    @legacy_writer(lambda self, *args, **kwargs: self.root.parent, reservation=True)
    async def execute(self,job):
        from packages.actions.executor import execute_dynamic
        if job['execution'].get('chainSnapshot'):
            from .chain_runtime import execute_job_chain
            return await execute_job_chain(self,job)
        eid=job['execution']['id'];audit={};outcome='uncertain'
        try:
            snapshot=await io(self.templates.snapshot,job,job['execution'])
            audit={k:snapshot[k] for k in ('actionId','actionRevision','registryRevision','digest','argumentsDigest','contextDigest')}
            audit['executionKind']=snapshot['definition']['execution']['kind']
            await io(self.action_audit,job['jobId'],eid,audit,snapshot['definition'].get('recoverable') is True and job['execution']['policy']['recovery']['enabled'])
            snapshot['context']['attemptId']=job['execution']['attemptId']
            snapshot['context']['effectKey']=self.final.ledger.effect_key(job['execution']['runId'],'main',snapshot['actionId'])
            async def progress(**fields):
                try:await io(self.templates.checkpoint,eid,fields)
                except Exception as exc:raise SnapshotPersistenceError(str(exc)) from exc
            progress.checkpoint={'id':job['execution']['runId'],**copy.deepcopy(job['execution'].get('recoveryCheckpoint',{}))}
            progress.instantiate_template=lambda a,key:self.instantiate_action(a,key,snapshot.get('fixedTemplate'),job['execution']['authorization'],job['execution'].get('authorizationRevision'))
            from .retired import reject_execution as execute_system
            progress.execute_system=execute_system
            if job['execution']['attemptCount']>1 and job['execution']['policy']['recovery']['enabled']:
                from .system_actions import recovery_ready
                if not await io(recovery_ready,snapshot['definition'],snapshot['arguments']):raise RecoveryBlocked('Recovery paused by current business gates')
            query=snapshot.get('trigger',{}).get('query')
            if progress.checkpoint.get('triggerSelection') is not None:
                snapshot['context']['jobSelection']=copy.deepcopy(progress.checkpoint['triggerSelection'])
            elif query is not None and snapshot.get('trigger',{}).get('kind')=='event':
                from packages.actions.session_query import resolve
                selected=await resolve(query)
                selection={'ids':selected['ids'],'candidateCount':selected['candidateCount']}
                await progress(triggerTargetIds=selected['ids'],triggerSelection=selection)
                snapshot['context']['jobSelection']=selection
            async def before_effect():
                current = (await io(self.actions.read))['actions'].get(snapshot['actionId'])
                if not current or not current.get('enabled'):
                    raise AuthorizationRevoked('Action disabled before effect admission')
                await self._check_authorization(job['execution'].get('authorization', {'type':'user'}), current,
                                                job['execution'].get('authorizationRevision'))
            progress.before_effect = before_effect
            await before_effect()
            result=await execute_dynamic(snapshot['definition'],snapshot['arguments'],snapshot['context'],progress,self.pause)
            checkpoint=(await io(self.actions.read)).get('jobExecutions',{}).get(eid,{}).get('checkpoint',{})
            audit.update(self.selection_audit(checkpoint))
            completion=job['execution']['policy'].get('completion',{})
            signal=result.get('structuredContent',{}).get('panJobCompletion') if isinstance(result.get('structuredContent'),dict) else result.get('panJobCompletion')
            if result.get('status') not in {'unknown','uncertain','unconfirmed'} and completion.get('kind')=='monitoring' and not (result.get('processTreeEnded') is True and isinstance(signal,dict) and set(signal)=={'type','completed'} and signal['completed'] is True and signal['type']==completion.get('signalType')):
                raise MonitoringIncomplete('Monitoring Action exited without its structured completion signal')
            outcome=('uncertain' if result.get('status') in {'unknown','uncertain','unconfirmed'} else
                     'completed' if result.get('ok',True) and not result.get('isError',False) else 'failed')
            for key in ('jobId','matched','succeeded','failed'):
                if key=='jobId' and snapshot['definition']['execution']['kind']!='job_template':continue
                if key in result:audit[key if key!='jobId' else 'createdJobId']=result[key]
        except asyncio.CancelledError:
            outcome='cancelled' if eid in self.cancelled else 'uncertain'
        except MonitoringIncomplete as exc:
            outcome='restarting' if job['execution']['policy']['recovery']['enabled'] else 'failed';audit['error']=str(exc)
        except RecoveryBlocked as exc:
            outcome='restarting';audit.update(error=str(exc),gateBlocked=True)
        except AuthorizationRevoked as exc:
            outcome='cancelled';audit['error']=str(exc)
        except Exception as exc:
            from packages.automation.engine import PersistenceError
            outcome='uncertain' if isinstance(exc,(SnapshotPersistenceError,PersistenceError,TimeoutError)) else 'failed';audit['error']=str(exc)[:1000]
        finally:
            try:
                checkpoint=(await io(self.actions.read)).get('jobExecutions',{}).get(eid,{}).get('checkpoint',{})
                audit.update(self.selection_audit(checkpoint))
                await io(self.finalize,job['jobId'],eid,outcome,audit)
                await self.publish_events()
            except Exception as exc:self.diagnostics.append({'jobId':job['jobId'],'phase':'cleanup','error':str(exc)})
            self.cancelled.discard(eid)

    @legacy_writer(lambda self, *args, **kwargs: self.root.parent, reservation=True)
    def chain_save(self,jid,eid,steps,phase):
        with jobs._lock,jobs._job_lock(jid,self.root):
            job=self.get(jid);active=self.active(job)
            if eid not in active:raise SnapshotPersistenceError('Chain execution no longer admitted')
            execution=active[eid];run_id=execution['runId']
            canonical_steps=job.get('finalRuntime',{}).get('steps',{}).get(run_id,{})
            for step in steps:
                canonical=canonical_steps.get(step['stepId'])
                if canonical is None:continue
                if step['status']=='running' and canonical['state'] in {'pending','failed'}:
                    attempt=step['attempts'][-1]
                    self.final.ledger.begin_attempt(job,self.root,run_id,step['stepId'],attempt_id=attempt['id'],lease_id=eid,retry=len(step['attempts'])>1)
                    attempt['effectKey']=self.final.ledger.admit_effect(job,self.root,attempt['id'],step['actionId'])
                elif step['status']=='failed' and canonical['state']=='pending':
                    self.final.ledger.fail_step_without_attempt(job,self.root,run_id,step['stepId'])
                elif step['status'] in {'completed','failed','uncertain','unknown','cancelled','restarting'} and canonical['state']=='running':
                    attempt=next(a for a in job['finalRuntime']['attempts'].values() if a['runId']==run_id and a['stepId']==step['stepId'] and a['state']=='running')
                    self.final.ledger.record_effect(job,self.root,attempt['attemptId'],step['status'],step.get('result',{}))
                    self.final.ledger.finish_attempt(job,self.root,attempt['attemptId'],step['status'])
            active[eid].update(stepExecutions=copy.deepcopy(steps),phase=phase)
            job.update(activeExecutions=active,execution=next(iter(active.values())))
            jobs._write_job(jobs._job_path(jid,self.root),job)

    @staticmethod
    def selection_audit(checkpoint):
        safe={key:copy.deepcopy(checkpoint[key]) for key in ('targetIds','selection','processJobId','triggerTargetIds','triggerSelection') if key in checkpoint}
        if 'targetResults' in checkpoint:
            safe['targetResults']=[{key:row[key] for key in ('sessionId','ok','status','queueItemId','error') if key in row} for row in checkpoint['targetResults']]
        return safe

    @legacy_writer(lambda self, *args, **kwargs: self.root.parent, reservation=True)
    def action_audit(self,jid,eid,audit,eligible):
        with jobs._lock,jobs._job_lock(jid,self.root):
            job=self.get(jid);active=self.active(job)
            if eid not in active:raise SnapshotPersistenceError('Attempt admission changed')
            execution=active[eid]
            if not execution.get('chainSnapshot') and execution['attemptId'] not in job.get('finalRuntime',{}).get('attempts',{}):
                self.final.ledger.begin_attempt(job,self.root,execution['runId'],'main',attempt_id=execution['attemptId'],lease_id=eid,retry=execution['attemptCount']>1)
                execution['effectKey']=self.final.ledger.admit_effect(job,self.root,execution['attemptId'],execution['actionId'])
            active[eid].update(copy.deepcopy(audit))
            for attempt in job.get('attempts',[]):
                if attempt['id']==active[eid]['attemptId']:attempt.update(copy.deepcopy(audit))
            if active[eid]['attemptCount']==1:job['runRecoveryEligible']=bool(eligible)
            job['activeExecutions']=active;job['execution']=next(iter(active.values()))
            jobs._write_job(jobs._job_path(jid,self.root),job)

    @legacy_writer(lambda self, *args, **kwargs: self.root.parent, reservation=True)
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

    @legacy_writer(lambda self, *args, **kwargs: self.root.parent, reservation=True)
    def enqueue(self,jid,expected=None,entry=None,rerun_of=None,operation_id=None,provenance=None,authority=None,authorization_revision=None):
        with jobs._lock,jobs._job_lock(jid,self.root):
            job=self.get(jid)
            if expected is not None:revision(job['configRevision'],expected)
            if not job.get('enabled'):raise Conflict('Job disabled')
            pending=job.setdefault('pendingRuns',[])
            if len(pending)>=128:raise Conflict('Job queued Run capacity reached (128)')
            queued_id='queued_'+uuid.uuid4().hex[:24];run_id=canonical_id('run_')
            waiting={'id':queued_id,'runId':run_id,'configRevision':job['configRevision'],'entryId':entry,'createdAt':time.time(),
                     'cause':canonical_cause(),'policy':{key:copy.deepcopy(job['config'][key]) for key in ('recovery','completion','notification')},
                     'operationId':operation_id,'rerunOf':rerun_of,'provenance':copy.deepcopy(provenance),
                     **({'authorization':copy.deepcopy(authority),'authorizationRevision':authorization_revision} if authority is not None else {})}
            plan,_,_=self._freeze_new(job,queued_id,waiting,run_id)
            admission=self.final.ledger.admit(job,self.root,plan=plan,cause=waiting['cause'],authority=authority or job.get('authorization'),
                admission_id='adm_'+queued_id,run_id=run_id,rerun_of=rerun_of,operation_id=operation_id,notification=waiting['policy']['notification'],
                authorization_revision=authorization_revision)
            if provenance:job['finalRuntime']['plans'][run_id].update(copy.deepcopy(provenance))
            waiting['admissionId']=admission['admissionId'];pending.append(waiting)
            jobs._write_job(jobs._job_path(jid,self.root),job)
            return job

    def event_targets(self,event_type,include_inactive=False):
        return [{'_job':True,'jobId':job['jobId'],'configRevision':job['configRevision'],
                 **({'_admissionStatus':'disabled' if not job['enabled'] else 'unavailable' if not getattr(self,'event_available',lambda _:True)(event_type) else 'eligible'} if include_inactive else {})}
                for job in self.list() if (include_inactive or job['enabled']) and job['config']['trigger']['kind']=='event'
                and event_type in job['config']['trigger']['eventTypes']
                and (include_inactive or getattr(self,'event_available',lambda _:True)(event_type))]

    @legacy_writer(lambda self, *args, **kwargs: self.root.parent, reservation=True)
    def queue_event(self,event,target):
        jid=target['jobId'];trigger_id=target.get('triggerId') or target.get('id')
        with jobs._lock,jobs._job_lock(jid,self.root):
            try:job=self.get(jid)
            except KeyError:return
            receipts=job.setdefault('eventReceipts',{})
            receipt_key=event['id']+(('@'+trigger_id) if trigger_id else '')
            if receipt_key in receipts:return
            run_cause=canonical_cause(event,{**target,'triggerId':trigger_id} if trigger_id else target,job_id=jid)
            if admission_for_cause(job.get('finalRuntime',{}),run_cause):return
            if len(receipts)>=10000:raise Conflict('Job Event dedup capacity reached (10000)')
            eligible=job['enabled'] and job['configRevision']==target['configRevision']
            if not eligible:
                self.final.ledger.admit(job,self.root,plan={'jobId':jid,'configRevision':target['configRevision']},cause=run_cause,
                    authority=target.get('authorization',job.get('authorization')),state='rejected',authorization_revision=target.get('authorizationRevision'))
                receipts[receipt_key]={'status':'cancelled','reason':'Job disabled or configuration changed'}
            elif job['config']['overlap']['mode']=='forbid' and (self.active(job) or job.get('pendingRuns') or job.get('status')=='restarting'):
                admission=self.final.ledger.admit(job,self.root,plan={'jobId':jid,'configRevision':job['configRevision']},cause=run_cause,
                    authority=target.get('authorization',job.get('authorization')),state='skipped',reason='overlap',authorization_revision=target.get('authorizationRevision'))
                receipts[receipt_key]={'status':'skipped','reason':'Job overlap forbidden','admissionId':admission['admissionId']}
                self.diagnostics.append({'jobId':jid,'eventId':event['id'],'phase':'overlap','error':'Overlapping Event rejected'})
            else:
                pending=job.setdefault('pendingRuns',[])
                if len(pending)>=128:
                    admission=self.final.ledger.admit(job,self.root,plan={'jobId':jid,'configRevision':job['configRevision']},cause=run_cause,
                        authority=target.get('authorization',job.get('authorization')),state='rejected',authorization_revision=target.get('authorizationRevision'))
                    receipts[receipt_key]={'status':'rejected','reason':'Job queued Run capacity reached (128)','admissionId':admission['admissionId']}
                    jobs._write_job(jobs._job_path(jid,self.root),job)
                    raise Conflict('Job queued Run capacity reached (128)')
                queued_id='queued_'+uuid.uuid4().hex[:24];run_id=canonical_id('run_')
                waiting={'id':queued_id,'runId':run_id,'configRevision':job['configRevision'],
                         'entryId':None,'createdAt':time.time(),'event':copy.deepcopy(event),'cause':run_cause,
                         'receiptKey':receipt_key,'policy':{key:copy.deepcopy(job['config'][key]) for key in ('recovery','completion','notification')},
                         **({'jobInput':{**job['config']['arguments'],**copy.deepcopy(target['arguments'])}} if 'arguments' in target else {}),
                         **({'authorization':copy.deepcopy(target['authorization']),'authorizationRevision':target.get('authorizationRevision')} if 'authorization' in target else {})}
                plan,_,_=self._freeze_new(job,queued_id,waiting,run_id)
                admission=self.final.ledger.admit(job,self.root,plan=plan,cause=run_cause,
                    authority=target.get('authorization',job.get('authorization')),admission_id='adm_'+queued_id,run_id=run_id,notification=waiting['policy']['notification'],
                    authorization_revision=target.get('authorizationRevision'))
                waiting['admissionId']=admission['admissionId'];pending.append(waiting)
                receipts[receipt_key]={'status':'queued','runId':run_id,'admissionId':admission['admissionId']}
            jobs._write_job(jobs._job_path(jid,self.root),job)

    async def match_event(self,event,targets):
        for target in targets:
            await io(self.queue_event,event,target)
        await self.tick(await io(jobs._scheduled_task_config))

    @legacy_writer(lambda self, *args, **kwargs: self.root.parent, reservation=True)
    async def launch(self,jid,expected=None,entry=None,queued=None,confirmed=False,authority=None,expected_authorization=None,rerun_of=None,operation_id=None,provenance=None):
        if getattr(self,'setup_pending',None) and await io(self.setup_pending,jid):raise Conflict('Job setup incomplete; resume the saved edit')
        if self.closed:raise Conflict('Job runtime closed')
        if len(self.admissions)+sum(not t.done() for t in self.tasks.values())>=self.limit:
            raise Conflict('Shared scheduler dispatch capacity reached')
        eid='jexec_'+uuid.uuid4().hex
        self.admissions[eid]=asyncio.current_task();self.admission_jobs[eid]=jid
        try:
            before=await io(self.get,jid)
            source = authority or (before.get('runAuthorization') if before.get('status')=='restarting' else None) or before.get('authorization', {'type':'user'})
            continuation=before.get('chainRecovery') if before.get('status')=='restarting' or confirmed else None
            action = (await io(self.actions.read))['actions'].get(before['config']['actionId']) if not continuation else None
            if not continuation and (not action or not action.get('enabled')):
                raise Conflict('Action disabled or missing')
            authorization_revision = await self._check_authorization(source, action,expected_authorization)
            view=await io(self.view,before)
            if continuation:
                if not before['enabled'] and not confirmed:raise Conflict('Job disabled')
            elif not view['enabled'] and not (confirmed and view['integrity']['status']=='complete' and not view['integrity']['approvalRequired']):raise Conflict('Job disabled, incomplete or requires user repair')
            trigger=continuation['snapshot']['trigger'] if continuation else before['config']['trigger']
            if trigger['kind']=='event' and not all(getattr(self,'event_available',lambda _:True)(t) for t in trigger['eventTypes']):
                raise Conflict('Event source unavailable; queued intent retained')
            if before.get('status')=='restarting' and not before.get('chainRecovery'):
                from .system_actions import recovery_ready
                state=await io(self.actions.read)
                if not await io(recovery_ready,state['actions'][before['config']['actionId']],before['config']['arguments']):raise Conflict('Recovery blocked by business gates; budget unchanged')
            try:job=await io(self.claim,jid,eid,expected,entry,queued,confirmed,source,authorization_revision,rerun_of,operation_id,provenance)
            except Conflict as exc:
                if queued is None and before['config']['overlap']['mode']=='queue' and 'overlap capacity' in str(exc):
                    waiting=await io(self.enqueue,jid,expected,entry,rerun_of,operation_id,provenance,source,authorization_revision)
                    await io(self.consume_entry,waiting,entry)
                    return await io(self.view,waiting)
                raise
            job=await io(self.consume_entry,job,entry)
            public=await io(self.view,job)
            if self.closed or eid in self.cancelled:raise asyncio.CancelledError()
            task=asyncio.create_task(self.execute(job),name='job-action-'+eid)
            key=jid+':'+eid if job['config']['overlap']['mode']=='parallel' else jid
            self.tasks[key]=task;self.task_jobs[key]=jid
            return public
        except BaseException:
            current=await io(self.get,jid)
            if eid in self.active(current):
                await io(self.finalize,jid,eid,'cancelled' if eid in self.cancelled else 'uncertain')
            raise
        finally:
            self.admissions.pop(eid,None);self.admission_jobs.pop(eid,None)
            if not self.live_for(jid):self.cancelled.discard(eid)

    @legacy_writer(lambda self, *args, **kwargs: self.root.parent, reservation=True)
    async def tick(self,cfg):
        if self.closed:return
        if getattr(self,'time_events',None):await self.time_events.tick(cfg)
        policy=getattr(self,'quota_policy',None)
        if policy is not None:
            try:await policy.tick()
            except Exception as exc:self.diagnostics.append({'phase':'quota-policy','error':type(exc).__name__})
        for jid,task in list(self.tasks.items()):
            if task.done():
                await task;self.tasks.pop(jid,None);self.task_jobs.pop(jid,None)
        # Reconcile cleanup failures without ever rerunning effects.
        try:
            await io(self.recover,False)
        except Exception as exc:
            self.diagnostics.append({'phase':'recovery','error':str(exc)})
            await self.publish_events()
            return  # This extension must not starve legacy scheduler/message Jobs.
        await self.publish_events()
        if not cfg.get('enabled',True):return
        limit=max(1,min(32,int(cfg.get('max_concurrent_dispatch',5))));self.limit=limit
        for job in await io(jobs.list_jobs,self.root):
            if len(self.tasks)>=limit:break
            if not is_action_job(job) or not job.get('enabled') or job.get('paused'):continue
            safe_boundary=job.get('chainRecovery',{}).get('safeBoundary') is True
            continuing=job.get('status')=='restarting' and bool(job.get('chainRecovery'))
            job=await io(self.view,job)
            if not job['enabled'] and not continuing:continue
            policy=job['config'].get('overlap',{})
            maximum=policy.get('parallelLimit',3) if policy.get('mode')=='parallel' else 1
            if (len(self.active(job))>=maximum and policy.get('mode')!='queue') or self.admitting(job['jobId']):continue
            if job.get('pendingRuns') and len(self.active(job))<maximum and job.get('status')!='restarting':
                pending=job['pendingRuns'][0]
                if pending['configRevision']!=job['configRevision'] and not pending.get('runId'):
                    await io(jobs._update,job['jobId'],{'pendingRuns':[]},registry_root=self.root)
                    self.diagnostics.append({'jobId':job['jobId'],'phase':'queue','error':'Queued Run configuration changed; cancelled'})
                    continue
                try:await self.launch(job['jobId'],expected=job['configRevision'],queued=pending['id'],authority=pending.get('authorization'),expected_authorization=pending.get('authorizationRevision'),
                    rerun_of=pending.get('rerunOf'),operation_id=pending.get('operationId'),provenance=pending.get('provenance'))
                except (Conflict,ValueError,AuthorizationRevoked) as exc:self.diagnostics.append({'jobId':job['jobId'],'phase':'queue','error':str(exc)})
                continue
            recovery=job.get('runPolicy',{}).get('recovery',job.get('config',{}).get('recovery',{}))
            if job.get('status')=='restarting':
                count=job.get('recoveryFailures',sum(a.get('runId')==job.get('runId') for a in job.get('attempts',[])))
                if not safe_boundary and (not recovery.get('enabled') or count>=recovery.get('maxAttempts',3)):
                    if count>=recovery.get('maxAttempts',3):
                        await io(self.finalize_recovery_exhausted,job)
                    continue
                if job.get('nextAttemptAt',0)>time.time():continue
                try:await self.launch(job['jobId'],expected=job.get('configRevision'))
                except (Conflict,ValueError,AuthorizationRevoked) as exc:self.diagnostics.append({'jobId':job['jobId'],'error':str(exc)})
                continue
            if job['config']['trigger']['kind']=='immediate' and not job.get('runCount'):
                try:await self.launch(job['jobId'])
                except (Conflict,ValueError,AuthorizationRevoked) as exc:self.diagnostics.append({'jobId':job['jobId'],'error':str(exc)})
                continue
            if job.get('schedulerOwner') == 'event':
                continue  # Time Sources exclusively own this periodic plan.
            for entry in job.get('schedule',[]):
                if entry.get('enabled',True) and jobs._entry_is_due(entry,datetime.now().replace(microsecond=0)):
                    if await io(self.skip_misfire,job,entry,cfg):
                        break
                    try:await self.launch(job['jobId'],entry=entry['id'])
                    except (Conflict,ValueError,AuthorizationRevoked) as exc:self.diagnostics.append({'jobId':job['jobId'],'error':str(exc)})
                    break

    @legacy_writer(lambda self, *args, **kwargs: self.root.parent, reservation=True)
    def finalize_recovery_exhausted(self,job):
        with jobs._lock,jobs._job_lock(job['jobId'],self.root):
            current=self.get(job['jobId'])
            if current.get('status')=='restarting' and current.get('recoveryFailures',sum(a.get('runId')==current.get('runId') for a in current.get('attempts',[])))>=current.get('runPolicy',{}).get('recovery',current.get('config',{}).get('recovery',{})).get('maxAttempts',3):
                current['status']='failed';current['lastStatus']='failed';current['lastError']='Recovery attempts exhausted';current['enabled']=False;current['config']['enabled']=False
                summary=copy.deepcopy(current.get('lastExecution',{}))
                if summary:
                    summary.update(id=summary['id']+':exhausted',status='failed',error='Recovery attempts exhausted')
                    current['lastExecution']=summary;current.setdefault('terminalOutbox',{})[summary['id']]=summary
                    canonical_run=current.get('finalRuntime',{}).get('runs',{}).get(summary.get('runId'))
                    if canonical_run and canonical_run['state']=='retry_wait':
                        self.final.ledger.transition_run(current,self.root,summary['runId'],'running')
                        self.final.ledger.transition_run(current,self.root,summary['runId'],'failed')
                        self.final.ledger.ensure_outbox(current,self.root,summary['runId'],summary['id'],summary)
                    for attempt in current.get('attempts',[]):
                        if attempt['id']==summary.get('attemptId'):attempt.update(status='failed',terminalStatus='failed')
                jobs._write_job(jobs._job_path(job['jobId'],self.root),current)

    @legacy_writer(lambda self, *args, **kwargs: self.root.parent, reservation=True)
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

    @legacy_writer(lambda self, *args, **kwargs: self.root.parent, reservation=True)
    def recover(self,initial=True):
        # Legacy in-flight effects remain uncertain. Explicit chains additionally
        # resume only durable boundaries with no unconfirmed running Step.
        state=self.actions.read()
        for intent in state.get('jobInstantiations',{}).values():
            if intent.get('jobRoot')==str(self.root) and intent['phase']=='reserved':self.publish_intent(intent)
        for job in jobs.list_jobs(self.root):
            if not is_action_job(job):continue
            if self.live_for(job['jobId']) or self.admitting(job['jobId']):continue
            for eid,execution in self.active(job).items():
                snap=state.get('jobExecutions',{}).get(eid,{})
                audit={k:snap[k] for k in ('actionId','actionRevision','registryRevision','digest','argumentsDigest','contextDigest') if k in snap}
                audit.update(self.selection_audit(snap.get('checkpoint',{})))
                if execution.get('chainSnapshot'):
                    steps=copy.deepcopy(execution['stepExecutions'])
                    safe=chains.interrupted(steps)
                    self.chain_save(job['jobId'],eid,steps,'boundary' if safe else 'interrupted')
                    audit.update(stepExecutions=steps,safeBoundary=safe)
                    if safe:
                        self.action_audit(job['jobId'],eid,{},True)
                    self.finalize(job['jobId'],eid,'restarting' if safe else 'uncertain',audit)
                else:
                    self.finalize(job['jobId'],eid,'uncertain',audit)
        state=self.actions.read()
        for eid,snap in state.get('jobExecutions',{}).items():
            if snap.get('jobRoot')==str(self.root) and not self.live_for(snap.get('jobId')) and not self.admitting(snap.get('jobId')):
                audit={k:snap[k] for k in ('actionId','digest','argumentsDigest','contextDigest') if k in snap}
                jobs._append_run_record_once({'run_id':eid,'task_id':snap.get('taskId',snap['jobId']),
                    'dispatch_key':eid,'status':'uncertain','result':audit},registry_root=self.root)
                self.templates.clear_snapshot(eid)
        if initial:
            actual={j['jobId']:chains.action_ids(j['config']) for j in jobs.list_jobs(self.root) if is_action_job(j)}
            state=self.actions.read()
            existing=state.get('jobReferences',{})
            needs=any(existing.get(jid,{}).get('actionIds',[existing.get(jid,{}).get('actionId')])!=aids for jid,aids in actual.items())
            needs=needs or any(r.get('jobRoot')==str(self.root) and jid not in actual for jid,r in existing.items())
            if needs:
                def reconcile(state):
                    refs=state.setdefault('jobReferences',{})
                    for jid,r in list(refs.items()):
                        if r.get('jobRoot')==str(self.root) and jid not in actual:refs.pop(jid)
                    for jid,aids in actual.items():
                        refs[jid]={'actionId':aids[0],'actionIds':aids,'position':'/config/actionId','jobRoot':str(self.root)}
                self.templates.transaction(reconcile)

    @legacy_writer(lambda self, *args, **kwargs: self.root.parent, reservation=True)
    def delete(self,jid,expected):
        with jobs._lock,jobs._job_lock(jid,self.root):
            job=self.get(jid);revision(job['configRevision'],expected)
            if self.active(job) or self.admitting(jid):raise Conflict('Job running or cleaning')
            legacy_pending=bool(job.get('terminalOutbox')) or (job.get('lastExecution') and job.get('terminalEventEmittedFor')!=job['lastExecution'].get('id'))
            if legacy_pending or any(item.get('status')=='pending' for item in job.get('finalRuntime',{}).get('outbox',{}).values()):
                raise Conflict('Job has unacknowledged notification outbox')
            if getattr(self,'setup_pending',None) and self.setup_pending(jid):raise Conflict('Close the pending setup before deleting this Job')
            if getattr(self,'managed_cleanup',None):self.managed_cleanup(jid)
            jobs._job_path(jid,self.root).unlink()
        def detach(state):state.setdefault('jobReferences',{}).pop(jid,None)
        self.templates.transaction(detach)
        from . import pins
        pins.set_pin(self.root,jid,False)
        return {'deleted':True,'jobId':jid}

    def set_enabled(self,jid,enabled,expected,authority):
        old=self.get(jid)
        return self.update(jid,{**old['config'],'enabled':enabled},expected,True,authority)

    @legacy_writer(lambda self, *args, **kwargs: self.root.parent, reservation=True)
    def set_paused(self, jid, paused, expected, authority=None):
        with jobs._lock, jobs._job_lock(jid, self.root):
            job=self.get(jid); revision(job['configRevision'], expected)
            job['paused']=paused; job['configRevision']+=1
            if not paused and authority is not None:
                job['authorization']=copy.deepcopy(authority)
                if not self.active(job):job['runAuthorization']=copy.deepcopy(authority)
            jobs._write_job(jobs._job_path(jid,self.root),job)
        return self.view(job)

    @legacy_writer(lambda self, *args, **kwargs: self.root.parent, reservation=True)
    async def run_again(self,jid,expected,rerun_of,request_key,authority=None):
        """D3 Run again: same Job, new Run and new effect identities."""
        source=await io(self.get,jid);revision(source['configRevision'],expected)
        await io(self.final.run,jid,rerun_of)
        operation,replay=await io(self.final.begin_operation,jid,operation='create',request_key=request_key,authority=authority,
            expected_revision=expected,payload={'jobId':jid,'rerunOf':rerun_of,'expectedRevision':expected})
        if replay and operation['state']=='completed':
            return {'job':await io(self.view,await io(self.get,jid)),**copy.deepcopy(operation.get('result',{})),'operationId':operation['operationId']}
        current=await io(self.final.store.get,jid)
        existing=next((rid for rid,plan in current.get('finalRuntime',{}).get('plans',{}).items()
                       if plan.get('operationId')==operation['operationId']),None)
        if existing is None:
            await self.launch(jid,expected,authority=authority,rerun_of=rerun_of,operation_id=operation['operationId'])
            current=await io(self.final.store.get,jid)
            existing=next((rid for rid,plan in current.get('finalRuntime',{}).get('plans',{}).items()
                           if plan.get('operationId')==operation['operationId']),None)
        if existing is None:raise OSError('Canonical Run admission missing after operation acceptance')
        run=current['finalRuntime']['runs'][existing]
        return {'job':await io(self.view,current),'runId':existing,'admissionId':run['admissionId'],'operationId':operation['operationId']}

    @legacy_writer(lambda self, *args, **kwargs: self.root.parent, reservation=True)
    async def clone_and_run(self,jid,expected,request_key,authority=None,overrides=None):
        """D3 Clone and run: one explicit new Job, then one canonical Run."""
        source=await io(self.get,jid);revision(source['configRevision'],expected)
        changes=copy.deepcopy(overrides or {})
        payload={'sourceJobRef':{'id':jid,'revision':expected,'digest':digest(source['config'])},'overrides':changes}
        operation,replay=await io(self.final.begin_operation,jid,operation='clone-and-run',request_key=request_key,authority=authority,
            expected_revision=expected,payload=payload)
        if replay and operation['state']=='completed':
            return copy.deepcopy(operation['result'])
        config=copy.deepcopy(source['config']);config.update(changes)
        # Cloning never activates the source's periodic Event Creators. The one
        # requested manual Run is admitted separately below.
        config.update(enabled=True,schedule=[],trigger={'kind':'manual'})
        created=await io(self.create,config,True,authority,creation_key='clone:'+operation['operationId'])
        clone_id=created['jobId']
        def provenance(job):
            job['clonedFrom']={'jobId':jid,'configRevision':expected,'digest':digest(source['config'])}
        await io(self.final.store.mutate,clone_id,provenance)
        clone=await io(self.final.store.get,clone_id)
        run_id=next((rid for rid,plan in clone.get('finalRuntime',{}).get('plans',{}).items()
                     if plan.get('cloneOperationId')==operation['operationId']),None)
        if run_id is None:
            await self.launch(clone_id,clone['configRevision'],authority=authority,
                provenance={'cloneOperationId':operation['operationId'],'clonedFrom':copy.deepcopy(clone['clonedFrom'])})
            clone=await io(self.final.store.get,clone_id)
            run_id=next((rid for rid,plan in clone.get('finalRuntime',{}).get('plans',{}).items()
                         if plan.get('cloneOperationId')==operation['operationId']),None)
        if run_id is None:raise OSError('Canonical clone Run admission missing')
        admission_id=clone['finalRuntime']['runs'][run_id]['admissionId']
        result={'jobId':clone_id,'runId':run_id,'admissionId':admission_id,'operationId':operation['operationId']}
        await io(self.final.store.mutate,jid,lambda job:self.final.ledger.complete_operation(job,self.root,operation['operationId'],result,run_id=run_id,admission_id=admission_id))
        return result

    @legacy_writer(lambda self, *args, **kwargs: self.root.parent, reservation=True)
    async def cancel(self,jid,expected):
        def disable():
            with jobs._lock,jobs._job_lock(jid,self.root):
                job=self.get(jid);revision(job['configRevision'],expected)
                job['enabled']=False;job['config']['enabled']=False
                for waiting in job.get('pendingRuns',[]):
                    run_id=waiting.get('runId')
                    if run_id and job.get('finalRuntime',{}).get('runs',{}).get(run_id,{}).get('state')=='queued':
                        self.final.ledger.request_cancel(job,self.root,run_id,request_key='legacy-cancel:'+run_id+':'+uuid.uuid4().hex,authority=job.get('authorization'))
                        self.final.ledger.transition_run(job,self.root,run_id,'cancelled')
                        operation_id=waiting.get('operationId')
                        if operation_id in job.get('finalRuntime',{}).get('operations',{}):
                            self.final.ledger.complete_operation(job,self.root,operation_id,{'jobId':jid,'runId':run_id,'state':'cancelled'},run_id=run_id,admission_id=waiting.get('admissionId'))
                for execution in self.active(job).values():
                    run_id=execution.get('runId');run=job.get('finalRuntime',{}).get('runs',{}).get(run_id)
                    if run and run['state'] in {'queued','running','retry_wait','paused_at_boundary'}:
                        self.final.ledger.request_cancel(job,self.root,run_id,request_key='legacy-cancel:'+run_id+':'+uuid.uuid4().hex,authority=job.get('authorization'))
                job['configRevision']+=1
                job['pendingRuns']=[]
                if not job.get('execution'):job['status']='cancelled'
                jobs._write_job(jobs._job_path(jid,self.root),job)
                return job
        job=await io(disable)
        for eid in self.active(job):self.cancelled.add(eid)
        keys=[key for key in self.tasks if self.task_jobs.get(key,key)==jid]
        pending=[self.tasks[key] for key in keys]+[task for key,task in self.admissions.items() if self.admission_jobs.get(key)==jid]
        pending=[task for task in pending if task is not asyncio.current_task()]
        for task in pending:task.cancel()
        await asyncio.gather(*pending,return_exceptions=True)
        for key in keys:self.tasks.pop(key,None);self.task_jobs.pop(key,None)
        for eid in self.active(await io(self.get,jid)):
            await io(self.finalize,jid,eid,'cancelled')
        return self.view(await io(self.get,jid))

    @legacy_writer(lambda self, *args, **kwargs: self.root.parent, reservation=True)
    def reserve_recovery(self,jid,expected,status,request_key=None,authority=None,run_id=None):
        state=self.actions.read()
        with jobs._lock,jobs._job_lock(jid,self.root):
            job=self.get(jid);revision(job['configRevision'],expected)
            if self.active(job) or self.admitting(jid):raise Conflict('Attempt still running or cleaning')
            last=job.get('lastExecution',{})
            if run_id is not None:
                last=copy.deepcopy(job.get('runSummaries',{}).get(run_id) or (last if last.get('runId')==run_id else {}))
            elif last.get('status') not in status:
                # lastExecution is a display selection, not the retry index.
                # Preserve the old unambiguous confirmation convenience while
                # requiring an original Run ID when several candidates exist.
                eligible=[summary for summary in job.get('runSummaries',{}).values() if summary.get('status') in status]
                if len(eligible)>1:raise Conflict('Select an original Run ID to confirm recovery')
                if eligible:last=copy.deepcopy(eligible[0])
            if last.get('stepExecutions') and last.get('status') in ('unknown','uncertain'):
                raise Conflict('Uncertain chain step requires reconciliation; replay refused')
            if last.get('status') not in status or not last.get('runId'):raise Conflict('No eligible terminal Run to confirm')
            # A process receipt must have a known terminal child. Never spawn
            # a second command while the original can still be alive.
            action=state['actions'].get(job['config']['actionId'])
            if not action:raise Conflict('Action reference missing')
            if (last.get('executionKind') in (None,'command')) and last['status'] in ('unknown','uncertain'):
                raise Conflict('Command process identity unavailable; reconcile it before recovery')
            if last.get('processJobId'):
                child=jobs.get(last['processJobId'],self.root)
                if not child or child.get('status') not in ('completed','failed','cancelled'):
                    raise Conflict('Original process has no trusted terminal receipt')
            checkpoint={key:copy.deepcopy(last[key]) for key in ('targetIds','targetResults','selection','triggerTargetIds','triggerSelection') if key in last}
            canonical_run=job.get('finalRuntime',{}).get('runs',{}).get(last['runId'])
            if canonical_run and canonical_run['state']=='uncertain':raise Conflict('Uncertain Run requires reconciliation; replay refused')
            selected=job.get('runRecoveries',{}).get(last['runId'])
            if selected:
                job['chainRecovery']=copy.deepcopy(selected)
                job['status']='restarting'
            operation=None
            if canonical_run:
                operation,_=self.final.ledger.begin_operation(job,self.root,operation='retry',request_key=request_key or 'legacy-retry:'+uuid.uuid4().hex,
                    authority=authority,expected_revision=expected,payload={'runId':last['runId'],'stepId':last.get('failedStepId')})
            job['configRevision']+=1
            job['recoveryConfirmation']={'status':'reserved','runId':last['runId'],'configRevision':job['configRevision'],'checkpoint':checkpoint,
                **({'operationId':operation['operationId']} if operation else {})}
            jobs._write_job(jobs._job_path(jid,self.root),job)
            return job

    @legacy_writer(lambda self, *args, **kwargs: self.root.parent, reservation=True)
    async def confirm_recovery(self,jid,expected,failed=False,authority=None,run_id=None):
        current=await io(self.get,jid)
        source=authority or current.get('runAuthorization') or current.get('authorization',{'type':'user'})
        action=(await io(self.actions.read))['actions'].get(current['config']['actionId']) if not current.get('chainRecovery') else None
        if not current.get('chainRecovery') and (not action or not action.get('enabled')):raise Conflict('Action disabled or missing')
        await self._check_authorization(source,action)
        job=await io(self.reserve_recovery,jid,expected,('failed',) if failed else ('uncertain','unknown'),None,source,run_id)
        return await self.launch(jid,job['configRevision'],confirmed=True,authority=source,operation_id=job.get('recoveryConfirmation',{}).get('operationId'))

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
            self.task_jobs.clear()
            await io(self.recover)
        finally:
            await io(self.ownership.release)
            self.owned=False
