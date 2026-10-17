"""Manifest-contributed views; actions use the existing capability providers."""
import asyncio
from contextlib import asynccontextmanager
import json
import os
import re
from types import SimpleNamespace

from .catalog import Catalog, digest
from .data import plugin_data_dir
from .runtime import Runtime, error


class PanelHost:
    def __init__(self, get_manifest, data_root):
        self.get_manifest = get_manifest
        self.data_root = data_root
        self.sessions = {}
        self.runtime = Runtime(get_manifest, self.sessions.get, data_root=data_root, audience="panel")
        self.locks = {}
        self.tasks = set()
        self.closing = False

    def panels(self):
        return [{**p, "pluginId": plugin.id, "path": f"/plugins/{plugin.id}/{p['id']}"}
                for plugin in Catalog(self.get_manifest()).plugins.values() for p in plugin.panels]

    async def invoke(self, plugin_id, panel_id, action, arguments, request_id=None):
        if self.closing:
            raise ValueError("Panel host is stopping")
        if not isinstance(arguments, dict) or len(json.dumps(arguments)) > 32768:
            raise ValueError("Invalid panel arguments")
        plugin = Catalog(self.get_manifest()).get(plugin_id)
        panel = next((p for p in plugin.panels if p['id'] == panel_id), None)
        if not panel or action not in panel['actions']:
            raise ValueError("Unknown panel action")
        spec = panel['actions'][action]
        writing = spec['effect'] == 'write_external'
        if writing and (not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,80}', request_id)):
            raise ValueError("External changes require a stable requestId")
        task = asyncio.create_task(self._execute(plugin, panel_id, spec, arguments, request_id if writing else None))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
        return await asyncio.shield(task)

    async def _execute(self, plugin, panel_id, spec, arguments, request_id):
        async with self.locks.setdefault(plugin.id, asyncio.Lock()):
            data = plugin_data_dir(self.data_root, plugin.id, plugin.data_version)
            journal = data / 'panel-requests.json'
            ledger = json.loads(journal.read_text(encoding='utf-8')) if journal.exists() else {}
            signature = digest([panel_id, spec, arguments])
            if request_id:
                previous = ledger.get(request_id)
                if previous:
                    if previous['signature'] != signature:
                        raise ValueError("requestId was already used for another operation")
                    return previous.get('result') or error('execution_unconfirmed', 'Previous change has no confirmed outcome; refresh state before any new change')
                if len(ledger) >= 128:
                    completed = next((key for key, value in ledger.items() if 'result' in value and not value['result'].get('isError')), None)
                    if completed is None:
                        raise ValueError('Too many unconfirmed changes; reconcile stored operations first')
                    del ledger[completed]
                ledger[request_id] = {'signature': signature}
                self._write(journal, ledger)
            sid = 'panel_' + plugin.id
            mode = 'gateway' if 'gateway' in plugin.supported_modes else plugin.default_mode
            view = {'gateway': 'gateway', 'standalone': 'standalone', 'pan_native': 'native'}[mode]
            self.sessions[sid] = SimpleNamespace(id=sid, name=plugin.display_name, adapter_config={
                'plugin_bindings': [{'plugin_id': plugin.id, 'enabled': True, 'mode': mode}]})
            try:
                name = spec['capability']
                if view == 'native':
                    descriptors = await self.runtime.directory(sid, view)
                    name = next(d['native_name'] for d in descriptors if d['name'] == name)
                # No outer timeout: provider deadlines return uncertainty, and close drains admitted work.
                result = await self.runtime.handle({'session_id': sid, 'operation': 'call', 'view': view,
                    'name': name, 'arguments': arguments})
            except Exception:
                result = error('execution_unconfirmed', 'Operation did not return a confirmed result; refresh state')
            if request_id:
                ledger[request_id]['result'] = result
                self._write(journal, ledger)
            return result

    @staticmethod
    def _write(path, value):
        temporary = path.with_suffix('.tmp')
        with temporary.open('w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)

    async def close(self):
        self.closing = True
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.runtime.close()


@asynccontextmanager
async def panel_lifespan(app, get_manifest, data_root):
    host = PanelHost(get_manifest, data_root)
    app.state.plugin_panels = host
    try:
        yield host
    finally:
        await host.close()
        app.state.plugin_panels = None
