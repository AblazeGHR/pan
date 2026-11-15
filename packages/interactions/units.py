"""Unified projection and atomic batch persistence over the original Card ledger."""
import json

from .contracts import InteractionError, canonical, identity, validate, validate_answers


def questions(definition):
    if definition.get('schemaVersion', 1) == 3:
        return definition['units']
    if definition.get('schemaVersion') == 2:
        return definition['questions']
    if definition.get('choices'):
        return [{'id': 'legacy', 'prompt': definition['title'], 'type': 'single_choice',
                 'choices': definition['choices']}]
    if 'answerInput' in definition:
        return [{'id': 'legacy', 'prompt': definition['title'], 'type': 'text'}]
    return []


def project(row, db):
    definition = json.loads(row['definition'])
    original = json.loads(row['response']) if row['response'] else None
    batches = [json.loads(r[0]) for r in db.execute(
        'SELECT document FROM unit_submissions WHERE card_id=? ORDER BY rowid', (row['id'],))]
    answers = {}
    for batch in batches:
        answers.update(batch['answers'])
    # Old terminal facts are display adapters only. Never write new intents.
    if original and not batches:
        answers = original.get('answers', {})
        if original.get('outcome') == 'reject' and not answers:
            answers = {q['id']: {'rejected': True, **({'btw': original['answer']} if original.get('answer') else {})} for q in questions(definition)}
        if not answers and questions(definition):
            answers = {'legacy': ({'rejected': True, 'btw': original.get('answer', '')}
                if original.get('outcome') == 'reject' else
                {'choiceId': original['choiceId'], 'btw': original.get('answer', '')}
                if original.get('choiceId') else {'text': original.get('answer', '')})}
    events = [dict(event=json.loads(r[0]), delivery=json.loads(r[1])) for r in db.execute(
        'SELECT event,delivery FROM question_events WHERE card_id=?', (row['id'],))]
    result = []
    for q in questions(definition):
        answer = answers.get(q['id'])
        terminal = bool(original) and not batches
        receipts = [{'eventId': e['event']['id'], **e['delivery']} for e in events
                    if e['event']['payload'].get('questionId') == q['id']]
        if terminal and definition.get('schemaVersion', 1) == 1:
            receipts = [json.loads(row['delivery'])]
        result.append({**q, 'state': 'rejected' if answer and answer.get('rejected') else
                       'answered' if answer else 'processed' if terminal else 'pending',
                       'answer': answer, 'executions': receipts})
    done = sum(q['state'] != 'pending' for q in result)
    return {'units': result, 'unitStatus': 'processed' if done == len(result) else
            'partial' if done else 'pending', 'submissions': [b['submissionId'] for b in batches]}


