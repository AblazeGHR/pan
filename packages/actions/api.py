"""Core Action APIs: resource admission, off-loop IO, durable detach/rollback."""
import copy
import shutil

from fastapi import APIRouter, HTTPException, Request

from packages.plugins.lifecycle import coordinator, io, pinned, settled
from .store import Store, PersistenceError


def install(server):
    router = APIRouter()

    async def user(request):
        svc = getattr(server.app.state, 'automation', None)
        return bool(svc and await io(svc.auth.authorized, request.headers.get('authorization','')))

    async def require_capability(request, capability):
        svc = getattr(server.app.state, 'automation', None)
        if not svc or not await io(svc.auth.authorized, request.headers.get('authorization','')):
            raise HTTPException(401, 'User login required')
        allowed, _ = await io(svc.auth.permission_allowed, 'action', capability)
        if not allowed:
            raise HTTPException(403, f'Authorization denied: action.{capability}')

    async def sessions():
        return await io(server.sess.list_all, load_history=False)

    def bindings(session):
        return server._session_plugin_fields(session)['pluginBindings']

    def blockers(pid, owners):
        result = []
        for session in owners:
            live = server.worker.find_alive_worker_by_session(session.id)
            if live and live.status == 'running' and any(b['enabled'] and b['plugin_id'] == pid for b in bindings(session)):
                result.append({'id': session.id, 'name': session.name})
        return result

    async def view():
        state, owners = await io(Store().read), await sessions()
        groups = []
        for group in state['groups'].values():
            item = copy.deepcopy(group)
            pid = 'action-group-' + group['id'].replace('_', '-')
            item['blockers'] = blockers(pid, owners)
            item['sessions'] = []
            for session in owners:
                binding = next((b for b in bindings(session) if b['plugin_id'] == pid), None)
                if binding:
                    frozen = pinned(session.id)[0].get(pid) if server.worker.find_alive_worker_by_session(session.id) else None
                    item['sessions'].append({'id': session.id, 'name': session.name, **binding,
                                             'loadedVersion': frozen.version if frozen else None})
            groups.append(item)
        return {'revision': state['revision'], 'actions': list(state['actions'].values()), 'groups': groups}

    @router.get('/api/actions/session-operation-template')
    async def session_operation_template(request: Request):
        await require_capability(request, 'read')
        from .session_query import template
        return template()

    @router.get('/api/actions')
    async def listing(request: Request):
        await require_capability(request, 'read')
        return await view()

    @router.post('/api/actions/{kind}')
    async def create(kind: str, data: dict, request: Request):
        if kind not in {'actions', 'groups'}:
            raise HTTPException(404, 'Unknown item kind')
        if set(data) - {'item', 'expectedRevision'} or not isinstance(data.get('item'), dict):
            raise HTTPException(400, 'Request requires item and optional expectedRevision')
        try:
            await require_capability(request, 'create')
            if kind == 'actions' and data['item'].get('enabled'):
                await require_capability(request, 'lifecycle')
            if kind == 'actions' and data['item'].get('system'):
                await require_capability(request, 'manageSystemActions')
            item = await io(Store().put, kind, data['item'], data.get('expectedRevision'), create=True, authorized=await user(request))
            return {**await view(), 'item': item}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(401, str(exc)) from exc
        except PersistenceError as exc:
            raise HTTPException(503,str(exc)) from exc

    @router.put('/api/actions/{kind}/{item_id}')
    async def put(kind: str, item_id: str, data: dict, request: Request):
        if kind not in {'actions', 'groups'}:
            raise HTTPException(404, 'Unknown item kind')
        if set(data) - {'item', 'expectedRevision'} or not isinstance(data.get('item'), dict) or data['item'].get('id') != item_id:
            raise HTTPException(400, 'Request requires item with matching ID and optional expectedRevision')
        try:
            await require_capability(request, 'updateConfig')
            if kind == 'actions':
                current = await io(Store().read)
                old = current['actions'].get(item_id)
                if old and old.get('enabled', False) != data['item'].get('enabled', False):
                    await require_capability(request, 'lifecycle')
            if kind == 'actions' and data['item'].get('system'):
                await require_capability(request, 'manageSystemActions')
            await io(Store().put, kind, data['item'], data.get('expectedRevision'), authorized=await user(request))
            return await view()
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(401, str(exc)) from exc
        except PersistenceError as exc:
            raise HTTPException(503,str(exc)) from exc

    @router.delete('/api/actions/{kind}/{item_id}')
    async def delete(kind: str, item_id: str, request: Request, expectedRevision: int | None = None, confirmReferences: bool = False):
        if kind not in {'actions', 'groups'}:
            raise HTTPException(404, 'Unknown item kind')
        try:
            await require_capability(request, 'delete')
            await io(Store().delete, kind, item_id, expectedRevision, authorized=await user(request), confirm_references=confirmReferences)
            return await view()
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(401, str(exc)) from exc
        except PersistenceError as exc:
            raise HTTPException(503,str(exc)) from exc

    @router.post('/api/actions/groups/{group_id}/{operation}')
    @settled
    async def release(group_id: str, operation: str, data: dict, request: Request):
        if operation not in {'build', 'unbuild'} or set(data) - {'expectedRevision'}:
            raise HTTPException(400, 'Expected build or unbuild and optional expectedRevision')
        pid = 'action-group-' + group_id.replace('_', '-')
        try:
            await require_capability(request, 'lifecycle')
            # Group first; this closes new-reference admission before collecting
            # Sessions. No mutex or store writer lock is held while we await IO.
            async with coordinator().claim(exclusive={pid}):
                store = Store()
                state = await io(store.read)
                group = state['groups'].get(group_id)
                if not group:
                    raise HTTPException(404, 'Group not found')
                if operation == 'build':
                    catalog = await io(server._plugin_catalog)
                    if pid in catalog.plugins and not group.get('build'):
                        raise ValueError('Group plugin ID conflicts with an installed plugin')
                    await io(store.build, group_id, data.get('expectedRevision'))
                    for session in await sessions():
                        if any(b['enabled'] and b['plugin_id'] == pid for b in bindings(session)):
                            if server.worker.find_alive_worker_by_session(session.id):
                                server.worker.request_delayed_restart(session.id)
                else:
                    owners = [s for s in await sessions() if any(b['plugin_id'] == pid for b in bindings(s))]
                    async with coordinator().claim(exclusive={'session:' + s.id for s in owners}):
                        blocked = blockers(pid, owners)
                        if blocked:
                            message = f'Cannot unbuild action group "{group["name"]}": it is enabled by running sessions:\n'
                            message += '\n'.join(f'- {s["name"]}: {s["id"]}' for s in blocked)
                            raise HTTPException(409, message + '\nStop or disable the group for these sessions first.')
                        originals = []
                        try:
                            for session in owners:
                                previous = copy.deepcopy(session.adapter_config)
                                staged = copy.deepcopy(previous)
                                await io(server._set_plugin_config, staged, [b for b in bindings(session) if b['plugin_id'] != pid])
                                originals.append((session, previous))
                                await server.sess.commit_adapter_config_async(session, staged, previous_config=previous)
                            await io(store.unbuild, group_id, data.get('expectedRevision'))
                        except BaseException as failure:
                            errors = []
                            for session, previous in originals:
                                session.adapter_config = previous
                            for session, previous in originals:
                                try:
                                    await server.sess.commit_adapter_config_async(session, previous)
                                except Exception as exc:
                                    errors.append(f'{session.id}: {exc}')
                            if errors:
                                raise HTTPException(500, 'Unbuild failed; registration retained, but Session rollback persistence failed: '
                                                    + '; '.join(errors)) from failure
                            raise
                        # Publish retirement before releasing Session admission,
                        # even if physical cleanup later fails. Old loaded pins
                        # can never accept another task on this Worker.
                        for session in owners:
                            if server.worker.find_alive_worker_by_session(session.id):
                                server.worker.request_delayed_restart(session.id)
                        def cleanup():
                            directory = store.root / 'builds' / group_id
                            if directory.exists():
                                shutil.rmtree(directory)
                        try:
                            await io(cleanup)
                        except OSError as exc:
                            raise HTTPException(500, 'Group unregistered, but release file cleanup failed: ' + str(exc)) from exc
            return await view()
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.post('/api/action-drafts/{kind}/{operation}')
    async def drafts(kind: str, operation: str, data: dict, request: Request):
        if kind not in {'actions', 'groups'} or operation not in {'put', 'delete'} or set(data) - {'item', 'id', 'expectedRevision'}:
            raise HTTPException(400, 'Invalid draft operation')
        try:
            await require_capability(request, 'create' if operation == 'put' else 'delete')
            if operation == 'put':
                item = await io(Store().put, kind, data.get('item'), data.get('expectedRevision'), draft=True)
                return {**await view(), 'item': item}
            else:
                await io(Store().delete, kind, data.get('id'), data.get('expectedRevision'))
            return await view()
        except PermissionError as exc:
            raise HTTPException(401, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    server.app.include_router(router)
