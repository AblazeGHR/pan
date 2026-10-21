"""State predicates, frozen parameter mapping and durable serial executors."""
import asyncio
import copy
import hashlib
import json
import operator
import uuid
from collections import deque

from .events import now


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


def arguments(mapping, event):
    values = {}
    for key, binding in mapping.items():
        if binding.get('from') == 'literal' and set(binding) == {'from', 'value'}:
            values[key] = copy.deepcopy(binding['value'])
        elif binding.get('from') == 'event' and set(binding) == {'from', 'path'}:
            values[key] = copy.deepcopy(field(event, binding['path']))
        else:
            raise ValueError('Invalid argument binding')
    return values


class PersistenceError(RuntimeError):
    pass


class Engine:
    def __init__(self, ledger, bus, actions, execute, state_reader, max_pending=1024, max_triggers=64):
        self.ledger, self.bus, self.actions = ledger, bus, actions
        self.execute, self.state_reader = execute, state_reader
        self.max_pending, self.max_triggers = max_pending, max_triggers
        self.tasks = {}
        self.wakes = set()
        self.diagnostics = deque(maxlen=100)
        self.closed = False
        self.matching = asyncio.Lock()

    async def io(self, fn, *args, **kw):
        # Settle writes before propagating cancellation; no orphan disk writer.
        task = asyncio.create_task(asyncio.to_thread(fn, *args, **kw))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    def available(self, trigger):
        return all(self.bus.available(t) for t in trigger['eventTypes'])

    async def start(self):
        if self.closed:
            return
        await self.io(self.ledger.recover)
        # close is terminal for this Service instance, including in-flight start.
        if not self.closed:
            await self.kick()

    async def match(self, event):
        # Only matching/append is serialized. Never wait for an Action here.
        async with self.matching:
            state = await self.io(self.ledger.read)
            pending = sum(r['status'] in {'queued', 'running'} for r in state['runs'].values())
            seen = {(r['event']['id'], r['triggerId']) for r in state['runs'].values()}
            persisted = []
            for trigger in state['triggers'].values():
                if (event['id'], trigger['id']) in seen:
                    continue
                if not trigger['enabled'] or event['type'] not in trigger['eventTypes'] or not self.available(trigger):
                    continue
                run = dict(id='run_' + uuid.uuid4().hex, triggerId=trigger['id'], triggerRevision=trigger['revision'], actionId=trigger['actionId'], event=copy.deepcopy(event), createdAt=now(), status='queued', admitted=False, arguments={}, state={})
                try:
                    if pending >= self.max_pending:
                        raise ValueError('Trigger backlog full')
                    if not await evaluate(trigger.get('condition'), self.state_reader, run['state']):
                        run.update(status='skipped', finishedAt=now())
                    else:
                        run['arguments'] = arguments(trigger.get('arguments', {}), event)
                except Exception as exc:
                    run.update(status='failed', error=str(exc), finishedAt=now())
                try:
                    saved = await self.io(self.ledger.append, run)
                    if saved:
                        persisted.append(saved['id'])
                        pending += saved['status'] == 'queued'
                except Exception as exc:
                    self.diagnostics.append(dict(eventId=event['id'], triggerId=trigger['id'], error=str(exc), phase='persist', at=now()))
            # Durable admission fence prevents existing runners/config kicks from
            # consuming partially matched events. Failure leaves them waiting.
            try:
                if persisted:
                    await self.io(self.ledger.release_event, event['id'])
            except Exception as exc:
                self.diagnostics.append(dict(eventId=event['id'], phase='admission', error=str(exc), at=now()))
            await self.kick()
            return persisted

    async def kick(self):
        if self.closed:
            return
        state = await self.io(self.ledger.read)
        # Publication below and runner retirement have no await: the event loop
        # orders ownership changes, even when this snapshot predates close.
        if self.closed:
            return
        for trigger in state['triggers'].values():
            tid = trigger['id']
            if trigger['enabled'] and self.available(trigger):
                if any(r['triggerId'] == tid and r['status'] == 'queued' and r.get('admitted', True) for r in state['runs'].values()):
                    self.wakes.add(tid)
                    if tid not in self.tasks:
                        self.tasks[tid] = asyncio.create_task(self._runner(tid))

    async def _runner(self, tid):
        try:
            while not self.closed:
                self.wakes.discard(tid)
                await self.drain(tid)
                # A kick during any drain await leaves a coalesced wake. Consume
                # it with a fresh read, including after an empty stale snapshot.
                if tid not in self.wakes:
                    return
        finally:
            # No await between wake decision and ownership retirement. A later
            # kick either wakes this owner or installs its successor, never both.
            if self.tasks.get(tid) is asyncio.current_task():
                del self.tasks[tid]
                self.wakes.discard(tid)

    async def drain(self, tid):
        try:
            while not self.closed:
                state = await self.io(self.ledger.read)
                trigger = state['triggers'].get(tid)
                if not trigger or not trigger['enabled'] or not self.available(trigger):
                    return
                run = next((r for r in state['runs'].values() if r['triggerId'] == tid and r['status'] == 'queued'), None)
                if not run or not run.get('admitted', True):
                    return
                run = await self.io(self.ledger.claim, run['id'])
                if not run:
                    continue
                if not self.available(trigger):
                    await self.io(self.ledger.defer_unavailable, run['id'], run['revision'])
                    return
                try:
                    action = (await self.io(self.actions.read))['actions'][run['actionId']]
                    digest = hashlib.sha256(json.dumps(action, sort_keys=True).encode()).hexdigest()
                    await self.io(self.ledger.update, run['id'], actionDigest=digest, action=action)
                    async def progress(**fields):
                        try:
                            await self.io(self.ledger.update, run['id'], **fields)
                        except Exception as exc:
                            raise PersistenceError(str(exc)) from exc
                    result = await self.execute(action, run['arguments'], run['event'], progress)
                    ok = not result.get('isError', False) and result.get('ok', True)
                    try:
                        await self.io(self.ledger.update, run['id'], status='succeeded' if ok else 'failed', result=result, finishedAt=now())
                    except Exception as exc:
                        raise PersistenceError(str(exc)) from exc
                except asyncio.CancelledError:
                    await self.io(self.ledger.update, run['id'], status='uncertain', error='Execution interrupted; no replay', finishedAt=now())
                    raise
                except Exception as exc:
                    await self.io(self.ledger.update, run['id'], status='uncertain' if isinstance(exc, PersistenceError) else 'failed', error=str(exc), finishedAt=now())
        except Exception as exc:
            self.diagnostics.append(dict(triggerId=tid, phase='executor', error=str(exc), at=now()))

    async def close(self):
        self.closed = True
        tasks = tuple(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        # A task cancelled before its first step never enters _runner.finally.
        self.tasks.clear()
        self.wakes.clear()
        await self.io(self.ledger.recover)
