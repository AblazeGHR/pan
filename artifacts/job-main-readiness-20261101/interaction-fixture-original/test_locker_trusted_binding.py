"""Creation/approval/effect barriers and producer scope, isolated stores only."""
import asyncio
import json
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from packages.core import session, worker
from packages.actions.store import _lock as actions_lock
from packages.interactions.api import register, get_store
from packages.interactions.contracts import InteractionError
from packages.interactions.runtime import bind
from packages.interactions.store import InteractionStore, UserPrincipal
from packages.interactions.trusted import create, verify
from packages.automation.service import Service
from tests.test_automation_core import action, trigger, until


async def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(worker,'_wake_worker',AsyncMock())
    owner=session.create('producer',workdir=str(tmp_path))
    target=session.create('approved-target',workdir=str(tmp_path))
    calls=[]
    async def pause(sid,args):
        calls.append(sid)
        return {'ok':True}
    svc=Service(tmp_path/'automation',worker,pause)
    await svc.start()
    adapter=bind(svc)
    svc.engine.actions.put('actions',action([target.id]))
    svc.ledger.put({**trigger('selected'),'eventTypes':['pan.interaction.responded']},0,True)
    request={'ownerSessionId':owner.id,'recipientId':'user','requestKey':'card',
             'card':{'title':'Producer label','body':'body','answerInput':{},'choices':[{'id':'go','label':'Do nothing',
                      'triggerRef':{'id':'selected'}}]}}
    return svc,adapter,owner,target,calls,request








def test_old_unbound_migration_has_no_guessed_binding(tmp_path):
    source=InteractionStore(tmp_path/'original.sqlite')
    req={'ownerSessionId':'owner','recipientId':'user','requestKey':'old','card':{'title':'Old','body':'body',
        'choices':[{'id':'go','label':'Go','triggerRef':{'id':'unknown'}}]}}
    card=source.create(req)
    with source.db() as db:
        db.execute('ALTER TABLE cards DROP COLUMN bindings')
        before=[tuple(r) for r in db.execute('SELECT * FROM cards')]
    original_bytes=source.path.read_bytes()
    shutil.copy2(source.path,tmp_path/'copy.sqlite')
    migrated=InteractionStore(tmp_path/'copy.sqlite')
    with migrated.db() as db:
        assert [tuple(r)[:-1] for r in db.execute('SELECT * FROM cards')]==before
    view=migrated.get_for_user(UserPrincipal('user'),card['id'])
    assert view['trustedChoices']=={} and view['definition']==card['definition']
    assert source.path.read_bytes()==original_bytes
    migrated.reject(UserPrincipal('user'),card['id'],{'requestId':'reject','expectedRevision':0})
    assert migrated.get_for_user(UserPrincipal('user'),card['id'])['status']=='rejected'
