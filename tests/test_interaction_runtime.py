"""Product UserAuth -> directed EventStore/Engine -> safe local Pause."""
import asyncio
import json
import hashlib
import shutil
from unittest.mock import AsyncMock

import httpx
from fastapi import FastAPI

from packages.automation.service import Service
from packages.interactions.api import register, get_store
from packages.interactions.runtime import bind
from packages.interactions.store import UserPrincipal, InteractionStore
from packages.core import session, worker
from tests.test_automation_core import action, trigger, until
from packages.interactions.trusted import create as create_bound
from packages.interactions.contracts import InteractionError
import pytest


def test_real_auth_and_directed_engine(tmp_path, monkeypatch):
    monkeypatch.setattr(worker, '_wake_worker', AsyncMock())
    async def run():
        owner=session.create('source',workdir=str(tmp_path))
        target=session.create('target',workdir=str(tmp_path))
        paused=[]
        async def pause(sid, arguments):
            paused.append(sid)
            return {'ok':True}
        svc=Service(tmp_path/'automation',worker,pause)
        await svc.start()
        app=FastAPI();app.state.automation=svc;register(app)
        app.state.interaction_trigger_adapter=bind(svc)
        svc.engine.actions.put('actions',action([target.id]))
        for tid in ('selected','not-selected'):
            svc.ledger.put({**trigger(tid),'eventTypes':['pan.interaction.responded']},0,True)
        auth={'Authorization':'Bearer '+svc.auth.login(svc.auth.key())}
        card=create_bound(svc,get_store(),{'ownerSessionId':owner.id,'recipientId':'user','requestKey':'one',
            'card':{'title':'choose','body':'private body','summaryBody':'S'*250,
                    'choices':[{'id':'go','label':'Go','triggerRef':{'id':'selected','revision':1}}]}})
        other=get_store().create({'ownerSessionId':owner.id,'recipientId':'someone-else','requestKey':'other',
            'card':{'title':'other','body':'other secret'}})
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                assert (await client.get('/api/user-lockers')).status_code==401
                assert (await client.get('/api/user-lockers',headers={'Authorization':'Bearer wrong'})).status_code==401
                assert (await client.get('/api/user-lockers',headers=auth)).status_code==200
                assert (await client.get('/api/user-inbox/'+other['id'],headers=auth)).status_code==404
                assert (await client.put('/api/user-inbox/'+other['id']+'/read',headers=auth,json={'read':True})).status_code==404
                request={'requestId':'once','expectedRevision':0,'choiceId':'go'}
                result=await client.post('/api/user-inbox/'+card['id']+'/submit',headers=auth,json=request)
                assert result.status_code==200,result.text
                assert result.json()['card']['delivery']['state']=='accepted'
                again=await client.post('/api/user-inbox/'+card['id']+'/submit',headers=auth,json=request)
                assert again.json()['card']['delivery']['state']=='accepted'
                async def done():
                    runs=(await svc.engine.io(svc.ledger.read))['runs']
                    return len(runs)==1 and next(iter(runs.values()))['status']=='succeeded'
                await until(done)
                assert paused==[target.id]
                assert {r['triggerId'] for r in svc.ledger.read()['runs'].values()}=={'selected'}
                assert len(svc.events.page()['events'])==1
                # A Trigger update after planning is rechecked at admission.
                old=svc.ledger.read()['triggers']['selected']
                svc.ledger.put({k:v for k,v in old.items() if k in {'id','name','eventTypes','actionId','arguments','enabled','retry','executionMode','maxConcurrency'}} | {'enabled':False},old['revision'],True)
                frozen_event=next(iter(svc.ledger.read()['runs'].values()))['event']
                with pytest.raises(InteractionError,match='changed'):
                    await app.state.interaction_trigger_adapter.submit({**frozen_event,'id':'evt_stale'},'selected',expected_revision=old['revision'])
                assert svc.events.lookup('evt_stale') is None
                # Response notice has separate Queue identity; neither full
                # answer nor body is sent. Delete must not add a notice.
                queue=session.get(owner.id,load_history=False).queue_pending
                assert len(queue)==1
                assert len(queue[0]['envelope']['summaryBody'])==200
                assert 'private body' not in json.dumps(queue)
                await client.delete('/api/user-inbox/'+card['id'],headers=auth)
                assert len(session.get(owner.id,load_history=False).queue_pending)==1
        finally:
            await svc.close()
    asyncio.run(run())


