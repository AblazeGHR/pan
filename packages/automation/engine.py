"""State predicates, frozen parameter mapping and durable bounded executors."""
import asyncio
import copy
import hashlib
import json
import operator
import uuid
from collections import deque
from datetime import datetime

from .policy import concurrency

from .events import now
from .store import Conflict


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
        self.signals = {}
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

    async def match(self, event, targets=None, strict=False):
        # Only matching/append is serialized. Never wait for an Action here.
        async with self.matching:
            state = await self.io(self.ledger.read)
            pending = sum(r['status'] in {'queued', 'running', 'retry_wait'} for r in state['runs'].values())
            seen = {(r['event']['id'], r['triggerId']) for r in state['runs'].values()}
            persisted = []
            errors = []
            for trigger in targets if targets is not None else state['triggers'].values():
                if (event['id'], trigger['id']) in seen:
                    continue
                if not trigger['enabled'] or event['type'] not in trigger['eventTypes']:
                    continue
                current = state['triggers'].get(trigger['id'])
                obsolete = not current or not current['enabled'] or current['revision'] != trigger['revision']
                if not obsolete and not self.available(trigger):
                    if strict:
                        errors.append('Event dependency unavailable')
                    continue
                run = dict(id='run_' + uuid.uuid4().hex, triggerId=trigger['id'], triggerRevision=trigger['revision'], actionId=trigger['actionId'], event=copy.deepcopy(event), createdAt=now(), status='queued', admitted=False, arguments={}, state={})
                try:
                    if obsolete:
                        run.update(status='cancelled', finishedAt=now(), error='Trigger changed before event delivery')
                    elif pending >= self.max_pending:
                        self.diagnostics.append(dict(eventId=event['id'], triggerId=trigger['id'], phase='capacity', error='Trigger backlog full', at=now()))
                        raise ValueError('Trigger backlog full')
                    elif not await evaluate(trigger.get('condition'), self.state_reader, run['state']):
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
                    errors.append(str(exc))
                    self.diagnostics.append(dict(eventId=event['id'], triggerId=trigger['id'], error=str(exc), phase='persist', at=now()))
            # Durable admission fence prevents existing runners/config kicks from
            # consuming partially matched events. Failure leaves them waiting.
            try:
                if persisted or strict:
                    await self.io(self.ledger.release_event, event['id'])
            except Exception as exc:
                errors.append(str(exc))
                self.diagnostics.append(dict(eventId=event['id'], phase='admission', error=str(exc), at=now()))
            await self.kick()
            if strict and errors:
                raise PersistenceError('; '.join(errors))
            return persisted

    async def kick(self):
        if self.closed:
            return
        state = await self.io(self.ledger.read)
        # Publication below and runner retirement have no await: the event loop
        # orders ownership changes, even when this snapshot predates close.
        if self.closed:
            return
        for tid in self.tasks:
            self.signals[tid].set()
        for trigger in state['triggers'].values():
            tid = trigger['id']
            if trigger['enabled'] and self.available(trigger):
                if any(r['triggerId'] == tid and r['status'] in {'queued', 'retry_wait'} and r.get('admitted', True) for r in state['runs'].values()):
                    self.wakes.add(tid)
                    self.signals.setdefault(tid, asyncio.Event()).set()
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
                self.signals.pop(tid, None)

    async def drain(self, tid):
        active = {}
        signal = self.signals.setdefault(tid, asyncio.Event())
        try:
            while not self.closed:
                # Clear before every snapshot await: publication/completion during
                # any await leaves a wake. Ownership retirement remains atomic.
                signal.clear()
                for rid, task in list(active.items()):
                    if task.done():
                        try:
                            await task
                        except Exception as exc:
                            self.diagnostics.append(dict(triggerId=tid, runId=rid, phase='attempt', error=str(exc), at=now()))
                        del active[rid]
                state = await self.io(self.ledger.read)
                trigger = state['triggers'].get(tid)
                enabled = trigger and trigger['enabled'] and self.available(trigger)
                limit = concurrency(trigger) if trigger else 1
                waiting = [r for r in state['runs'].values() if r['triggerId'] == tid
                           and r['status'] in {'queued', 'retry_wait'}] if enabled else []
                # Serial retains admission order, including its retry backoff.
                candidates = waiting[:1] if trigger and trigger.get('executionMode', 'serial') == 'serial' else waiting
                delay = None
                for run in candidates:
                    if self.closed or len(active) >= limit:
                        break
                    if run['id'] in active:
                        continue  # A committed finish may precede its task retirement.
                    if not run.get('admitted', True):
                        continue
                    if run['status'] == 'retry_wait':
                        remaining = datetime.fromisoformat(run['nextAttemptAt']).timestamp()-self.ledger.clock()
                        if remaining > 0:
                            delay = remaining if delay is None else min(delay, remaining)
                            continue
                    claimed = await self.io(self.ledger.claim, run['id'])
                    if self.closed:
                        return
                    if not claimed:
                        fresh = await self.io(self.ledger.read)
                        current = fresh['triggers'].get(tid)
                        if (fresh['runs'].get(run['id'], {}).get('status') not in {'queued', 'retry_wait'}
                                or (current and concurrency(current) != limit)):
                            signal.set()  # A stale claim retired this head; examine its successor.
                        break  # Never skip this head using a stale concurrency snapshot.
                    if not self.available(trigger):
                        await self.io(self.ledger.defer_unavailable, claimed['id'], claimed['revision'])
                        break
                    task = asyncio.create_task(self.run_attempt(claimed), name='automation-attempt-'+claimed['activeAttemptId'])
                    active[claimed['id']] = task
                    task.add_done_callback(lambda _: signal.set())
                if not active and (not enabled or not waiting):
                    return
                if not active and delay is None:
                    if signal.is_set():
                        continue
                    # A rejected stale claim or admission fence needs a fresh
                    # snapshot; no poll loop. A concurrent kick is coalesced.
                    return
                if len(active) >= limit:
                    delay = None  # Active completion frees a slot and wakes us.
                if delay is None:
                    await signal.wait()
                else:
                    try:
                        await asyncio.wait_for(signal.wait(), delay)
                    except TimeoutError:
                        pass
        except asyncio.CancelledError:
            for task in active.values():
                task.cancel()
            raise
        except Exception as exc:
            self.diagnostics.append(dict(triggerId=tid, phase='executor', error=str(exc), at=now()))
        finally:
            # Trigger disable allows started work to complete. Service close
            # cancels it and settles all progress writes before recovery.
            await asyncio.gather(*active.values(), return_exceptions=True)

    async def run_attempt(self, run):
        rid = run['id']
        try:
            action = (await self.io(self.actions.read))['actions'][run['actionId']]
            digest = hashlib.sha256(json.dumps(action, sort_keys=True).encode()).hexdigest()
            run = await self.io(self.ledger.prepare_action, rid, action, digest)
            async def progress(**fields):
                try:
                    await self.io(self.ledger.update, rid, **fields)
                except Exception as exc:
                    raise PersistenceError(str(exc)) from exc
            # Existing executor signature remains callable-compatible. Session
            # Operations use this durable checkpoint on later attempts, avoiding
            # query reevaluation or repeating confirmed successful targets.
            progress.checkpoint = copy.deepcopy(run)
            if getattr(self, 'job_runtime', None):
                progress.instantiate_template = self.job_runtime.instantiate_action
            result = await self.execute(action, run['arguments'], run['event'], progress)
            ok = not result.get('isError', False) and result.get('ok', True)
            try:
                await self.io(self.ledger.finish, rid, 'succeeded' if ok else 'failed', result=result)
            except Exception as exc:
                raise PersistenceError(str(exc)) from exc
        except asyncio.CancelledError:
            await self.io(self.ledger.interrupt, rid, run['activeAttemptId'], 'Execution interrupted; no automatic retry')
            raise
        except Exception as exc:
            if isinstance(exc, PersistenceError):
                await self.io(self.ledger.interrupt, rid, run['activeAttemptId'], str(exc))
            else:
                await self.io(self.ledger.finish, rid, 'failed', retryable=not isinstance(exc, Conflict), error=str(exc))

    async def close(self):
        self.closed = True
        tasks = tuple(self.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        # A task cancelled before its first step never enters _runner.finally.
        self.tasks.clear()
        self.wakes.clear()
        self.signals.clear()
        await self.io(self.ledger.recover)
