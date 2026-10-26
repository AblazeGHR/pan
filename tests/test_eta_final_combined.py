"""RuntimeIssue ingress -> canonical admission; dispatch and reporting are separate."""
import asyncio
import pytest
from packages.core.runtime_issues import map_runtime_issue
from tests.test_automation_canonical import canonical, enabled, job_def, subscription, run
from tests.test_automation_core import until


@pytest.mark.parametrize('trusted', [True, False])
def test_runtime_issue_trusted_system_binding_and_no_payload_identity_fallback(canonical, trusted):
    h = canonical
    definition = job_def(h)
    definition['steps'][0]['arguments']['sessionId'] = {'from':'input', 'path':'target'}
    job = enabled(h, definition)
    _, target = subscription(h, job, eventTypes=['runtime.issue.v1'],
        arguments={'target':{'from':'system', 'path':'sessionId'}})
    mapping = map_runtime_issue({'type':'codex.turn_error', 'error':{'code':'usage_limit_reached',
        'message':'usage limit'}}, adapter='codex', metadata={'sessionId':'owned-target', 'workerId':'owned-worker'})
    if not trusted:
        mapping['event'].pop('system')
        mapping['event']['payload']['sessionId'] = 'forged-payload-target'
    async def check():
        svc = h.automation
        await svc.start()
        try:
            svc.observe_runtime_issue(mapping)
            await asyncio.wait_for(svc.ingress.join(), 3)
            async def received():
                row = svc.events.lookup(mapping['event']['id'])
                return row and (row['status'] == 'delivered' or row['deliveryCount'] > 0)
            await until(received)
            if trusted:
                runs = h.service.final.runs(job['id'])
                assert len(runs) == 1 and runs[0]['state'] == 'queued'
                assert h.provider.calls == []
                await h.service.dispatch_once()
                assert run(h, runs[0]['runId'])['state'] == 'completed'
                assert h.provider.targets == ['owned-target']
                assert h.provider.sources == ['automation']
            else:
                # A payload correlation cannot substitute for host identity.
                assert not h.service.final.runs(job['id']) and not h.provider.calls
                row = svc.events.lookup(mapping['event']['id'])
                assert 'sessionId' in row['lastError']
        finally:
            await svc.close()
    if trusted:
        asyncio.run(check())
    else:
        # Keep the RuntimeIssue payload valid while removing only host identity.
        mapping['event']['payload'].pop('sessionId')
        mapping['event']['payload']['correlation']['sessionId'] = 'forged-payload-target'
        asyncio.run(check())
