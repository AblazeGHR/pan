"""Event matching and State evaluation; all effects belong to Jobs."""
import asyncio
import copy
import operator
import uuid
from collections import deque

from packages.core.system_fields import system_binding_name, system_value
from .events import now
from .store import Conflict
from .chain_engine import JobAdmission


def field(value, path):
    for key in path.split('.'):
        value = value[key]
    return value


async def evaluate(condition, reader, cache):
    if condition is None:
        return True
    if set(condition) == {'and'}:
        return all([await evaluate(c, reader, cache) for c in condition['and']])
    if set(condition) == {'or'}:
        return any([await evaluate(c, reader, cache) for c in condition['or']])
    if set(condition) == {'not'}:
        return not await evaluate(condition['not'], reader, cache)
    if set(condition) != {'field', 'op', 'value'}:
        raise ValueError('Invalid State condition')
    key = condition['field']
    if key not in cache:
        cache[key] = await reader(key)
    ops = dict(eq=operator.eq, ne=operator.ne, gt=operator.gt, gte=operator.ge, lt=operator.lt, lte=operator.le)
    return ops[condition['op']](cache[key], condition['value'])


def matches_event(filters,event):
    try:return all(field(event,path)==value for path,value in filters.items())
    except (KeyError,TypeError):return False


def arguments(mapping, event):
    values = {}
    for key, binding in mapping.items():
        if binding.get('from') == 'literal' and set(binding) == {'from', 'value'}:
            values[key] = copy.deepcopy(binding['value'])
        elif binding.get('from') == 'event' and set(binding) == {'from', 'path'}:
            values[key] = copy.deepcopy(field(event, binding['path']))
        elif binding.get('from') == 'system' and set(binding) == {'from', 'path'}:
            name = system_binding_name(binding['path'])
            # No fallback to payload: a missing trusted field fails the match
            # visibly instead of silently targeting an unverified identity.
            values[key] = copy.deepcopy(system_value(event, name))
        else:
            raise ValueError('Invalid argument binding')
    return values


class PersistenceError(RuntimeError):
    pass


class DependencyUnavailable(RuntimeError):
    pass


