"""User -> own Session cards. Independent storage; no interaction response effects."""
import json
import time
from datetime import datetime, timezone

from .contracts import InteractionError, canonical, identity, validate

REMINDER = '你还有未回答的用户卡片，请用 `reminding_card(operation="list_pending")` 查询；会话创建的卡片走 `interaction_card`。如果目前仍无法回答，请忽略这条消息并朝着能回答的方向工作，但不要为了回答而随意回答'


def timestamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


class UserCardStore:
    def __init__(self, store, *, clock=time.time):
        self.store = store
        self.clock = clock
        with self.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS user_cards(
                seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE NOT NULL,owner TEXT NOT NULL,
                recipient TEXT NOT NULL,signature TEXT NOT NULL,kind TEXT NOT NULL,body TEXT NOT NULL,
                attachments TEXT NOT NULL,created_at TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 0,
                answer TEXT,answered_at TEXT,is_read INTEGER NOT NULL DEFAULT 1)''')
            columns = {r[1] for r in db.execute('PRAGMA table_info(user_cards)')}
            if 'parent_type' not in columns:
                db.execute('ALTER TABLE user_cards ADD COLUMN parent_type TEXT')
                db.execute('ALTER TABLE user_cards ADD COLUMN parent_id TEXT')
            db.execute('CREATE INDEX IF NOT EXISTS user_cards_parent ON user_cards(parent_type,parent_id)')
            db.execute('CREATE INDEX IF NOT EXISTS user_cards_pending ON user_cards(owner,answered_at)')
            db.execute('CREATE TABLE IF NOT EXISTS user_card_deleted(id TEXT PRIMARY KEY)')
            db.execute('CREATE TABLE IF NOT EXISTS user_card_reminders(owner TEXT PRIMARY KEY,last_at REAL NOT NULL)')
            db.execute('''CREATE TABLE IF NOT EXISTS user_card_outbox(
                id TEXT PRIMARY KEY,owner TEXT NOT NULL,body TEXT NOT NULL)''')
            db.execute('CREATE TABLE IF NOT EXISTS user_card_settings(id INTEGER PRIMARY KEY,minutes INTEGER NOT NULL)')
            db.execute('INSERT OR IGNORE INTO user_card_settings VALUES(1,10)')

    def db(self):
        return self.store.db()

    @staticmethod
    def view(row):
        return dict(id=row['id'],direction='user',kind=row['kind'],ownerSessionId=row['owner'],
                    lockerId=identity('ulocker_', [row['recipient'],row['owner']]),
                    parent=({'type': row['parent_type'], 'id': row['parent_id']} if row['parent_id'] else None),
                    createdAt=row['created_at'],answeredAt=row['answered_at'],body=row['body'],
                    attachments=json.loads(row['attachments']),revision=row['revision'],
                    status='answered' if row['answered_at'] else 'pending',answer=row['answer'],read=bool(row['is_read']))

    def create(self, principal, data):
        if set(data)-{'parent'} != {'ownerSessionId','requestKey','kind','body','attachments'}:
            raise InteractionError('invalid','Invalid create fields')
        for key in ('ownerSessionId','requestKey'):
            validate('Id',data[key])
        if not isinstance(data['kind'],str) or data['kind'] not in ('question','task') or not isinstance(data['body'],str) or len(data['body']) > 100000:
            raise InteractionError('invalid','Card requires question/task and text body (max 100000)')
        attachments=data['attachments']
        if not isinstance(attachments,list) or len(attachments)>100:
            raise InteractionError('invalid','Invalid attachments')
        if not data['body'].strip() and not attachments:
            raise InteractionError('invalid','Card requires body or attachments')
        for a in attachments:
            if not isinstance(a,dict) or not {'id','name'} <= set(a) or set(a)-{'id','name','mimeType','size'}:
                raise InteractionError('invalid','Invalid attachment')
            validate('Id',a['id'])
            if not isinstance(a['name'],str) or len(a['name'])>1000:
                raise InteractionError('invalid','Invalid attachment name')
            if 'mimeType' in a and not isinstance(a['mimeType'],str):
                raise InteractionError('invalid','Invalid mimeType')
            if 'size' in a and (type(a['size']) is not int or a['size']<0):
                raise InteractionError('invalid','Invalid size')
        owner=data['ownerSessionId']; cid=identity('ucard_', [principal.id,owner,data['requestKey']])
        parent = data.get('parent')
        from .threads import validate_parent
        sig=canonical(data); at=self.clock()
        with self.db() as db:
            if db.execute('SELECT 1 FROM user_card_deleted WHERE id=?',(cid,)).fetchone():
                raise InteractionError('conflict','Card deleted')
            old=db.execute('SELECT * FROM user_cards WHERE id=?',(cid,)).fetchone()
            if old:
                if old['signature']!=sig:
                    raise InteractionError('conflict','requestKey content conflict')
                return self.view(old)
            validate_parent(db, parent, cid, principal.id, owner)
            if parent and db.execute(
                    'SELECT 1 FROM user_cards WHERE parent_type=? AND parent_id=? LIMIT 1',
                    (parent['type'], parent['id'])).fetchone():
                raise InteractionError('conflict', 'Parent already has an attached reminder card')
            if db.execute('SELECT count(*) FROM user_cards WHERE recipient=?',(principal.id,)).fetchone()[0]>=1000:
                raise InteractionError('capacity','User card capacity reached')
            db.execute('INSERT INTO user_cards(id,owner,recipient,signature,kind,body,attachments,created_at,parent_type,parent_id) VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (cid,owner,principal.id,sig,data['kind'],data['body'],canonical(attachments),timestamp(at),
                        parent['type'] if parent else None,parent['id'] if parent else None))
            db.execute('INSERT OR IGNORE INTO user_lockers(owner,recipient) VALUES(?,?)',(owner,principal.id))
            db.execute('INSERT OR REPLACE INTO user_card_reminders VALUES(?,?)',(owner,at))
            db.execute('INSERT INTO user_card_outbox VALUES(?,?,?)',(cid+':created',owner,f'{REMINDER}\n\n{data["body"]}\n\nCard id: {cid}'))
            return self.view(db.execute('SELECT * FROM user_cards WHERE id=?',(cid,)).fetchone())

    def list(self, *, principal=None, owner=None):
        with self.db() as db:
            if owner is not None:
                rows=db.execute('SELECT * FROM user_cards WHERE owner=? AND answered_at IS NULL ORDER BY seq',(owner,))
            else:
                rows=db.execute('SELECT * FROM user_cards WHERE recipient=? ORDER BY seq',(principal.id,))
            return [self.view(row) for row in rows]

    def get(self, db, cid, *, principal=None, owner=None):
        row=db.execute('SELECT * FROM user_cards WHERE id=? AND '+('owner' if owner is not None else 'recipient')+'=?',
                       (cid,owner if owner is not None else principal.id)).fetchone()
        if row is None:
            raise InteractionError('not_found','Card unavailable in this scope')
        return row

    def stop_if_done(self, db, owner):
        if not db.execute('SELECT 1 FROM user_cards WHERE owner=? AND answered_at IS NULL',(owner,)).fetchone():
            db.execute('DELETE FROM user_card_reminders WHERE owner=?',(owner,))
            db.execute('DELETE FROM user_card_outbox WHERE owner=?',(owner,))

    def answer(self, owner, data):
        if set(data)!={'cardId','body','expectedRevision'} or type(data['expectedRevision']) is not int or not isinstance(data['body'],str) or len(data['body'])>100000:
            raise InteractionError('invalid','answer requires cardId, body and integer expectedRevision')
        validate('Id',data['cardId'])
        with self.db() as db:
            row=self.get(db,data['cardId'],owner=owner)
            if row['answered_at'] or row['revision']!=data['expectedRevision']:
                raise InteractionError('conflict','Card already answered or revision changed')
            if row['kind']=='question' and not data['body'].strip():
                raise InteractionError('invalid','Question requires a nonempty answer')
            changed=db.execute('UPDATE user_cards SET answer=?,answered_at=?,is_read=0,revision=revision+1 WHERE id=? AND revision=? AND answered_at IS NULL',
                              (data['body'],timestamp(self.clock()),row['id'],data['expectedRevision'])).rowcount
            if changed!=1:
                raise InteractionError('conflict','Revision changed')
            self.stop_if_done(db,owner)
            return self.view(self.get(db,row['id'],owner=owner))

    def delete(self, principal, cid):
        with self.db() as db:
            row=self.get(db,cid,principal=principal)
            from .threads import delete_descendants
            delete_descendants(db, 'user_card', cid, principal.id)
            db.execute('INSERT INTO user_card_deleted VALUES(?)',(cid,))
            db.execute('DELETE FROM user_cards WHERE id=?',(cid,))
            db.execute('DELETE FROM user_card_outbox WHERE id=?',(cid+':created',))
            self.stop_if_done(db,row['owner'])
        return {'deleted':True}

    def read(self, principal, cid, read):
        if type(read) is not bool:
            raise InteractionError('invalid','read must be boolean')
        with self.db() as db:
            row=self.get(db,cid,principal=principal)
            if not row['answered_at']:
                raise InteractionError('conflict','Unanswered cards have no read status')
            db.execute('UPDATE user_cards SET is_read=?,revision=revision+1 WHERE id=?',(int(read),cid))
            return self.view(self.get(db,cid,principal=principal))

    def settings(self, minutes=None):
        if minutes is not None and (type(minutes) is not int or not 1<=minutes<=1440):
            raise InteractionError('invalid','minutes must be an integer from 1 to 1440')
        with self.db() as db:
            if minutes is not None:
                db.execute('UPDATE user_card_settings SET minutes=? WHERE id=1',(minutes,))
            return {'minutes':db.execute('SELECT minutes FROM user_card_settings WHERE id=1').fetchone()[0]}

    def reminders(self):
        """Persist one due intent per owner, coalescing elapsed cycles after downtime."""
        at=self.clock()
        with self.db() as db:
            interval=db.execute('SELECT minutes*60 FROM user_card_settings WHERE id=1').fetchone()[0]
            for row in db.execute('SELECT * FROM user_card_reminders').fetchall():
                owner=row['owner']
                if at-row['last_at']>=interval:
                    # An unacknowledged notification is retried by original identity.
                    if not db.execute('SELECT 1 FROM user_card_outbox WHERE owner=?',(owner,)).fetchone():
                        qid=identity('ucremind_', [owner,row['last_at']])
                        db.execute('INSERT INTO user_card_outbox VALUES(?,?,?)',(qid,owner,REMINDER))
                    db.execute('UPDATE user_card_reminders SET last_at=? WHERE owner=?',(at,owner))
            return [dict(r) for r in db.execute('SELECT * FROM user_card_outbox')]

    def acknowledge(self, qid):
        with self.db() as db:
            db.execute('DELETE FROM user_card_outbox WHERE id=?',(qid,))

    def drop_reminders(self, owner):
        """Forget reminder state only when the owner Session is definitively gone."""
        with self.db() as db:
            db.execute('DELETE FROM user_card_reminders WHERE owner=?',(owner,))
            db.execute('DELETE FROM user_card_outbox WHERE owner=?',(owner,))

    def delay(self):
        with self.db() as db:
            if db.execute('SELECT 1 FROM user_card_outbox').fetchone():
                return 60  # failed admission: bounded retry, no busy loop
            row=db.execute('SELECT min(last_at) FROM user_card_reminders').fetchone()
            if row[0] is None:
                return None
            interval=db.execute('SELECT minutes*60 FROM user_card_settings WHERE id=1').fetchone()[0]
            return max(0,row[0]+interval-self.clock())


def get_store():
    from .api import get_store as interaction_store
    return UserCardStore(interaction_store())


async def enqueue_reminder(owner, qid, body):
    """Admit a normal queued Notification, preserving Illegal/Pause gates."""
    from packages.core import worker
    target=worker._get_session_shallow(owner)
    if target is None:
        # A deleted Session cannot ever consume this reminder.  Clear only its
        # reminder bookkeeping; do not touch other card data or queue state.
        get_store().drop_reminders(owner)
        return True
    if worker._shutdown_started:
        return False
    async with worker.queue_lock(owner):
        if any(r.get('id')==qid for r in [*target.queue_pending,*target.queue_delivery_ledger.values()]):
            return True
        item=await worker._append_new_queue_item(target,dict(id=qid,queueItemId=qid,type='notice',kind='notice',
            source='system',status='notice',noticeKind='user_card_reminder',result=body,
            sessionId=owner,revision=1,createdAt=time.time(),deliveryState='queued',dispatchState='queued'))
    await worker._bcast(dict(type='queue.item_added',sessionId=owner,queueItemId=qid,
                             queueRevision=target.queue_revision,item=dict(item)))
    # Reminder admission follows ordinary queue delivery.  _wake_worker keeps
    # Pause and Illegal gates; it never force-clears an Illegal lock.
    await worker._wake_worker(owner, auto_spawn=True)
    return True


class ReminderScheduler:
    def __init__(self, store, enqueue=enqueue_reminder):
        import asyncio
        self.store=store; self.enqueue=enqueue
        self.changed=asyncio.Event(); self.lock=asyncio.Lock()

    async def tick(self):
        for row in self.store.reminders():
            if await self.enqueue(row['owner'],row['id'],row['body']):
                self.store.acknowledge(row['id'])

    async def run(self):
        import asyncio
        import logging
        while True:
            self.changed.clear()
            try:
                async with self.lock:
                    await self.tick()
                    delay=self.store.delay()
            except Exception:
                logging.getLogger(__name__).exception('User card reminder admission failed')
                delay=60
            try:
                await asyncio.wait_for(self.changed.wait(),timeout=delay)
            except asyncio.TimeoutError:
                pass
