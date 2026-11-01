"""Real TCP quota boundary. The missing canonical query remains an open gap."""
from contextlib import contextmanager
import copy
import socket
import threading
import time
from types import SimpleNamespace

import httpx
import uvicorn

from packages.actions.typed_executor import TypedExecutor
from packages.automation.api import install
from packages.automation.quota_policy import Store
from tests.job_retirement_native_fixture import h  # noqa: F401


@contextmanager
def owned_tcp(app):
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    port = listener.getsockname()[1]
    assert port not in (8767, 8768)
    listener.listen(64)
    server = uvicorn.Server(uvicorn.Config(app, log_level='error', lifespan='off'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.01)
        assert server.started and thread.is_alive()
        yield f'http://127.0.0.1:{port}'
    finally:
        server.should_exit = True
        thread.join(10)
        listener.close()
        assert not thread.is_alive()
        with socket.socket() as probe:
            assert probe.connect_ex(('127.0.0.1', port)) != 0


def test_quota_real_tcp_cas_reopen_and_legacy_writer_fence(h):
    h.service.executor = TypedExecutor()
    install(SimpleNamespace(app=h.app))
    h.service.owner.claim(0)
    h.service.owner.set_state('active')
    headers = {'Authorization': 'Bearer ' + h.token}
    path = '/api/automation/quota-policy'
    with owned_tcp(h.app) as url, httpx.Client(base_url=url, timeout=5, trust_env=False) as client:
        state = client.get(path).json()
        assert state['mode'] == 'manual' and state['adapter'] == 'unavailable'
        disabled = {'disabledAccountIds': ['owned-disabled'], 'expectedRevision': state['revision']}
        assert client.put(path + '/disabled-accounts', json=disabled).status_code == 401
        assert client.put(path + '/disabled-accounts', json=disabled, headers=headers).status_code == 200
        assert client.put(path + '/disabled-accounts', json=disabled, headers=headers).status_code == 409
        state = client.get(path).json()
        update = {'mode': 'automatic', 'maSessionId': 'owned-ma', 'expectedRevision': state['revision']}
        assert client.put(path, json={**update, 'script': 'unsafe'}, headers=headers).status_code == 400
        assert client.put(path, json=update, headers=headers).status_code == 200
        state = client.get(path).json()
        before = copy.deepcopy(h.automation.quota_policy.store.read())
        for kind in ('monitor95', 'once'):
            result = client.post(path + '/query-jobs', json={
                'kind': kind, 'expectedRevision': state['revision']}, headers=headers)
            assert result.status_code == 409 and 'fence' in result.text
            assert h.automation.quota_policy.store.read() == before
        assert client.get('/api/jobs', headers=headers).json()['items'] == []
        assert before['commands'] == {}
        assert Store(h.automation.ledger.path.parent).read() == before
    # A second actual listener uses the same durable state/auth; this is only
    # listener reopening, never described as a full process/lifespan restart.
    with owned_tcp(h.app) as url, httpx.Client(base_url=url, timeout=5, trust_env=False) as client:
        reopened = client.get(path).json()
        assert reopened['mode'] == 'automatic'
        assert reopened['disabledAccountIds'] == ['owned-disabled']
        assert client.post(path + '/query-jobs', headers=headers, json={
            'kind': 'once', 'expectedRevision': reopened['revision']}).status_code == 409
        assert h.automation.quota_policy.store.read() == before
