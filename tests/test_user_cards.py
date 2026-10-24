import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from packages.interactions.store import InteractionStore, UserPrincipal
from packages.interactions.contracts import InteractionError
from packages.interactions.user_cards import UserCardStore, ReminderScheduler, REMINDER, enqueue_reminder


USER=UserPrincipal('user')


def payload(key='one',kind='question',owner='owner',attachments=None):
    return dict(ownerSessionId=owner,requestKey=key,kind=kind,body='**Explain** this',attachments=attachments or [])


def store(tmp_path,clock=lambda:1000):
    return UserCardStore(InteractionStore(tmp_path/'cards.sqlite'),clock=clock)


def test_independent_identity_scope_rules_and_tombstone(tmp_path):
    box=store(tmp_path)
    card=box.create(USER,payload(attachments=[{'id':'att_one','name':'a.txt'}]))
    assert card['answeredAt'] is None and card['read']
    assert box.create(USER,payload(attachments=[{'id':'att_one','name':'a.txt'}]))==card
    with pytest.raises(InteractionError,match='conflict'):
        box.create(USER,payload(kind='task'))
    assert box.list(owner='manager')==[]
    for actor in ('manager','foreign'):
        with pytest.raises(InteractionError) as exc:
            box.answer(actor,dict(cardId=card['id'],body='x',expectedRevision=0))
        assert exc.value.code=='not_found'
    for answer in ('',' \n '):
        with pytest.raises(InteractionError) as exc:
            box.answer('owner',dict(cardId=card['id'],body=answer,expectedRevision=0))
        assert exc.value.code=='invalid'
    with pytest.raises(InteractionError):
        box.read(USER,card['id'],True)
    done=box.answer('owner',dict(cardId=card['id'],body='answer',expectedRevision=0))
    assert done['status']=='answered' and not done['read'] and done['answer']=='answer'
    assert done['createdAt']==card['createdAt'] and done['answeredAt']
    assert box.list(owner='owner')==[]
    with pytest.raises(InteractionError) as exc:
        box.answer('owner',dict(cardId=card['id'],body='overwrite',expectedRevision=1))
    assert exc.value.code=='conflict'
    assert box.read(USER,card['id'],True)['read']
    task=box.create(USER,payload('task','task'))
    assert box.answer('owner',dict(cardId=task['id'],body='',expectedRevision=0))['answer']==''
    pending=box.create(USER,payload('delete'))
    box.delete(USER,pending['id'])
    assert box.list(owner='owner')==[]
    with pytest.raises(InteractionError,match='deleted'):
        box.create(USER,payload('delete'))
    box.delete(USER,card['id'])
    with box.db() as db:
        for table in ('cards','response_outbox','rejection_outbox'):
            assert db.execute('SELECT count(*) FROM '+table).fetchone()[0]==0


def test_revision_cas_race(tmp_path):
    box=store(tmp_path)
    card=box.create(USER,payload())
    with pytest.raises(InteractionError) as exc:
        box.answer('owner',dict(cardId=card['id'],body='x',expectedRevision=3))
    assert exc.value.code=='conflict'
    gate=threading.Barrier(2)
    def answer(body):
        gate.wait()
        try:
            return box.answer('owner',dict(cardId=card['id'],body=body,expectedRevision=0))['answer']
        except InteractionError as exc:
            return exc.code
    with ThreadPoolExecutor(2) as pool:
        results=list(pool.map(answer,['a','b']))
    assert results.count('conflict')==1
    assert box.list(principal=USER)[0]['revision']==1


