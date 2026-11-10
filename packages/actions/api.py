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
        await require_capability(request, 'updateConfig')
        raise HTTPException(410, {'code': 'retired', 'message': 'Legacy Action/Group writes retired; use canonical APIs'})

    @router.post('/api/actions/groups/{group_id}/{operation}')
    @settled
    async def release(group_id: str, operation: str, data: dict, request: Request):
        await require_capability(request, 'lifecycle')
        if operation not in {'build', 'unbuild'} or set(data) - {'expectedRevision'}:
            raise HTTPException(400, 'Expected build or unbuild and optional expectedRevision')
        raise HTTPException(410, {'code': 'retired', 'message': 'Legacy Group build/unbuild retired; use canonical Group APIs'})

    @router.post('/api/action-drafts/{kind}/{operation}')
    async def drafts(kind: str, operation: str, data: dict, request: Request):
        await require_capability(request, 'updateConfig')
        raise HTTPException(410, {'code': 'retired', 'message': 'Legacy Action/Group writes retired; use canonical APIs'})

    server.app.include_router(router)
