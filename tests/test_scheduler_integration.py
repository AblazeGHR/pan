"""R6-G production HTTP retirement with no host lifecycle or listener."""
import pytest
from tests.test_scheduler_api import WRITES_WITH_CALLERS, contents, scheduler_env  # noqa: F401


@pytest.mark.parametrize('method,path', WRITES_WITH_CALLERS)
def test_http_mutation_does_not_change_archive_or_dispatch(scheduler_env, monkeypatch, method, path):
    from packages.core import worker
    from packages.scheduler.creators import CreatorStore
    client, task, root, headers = scheduler_env
    def forbidden(*args, **kwargs):
        pytest.fail('Retired route reached a dispatch or Creator mutation')
    monkeypatch.setattr(worker, 'assign', forbidden)
    monkeypatch.setattr(CreatorStore, 'create', forbidden)
    before = contents(root)
    response = client.request(method, '/api/scheduler' + path.replace('{id}', task['id']),
                              headers=headers, json={'schedule': {'kind': 'once', 'at': '2026-01-01T00:00:00'}})
    assert response.status_code == 410 and response.json()['detail']['code'] == 'retired'
    assert contents(root) == before
