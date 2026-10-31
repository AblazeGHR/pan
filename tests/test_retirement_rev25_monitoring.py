"""Original delegated monitoring revoke assertion using real detached effects."""
import asyncio
import copy
import json
import time

from packages.jobs.final_service import FinalService
from tests.test_job_automatic_recovery import env, create, state
from tests.test_job_monitoring_completion import monitoring_definition


def test_real_delegated_monitoring_revocation_preserves_attempt_budget_deadline_and_original_effect(env):
    grants = env.auth._default_permissions()
    grants['job']['execute'] = grants['action']['manageSystemActions'] = True
    env.auth.change_permissions(grants, 0)
    marker = env.tmp / 'real-monitoring-invocations'
    code = (f'from pathlib import Path; import json; Path({str(marker)!r}).open("a").write("ran\\n"); '
            'print(json.dumps({"panJobCompletion":{"type":"owned.done","completed":False}}))')
    value = monitoring_definition(env, code)
    value['recovery']['backoffSeconds'] = ['0.1']
    jid, rid = create(env, value, actor={'type': 'agent', 'id': 'owned-delegated-monitor', 'revision': 1})
    asyncio.run(env.service.dispatch_once())
    deadline = time.monotonic() + 12
    while True:
        asyncio.run(env.service.recover_external_receipts())
        plan = env.service.final.store.get(jid)['finalRuntime']['plans'][rid]
        if plan.get('autoRecovery'):
            break
        assert time.monotonic() < deadline
        time.sleep(.025)
    before = copy.deepcopy(state(env, jid))
    budget = copy.deepcopy(plan['autoRecovery'])
    windows = copy.deepcopy(plan.get('recoveryWindows', {}))
    assert len(before['attempts']) == len(before['effects']) == 1
    assert marker.read_text().splitlines() == ['ran']
    env.auth.change_permissions(env.auth._default_permissions(), 1)
    time.sleep(.15)
    for _ in range(2):
        asyncio.run(env.service.dispatch_once())
    cold = FinalService(env.root, env.auth)
    try:
        cold.recover()
        asyncio.run(cold.dispatch_once())
        after = cold.final.get(jid)
        current = cold.final.store.get(jid)['finalRuntime']['plans'][rid]
        assert after['attempts'] == before['attempts']
        assert after['effects'] == before['effects']
        assert current['autoRecovery'] == budget and current.get('recoveryWindows', {}) == windows
        assert marker.read_text().splitlines() == ['ran']
        assert len(after['runs']) == 1
    finally:
        asyncio.run(cold.close())
