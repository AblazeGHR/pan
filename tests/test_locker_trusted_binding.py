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


def test_create_writer_barrier(tmp_path,monkeypatch):
    async def run():
        svc,adapter,owner,target,calls,request=await setup(tmp_path,monkeypatch)
        store=InteractionStore(tmp_path/'cards.sqlite')
        entered,release=threading.Event(),threading.Event()
        original=store.create
        def held(*args,**kwargs):
            entered.set()
            assert release.wait(5)
            return original(*args,**kwargs)
        monkeypatch.setattr(store,'create',held)
        try:
            with ThreadPoolExecutor(1) as pool:
                pending=pool.submit(create,svc,store,request)
                assert entered.wait(5)
                try:
                    assert not svc.ledger.lock.acquire(blocking=False)
                    assert not actions_lock.acquire(blocking=False)
                finally:
                    release.set()
                card=pending.result(5)
            assert card['trustedChoices']['go']['targetIds']==[target.id]
            assert 'pause_everything' in card['trustedChoices']['go']['summary']
            svc.engine.actions.put('actions',action([owner.id]))
            with pytest.raises(InteractionError,match='changed'):
                verify(svc,{'payload':{'ownerSessionId':owner.id,'executionBinding':card['trustedChoices']['go']}})
            assert calls==[]
        finally:
            release.set()
            await svc.close()
    asyncio.run(run())


@pytest.mark.parametrize('stage',['approval','admission','effect','revoked_effect'])
def test_fixed_meaning_barriers(tmp_path,monkeypatch,stage):
    async def run():
        svc,adapter,owner,target,calls,request=await setup(tmp_path,monkeypatch)
        store=get_store();card=create(svc,store,request)
        entered,release=asyncio.Event(),asyncio.Event()
        original=svc.engine.execute
        async def held(*args):
            entered.set();await release.wait()
            return await original(*args)
        if stage in {'effect','revoked_effect'}:
            svc.engine.execute=held
        user=UserPrincipal('user')
        try:
            if stage=='approval':
                svc.engine.actions.put('actions',action([owner.id]))
                app=FastAPI();app.state.automation=svc;register(app)
                auth={'Authorization':'Bearer '+svc.auth.login(svc.auth.key())}
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                    response=await client.post('/api/user-inbox/'+card['id']+'/submit',headers=auth,
                        json={'requestId':'once','expectedRevision':0,'choiceId':'go'})
                assert response.status_code==409 and response.json()['detail']['code']=='stale'
                assert store.get_for_user(user,card['id'])['response'] is None
                assert svc.events.page()['events']==[]
            else:
                store.respond(user,card['id'],{'requestId':'once','expectedRevision':0,'choiceId':'go'})
                if stage=='admission':
                    original_authorize=adapter.authorize
                    async def change_after_authorize(event):
                        result=await original_authorize(event)
                        svc.engine.actions.put('actions',action([owner.id]))
                        return result
                    adapter.authorize=change_after_authorize
                    with pytest.raises(InteractionError) as stale:
                        await store.dispatch(user,card['id'],adapter)
                    assert stale.value.code=='stale'
                    assert store.get_for_user(user,card['id'])['delivery']['state']=='rejected'
                    assert svc.events.page()['events']==[]
                else:
                    assert (await store.dispatch(user,card['id'],adapter))['delivery']['state']=='accepted'
                    await asyncio.wait_for(entered.wait(),5)
                    if stage=='effect':
                        svc.engine.actions.put('actions',action([owner.id]))
                    else:
                        owner.pan_access={'restrict_to_managed':True};session.save(owner)
                    release.set()
                    async def done():
                        runs=svc.ledger.read()['runs']
                        return bool(runs) and all(r['status']=='failed' for r in runs.values())
                    await until(done)
            assert calls==[]
        finally:
            release.set();await svc.close()
    asyncio.run(run())


