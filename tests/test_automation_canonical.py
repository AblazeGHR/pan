"""R5-P3 shared canonical admission fixture; no legacy execution host.

Only in-process HTTP and temporary stores are used. Dispatch is explicit so
every test can distinguish a durable admission from a provider effect.
"""
import asyncio
import copy
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from packages.automation.service import Service
from packages.jobs import final_api
from packages.jobs.final_service import bind, definition, object_ref
from packages.jobs.final_runtime.executor import TypedExecutor

USER = {'type':'user', 'id':'local'}


class SessionProvider:
    def __init__(self):
        self.calls, self.fail_once, self.unknown, self.after_send = [], None, False, None
        self.targets, self.sources = [], []

    async def send_session(self, session_id, text, *, effect_key, source):
        self.calls.append((text, copy.deepcopy(effect_key)))
        self.targets.append(session_id)
        self.sources.append(source)
        if self.after_send:
            self.after_send()
        if self.unknown:
            return {'status':'unknown'}
        if self.fail_once == text:
            self.fail_once = None
            return {'status':'rejected'}
        return {'status':'queued', 'messageId':'message_' + str(len(self.calls))}


@pytest.fixture
def canonical(tmp_path):
    app = FastAPI()
    automation = Service(tmp_path / 'automation',
        SimpleNamespace(workers={}, set_running_observer=lambda cb: None), None)
    automation.auth = final_api.CanonicalUserAuth(tmp_path / 'automation')
    service = bind(app, automation, tmp_path / 'canonical')
    app.state.automation = automation
    from packages.jobs.action_api import router as action_router
    from packages.jobs.api import router as facade_router
    final_api.install(app, action_router, facade_router)
    provider = SessionProvider()
    service.executor = TypedExecutor(session_provider=provider)
    product = next(item for item in service.actions(actor=USER)['items']
                   if item['id'] == 'product_session_message')
    value = definition('action', product)
    value.update(system=False, retry={'idempotent':True, 'sameKeySupported':True, 'requiresProof':False})
    action = service.publish_action('owned_message', value)
    service.owner.claim(0)
    service.owner.set_state('active')
    automation.events.initialize()
    from packages.automation.api import install
    install(SimpleNamespace(app=app))
    client = TestClient(app)
    token = automation.auth.login(automation.auth.key())
    client.headers['Authorization'] = 'Bearer ' + token
    result = SimpleNamespace(app=app, automation=automation, service=service, provider=provider,
        client=client, root=tmp_path / 'canonical', action=object_ref('action', action), token=token)
    yield result
    client.close()
    asyncio.run(automation.close())
    asyncio.run(service.close())


def job_def(h, *, steps=1, **fields):
    return {'name':'job', 'steps':[{'stepId':'s' + str(i), 'actionRef':h.action,
        'arguments':{'sessionId':{'from':'literal', 'value':'owned-session'},
                     'text':{'from':'literal', 'value':str(i)}},
        'completion':{'contract':'durable-queue-accepted'}} for i in range(steps)],
        'retention':{'policyRef':'test'},
        'overlap':{'mode':'queue', 'maxActive':1, 'maxPending':16}, **fields}


def create(h, kind='jobs', value=None, key='create'):
    response = h.client.post('/api/' + kind, json={'definition':value or job_def(h),
        'expectedRevision':0, 'requestKey':key})
    assert response.status_code == 201, response.text
    return response.json()['result']['object']


def control(h, kind, item, name, key=None):
    return h.client.post('/api/' + kind + '/' + item['id'] + '/operations', json={
        'operation':name, 'expectedRevision':item.get('configRevision', item.get('revision')),
        'expectedStateRevision':item['stateRevision'], 'requestKey':key or name, 'parameters':{}})


def enabled(h, value=None, key='create'):
    item = create(h, value=value, key=key)
    response = control(h, 'jobs', item, 'enable', key + '-enable')
    assert response.status_code == 200, response.text
    return response.json()['result']['object']


def subscription(h, job=None, *, key='trigger', enable=True, **fields):
    job = job or enabled(h)
    item = create(h, 'triggers', {'name': key, 'eventTypes': ['workers.all_idle'],
        'creatorSelector': {'mode': 'registeredType', 'providerType': 'core'},
        'targetJobId': job['id'], **fields}, key)
    if enable:
        response = control(h, 'triggers', item, 'enable', key + '-enable')
        assert response.status_code == 200, response.text
        item = response.json()['result']['object']
    return job, item


def fact(h, event_id='event', *, payload=None):
    event = h.automation.bus.prepare(h.automation.core, 'workers.all_idle',
        {'runningCount': 0} if payload is None else payload, event_id)
    event['occurredAt'] = '2026-10-26T00:00:00Z'
    return event


def admit(h, event, target):
    return asyncio.run(h.service.admit_event_async(event, target))


def run(h, run_id):
    return h.service.get_run(run_id, actor=USER)


def operation(h, run_id, name, key=None, **fields):
    response = h.client.post('/api/job-runs/' + run_id + '/operations', json={
        'operation': name, 'expectedStateRevision': run(h, run_id)['stateRevision'],
        'requestKey': key or name, **fields})
    return response


async def deliver(h, event, *, complete=True):
    svc = h.automation
    svc.started = True
    receipt = await svc.accept(copy.deepcopy(event), svc.core)
    row = svc.events.lookup(event['id'])
    await svc.deliver(row)
    if complete:
        svc.events.delivery(event['id'], done=True)
    return receipt, svc.events.subscription_receipts(event['id'])
