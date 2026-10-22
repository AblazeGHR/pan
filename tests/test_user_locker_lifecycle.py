"""User-only Locker lifecycle and durable rejection receipts."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import pytest
from packages.interactions.store import InteractionStore, UserPrincipal
from packages.interactions.contracts import InteractionError, validate
USER=UserPrincipal('user')
def make(store,key='one',owner='owner',**definition):
    return store.create({'ownerSessionId':owner,'recipientId':'user','requestKey':key,'card':{
        'title':'Question','body':'full body','summaryBody':'摘要'*150,**definition}})
def req(key='once'): return {'requestId':key,'expectedRevision':0}
class Queue:
    def __init__(self): self.rows={}; self.puts=0; self.crash=False
    async def lookup(self,sid,qid): return self.rows.get((sid,qid))
    async def put(self,sid,qid,message):
        self.puts+=1; self.rows[sid,qid]={'status':'accepted','queueItemId':qid,'message':message}
        if self.crash: raise asyncio.CancelledError()
        return self.rows[sid,qid]
def test_sources_read_and_legacy(tmp_path):
    store=InteractionStore(tmp_path/'cards.sqlite')
    a=make(store,attachments=[{'id':'att_'+'a'*32,'name':'文件.pdf'}]); b=make(store,'two'); c=make(store,'three','another')
    assert a['lockerId']==b['lockerId']!=c['lockerId']
    validate('Card',a)
    assert not a['read'] and store.mark_read(USER,a['id'])['read']
    assert not store.get_for_user(USER,b['id'])['read']
    assert not store.mark_read(USER,a['id'],False)['read']
    with pytest.raises(InteractionError): store.mark_read(UserPrincipal('other'),a['id'])
    with pytest.raises(InteractionError): store.mark_read(USER,a['id'],1)
    legacy=store.create({'ownerSessionId':'owner','recipientId':'user','requestKey':'legacy','card':{'title':'legacy','body':'untouched'}})
    assert legacy['definition']['summaryBody']=='untouched'
    assert InteractionStore(store.path).get_for_user(USER,legacy['id'])==legacy

def test_atomic_submit_reject(tmp_path):
    store=InteractionStore(tmp_path/'cards.sqlite'); card=make(store,answerInput={})
    def run(reject):
        try:
            other=InteractionStore(store.path)
            return (other.reject(USER,card['id'],req()) if reject else other.respond(USER,card['id'],{**req(),'answer':'yes'}))['status']
        except InteractionError as exc: return exc.code
    with ThreadPoolExecutor(2) as pool: results=list(pool.map(run,[True,False]))
    assert results.count('conflict')==1
    assert store.get_for_user(USER,card['id'])['status'] in {'responded','rejected'}

def test_reject_delete_tombstone(tmp_path):
    store=InteractionStore(tmp_path/'cards.sqlite'); card=make(store)
    with pytest.raises(InteractionError): store.delete(USER,card['id'])
    rejected=store.reject(USER,card['id'],req())
    assert rejected['status']=='rejected' and store.reject(USER,card['id'],req())==rejected
    with pytest.raises(InteractionError): store.respond(USER,card['id'],req(),allow_empty=True)
    with pytest.raises(InteractionError): store.reject(USER,card['id'],req('different'))
    queue=Queue(); asyncio.run(store.deliver_rejections(queue))
    message=next(iter(queue.rows.values()))['message']
    assert message['content']['envelope']['summaryBody']=='摘要'*100
    assert 'full body' not in str(message)
    assert store.delete(USER,card['id'])==store.delete(USER,card['id'])
    assert store.list_for_user(USER)==[]
    with store.db() as db:
        assert db.execute('SELECT count(*) FROM cards').fetchone()[0]==0
        assert db.execute('SELECT payload FROM rejection_outbox').fetchone()[0] is None
        tombstone=dict(db.execute('SELECT * FROM tombstones').fetchone())
        assert 'full body' not in str(tombstone) and '摘要' not in str(tombstone)
        assert db.execute('SELECT count(*) FROM user_lockers').fetchone()[0]==1
    with pytest.raises(InteractionError): make(store)
    asyncio.run(InteractionStore(store.path).deliver_rejections(queue)); assert queue.puts==1

def test_cold_recovery_after_queue_commit_and_user_delete(tmp_path):
    store=InteractionStore(tmp_path/'cards.sqlite'); card=make(store); store.reject(USER,card['id'],req())
    queue=Queue(); queue.crash=True
    with pytest.raises(asyncio.CancelledError): asyncio.run(store.deliver_rejections(queue))
    store.delete(USER,card['id']); asyncio.run(InteractionStore(store.path).deliver_rejections(queue))
    assert queue.puts==1
    with store.db() as db: assert tuple(db.execute('SELECT state,payload FROM rejection_outbox').fetchone())==('accepted',None)

def test_unknown_not_replayed(tmp_path):
    store=InteractionStore(tmp_path/'cards.sqlite'); card=make(store); store.reject(USER,card['id'],req())
    class Missing(Queue):
        async def put(self,*args): self.puts+=1; raise OSError('unknown')
    queue=Missing(); asyncio.run(store.deliver_rejections(queue)); asyncio.run(store.deliver_rejections(queue)); assert queue.puts==1
    with store.db() as db: assert db.execute('SELECT state FROM rejection_outbox').fetchone()[0]=='unknown'

def test_locker_api_read_empty_submit_and_permissions(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from packages.interactions.api import register
    from packages.core import session
    owner=session.create('locker-owner',workdir=str(tmp_path)); app=FastAPI(); register(app); client=TestClient(app)
    app.state.user_inbox_principal=lambda r: USER
    card=client.post('/api/interaction-cards',json={'ownerSessionId':owner.id,'recipientId':'user','requestKey':'one',
        'card':{'title':'No inputs','body':'body'}}).json()['card']
    sources=client.get('/api/user-lockers').json()['lockers']
    validate('Locker',sources[0])
    assert sources[0]['sessionId']==owner.id and sources[0]['sourceName']==owner.name
    assert client.put(f"/api/user-inbox/{card['id']}/read",json={'read':True}).json()['card']['read']
    result=client.post(f"/api/user-inbox/{card['id']}/submit",json=req())
    assert result.status_code==200,result.text
    assert result.json()['card']['status']=='responded'
    assert client.delete(f"/api/user-inbox/{card['id']}").status_code==200
    assert client.get('/api/user-lockers').json()['lockers'][0]['total']==0
    app.state.user_inbox_principal=lambda r: UserPrincipal('other')
    assert client.get('/api/user-lockers').json()['lockers']==[]

def test_deletion_preserves_producer_receipt_and_detached_trigger(tmp_path):
    from packages.interactions.bridge import job_response
    store=InteractionStore(tmp_path/'cards.sqlite')
    card=make(store,choices=[{'id':'go','label':'Go','triggerRef':{'id':'trigger'}}])
    submitted=store.respond(USER,card['id'],{**req(),'choiceId':'go'})
    event=store.event_for_owner('owner',card['id'])
    store.delete(USER,card['id'])
    receipt=store.receipt_for_owner('owner',card['id']); validate('ProducerReceipt',receipt)
    assert receipt['response']==submitted['response'] and receipt['event']==event
    class Adapter:
        def __init__(self): self.puts=0
        async def authorize(self,event): return True
        async def accept(self,event): self.puts+=1; return {'status':'accepted','receiptId':'stable'}
        async def lookup(self,event_id): raise AssertionError('no uncertain attempt')
    adapter=Adapter(); asyncio.run(InteractionStore(store.path).recover_detached(adapter))
    asyncio.run(InteractionStore(store.path).recover_detached(adapter)); assert adapter.puts==1
    with store.db() as db: assert db.execute('SELECT event FROM detached_deliveries').fetchone()[0] is None
    rejected=make(store,'reject'); rejected=store.reject(USER,rejected['id'],req())
    assert job_response(rejected)['status']=='response_recorded'
    assert job_response(rejected)['response']['outcome']=='reject'

def test_real_v1_database_migration_is_idempotent(tmp_path):
    import sqlite3
    from packages.interactions.contracts import canonical, identity
    path=tmp_path/'v1.sqlite'
    request={'ownerSessionId':'old','recipientId':'user','requestKey':'key','card':{'title':'Old','body':'original'}}
    card_id=identity('card_',['old','key'])
    with sqlite3.connect(path) as db:
        db.execute('''CREATE TABLE cards(id TEXT PRIMARY KEY,owner TEXT NOT NULL,recipient TEXT NOT NULL,
          request_key TEXT NOT NULL,signature TEXT NOT NULL,definition TEXT NOT NULL,created_at TEXT NOT NULL,
          revision INTEGER NOT NULL DEFAULT 0,response TEXT,response_key TEXT,event TEXT,delivery TEXT NOT NULL,UNIQUE(owner,request_key))''')
        db.execute('INSERT INTO cards(id,owner,recipient,request_key,signature,definition,created_at,delivery) VALUES(?,?,?,?,?,?,?,?)',
                   (card_id,'old','user','key',canonical(request),canonical(request['card']),'2026-10-01',canonical({'state':'not_requested'})))
    store=InteractionStore(path); old=store.get_for_user(USER,card_id)
    assert old['definition']['body']==old['definition']['summaryBody']=='original'
    assert store.create(request)==old and InteractionStore(path).create(request)==old
    with store.db() as db: assert db.execute('SELECT count(*) FROM user_lockers').fetchone()[0]==1

def test_rejection_partial_failure_and_parallel_dispatch(tmp_path):
    store=InteractionStore(tmp_path/'cards.sqlite')
    for key in ('one','two'): store.reject(USER,make(store,key)['id'],req())
    class Partial(Queue):
        async def put(self,sid,qid,message):
            if self.puts==0:
                self.puts+=1; return {'status':'rejected'}
            return await super().put(sid,qid,message)
    queue=Partial()
    async def run(): await asyncio.gather(store.deliver_rejections(queue),InteractionStore(store.path).deliver_rejections(queue))
    asyncio.run(run())
    # No await inside fake Queue methods: each proven rejection is retryable;
    # acceptance remains unique and does not repeat in subsequent recovery.
    asyncio.run(store.deliver_rejections(queue))
    assert len(queue.rows)==2 and queue.puts==3
    with store.db() as db: assert all(r[0]=='accepted' for r in db.execute('SELECT state FROM rejection_outbox'))
