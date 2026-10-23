"""Real isolated server + local deterministic CLI; never uses 8767/8768."""
import json
import socket
from types import SimpleNamespace

import psutil
import pytest

import test_real_queue_http_e2e as real
from test_real_queue_http_e2e import _PanRuntime, _create, _enqueue, _received, _spawn, _wait_for
from test_delayed_restart_locked_queue_http import _stop_owned, _read_session_metadata


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
    return _read_session_metadata(runtime.data_root / "sessions" / f"{sid}.json")


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
    assert set(second["illegal_batches"][batch]["messages"]) == {b, c, d}
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


@pytest.mark.parametrize("method", ["SIGINT", "SIGTERM", "idle-SIGINT", "idle-SIGTERM", "safe-api", "preserve-api", "q-config", "q-menu", "q-menu-2", "q-menu-3", "q-triple"])
def test_owned_host_signal_vs_exit_api_handshake(tmp_path, monkeypatch, method):
    monkeypatch.setenv("PAN_TEST_HOST_EXIT_PROBE", "1")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    assert port not in {8767, 8768}
    monkeypatch.setattr(real, "PORT", port)
    instance = _PanRuntime(tmp_path / "host-exit")
    if method in {"q-config", "q-triple"}:
        instance.data_root.mkdir(parents=True)
        (instance.data_root / "config.json").write_text(
            json.dumps({"session_lifecycle": {"exitStrategy": "offline"}}), encoding="utf-8")
    instance.start()
    try:
        session = _create(instance, "real-fifo-host-exit-proof")
        sid = session["id"]
        _enqueue(instance, sid, "hold-A", "active")
        _wait_for(lambda: _received(instance, cwd=session["workdir"], text="hold-A"))
        paused(instance, sid, True)
        _enqueue(instance, sid, "B", "pending")
        assert durable(instance, sid)["legal_offline"] is False
        if method.startswith("idle-"):
            instance.gates["hold-A"].touch()
            _wait_for(lambda: durable(instance, sid)["legal_offline"], label="safe Idle committed")
        if method.startswith("q-"):
            from packages.core.console_exit import ConsoleExit
            now = [0.0]
            console = ConsoleExit(lambda payload: instance.request("POST", "/api/main/exit", json=payload),
                lambda: "ask" if method.startswith("q-menu") else "offline", clock=lambda: now[0])
            console.line("qqq" if method == "q-triple" else "q")
            now[0] = 2.1
            console.tick()
            if method.startswith("q-menu"):
                console.line(method[-1] if method[-1].isdigit() else "1")
            assert console.accepted
        elif method.endswith("api"):
            payload = {"options": {"markRunningSessionsOffline": method == "safe-api"}}
            assert instance.request("POST", "/api/main/exit", json=payload)["accepted"]
        else:
            instance.request("POST", f"/__test/host-signal/{method.removeprefix('idle-')}")
        _wait_for(lambda: instance.process.poll() is not None, timeout=25, label="owned host stopped")
        value = durable(instance, sid)
        illegal = method not in {"safe-api", "q-config", "q-menu", "idle-SIGINT", "idle-SIGTERM"}
        assert value["legal_offline"] is (not illegal)
        assert bool(value["illegal_batches"]) is illegal
        assert "recovery_requested" not in value
        instance.stop()
        instance.start()
        again = durable(instance, sid)
        assert again["illegal_batches"] == value["illegal_batches"]
        print("HOST_EXIT_PROOF", method, port, instance.data_root)
    finally:
        _stop_owned(instance, SimpleNamespace(stop=lambda: None), None)


