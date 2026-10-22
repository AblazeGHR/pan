"""Explicit automation lifetime, durable event admission and bounded dispatch."""
import asyncio
from functools import partial

from packages.actions.executor import execute_dynamic
from packages.actions.store import Store
from .auth import UserAuth
from .engine import Engine
from .events import Bus, now
from .event_store import EventStore
from .store import Ledger


class Service:
    def __init__(self, root, worker, pause):
        self.events = EventStore(root)
        self.bus = Bus(durable=self.accept)
        self.ledger = Ledger(root)
        self.auth = UserAuth(root)
        self.worker = worker
        self.core = self.bus.activate('core', 'workers')
        self.bus.register(self.core, 'workers.all_idle', {'type': 'object', 'properties': {'runningCount': {'const': 0}}, 'required': ['runningCount'], 'additionalProperties': False})
        async def state_reader(key):
            if key == 'workers.runningCount':
                return sum(w.status == 'running' for w in worker.workers.values())
            raise ValueError('Unknown State field: ' + str(key))
        self.engine = Engine(self.ledger, self.bus, Store(), partial(execute_dynamic, pause=pause), state_reader)
        self.previous = sum(w.status == 'running' for w in worker.workers.values())
        self.ingress = asyncio.Queue(128)
        self.wake = asyncio.Event()
        self.admission = asyncio.Lock()
        self.started = False
        self.stopping = False
        self.dispatcher = None
        self.ingester = None
        self.job_runtime = None
        self.engine_started = False

    def diagnostic(self, phase, error, event_id=None):
        self.bus.diagnostics.append(dict(phase=phase, error=str(error), eventId=event_id, at=now()))

    def observe(self):
        count = sum(w.status == 'running' for w in self.worker.workers.values())
        previous, self.previous = self.previous, count
        if previous > 0 and count == 0:
            try:
                self.ingress.put_nowait(self.bus.prepare(self.core, 'workers.all_idle', {'runningCount': 0}))
            except Exception as exc:
                self.diagnostic('core-ingress', exc)

    async def accept(self, event, handle=None):
        # At most one admission snapshot/commit at a time; no Action is awaited.
        async with self.admission:
            if not self.started or self.stopping:
                raise ValueError('Durable event admission is closed')
            state = await self.engine.io(self.ledger.read)
            if handle is not None and handle not in self.bus.handles:
                raise ValueError('Event publisher revoked before durable admission')
            targets = [t for t in state['triggers'].values() if t['enabled'] and event['type'] in t['eventTypes'] and self.engine.available(t)]
            if self.job_runtime:
                targets.extend(await self.engine.io(self.job_runtime.event_targets,event['type']))
            try:
                receipt = await self.engine.io(self.events.append, event, targets)
            except Exception as exc:
                self.diagnostic('event-admission', exc, event['id'])
                raise
            self.wake.set()
            return receipt

    async def ingest_core(self):
        while True:
            event = await self.ingress.get()
            try:
                await self.accept(event)
            except Exception as exc:
                self.diagnostic('core-persist', exc, event['id'])
            finally:
                self.ingress.task_done()

    async def maintenance(self):
        removed = await self.engine.io(self.events.maintenance)
        runs = await self.engine.io(self.ledger.maintenance, removed['retainedIds'])
        return {'deletedEvents': removed['deletedEvents'], **runs}

    async def dispatch(self):
        loop = asyncio.get_running_loop()
        maintenance_at = loop.time() + 60
        failures = 0
        cursor = 0
        cycle_retry = False
        while not self.stopping:
            self.wake.clear()
            retry = False
            try:
                if loop.time() >= maintenance_at:
                    await self.maintenance()
                    maintenance_at = loop.time() + 60
                batch = await self.engine.io(self.events.pending, 64, cursor)
                for row in batch:
                    cursor = row['seq']
                    if self.stopping:
                        return
                    try:
                        await self.engine.match(row['event'], targets=[t for t in row['targets'] if not t.get('_job')], strict=True)
                        if self.job_runtime:
                            await self.job_runtime.match_event(row['event'],[t for t in row['targets'] if t.get('_job')])
                        # Notifications remain best effort; durable inbox delivery
                        # guarantees Trigger matching, not plugin delivery receipts.
                        self.bus._fanout(row['event'])
                        await self.engine.io(self.events.delivery, row['event']['id'], done=True)
                    except Exception as exc:
                        retry = True
                        self.diagnostic('event-delivery', exc, row['event']['id'])
                        await self.engine.io(self.events.delivery, row['event']['id'], error=exc)
                cycle_retry = cycle_retry or retry
                if len(batch) == 64:
                    continue
                cursor = 0
                retry = cycle_retry
                cycle_retry = False
            except Exception as exc:
                retry = True
                self.diagnostic('event-store', exc)
            failures = min(failures + 1, 5) if retry else 0
            # Retry only Event-to-Run persistence, never a failed/uncertain Action.
            # A wake or plugin-ready interrupts this bounded failure backoff.
            delay = min(2**failures, 30) if retry else max(.1, maintenance_at-loop.time())
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=delay)
            except TimeoutError:
                pass

    async def start(self):
        if self.stopping:
            return
        from packages.jobs.action_runtime import Runtime
        from packages.scheduler.store import data_root
        self.job_runtime = Runtime(data_root(), self.engine.actions, self.engine.execute.keywords.get('pause') if isinstance(self.engine.execute, partial) else None)
        self.job_runtime.event_available = self.bus.available
        await self.job_runtime.start()
        self.engine.job_runtime = self.job_runtime
        from .quota_policy import Runtime as QuotaRuntime
        self.quota_policy = QuotaRuntime(self.ledger.path.parent, data_root())
        from .quota_host import production
        self.quota_policy.adapter = production(self.quota_policy.store, self.worker, self.worker._sess.SESSION_DIR.parent)
        self.quota_policy.adapter.runtime = self.quota_policy
        await self.quota_policy.recover()
        self.job_runtime.quota_policy = self.quota_policy
        from packages.core.runtime_issues import set_observer
        set_observer(self.quota_policy.observe)
        if self.stopping:
            return
        await self.engine.io(self.auth.key)
        await self.engine.io(self.events.initialize)
        if self.stopping:
            return
        await self.maintenance()
        self.engine_started = True
        await self.engine.start()
        if self.stopping:
            return
        self.started = True
        self.dispatcher = asyncio.create_task(self.dispatch(), name='automation-event-dispatch')
        self.ingester = asyncio.create_task(self.ingest_core(), name='automation-core-ingress')
        self.worker.set_running_observer(self.observe)

    async def close(self):
        if self.started:
            self.worker.set_running_observer(None)
        self.stopping = True
        if getattr(self,'quota_policy',None):
            self.quota_policy.closed = True
        if self.job_runtime:
            self.job_runtime.closed = True
        self.wake.set()
        # Settle accepted writes before returning; pending records survive close.
        tasks = [t for t in (self.ingester, self.dispatcher) if t]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        async with self.admission:
            self.started = False
        await self.bus.close()
        if self.engine_started:
            await self.engine.close()
        else:
            self.engine.closed = True
        if getattr(self,'quota_policy',None):
            await self.quota_policy.close()
        if self.job_runtime:
            await self.job_runtime.close()
        if getattr(self,'quota_policy',None):
            self.quota_policy.closed = True
            from packages.core.runtime_issues import set_observer
            set_observer(None)

    async def replay(self, ids):
        async with self.engine.matching:
            state = await self.engine.io(self.ledger.read)
            if isinstance(ids, list) and any(r['event']['id'] in ids and r['status'] == 'uncertain' for r in state['runs'].values()):
                raise ValueError('Uncertain Run requires reconciliation; Event replay refused')
            result = await self.engine.io(self.events.replay, ids)
        self.wake.set()
        return result

    async def plugin_enabled(self, plugin_id):
        return self.bus.activate('plugin', plugin_id)

    async def plugin_ready(self):
        self.wake.set()
        await self.engine.kick()

    async def plugin_disabled(self, handle):
        await self.bus.deactivate(handle)
