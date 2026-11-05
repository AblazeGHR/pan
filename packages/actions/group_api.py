"""Group HTTP adapter; authorization and receipts reuse the canonical boundary."""
from fastapi import APIRouter, Request

from packages.jobs.final_api import ContractRoute, actor, body, fields, reject
from packages.plugins.lifecycle import coordinator, io, settled
from .groups import Groups

router = APIRouter(route_class=ContractRoute)


def groups(request):
    service = getattr(request.app.state, "job_final_service", None)
    if service is None:
        reject("provider_unavailable", "Canonical service unavailable")
    return Groups(service)


@router.get('/action-groups')
async def listing(request: Request):
    who = await actor(request, 'action', 'read')
    return await io(groups(request).list, actor=who)


@router.put('/action-groups/{group_id}')
async def put(group_id: str, request: Request):
    who = await actor(request)
    value = await body(request)
    fields(value, {'group', 'expectedRevision', 'requestKey'}, {'group', 'expectedRevision', 'requestKey'})
    if not isinstance(value['group'], dict) or value['group'].get('id') != group_id:
        reject('invalid_schema', 'Group ID mismatch')
    return await io(groups(request).put, value['group'], value['expectedRevision'], value['requestKey'], actor=who)


@router.post('/action-groups/preview-reference-update')
async def preview(request: Request):
    who = await actor(request, 'action', 'read')
    return await io(groups(request).preview, await body(request), actor=who)


@router.delete('/action-groups/{group_id}')
async def delete(group_id: str, request: Request):
    who = await actor(request, 'action', 'delete')
    value = await body(request)
    fields(value, {'expectedRevision', 'requestKey'}, {'expectedRevision', 'requestKey'})
    return await io(groups(request).delete, group_id, value['expectedRevision'], value['requestKey'], actor=who)


@router.post('/action-groups/apply-reference-update')
async def apply(request: Request):
    who = await actor(request, 'action', 'updateConfig')
    return await io(groups(request).apply, await body(request), actor=who)


@router.get('/action-groups/releases/{version}')
async def release(version: str, request: Request):
    who = await actor(request, 'action', 'read')
    return await io(groups(request).release, version, actor=who)


@router.post('/action-groups/{group_id}/build')
@settled
async def build(group_id: str, request: Request):
    who = await actor(request, 'action', 'lifecycle')
    value = await body(request)
    fields(value, {'expectedRevision', 'requestKey'}, {'expectedRevision', 'requestKey'})
    pid = 'action-group-' + group_id.replace('_', '-')
    async with coordinator().claim(exclusive={pid}):
        result = await io(groups(request).publish, group_id, value['expectedRevision'], value['requestKey'], actor=who)
        if result.get('replayed'):
            return result
        # Keep the existing idle/deferred restart protocol. Pins already loaded
        # by running Workers continue to reference immutable release files.
        from packages.core import worker, session
        for owner in await io(session.list_all, load_history=False):
            bindings = owner.adapter_config.get('plugin_bindings', [])
            if any(b.get('enabled') and b.get('plugin_id') == pid for b in bindings):
                if worker.find_alive_worker_by_session(owner.id):
                    worker.request_delayed_restart(owner.id)
        return result


@router.post('/action-groups/{group_id}/unbuild')
@settled
async def unbuild(group_id: str, request: Request):
    who = await actor(request, 'action', 'lifecycle')
    value = await body(request)
    fields(value, {'expectedRevision', 'requestKey'}, {'expectedRevision', 'requestKey'})
    adapter = groups(request)
    try:
        await io(adapter.service.operation_for_key, 'disable', value['requestKey'], actor=who)
    except KeyError:
        pass
    else:
        # Check the original fingerprint without repeating binding cleanup or
        # delayed restarts after an ambiguous response.
        return await io(adapter.unbuild, group_id, value['expectedRevision'], value['requestKey'], actor=who)
    from packages.core import worker, session
    from packages.web import server
    import copy
    pid = 'action-group-' + group_id.replace('_', '-')
    async with coordinator().claim(exclusive={pid}):
        owners = [s for s in await io(session.list_all, load_history=False)
            if any(b.get('plugin_id') == pid for b in s.adapter_config.get('plugin_bindings', []))]
        async with coordinator().claim(exclusive={'session:' + s.id for s in owners}):
            for owner in owners:
                live = worker.find_alive_worker_by_session(owner.id)
                if live and live.status == 'running' and any(b.get('enabled') and b.get('plugin_id') == pid
                        for b in owner.adapter_config.get('plugin_bindings', [])):
                    reject('reference_conflict', 'Group is enabled by a running Session')
            originals = []
            try:
                for owner in owners:
                    previous = copy.deepcopy(owner.adapter_config)
                    staged = copy.deepcopy(previous)
                    await io(server._set_plugin_config, staged,
                        [b for b in staged.get('plugin_bindings', []) if b.get('plugin_id') != pid])
                    originals.append((owner, previous))
                    await session.commit_adapter_config_async(owner, staged, previous_config=previous)
                result = await io(groups(request).unbuild, group_id, value['expectedRevision'], value['requestKey'], actor=who)
            except BaseException as failure:
                errors = []
                for owner, previous in originals:
                    owner.adapter_config = previous
                for owner, previous in originals:
                    try:
                        await session.commit_adapter_config_async(owner, previous)
                    except Exception:
                        errors.append(owner.id)
                if errors:
                    reject('storage_unavailable', 'Group retained; Session rollback could not be confirmed')
                raise
            for owner in owners:
                if worker.find_alive_worker_by_session(owner.id):
                    worker.request_delayed_restart(owner.id)
            return result
