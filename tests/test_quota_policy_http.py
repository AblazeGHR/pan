"""Policy config remains HTTP-managed; the retired query Job writer is fenced."""
from types import SimpleNamespace
from packages.automation.api import install
from packages.automation.quota_policy import Store
from tests.test_automation_canonical import canonical


def test_quota_policy_owned_http_default_unavailable_is_unknown(canonical):
    h = canonical
    from tests.support.quota_unavailable_source import source
    h.automation.quota_policy.adapter.source = source(h.root / "empty-quota")
    install(SimpleNamespace(app=h.app))
    url = '/api/automation/quota-policy'
    state = h.client.get(url).json()
    assert state['mode'] == 'manual' and state['adapter'] == 'unavailable'
    assert state['disabledAccountIds'] == []
    disabled = {'disabledAccountIds':['owned-disabled'], 'expectedRevision':state['revision']}
    assert h.client.put(url + '/disabled-accounts', json=disabled,
        headers={'Authorization':'Bearer invalid'}).status_code == 401
    assert h.client.put(url + '/disabled-accounts', json={**disabled,
        'disabledAccountIds':['duplicate', 'duplicate']}).status_code == 400
    assert h.client.put(url + '/disabled-accounts', json=disabled).status_code == 200
    assert h.client.put(url + '/disabled-accounts', json=disabled).status_code == 409
    state = h.client.get(url).json()
    response = h.client.put(url, json={'mode':'automatic', 'maSessionId':'owned-ma',
        'expectedRevision':state['revision']})
    assert response.status_code == 200, response.text
    state = h.client.get(url).json()
    before = h.automation.quota_policy.store.read()
    response = h.client.post(url + '/query-jobs', json={'kind':'once', 'expectedRevision':state['revision']})
    assert response.status_code == 200, response.text
    job = response.json()['job']
    assert job['quotaPolicy']['purpose'] == 'once'
    assert job['quotaPolicy']['expectedAccountId'] is None
    duplicate = h.client.post(url + '/query-jobs', json={'kind':'once', 'expectedRevision':state['revision']})
    assert duplicate.status_code == 200 and duplicate.json()['job'] == job
    aggregate = h.service.final.get(job['jobId'])
    assert not aggregate['runs'] and not aggregate['attempts'] and not aggregate['effects']
    assert not h.automation.quota_policy.store.read()['commands']
    assert h.automation.quota_policy.adapter.__class__.__name__ == 'HostAdapter'
    assert h.automation.quota_policy.adapter.ports == {}
    restored = Store(h.automation.ledger.path.parent).read()
    assert restored['disabledAccountIds'] == ['owned-disabled'] and restored['mode'] == 'automatic'
    assert not h.provider.calls

# Main-side real HTTP imports retained after conflict resolution.
import json
from pathlib import Path
import socket
import httpx
from tests import test_real_queue_http_e2e as real_http

def test_quota_policy_owned_http_default_unavailable_is_unknown_real_http(tmp_path,monkeypatch):
    # Reuse the existing complete native two-process successor, including real
    # auth, CAS, original Event/Run/Attempt/effect, unknown/no-replay and restart.
    from tests.test_quota_native_admission import test_unknown_production_process_restart_real_http
    test_unknown_production_process_restart_real_http(tmp_path, monkeypatch)
