"""Core Action Panel APIs. Agent-facing routes only maintain drafts."""
import copy
import shutil

from fastapi import APIRouter, HTTPException

from packages.plugins.lifecycle import serialized, pinned
from .store import Store


def install(server):
    router = APIRouter()

    def blockers(pid):
        result = []
        for session in server.sess.list_all(load_history=False):
            bindings = server._session_plugin_fields(session)['pluginBindings']
            live = server.worker.find_alive_worker_by_session(session.id)
            if live and live.status == 'running' and any(b['enabled'] and b['plugin_id'] == pid for b in bindings):
                result.append({'id': session.id, 'name': session.name})
        return result

    def view():
        state = Store().read()
        sessions = server.sess.list_all(load_history=False)
        groups = []
        for group in state['groups'].values():
            item = copy.deepcopy(group)
            pid = 'action-group-' + group['id'].replace('_', '-')
            item['blockers'] = blockers(pid)
            item['sessions'] = []
            for session in sessions:
                binding = next((b for b in server._session_plugin_fields(session)['pluginBindings'] if b['plugin_id'] == pid), None)
                if binding:
                    frozen = (pinned(session.id)[0].get(pid)
                              if server.worker.find_alive_worker_by_session(session.id) else None)
                    item['sessions'].append({'id': session.id, 'name': session.name, **binding,
                                             'loadedVersion': frozen.version if frozen else None})
            groups.append(item)
        return {'revision': state['revision'], 'actions': list(state['actions'].values()), 'groups': groups}

    @router.get('/api/actions')
    async def listing():
        return view()

    @router.put('/api/actions/{kind}/{item_id}')
    @serialized
    async def put(kind: str, item_id: str, data: dict):
        if kind not in {'actions', 'groups'}:
            raise HTTPException(404, 'Unknown item kind')
        if set(data) - {'item', 'expectedRevision'} or not isinstance(data.get('item'), dict) or data['item'].get('id') != item_id:
            raise HTTPException(400, 'Request requires item with matching ID and optional expectedRevision')
        try:
            Store().put(kind, data['item'], data.get('expectedRevision'))
            return view()
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.delete('/api/actions/{kind}/{item_id}')
    @serialized
    async def delete(kind: str, item_id: str, expectedRevision: int | None = None):
        if kind not in {'actions', 'groups'}:
            raise HTTPException(404, 'Unknown item kind')
        try:
            Store().delete(kind, item_id, expectedRevision)
            return view()
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.post('/api/actions/groups/{group_id}/{operation}')
    @serialized
    async def release(group_id: str, operation: str, data: dict):
        if operation not in {'build', 'unbuild'} or set(data) - {'expectedRevision'}:
            raise HTTPException(400, 'Expected build or unbuild and optional expectedRevision')
        store = Store()
        group = store.read()['groups'].get(group_id)
        if not group:
            raise HTTPException(404, 'Group not found')
        pid = 'action-group-' + group_id.replace('_', '-')
        try:
            if operation == 'build':
                catalog = server._plugin_catalog()
                if pid in catalog.plugins and not group.get('build'):
                    raise ValueError('Group plugin ID conflicts with an installed plugin')
                store.build(group_id, data.get('expectedRevision'))
                # Action releases are pinned even in gateway mode; reuse the
                # normal idle/deferred restart policy to load the next release.
                for session in server.sess.list_all(load_history=False):
                    if any(b['enabled'] and b['plugin_id'] == pid for b in server._session_plugin_fields(session)['pluginBindings']):
                        if server.worker.find_alive_worker_by_session(session.id):
                            server.worker.request_delayed_restart(session.id)
            else:
                blocked = blockers(pid)
                if blocked:
                    message = f'Cannot unbuild action group "{group["name"]}": it is enabled by running sessions:\n'
                    message += '\n'.join(f'- {s["name"]}: {s["id"]}' for s in blocked)
                    message += '\nStop or disable the group for these sessions first.'
                    raise HTTPException(409, message)
                # Synchronous atomic writes while holding the generic startup gate:
                # no consumer can become running between check and unregister.
                originals = []
                try:
                    for session in server.sess.list_all(load_history=False):
                        bindings = server._session_plugin_fields(session)['pluginBindings']
                        if any(b['plugin_id'] == pid for b in bindings):
                            originals.append((session, copy.deepcopy(session.adapter_config)))
                            staged = copy.deepcopy(session.adapter_config)
                            server._set_plugin_config(staged, [b for b in bindings if b['plugin_id'] != pid])
                            session.adapter_config = staged
                            server.sess.save(session)
                    store.unbuild(group_id, data.get('expectedRevision'))
                except BaseException:
                    for session, previous in originals:
                        session.adapter_config = previous
                        server.sess.save(session)
                    raise
                # Released versions have no effective references after unregister.
                directory = store.root / 'builds' / group_id
                try:
                    if directory.exists():
                        shutil.rmtree(directory)
                except OSError as exc:
                    raise HTTPException(500, 'Group unregistered, but release file cleanup failed: ' + str(exc)) from exc
                for session, _ in originals:
                    if server.worker.find_alive_worker_by_session(session.id):
                        server.worker.request_delayed_restart(session.id)
            return view()
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.post('/api/action-drafts/{kind}/{operation}')
    @serialized
    async def drafts(kind: str, operation: str, data: dict):
        if kind not in {'actions', 'groups'} or operation not in {'put', 'delete'} or set(data) - {'item', 'id', 'expectedRevision'}:
            raise HTTPException(400, 'Invalid draft operation')
        try:
            if operation == 'put':
                Store().put(kind, data.get('item'), data.get('expectedRevision'), draft=True)
            else:
                Store().delete(kind, data.get('id'), data.get('expectedRevision'))
            return view()
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    server.app.include_router(router)
