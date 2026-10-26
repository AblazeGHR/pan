"""Event history and replay via in-process HTTP over the canonical owner."""
import asyncio
from types import SimpleNamespace
from packages.automation.api import install
from tests.test_automation_canonical import canonical, subscription, fact, deliver


def test_event_history_http_bounded_pages_authorized_replay_and_no_new_run(canonical):
    h = canonical
    job, target = subscription(h)
    install(SimpleNamespace(app=h.app))
    asyncio.run(deliver(h, fact(h)))
    assert h.client.get('/api/automation/events?limit=101').status_code == 400
    page = h.client.get('/api/automation/events?limit=1').json()
    assert [row['event']['id'] for row in page['events']] == ['event']
    assert page['events'][0]['subscriptions']['trigger:' + target['id']]['runId']
    assert h.client.post('/api/automation/events/replay', json={'eventIds':['event']},
        headers={'Authorization':'Bearer invalid'}).status_code == 401
    assert h.client.post('/api/automation/events/replay', json={'eventIds':['missing']}).status_code == 409
    assert h.client.post('/api/automation/events/replay', json={'eventIds':['event']}).status_code == 200
    asyncio.run(h.automation.deliver(h.automation.events.lookup('event')))
    assert len(h.service.final.runs(job['id'])) == 1 and not h.provider.calls
    assert h.client.post('/api/automation/events', json={'source':'core'}).status_code == 405


def test_event_receipt_http_points_to_same_run_before_and_after_dispatch(canonical):
    h = canonical
    _, target = subscription(h)
    install(SimpleNamespace(app=h.app))
    _, receipts = asyncio.run(deliver(h, fact(h)))
    rid = receipts['trigger:' + target['id']]['runId']
    assert h.client.get('/api/job-runs/' + rid).json()['state'] == 'queued'
    asyncio.run(h.service.dispatch_once())
    assert h.client.get('/api/job-runs/' + rid).json()['state'] == 'completed'
    page = h.client.get('/api/automation/events?limit=1').json()
    assert page['events'][0]['subscriptions']['trigger:' + target['id']]['runId'] == rid
    assert len(h.provider.calls) == 1
