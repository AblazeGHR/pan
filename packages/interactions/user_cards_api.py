"""Authenticated user APIs and the existing loopback stdio owner boundary."""
from fastapi import HTTPException, Request
from .contracts import InteractionError
from .user_cards import get_store, ReminderScheduler


def scheduler(app):
    if getattr(app.state,'user_card_scheduler',None) is None:
        app.state.user_card_scheduler=ReminderScheduler(get_store())
    return app.state.user_card_scheduler


def register(app, user):
    from .api import failure

    @app.post('/api/reminding-cards')
    async def create(data:dict,request:Request):
        principal=await user(request)
        from packages.core import session
        from packages.web import server
        from .locker_messages import _validate_links
        try:
            from .contracts import validate
            validate('Id',data.get('ownerSessionId'))
            source=session.get(data.get('ownerSessionId'),load_history=False)
            if not source:
                raise InteractionError('not_found','Owner Session unavailable')
            # Validate the entire document before inspecting attachment fields.
            # Store admission is performed only after registry ownership checks.
            if not isinstance(data.get('attachments'),list):
                raise InteractionError('invalid','attachments required')
            if not isinstance(data.get('body'),str):
                raise InteractionError('invalid','body required')
            _validate_links(server,source.id,data['body'])
            for a in data['attachments']:
                if not isinstance(a,dict) or not isinstance(a.get('id'),str):
                    raise InteractionError('invalid','Invalid attachment')
                error=server._attachment_id_error(source.id,a['id'])
                if error:
                    raise InteractionError('forbidden',error['code'])
            run=scheduler(app)
            async with run.lock:
                card=run.store.create(principal,data)
                run.store.store.remember_source(source.id,principal.id,source.name,session.effective_workspace_ids(source))
                await run.tick()
                run.changed.set()
            return {'card':card}
        except InteractionError as exc:
            raise failure(exc) from exc

    @app.get('/api/reminding-cards')
    async def cards(request:Request):
        return {'cards':get_store().list(principal=await user(request))}

    @app.get('/api/reminding-cards/settings')
    async def settings(request:Request):
        await user(request)
        return get_store().settings()

    @app.put('/api/reminding-cards/settings')
    async def save_settings(data:dict,request:Request):
        await user(request)
        try:
            if set(data)!={'minutes'} or data['minutes'] is None:
                raise InteractionError('invalid','minutes required')
            result=get_store().settings(data['minutes'])
            scheduler(app).changed.set()
            return result
        except InteractionError as exc:
            raise failure(exc) from exc

    @app.delete('/api/reminding-cards/{card_id}')
    async def delete(card_id:str,request:Request):
        principal=await user(request)
        try:
            run=scheduler(app)
            async with run.lock:
                result=run.store.delete(principal,card_id)
                run.changed.set()
            return result
        except InteractionError as exc:
            raise failure(exc) from exc

    @app.put('/api/reminding-cards/{card_id}/read')
    async def read(card_id:str,data:dict,request:Request):
        principal=await user(request)
        try:
            if set(data)!={'read'}:
                raise InteractionError('invalid','read required')
            return {'card':get_store().read(principal,card_id,data['read'])}
        except InteractionError as exc:
            raise failure(exc) from exc

    @app.get('/api/reminding-cards/{card_id}/attachments/{attachment_id}')
    async def attachment(card_id:str,attachment_id:str,request:Request):
        principal=await user(request)
        try:
            store=get_store()
            with store.db() as db:
                card=store.view(store.get(db,card_id,principal=principal))
            if attachment_id not in {a['id'] for a in card['attachments']}:
                raise InteractionError('not_found','Attachment is not part of this card')
            from packages.web import server
            error=server._attachment_id_error(card['ownerSessionId'],attachment_id)
            if error:
                raise InteractionError('not_found',error['code'])
            record=server._attachment_record(card['ownerSessionId'],attachment_id)
            from starlette.responses import FileResponse
            return FileResponse(record['path'],filename=record.get('displayName') or 'attachment',
                                media_type=record.get('mimeType') or 'application/octet-stream')
        except InteractionError as exc:
            raise failure(exc) from exc

    @app.post('/api/reminding-card-tools/{operation}')
    async def tool(operation:str,data:dict,request:Request):
        from packages.web.terminal_tools_api import _local, resolve_caller
        sid=request.headers.get('X-Pan-Queue-Caller')
        caller=resolve_caller(sid) if sid and _local(request) else None
        if not caller:
            raise HTTPException(403,detail={'code':'caller_unavailable'})
        try:
            if operation=='list_pending' and not data:
                return {'cards':get_store().list(owner=caller['id'])}
            if operation!='answer':
                raise InteractionError('invalid','Use list_pending (no parameters) or answer')
            run=scheduler(app)
            async with run.lock:
                result=run.store.answer(caller['id'],data)
                run.changed.set()
            return {'card':result}
        except InteractionError as exc:
            raise failure(exc) from exc
