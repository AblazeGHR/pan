from types import SimpleNamespace

from packages.core.terminal.contracts import RuntimeState
from packages.core.terminal.service import TerminalService


def test_shutdown_consumes_late_owned_cleanup_within_same_total_budget(tmp_path, monkeypatch):
    service = TerminalService(tmp_path)
    tid = 'term_shutdown_ma'
    record = SimpleNamespace(terminal_id=tid, detached=False, owner='service', status=RuntimeState.RUNNING)
    service._states[tid] = object()
    monkeypatch.setattr(service, '_safe_records', lambda: [record])
    attempts = []
    def close(terminal_id, *, reason, budget):
        attempts.append(budget)
        return {'status': 'exited' if len(attempts) == 2 else 'cleanup-failed'}
    monkeypatch.setattr(service, '_close_for_shutdown', close)
    monkeypatch.setattr(service, '_stop_heartbeat_for', lambda *args, **kwargs: None)
    monkeypatch.setattr(service, '_release_for', lambda *args: None)
    report = service.shutdown(budget=.5)
    assert len(attempts) == 2
    assert attempts[1] < attempts[0]
    assert report['exited'] == [tid]
    assert report['unconfirmed'] == []
    assert report['secrets_retained'] is False


def test_shutdown_persistent_unconfirmed_keeps_owner_and_real_budget(tmp_path, monkeypatch):
    service = TerminalService(tmp_path)
    tid = 'term_shutdown_ma'
    record = SimpleNamespace(terminal_id=tid, detached=False, owner='service', status=RuntimeState.RUNNING)
    owner = object()
    service._states[tid] = owner
    monkeypatch.setattr(service, '_safe_records', lambda: [record])
    monkeypatch.setattr(service, '_close_for_shutdown', lambda *args, **kwargs: {'status': 'cleanup-failed'})
    monkeypatch.setattr(service, '_stop_heartbeat_for', lambda *args, **kwargs: None)
    report = service.shutdown(budget=.08)
    assert report['unconfirmed'] == [tid]
    assert report['secrets_retained'] is True
    assert report['budget_exhausted'] is True
    assert report['elapsed_seconds'] < .2
    assert service._states[tid] is owner
