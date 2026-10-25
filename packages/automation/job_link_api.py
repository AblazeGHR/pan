"""User-only UI composition endpoints, deliberately outside legacy MCP paths."""
from fastapi import APIRouter,HTTPException,Request
import sqlite3
from packages.automation.authorization import require
from packages.jobs.action_templates import Conflict
from .store import Conflict as LedgerConflict
from packages.plugins.lifecycle import io

router=APIRouter(prefix='/api/automation')


async def host(request):
    if await require(request)!='user':raise HTTPException(403,'Job composition requires user Bearer')
    svc=request.app.state.automation
    if not getattr(svc,'links',None):raise HTTPException(503,'Job composition unavailable')
    return svc


async def call(fn,*args):
    try:return await fn(*args)
    except (Conflict,LedgerConflict) as exc:raise HTTPException(409,str(exc)) from exc
    except KeyError as exc:raise HTTPException(404,str(exc)) from exc
    except (ValueError,TypeError) as exc:raise HTTPException(400,str(exc)) from exc
    except (OSError,sqlite3.Error) as exc:raise HTTPException(503,'Setup incomplete. Draft is disabled; retry the same operation.') from exc


@router.get('/job-edits')
async def edits(request:Request):
    svc=await host(request)
    return {'operations':await io(svc.links.operations)}


@router.post('/jobs/compose')
async def compose(data:dict,request:Request):
    svc=await host(request)
    result=await call(svc.links.compose,data)
    await svc.engine.kick()
    return result


@router.post('/job-edits/{key}/resume')
async def resume(key:str,request:Request):
    svc=await host(request)
    state=await io(svc.ledger.read)
    op=state.get('linkOperations',{}).get(key)
    if not op:raise HTTPException(404,'Job edit missing')
    return await call(svc.links.compose,op['request'])


@router.post('/job-edits/{key}/cancel')
async def cancel(key:str,request:Request):
    svc=await host(request)
    await call(svc.links.cancel,key)
    return {'cancelled':True}