def test_scope_and_dynamic_targets_fail_closed(tmp_path,monkeypatch):
    async def run():
        svc,adapter,owner,target,calls,request=await setup(tmp_path,monkeypatch)
        store=InteractionStore(tmp_path/'cards.sqlite')
        try:
            owner.pan_access={'restrict_to_managed':True};session.save(owner)
            with pytest.raises(InteractionError,match='targets') as denied:
                create(svc,store,request)
            assert denied.value.code=='forbidden'
            owner.managed=[target.id];session.save(owner)
            card=create(svc,store,request)
            assert card['trustedChoices']['go']['authorization']['basis']=='self_or_managed'
            dynamic=action([target.id]);dynamic['execution']['selector']={'mode':'event','path':'payload.response.answer'}
            svc.engine.actions.put('actions',dynamic)
            with pytest.raises(InteractionError,match='provable') as incomplete:
                create(svc,store,{**request,'requestKey':'dynamic'})
            assert incomplete.value.code=='incomplete'
            assert len(store.list_for_user(UserPrincipal('user')))==1
        finally:
            await svc.close()
    asyncio.run(run())


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


@pytest.mark.parametrize('changed',[False,True])
def test_cold_queued_run_and_unknown_original_receipt(tmp_path,monkeypatch,changed):
    async def run():
        svc,adapter,owner,target,calls,request=await setup(tmp_path,monkeypatch)
        store=InteractionStore(tmp_path/'cards.sqlite');card=create(svc,store,request)
        principal=UserPrincipal('user')
        store.respond(principal,card['id'],{'requestId':'once','expectedRevision':0,'choiceId':'go'})
        monkeypatch.setattr(svc.engine,'kick',AsyncMock())
        append=svc.events.append
        def lose_receipt(*args):
            append(*args)
            raise OSError('original Event committed, receipt lost')
        monkeypatch.setattr(svc.events,'append',lose_receipt)
        assert (await store.dispatch(principal,card['id'],adapter))['delivery']['state']=='unknown'
        svc.wake.set()  # Dispatcher wake after the deliberately lost admission receipt.
        async def queued():
            runs=svc.ledger.read()['runs']
            return len(runs)==1 and next(iter(runs.values()))['status']=='queued'
        await until(queued)
        await svc.close()
        if changed:
            svc.engine.actions.put('actions',action([owner.id]))
        async def pause(sid,args):
            calls.append(sid);return {'ok':True}
        restarted=Service(tmp_path/'automation',worker,pause)
        await restarted.start()
        try:
            new_adapter=bind(restarted)
            reopened=InteractionStore(store.path)
            assert (await reopened.dispatch(principal,card['id'],new_adapter))['delivery']['state']=='accepted'
            await restarted.engine.kick()
            async def done():
                runs=restarted.ledger.read()['runs']
                return len(runs)==1 and next(iter(runs.values()))['status']==('failed' if changed else 'succeeded')
            await until(done)
            assert calls==([] if changed else [target.id])
            assert len(restarted.events.page()['events'])==1
        finally:
            await restarted.close()
    asyncio.run(run())


def test_later_attempt_cannot_read_new_action(tmp_path,monkeypatch):
    async def run():
        svc,adapter,owner,target,calls,request=await setup(tmp_path,monkeypatch)
        try:
            svc.engine.actions.put('actions',{**action([target.id]),'idempotent':True})
            svc.ledger.put({**trigger('selected'),'eventTypes':['pan.interaction.responded'],
                            'retry':{'enabled':True,'maxAttempts':3}},1,True)
            store=InteractionStore(tmp_path/'cards.sqlite');card=create(svc,store,request)
            store.respond(UserPrincipal('user'),card['id'],{'requestId':'once','expectedRevision':0,'choiceId':'go'})
            event=store.event_for_owner(owner.id,card['id'])
            svc.ledger.append(dict(id='later',triggerId='selected',triggerRevision=2,actionId='pause',event=event,
                                   status='queued',admitted=True,arguments={}))
            execute=svc.engine.execute
            svc.engine.execute=AsyncMock(return_value={'ok':False})
            await svc.engine.run_attempt(svc.ledger.claim('later'))
            assert svc.ledger.read()['runs']['later']['status']=='retry_wait'
            svc.engine.actions.put('actions',{**action([owner.id]),'idempotent':True})
            clock=svc.ledger.clock
            svc.ledger.clock=lambda:clock()+100
            svc.engine.execute=execute
            await svc.engine.run_attempt(svc.ledger.claim('later'))
            row=svc.ledger.read()['runs']['later']
            assert row['attemptCount']==2 and row['status']=='failed' and 'changed' in row['error']
            assert calls==[]
        finally:
            await svc.close()
    asyncio.run(run())