@pytest.mark.parametrize('finish',['answer','delete'])
def test_fake_clock_first_periodic_stop_and_restart(tmp_path,finish):
    clock=[1000]
    box=store(tmp_path,lambda:clock[0]); sent=[]
    async def enqueue(*args): sent.append(args); return True
    runner=ReminderScheduler(box,enqueue)
    async def run():
        assert box.delay() is None
        card=box.create(USER,payload())
        await runner.tick()
        assert len(sent)==1 and REMINDER in sent[0][2] and card['body'] in sent[0][2] and card['id'] in sent[0][2]
        clock[0]+=599; await runner.tick(); assert len(sent)==1
        clock[0]+=1; await runner.tick(); assert len(sent)==2 and sent[-1][2]==REMINDER
        box.settings(2)
        clock[0]+=119; await runner.tick(); assert len(sent)==2
        clock[0]+=1; await runner.tick(); assert len(sent)==3
        # Many missed periods become one notification, then a fresh full interval.
        clock[0]+=5000
        restarted=ReminderScheduler(store(tmp_path,lambda:clock[0]),enqueue)
        await restarted.tick(); assert len(sent)==4
        await restarted.tick(); assert len(sent)==4 and restarted.store.delay()==120
        other=box.create(USER,payload('two'))
        await runner.tick(); assert len(sent)==5
        box.delete(USER,other['id'])
        if finish=='answer': box.answer('owner',dict(cardId=card['id'],body='done',expectedRevision=0))
        else: box.delete(USER,card['id'])
        clock[0]+=10000; await runner.tick(); assert len(sent)==5
        assert box.delay() is None
    asyncio.run(run())


def test_recovered_outbox_uses_original_id_and_no_burst(tmp_path):
    box=store(tmp_path); box.create(USER,payload())
    first=box.reminders()
    assert len(first)==1
    recovered=store(tmp_path,lambda:99999)
    assert recovered.reminders()==first
    recovered.acknowledge(first[0]['id'])
    assert recovered.reminders()==[] and recovered.delay()==600


@pytest.mark.parametrize('minutes',[0,1441,True,1.5,'10'])
def test_invalid_interval(tmp_path,minutes):
    with pytest.raises(InteractionError): store(tmp_path).settings(minutes)


def test_attachment_only_card_and_interval_boundaries(tmp_path):
    box=store(tmp_path)
    assert box.settings()=={'minutes':10}
    for minutes in (1,1440):
        assert box.settings(minutes)=={'minutes':minutes}
    data={**payload(),'body':'','attachments':[{'id':'att_one','name':'evidence'}]}
    assert box.create(USER,data)['attachments']==data['attachments']
    for bad in ({**payload('empty'),'body':''},{**payload('kind'),'kind':[]},
                {**payload('revision'),'attachments':[{'id':'att_one','name':42}]}):
        with pytest.raises(InteractionError): box.create(USER,bad)


def test_scheduler_waits_on_event_without_empty_polling(tmp_path):
    box=store(tmp_path); calls=[]
    class CountingScheduler(ReminderScheduler):
        async def tick(self):
            calls.append(True)
            await super().tick()
    async def run():
        runner=CountingScheduler(box)
        task=asyncio.create_task(runner.run())
        for _ in range(20): await asyncio.sleep(0)
        assert len(calls)==1 and box.delay() is None
        runner.changed.set()
        for _ in range(20): await asyncio.sleep(0)
        assert len(calls)==2
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
    asyncio.run(run())


def test_reminder_legal_path_wakes_normal_queue(tmp_path,monkeypatch):
    from packages.core import session,worker,legal_offline
    source=session.create('owner',workdir=str(tmp_path))
    session.save(source)
    wake=AsyncMock()
    monkeypatch.setattr(worker,'_wake_worker',wake)
    monkeypatch.setattr(worker,'_bcast',AsyncMock())
    async def run():
        assert await enqueue_reminder(source.id,'notice1',REMINDER)
        assert await enqueue_reminder(source.id,'notice1',REMINDER)
        current=session.get(source.id,load_history=False)
        assert len(current.queue_pending)==1
        assert current.queue_pending[0]['type']=='notice'
        assert current.queue_pending[0]['kind']=='notice'
        wake.assert_awaited_once_with(source.id,auto_spawn=True)
    asyncio.run(run())


