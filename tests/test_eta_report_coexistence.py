"""Merged Worker terminal facts preserve report and ETA observation independently."""
import json
from pathlib import Path

import pytest

from packages.core import worker, runtime_issues, session_error_log
from packages.core.adapters import CodexAdapter
from tests import test_report_error_context as h
from tests.test_report_error_context import _no_real_recovery_spawn


@pytest.mark.parametrize('adapter,classification',[('codex','codex_capacity'),('cbc','unknown')])
def test_same_terminal_report_and_runtime_issue_once(monkeypatch,adapter,classification):
    fixture=json.loads((Path(__file__).parent/'fixtures/terminal_capacity_report.json').read_text(encoding='utf-8'))
    event=fixture['event']
    observed=[]
    runtime_issues.set_observer(observed.append)
    if adapter=='codex':
        monkeypatch.setattr(h,'CbcAdapter',CodexAdapter)
        monkeypatch.setattr(worker,'_schedule_usage_enrichment',lambda sid:None)
    try:
        child,manager,_=h._setup_managed_pair()
        h._run_stream_worker(child,[h._event_line(h._assistant_event(event['result'])),
            h._event_line(event),h._event_line(event)],seq=8)
        assert len(manager.queue_pending)==1
        report=manager.queue_pending[0]
        assert report['status']=='error' and report['errorCode']=='serverOverloaded'
        assert report['result']==report['lastMessage']==event['result']
        rows=[json.loads(line) for line in session_error_log.path().read_text(encoding='utf-8').splitlines()]
        terminal=[row for row in rows if row['kind']=='terminal_error']
        assert len(terminal)==1 and len(observed)==1
        mapping=terminal[0]['runtimeIssue']
        assert mapping['issue']['classification']==classification
        assert mapping['issue']['correlation']['taskSeq']==8
        assert mapping['issue']['correlation']['sessionId']==child.id
        assert terminal[0]['errorText']==report['errorText']
        assert mapping['issue']['issueId']==observed[0]['issue']['issueId']
        assert 'runtimeIssue' not in report and 'raw' not in report
    finally:
        runtime_issues.set_observer(None)
        h._cleanup()


@pytest.mark.parametrize('enabled', [True, False])
def test_behavior_gate_uses_durable_issue_without_exporting_it(monkeypatch, enabled):
    import copy
    fixture = json.loads((Path(__file__).parent / 'fixtures/terminal_capacity_report.json').read_text(encoding='utf-8'))
    event = fixture['event']
    config = copy.deepcopy(worker.load_config())
    config.setdefault('ui', {})['terminalBehavior'] = {
        'autoReportByErrorType': {'codex_capacity': enabled}}
    monkeypatch.setattr(worker, 'load_config', lambda: config)
    monkeypatch.setattr(h, 'CbcAdapter', CodexAdapter)
    monkeypatch.setattr(worker, '_schedule_usage_enrichment', lambda sid: None)
    observed = []
    runtime_issues.set_observer(observed.append)
    try:
        child, manager, _ = h._setup_managed_pair()
        h._run_stream_worker(child, [h._event_line(h._assistant_event(event['result'])),
            h._event_line(event), h._event_line(event)], seq=8)
        diagnostic = child.last_result['reportDiagnostic']
        assert diagnostic['runtimeIssue']['classification'] == 'codex_capacity'
        assert diagnostic['runtimeIssue']['correlation']['taskSeq'] == 8
        cold = json.loads(h._sess._path(child.id).read_text(encoding='utf-8'))
        assert cold['last_result']['reportDiagnostic'] == diagnostic
        rows = [json.loads(line) for line in session_error_log.path().read_text(encoding='utf-8').splitlines()]
        terminal = [row for row in rows if row['kind'] == 'terminal_error']
        assert len(terminal) == len(observed) == 1
        assert terminal[0]['runtimeIssue']['issue']['classification'] == 'codex_capacity'
        assert len(manager.queue_pending) == int(enabled)
        if enabled:
            report = manager.queue_pending[0]
            expected = {key: value for key, value in diagnostic.items() if key not in {'runtimeIssue', 'raw'}}
            assert all(report[key] == value for key, value in expected.items())
            assert 'runtimeIssue' not in report and 'raw' not in report
        # Both automatic and explicit reports use this projection; it must not
        # mutate the durable policy input or leak future raw observations.
        original = copy.deepcopy(diagnostic)
        projected = worker._report_diagnostic_projection({**diagnostic, 'raw': {'secret': 'fixture'}})
        assert 'runtimeIssue' not in projected and 'raw' not in projected
        assert projected['errorCode'] == diagnostic['errorCode']
        assert diagnostic == original
    finally:
        runtime_issues.set_observer(None)
        h._cleanup()
