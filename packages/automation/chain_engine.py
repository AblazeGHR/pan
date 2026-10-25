"""Trigger host adapter; legacy no-steps Runs retain their original executor."""
import asyncio
import copy

from packages.jobs import chains
from packages.jobs.process_wait import CleanupUnconfirmed


async def run_chain(engine, run):
    from .engine import PersistenceError, DependencyUnavailable
    from .policy import retry_policy
    rid = run['id']
    steps = copy.deepcopy(run['stepExecutions'])
    snapshot = run['chainSnapshot']
    eligible = False
    effect_started = False
    tasks = getattr(engine, 'chain_tasks', None)
    if tasks is None:
        tasks = engine.chain_tasks = {}
    tasks[rid] = asyncio.current_task()

    async def save(records, phase):
        try:
            await engine.io(engine.ledger.save_chain, rid, run['activeAttemptId'], records, phase, eligible)
        except Exception as exc:
            raise chains.ChainPersistenceError(str(exc)) from exc

    async def guard(definition):
        current_run = (await engine.io(engine.ledger.read))['runs'][rid]
        if current_run.get('cancellationRequested'):
            raise asyncio.CancelledError()
        state = await engine.io(engine.actions.read)
        current = state['actions'].get(definition['actionId'])
        if (not current or not current.get('enabled')
                or state.get('actionGenerations', {}).get(definition['actionId'], 0) != definition['generation']):
            raise PermissionError('Action disabled, deleted or replaced before effect admission')
        await engine._check_authorization(run.get('authorization', {'type': 'user'}),
                                           definition['definition'], run.get('authorizationRevision'))
        trigger = (await engine.io(engine.ledger.read))['triggers'].get(run['triggerId'])
        if engine.closed or not trigger or not engine.available(trigger):
            raise DependencyUnavailable('Event dependency unavailable before effect')

    async def progress_factory(definition, step):
        async def progress(**fields):
            step['checkpoint'].update(copy.deepcopy(fields))
            await save(steps, 'step_running')
        progress.checkpoint = {'id': rid + ':' + step['stepId'], **copy.deepcopy(step['checkpoint'])}
        progress.before_effect = lambda: guard(definition)
        if getattr(engine, 'job_runtime', None):
            progress.instantiate_template = lambda action, key: engine.job_runtime.instantiate_action(
                action, key, definition.get('fixedTemplate'), run.get('authorization', {'type': 'user'}),
                run.get('authorizationRevision'))
        return progress

    async def invoke(definition, args, event, progress):
        nonlocal effect_started, eligible
        # Eligibility is frozen from the current step definition; completed
        # steps never participate in its retry and are never replayed.
        eligible = definition['definition'].get('idempotent') is True and retry_policy(run.get('retryPolicy'))['enabled']
        await guard(definition)
        await save(steps, 'step_running')
        effect_started = True
        return await engine.execute(definition['definition'], args, event, progress)

    try:
        outcome, result = await chains.execute(snapshot, steps, invoke=invoke, save=save, guard=guard,
                                               progress_factory=progress_factory)
        await engine.io(engine.ledger.finish, rid, 'succeeded' if outcome == 'completed' else outcome,
                        result=result, retryable=eligible)
    except asyncio.CancelledError:
        current = (await engine.io(engine.ledger.read))['runs'][rid]
        outcome = 'cancelled' if current.get('cancellationRequested') else 'uncertain'
        steps = copy.deepcopy(current['stepExecutions'])
        for step in steps:
            if step['status'] == 'running':
                chains.finish_step(step, outcome, error='Execution interrupted; no automatic replay')
        await save(steps, 'terminal')
        await engine.io(engine.ledger.finish, rid, outcome, retryable=False)
    except Exception as exc:
        current = (await engine.io(engine.ledger.read))['runs'][rid]
        steps = copy.deepcopy(current['stepExecutions'])
        uncertain = isinstance(exc, (chains.ChainPersistenceError, PersistenceError, CleanupUnconfirmed, TimeoutError)) or (
            isinstance(exc, DependencyUnavailable) and effect_started)
        outcome = 'uncertain' if uncertain else 'cancelled' if isinstance(exc, PermissionError) else 'failed'
        for step in steps:
            if step['status'] == 'running':
                chains.finish_step(step, outcome, error=str(exc)[:1000])
        await save(steps, 'terminal')
        if isinstance(exc, DependencyUnavailable) and not effect_started:
            await engine.io(engine.ledger.defer_unavailable, rid,
                            (await engine.io(engine.ledger.read))['runs'][rid]['revision'])
        else:
            await engine.io(engine.ledger.finish, rid, outcome, retryable=eligible and outcome == 'failed', error=str(exc)[:1000])
    finally:
        tasks.pop(rid, None)