def test_reminder_illegal_stays_locked_and_wake_does_not_unlock(tmp_path,monkeypatch):
    from packages.core import session,worker,legal_offline,queue_locks
    source=session.create('owner',workdir=str(tmp_path))
    identity=dict(token='failed',generation=1,serviceId='previous',workerId='old',pid=None)
    legal_offline.admit(source,identity,kind='foreground')
    legal_offline.detect(source,'test-crash',eligible=lambda row:True,remember=lambda row:None)
    session.save(source)
    original_wake=worker._wake_worker
    created=AsyncMock()
    monkeypatch.setattr(worker,'create_worker',created)
    monkeypatch.setattr(worker,'_bcast',AsyncMock())
    async def run():
        assert await enqueue_reminder(source.id,'illegal-notice',REMINDER)
        current=session.get(source.id,load_history=False)
        row=current.queue_pending[0]
        assert row['type']=='notice' and row['kind']=='notice'
        assert legal_offline.is_illegal(current)
        assert queue_locks.source(row)=='illegal'
        await original_wake(source.id,auto_spawn=True)
        assert legal_offline.is_illegal(current)
        assert queue_locks.source(row)=='illegal'
        created.assert_not_called()
    asyncio.run(run())


def test_missing_owner_drops_reminder_state(tmp_path,monkeypatch):
    import packages.interactions.user_cards as user_cards
    box=store(tmp_path)
    monkeypatch.setattr(user_cards,'get_store',lambda:box)
    with box.db() as db:
        db.execute('INSERT INTO user_card_reminders VALUES(?,?)',('deleted-owner',1000))
        db.execute('INSERT INTO user_card_outbox VALUES(?,?,?)',('deleted-notice','deleted-owner',REMINDER))
    async def run():
        assert await enqueue_reminder('deleted-owner','deleted-notice',REMINDER)
    asyncio.run(run())
    assert box.delay() is None
    with box.db() as db:
        assert db.execute('SELECT count(*) FROM user_card_reminders').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM user_card_outbox').fetchone()[0]==0


def test_http_auth_attachments_owner_counts_and_terminal(tmp_path,monkeypatch):
    from packages.core import session
    from packages.web import server
    from packages.interactions.api import register
    source=session.create('owner',workdir=str(tmp_path)); other=session.create('other',workdir=str(tmp_path))
    app=FastAPI(); register(app)
    app.state.user_inbox_principal=lambda request: USER if request.headers.get('Authorization')=='Bearer test' else None
    auth={'Authorization':'Bearer test'}; caller={'X-Pan-Queue-Caller':source.id}
    client=TestClient(app,client=('127.0.0.1',12345))
    route='/api/user-cards'; data=payload(owner=source.id,attachments=[{'id':'att_one','name':'a.txt'}])
    assert client.post(route,json=data).status_code==401
    monkeypatch.setattr(server,'_attachment_id_error',lambda sid,aid: None if aid=='att_one' else {'code':'wrong_owner'})
    monkeypatch.setattr(server,'_validate_message_attachment_references',lambda *args:None)
    denied={**data,'attachments':[{'id':'foreign','name':'x'}]}
    assert client.post(route,json=denied,headers=auth).status_code==403
    card=client.post(route,json=data,headers=auth).json()['card']
    assert client.get('/api/user-lockers',headers=auth).json()['lockers'][0]['unread']==0
    tool='/api/user-card-tools/'
    assert client.post(tool+'list_pending',json={},headers={'X-Pan-Queue-Caller':other.id}).json()['cards']==[]
    assert client.post(tool+'list_pending',json={'ownerSessionId':source.id},headers=caller).status_code==422
    for h in ({},{**caller,'Origin':'http://localhost'}):
        assert client.post(tool+'list_pending',json={},headers=h).status_code==403
    assert client.post(tool+'list_pending',json={},headers=caller).json()['cards']==[card]
    answer=dict(cardId=card['id'],body='done',expectedRevision=0)
    assert client.post(tool+'answer',json=answer,headers={'X-Pan-Queue-Caller':other.id}).status_code==404
    assert client.post(tool+'answer',json=answer,headers=caller).status_code==200
    assert client.post(tool+'answer',json=answer,headers=caller).status_code==409
    assert client.post(tool+'list_pending',json={},headers=caller).json()['cards']==[]
    summary=client.get('/api/user-lockers',headers=auth).json()['lockers'][0]
    assert summary['total']==1 and summary['unread']==1
    assert client.put(route+'/'+card['id']+'/read',json={'read':True},headers=auth).json()['card']['read']
    assert client.get('/api/user-lockers',headers=auth).json()['lockers'][0]['unread']==0
    assert client.delete(route+'/'+card['id'],headers=auth).status_code==200
    assert client.get(route,headers=auth).json()['cards']==[]
