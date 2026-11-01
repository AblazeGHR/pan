"""Bind the canonical Job owner an Automation host requires, and optionally
route a plugin fact through a canonical Trigger to a canonical Job.

``Service.start`` refuses to run without a claimed canonical Job owner, and a
canonical owner retires the legacy Trigger dispatch.  Tests that used to assert
"plugin fact -> legacy trigger -> Pause effect" therefore have to assert
"plugin fact -> canonical Trigger -> canonical Job Run -> provider send"
instead.  ``dispatch_host`` installs exactly that: a recording session provider,
one published Action, one enabled Job and one enabled Trigger for a fact type,
so ``provider.calls`` is a drop-in replacement for the old effect spy.
"""
from types import SimpleNamespace

from fastapi import FastAPI

from packages.jobs.final_runtime.executor import TypedExecutor
from packages.jobs.final_service import bind as bind_final, definition, object_ref

USER = {'type': 'user', 'id': 'local'}


class RecordingProvider:
    """Session provider that records the session id it was asked to send to."""

    def __init__(self):
        self.calls, self.texts = [], []

    async def send_session(self, session_id, text, *, effect_key, source):
        self.calls.append(session_id)
        self.texts.append(text)
        return {'status': 'queued', 'messageId': 'message_' + str(len(self.calls))}


def claim(automation, root, *, app=None):
    """Attach a claimed canonical owner to ``automation`` and return it."""
    service = bind_final(app if app is not None else FastAPI(), automation, root)
    if service.owner.token is None:
        service.owner.claim(0)
    service.owner.set_state('active')
    return service


def _owned_action(service):
    product = next(item for item in service.actions(actor=USER)['items']
                   if item['id'] == 'product_session_message')
    value = definition('action', product)
    value.update(system=False,
                 retry={'idempotent': True, 'sameKeySupported': True, 'requiresProof': False})
    return object_ref('action', service.publish_action('owned_message', value))


def job_definition(action_ref, session_id, **fields):
    return {'name': 'owned-job',
            'steps': [{'stepId': 's0', 'actionRef': action_ref,
                       'arguments': {'sessionId': {'from': 'literal', 'value': session_id},
                                     'text': {'from': 'literal', 'value': 'owned'}},
                       'completion': {'contract': 'durable-queue-accepted'}}],
            'retention': {'policyRef': 'test'},
            'overlap': {'mode': 'queue', 'maxActive': 1, 'maxPending': 16},
            **fields}


def dispatch_host(automation, root, *, event_type, session_id='selected', app=None, key='owned'):
    """Claim a canonical owner and route ``event_type`` into one owned Job."""
    target = app if app is not None else FastAPI()
    service = bind_final(target, automation, root)
    provider = RecordingProvider()
    service.executor = TypedExecutor(session_provider=provider)
    if service.owner.token is None:
        service.owner.claim(0)
    service.owner.set_state('active')
    action = _owned_action(service)
    job = service.write('job', None, {'definition': job_definition(action, session_id),
                                      'expectedRevision': 0, 'requestKey': key + '-job'},
                        actor=USER)['result']['object']
    job = service.control('job', job['id'], {
        'operation': 'enable', 'expectedRevision': job['configRevision'],
        'expectedStateRevision': job['stateRevision'], 'requestKey': key + '-job-enable',
        'parameters': {}}, actor=USER)['result']['object']
    trigger = service.write('trigger', None, {
        'definition': {'name': key + '-trigger', 'eventTypes': [event_type],
                       'creatorSelector': {'mode': 'registeredType', 'providerType':
                           'plugin' if event_type.startswith('producer.') else 'core'},
                       'targetJobId': job['id']},
        'expectedRevision': 0, 'requestKey': key + '-trigger'}, actor=USER)['result']['object']
    trigger = service.control('trigger', trigger['id'], {
        'operation': 'enable', 'expectedRevision': trigger['revision'],
        'expectedStateRevision': trigger['stateRevision'], 'requestKey': key + '-trigger-enable',
        'parameters': {}}, actor=USER)['result']['object']
    return SimpleNamespace(service=service, provider=provider, job=job, trigger=trigger,
                           calls=provider.calls)
