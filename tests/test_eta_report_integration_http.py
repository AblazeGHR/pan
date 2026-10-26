"""Report queue facts and canonical Event admission retain independent identities."""
import asyncio
import json
from packages.core import runtime_issues, worker
from tests import test_report_error_context as report
from tests.test_report_error_context import _no_real_recovery_spawn
from tests.test_automation_canonical import canonical, subscription, deliver


def test_terminal_report_and_event_admission_share_original_issue_without_extra_report(canonical, monkeypatch):
    h = canonical
    job, target = subscription(h, eventTypes=['runtime.issue.v1'])
    observed = []
    runtime_issues.set_observer(observed.append)
    try:
        child, manager, _ = report._setup_managed_pair()
        event = {'type':'result', 'subtype':'error_during_execution', 'is_error':True,
                 'result':'owned capacity sample', 'error_code':'serverOverloaded'}
        # Use the same deterministic report fixture already exercised by the
        # coexistence test; no worker process or provider is started.
        from pathlib import Path
        event = json.loads((Path(__file__).parent / 'fixtures/terminal_capacity_report.json').read_text(encoding='utf-8'))['event']
        report._run_stream_worker(child, [report._event_line(event), report._event_line(event)], seq=8)
        assert len(manager.queue_pending) == 1 and len(observed) == 1
        item = manager.queue_pending[0]
        issue = observed[0]
        ev = h.automation.bus.prepare(h.automation.issue_runtime, 'runtime.issue.v1',
            issue['event']['payload'], issue['event']['id'], system=issue['event'].get('system'))
        asyncio.run(deliver(h, ev))
        rid = h.service.final.runs(job['id'])[0]['runId']
        assert len(manager.queue_pending) == 1
        assert item['status'] == 'error' and item['result'] == event['result']
        assert h.client.get('/api/job-runs/' + rid).json()['cause']['eventId'] == issue['event']['id']
        assert h.provider.calls == []
        asyncio.run(h.service.dispatch_once())
        assert h.client.get('/api/job-runs/' + rid).json()['state'] == 'completed'
        assert len(manager.queue_pending) == 1 and len(h.provider.calls) == 1
    finally:
        runtime_issues.set_observer(None)
        report._cleanup()
