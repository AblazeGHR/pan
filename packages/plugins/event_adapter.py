"""Catalog-admitted external event connections, shared across Session projections."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import signal
import sys
import uuid

import jsonschema

from .catalog import Catalog, digest, session_bindings
from .data import plugin_data_dir
from .lifecycle import io
from .providers import provider_entry
from packages.jobs.window import command_window_options

PROTOCOL = 'pan-events/v1'
MAX_FRAME = 128 * 1024
TIMEOUT = 5


def validate_events(pid, spec, servers):
    ref = spec['provider']['command_ref']
    if ref not in servers or ref in {'pan', 'pan-tools'} or not servers[ref].get('command'):
        raise ValueError('Event provider requires a configured stdio command_ref')
    if servers[ref].get('url') or 'packages.plugins.mcp' in servers[ref].get('args', []):
        raise ValueError('Event provider cannot be a remote/projection connection')
    seen = set()
    def check(value):
        if isinstance(value, dict):
            if any(k in value for k in ('$ref', '$dynamicRef', '$recursiveRef')):
                raise ValueError('Event schema references unsupported')
            for child in value.values():
                check(child)
        elif isinstance(value, list):
            for child in value:
                check(child)
    for definition in spec['definitions']:
        name = definition['type']
        if not name.startswith(pid + '.') or name in seen:
            raise ValueError('Event definitions require unique plugin-owned types')
        seen.add(name)
        jsonschema.Draft202012Validator.check_schema(definition['schema'])
        check(definition['schema'])


class EventConnection:
    """One reader, serialized bounded writes, one in-flight delivery per subscriber."""
    def __init__(self, manager, plugin, catalog, members, revision):
        self.manager, self.plugin = manager, plugin
        self.catalog, self.members, self.revision = catalog, members, revision
        self.generation = uuid.uuid4().hex
        self.handle = None
        self.process = None
        self.reader = None
        self.pending = {}
        self.sequence = 0
        self.write_lock = asyncio.Lock()
        self.closed = False
        self.ready = False

    async def start(self):
        root = self.manager.root
        data = await io(plugin_data_dir, root, self.plugin.id, self.plugin.data_version)
        entry = provider_entry(self.catalog.servers[self.plugin.events['provider']['command_ref']],
                               type('Owner', (), {'id': '', 'name': self.plugin.display_name})())
        argv = [entry['command'], *entry.get('args', [])]
        windows = os.name == 'nt'
        if windows:
            argv = [sys.executable, '-m', 'packages.plugins.stdio_runner', *argv]
        # runner must resolve its own package even with an external provider cwd.
        env = {**os.environ, **entry.get('env', {}), 'PAN_PLUGIN_ID': self.plugin.id,
               'PAN_PLUGIN_ROLE': 'events', 'PAN_PLUGIN_DATA_DIR': str(data)}
        env['PYTHONPATH'] = str(Path(__file__).resolve().parents[2]) + (
            os.pathsep + env['PYTHONPATH'] if env.get('PYTHONPATH') else '')
        env.pop('PAN_AGENT_SESSION_ID', None)
        self.process = await asyncio.create_subprocess_exec(
            *argv, cwd=entry.get('cwd'), env=env, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            limit=MAX_FRAME, **(command_window_options() if windows else {'start_new_session': True}))
        self.reader = asyncio.create_task(self.read(), name=f'events-{self.plugin.id}')
        self.stderr = asyncio.create_task(self.drain_stderr())
        initialized = await self.request('provider.initialize', {'protocol_version': 'pan-capability/v1',
                                                               'extensions': [PROTOCOL], 'role': 'events'})
        if initialized.get('protocol_version') != 'pan-capability/v1' or PROTOCOL not in initialized.get('extensions', []):
            raise ValueError('Event provider did not negotiate pan-events/v1')
        reply = await self.request('events.initialize', {'protocol_version': PROTOCOL,
            'plugin_id': self.plugin.id, 'generation': self.generation,
            'definitions': self.plugin.events['definitions'], 'subscriptions': self.plugin.events['subscriptions']})
        if reply != {'ready': True}:
            raise ValueError('Event provider not ready')
        if self.reader.done() or not await self.manager.eligible(self):
            raise ValueError('Plugin binding changed during Event startup')
        self.handle = await self.manager.service.plugin_enabled(self.plugin.id)
        for definition in self.plugin.events['definitions']:
            self.manager.service.bus.register(self.handle, definition['type'], definition['schema'], definition['schemaVersion'])
            # Presentation metadata; eligibility and schema ownership remain Bus authority.
            self.manager.service.bus.definitions[definition['type']]['description'] = definition['description']
        if self.plugin.events['subscriptions']:
            self.manager.service.bus.subscribe(self.handle, 'plugin.' + self.plugin.id, self.deliver)
        self.ready = True
        await self.manager.service.plugin_ready()
        await self.request('events.ready', {'generation': self.generation})

    async def drain_stderr(self):
        while chunk := await self.process.stderr.read(2048):
            self.manager.diagnostic(self.plugin.id, 'stderr: ' + chunk.decode(errors='replace')[:512])

    async def write(self, message):
        raw = (json.dumps(message, ensure_ascii=False, allow_nan=False) + '\n').encode()
        if len(raw) > MAX_FRAME:
            raise ValueError('Event frame exceeds 128 KiB')
        async with asyncio.timeout(TIMEOUT), self.write_lock:
            if self.closed:
                raise RuntimeError('Event connection closed')
            self.process.stdin.write(raw)
            await self.process.stdin.drain()

    async def request(self, method, params):
        self.sequence += 1
        rid = 'host-' + str(self.sequence)
        future = asyncio.get_running_loop().create_future()
        self.pending[rid] = future
        try:
            async with asyncio.timeout(TIMEOUT):
                await self.write({'jsonrpc': '2.0', 'id': rid, 'method': method, 'params': params})
                return await future
        finally:
            self.pending.pop(rid, None)

    async def read(self):
        try:
            while line := await self.process.stdout.readline():
                if len(line) > MAX_FRAME:
                    raise ValueError('Event frame exceeds 128 KiB')
                message = json.loads(line, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
                if not isinstance(message, dict) or message.get('jsonrpc') != '2.0':
                    raise ValueError('Invalid Event JSON-RPC frame')
                if 'method' in message:
                    # Handle inline: bounded work, no per-message task spawning.
                    rid = message.get('id')
                    if not isinstance(rid, (str, int)) or isinstance(rid, bool):
                        raise ValueError('Event publication requires a request ID')
                    try:
                        if message['method'] != 'events.publish':
                            raise ValueError('Unknown Event method')
                        params = message.get('params', {})
                        if not isinstance(params, dict) or set(params) - {'type', 'payload', 'event_id', 'generation'}:
                            raise ValueError('Invalid Event publication fields; source is connection-bound')
                        if params.get('generation') != self.generation or not self.ready or not await self.manager.eligible(self):
                            raise ValueError('Event connection is no longer eligible')
                        result = self.manager.service.bus.publish(self.handle, params['type'], params['payload'], params.get('event_id'))
                        reply = {'result': result}
                    except Exception as exc:
                        self.manager.diagnostic(self.plugin.id, str(exc))
                        reply = {'error': {'code': -32602, 'message': str(exc)[:1000]}}
                    await self.write({'jsonrpc': '2.0', 'id': rid, **reply})
                else:
                    future = self.pending.get(message.get('id'))
                    if future is None or future.done():
                        raise ValueError('Unexpected Event response ID')
                    if 'error' in message:
                        future.set_exception(RuntimeError(str(message['error'])[:1000]))
                    elif 'result' in message:
                        future.set_result(message['result'])
                    else:
                        raise ValueError('Event response missing result')
            raise RuntimeError('Event provider stdout closed')
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.manager.diagnostic(self.plugin.id, str(exc))
        finally:
            self.ready = False
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(RuntimeError('Event connection disconnected'))
            if self.handle is not None:
                await self.manager.service.plugin_disabled(self.handle)
                self.handle = None

    async def deliver(self, event):
        if event['type'] not in self.plugin.events['subscriptions']:
            return
        try:
            if not self.ready or not await self.manager.eligible(self):
                raise RuntimeError('Event subscriber no longer eligible')
            if await self.request('events.deliver', {'generation': self.generation, 'event': event}) != {'accepted': True}:
                raise RuntimeError('Event subscriber rejected delivery')
        except Exception as exc:
            self.manager.diagnostic(self.plugin.id, 'delivery: ' + (str(exc) or type(exc).__name__))
            # A stalled consumer loses its connection; other subscribers continue.
            self.ready = False
            if self.reader:
                self.reader.cancel()
            raise

    async def close(self):
        self.closed, self.ready = True, False
        if self.reader:
            self.reader.cancel()
            await asyncio.gather(self.reader, return_exceptions=True)
        if self.handle is not None:
            await self.manager.service.plugin_disabled(self.handle)
            self.handle = None
        if self.process:
            if os.name == 'nt':
                if self.process.returncode is None:
                    self.process.kill()
            else:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            await asyncio.wait_for(self.process.wait(), TIMEOUT)
            await asyncio.gather(self.stderr, return_exceptions=True)


class EventRuntime:
    """Service-owned union of the existing enabled Session bindings; no new enable flag."""
    def __init__(self, service, get_manifest, get_sessions, root):
        self.service, self.get_manifest, self.get_sessions, self.root = service, get_manifest, get_sessions, root
        self.connections = {}
        self.lock = asyncio.Lock()
        self.task = None
        self.stopping = False

    def diagnostic(self, pid, message):
        from packages.automation.events import now
        self.service.bus.diagnostics.append({'plugin': pid, 'error': message[:1000], 'at': now()})

    def desired(self):
        catalog = Catalog(self.get_manifest())
        members = {}
        for session in self.get_sessions():
            for binding in session_bindings(session, catalog):
                pid = binding['plugin_id']
                if binding['enabled'] and catalog.get(pid).events is not None:
                    members.setdefault(pid, {})[session.id] = session.adapter_config.get('plugin_selection_revision', 0)
        return catalog, members

    async def eligible(self, connection):
        if self.stopping or self.connections.get(connection.plugin.id) is not connection:
            return False
        catalog, members = await io(self.desired)
        pid = connection.plugin.id
        plugin = catalog.get(pid)
        revision = digest([plugin.events, catalog.servers[plugin.events['provider']['command_ref']], plugin.data_version]) if plugin.events else None
        # Keep a connection only while at least one original member still owns it.
        return revision == connection.revision and any(
            sid in members.get(pid, {}) and members[pid][sid] == rev for sid, rev in connection.members.items())

    async def reconcile(self):
        async with self.lock:
            if self.stopping:
                return
            catalog, members = await io(self.desired)
            for pid, connection in list(self.connections.items()):
                if not connection.ready or not await self.eligible(connection):
                    del self.connections[pid]
                    await connection.close()
            for pid, owners in members.items():
                if pid in self.connections:
                    self.connections[pid].members = owners
                    continue
                plugin = catalog.get(pid)
                revision = digest([plugin.events, catalog.servers[plugin.events['provider']['command_ref']], plugin.data_version])
                connection = EventConnection(self, plugin, catalog, owners, revision)
                self.connections[pid] = connection
                try:
                    await connection.start()
                except Exception as exc:
                    self.diagnostic(pid, 'startup: ' + str(exc))
                    del self.connections[pid]
                    await connection.close()

    async def start(self):
        await self.reconcile()
        self.task = asyncio.create_task(self.watch(), name='pan-plugin-events')

    async def watch(self):
        while True:
            await asyncio.sleep(2)
            try:
                await self.reconcile()
            except Exception as exc:
                self.diagnostic('runtime', str(exc))

    async def close(self):
        self.stopping = True
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        async with self.lock:
            await asyncio.gather(*(c.close() for c in self.connections.values()))
            self.connections.clear()
