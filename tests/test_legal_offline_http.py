"""Real isolated server + local deterministic CLI; never uses 8767/8768."""
import json
import socket
from types import SimpleNamespace

import psutil
import pytest

import test_real_queue_http_e2e as real
from test_real_queue_http_e2e import _PanRuntime, _create, _enqueue, _received, _spawn, _wait_for
from test_delayed_restart_locked_queue_http import _stop_owned


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    assert port not in {8767, 8768}
    monkeypatch.setattr(real, "PORT", port)
    instance = _PanRuntime(tmp_path / "runtime")
    instance.start()
    try:
        yield instance
    finally:
        _stop_owned(instance, SimpleNamespace(stop=lambda: None), None)


def durable(runtime, sid):
    return json.loads((runtime.data_root / "sessions" / f"{sid}.json").read_text(encoding="utf-8"))


def paused(runtime, sid, value):
    result = runtime.request("PATCH", f"/api/sessions/{sid}/queue/paused", json={"paused": value})
    assert result["ok"], result


def kill_owned_provider(runtime, pid, cwd):
    process = psutil.Process(pid)
    assert process.cwd() == cwd and str(runtime.data_root) in cwd
    created = process.create_time()
    process.kill()
    _wait_for(lambda: not psutil.pid_exists(pid) or psutil.Process(pid).create_time() != created,
              label="owned provider exit")


def test_external_running_kill_snapshot_manual_changes_restart_and_successor_idle(runtime):
    s = _create(runtime, "real-fifo-illegal")
    sid = s["id"]
    _enqueue(runtime, sid, "hold-A", "active")
    active = _wait_for(lambda: _received(runtime, cwd=s["workdir"], text="hold-A"))[0]
    assert durable(runtime, sid)["legal_offline"] is False
    paused(runtime, sid, True)
    b = _enqueue(runtime, sid, "B", "B")["item"]["queueItemId"]
    c = _enqueue(runtime, sid, "C", "C")["item"]["queueItemId"]
    kill_owned_provider(runtime, active["pid"], s["workdir"])
    first = _wait_for(lambda: (value if (value := durable(runtime, sid))["illegal_batches"] else None),
                      label="durable Illegal snapshot")
    batch = next(iter(first["illegal_batches"]))
    assert set(first["illegal_batches"][batch]["messages"]) == {b, c}
    detail = runtime.request("GET", f"/api/sessions/{sid}")
    assert detail["illegalOffline"] and not detail["legalOffline"]
    runtime.request("PATCH", f"/api/sessions/{sid}/queue/{b}/lock", json={"locked": False})
    runtime.request("PATCH", f"/api/sessions/{sid}/queue/{c}/lock", json={"locked": True})
    d = _enqueue(runtime, sid, "D", "D")["item"]["queueItemId"]
    runtime.stop()
    runtime.start()
    second = durable(runtime, sid)
    assert list(second["illegal_batches"]) == [batch]
    assert second["illegal_batches"][batch]["messages"] == first["illegal_batches"][batch]["messages"]
    _spawn(runtime, sid)
    recovered = _wait_for(lambda: (value if (value := durable(runtime, sid))
                                   ["illegal_batches"][batch]["recovered"] else None),
                          label="successor Idle recovered batch")
    assert recovered["legal_offline"]
    rows = {row["id"]: row for row in recovered["queue_pending"]}
    assert not rows[b].get("queueLockSource") and not rows[d].get("queueLockSource")
    assert rows[c]["queueLockSource"] == "manual"
    paused(runtime, sid, False)
    _wait_for(lambda: _received(runtime, cwd=s["workdir"], text="D"), label="D handoff")
    assert len(_received(runtime, cwd=s["workdir"], text="B")) == 1
    assert not _received(runtime, cwd=s["workdir"], text="C")
    print("LEGAL_OFFLINE_HTTP", json.dumps({"port": real.PORT, "batch": batch,
          "oldPid": active["pid"], "members": [b, c], "newItem": d, "dataRoot": str(runtime.data_root)}))


@pytest.mark.parametrize("text", ["normal", "fail-429"])
def test_safe_idle_and_captured_429_survive_idle_cli_kill_without_illegal(runtime, text):
    s = _create(runtime, "real-idle-" + text)
    sid = s["id"]
    _enqueue(runtime, sid, text, "initial")
    active = _wait_for(lambda: _received(runtime, cwd=s["workdir"], text=text))[0]
    done = _wait_for(lambda: (value if (value := durable(runtime, sid))["last_result"] else None))
    assert done["last_result"]["status"] == ("error" if text == "fail-429" else "done")
    assert done["legal_offline"] and not done["illegal_batches"]
    paused(runtime, sid, True)
    _enqueue(runtime, sid, "pending", "pending")
    kill_owned_provider(runtime, active["pid"], s["workdir"])
    runtime.stop(); runtime.start()
    value = durable(runtime, sid)
    assert value["legal_offline"] and not value["illegal_batches"]
    assert all(not item.get("queueLockSource") for item in value["queue_pending"])
    print("LEGAL_IDLE_HTTP", text, real.PORT, active["pid"])


