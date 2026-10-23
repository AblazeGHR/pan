"""User Inbox persistence and at-most-one response; no Job execution ownership."""
from __future__ import annotations
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from .contracts import InteractionError, canonical, identity, validate


def now():
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class UserPrincipal:
    id: str
    def __post_init__(self):
        validate('Id', self.id)


class InteractionStore:
    def __init__(self, path, *, capacity=1000):
        self.path = Path(path)
        self.capacity = capacity
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS cards(
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, recipient TEXT NOT NULL,
                request_key TEXT NOT NULL, signature TEXT NOT NULL, definition TEXT NOT NULL,
                created_at TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
                response TEXT, response_key TEXT, event TEXT, delivery TEXT NOT NULL,
                UNIQUE(owner,request_key))''')
            db.execute('CREATE INDEX IF NOT EXISTS cards_recipient ON cards(recipient,id)')
            columns={r[1] for r in db.execute('PRAGMA table_info(cards)')}
            if 'is_read' not in columns:
                db.execute('ALTER TABLE cards ADD COLUMN is_read INTEGER NOT NULL DEFAULT 0')
            db.execute('CREATE TABLE IF NOT EXISTS user_lockers(owner TEXT NOT NULL,recipient TEXT NOT NULL,PRIMARY KEY(owner,recipient))')
            db.execute('INSERT OR IGNORE INTO user_lockers SELECT DISTINCT owner,recipient FROM cards')
            db.execute('CREATE TABLE IF NOT EXISTS owner_receipts(id TEXT PRIMARY KEY,owner TEXT,recipient TEXT,response TEXT,event TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS tombstones(id TEXT PRIMARY KEY,owner TEXT,recipient TEXT,signature TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS rejection_outbox(id TEXT PRIMARY KEY,owner TEXT,payload TEXT,state TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS response_outbox(id TEXT PRIMARY KEY,owner TEXT,payload TEXT,state TEXT NOT NULL)')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _view(self, row, db):
        definition=json.loads(row['definition'])
        definition.setdefault('summaryBody',definition.get('notification',{}).get('summary',definition['body']))
        response=json.loads(row['response']) if row['response'] else None
        notice=db.execute('SELECT state FROM rejection_outbox WHERE id=?',(identity('rejection_',[row['id']]),)).fetchone()
        return dict(schemaVersion=1,lockerId=identity('ulocker_',[row['recipient'],row['owner']]),read=bool(row['is_read']),sourceName=row['owner'],id=row['id'],ownerSessionId=row['owner'],recipientId=row['recipient'],
                    definition=definition,createdAt=row['created_at'],revision=row['revision'],
                    status=('rejected' if response.get('outcome')=='reject' else 'responded') if response else 'open',
                    response=response,
                    delivery=json.loads(row['delivery']),rejectionDelivery={'state':notice[0] if notice else 'not_requested'})

    def create(self, request):
        validate('Create',request)
        signature = canonical(request)
        card_id = identity('card_', [request['ownerSessionId'],request['requestKey']])
        with self.db() as db:
            tombstone=db.execute('SELECT * FROM tombstones WHERE id=?',(card_id,)).fetchone()
            if tombstone:
                raise InteractionError('conflict','Card deleted; identity retained')
            row = db.execute('SELECT * FROM cards WHERE id=?',(card_id,)).fetchone()
            if row:
                if row['signature'] != signature:
                    raise InteractionError('conflict','requestKey content conflict')
                return self._view(row,db)
            db.execute('INSERT OR IGNORE INTO user_lockers VALUES(?,?)',(request['ownerSessionId'],request['recipientId']))
            count = db.execute('SELECT count(*) FROM cards WHERE recipient=? AND response IS NULL',(request['recipientId'],)).fetchone()[0]
            if count >= self.capacity:
                raise InteractionError('capacity','User Inbox capacity reached; existing cards retained')
            db.execute('''INSERT INTO cards(id,owner,recipient,request_key,signature,definition,created_at,delivery)
                VALUES(?,?,?,?,?,?,?,?)''', (card_id,request['ownerSessionId'],request['recipientId'],request['requestKey'],
                    signature,canonical(request['card']),now(),canonical({'state':'not_requested'})))
            return self._view(db.execute('SELECT * FROM cards WHERE id=?',(card_id,)).fetchone(),db)

    def _user_id(self, principal):
        if not isinstance(principal,UserPrincipal):
            raise InteractionError('unauthorized','Verified user principal required')
        return principal.id

    def _user_row(self,db,principal,card_id):
        row = db.execute('SELECT * FROM cards WHERE id=? AND recipient=?',(card_id,self._user_id(principal))).fetchone()
        if row is None:
            raise InteractionError('not_found','Card not found for this user')
        return row

    def get_for_user(self,principal,card_id):
        with self.db() as db:
            return self._view(self._user_row(db,principal,card_id),db)

    def list_for_user(self,principal,*,limit=100,after=None):
        if type(limit) is not int or not 1 <= limit <= 500:
            raise InteractionError('invalid','limit must be 1..500')
        user_id=self._user_id(principal)
        with self.db() as db:
            cursor=0
            if after is not None:
                row=db.execute('SELECT rowid FROM cards WHERE id=? AND recipient=?',(after,user_id)).fetchone()
                if row is None:
                    raise InteractionError('not_found','Cursor not found for this user')
                cursor=row[0]
            return [self._view(r,db) for r in db.execute('SELECT * FROM cards WHERE recipient=? AND rowid>? ORDER BY rowid LIMIT ?',
                                                     (user_id,cursor,limit))]


    def get_for_owner(self,owner,card_id):
        with self.db() as db:
            row=db.execute('SELECT * FROM cards WHERE id=? AND owner=?',(card_id,owner)).fetchone()
            if row is None:
                raise InteractionError('not_found','Card not found for this owner')
            return self._view(row,db)

    def event_for_owner(self,owner,card_id):
        self.get_for_owner(owner,card_id)
        with self.db() as db:
            row=db.execute('SELECT event FROM cards WHERE id=? AND owner=?',(card_id,owner)).fetchone()
            return json.loads(row[0]) if row and row[0] else None

    def respond(self,principal,card_id,data,*,allow_empty=False):
        validate('Respond',data)
        user_id=self._user_id(principal)
        supplied={k:data[k] for k in ('answer','choiceId') if k in data}
        with self.db() as db:
            row=self._user_row(db,principal,card_id)
            if row['response']:
                previous=json.loads(row['response'])
                previous_payload={k:previous[k] for k in ('answer','choiceId') if k in previous}
                if previous['requestId']==data['requestId'] and previous_payload==supplied and previous.get('outcome','submit')=='submit':
                    return self._view(row,db)
                raise InteractionError('conflict','Card already responded; response identity/content conflict')
            if row['revision']!=data['expectedRevision']:
                raise InteractionError('conflict','Stale Card revision')
            definition=json.loads(row['definition'])
            if not supplied and not allow_empty:
                raise InteractionError('invalid','Response requires a declared input or choice')
            if 'answer' in supplied:
                if 'answerInput' not in definition:
                    raise InteractionError('invalid','Card does not declare answer input')
                if len(supplied['answer']) > definition['answerInput'].get('maxLength',32000):
                    raise InteractionError('invalid','Answer exceeds declared maxLength')
            choices={c['id']:c for c in definition.get('choices',[])}
            if 'choiceId' in supplied and supplied['choiceId'] not in choices:
                raise InteractionError('invalid','Unknown choice')
            timestamp=now()
            result={'id':identity('response_',[card_id,data['requestId']]),'requestId':data['requestId'],
                    'userId':user_id,'createdAt':timestamp,**supplied}
            payload={'cardId':card_id,'ownerSessionId':row['owner'],'recipientId':row['recipient'],
                     'response':result,'correlation':definition.get('correlation',{})}
            trigger=choices.get(supplied.get('choiceId'),{}).get('triggerRef')
            if trigger:
                payload['triggerRef']=trigger
            event={'id':identity('evt_',[card_id,result['id']]),'type':'pan.interaction.responded','schemaVersion':1,
                   'source':{'type':'core','id':'pan'},'occurredAt':timestamp,'payload':payload}
            validate('Event',event)
            state={'state':'pending' if trigger else 'not_requested'}
            db.execute('''UPDATE cards SET response=?,response_key=?,event=?,delivery=?,revision=revision+1 WHERE id=?''',
                       (canonical(result),data['requestId'],canonical(event),canonical(state),card_id))
            notification_id=identity('submission_',[card_id])
            db.execute('INSERT INTO response_outbox VALUES(?,?,?,?)',
                       (notification_id,row['owner'],canonical({'id':notification_id,'cardId':card_id,
                        'lockerId':identity('ulocker_',[row['recipient'],row['owner']]),
                        'summaryBody':definition.get('summaryBody',definition.get('notification',{}).get('summary',definition['body']))[:200]}),'pending'))
            return self._view(db.execute('SELECT * FROM cards WHERE id=?',(card_id,)).fetchone(),db)

    def _settle(self,card_id,result):
        with self.db() as db:
            row=db.execute('SELECT delivery FROM cards WHERE id=?',(card_id,)).fetchone()
            if row is None:
                # User deletion may race an already claimed Trigger dispatch.
                db.execute('CREATE TABLE IF NOT EXISTS detached_deliveries(id TEXT PRIMARY KEY,recipient TEXT,event TEXT,delivery TEXT)')
                detached=db.execute('SELECT delivery FROM detached_deliveries WHERE id=?',(card_id,)).fetchone()
                if detached and json.loads(detached[0])['state']!='accepted' and not (result['state']=='unavailable' and json.loads(detached[0])['state'] in {'in_flight','unknown'}):
                    db.execute("UPDATE detached_deliveries SET delivery=?,event=CASE WHEN ?='accepted' THEN NULL ELSE event END WHERE id=?",
                               (canonical(result),result['state'],card_id))
                return
            current=json.loads(row[0])['state']
            if current=='accepted' or (result['state']=='unavailable' and current in {'in_flight','unknown'}):
                return
            db.execute('UPDATE cards SET delivery=? WHERE id=?',(canonical(result),card_id))

    async def dispatch(self,principal,card_id,adapter):
        with self.db() as db:
            row=self._user_row(db,principal,card_id)
            state=json.loads(row['delivery'])['state']
            if state in {'not_requested','accepted'}:
                return self._view(row,db)
            event=json.loads(row['event'])
        if adapter is None:
            if state not in {'in_flight','unknown'}:
                self._settle(card_id,{'state':'unavailable'})
            return self.get_for_user(principal,card_id)
        try:
            allowed=await adapter.authorize(json.loads(canonical(event)))
        except Exception as exc:
            raise InteractionError('unavailable','Trigger authorization unavailable') from exc
        if allowed is not True:
            raise InteractionError('forbidden','Trigger authorization rejected')
        with self.db() as db:
            row=self._user_row(db,principal,card_id)
            state=json.loads(row['delivery'])['state']
            if state=='accepted':
                return self._view(row,db)
            reconcile=state in {'in_flight','unknown'}
            if not reconcile:
                db.execute('UPDATE cards SET delivery=? WHERE id=?',(canonical({'state':'in_flight'}),card_id))
        try:
            result=(await adapter.lookup(event['id'])) if reconcile else (await adapter.accept(json.loads(canonical(event))))
            if isinstance(result,dict) and result.get('status')=='accepted' and isinstance(result.get('receiptId'),str) and result['receiptId']:
                settled={'state':'accepted','receiptId':result['receiptId']}
                validate('Delivery',settled)
            elif not reconcile and isinstance(result,dict) and result.get('status')=='rejected':
                settled={'state':'rejected'}
            else:
                settled={'state':'unknown'}
        except Exception:
            settled={'state':'unknown'}
        self._settle(card_id,settled)
        return self.get_for_user(principal,card_id)
    def mark_read(self,principal,card_id,value=True):
        if type(value) is not bool:
            raise InteractionError('invalid','read must be boolean')
        with self.db() as db:
            self._user_row(db,principal,card_id)
            db.execute('UPDATE cards SET is_read=? WHERE id=?',(int(value),card_id))
            return self._view(self._user_row(db,principal,card_id),db)

    def reject(self,principal,card_id,data):
        validate('Reject',data)
        with self.db() as db:
            row=self._user_row(db,principal,card_id)
            if row['response']:
                previous=json.loads(row['response'])
                if previous.get('outcome')=='reject' and previous['requestId']==data['requestId']:
                    return self._view(row,db)
                raise InteractionError('conflict','Card already completed')
            if row['revision']!=data['expectedRevision']:
                raise InteractionError('conflict','Stale Card revision')
            result={'id':identity('response_',[card_id,data['requestId']]),'requestId':data['requestId'],
                    'userId':self._user_id(principal),'createdAt':now(),'outcome':'reject'}
            card=self._view(row,db)
            event_id=identity('rejection_',[card_id])
            payload={'id':event_id,'cardId':card_id,'lockerId':card['lockerId'],
                     'summaryBody':card['definition']['summaryBody'][:200]}
            db.execute('INSERT INTO rejection_outbox VALUES(?,?,?,?)',
                       (event_id,row['owner'],canonical(payload),'pending'))
            db.execute('UPDATE cards SET response=?,response_key=?,revision=revision+1,is_read=1 WHERE id=?',
                       (canonical(result),data['requestId'],card_id))
            return self._view(self._user_row(db,principal,card_id),db)

    def delete(self,principal,card_id):
        import hashlib
        user_id=self._user_id(principal)
        with self.db() as db:
            old=db.execute('SELECT recipient FROM tombstones WHERE id=?',(card_id,)).fetchone()
            if old and old[0]==user_id:
                return {'deleted':True,'id':card_id}
            row=self._user_row(db,principal,card_id)
            if not row['response']:
                raise InteractionError('conflict','Only completed Cards may be deleted')
            # The producer keeps its response receipt, not the user Card body.
            db.execute('INSERT OR IGNORE INTO owner_receipts VALUES(?,?,?,?,?)',
                       (card_id,row['owner'],user_id,row['response'],row['event']))
            # Trigger intent must survive user-side deletion. This is an internal
            # delivery record, not a second user container or consumption cursor.
            db.execute('CREATE TABLE IF NOT EXISTS detached_deliveries(id TEXT PRIMARY KEY,recipient TEXT,event TEXT,delivery TEXT)')
            if json.loads(row['delivery'])['state'] not in {'accepted','not_requested','rejected'}:
                db.execute('INSERT OR IGNORE INTO detached_deliveries VALUES(?,?,?,?)',
                           (card_id,user_id,row['event'],row['delivery']))
            db.execute('INSERT INTO tombstones VALUES(?,?,?,?)',
                       (card_id,row['owner'],user_id,hashlib.sha256(row['signature'].encode()).hexdigest()))
            db.execute('DELETE FROM cards WHERE id=?',(card_id,))
            return {'deleted':True,'id':card_id}

    async def deliver_rejections(self,queue):
        await self._deliver_notices(queue, 'rejection_outbox', 'rejected')

    async def deliver_responses(self,queue):
        await self._deliver_notices(queue, 'response_outbox', 'submitted')

    async def _deliver_notices(self,queue,table,outcome):
        assert table in {'rejection_outbox','response_outbox'}
        # Stable Queue IDs and receipts bridge the two durable files. Unknown
        # claims reconcile by ID only; absence is never proof of non-delivery.
        with self.db() as db:
            ids=[r[0] for r in db.execute(f"SELECT id FROM {table} WHERE state!='accepted'")]
        for event_id in ids:
            with self.db() as db:
                row=db.execute(f'SELECT * FROM {table} WHERE id=?',(event_id,)).fetchone()
                if row['state']=='accepted':
                    continue
                reconcile=row['state'] in {'in_flight','unknown'}
                if not reconcile:
                    db.execute(f"UPDATE {table} SET state='in_flight' WHERE id=?",(event_id,))
                owner=row['owner']; payload=json.loads(row['payload'])
            qid=identity('q_',[event_id])
            try:
                receipt=await queue.lookup(owner,qid) if reconcile else await queue.put(owner,qid,{
                    'id':event_id,'lockerId':payload['lockerId'],
                    'source':{'pluginId':'interaction','scope':'user','peerId':owner},
                    'content':{'text':'Interaction '+outcome+': '+payload['summaryBody'],
                               'envelope':{'type':'interaction.'+outcome,**payload}}})
                state='accepted' if receipt and receipt.get('status')=='accepted' else (
                    'pending' if not reconcile and receipt and receipt.get('status')=='rejected' else 'unknown')
            except Exception:
                state='unknown'
            with self.db() as db:
                db.execute(f"UPDATE {table} SET state=?,payload=CASE WHEN ?='accepted' THEN NULL ELSE payload END WHERE id=? AND state!='accepted'",
                           (state,state,event_id))
    async def recover_detached(self,adapter):
        if adapter is None:
            return
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS detached_deliveries(id TEXT PRIMARY KEY,recipient TEXT,event TEXT,delivery TEXT)')
            ids=[r[0] for r in db.execute("SELECT id FROM detached_deliveries WHERE event IS NOT NULL")]
        for card_id in ids:
            with self.db() as db:
                row=db.execute('SELECT * FROM detached_deliveries WHERE id=?',(card_id,)).fetchone()
                if row['event'] is None:
                    continue
                event=json.loads(row['event'])
            try:
                if await adapter.authorize(json.loads(canonical(event))) is not True:
                    continue
            except Exception:
                continue
            with self.db() as db:
                row=db.execute('SELECT * FROM detached_deliveries WHERE id=?',(card_id,)).fetchone()
                if row['event'] is None:
                    continue
                state=json.loads(row['delivery'])['state']
                reconcile=state in {'in_flight','unknown'}
                db.execute('UPDATE detached_deliveries SET delivery=? WHERE id=?',
                           (canonical({'state':'in_flight'}),card_id))
            try:
                result=await adapter.lookup(event['id']) if reconcile else await adapter.accept(json.loads(canonical(event)))
                if isinstance(result,dict) and result.get('status')=='accepted' and result.get('receiptId'):
                    settled={'state':'accepted','receiptId':result['receiptId']}
                    validate('Delivery',settled)
                elif not reconcile and isinstance(result,dict) and result.get('status')=='rejected':
                    settled={'state':'rejected'}
                else:
                    settled={'state':'unknown'}
            except Exception:
                settled={'state':'unknown'}
            with self.db() as db:
                db.execute("UPDATE detached_deliveries SET delivery=?,event=CASE WHEN ?='accepted' THEN NULL ELSE event END WHERE id=? AND json_extract(delivery,'$.state')!='accepted'",
                           (canonical(settled),settled['state'],card_id))


    def receipt_for_owner(self,owner,card_id):
        with self.db() as db:
            row=db.execute('SELECT owner,recipient,response,event FROM cards WHERE id=? AND owner=?',(card_id,owner)).fetchone()
            if row is None:
                row=db.execute('SELECT owner,recipient,response,event FROM owner_receipts WHERE id=? AND owner=?',(card_id,owner)).fetchone()
            if row is None:
                raise InteractionError('not_found','Receipt not found for this owner')
            return {'cardId':card_id,'ownerSessionId':row['owner'],'recipientId':row['recipient'],
                    'response':json.loads(row['response']) if row['response'] else None,
                    'event':json.loads(row['event']) if row['event'] else None}
