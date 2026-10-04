from types import SimpleNamespace
import json
import pytest

from packages.core.terminal.contracts import RuntimeState
from packages.core.terminal.service import TerminalService


@pytest.mark.parametrize("change", [{}, {"pid": 8}, {"filetime": "99"},
                                     {"close_ok": "true"}, {"converged": False},
                                     {"owner_retained": True}, {"tree_remaining": 1},
                                     {"exit_code": 6}, {"pipe": False}])
def test_runner_final_record_requires_exact_identity_and_cleanup_facts(tmp_path, change):
    service = TerminalService(tmp_path)
    state = SimpleNamespace(terminal_id="term_record_ma", runner_pid=7, runner_filetime=9)
    cleanup = {"converged": True, "close_ok": True, "owner_retained": False,
               "tree_remaining": 0, "retained": [], "pipe": {"converged": True}}
    payload = {"terminal_id": state.terminal_id, "phase": "exited", "exit_code": 0,
               "runner_identity": {"pid": 7, "process_created_at_filetime": "9"}, "cleanup": cleanup}
    for key, value in change.items():
        if key == "pid":
            payload["runner_identity"]["pid"] = value
        elif key == "filetime":
            payload["runner_identity"]["process_created_at_filetime"] = value
        elif key == "exit_code":
            payload[key] = value
        elif key == "pipe":
            cleanup["pipe"]["converged"] = value
        else:
            cleanup[key] = value
    path = tmp_path / "runner-status" / f"{state.terminal_id}.json"
    path.parent.mkdir()
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert service._runner_cleanup_record_confirmed(state) is (not change)


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
