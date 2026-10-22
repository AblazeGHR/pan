"""Versioned scheduler defaults; no resource ownership or business admission here."""
import copy

MAX_CONCURRENCY = 32
MAX_ATTEMPTS = 10
BACKOFF_SECONDS = 1
BACKOFF_CAP_SECONDS = 60

RETRY_SCHEMA = {'type': 'object', 'additionalProperties': False, 'properties': {
    'enabled': {'type': 'boolean', 'default': False},
    'maxAttempts': {'type': 'integer', 'minimum': 1, 'maximum': MAX_ATTEMPTS, 'default': 3}}}
SCHEDULING_SCHEMA = {'type': 'object', 'additionalProperties': False, 'properties': {
    'executionMode': {'type': 'string', 'enum': ['serial', 'parallel'], 'default': 'serial'},
    'maxConcurrency': {'type': 'integer', 'minimum': 1, 'maximum': MAX_CONCURRENCY},
    'retry': RETRY_SCHEMA}, 'allOf': [
        {'if': {'properties': {'executionMode': {'const': 'parallel'}}, 'required': ['executionMode']},
         'then': {'properties': {'maxConcurrency': {'default': 3}}},
         'else': {'properties': {'maxConcurrency': {'const': 1, 'default': 1}}}}]}


def retry_policy(value=None):
    value = {} if value is None else copy.deepcopy(value)
    if not isinstance(value, dict) or set(value) - {'enabled', 'maxAttempts'}:
        raise ValueError('Unknown retry policy fields')
    value.setdefault('enabled', False)
    value.setdefault('maxAttempts', 3)
    if type(value['enabled']) is not bool or type(value['maxAttempts']) is not int or not 1 <= value['maxAttempts'] <= MAX_ATTEMPTS:
        raise ValueError('retry requires boolean enabled and maxAttempts 1..10 (including first)')
    return value


def concurrency(trigger):
    mode = trigger.get('executionMode', 'serial')
    if not isinstance(mode, str) or mode not in {'serial', 'parallel'}:
        raise ValueError('executionMode must be serial or parallel')
    limit = trigger.get('maxConcurrency', 3 if mode == 'parallel' else 1)
    if type(limit) is not int or not 1 <= limit <= MAX_CONCURRENCY or (mode == 'serial' and limit != 1):
        raise ValueError('maxConcurrency requires 1 for serial or integer 1..32 for parallel')
    return limit


def backoff(attempt_count):
    return min(BACKOFF_SECONDS * 2**(attempt_count-1), BACKOFF_CAP_SECONDS)
