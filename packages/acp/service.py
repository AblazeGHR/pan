"""Narrow, server-owned ACP turn state and filtered event projection.

This object is not a transport or an authentication mechanism.  A local
transport must authenticate every request before invoking it, and must not
expose ``observe`` to clients.  Unknown side effects are never retried here.
"""
from __future__ import annotations

import asyncio
import json
import secrets
from dataclasses import dataclass, field

from .policy import resolve_workspace
from .projection import TurnProjection, Unconfirmed


@dataclass
class Turn:
    turn_id: str
    pending: list = field(default_factory=list)
    pending_bytes: int = 0
    projection: TurnProjection | None = None
    chunks: list = field(default_factory=list)
    error: bool = False
    queue_id: str | None = None
    cancelled: bool = False
    cancel_requested: bool = False
    cancel_attempted: bool = False
    changed: asyncio.Event = field(default_factory=asyncio.Event)


@dataclass
class Context:
    principal: str
    profile: object
    cwd: str
    session_id: str | None = None
    turn: Turn | None = None
    last_turn: Turn | None = None


class Service:
    """Per-process authority. All methods must run on the server event loop."""

    def __init__(self, profile_lookup, create_session, enqueue, withdraw=None, interrupt=None, recover=None,
                 save=None, remove=None):
        self.profile_lookup = profile_lookup
        self.create_session = create_session
        self.enqueue = enqueue
        self.withdraw = withdraw
        self.interrupt = interrupt
        self.recover_receipt = recover
        self.save = save or (lambda *_: None)
        self.remove = remove or (lambda *_: None)
        self.contexts: dict[str, Context] = {}
        self.by_session: dict[str, Context] = {}

    def _context(self, principal, handle):
        ctx = self.contexts.get(handle) if isinstance(handle, str) else None
        if not ctx or not isinstance(principal, str) or ctx.principal != principal:
            raise PermissionError('ACP context unavailable')
        # Disabling or changing the profile takes effect on the next request.
        if self.profile_lookup(principal) != ctx.profile:
            raise PermissionError('ACP profile changed')
        return ctx

    def prepare(self, principal, cwd):
        profile = self.profile_lookup(principal)
        if profile is None:
            raise PermissionError('ACP profile unavailable')
        if len(self.contexts) >= 128:
            raise Unconfirmed('ACP context limit reached')
        path = resolve_workspace(profile, cwd)
        handle = secrets.token_urlsafe(32)
        ctx = Context(principal, profile, str(path))
        self.save(handle, ctx)
        self.contexts[handle] = ctx
        return handle

    async def submit(self, principal, handle, turn_id, text):
        ctx = self._context(principal, handle)
        if not isinstance(turn_id, str) or not turn_id or not isinstance(text, str) or not text.strip():
            raise ValueError('invalid turn')
        if ctx.turn is not None:
            # Never retry an ambiguous submit, even for the same id.
            raise Unconfirmed('previous turn remains active')
        turn = ctx.turn = Turn(turn_id)
        try:
            # Persist identity and intent before any external side effect.
            self.save(handle, ctx)
            if ctx.session_id is None:
                created = await self.create_session(ctx.profile, ctx.cwd)
                if not isinstance(created, str) or not created or created in self.by_session:
                    raise Unconfirmed('session creation unconfirmed')
                ctx.session_id = created
                self.by_session[created] = ctx
                self.save(handle, ctx)
            queue_id = await self.enqueue(ctx.session_id, turn_id, text)
            if not isinstance(queue_id, str) or not queue_id:
                raise Unconfirmed('queue admission unconfirmed')
            turn.queue_id = queue_id
            self.save(handle, ctx)
            turn.projection = TurnProjection(ctx.session_id, queue_id)
            pending, turn.pending = turn.pending, []
            turn.pending_bytes = 0
            for event in pending:
                self._offer(turn, event)
            if turn.cancel_requested:
                await self._cancel(ctx, turn)
        except Exception:
            turn.error = True
            turn.changed.set()
            raise

    def _offer(self, turn, event):
        try:
            if turn.projection is None:
                size = len(json.dumps(event, ensure_ascii=True).encode('utf-8'))
                if len(turn.pending) >= 512 or turn.pending_bytes + size > 1024 * 1024:
                    raise Unconfirmed('pre-admission buffer exhausted')
                turn.pending.append(event)
                turn.pending_bytes += size
            else:
                turn.chunks.extend(turn.projection.offer(event))
                if sum(len(x.encode('utf-8')) for x in turn.chunks) > 1024 * 1024:
                    raise Unconfirmed('unconsumed output buffer exhausted')
        except Exception:
            turn.error = True
        turn.changed.set()

    def observe(self, event):
        """Consume private raw events; never return them to the caller."""
        if not isinstance(event, dict) or event.get('type') not in {
                'queue.item_delivered', 'worker.stream', 'worker.result'}:
            return
        ctx = self.by_session.get(event.get('sessionId'))
        if ctx and ctx.turn and not ctx.turn.error:
            self._offer(ctx.turn, event)

    def poll(self, principal, handle, turn_id, cursor=0):
        ctx = self._context(principal, handle)
        turn = ctx.turn
        if turn is None or turn.turn_id != turn_id:
            raise PermissionError('ACP turn unavailable')
        if turn.error:
            raise Unconfirmed('turn outcome unknown')
        if type(cursor) is not int or not 0 <= cursor <= len(turn.chunks):
            raise ValueError('invalid output cursor')
        chunks = []
        size = 0
        end = cursor
        for chunk in turn.chunks[cursor:]:
            encoded_size = len(json.dumps(chunk, ensure_ascii=True).encode('utf-8'))
            if encoded_size > 128 * 1024:
                raise Unconfirmed('output chunk exceeds transport limit')
            if size + encoded_size > 128 * 1024:
                break
            chunks.append(chunk)
            size += encoded_size
            end += 1
        terminal = turn.projection.terminal if turn.projection else None
        if turn.cancelled:
            terminal = {'status': 'cancelled'}
        return {'chunks': chunks, 'cursor': end,
                'status': terminal['status'] if terminal and end == len(turn.chunks) else None}

    def finish(self, principal, handle, turn_id, cursor):
        ctx = self._context(principal, handle)
        turn = ctx.turn
        if (turn is None or turn.turn_id != turn_id or turn.error
                or type(cursor) is not int or cursor != len(turn.chunks)
                or not (turn.cancelled or (turn.projection and turn.projection.terminal))):
            raise Unconfirmed('terminal or output acknowledgement unavailable')
        previous = ctx.last_turn
        ctx.last_turn, ctx.turn = turn, None
        try:
            self.save(handle, ctx)
        except Exception:
            ctx.turn, ctx.last_turn = turn, previous
            raise

    def release(self, principal, handle):
        ctx = self._context(principal, handle)
        if ctx.turn is not None:
            raise Unconfirmed('active or unknown turn cannot be discarded')
        self.remove(handle)
        if ctx.session_id:
            self.by_session.pop(ctx.session_id, None)
        del self.contexts[handle]

    def recover(self, principal, handle, turn_id):
        ctx = self._context(principal, handle)
        turn = next((candidate for candidate in (ctx.turn, ctx.last_turn)
                     if candidate is not None and candidate.turn_id == turn_id), None)
        if (turn is None or not ctx.session_id
                or self.recover_receipt is None):
            return {'state': 'unknown'}
        return self.recover_receipt(ctx.session_id, turn_id, turn.queue_id)

    async def cancel(self, principal, handle, turn_id):
        ctx = self._context(principal, handle)
        turn = ctx.turn
        if turn is None or turn.turn_id != turn_id or turn.error:
            raise Unconfirmed('turn unavailable')
        turn.cancel_requested = True
        if turn.queue_id is not None:
            await self._cancel(ctx, turn)

    async def _cancel(self, ctx, turn):
        if turn.cancel_attempted or turn.cancelled or (turn.projection and turn.projection.terminal):
            return
        turn.cancel_attempted = True
        if self.withdraw is None or self.interrupt is None:
            raise Unconfirmed('cancellation unavailable')
        # The queue deletion operation performs its own state check under the
        # queue lock. Only a persisted deletion proves queued cancellation.
        result = await self.withdraw(ctx.session_id, turn.queue_id)
        if result == 'deleted':
            turn.cancelled = True
            return
        if result != 'not_queued':
            raise Unconfirmed('withdrawal outcome unknown')
        identity = turn.projection.identity if turn.projection else None
        if identity is None:
            raise Unconfirmed('delivery identity unavailable')
        # Submitted interrupt is not a terminal; poll remains pending until
        # the original execution emits its matching terminal result.
        if await self.interrupt(ctx.session_id, *identity) != 'submitted':
            raise Unconfirmed('interrupt outcome unconfirmed')
