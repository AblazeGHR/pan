"""Immutable reminding-card parent links; all operations share the caller's transaction."""
from .contracts import InteractionError, validate


TABLES = {'locker_message': 'locker_messages', 'interaction_card': 'cards', 'user_card': 'user_cards'}


def validate_parent(db, parent, cid, recipient, owner):
    if parent is None:
        return
    if (not isinstance(parent, dict) or set(parent) != {'type', 'id'}
            or not isinstance(parent['type'], str) or parent['type'] not in TABLES):
        raise InteractionError('invalid', 'Invalid parent type or fields')
    validate('Id', parent['id'])
    seen = {('user_card', cid)}
    current = parent
    while current:
        key = (current['type'], current['id'])
        if key in seen:
            raise InteractionError('invalid', 'Parent cycle is forbidden')
        seen.add(key)
        table = TABLES[current['type']]
        row = db.execute(f'SELECT * FROM {table} WHERE id=? AND recipient=? AND owner=?',
                         (current['id'], recipient, owner)).fetchone()
        if row is None:
            raise InteractionError('not_found', 'Parent unavailable in this Locker')
        current = ({'type': row['parent_type'], 'id': row['parent_id']}
                   if table == 'user_cards' and row['parent_id'] else None)


def delete_descendants(db, parent_type, parent_id, recipient):
    # Interaction storage can be used before the reminding-card schema is opened.
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='user_cards'").fetchone():
        return
    if 'parent_id' not in {r[1] for r in db.execute('PRAGMA table_info(user_cards)')}:
        return
    pending = [(parent_type, parent_id)]
    seen = set()
    owners = set()
    while pending:
        kind, pid = pending.pop()
        if (kind, pid) in seen:
            continue
        seen.add((kind, pid))
        children = db.execute('SELECT id,owner FROM user_cards WHERE parent_type=? AND parent_id=? AND recipient=?',
                              (kind, pid, recipient)).fetchall()
        for child in children:
            cid = child['id']
            pending.append(('user_card', cid))
            owners.add(child['owner'])
            db.execute('INSERT OR IGNORE INTO user_card_deleted VALUES(?)', (cid,))
            db.execute('DELETE FROM user_card_outbox WHERE id=?', (cid + ':created',))
            db.execute('DELETE FROM user_cards WHERE id=?', (cid,))
    for owner in owners:
        if not db.execute('SELECT 1 FROM user_cards WHERE owner=? AND answered_at IS NULL', (owner,)).fetchone():
            db.execute('DELETE FROM user_card_reminders WHERE owner=?', (owner,))
            db.execute('DELETE FROM user_card_outbox WHERE owner=?', (owner,))
