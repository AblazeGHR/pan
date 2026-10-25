"""Explicit user-only opt-in source management; legacy routes are unchanged."""
from fastapi import APIRouter, HTTPException, Request
import sqlite3
import hashlib
import json
from packages.automation.authorization import require
from packages.jobs.action_templates import Conflict
from packages.plugins.lifecycle import io
from .time_events import Sources, EVENT_TYPE, PAYLOAD_SCHEMA
from .store import data_root

router=APIRouter(prefix='/event-sources')


def references_version(refs):
    return hashlib.sha256(json.dumps(sorted(refs,key=lambda r:r['triggerId']),sort_keys=True).encode()).hexdigest()


async def access(request):
    if await require(request)!='user':raise HTTPException(403,'Time Event source management requires user Bearer')
    return Sources(data_root())


async def invoke(fn,*args,**kwargs):
    try:return await io(fn,*args,**kwargs)
    except Conflict as exc:raise HTTPException(409,str(exc)) from exc
    except KeyError as exc:raise HTTPException(404,str(exc)) from exc
    except ValueError as exc:raise HTTPException(400,str(exc)) from exc
    except (OSError,RuntimeError,sqlite3.Error) as exc:raise HTTPException(503,'Time Event storage unavailable') from exc


@router.get('')
async def listing(request:Request):
    sources=await access(request)
    links=getattr(request.app.state.automation,'links',None)
    rows=[sources.public(s) for s in await invoke(sources.list)]
    for row in rows:
        row['references']=await io(links.references,row['id']) if links else []
        row['referencesVersion']=references_version(row['references'])
        if row.get('setupOperation') and links and not await io(links.source_ready,row['setupOperation']):
            row.update(enabled=False,setupPending=True)
    return {'sources':rows,'eventType':EVENT_TYPE,'payloadSchema':PAYLOAD_SCHEMA}


@router.post('')
async def create(data:dict,request:Request):
    sources=await access(request)
    if set(data)-{'config','expectedRevision','creationKey'} or not {'config','expectedRevision'}<=set(data):raise HTTPException(400,'Expected config and expectedRevision')
    key=data.get('creationKey')
    if key is not None and (not isinstance(key,str) or not 8<=len(key)<=100):raise HTTPException(400,'Invalid creation key')
    return {'source':await invoke(sources.put,data['config'],data['expectedRevision'],creation_key='event-register:'+key if key else None)}


@router.put('/{sid}')
async def update(sid:str,data:dict,request:Request):
    sources=await access(request)
    if set(data)-{'config','expectedRevision','expectedReferences'} or not {'config','expectedRevision'}<=set(data):raise HTTPException(400,'Expected config and expectedRevision')
    links=getattr(request.app.state.automation,'links',None)
    current=next((s for s in await invoke(sources.list) if s['id']==sid),None)
    if current and current.get('setupOperation') and links and not await io(links.source_ready,current['setupOperation']):raise HTTPException(409,'Resume or close the owning Job setup before editing this source')
    if 'expectedReferences' in data:
        refs=await io(links.references,sid) if links else []
        if data['expectedReferences']!=references_version(refs):raise HTTPException(409,'Subscriber references changed; review source impact again')
    return {'source':await invoke(sources.put,data['config'],data['expectedRevision'],sid)}


@router.delete('/{sid}')
async def delete(sid:str,expectedRevision:int,request:Request):
    sources=await access(request)
    links=getattr(request.app.state.automation,'links',None)
    if links and await io(links.references,sid):raise HTTPException(409,'Detach referencing Triggers / Jobs before deleting this source')
    await invoke(sources.delete,sid,expectedRevision)
    return {'deleted':True}
