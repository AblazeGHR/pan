"""ACP v1 connection state. Backend owns authorization and durable effects.

This module never opens Pan sessions or sends worker input by itself. The
backend must authorize profile/workspace and own submission, identity,
delivery, cancellation and terminal reconciliation on the service side.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .backend import RecoveredTerminal

MAX_LINE = 1024 * 1024


@dataclass
class Session:
    context: Any
    task: asyncio.Task | None = None
    turn_id: str | None = None
    request_id: str | int | None = None


class Protocol:
    """One ACP connection, with connection-local session ownership."""
    def __init__(self, backend, send: Callable[[dict], Awaitable[None]]):
        self.backend = backend
        self.send = send
        self.initialized = False
        self.closed = False
        self.sessions: dict[str, Session] = {}
        self.tasks: set[asyncio.Task] = set()
        self.pending_permissions = {}

    async def _error(self, request_id, code, message):
        await self.send({'jsonrpc': '2.0', 'id': request_id, 'error': {'code': code, 'message': message}})

    async def _result(self, request_id, result):
        await self.send({'jsonrpc': '2.0', 'id': request_id, 'result': result})

    async def handle(self, message):
        if isinstance(message, dict) and message.get('jsonrpc') == '2.0' and 'method' not in message:
            request_id = message.get('id')
            if isinstance(request_id, str):
                future = self.pending_permissions.pop(request_id, None)
                if future is not None and not future.done():
                    future.set_result(message.get('result'))
            return
        if not isinstance(message, dict) or message.get('jsonrpc') != '2.0' or not isinstance(message.get('method'), str):
            await self._error(None, -32600, 'Invalid request')
            return
        method, params = message['method'], message.get('params', {})
        has_id = 'id' in message
        request_id = message.get('id')
        if has_id and (isinstance(request_id, bool) or not isinstance(request_id, (str, int))):
            await self._error(None, -32600, 'Invalid request')
            return
        if not isinstance(params, dict):
            if has_id: await self._error(request_id, -32602, 'Invalid parameters')
            return
        if method == 'session/cancel' and not has_id:
            sid = params.get('sessionId')
            session = self.sessions.get(sid) if isinstance(sid, str) else None
            if session and session.task and not session.task.done() and session.turn_id:
                try:
                    await self.backend.cancel(session.context, session.turn_id)
                except Exception:
                    pass  # No notification reply and no claim of cancellation.
            return
        if method == '$/cancel_request' and not has_id:
            target = params.get('requestId')
            if isinstance(target, bool) or not isinstance(target, (str, int)):
                return
            matches = [s for s in self.sessions.values() if type(s.request_id) is type(target)
                       and s.request_id == target and s.task and not s.task.done() and s.turn_id]
            if len(matches) == 1:
                try:
                    await self.backend.cancel(matches[0].context, matches[0].turn_id)
                except Exception:
                    pass  # Cancellation outcome remains unconfirmed.
            return
        if not has_id:
            return
        if method == 'initialize':
            if self.initialized or type(params.get('protocolVersion')) is not int:
                await self._error(request_id, -32602, 'Unsupported initialization')
                return
            self.initialized = True
            await self._result(request_id, {'protocolVersion': 1, 'agentCapabilities': {'loadSession': False},
                'agentInfo': {'name': 'pan', 'version': '0.1'}, 'authMethods': []})
            return
        if not self.initialized:
            await self._error(request_id, -32600, 'Initialize first')
            return
        if method == 'session/new':
            cwd, servers = params.get('cwd'), params.get('mcpServers')
            if not isinstance(cwd, str) or not os.path.isabs(cwd) or not isinstance(servers, list):
                await self._error(request_id, -32602, 'Absolute cwd and mcpServers required')
                return
            try:
                # prepare must only authorize and describe: it cannot create a
                # durable Session before the first prompt.
                context = await self.backend.prepare(cwd, servers) if servers else await self.backend.prepare(cwd)
            except Exception:
                await self._error(request_id, -32000, 'Session authorization unavailable')
                return
            sid = str(uuid.uuid4())
            self.sessions[sid] = Session(context)
            await self._result(request_id, {'sessionId': sid})
            return
        if method == 'session/prompt':
            sid, blocks = params.get('sessionId'), params.get('prompt')
            session = self.sessions.get(sid) if isinstance(sid, str) else None
            if session is None and isinstance(sid, str):
                await self.send({'jsonrpc':'2.0', 'id':request_id, 'error':{'code':-32002,
                                'message':'Session not found', 'data':{'sessionId':sid}}})
                return
            if session is None or not isinstance(blocks, list) or not blocks or any(
                    not isinstance(block, dict) or block.get('type') not in ('text', 'resource_link')
                    or (block.get('type') == 'text' and not isinstance(block.get('text'), str))
                    or (block.get('type') == 'resource_link' and
                        (not isinstance(block.get('uri'), str) or not isinstance(block.get('name'), str))) for block in blocks):
                await self._error(request_id, -32602, 'Invalid session or prompt')
                return
            if session.task and not session.task.done():
                await self._error(request_id, -32000, 'Session is busy')
                return
            session.turn_id = uuid.uuid4().hex
            session.request_id = request_id
            session.task = asyncio.create_task(self._prompt(request_id, sid, session, blocks, session.turn_id))
            self.tasks.add(session.task)
            session.task.add_done_callback(self.tasks.discard)
            return
        await self._error(request_id, -32601, 'Method not found')

    async def _prompt(self, request_id, sid, session, blocks, turn_id):
        active = True
        emission_lock = asyncio.Lock()
        async def emit(text):
            if (isinstance(text, dict) and isinstance(text.get('permissionId'), str)
                    and isinstance(text.get('command'), str) and text['command']):
                if not active or self.closed or session.turn_id != turn_id:
                    return 'decline'
                permission_id = text['permissionId']
                request = 'pan-permission-' + uuid.uuid4().hex
                future = asyncio.get_running_loop().create_future()
                self.pending_permissions[request] = future
                try:
                    await self.send({'jsonrpc':'2.0', 'id':request, 'method':'session/request_permission',
                        'params':{'sessionId':sid,
                            'toolCall':{'toolCallId':permission_id, 'title':text['command'], 'kind':'execute', 'status':'pending'},
                            'options':[{'optionId':'allow_once', 'name':'Allow once', 'kind':'allow_once'},
                                       {'optionId':'reject_once', 'name':'Reject once', 'kind':'reject_once'}]}})
                    reply = await future
                    outcome = reply.get('outcome') if isinstance(reply, dict) else None
                    if isinstance(outcome, dict) and outcome.get('outcome') == 'selected' and outcome.get('optionId') == 'allow_once':
                        return 'accept'
                    return 'decline'
                finally:
                    self.pending_permissions.pop(request, None)
            async with emission_lock:
                if not active or self.closed or session.turn_id != turn_id or not isinstance(text, str):
                    return
                await self.send({'jsonrpc': '2.0', 'method': 'session/update', 'params': {'sessionId': sid,
                    'update': {'sessionUpdate': 'agent_message_chunk', 'content': {'type': 'text', 'text': text}}}})
        try:
            result = await self.backend.prompt(session.context, blocks, turn_id, emit)
            if result not in ('end_turn', 'cancelled'):
                raise ValueError('unconfirmed terminal')
            async with emission_lock:
                active = False
                if not self.closed: await self._result(request_id, {'stopReason': result})
        except RecoveredTerminal as exc:
            async with emission_lock:
                active = False
                if not self.closed:
                    await self.send({'jsonrpc':'2.0', 'id':request_id,
                        'error':{'code':-32000, 'message':'Terminal confirmed; stream outcome unknown',
                                 'data':{'panTerminal':exc.receipt}}})
        except Exception:
            async with emission_lock:
                active = False
                if not self.closed: await self._error(request_id, -32000, 'Turn failed or outcome unknown')
        finally:
            if session.turn_id == turn_id:
                session.turn_id = None
                session.request_id = None

    async def close(self):
        self.closed = True
        for future in self.pending_permissions.values():
            if not future.done(): future.set_result(None)
        await self.backend.disconnect()


async def serve(backend, stdin, stdout):
    """Serve newline-delimited UTF-8 JSON-RPC without non-protocol stdout."""
    write_lock = asyncio.Lock()
    async def send(message):
        raw = (json.dumps(message, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')
        async with write_lock:
            stdout.write(raw)
            stdout.flush()
    protocol = Protocol(backend, send)
    try:
        while True:
            line = await asyncio.to_thread(stdin.readline, MAX_LINE + 1)
            if not line: break
            if len(line) > MAX_LINE or not line.endswith(b'\n'):
                await protocol._error(None, -32700, 'Invalid JSON line')
                break
            try:
                message = json.loads(line.decode('utf-8'))
            except (UnicodeDecodeError, json.JSONDecodeError):
                await protocol._error(None, -32700, 'Invalid JSON')
                continue
            await protocol.handle(message)
    finally:
        await protocol.close()
