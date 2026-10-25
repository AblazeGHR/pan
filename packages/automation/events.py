"""Bounded in-process fanout; publisher identity is an opaque runtime handle."""
import asyncio
import copy
import json
import uuid
from collections import deque
from datetime import datetime, timezone

import jsonschema

from packages.core.system_fields import canonical_system


def now():
    return datetime.now(timezone.utc).isoformat()


class Bus:
    def __init__(self, capacity=128, durable=None):
        self.capacity = capacity
        self.durable = durable
        self.definitions = {}
        self.handles = {}
        self.subscribers = {}
        self.diagnostics = deque(maxlen=100)

    def activate(self, kind, owner):
        if kind not in {'core', 'plugin'} or not isinstance(owner, str) or not owner:
            raise ValueError('Invalid runtime owner')
        if any(s == {'type': kind, 'id': owner} for s in self.handles.values()):
            raise ValueError('Runtime owner already active')
        if len(self.handles) >= 64:
            raise ValueError('Runtime owner capacity reached')
        handle = object()
        self.handles[handle] = {'type': kind, 'id': owner}
        return handle

    def register(self, handle, event_type, schema, version=1, *, max_payload_bytes=65536):
        if event_type not in self.definitions and len(self.definitions) >= 128:
            raise ValueError('Event definition capacity reached')
        if type(version) is not int or version < 1:
            raise ValueError('Invalid schema version')
        source = self.handles[handle]
        if (type(max_payload_bytes) is not int or not 1 <= max_payload_bytes <= 2_000_000
                or (max_payload_bytes > 65536 and source['type'] != 'core')):
            raise ValueError('Invalid Event payload capacity')
        if not isinstance(event_type, str) or not event_type.startswith(source['id'] + '.'):
            raise ValueError('Event type must use owner namespace')
        jsonschema.Draft202012Validator.check_schema(schema)
        def refs(v):
            if isinstance(v, dict):
                if any(k in v for k in ('$ref', '$dynamicRef', '$recursiveRef')):
                    raise ValueError('Schema references unsupported')
                for child in v.values():
                    refs(child)
            elif isinstance(v, list):
                for child in v:
                    refs(child)
        refs(schema)
        old = self.definitions.get(event_type)
        if old and old['source'] != source:
            raise ValueError('Event already owned')
        self.definitions[event_type] = dict(type=event_type, source=source.copy(), schema=copy.deepcopy(schema), schemaVersion=version, available=True,
                                            maxPayloadBytes=max_payload_bytes)

    def available(self, event_type):
        return self.definitions.get(event_type, {}).get('available', False)

    def subscribe(self, handle, name, callback):
        if len(self.subscribers) >= 64:
            raise ValueError('Subscriber capacity reached')
        if handle not in self.handles or name in self.subscribers:
            raise ValueError('Invalid subscription')
        queue = asyncio.Queue(self.capacity)
        async def consume():
            while True:
                event = await queue.get()
                try:
                    await callback(event)
                except Exception as exc:
                    self.diagnostics.append(dict(subscriber=name, eventId=event['id'], error=str(exc), at=now()))
                finally:
                    queue.task_done()
        self.subscribers[name] = (handle, queue, asyncio.create_task(consume()))

    def prepare(self, handle, event_type, payload, event_id=None, *, system=None):
        limit = self.definitions.get(event_type, {}).get('maxPayloadBytes', 65536)
        if len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')) > limit:
            raise ValueError('Event payload exceeds capacity')
        if event_id is not None and (not isinstance(event_id, str) or not event_id or len(event_id) > 128):
            raise ValueError('Invalid Event ID')
        source = self.handles.get(handle)
        definition = self.definitions.get(event_type)
        if not source or not definition or definition['source'] != source or not definition['available']:
            raise ValueError('Runtime is not eligible to publish this Event')
        jsonschema.Draft202012Validator(definition['schema']).validate(payload)
        # System fields are trusted runtime identity, not producer payload. Only
        # the host-owned core runtime may attach them; a plugin must never be
        # able to forge a Session/Worker identity through its publish call.
        identity = canonical_system(system) if system is not None else {}
        if identity and source['type'] != 'core':
            raise ValueError('System fields require a trusted core runtime')
        event = dict(id=event_id or 'evt_' + uuid.uuid4().hex, type=event_type, schemaVersion=definition['schemaVersion'], source=source.copy(), occurredAt=now(), payload=copy.deepcopy(payload))
        if identity:
            event['system'] = identity
        return event

    def publish(self, handle, event_type, payload, event_id=None, *, system=None):
        if self.durable is not None:
            raise ValueError('Durable runtime requires await bus.publish_async')
        return self._fanout(self.prepare(handle, event_type, payload, event_id, system=system))

    async def publish_async(self, handle, event_type, payload, event_id=None, *, system=None):
        event = self.prepare(handle, event_type, payload, event_id, system=system)
        if self.durable is None:
            return self._fanout(event)
        receipt = await self.durable(event, handle)
        return {**receipt, 'persisted': True, 'accepted': True, 'rejected': []}

    def _fanout(self, event):
        rejected = []
        for name, (_, queue, _) in self.subscribers.items():
            try:
                queue.put_nowait(copy.deepcopy(event))
            except asyncio.QueueFull:
                rejected.append(name)
        if rejected:
            self.diagnostics.append(dict(eventId=event['id'], error='subscriber_full', subscribers=rejected, at=now()))
        return dict(event=event, rejected=rejected, accepted=not rejected)

    async def deactivate(self, handle):
        source = self.handles.pop(handle, None)
        for definition in self.definitions.values():
            if definition['source'] == source:
                definition['available'] = False
        tasks = []
        for name, (owner, _, task) in list(self.subscribers.items()):
            if owner is handle:
                del self.subscribers[name]
                task.cancel()
                tasks.append(task)
        await asyncio.gather(*tasks, return_exceptions=True)

    async def close(self):
        for handle in list(self.handles):
            await self.deactivate(handle)
