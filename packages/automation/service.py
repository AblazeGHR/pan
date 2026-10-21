"""Explicit service lifetime and stable internal adapter contract."""
from functools import partial

from packages.actions.executor import execute_dynamic
from packages.actions.store import Store
from .auth import UserAuth
from .engine import Engine
from .events import Bus
from .store import Ledger


class Service:
    def __init__(self, root, worker, pause):
        self.bus = Bus()
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

    def observe(self):
        count = sum(w.status == 'running' for w in self.worker.workers.values())
        previous, self.previous = self.previous, count
        if previous > 0 and count == 0:
            self.bus.publish(self.core, 'workers.all_idle', {'runningCount': 0})

    async def start(self):
        await self.engine.io(self.auth.key)
        await self.engine.start()
        self.bus.subscribe(self.core, 'core.triggers', self.engine.match)
        self.worker.set_running_observer(self.observe)

    async def close(self):
        self.worker.set_running_observer(None)
        await self.bus.close()
        await self.engine.close()

    async def plugin_enabled(self, plugin_id):
        # Only trusted runtime/adapter code calls this, never an HTTP payload.
        handle = self.bus.activate('plugin', plugin_id)
        return handle

    async def plugin_ready(self):
        # Call after declarations are registered; resume waiting durable runs.
        await self.engine.kick()

    async def plugin_disabled(self, handle):
        await self.bus.deactivate(handle)
