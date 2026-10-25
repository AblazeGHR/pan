"""Trigger-to-Job admission adapter. No Action or chain execution host."""
import copy

from packages.jobs.final_runtime.coordinator import TriggerAdmissionPort
from packages.jobs.final_runtime.ledger import admission_for_cause, cause


class JobAdmission(TriggerAdmissionPort):
    def __init__(self, engine):
        self.engine = engine

    async def target(self, trigger, run):
        engine = self.engine
        runtime = engine.job_runtime
        if trigger.get('targetJobId'):
            job_id = trigger['targetJobId']
        else:
            # Only simple declarative Actions/chains can migrate. Existing
            # active/unknown legacy work is retained and blocks automatic handoff.
            state = await engine.io(engine.ledger.read)
            legacy = [r for r in state['runs'].values() if r['triggerId'] == trigger['id']
                      and r.get('recordType') != 'triggerAdmission'
                      and r['status'] in {'queued', 'running', 'retry_wait', 'unknown', 'uncertain'}]
            if legacy:
                raise ValueError('Legacy Trigger execution requires explicit handoff/reconciliation')
            steps = copy.deepcopy(trigger.get('steps'))
            if steps is None:
                steps = [{'stepId':'main', 'actionId':trigger['actionId'], 'arguments':{
                    key:{'from':'input','path':key} for key in run['arguments']}}]
            config = dict(name=trigger['name'], description='Managed by Trigger ' + trigger['id'],
                enabled=True, actionId=trigger['actionId'], arguments={}, steps=steps,
                trigger={'kind':'manual'}, schedule=[],
                overlap={'mode':'parallel' if trigger.get('executionMode') == 'parallel' else 'queue',
                         'parallelLimit':trigger.get('maxConcurrency', 1)})
            # Runtime's durable creation intent reuses this identity even when a
            # crash occurs between Job creation and Trigger link publication.
            job = await engine.io(runtime.create, config, True, run['authorization'],
                                  creation_key='trigger-managed:' + trigger['id'])
            job_id = job['jobId']
            provenance = {'triggerId':trigger['id'], 'triggerRevision':trigger['revision'],
                          'trigger':copy.deepcopy(trigger), 'source':'direct-trigger'}
            await engine.io(runtime.final.store.mutate, job_id,
                            lambda stored: stored.setdefault('triggerProvenance', copy.deepcopy(provenance)))
            await engine.io(engine.ledger.bind_job, trigger['id'], trigger['revision'], job_id, provenance)
        job = await engine.io(runtime.view, await engine.io(runtime.get, job_id))
        return dict(jobId=job_id, configRevision=job['configRevision'], triggerId=trigger['id'],
            triggerRevision=trigger['revision'], arguments=copy.deepcopy(run['arguments']),
            authorization=copy.deepcopy(run['authorization']), authorizationRevision=run.get('authorizationRevision'),
            provenance={'triggerRunId':run['id'], 'triggerRevision':trigger['revision'], 'state':copy.deepcopy(run['state'])})

    async def lookup(self, run):
        runtime = self.engine.job_runtime
        if not runtime or not run.get('targetJobId'):
            return None
        try:
            job = await self.engine.io(runtime.get, run['targetJobId'])
        except KeyError:
            return None
        return copy.deepcopy(admission_for_cause(job.get('finalRuntime', {}),
            cause(run['event'], {'triggerId':run['triggerId']})))

    async def admit(self, event, target):
        engine, runtime = self.engine, self.engine.job_runtime
        def commit():
            from packages.core import background_jobs as registry
            # Freeze Trigger and Job lifecycle during the admission transaction.
            # queue_event reenters the same Job lock and commits canonical intent.
            with engine.ledger.lock, registry._lock, registry._job_lock(target['jobId'], runtime.root):
                trigger = engine.ledger.read()['triggers'].get(target['triggerId'])
                if (not trigger or not trigger['enabled'] or trigger.get('paused')
                        or trigger['revision'] != target['triggerRevision'] or not engine.available(trigger)):
                    return {'jobId':target['jobId'], 'status':'skipped', 'reason':'Trigger no longer eligible'}
                job = runtime.view(runtime.get(target['jobId']))
                if not job['enabled'] or job.get('paused'):
                    return {'jobId':target['jobId'], 'status':'skipped', 'reason':'Job disabled or paused'}
                runtime.queue_event(event, target)
                job = runtime.get(target['jobId'])
                state = job.get('finalRuntime', {})
                receipt = copy.deepcopy(admission_for_cause(state, cause(event, target)))
                if receipt is None:
                    raise RuntimeError('Job admission outcome unavailable; query original identity')
                admission_id = receipt['admissionId']
                run_id = next((rid for rid, run in state['runs'].items() if run['admissionId'] == admission_id), None)
                if run_id:
                    state['plans'][run_id]['triggerProvenance'] = copy.deepcopy(target['provenance'])
                    registry._write_job(registry._job_path(target['jobId'], runtime.root), job)
                    receipt['runId'] = run_id
                return receipt
        return await engine.io(commit)