def test_notification_crash_reconciles_only_and_migration_preserves_foreign_recipient(tmp_path):
    async def run():
        original=InteractionStore(tmp_path/'legacy.sqlite')
        card=original.create({'ownerSessionId':'owner','recipientId':'legacy-alias','requestKey':'old',
                           'card':{'title':'legacy','body':'body','answerInput':{}}})
        with original.db() as db:
            db.execute('DROP TABLE response_outbox')  # Pre-wiring database shape.
            old_row=tuple(db.execute('SELECT * FROM cards').fetchone())
        digest=hashlib.sha256(original.path.read_bytes()).hexdigest()
        shutil.copy2(original.path,tmp_path/'copy.sqlite')
        store=InteractionStore(tmp_path/'copy.sqlite')
        with store.db() as db:
            assert tuple(db.execute('SELECT * FROM cards').fetchone())==old_row
        assert store.list_for_user(UserPrincipal('user'))==[]
        store.respond(UserPrincipal('legacy-alias'),card['id'],{'requestId':'r','expectedRevision':0,'answer':'private'})
        receipts={};calls=[]
        class Queue:
            async def put(self,sid,qid,message):
                calls.append(qid);receipts[qid]={'status':'accepted'}
                raise RuntimeError('after durable Queue commit')
            async def lookup(self,sid,qid):return receipts.get(qid)
        await store.deliver_responses(Queue())
        reopened=InteractionStore(store.path)
        await reopened.deliver_responses(Queue())
        await reopened.deliver_responses(Queue())
        assert len(calls)==1
        with reopened.db() as db:
            assert db.execute('SELECT state,payload FROM response_outbox').fetchone()[:]==('accepted',None)
        assert reopened.list_for_user(UserPrincipal('user'))==[]
        assert hashlib.sha256(original.path.read_bytes()).hexdigest()==digest
    asyncio.run(run())


def test_real_event_receipt_recovers_unknown_without_second_admission(tmp_path,monkeypatch):
    monkeypatch.setattr(worker,'_wake_worker',AsyncMock())
    async def run():
        svc=Service(tmp_path/'automation',worker,AsyncMock(return_value={'ok':True}))
        await svc.start()
        try:
            adapter=bind(svc)
            owner=session.create('owner',workdir=str(tmp_path))
            target=session.create('target',workdir=str(tmp_path))
            svc.engine.actions.put('actions',action([target.id]))
            svc.ledger.put({**trigger('selected'),'eventTypes':['pan.interaction.responded']},0,True)
            store=InteractionStore(tmp_path/'cards.sqlite')
            card=create_bound(svc,store,{'ownerSessionId':owner.id,'recipientId':'user','requestKey':'once',
                'card':{'title':'choose','body':'body','choices':[{'id':'go','label':'Go',
                    'triggerRef':{'id':'selected','revision':1}}]}})
            principal=UserPrincipal('user')
            store.respond(principal,card['id'],{'requestId':'once','expectedRevision':0,'choiceId':'go'})
            original=svc.events.append
            calls=[]
            def commit_then_lose_receipt(*args):
                calls.append(args[0]['id']);original(*args)
                raise OSError('receipt lost after Event commit')
            monkeypatch.setattr(svc.events,'append',commit_then_lose_receipt)
            assert (await store.dispatch(principal,card['id'],adapter))['delivery']['state']=='unknown'
            monkeypatch.setattr(svc.events,'append',original)
            reopened=InteractionStore(store.path)
            assert (await reopened.dispatch(principal,card['id'],adapter))['delivery']['state']=='accepted'
            await reopened.dispatch(principal,card['id'],adapter)
            assert len(calls)==1
            assert len(svc.events.page()['events'])==1
        finally:
            await svc.close()
    asyncio.run(run())
