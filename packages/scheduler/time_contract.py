"""Stable scheduler event identity and public v1 payload schema; no host imports."""
EVENT_TYPE = 'scheduler.time.v1'
OWNER = 'scheduler-event-v1'
PAYLOAD_SCHEMA = {'type': 'object', 'additionalProperties': False,
    'required': ['scheduleId', 'scheduleRevision', 'scheduledAt', 'emittedAt', 'eventId'],
    'properties': {'scheduleId': {'type': 'string'}, 'scheduleRevision': {'type': 'integer', 'minimum': 1},
                   **{k: {'type': 'string'} for k in ('scheduledAt', 'emittedAt', 'eventId')}}}
