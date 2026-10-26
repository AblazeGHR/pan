"""Host policy config persists independently of legacy query-host execution."""
import asyncio
from types import SimpleNamespace
from packages.automation.api import install
from packages.automation.quota_policy import Store
from tests.test_automation_canonical import canonical


def test_owned_host_configuration_read_projection_and_restart(canonical):
    h = canonical
    install(SimpleNamespace(app=h.app))
    url = '/api/automation/quota-policy'
    state = h.client.get(url).json()
    assert state['host'] == {'enabled':False, 'sessionRoles':{}}
    assert state['adapter'] == 'unavailable'
    body = {'enabled':True, 'sessionRoles':{'owned-target':'ta'}, 'expectedRevision':state['revision']}
    assert h.client.put(url + '/host', json=body, headers={'Authorization':'Bearer invalid'}).status_code == 401
    for invalid in ({**body, 'effectsEnabled':True}, {**body, 'enabled':'true'},
                    {**body, 'sessionRoles':{'owned-target':'guess-from-name'}}):
        assert h.client.put(url + '/host', json=invalid).status_code == 400
    assert h.client.put(url + '/host', json=body).status_code == 200
    assert h.client.put(url + '/host', json=body).status_code == 409
    state = h.client.get(url).json()
    assert state['host'] == {'enabled':True, 'sessionRoles':{'owned-target':'ta'}}
    # Enabling policy configuration cannot manufacture a business effect port.
    snapshot = asyncio.run(h.automation.quota_policy.query())
    assert snapshot['status'] == 'unknown'
    assert not h.automation.quota_policy.store.read()['commands']
    restored = Store(h.automation.ledger.path.parent).read()
    assert restored['host'] == state['host']
    assert not h.provider.calls
