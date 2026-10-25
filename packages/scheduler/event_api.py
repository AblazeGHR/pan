"""Explicit user-only opt-in source management; legacy routes are unchanged."""
from fastapi import APIRouter, HTTPException, Request
import sqlite3
from packages.automation.authorization import require
from packages.jobs.action_templates import Conflict
from packages.plugins.lifecycle import io
from .time_events import Sources, EVENT_TYPE, PAYLOAD_SCHEMA
from .store import data_root

router=APIRouter(prefix='/event-sources')


async def access(request):
    if await require(request)!='user':raise HTTPException(403,'Time Event source management requires user Bearer')
    return Sources(data_root())


async def invoke(fn,*args):
    try:return await io(fn,*args)
    except Conflict as exc:raise HTTPException(409,str(exc)) from exc
    except KeyError as exc:raise HTTPException(404,str(exc)) from exc
    except ValueError as exc:raise HTTPException(400,str(exc)) from exc
    except (OSError,RuntimeError,sqlite3.Error) as exc:raise HTTPException(503,'Time Event storage unavailable') from exc


@router.get('')
async def listing(request:Request):
    sources=await access(request)
    return {'sources':[sources.public(s) for s in await invoke(sources.list)],'eventType':EVENT_TYPE,'payloadSchema':PAYLOAD_SCHEMA}


@router.post('')
async def create(data:dict,request:Request):
    sources=await access(request)
    if set(data)!={'config','expectedRevision'}:raise HTTPException(400,'Expected config and expectedRevision')
    return {'source':await invoke(sources.put,data['config'],data['expectedRevision'])}


@router.put('/{sid}')
async def update(sid:str,data:dict,request:Request):
    sources=await access(request)
    if set(data)!={'config','expectedRevision'}:raise HTTPException(400,'Expected config and expectedRevision')
    return {'source':await invoke(sources.put,data['config'],data['expectedRevision'],sid)}


@router.delete('/{sid}')
async def delete(sid:str,expectedRevision:int,request:Request):
    sources=await access(request)
    await invoke(sources.delete,sid,expectedRevision)
    return {'deleted':True}