class Engine:
    def __init__(self, ledger, bus, actions, execute=None, state_reader=None, max_pending=1024, max_triggers=64):
        self.ledger, self.bus, self.actions = ledger, bus, actions
        # The former execute argument is accepted for transport construction
        # compatibility only. Trigger never retains or invokes an executor.
        self.state_reader = state_reader
        self.max_pending, self.max_triggers = max_pending, max_triggers
        self.closed = False
        self.matching = asyncio.Lock()
        self.dispatching = asyncio.Lock()
        self.diagnostics = deque(maxlen=100)
        self.authorization = None
        self.job_runtime = None
        self.admission_port = JobAdmission(self)
        self.tasks = {}  # No Trigger effect tasks; old readers may inspect this.

    async def io(self, fn, *args, **kw):
        task = asyncio.create_task(asyncio.to_thread(fn, *args, **kw))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    def available(self, trigger):
        return all(self.bus.available(t) for t in trigger['eventTypes'])

    async def _check_authorization(self, source):
        if source.get('type') != 'agent':
            return None
        if self.authorization is None:
            raise PermissionError('Delegated authorization unavailable')
        allowed, revision = await self.io(self.authorization, 'job', 'execute')
        if not allowed:
            raise PermissionError('Delegated authorization revoked before admission')
        return revision

    async def start(self):
        if not self.closed:
            await self.kick()

    async def subscriptions(self):
        # Import existing embedded Event subscriptions once as visible Triggers.
        # Runtime.event_targets/match_event are no longer delivery hosts.
        if self.job_runtime:
            for job in await self.io(self.job_runtime.list):
                if job['config']['trigger']['kind'] == 'event':
                    await self.io(self.ledger.import_subscription, job)
        return (await self.io(self.ledger.read))['triggers']

    async def match(self, event, targets=None, strict=False):
        async with self.matching:
            state = await self.io(self.ledger.read)
            seen = {(r['event']['id'], r['triggerId']) for r in state['runs'].values()}
            pending = sum(r.get('recordType') == 'triggerAdmission' and r['status'] == 'queued' for r in state['runs'].values())
            persisted, errors = [], []
            for trigger in targets if targets is not None else state['triggers'].values():
                if (event['id'], trigger['id']) in seen or event['type'] not in trigger['eventTypes']:
                    continue
                run = dict(id='run_' + uuid.uuid4().hex, recordType='triggerAdmission',
                    triggerId=trigger['id'], triggerRevision=trigger['revision'],
                    event=copy.deepcopy(event), createdAt=now(), status='queued',
                    admitted=False, arguments={}, state={}, triggerSnapshot=copy.deepcopy(trigger))
                current = state['triggers'].get(trigger['id'])
                try:
                    run['authorization'] = copy.deepcopy(trigger.get('authorization', {'type':'user'}))
                    if not current or current['revision'] != trigger['revision']:
                        run.update(status='cancelled', error='Trigger changed before delivery')
                    elif not current['enabled'] or current.get('paused'):
                        run.update(status='skipped', error='Trigger disabled or paused')
                    elif not self.available(trigger):
                        raise DependencyUnavailable('Event dependency unavailable')
                    elif not matches_event(trigger.get('eventFilter', {}), event):
                        run.update(status='skipped', error='Event filter did not match')
                    elif pending >= self.max_pending:
                        raise ValueError('Trigger backlog full')
                    elif not await evaluate(trigger.get('condition'), self.state_reader, run['state']):
                        run.update(status='skipped')
                    else:
                        run['authorizationRevision'] = await self._check_authorization(run['authorization'])
                        run['arguments'] = arguments(trigger.get('arguments', {}), event)
                except DependencyUnavailable as exc:
                    errors.append(str(exc))
                    continue  # Known pre-admission dependency gap; safe to retry.
                except Exception as exc:
                    run.update(status='failed', error=str(exc))
                if run['status'] != 'queued':
                    run['finishedAt'] = now()
                try:
                    saved = await self.io(self.ledger.append, run)
                    if saved:
                        persisted.append(saved['id'])
                        pending += saved['status'] == 'queued'
                except Exception as exc:
                    errors.append(str(exc))
                    self.diagnostics.append(dict(phase='persist', eventId=event['id'], error=str(exc), at=now()))
            try:
                if persisted or strict:
                    await self.io(self.ledger.release_event, event['id'])
            except Exception as exc:
                errors.append(str(exc))
            await self.kick()
            if strict and errors:
                raise PersistenceError('; '.join(errors))
            return persisted

    async def kick(self):
        if self.closed:
            return
        async with self.dispatching:
            state = await self.io(self.ledger.read)
            for run in state['runs'].values():
                if self.closed:
                    return
                if run.get('recordType') != 'triggerAdmission':
                    continue  # Historical TriggerRuns are evidence, never work.
                if run['status'] == 'admitting':
                    # Cross-store interrupted intent: query by original identity.
                    # Missing/unknown outcome is never sent again.
                    result = await self.admission_port.lookup(run)
                    await self.io(self.ledger.update, run['id'], status='admitted' if result else 'uncertain',
                                  result=result, finishedAt=now())
                    continue
                if run['status'] != 'queued' or not run.get('admitted'):
                    continue
                trigger = (await self.io(self.ledger.read))['triggers'].get(run['triggerId'])
                if not trigger or not trigger['enabled'] or trigger.get('paused') or trigger['revision'] != run['triggerRevision']:
                    await self.io(self.ledger.update, run['id'], status='cancelled', finishedAt=now())
                    continue
                if not self.available(trigger) or not self.job_runtime:
                    continue
                try:
                    revision = await self._check_authorization(run['authorization'])
                    if revision != run.get('authorizationRevision'):
                        raise PermissionError('Authorization changed before admission')
                    target = await self.admission_port.target(trigger, run)
                    # Persist resolved identity before crossing the Job boundary.
                    await self.io(self.ledger.update, run['id'], targetJobId=target['jobId'], status='admitting')
                    result = await self.admission_port.admit(run['event'], target)
                    await self.io(self.ledger.update, run['id'], status='skipped' if result.get('status') == 'skipped' or result.get('state') in {'skipped','rejected'} else 'admitted', result=result, finishedAt=now())
                except Exception as exc:
                    current = (await self.io(self.ledger.read))['runs'][run['id']]
                    await self.io(self.ledger.update, run['id'], status='uncertain' if current['status'] == 'admitting' else 'failed',
                                  error=str(exc), finishedAt=now())
            if self.job_runtime and not self.closed:
                await self.job_runtime.tick({'enabled': True})

    async def close(self):
        self.closed = True
        async with self.dispatching:
            pass
