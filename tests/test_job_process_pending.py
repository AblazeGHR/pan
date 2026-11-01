"""A known native execution-pending receipt is not an unknown outcome."""
import asyncio

from packages.jobs.final_service import FinalService, object_ref
from tests.test_job_automatic_recovery import env, job_definition, create, drive_until


def test_non_retrying_process_waits_for_original_receipt_across_cold_lookup(env):
    h = env
    gate, counter = h.tmp / 'release', h.tmp / 'original-process.txt'
    code = ("import time; from pathlib import Path; "
            f"Path({str(counter)!r}).open('a').write('once\\n'); "
            f"gate=Path({str(gate)!r})\nwhile not gate.exists(): time.sleep(.03)")
    value = job_definition(h, recovery={'automatic': False}, code=code)
    action = next(a for a in h.service.actions(actor={'type': 'user', 'id': 'local'})['items']
                  if a['id'] == 'product_process_logs_v1')
    value['steps'][0]['actionRef'] = object_ref('action', action)
    jid, rid = create(h, value)
    try:
        asyncio.run(h.service.dispatch_once())
        state = h.service.final.store.get(jid)['finalRuntime']
        assert state['runs'][rid]['state'] == 'running'
        aid = state['plans'][rid]['pendingProcessAttempt']
        assert state['attempts'][aid]['state'] == 'running'
        assert not state['effects'] and not state['outbox']
        cold = FinalService(h.root, h.auth, owner=h.service.owner)
        for _ in range(2): asyncio.run(cold.recover_external_receipts())
        after = cold.final.store.get(jid)['finalRuntime']
        assert after['runs'] == state['runs'] and after['attempts'] == state['attempts']
        assert not after['effects'] and not after['outbox']
        gate.write_text('release')
        old = h.service
        h.service = cold
        asyncio.run(drive_until(h, jid, rid, 'completed'))
        assert counter.read_text().splitlines() == ['once']
        done = cold.final.store.get(jid)['finalRuntime']
        assert set(done['attempts']) == {aid}
        assert done['effects'][0]['effectKey']['runId'] == rid
        assert done['effects'][0]['outcome'] == 'succeeded'
        asyncio.run(cold.recover_external_receipts())
        assert cold.final.store.get(jid)['finalRuntime'] == done
        asyncio.run(old.close())
    finally:
        gate.write_text('release')