def test_hard_service_crash_identifies_unfinished_run_once_before_dispatch(runtime):
    s = _create(runtime, "real-fifo-service-crash")
    sid = s["id"]
    _enqueue(runtime, sid, "hold-A", "active")
    active = _wait_for(lambda: _received(runtime, cwd=s["workdir"], text="hold-A"))[0]
    paused(runtime, sid, True)
    b = _enqueue(runtime, sid, "B", "B")["item"]["queueItemId"]
    before = durable(runtime, sid)
    assert before["legal_offline"] is False and not before["illegal_batches"]
    # Kill only server identities recorded by this owned harness. No graceful
    # lifespan callback can mark the interrupted execution safe.
    for pid, created, command in reversed(runtime._owned_processes):
        try:
            process = psutil.Process(pid)
            if abs(process.create_time() - created) < .01 and str(real.SERVER_LAUNCHER) in command:
                process.kill()
        except psutil.NoSuchProcess:
            pass
    runtime.stop()
    try:
        kill_owned_provider(runtime, active["pid"], s["workdir"])
    except psutil.NoSuchProcess:
        pass
    runtime.start()
    crashed = durable(runtime, sid)
    key = before["offline_run"]["id"]
    assert list(crashed["illegal_batches"]) == [key]
    assert crashed["illegal_batches"][key]["messages"] == [b]
    assert not _received(runtime, cwd=s["workdir"], text="B")
    runtime.stop(); runtime.start()
    assert list(durable(runtime, sid)["illegal_batches"]) == [key]
    _spawn(runtime, sid)
    _wait_for(lambda: durable(runtime, sid)["illegal_batches"][key]["recovered"])
    assert not _received(runtime, cwd=s["workdir"], text="B")  # Pause still owns handoff.
    print("LEGAL_CRASH_HTTP", json.dumps({"port": real.PORT, "run": key,
          "member": b, "oldPid": active["pid"], "dataRoot": str(runtime.data_root)}))


@pytest.mark.parametrize("failed_ready", [False, True])
def test_all_locked_restart_stays_dormant_and_constructed_idle_does_not_recover(runtime, monkeypatch, tmp_path, failed_ready):
    s = _create(runtime, "real-fifo-delayed-init")
    sid = s["id"]
    _enqueue(runtime, sid, "hold-A", "active")
    active = _wait_for(lambda: _received(runtime, cwd=s["workdir"], text="hold-A"))[0]
    b = _enqueue(runtime, sid, "B", "queued")["item"]["queueItemId"]
    kill_owned_provider(runtime, active["pid"], s["workdir"])
    failed = _wait_for(lambda: (value if (value := durable(runtime, sid))["illegal_batches"] else None))
    batch = next(iter(failed["illegal_batches"]))
    assert failed["illegal_batches"][batch]["messages"] == [b]
    # Unpaused, all locked. Restarting the isolated service cannot spawn merely
    # to recover Illegal locks. A user Start is the explicit next trigger.
    runtime.stop()
    gate = tmp_path / "adapter-ready"
    monkeypatch.setenv("PAN_E2E_INIT_GATE", str(gate))
    if failed_ready:
        monkeypatch.setenv("PAN_E2E_INIT_FAIL", "1")
    before = runtime.log_path.read_text(encoding="utf-8").count('"kind": "init"')
    runtime.start()
    assert runtime.log_path.read_text(encoding="utf-8").count('"kind": "init"') == before
    assert not durable(runtime, sid)["illegal_batches"][batch]["recovered"]
    _spawn(runtime, sid)
    _wait_for(lambda: runtime.log_path.read_text(encoding="utf-8").count('"kind": "init"') == before + 1)
    constructed = durable(runtime, sid)
    assert constructed["offline_owner"]["processes"]
    assert not constructed["illegal_batches"][batch]["recovered"]
    assert constructed["legal_offline"] is False
    assert not _received(runtime, cwd=s["workdir"], text="B")
    gate.touch()
    if failed_ready:
        _wait_for(lambda: not psutil.pid_exists(durable(runtime, sid)["offline_owner"]["processes"][0]["pid"]))
        assert not durable(runtime, sid)["illegal_batches"][batch]["recovered"]
        assert not _received(runtime, cwd=s["workdir"], text="B")
    else:
        _wait_for(lambda: durable(runtime, sid)["illegal_batches"][batch]["recovered"])
        _wait_for(lambda: _received(runtime, cwd=s["workdir"], text="B"))
    print("LEGAL_DELAYED_READY", json.dumps({"port": real.PORT, "failedReady": failed_ready,
          "batch": batch, "dataRoot": str(runtime.data_root)}))
