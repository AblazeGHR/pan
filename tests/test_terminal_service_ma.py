"""MA difference checks: stale stop results and exact identity fields."""

import threading
from types import SimpleNamespace

import pytest

from packages.core.terminal.contracts import ProcessStatus
from tests.test_terminal_service_rework import (
    _service, _record_identity, _FakeProcess, _converged_launcher_status,
)


def test_success_published_before_resend_lock_is_not_reset(tmp_path):
    process = _FakeProcess()
    service = _service(tmp_path, process=process, heartbeat_interval=5.0)
    terminal_id = service.create()["terminal_id"]
    state = service._states[terminal_id]
    _converged_launcher_status(service.root, terminal_id, *_record_identity(service, terminal_id))
    process.exit(0)

    class Call:
        confirmed = False
        in_flight = False
        resets = 0

        def run(self, budget):
            return True, {"runner_confirmed": self.confirmed}, None

        def reset(self):
            self.resets += 1
            return True

    call = Call()

    class PublishBeforeAcquire:
        """Another caller completed the retry while this caller waited for the lock."""
        lock = threading.Lock()

        def acquire(self, *, timeout):
            call.confirmed = True
            return self.lock.acquire(timeout=timeout)

        def release(self):
            self.lock.release()

    state.close_call = call
    state.close_op_lock = PublishBeforeAcquire()
    try:
        assert service.close(terminal_id)["status"] == "exited"
        assert call.resets == 0, "A stale failure must not reset another caller's success"
    finally:
        service.shutdown(budget=2.0)


@pytest.mark.parametrize("pid,ft", [(40001.9, 77), (40001, 77.9), (True, 77)])
def test_identity_fields_are_exact_not_truncated(tmp_path, pid, ft):
    service = _service(tmp_path, probe=lambda _: SimpleNamespace(
        status=ProcessStatus.DEAD,
        identity=SimpleNamespace(pid=pid, created_at_filetime=ft),
    ))
    assert service._identity_evidence(40001 if pid is not True else 1, 77)["status"] == "unattributable"
