"""User-only response endpoints and Session-owned producer endpoints.

Uses the host's existing Automation UserAuth; an explicit verifier may override
it for other trusted hosts. No independent login or client-supplied identity.
"""
import inspect
from fastapi import HTTPException, Request
from packages.core import session
from .contracts import InteractionError, SCHEMA
from .store import InteractionStore, UserPrincipal


def get_store():
    return InteractionStore(session.SESSION_DIR.parent / 'user_inbox' / 'cards.sqlite')


def failure(exc):
    status={'unauthorized':401,'forbidden':403,'not_found':404,'unavailable':503,
            'invalid':422,'incomplete':409,'stale':409,'capacity':409,'conflict':409}.get(exc.code,409)
    return HTTPException(status,detail={'code':exc.code,'message':str(exc)})


def register(app):
    async def user(request):
        verifier=getattr(app.state,'user_inbox_principal',None)
        if verifier is None:
            from .runtime import principal as local_principal
            if getattr(app.state, 'automation', None) is None:
                raise HTTPException(503,detail={'code':'user_auth_unavailable'})
            verifier=lambda request: local_principal(app, request)
        try:
            principal=verifier(request)
            if inspect.isawaitable(principal):
                principal=await principal
        except Exception as exc:
            raise HTTPException(401,detail={'code':'unauthorized'}) from exc
        if not isinstance(principal,UserPrincipal):
            raise HTTPException(401,detail={'code':'unauthorized'})
        return principal

    def owner(owner_id):
        if not isinstance(owner_id,str) or not session.get(owner_id,load_history=False):
            raise HTTPException(404,detail={'code':'owner_not_found'})

    @app.get('/api/interaction-cards/schema')
    async def schema():
        return SCHEMA

    @app.post('/api/interaction-card-tools/{operation}')
    async def card_producer(operation: str, data: dict, request: Request):
        from packages.web.terminal_tools_api import _local, resolve_caller, check_access
        caller_id = request.headers.get('X-Pan-Queue-Caller')
        caller = resolve_caller(caller_id) if caller_id and _local(request) else None
        if not caller:
            raise HTTPException(403, detail={'code': 'caller_unavailable'})
        try:
            from .contracts import validate
            validate('CardProducer', {**data, 'operation': operation})
            if 'operation' in data:
                raise InteractionError('invalid', 'operation belongs in the route')
            store = get_store()
            if operation == 'list_pending':
                return store.list_pending_for_producer(caller['id'], limit=data.get('limit', 100), after=data.get('after'))
            if operation == 'no_answer_needed':
                return {'card': store.no_answer_needed(caller['id'], data['cardId'], data['expectedRevision'])}
            owner_id = data.get('ownerSessionId', caller['id'])
            if not check_access(caller, owner_id):
                raise InteractionError('forbidden', 'Owner is outside caller scope')
            from .trusted import create as create_trusted
            import asyncio
            payload = {**data, 'ownerSessionId': owner_id, 'producerSessionId': caller['id']}
            card = await asyncio.to_thread(create_trusted, getattr(app.state, 'automation', None), store,
                                          payload, producer_id=caller['id'])
            source = session.get(owner_id, load_history=False)
            store.remember_source(owner_id, data['recipientId'], source.name, session.effective_workspace_ids(source))
            return {'card': card}
        except InteractionError as exc:
            raise failure(exc) from exc

    @app.post('/api/interaction-cards')
    async def create(data:dict):
        owner(data.get('ownerSessionId'))
        try:
            from .contracts import validate
            validate('Create',data)
            from .trusted import create as create_trusted
            import asyncio
            store=get_store()
            # Repeating a create must not rebind a frozen Card to newer definitions.
            from .contracts import identity
            cid=identity('card_',[data['ownerSessionId'],data['requestKey']])
            with store.db() as db:
                exists=db.execute('SELECT 1 FROM cards WHERE id=?',(cid,)).fetchone()
            if exists:
                return {'card':store.create(data)}
            card=await asyncio.to_thread(create_trusted,getattr(app.state,'automation',None),store,data)
            source=session.get(data['ownerSessionId'],load_history=False)
            if source:
                store.remember_source(source.id,data['recipientId'],source.name,session.effective_workspace_ids(source))
            return {'card':card}
        except InteractionError as exc:
            raise failure(exc) from exc

    @app.get('/api/interaction-cards/{card_id}')
    async def producer_get(card_id:str,ownerSessionId:str):
        owner(ownerSessionId)
        try:
            return {'card':get_store().get_for_owner(ownerSessionId,card_id)}
        except InteractionError as exc:
            raise failure(exc) from exc

    @app.get('/api/interaction-cards/{card_id}/receipt')
    async def producer_receipt(card_id:str,ownerSessionId:str):
        owner(ownerSessionId)
        try:
            return {'receipt':get_store().receipt_for_owner(ownerSessionId,card_id)}
        except InteractionError as exc:
            raise failure(exc) from exc

    @app.post('/api/locker-message-tools/{operation}')
    async def locker_message_producer(operation: str, data: dict, request: Request):
        # Same-user loopback assertion, identical to the existing stdio bridge.
        # This is not remote authentication; browser and SSE forwarding fail closed.
        from packages.web.terminal_tools_api import _local, resolve_caller
        caller_id = request.headers.get('X-Pan-Queue-Caller')
        caller = resolve_caller(caller_id) if caller_id and _local(request) else None
        if not caller:
            raise HTTPException(403, detail={'code': 'caller_unavailable'})
        from .contracts import validate, identity
        from .runtime import LOCAL_RECIPIENT
        try:
            validate('LockerMessageProducer', {**data, 'operation': operation})
            if 'operation' in data:
                raise InteractionError('invalid', 'operation belongs in the route')
            if operation == 'create':
                from .locker_messages import accept_locker_message
                return {'message': accept_locker_message(caller['id'], LOCAL_RECIPIENT, data['message'], agent_producer=True)}
            mid = identity('lmsg_', [LOCAL_RECIPIENT, caller['id'], data['messageId']])
            from .store import UserPrincipal
            return {'message': get_store().get_message(UserPrincipal(LOCAL_RECIPIENT), mid)}
        except InteractionError as exc:
            raise failure(exc) from exc

    @app.get('/api/user-inbox')
    async def inbox(request:Request,limit:int=100,after:str | None=None):
        principal=await user(request)
        try:
            cards=get_store().list_for_user(principal,limit=limit,after=after)
            return {'schemaVersion':1,'cards':cards,'nextAfter':cards[-1]['id'] if len(cards)==limit else None}
        except InteractionError as exc:
            raise failure(exc) from exc

    @app.get('/api/user-lockers')
    async def lockers(request:Request):
        principal=await user(request)
        store=get_store()
        with store.db() as db:
            rows=db.execute("""SELECT l.owner,l.source_name,l.workspace_ids,
                (SELECT count(*) FROM cards c WHERE c.owner=l.owner AND c.recipient=l.recipient)+
                (SELECT count(*) FROM locker_messages m WHERE m.owner=l.owner AND m.recipient=l.recipient) AS total,
                (SELECT count(*) FROM cards c WHERE c.owner=l.owner AND c.recipient=l.recipient AND c.is_read=0)+
                (SELECT count(*) FROM locker_messages m WHERE m.owner=l.owner AND m.recipient=l.recipient AND m.is_read=0) AS unread
                FROM user_lockers l WHERE l.recipient=?""", (principal.id,)).fetchall()
        from .contracts import identity
        import json
        result=[]
        for row in rows:
            source=session.get(row['owner'],load_history=False)
            name=source.name if source else row['source_name'] or row['owner']
            workspaces=session.effective_workspace_ids(source) if source else json.loads(row['workspace_ids'])
            if source:
                store.remember_source(row['owner'],principal.id,name,workspaces)
            result.append({'id':identity('ulocker_',[principal.id,row['owner']]),'sessionId':row['owner'],
                           'sourceName':name,'workspaceIds':workspaces,
                           'available':source is not None,'total':row['total'],'unread':row['unread']})
        return {'lockers':result}

    @app.get('/api/user-locker-messages')
    async def messages(request:Request,limit:int=100,after:str | None=None):
        principal=await user(request)
        try:
            return {'schemaVersion':1,**get_store().list_messages(principal,limit=limit,after=after)}
        except InteractionError as exc:
            raise failure(exc) from exc

    @app.get('/api/user-locker-messages/{message_id}')
    async def message(message_id:str,request:Request):
        principal=await user(request)
        try:
            return {'message':get_store().get_message(principal,message_id)}
        except InteractionError as exc:
            raise failure(exc) from exc

    @app.put('/api/user-locker-messages/{message_id}/read')
    async def message_read(message_id:str,data:dict,request:Request):
        principal=await user(request)
        if set(data)!={'read'}:
            raise HTTPException(422,detail={'code':'invalid'})
        try:
            return {'message':get_store().mark_message_read(principal,message_id,data['read'])}
        except InteractionError as exc:
            raise failure(exc) from exc

    @app.delete('/api/user-locker-messages/{message_id}')
    async def message_delete(message_id:str,request:Request):
        principal=await user(request)
        try:
            return get_store().delete_message(principal,message_id)
        except InteractionError as exc:
            raise failure(exc) from exc

    @app.get('/api/user-locker-messages/{message_id}/attachments/{attachment_id}')
    async def message_attachment(message_id:str,attachment_id:str,request:Request):
        principal=await user(request)
        try:
            item=get_store().get_message(principal,message_id)
            from .locker_messages import attachment_ids
            if attachment_id not in attachment_ids(item['message']):
                raise InteractionError('not_found','Attachment is not part of this Locker message')
            from packages.web import server
            # Same opaque registry validation as Chat, with no cross-source
            # allowance. Message ownership remains valid if its Session is gone.
            error=server._attachment_id_error(item['ownerSessionId'],attachment_id)
            if error:
                raise InteractionError('not_found',error['code'])
            record=server._attachment_record(item['ownerSessionId'],attachment_id)
            from starlette.responses import FileResponse
            return FileResponse(record['path'],filename=record.get('displayName') or 'attachment',
                                media_type=record.get('mimeType') or 'application/octet-stream')
        except InteractionError as exc:
            raise failure(exc) from exc

    @app.put('/api/user-inbox/{card_id}/read')
    async def mark_read(card_id:str,data:dict,request:Request):
        principal=await user(request)
        if set(data)!={'read'}:
            raise HTTPException(422,detail={'code':'invalid'})
        try:
            return {'card':get_store().mark_read(principal,card_id,data['read'])}
        except InteractionError as exc:
            raise failure(exc) from exc

    @app.post('/api/user-inbox/{card_id}/reject')
    async def reject(card_id:str,data:dict,request:Request):
        principal=await user(request)
        store=get_store()
        try:
            card=store.reject(principal,card_id,data)
            from packages.core.inbox_service import SessionQueue
            await store.deliver_rejections(SessionQueue())
            return {'card':store.get_for_user(principal,card_id)}
        except InteractionError as exc:
            raise failure(exc) from exc

    @app.delete('/api/user-inbox/{card_id}')
    async def delete(card_id:str,request:Request):
        principal=await user(request)
        try:
            return get_store().delete(principal,card_id)
        except InteractionError as exc:
            raise failure(exc) from exc

    @app.get('/api/user-inbox/{card_id}')
    async def read(card_id:str,request:Request):
        principal=await user(request)
        try:
            return {'card':get_store().get_for_user(principal,card_id)}
        except InteractionError as exc:
            raise failure(exc) from exc

    @app.post('/api/user-inbox/{card_id}/submit')
    @app.post('/api/user-inbox/{card_id}/respond')
    async def respond(card_id:str,data:dict,request:Request):
        principal=await user(request)
        store=get_store()
        try:
            current=store.get_for_user(principal,card_id)
            choice=next((c for c in current['definition'].get('choices',[]) if c['id']==data.get('choiceId')),None)
            if current['status'] == 'open' and choice and choice.get('triggerRef'):
                from .trusted import verify
                import asyncio
                await asyncio.to_thread(verify,getattr(app.state,'automation',None),{
                    'payload':{'ownerSessionId':current['ownerSessionId'],
                               'executionBinding':current.get('trustedChoices',{}).get(choice['id'])}})
            card=store.respond(principal,card_id,data,allow_empty=request.url.path.endswith('/submit'))
        except InteractionError as exc:
            raise failure(exc) from exc
        # Response persistence and Trigger acceptance are distinct results.
        from packages.core.inbox_service import SessionQueue
        await store.deliver_responses(SessionQueue())
        try:
            card=await store.dispatch(principal,card_id,getattr(app.state,'interaction_trigger_adapter',None))
            return {'card':card}
        except InteractionError as exc:
            return {'card':store.get_for_user(principal,card_id),'dispatchError':{'code':exc.code,'message':str(exc)}}

    @app.post('/api/user-inbox/{card_id}/dispatch')
    async def dispatch(card_id:str,request:Request):
        principal=await user(request)
        try:
            card=await get_store().dispatch(principal,card_id,getattr(app.state,'interaction_trigger_adapter',None))
            return {'card':card}
        except InteractionError as exc:
            raise failure(exc) from exc
