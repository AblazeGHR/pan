"""Serialize plugin registration with existing Worker startup and settings commits."""
import asyncio
import functools
import weakref

_gates = weakref.WeakKeyDictionary()
_pins = {}


class Gate:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.owner = None
        self.depth = 0

    async def __aenter__(self):
        task = asyncio.current_task()
        if self.owner is not task:
            await self.lock.acquire()
            self.owner = task
        self.depth += 1

    async def __aexit__(self, *args):
        self.depth -= 1
        if not self.depth:
            self.owner = None
            self.lock.release()


def gate():
    return _gates.setdefault(asyncio.get_running_loop(), Gate())


def serialized(function):
    @functools.wraps(function)
    async def wrapped(*args, **kwargs):
        async with gate():
            return await function(*args, **kwargs)
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