def test_clear_illegal_startup_policy_retires_persisted_incident_without_successor(runtime):
    session = _create(runtime, "real-fifo-clear-illegal")
    sid = session["id"]
    _enqueue(runtime, sid, "hold-A", "active")
    active = _wait_for(lambda: _received(runtime, cwd=session["workdir"], text="hold-A"))[0]
    paused(runtime, sid, True)
    b = _enqueue(runtime, sid, "B", "B")["item"]["queueItemId"]
    c = _enqueue(runtime, sid, "C", "C")["item"]["queueItemId"]
    kill_owned_provider(runtime, active["pid"], session["workdir"])
    _wait_for(lambda: durable(runtime, sid)["illegal_batches"])
    runtime.request("PATCH", f"/api/sessions/{sid}/queue/{c}/lock", json={"locked": True})
    # Select via production settings API; do not apply it to an already running host.
    settings = runtime.request("PUT", "/api/settings/session-lifecycle",
                               json={"startupPreference": "clear-illegal"})
    assert settings["startupPreference"] == "clear-illegal"
    assert runtime.request("GET", f"/api/sessions/{sid}")["illegalOffline"]
    runtime.stop()
    runtime.start()
    value = durable(runtime, sid)
    assert value["legal_offline"] and "recovery_requested" not in value
    assert all(op["state"] == "offline" for op in value["execution_operations"].values())
    rows = {row["id"]: row for row in value["queue_pending"]}
    assert not rows[b].get("queueLockSource")
    assert rows[c]["queueLockSource"] == "manual"
    detail = runtime.request("GET", f"/api/sessions/{sid}")
    assert detail["legalOffline"] and not detail["illegalOffline"] and not detail["workerId"]
    assert not _received(runtime, cwd=session["workdir"], text="B")
    batches = value["illegal_batches"]
    runtime.stop()
    runtime.start()
    assert durable(runtime, sid)["illegal_batches"] == batches


def test_manual_pending_recovery_and_safe_offline_are_one_state(runtime):
    session = _create(runtime, "manual-illegal-state")
    sid = session["id"]
    paused(runtime, sid, True)
    a = _enqueue(runtime, sid, "A", "A")["item"]["queueItemId"]
    b = _enqueue(runtime, sid, "B", "B")["item"]["queueItemId"]
    runtime.request("PATCH", f"/api/sessions/{sid}/queue/{a}/lock", json={"locked": True})
    marked = runtime.request("POST", f"/api/sessions/{sid}/legality/mark-illegal")
    assert marked["illegalOffline"] and not marked["legalOffline"]
    assert "recoveryRequested" not in marked
    value = durable(runtime, sid)
    assert "recovery_requested" not in value
    rows = {row["id"]: row for row in value["queue_pending"]}
    assert rows[a]["queueLockSource"] == "manual" and rows[b]["queueLockSource"] == "illegal"
    candidates = runtime.request("GET", "/api/sessions/recovery-candidates")
    assert sid in [row["id"] for row in candidates["sessions"]]
    batches = value["illegal_batches"]
    runtime.request("POST", f"/api/sessions/{sid}/legality/mark-illegal")
    assert durable(runtime, sid)["illegal_batches"] == batches
    safe = runtime.request("POST", f"/api/sessions/{sid}/legality/mark-safe-offline")
    assert safe["legalOffline"] and not safe["illegalOffline"]
    candidates = runtime.request("GET", "/api/sessions/recovery-candidates")
    assert sid not in [row["id"] for row in candidates["sessions"]]
    value = durable(runtime, sid)
    rows = {row["id"]: row for row in value["queue_pending"]}
    assert rows[a]["queueLockSource"] == "manual" and not rows[b].get("queueLockSource")
    assert not runtime.request("GET", f"/api/sessions/{sid}")["workerId"]


def test_safe_unlock_dispatches_without_waiting_for_watchdog(runtime, monkeypatch):
    session = _create(runtime, "immediate-safe-unlock")
    sid = session["id"]
    paused(runtime, sid, True)
    _enqueue(runtime, sid, "send-after-safe", "pending")
    runtime.request("POST", f"/api/sessions/{sid}/legality/mark-illegal")
    monkeypatch.setenv("PAN_TEST_DISABLE_QUEUE_WATCHDOG", "1")
    runtime.stop()
    runtime.start()
    paused(runtime, sid, False)  # Illegal-only backlog cannot start a Worker.
    assert not runtime.request("GET", f"/api/sessions/{sid}")["workerId"]
    result = runtime.request("POST", f"/api/sessions/{sid}/legality/mark-safe-offline")
    assert result["status"] == "updated"
    _wait_for(lambda: _received(runtime, cwd=session["workdir"], text="send-after-safe"),
              timeout=8, label="immediate handoff with watchdog delayed for an hour")
