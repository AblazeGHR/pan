"""Core Action APIs: resource admission, off-loop IO, durable detach/rollback."""
import copy
import shutil
import uuid

from fastapi import APIRouter, HTTPException, Request

from packages.plugins.lifecycle import coordinator, io, pinned, settled
from .store import Store, PersistenceError, validate_action
from packages.automation.authorization import require, require_write, delegated


def install(server):
    router = APIRouter()

    def compatibility():
        service = getattr(server.app.state, 'job_final_service', None)
        if service is None:
            return None
        from .group_migration import LegacyGroupCompatibility
        return LegacyGroupCompatibility(service, Store().root)

    def request_key(request):
        return request.headers['x-pan-request-key'] if 'x-pan-request-key' in request.headers else 'legacy-http-' + uuid.uuid4().hex

    async def compatible_write(request, gid, operation, expected, raw=None, create=False, key=None):
        adapter = compatibility()
        if adapter is None or not await io(adapter.active) or (operation != 'put' and not await io(adapter.owned, gid)):
            return None
        from packages.jobs.final_api import actor
        who = await actor(request)
        return await io(adapter.mutate, gid, operation, expected, key or request_key(request), actor=who, raw=raw, create=create)

    async def compatible_action(request, operation, expected, *, raw=None, aid=None, draft=False, create=False,
                                enabled=None, confirm_references=False, key=None):
        adapter = compatibility()
        if adapter is None or not await io(adapter.active):
            return None
        from packages.jobs.final_api import actor
        who = await actor(request)
        key = key or request_key(request)
        if operation == 'put':
            return await io(adapter.put_action, raw, expected, key, actor=who, draft=draft, create=create)
        if operation == 'lifecycle':
            return await io(adapter.lifecycle_action, aid, enabled, expected, key, actor=who)
        return await io(adapter.delete_action, aid, expected, key, actor=who, confirm_references=confirm_references)

    async def current_state():
        state = await io(Store().read)
        adapter = compatibility()
        return await io(adapter.projection, state) if adapter else state

    async def user(request):
        if delegated(request):
            return True
        svc = getattr(server.app.state, 'automation', None)
        return bool(svc and await io(svc.auth.authorized, request.headers.get('authorization','')))

    async def require_capability(request, capability):
        return await require(request, 'action', capability)

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

    async def view(request):
        state, owners = await io(Store().read), await sessions()
        adapter = compatibility()
        if adapter:
            state = await io(adapter.projection, state)
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
        try:
            await require_capability(request, 'read')
            readable=True
        except HTTPException as exc:
            if exc.status_code not in (401,403,503):raise
            readable=False
        return {'revision': state['revision'], 'actions': list(state['actions'].values()) if readable else [], 'groups': groups,
                **({'canonicalActions': state['canonicalActions']} if readable and state.get('canonicalActions') else {}),
                **({'preservedActions': state['preservedActions']} if readable and state.get('preservedActions') else {})}

    @router.get('/api/actions/session-operation-template')
    async def session_operation_template(request: Request):
        await require_capability(request, 'read')
        from .session_query import template
        return template()

    @router.get('/api/actions')
    async def listing(request: Request):
        await require_capability(request, 'read')
        return await view(request)

    @router.post('/api/actions/{kind}')
    async def create(kind: str, data: dict, request: Request):
        if kind not in {'actions', 'groups'}:
            raise HTTPException(404, 'Unknown item kind')
        if set(data) - {'item', 'expectedRevision'} or not isinstance(data.get('item'), dict):
            raise HTTPException(400, 'Request requires item and optional expectedRevision')
        try:
            if kind == 'actions':
                await require_write(request, 'action', None, data['item'])
                result = await compatible_action(request, 'put', data.get('expectedRevision'), raw=data['item'], create=True)
            else:
                result = await compatible_write(request, data['item'].get('id'), 'put', data.get('expectedRevision'), data['item'], create=True)
            if result:
                return {**await view(request), 'item': result['result']['item'], 'operation': result}
            item = await io(Store().put, kind, data['item'], data.get('expectedRevision'), create=True, authorized=await user(request))
            return {**await view(request), 'item': item}
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
            if kind == 'groups' and await compatible_write(request, item_id, 'put', data.get('expectedRevision'), data['item']):
                return await view(request)
            if kind == 'actions':
                current = await current_state()
                await require_write(request, 'action', current['actions'].get(item_id), validate_action(data['item']))
                if await compatible_action(request, 'put', data.get('expectedRevision'), raw=data['item']):
                    return await view(request)
            await io(Store().put, kind, data['item'], data.get('expectedRevision'), authorized=await user(request))
            return await view(request)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(401, str(exc)) from exc
        except PersistenceError as exc:
            raise HTTPException(503,str(exc)) from exc

    @router.post('/api/actions/actions/{item_id}/lifecycle')
    async def lifecycle(item_id: str, data: dict, request: Request):
        await require_capability(request, 'lifecycle')
        if set(data) != {'enabled', 'expectedRevision'} or type(data['enabled']) is not bool:
            raise HTTPException(400, 'Expected enabled and expectedRevision')
        state = await current_state()
        item = state['actions'].get(item_id)
        if not item:
            raise HTTPException(404, 'Action missing')
        if item.get('system'):
            await require_capability(request, 'manageSystemActions')
        try:
            if await compatible_action(request, 'lifecycle', data['expectedRevision'], aid=item_id, enabled=data['enabled']):
                return await view(request)
            await io(Store().set_enabled, item_id, data['enabled'], data['expectedRevision'])
            return await view(request)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.delete('/api/actions/{kind}/{item_id}')
    async def delete(kind: str, item_id: str, request: Request, expectedRevision: int | None = None, confirmReferences: bool = False):
        if kind not in {'actions', 'groups'}:
            raise HTTPException(404, 'Unknown item kind')
        try:
            if kind == 'groups' and await compatible_write(request, item_id, 'delete', expectedRevision):
                return await view(request)
            if kind == 'actions':
                await require_capability(request, 'delete')
                if await compatible_action(request, 'delete', expectedRevision, aid=item_id, confirm_references=confirmReferences):
                    return await view(request)
            await io(Store().delete, kind, item_id, expectedRevision, authorized=await user(request), confirm_references=confirmReferences)
            return await view(request)
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
            # Group first; this closes new-reference admission before collecting
            # Sessions. No mutex or store writer lock is held while we await IO.
            async with coordinator().claim(exclusive={pid}):
                adapter = compatibility()
                if adapter and await io(adapter.owned, group_id):
                    if operation == 'build':
                        receipt = await compatible_write(request, group_id, operation, data.get('expectedRevision'))
                        if not receipt.get('replayed'):
                            for session in await sessions():
                                if any(b['enabled'] and b['plugin_id'] == pid for b in bindings(session)):
                                    if server.worker.find_alive_worker_by_session(session.id):
                                        server.worker.request_delayed_restart(session.id)
                    else:
                        from types import SimpleNamespace
                        from packages.jobs.final_api import actor
                        from .group_api import unbuild_operation
                        who = await actor(request, 'action', 'lifecycle')
                        def unbuild(gid, expected, key, *, actor):
                            return adapter.mutate(gid, 'unbuild', expected, key, actor=actor)
                        await unbuild_operation(group_id, {'expectedRevision': data.get('expectedRevision'),
                            'requestKey': request_key(request)}, who,
                            SimpleNamespace(service=adapter.service, unbuild=unbuild))
                    return await view(request)
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
            return await view(request)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.post('/api/action-drafts/{kind}/{operation}')
    async def drafts(kind: str, operation: str, data: dict, request: Request):
        if kind not in {'actions', 'groups'} or operation not in {'put', 'delete'} or set(data) - {'item', 'id', 'expectedRevision', 'requestKey'}:
            raise HTTPException(400, 'Invalid draft operation')
        try:
            if 'requestKey' in data:
                from packages.jobs.final_contracts import check_request_key
                check_request_key(data['requestKey'])
            if kind == 'groups':
                gid = (data.get('item') or {}).get('id') if operation == 'put' else data.get('id')
                current = await current_state()
                if operation == 'put':
                    await require_write(request, 'action', current['groups'].get(gid), data.get('item'))
                else:
                    await require_capability(request, 'delete')
                receipt = await compatible_write(request, gid, operation, data.get('expectedRevision'), data.get('item'), key=data.get('requestKey'))
                if receipt:
                    result = await view(request)
                    if operation == 'put':
                        result['item'] = next(g for g in result['groups'] if g['id'] == receipt['result']['group']['id'])
                    result['operation'] = receipt
                    return result
            if kind == 'actions':
                current = await current_state()
                if operation == 'put':
                    raw = data.get('item') or {}
                    old = current['actions'].get(raw.get('id'))
                    await require_write(request, 'action', old, {**raw, 'enabled': False})
                else:
                    await require_capability(request, 'delete')
                receipt = await compatible_action(request, operation, data.get('expectedRevision'),
                    raw=data.get('item'), aid=data.get('id'), draft=True, key=data.get('requestKey'))
                if receipt:
                    result = await view(request)
                    if operation == 'put':
                        result['item'] = receipt['result']['item']
                    result['operation'] = receipt
                    return result
            if operation == 'put':
                item = await io(Store().put, kind, data.get('item'), data.get('expectedRevision'), draft=True)
                return {**await view(request), 'item': item}
            else:
                await io(Store().delete, kind, data.get('id'), data.get('expectedRevision'))
            return await view(request)
        except PermissionError as exc:
            raise HTTPException(401, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    server.app.include_router(router)
