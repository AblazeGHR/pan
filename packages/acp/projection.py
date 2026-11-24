"""Bounded service-side projection for one already-authorized ACP turn.

This is not an authorization boundary by itself: only the service may create
an instance after verifying the principal and Session ownership. Raw events
and buffered candidates never cross into the ACP client.
"""
from __future__ import annotations

import json


class Unconfirmed(RuntimeError):
    """Evidence is missing or inconsistent; callers must not replay effects."""


class TurnProjection:
    def __init__(self, session_id: str, queue_id: str, *, max_events=512, max_bytes=1024 * 1024):
        if not isinstance(session_id, str) or not session_id or not isinstance(queue_id, str) or not queue_id:
            raise ValueError('session and queue identities required')
        self.session_id, self.queue_id = session_id, queue_id
        self.max_events, self.max_bytes = max_events, max_bytes
        self.pending = []
        self.pending_bytes = 0
        self.identity = None
        self.seen = set()
        self.last_event_seq = 0
        self.event_epoch = None
        self.blocks = {}
        self.terminal = None

    @staticmethod
    def _identity(event):
        wid, generation, execution = event.get('workerId'), event.get('generation'), event.get('executionSeq')
        if not isinstance(wid, str) or not wid or type(generation) is not int or generation < 0 or type(execution) is not int or execution < 1:
            raise Unconfirmed('missing execution identity')
        return (wid, generation, execution)

    def offer(self, event):
        """Return text deltas; a matching terminal is available in terminal."""
        if not isinstance(event, dict) or event.get('sessionId') != self.session_id:
            return []
        kind = event.get('type')
        if kind == 'queue.item_delivered':
            ids = event.get('queueItemIds')
            if not isinstance(ids, list) or self.queue_id not in ids:
                return []
            if len(ids) != 1:
                raise Unconfirmed('delivery includes other queue items')
            identity = self._identity(event)
            if self.identity is not None and self.identity != identity:
                raise Unconfirmed('conflicting delivery identity')
            self.identity = identity
            pending, self.pending = self.pending, []
            self.pending_bytes = 0
            chunks = []
            for candidate in pending:
                chunks.extend(self._project(candidate))
            return chunks
        if kind not in ('worker.stream', 'worker.result'):
            return []
        self._identity(event)
        if self.identity is None:
            size = len(json.dumps(event, ensure_ascii=True).encode('utf-8'))
            if len(self.pending) >= self.max_events or self.pending_bytes + size > self.max_bytes:
                raise Unconfirmed('pre-delivery event buffer exhausted')
            self.pending.append(event)
            self.pending_bytes += size
            return []
        return self._project(event)

    def _project(self, event):
        if self._identity(event) != self.identity:
            return []
        if self.terminal is not None:
            return []
        epoch, seq = event.get('eventEpoch'), event.get('eventSeq')
        if not isinstance(epoch, str) or not epoch or type(seq) is not int or seq < 1:
            raise Unconfirmed('event identity unavailable')
        if self.event_epoch is not None and epoch != self.event_epoch:
            raise Unconfirmed('event epoch changed')
        event_id = (epoch, seq)
        if event_id in self.seen:
            return []
        if seq <= self.last_event_seq:
            raise Unconfirmed('out of order event')
        self.event_epoch, self.last_event_seq = epoch, seq
        self.seen.add(event_id)
        if event['type'] == 'worker.result':
            if (not isinstance(event.get('terminalKey'), str) or not event['terminalKey']
                    or type(event.get('resultCursor')) is not int or event['resultCursor'] < 1
                    or event.get('status') not in ('done', 'error', 'cancelled')):
                raise Unconfirmed('terminal identity or status unavailable')
            self.terminal = {k: event[k] for k in ('terminalKey', 'resultCursor', 'status')}
            return []
        payload = event.get('event')
        if not isinstance(payload, dict):
            raise Unconfirmed('invalid stream event')
        kind = payload.get('type')
        if kind == 'content.part' and payload.get('role') == 'assistant':
            part = payload.get('part')
            text = part.get('text') if isinstance(part, dict) and part.get('type') == 'text' else None
        elif kind == 'assistant':
            message = payload.get('message')
            content = message.get('content') if isinstance(message, dict) else None
            if not isinstance(content, list):
                raise Unconfirmed('invalid assistant content')
            text = ''.join(part['text'] for part in content if isinstance(part, dict)
                           and part.get('type') == 'text' and isinstance(part.get('text'), str))
        else:
            return []
        if text is None:
            return []
        key = payload.get('item_id') or payload.get('itemId') or 'single'
        if not isinstance(key, str):
            raise Unconfirmed('invalid assistant item identity')
        if key == 'single' and self.blocks and key not in self.blocks:
            raise Unconfirmed('assistant snapshot lacks item identity')
        if key != 'single' and 'single' in self.blocks:
            raise Unconfirmed('assistant item identity changed')
        previous = self.blocks.get(key, '')
        if payload.get('delta') is True:
            combined = previous + text
            cumulative = payload.get('stream_text')
            if cumulative is not None and cumulative != combined:
                raise Unconfirmed('stream gap or inconsistent cumulative text')
            self.blocks[key] = combined
            return [text] if text else []
        if not text.startswith(previous):
            raise Unconfirmed('assistant snapshot changed unexpectedly')
        self.blocks[key] = text
        suffix = text[len(previous):]
        return [suffix] if suffix else []
