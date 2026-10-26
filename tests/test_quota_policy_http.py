"""Policy config remains HTTP-managed; the retired query Job writer is fenced."""
from types import SimpleNamespace
from packages.automation.api import install
from packages.automation.quota_policy import Store
from tests.test_automation_canonical import canonical


def test_quota_policy_owned_http_default_unavailable_is_unknown(canonical):
    h = canonical
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
    assert response.status_code == 409 and 'fence' in response.text
    assert h.automation.quota_policy.store.read() == before
    assert h.automation.quota_policy.adapter.__class__.__name__ == 'Adapter'
    restored = Store(h.automation.ledger.path.parent).read()
    assert restored['disabledAccountIds'] == ['owned-disabled'] and restored['mode'] == 'automatic'
    assert not h.provider.calls
