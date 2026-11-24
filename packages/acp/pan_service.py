"""Bind the scoped ACP state machine to Pan's canonical Session/queue APIs."""
from __future__ import annotations

import secrets

from .service import Service
from .projection import Unconfirmed


def receipt_status(session, turn_id, queue_id=None):
    """Return only the original queue/terminal identity, never result text."""
    ledger = getattr(session, 'queue_delivery_ledger', {}) or {}
    matches = [(qid, row) for qid, row in ledger.items() if isinstance(row, dict)
               and row.get('clientMessageId') == 'acp:' + turn_id
               and (queue_id is None or qid == queue_id)]
    if len(matches) != 1:
        return {'state': 'unknown'}
    qid, row = matches[0]
    if row.get('deliveryState') == 'deleted':
        return {'state': 'withdrawn', 'queueItemId': qid}
    identity = (row.get('workerId'), row.get('generation'), row.get('executionSeq'))
    if (not isinstance(identity[0], str) or not identity[0]
            or type(identity[1]) is not int or identity[1] < 0
            or type(identity[2]) is not int or identity[2] < 1):
        return {'state': 'unknown'}
    results = [item for item in (getattr(session, 'terminal_results', []) or [])
               if isinstance(item, dict)
               and (item.get('workerId'), item.get('generation'), item.get('executionSeq')) == identity
               and isinstance(item.get('terminalKey'), str) and item.get('terminalKey')
               and type(item.get('resultCursor')) is int and item['resultCursor'] > 0
               and item.get('status') in ('done', 'error', 'cancelled')]
    if len(results) != 1:
        return {'state': 'unknown'}
    terminal = results[0]
    return {'state': 'terminal', 'status': terminal['status'], 'queueItemId': qid,
            'workerId': identity[0], 'generation': identity[1], 'executionSeq': identity[2],
            'terminalKey': terminal['terminalKey'], 'resultCursor': terminal['resultCursor']}


def bind(server, profile_lookup, save=None, remove=None, load=None):
    async def create(profile, cwd):
        result = await server.api_create_session({
            'name': 'acp-' + secrets.token_hex(12),
            'sessionTemplate': profile.template,
            'workdir': cwd,
        })
        if not isinstance(result, dict) or result.get('error') or not result.get('id'):
            raise Unconfirmed('session creation failed or unconfirmed')
        return result['id']

    async def enqueue(session_id, turn_id, text):
        result = await server.api_session_queue_enqueue(session_id, {
            'text': text, 'clientMessageId': 'acp:' + turn_id,
        })
        if not result.get('ok') or result.get('duplicate'):
            raise Unconfirmed('queue admission failed or duplicated')
        return result.get('item', {}).get('queueItemId')

    async def withdraw(session_id, queue_id):
        result = await server.api_session_queue_delete(session_id, queue_id)
        if result.get('ok') is True and result.get('queueItemId') == queue_id:
            return 'deleted'
        error = result.get('error')
        if isinstance(error, dict) and error.get('code') == 'queue_item_not_deletable':
            return 'not_queued'
        return 'unknown'

    async def interrupt(session_id, worker_id, generation, execution_seq):
        result = await server.worker.interrupt_expected_execution(
            session_id, worker_id, generation, execution_seq)
        if result.get('status') == 'rejected' and result.get('reason') == 'native_turn_unavailable':
            return 'native_pending'
        return result.get('status')

    def recover(session_id, turn_id, queue_id):
        session = server.sess.get(session_id, load_history=False)
        return receipt_status(session, turn_id, queue_id) if session else {'state': 'unknown'}

    return Service(profile_lookup, create, enqueue, withdraw, interrupt, recover, save, remove, load)
