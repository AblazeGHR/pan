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

    def _view(self, row):
        return dict(schemaVersion=1,id=row['id'],ownerSessionId=row['owner'],recipientId=row['recipient'],
                    definition=json.loads(row['definition']),createdAt=row['created_at'],revision=row['revision'],
                    status='responded' if row['response'] else 'open',
                    response=json.loads(row['response']) if row['response'] else None,
                    delivery=json.loads(row['delivery']))

    def create(self, request):
        validate('Create',request)
        signature = canonical(request)
        card_id = identity('card_', [request['ownerSessionId'],request['requestKey']])
        with self.db() as db:
            row = db.execute('SELECT * FROM cards WHERE id=?',(card_id,)).fetchone()
            if row:
                if row['signature'] != signature:
                    raise InteractionError('conflict','requestKey content conflict')
                return self._view(row)
            count = db.execute('SELECT count(*) FROM cards WHERE recipient=? AND response IS NULL',(request['recipientId'],)).fetchone()[0]
            if count >= self.capacity:
                raise InteractionError('capacity','User Inbox capacity reached; existing cards retained')
            db.execute('''INSERT INTO cards(id,owner,recipient,request_key,signature,definition,created_at,delivery)
                VALUES(?,?,?,?,?,?,?,?)''', (card_id,request['ownerSessionId'],request['recipientId'],request['requestKey'],
                    signature,canonical(request['card']),now(),canonical({'state':'not_requested'})))
            return self._view(db.execute('SELECT * FROM cards WHERE id=?',(card_id,)).fetchone())

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
            return self._view(self._user_row(db,principal,card_id))

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
            return [self._view(r) for r in db.execute('SELECT * FROM cards WHERE recipient=? AND rowid>? ORDER BY rowid LIMIT ?',
                                                     (user_id,cursor,limit))]


    def get_for_owner(self,owner,card_id):
        with self.db() as db:
            row=db.execute('SELECT * FROM cards WHERE id=? AND owner=?',(card_id,owner)).fetchone()
            if row is None:
                raise InteractionError('not_found','Card not found for this owner')
            return self._view(row)

    def event_for_owner(self,owner,card_id):
        self.get_for_owner(owner,card_id)
        with self.db() as db:
            row=db.execute('SELECT event FROM cards WHERE id=? AND owner=?',(card_id,owner)).fetchone()
            return json.loads(row[0]) if row and row[0] else None

    def respond(self,principal,card_id,data):
        validate('Respond',data)
        user_id=self._user_id(principal)
        supplied={k:data[k] for k in ('answer','choiceId') if k in data}
        with self.db() as db:
            row=self._user_row(db,principal,card_id)
            if row['response']:
                previous=json.loads(row['response'])
                previous_payload={k:previous[k] for k in ('answer','choiceId') if k in previous}
                if previous['requestId']==data['requestId'] and previous_payload==supplied:
                    return self._view(row)
                raise InteractionError('conflict','Card already responded; response identity/content conflict')
            if row['revision']!=data['expectedRevision']:
                raise InteractionError('conflict','Stale Card revision')
            definition=json.loads(row['definition'])
            if not supplied:
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
            return self._view(db.execute('SELECT * FROM cards WHERE id=?',(card_id,)).fetchone())

    def _settle(self,card_id,result):
        with self.db() as db:
            row=db.execute('SELECT delivery FROM cards WHERE id=?',(card_id,)).fetchone()
            current=json.loads(row[0])['state']
            if current=='accepted' or (result['state']=='unavailable' and current in {'in_flight','unknown'}):
                return
            db.execute('UPDATE cards SET delivery=? WHERE id=?',(canonical(result),card_id))

    async def dispatch(self,principal,card_id,adapter):
        with self.db() as db:
            row=self._user_row(db,principal,card_id)
            state=json.loads(row['delivery'])['state']
            if state in {'not_requested','accepted'}:
                return self._view(row)
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
                return self._view(row)
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