def submit(store, principal, card_id, data):
    from .store import now
    answers = data.get('unitAnswers', data.get('answers'))
    validate('UnitAnswers', answers)
    if set(data) - {'requestId', 'expectedRevision', 'unitAnswers', 'answers'} or ('unitAnswers' in data and 'answers' in data):
        raise InteractionError('invalid', 'Only one unit answer map is allowed')
    uid = store._user_id(principal)
    with store.db() as db:
        row = store._user_row(db, principal, card_id)
        sid = identity('submission_', [card_id, data['requestId']])
        previous = db.execute('SELECT document FROM unit_submissions WHERE id=?', (sid,)).fetchone()
        if previous:
            if json.loads(previous[0])['answers'] != answers:
                raise InteractionError('conflict', 'Submission identity/content conflict')
            return store._view(row, db)
        if row['no_answer_at'] or row['revision'] != data['expectedRevision']:
            raise InteractionError('conflict', 'Card terminal or stale revision')
        view = project(row, db)
        pending = {q['id']: q for q in view['units'] if q['state'] == 'pending'}
        if not set(answers) <= pending.keys():
            raise InteractionError('conflict', 'Submitted units are locked or unknown')
        definition = json.loads(row['definition'])
        subset = [q for q in questions(definition) if q['id'] in answers]
        validate_answers({'questions': subset}, answers)
        timestamp = now()
        batch = {'submissionId': sid, 'cardId': card_id, 'requestId': data['requestId'],
                 'createdAt': timestamp, 'answers': answers,
                 'units': [{**q, 'answer': answers[q['id']]} for q in subset]}
        result = {'id': identity('response_', [card_id, data['requestId']]),
                  'requestId': data['requestId'], 'userId': uid, 'createdAt': timestamp,
                  'answers': answers}
        bindings = json.loads(row['bindings'])
        for q in subset:
            answer = answers[q['id']]
            if answer.get('rejected'):
                continue
            ids = answer.get('choiceIds', [answer.get('choiceId')])
            for choice in q.get('choices', []):
                if choice['id'] not in ids or not (choice.get('triggerRef') or choice.get('actionRef')):
                    continue
                binding = (bindings if definition.get('schemaVersion', 1) == 1 else bindings.get(q['id'], {})).get(choice['id'])
                if not binding:
                    raise InteractionError('incomplete', 'Trusted execution binding required')
                payload = {'cardId': card_id, 'ownerSessionId': row['owner'], 'recipientId': row['recipient'],
                           'response': result, 'correlation': definition.get('correlation', {}),
                           'questionId': q['id'], 'choiceId': choice['id'], 'executionBinding': binding,
                           **{k: choice[k] for k in ('triggerRef', 'actionRef') if k in choice}}
                event = {'id': identity('evt_', [sid, q['id'], choice['id']]), 'type': 'pan.interaction.responded',
                         'schemaVersion': 1, 'source': {'type': 'core', 'id': 'pan'},
                         'occurredAt': timestamp, 'payload': payload}
                validate('Event', event)
                db.execute('INSERT INTO question_events VALUES(?,?,?,?)',
                           (event['id'], card_id, canonical(event), canonical({'state': 'pending'})))
        db.execute('INSERT INTO unit_submissions VALUES(?,?,?,?)', (sid, card_id, row['owner'], canonical(batch)))
        # Keep the original response column as the aggregate user fact, never as a new execution input.
        merged = {}
        for b in db.execute('SELECT document FROM unit_submissions WHERE card_id=?', (card_id,)):
            merged.update(json.loads(b[0])['answers'])
        result['answers'] = merged
        result['outcome'] = 'reject' if len(merged) == len(view['units']) and all(a.get('rejected') for a in merged.values()) else 'submit'
        if definition.get('schemaVersion', 1) == 1:
            legacy = merged['legacy']
            if legacy.get('choiceId'):
                result['choiceId'] = legacy['choiceId']
            if 'text' in legacy or legacy.get('btw'):
                result['answer'] = legacy.get('text', legacy.get('btw', ''))
        has_events = db.execute('SELECT 1 FROM question_events WHERE card_id=?', (card_id,)).fetchone()
        db.execute('UPDATE cards SET response=?,response_key=?,revision=revision+1,is_read=1,delivery=? WHERE id=?',
                   (canonical(result), data['requestId'], canonical({'state': 'pending' if has_events else 'not_requested'}), card_id))
        notice = {'id': sid, 'submissionId': sid, 'cardId': card_id, 'title': definition['title'],
                  'count': len(answers), 'rejectCount': sum(a.get('rejected') is True for a in answers.values()),
                  'responseKind': 'submit', 'lockerId': identity('ulocker_', [row['recipient'], row['owner']]),
                  'summaryBody': definition.get('summaryBody', definition.get('body', ''))[:200],
                  'readCommand': f'interaction_card(operation="submission", card_id="{card_id}", submission_id="{sid}")'}
        if len(answers) <= 3:
            notice.update(answers=answers, units=batch['units'])
        db.execute('INSERT INTO response_outbox VALUES(?,?,?,?)', (sid, row['owner'], canonical(notice), 'pending'))
        return store._view(store._user_row(db, principal, card_id), db)


def read_submission(store, owner, card_id, submission_id):
    with store.db() as db:
        row = db.execute('SELECT document FROM unit_submissions WHERE id=? AND card_id=? AND owner=?',
                         (submission_id, card_id, owner)).fetchone()
        if row is None:
            raise InteractionError('not_found', 'Submission not found for this owner')
        batch = json.loads(row[0])
        live = [dict(eventId=r[0], **json.loads(r[1])) for r in db.execute(
            "SELECT id,delivery FROM question_events WHERE card_id=? AND json_extract(event,'$.payload.response.requestId')=?",
            (card_id, batch['requestId']))]
        batch['executions'] = live or batch.get('executions', [])
        for execution in batch['executions']:
            detached = db.execute("SELECT delivery FROM detached_deliveries WHERE id=?", (execution['eventId'],)).fetchone() if db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='detached_deliveries'").fetchone() else None
            if detached:
                execution.update(json.loads(detached[0]))
        return batch
