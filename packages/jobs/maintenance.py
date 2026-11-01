"""Nonexecuting diagnostics for a refused runtime owner.

Only an offline-produced disposition manifest is read. No legacy payload is
loaded, registered, converted or executed. Unknown ownership forbids deletion.
"""
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from packages.automation.auth import UserAuth
from packages.jobs import final_contracts


def blocked(state):
    return JSONResponse(status_code=503, content={'ok': False, 'error': state['error']})


def state_for(root, conflict):
    return {'mode': 'maintenance', 'ready': False,
            'error': {'code': 'runtime_ownership_unverified', 'reason': conflict.reason,
                      'message': str(conflict), 'resource': 'job-runtime-owner.sqlite3',
                      'recovery': 'Reconcile the original owner offline; no automatic takeover.'},
            'root': Path(root), 'auth': None}


def dispositions(root, *, service=None):
    path = root / 'job-dispositions.json'
    if not path.exists():
        return {'items': [], 'manifestState': 'missing', 'message': '未提供离线处置清单；原数据未迁移、未执行。'}
    if any(p.is_symlink() or (hasattr(p, 'is_junction') and p.is_junction()) for p in (path, *path.parents)):
        raise ValueError('Disposition manifest contains a link')
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError('Disposition manifest exceeds limit')
    value = final_contracts.loads(path.read_text(encoding='utf-8'))
    if (not isinstance(value, dict) or set(value) != {'schemaVersion', 'records'}
            or type(value['schemaVersion']) is not int or value['schemaVersion'] != 1
            or not isinstance(value.get('records'), list) or len(value['records']) > 10000):
        raise ValueError('Invalid disposition manifest')
    items, ids = [], set()
    for row in value['records']:
        if not isinstance(row, dict) or set(row) != {'id', 'originalId', 'sourceFile', 'sha256', 'revision', 'reasonCodes', 'executable'}:
            raise ValueError('Invalid disposition entry')
        if (not all(isinstance(row[k], str) for k in ('id', 'originalId', 'sourceFile', 'sha256'))
                or not row['id'] or row['id'] in ids or row['executable'] is not False
                or type(row['revision']) is not int or row['revision'] < 1
                or len(row['sha256']) != 64 or any(c not in '0123456789abcdef' for c in row['sha256'])
                or not isinstance(row['reasonCodes'], list) or not all(isinstance(r, str) for r in row['reasonCodes'])):
            raise ValueError('Invalid disposition identity')
        relative = PurePosixPath(row['sourceFile'])
        if relative.is_absolute() or '..' in relative.parts or '\\' in row['sourceFile'] or ':' in row['sourceFile']:
            raise ValueError('Invalid disposition source path')
        ids.add(row['id'])
        owned = service is not None and service.owner.token is not None and service.owner.lock.file is not None
        resource_blocked = bool(set(row['reasonCodes']) & {'active_resource', 'uncertain_effect', 'pending_notice', 'unverified_authority'})
        receipt = None
        if service is not None:
            with service.transaction(readonly=True) as db:
                try:
                    receipt = service._get(db, 'migration_receipt', 'preserved-delete:' + row['id'])
                except KeyError:
                    pass
        items.append({**row, 'recognition': 'unrecognized', 'deleted': receipt is not None,
                      'deleteBlocked': not owned or resource_blocked or receipt is not None,
                      'deleteReason': '已删除原记录，原字节仍保留在本地审计备份；未知效果未结算。' if receipt else
                      '原资源或收据尚未核实，原记录保留，先按原身份核对。' if owned and resource_blocked else
                      '删除仅移除原记录并保留审计备份，不停止资源、不结算或重放未知效果。' if owned else
                      '原 owner 未核实，禁止修改原文件；未知效果未结算、不会重放。'})
    return {'items': items, 'manifestState': 'loaded', 'message': '原数据已保留，未迁移；建议手动创建新的 Job。'}


def install(app):
    class MaintenanceBoundary:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            state = getattr(scope.get('app', app).state, 'job_maintenance', None)
            path = scope.get('path', '')
            if state and scope['type'] == 'websocket':
                await send({'type': 'websocket.close', 'code': 1013,
                            'reason': 'runtime_ownership_unverified'})
                return
            if (state and scope['type'] == 'http' and path.startswith('/api/')
                    and not path.startswith('/api/job-maintenance/')):
                await blocked(state)(scope, receive, send)
                return
            await self.app(scope, receive, send)

    app.add_middleware(MaintenanceBoundary)

    router = APIRouter(prefix='/api/job-maintenance')

    def current(request):
        state = getattr(request.app.state, 'job_maintenance', None)
        if state:
            return state
        service = getattr(request.app.state, 'job_final_service', None)
        if service is None:
            raise HTTPException(503, 'Job owner is unavailable')
        return {'mode': 'active', 'root': service.owner.root, 'auth': service.auth, 'service': service}

    def auth(state):
        if state['auth'] is None:
            state['auth'] = UserAuth(state['root'] / 'automation')
        return state['auth']

    def authorized(request):
        state = current(request)
        if not auth(state).authorized(request.headers.get('authorization', '')):
            raise HTTPException(401, 'User login required')
        return state

    @router.get('/status')
    async def status(request: Request):
        state = getattr(request.app.state, 'job_maintenance', None)
        return {k: state[k] for k in ('mode', 'ready', 'error')} if state else {'mode': 'active', 'ready': True}

    @router.post('/login')
    async def login(request: Request):
        state = current(request)
        try:
            data = await request.json()
            if not isinstance(data, dict) or set(data) != {'key'}:
                raise HTTPException(400, 'Invalid login request')
            return {'token': auth(state).login(data['key'])}
        except PermissionError:
            raise HTTPException(401, 'Invalid user key')

    @router.get('/records')
    async def records(request: Request):
        state = authorized(request)
        try:
            return dispositions(state['root'], service=state.get('service'))
        except (ValueError, OSError):
            return JSONResponse(status_code=409, content={'ok': False, 'error': {
                'code': 'invalid_disposition_manifest', 'message': '处置清单无效；原数据未改变、未执行。'}})

    @router.delete('/records/{record_id}')
    async def delete(record_id: str, request: Request):
        state = authorized(request)
        if state['mode'] == 'maintenance':
            return blocked(state)
        from packages.jobs.final_api import body, fields
        from packages.jobs.migration_history import delete_preserved
        try:
            value = await body(request)
            names = {'expectedRevision', 'expectedSha256', 'expectedSourceFile', 'requestKey'}
            fields(value, names, names)
            record = next((row for row in dispositions(state['root'])['items'] if row['id'] == record_id), None)
            if record is None:
                raise final_contracts.ContractError('not_found', 'Original disposition record not found')
            from packages.plugins.lifecycle import io
            return await io(delete_preserved, state['service'], record, value, actor={'type': 'user', 'id': 'local'})
        except final_contracts.ContractError as exc:
            return JSONResponse(status_code=exc.status, content={'ok': False, 'error': exc.as_dict()})
        except ValueError:
            return JSONResponse(status_code=409, content={'ok': False, 'error': {
                'code': 'invalid_disposition_manifest', 'message': '处置清单无效，未准入删除。'}})
        except OSError:
            return JSONResponse(status_code=503, content={'ok': False, 'error': {
                'code': 'storage_unavailable', 'message': '删除存储结果未确认；保留原请求身份及审计备份，查询原操作。'}})

    app.include_router(router)
