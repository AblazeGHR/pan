"""Explicit user-only opt-in source management; legacy routes are unchanged."""
from fastapi import APIRouter, HTTPException, Request
import sqlite3
import hashlib
import json
from packages.automation.authorization import require
from packages.jobs.action_templates import Conflict
from packages.plugins.lifecycle import io
from .history import data_root

router=APIRouter(prefix='/event-sources')


def references_version(refs):
    return hashlib.sha256(json.dumps(sorted(refs,key=lambda r:r['triggerId']),sort_keys=True).encode()).hexdigest()


async def access(request):
    if await require(request)!='user':raise HTTPException(403,'Time Event source management requires user Bearer')
    from .source_history import SourceHistory
    return SourceHistory(data_root())


async def invoke(fn,*args,**kwargs):
    try:return await io(fn,*args,**kwargs)
    except Conflict as exc:raise HTTPException(409,str(exc)) from exc
    except KeyError as exc:raise HTTPException(404,str(exc)) from exc
    except ValueError as exc:raise HTTPException(400,str(exc)) from exc
    except (OSError,RuntimeError,sqlite3.Error) as exc:raise HTTPException(503,'Time Event storage unavailable') from exc


@router.get('')
async def listing(request:Request):
    from .time_contract import EVENT_TYPE, PAYLOAD_SCHEMA
    sources=await access(request)
    links=getattr(request.app.state.automation,'links',None)
    rows=[sources.public(s) for s in await invoke(sources.list)]
    for row in rows:
        row['references']=await io(links.references,row['id']) if links else await io(sources.references,row['id'])
        row['referencesVersion']=references_version(row['references'])
        if row.get('setupOperation') and links and not await io(links.source_ready,row['setupOperation']):
            row.update(enabled=False,setupPending=True)
    return {'sources':rows,'eventType':EVENT_TYPE,'payloadSchema':PAYLOAD_SCHEMA}


@router.post('')
async def create(request: Request):
    from packages.jobs.final_api import retired_legacy_write
    return await retired_legacy_write(request)


@router.put('/{sid}')
async def update(sid: str, request: Request):
    from packages.jobs.final_api import retired_legacy_write
    return await retired_legacy_write(request)


@router.delete('/{sid}')
async def delete(sid: str, request: Request):
    from packages.jobs.final_api import retired_legacy_write
    return await retired_legacy_write(request)
