"""Bind the scoped ACP state machine to Pan's canonical Session/queue APIs."""
from __future__ import annotations

import secrets
import hashlib
import json

from .service import Service
from .projection import Unconfirmed
from .policy import authorize_mcp, mcp_digest, physical_mcp_pin


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


def bind(server, profile_lookup, save=None, remove=None, load=None, data_root=None):
    def authorize(profile, requested):
        template = server._character_manager.get_session_template(profile.template)
        if template is None or (template.adapter or 'cbc') not in {'cbc', 'claude', 'codex'}:
            raise PermissionError('MCP transport unavailable for this template')
        names, digest = authorize_mcp(profile, requested, server._resolve_mcp_server_configs)
        catalog = server._plugin_catalog()
        for name in names:
            if physical_mcp_pin(catalog, [name]) != profile.mcp_pins[profile.mcp_names.index(name)]:
                raise PermissionError('MCP profile pin changed')
        return names, digest

    async def create(profile, cwd, names=(), digest='', secret_handle=None):
        if names:
            template = server._character_manager.get_session_template(profile.template)
            if template is None or (template.adapter or 'cbc') not in {'cbc', 'claude', 'codex'}:
                raise PermissionError('MCP transport unavailable for this template')
        if names and (any(name not in profile.mcp_names for name in names)
                      or mcp_digest(names, server._resolve_mcp_server_configs) != digest):
            raise PermissionError('MCP authorization changed')
        request = {
            'name': 'acp-' + secrets.token_hex(12),
            'sessionTemplate': profile.template,
            'workdir': cwd,
        }
        if names:
            request['mcpServers'] = list(names)
            catalog = server._plugin_catalog()
            for name in names:
                if physical_mcp_pin(catalog, [name]) != profile.mcp_pins[profile.mcp_names.index(name)]:
                    raise PermissionError('MCP profile pin changed')
            physical = catalog.compile(catalog.legacy_bindings(list(names)))
            pin = hashlib.sha256(json.dumps(physical, sort_keys=True, ensure_ascii=True,
                                           separators=(',', ':')).encode()).hexdigest()
            result = await server._create_session_with_mcp_pin(request, _acp_mcp_pin=pin)
        else:
            result = await server.api_create_session(request)
        if not isinstance(result, dict) or result.get('error') or not result.get('id'):
            raise Unconfirmed('session creation failed or unconfirmed')
        if secret_handle is not None:
            if data_root is None:
                raise Unconfirmed('private MCP transport unavailable')
            session = server.sess.get(result['id'], load_history=False)
            if session is None:
                raise Unconfirmed('session unavailable')
            session.adapter_config['_acp_secret_handle'] = secret_handle
            session.adapter_config['_acp_secret_root'] = str(data_root)
            session.adapter_config['_acp_secret_names'] = [name for name in names
                if profile.mcp_env[profile.mcp_names.index(name)]]
            server.sess.save(session)
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

    async def approval(session_id, worker_id, generation, execution_seq, request_id, decision):
        result = await server.worker.approval_expected_execution(
            session_id, worker_id, generation, execution_seq, request_id, decision)
        return result.get('status')

    return Service(profile_lookup, create, enqueue, withdraw, interrupt, recover, save, remove, load,
                   authorize, approval)
