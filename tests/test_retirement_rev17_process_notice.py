"""Actual canonical detached process completion -> original durable Session notice.

Separate from scheduled message backlog and legacy history drain. No legacy
execution/writer is activated. Existing fixture cleans owned supervisors.
"""
import asyncio
import json

from packages.core import session, worker
from packages.jobs.final_service import FinalService, object_ref
from tests.test_job_automatic_recovery import env, job_definition, create, drive_until


def completed(h):
    session.save(session.Session(id='ses_process_notice', name='owned process notice',
                                workdir=str(h.tmp), queue_paused=True))
    counter = h.tmp / 'actual-process.txt'
    code = f'from pathlib import Path; Path({str(counter)!r}).open("a").write("ran\\n")'
    value = job_definition(h, recovery={'automatic': False}, code=code)
    logged = next(a for a in h.service.actions(actor={'type': 'user', 'id': 'local'})['items']
                  if a['id'] == 'product_process_logs_v1')
    value['steps'][0]['actionRef'] = object_ref('action', logged)
    value['notification'] = {'on': ['completed'], 'recipients': [
        {'recipientId': 'completion', 'channelId': 'session', 'targetRef': 'ses_process_notice'}]}
    jid, rid = create(h, value)
    asyncio.run(drive_until(h, jid, rid, 'completed'))
    assert counter.read_text().splitlines() == ['ran']
    return jid, rid, counter


def test_actual_detached_completion_cold_original_notice_exact_once(env):
    jid, rid, counter = completed(env)
    before = env.service.final.history(jid)
    row = next(r for r in before['outbox'] if r['channel'] == 'session')
    assert row['status'] == 'pending' and row['runId'] == rid
    calls = []
    async def send(item):
        calls.append(item['outboxId'])
        if item['channel'] == 'ws': return {'outcome': 'succeeded'}
        result = await worker.enqueue_notice(item['targetRef'], json.dumps(item['summary']),
            source='automation', event_id=item['outboxId'], notice_kind='job_terminal',
            job_id=jid, notice_status=item['summary']['status'])
        assert result['ok']
        return {'outcome': 'succeeded'}
    cold = FinalService(env.root, env.auth)
    cold.outbox_sender = send
    try:
        asyncio.run(cold.deliver_outbox())
        history = cold.final.history(jid)
        assert all(r['status'] == 'acked' for r in history['outbox'])
        notices = session.get('ses_process_notice').queue_pending
        assert len(notices) == 1 and notices[0]['eventId'] == row['outboxId']
        notice = json.loads(notices[0]['result'])
        assert notice['runId'] == rid and notice['status'] == 'completed'
        assert notice['exitCode'] == 0 and notice['logRef']
        asyncio.run(cold.dispatch_once())
        asyncio.run(cold.deliver_outbox())
        assert cold.final.history(jid) == history
        assert len(calls) == len(set(calls)) == len(history['outbox'])
        assert counter.read_text().splitlines() == ['ran']
    finally:
        asyncio.run(cold.close())


def test_actual_detached_completion_notice_retains_original_exit_log_details(env):
    jid, rid, _ = completed(env)
    state = env.service.final.get(jid)
    receipt = state['effects'][0]['receipt']['receipt']
    summary = next(r for r in state['outbox'].values() if r['channel'] == 'session')['summary']
    assert summary.get('exitCode') == receipt['exitCode'] == 0, summary
    assert receipt['logRef'], 'The original process must actually publish its log reference'
    assert summary.get('logRef') == receipt['logRef'], summary
