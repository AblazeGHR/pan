"""Canonical read-only preview uses the original time provider, never a cursor."""
import pytest
from packages.scheduler.providers import time_definition
from tests.test_job_final_creators import config, T0
from tests.job_retirement_native_fixture import h  # noqa: F401


def snapshot(h):
    with h.service.transaction(readonly=True) as db:
        operations = db.execute('SELECT count(*) FROM final_operations').fetchone()[0]
    return operations, h.service.creators.list(), h.service.events.stats(), h.client.get('/api/jobs').json()


def value():
    return {'definition': time_definition('preview only', config([
        {'ruleId': 'once', 'type': 'once', 'at': '2026-01-01T00:00:00.125Z'},
        {'ruleId': 'grid', 'type': 'interval', 'seconds': '0.125', 'anchor': T0, 'advanceMode': 'grid'}])),
        'at': T0, 'count': 4}


def test_decimal_preview_merges_original_rules_no_write_and_repeat_stable(h):
    before = snapshot(h)
    response = h.client.post('/api/event-creators/preview', json=value())
    assert response.status_code == 200, response.text
    points = response.json()['items']
    assert [(p['ruleId'], p['at']) for p in points] == [
        ('once', '2026-01-01T00:00:00.125Z'), ('grid', '2026-01-01T00:00:00.125Z'),
        ('grid', '2026-01-01T00:00:00.25Z'), ('grid', '2026-01-01T00:00:00.375Z')]
    assert h.client.post('/api/event-creators/preview', json=value()).json() == response.json()
    assert snapshot(h) == before


@pytest.mark.parametrize('count', [0, 101, True, '3', None])
def test_preview_bad_count_rejects_without_operation_or_cursor(h, count):
    body = value()
    body['count'] = count
    before = snapshot(h)
    response = h.client.post('/api/event-creators/preview', json=body)
    assert response.status_code == 400 and response.json()['error']['code'] == 'invalid_schema'
    assert snapshot(h) == before


def test_preview_auth_and_original_provider_release_are_enforced(h):
    before = snapshot(h)
    assert h.client.post('/api/event-creators/preview', json={}, headers={'Authorization': 'Bearer invalid'}).status_code == 401
    body = value()
    body['definition']['providerRef']['generation'] += 1
    response = h.client.post('/api/event-creators/preview', json=body)
    assert response.status_code == 503 and response.json()['error']['code'] == 'provider_unavailable'
    assert snapshot(h) == before


def test_preview_uses_existing_creator_read_grant_without_execution_authority(h):
    from fastapi.testclient import TestClient
    from packages.automation import authorization
    async def trusted(scope, receive, send):
        scope[authorization._SCOPE_KEY] = authorization._MARKER
        await h.app(scope, receive, send)
    with TestClient(trusted) as agent:
        response = agent.post('/api/event-creators/preview', json=value())
        assert response.status_code == 403, response.text
        grants = h.automation.auth._default_permissions()
        grants['eventCreator']['read'] = True
        h.automation.auth.change_permissions(grants, 0)
        before = snapshot(h)
        response = agent.post('/api/event-creators/preview', json=value())
        assert response.status_code == 200 and len(response.json()['items']) == 4
        assert not grants['eventCreator']['create'] and not grants['job']['execute']
        assert snapshot(h) == before
