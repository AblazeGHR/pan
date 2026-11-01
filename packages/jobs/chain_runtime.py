"""Existing Action Job host adapter for the shared serial chain executor."""
import asyncio
import copy

from packages.plugins.lifecycle import io
from . import chains


async def execute_job_chain(runtime, job):
    from packages.actions.executor import execute_dynamic
    from packages.automation.engine import PersistenceError
    from .action_runtime import AuthorizationRevoked, SnapshotPersistenceError
    from .retired import reject_execution as execute_system
    from .system_actions import recovery_ready

    execution = job['execution']
    eid, jid = execution['id'], job['jobId']
    snapshot = execution['chainSnapshot']
    steps = copy.deepcopy(execution['stepExecutions'])
    outcome, audit = 'uncertain', {}

    async def save(records, phase):
        try:
            await io(runtime.chain_save, jid, eid, records, phase)
        except Exception as exc:
            raise chains.ChainPersistenceError(str(exc)) from exc

    async def guard(definition):
        if eid in runtime.cancelled:
            raise asyncio.CancelledError()
        state = await io(runtime.actions.read)
        current = state['actions'].get(definition['actionId'])
        if (not current or not current.get('enabled')
                or state.get('actionGenerations', {}).get(definition['actionId'], 0) != definition['generation']):
            raise AuthorizationRevoked('Action disabled, deleted or replaced before effect admission')
        await runtime._check_authorization(execution.get('authorization', {'type': 'user'}),
                                           definition['definition'], execution.get('authorizationRevision'))

    async def progress_factory(definition, step):
        async def progress(**fields):
            step['checkpoint'].update(copy.deepcopy(fields))
            # Selection/receipts are isolated to this Step, including its retries.
            await save(steps, 'step_running')
        progress.checkpoint = {'id': execution['runId'] + ':' + step['stepId'], **copy.deepcopy(step['checkpoint'])}
        progress.before_effect = lambda: guard(definition)
        progress.execute_system = execute_system
        progress.instantiate_template = lambda action, key: runtime.instantiate_action(
            action, key, definition.get('fixedTemplate'), execution['authorization'], execution.get('authorizationRevision'))
        return progress

    async def invoke(definition, args, event, progress):
        await guard(definition)
        if execution['attemptCount'] > 1 and execution['policy']['recovery']['enabled']:
            if not await io(recovery_ready, definition['definition'], args):
                raise AuthorizationRevoked('Recovery paused by current business gates')
        context = copy.deepcopy(execution['context'])
        current = next(s for s in steps if s['stepId'] == definition['stepId'])
        context.update(id=execution['runId'] + ':' + definition['stepId'], admittedExecution=eid,
                       dispatchKey=execution['runId'] + ':' + definition['stepId'],
                       attemptId=current['attempts'][-1]['id'],
                       effectKey=copy.deepcopy(current['attempts'][-1].get('effectKey')))
        # The old shell adapter checks this persisted Step identity and arguments
        # under its parent Job lock before creating an owned child.
        current['arguments'] = copy.deepcopy(args)
        await save(steps, 'step_running')
        query = snapshot.get('trigger', {}).get('query')
        if query is not None and snapshot.get('trigger', {}).get('kind') == 'event':
            selection = current['checkpoint'].get('triggerSelection')
            if selection is None:
                from packages.actions.session_query import resolve
                selected = await resolve(query)
                selection = {'ids': selected['ids'], 'candidateCount': selected['candidateCount']}
                await progress(triggerSelection=selection, triggerTargetIds=selected['ids'])
            context['jobSelection'] = copy.deepcopy(selection)
        return await execute_dynamic(definition['definition'], args, context, progress, runtime.pause)

    try:
        persisted = await io(runtime.templates.snapshot, job, execution)
        audit = {k: persisted[k] for k in ('actionId', 'actionRevision', 'registryRevision', 'digest', 'argumentsDigest', 'contextDigest')}
        audit['executionKind'] = 'action_chain'
        await io(runtime.action_audit, jid, eid, audit, execution['policy']['recovery']['enabled'] and
                 all(s['definition'].get('recoverable') is True for s in snapshot['steps']))
        outcome, result = await chains.execute(snapshot, steps, invoke=invoke, save=save, guard=guard,
                                               progress_factory=progress_factory,
                                               recovery=execution['policy']['recovery']['enabled'])
        if result is not None:
            audit['result'] = result
    except asyncio.CancelledError:
        outcome = 'cancelled' if eid in runtime.cancelled else 'uncertain'
    except AuthorizationRevoked as exc:
        outcome = 'cancelled'; audit['error'] = str(exc)
    except Exception as exc:
        outcome = 'uncertain' if isinstance(exc, (chains.ChainPersistenceError, SnapshotPersistenceError, PersistenceError, TimeoutError)) else 'failed'
        audit['error'] = str(exc)[:1000]
    finally:
        try:
            # An exception may follow the effect, or a cancellation may arrive
            # after a shielded write. Read the durable boundary before settling.
            current = runtime.active(await io(runtime.get, jid)).get(eid)
            if current:
                steps = copy.deepcopy(current['stepExecutions'])
                for step in steps:
                    if step['status'] == 'running':
                        chains.finish_step(step, outcome, error=audit.get('error', 'Execution interrupted'))
                await save(steps, 'terminal')
            await io(runtime.finalize, jid, eid, outcome, audit)
            await runtime.publish_events()
        except Exception as exc:
            runtime.diagnostics.append({'jobId': jid, 'phase': 'cleanup', 'error': str(exc)})
        runtime.cancelled.discard(eid)
