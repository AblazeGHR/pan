"""Loop-local admission reservations; no mutex is held across awaits or IO.

Reservations are atomic in-memory operations on the owning event loop. Readers
share Group resources; release transactions exclusively claim a Group and its
referencing Sessions. Only overlapping operations wait on the completion event.
"""
import asyncio
from contextlib import asynccontextmanager
import functools
import weakref

_coordinators = weakref.WeakKeyDictionary()
_pins = {}


class Coordinator:
    def __init__(self):
        self.active = {}
        self.changed = asyncio.Event()

    @asynccontextmanager
    async def claim(self, shared=(), exclusive=()):
        owner = asyncio.current_task()
        shared, exclusive = frozenset(shared), frozenset(exclusive)
        while any(task is not owner and (exclusive & (read | write) or shared & write)
                  for task, read, write in self.active.values()):
            await self.changed.wait()
        token = object()
        self.active[token] = (owner, shared, exclusive)
        try:
            yield
        finally:
            del self.active[token]
            event, self.changed = self.changed, asyncio.Event()
            event.set()


def coordinator():
    return _coordinators.setdefault(asyncio.get_running_loop(), Coordinator())


def action_groups(config):
    groups = {b['plugin_id'] for b in config.get('plugin_bindings', [])
              if b.get('enabled') and b.get('plugin_id', '').startswith('action-group-')}
    groups.update(b['name'] for b in config.get('mcp_servers', [])
                  if isinstance(b, dict) and b.get('name', '').startswith('action-group-'))
    return groups


@asynccontextmanager
async def session_claim(session, extra=()):
    # A preceding settings/release reservation may change the selection while
    # we wait. Re-read before admission; never proceed with an incomplete claim.
    while True:
        groups = action_groups(session.adapter_config) | set(extra)
        async with coordinator().claim(shared=groups, exclusive={'session:' + session.id}):
            if not action_groups(session.adapter_config) <= groups:
                continue
            yield
            return


def startup(function):
    @functools.wraps(function)
    async def wrapped(*args, **kwargs):
        from packages.core import session as sessions
        session = sessions.get(args[0] if args else kwargs['session_id'])
        if session is None:
            return await function(*args, **kwargs)
        async with session_claim(session):
            return await function(*args, **kwargs)
    return wrapped


def native_admission(function):
    @functools.wraps(function)
    async def wrapped(worker, *args, **kwargs):
        from packages.core import session as sessions
        session = sessions.get(worker.session_id)
        if session is None:
            return await function(worker, *args, **kwargs)
        async with session_claim(session):
            if set(pinned(session.id)[0]) - action_groups(session.adapter_config):
                raise RuntimeError('Loaded Action Group detached; restart Worker before native admission')
            return await function(worker, *args, **kwargs)
    return wrapped


async def io(function, *args, **kwargs):
    """A cancelled caller cannot release its reservation before its writer retires."""
    from packages.core.session import await_persistence
    return await await_persistence(asyncio.create_task(asyncio.to_thread(function, *args, **kwargs)))


def settled(function):
    """Finish a multi-file transaction (including rollback) before cancellation escapes."""
    @functools.wraps(function)
    async def wrapped(*args, **kwargs):
        from packages.core.session import await_persistence
        return await await_persistence(asyncio.create_task(function(*args, **kwargs)))
    return wrapped


def pin(session, catalog):
    """Capture immutable managed-plugin releases at a real Worker start."""
    from .catalog import session_bindings
    _pins[session.id] = ({b['plugin_id']: catalog.get(b['plugin_id'])
                         for b in session_bindings(session, catalog)
                         if b['enabled'] and b['plugin_id'].startswith('action-group-')}, dict(catalog.servers))


def pinned(session_id):
    return _pins.get(session_id, ({}, {}))


def apply_pins(catalog, session_id):
    plugins, servers = pinned(session_id)
    for pid, plugin in plugins.items():
        if pid in catalog.plugins:
            catalog.plugins[pid] = plugin
            catalog.servers[pid + '-provider'] = servers[pid + '-provider']


def capture(session):
    if not any(b.get('enabled') and b.get('plugin_id', '').startswith('action-group-')
               for b in session.adapter_config.get('plugin_bindings', [])):
        _pins.pop(session.id, None)
        return
    from packages.core.config import load_config, DEFAULT_PLUGIN_MANIFESTS
    from packages.core.manifest_loader import load_manifests
    from .catalog import Catalog
    pin(session, Catalog(load_manifests(load_config().get('plugin_manifests', DEFAULT_PLUGIN_MANIFESTS))))


async def capture_async(session):
    from packages.core.config import load_config, DEFAULT_PLUGIN_MANIFESTS
    from packages.core.manifest_loader import load_manifests
    from .catalog import Catalog
    def load():
        return Catalog(load_manifests(load_config().get('plugin_manifests', DEFAULT_PLUGIN_MANIFESTS)))
    if not action_groups(session.adapter_config):
        _pins.pop(session.id, None)
    else:
        pin(session, await io(load))
