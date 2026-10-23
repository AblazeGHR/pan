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
            'invalid':422,'capacity':409,'conflict':409}.get(exc.code,409)
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

    @app.post('/api/interaction-cards')
    async def create(data:dict):
        owner(data.get('ownerSessionId'))
        try:
            return {'card':get_store().create(data)}
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
            rows=db.execute('SELECT l.owner,count(c.id) AS total,coalesce(sum(1-c.is_read),0) AS unread FROM user_lockers l LEFT JOIN cards c ON c.owner=l.owner AND c.recipient=l.recipient WHERE l.recipient=? GROUP BY l.owner',
                            (principal.id,)).fetchall()
        from .contracts import identity
        result=[]
        for row in rows:
            source=session.get(row['owner'],load_history=False)
            result.append({'id':identity('ulocker_',[principal.id,row['owner']]),'sessionId':row['owner'],
                           'sourceName':source.name if source else row['owner'],
                           'workspaceIds':session.effective_workspace_ids(source) if source else [],
                           'available':source is not None,'total':row['total'],'unread':row['unread']})
        return {'lockers':result}

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
