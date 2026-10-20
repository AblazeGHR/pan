"""Nonempty Wake must not recover predecessors before adapter proof."""
import json

import psutil
import pytest

from test_legal_offline_http import runtime, durable, paused
from test_illegal_queue_wake_http import illegal
from test_real_queue_http_e2e import _received, _wait_for


@pytest.mark.parametrize("failed_ready", [False, True])
def test_explicit_wake_retains_old_locks_until_ready_and_on_startup_failure(runtime, monkeypatch, tmp_path, failed_ready):
    s = illegal(runtime); sid = s["id"]
    snapshot = durable(runtime, sid)
    old_id = snapshot["queue_pending"][0]["id"]
    batch = next(iter(snapshot["illegal_batches"]))
    runtime.stop()
    gate = tmp_path / "ready"
    monkeypatch.setenv("PAN_E2E_INIT_GATE", str(gate))
    if failed_ready: monkeypatch.setenv("PAN_E2E_INIT_FAIL", "1")
    runtime.start()
    before = runtime.log_path.read_text(encoding="utf-8").count('"kind": "init"')
    result = runtime.request("POST", "/api/sessions/wake", json={"sessionIds": [sid], "clientMessageId": "nonempty-wake"})
    assert result["ok"]
    paused(runtime, sid, False)
    _wait_for(lambda: runtime.log_path.read_text(encoding="utf-8").count('"kind": "init"') == before + 1)
    value = durable(runtime, sid)
    assert not value["illegal_batches"][batch]["recovered"]
    assert runtime.request("GET", f"/api/sessions/{sid}")["illegalOffline"]
    assert next(row for row in value["queue_pending"] if row["id"] == old_id)["queueLockSource"] == "illegal"
    assert not _received(runtime, cwd=s["workdir"], text="old backlog")
    if failed_ready: paused(runtime, sid, True)
    gate.touch()
    if failed_ready:
        records = [json.loads(line) for line in runtime.log_path.read_text(encoding="utf-8").splitlines()]
        pid = next(row["pid"] for row in reversed(records) if row.get("kind") == "init")
        _wait_for(lambda: not psutil.pid_exists(pid))
        value = durable(runtime, sid)
        assert not value["illegal_batches"][batch]["recovered"]
        assert runtime.request("GET", f"/api/sessions/{sid}")["illegalOffline"]
        assert not _received(runtime, cwd=s["workdir"], text="old backlog")
    else:
        _wait_for(lambda: durable(runtime, sid)["illegal_batches"][batch]["recovered"])
        _wait_for(lambda: _received(runtime, cwd=s["workdir"], text="old backlog"))
    print("WAKE_READY_FINAL", runtime.base_url, failed_ready, batch, str(tmp_path))
