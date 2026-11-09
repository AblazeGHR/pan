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
        # This is a read projection. Keep malformed source payloads inspectable
        # without making them executable or rewriting their original bytes.
        for kind in ('actions', 'groups'):
            projected = {}
            for source_id, source in state.get(kind, {}).items():
                raw = source if isinstance(source, dict) else {}
                item = {**raw, 'id': source_id,
                        'name': raw.get('name') if isinstance(raw.get('name'), str) else source_id,
                        'retirementStatus': 'Retired' if raw else 'Invalid',
                        'preservedSource': copy.deepcopy(source)}
                if kind == 'actions':
                    item.update(description=raw.get('description') if isinstance(raw.get('description'), str) else '',
                                execution=raw.get('execution') if isinstance(raw.get('execution'), dict) else {'kind': 'invalid'})
                else:
                    item['action_ids'] = [value for value in raw.get('action_ids', []) if isinstance(value, str)] if isinstance(raw.get('action_ids'), list) else []
                projected[source_id] = item
            state[kind] = projected
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
        await require_capability(request, 'create')
        raise HTTPException(410, {'code': 'retired', 'message': 'Legacy Action/Group writes retired; use canonical APIs'})

    @router.put('/api/actions/{kind}/{item_id}')
    async def put(kind: str, item_id: str, data: dict, request: Request):
        await require_capability(request, 'updateConfig')
        raise HTTPException(410, {'code': 'retired', 'message': 'Legacy Action/Group writes retired; use canonical APIs'})

    @router.post('/api/actions/actions/{item_id}/lifecycle')
    async def lifecycle(item_id: str, data: dict, request: Request):
        await require_capability(request, 'updateConfig')
        raise HTTPException(410, {'code': 'retired', 'message': 'Legacy Action/Group writes retired; use canonical APIs'})

    @router.delete('/api/actions/{kind}/{item_id}')
    async def delete(kind: str, item_id: str, request: Request, expectedRevision: int | None = None, confirmReferences: bool = False):
        await require_capability(request, 'delete')
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
        await require_capability(request, 'lifecycle')
        if operation == 'build':
            raise HTTPException(410, {'code': 'retired', 'message': 'Legacy Group build retired; use canonical Group build'})
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
                        # Preserve legacy build files for read-only audit.

            return await view(request)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.post('/api/action-drafts/{kind}/{operation}')
    async def drafts(kind: str, operation: str, data: dict, request: Request):
        await require_capability(request, 'updateConfig')
        raise HTTPException(410, {'code': 'retired', 'message': 'Legacy Action/Group writes retired; use canonical APIs'})

    server.app.include_router(router)
