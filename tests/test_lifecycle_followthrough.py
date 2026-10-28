"""Bounded canonical observation and controller-crash fixture contracts."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import psutil
import pytest

from packages.core import lifecycle_action
from packages.core.platform.execution_host import supervisor_alive, supervisor_exit_evidence
from packages.jobs.independent_executor import ExecutionHost, ExecutionStore, effect_id
from packages.jobs.window import command_window_options
from tests.support.lifecycle_observation import LifecycleObservation


def _attempt():
    return {"attemptId": "attempt_lifecycle", "runId": "run_lifecycle", "stepId": "step_main",
            "leaseId": "lease_lifecycle", "ownerEpoch": 1, "generation": 1, "state": "running"}


def _key():
    return {"runId": "run_lifecycle", "stepId": "step_main", "targetId": "main", "operationOrdinal": 0}


def _wait(read, ready, timeout=20):
    deadline = time.monotonic() + timeout
    value = None
    while time.monotonic() < deadline:
        value = read()
        if ready(value):
            return value
        time.sleep(.05)
    raise AssertionError(f"owned fixture deadline: {value!r}")


def test_observation_requires_existing_store_and_never_creates_it(tmp_path):
    with pytest.raises(FileNotFoundError):
        LifecycleObservation(tmp_path)
    assert not (tmp_path / "data").exists()


def test_observation_verifies_original_fence_and_persisted_frozen_options(tmp_path):
    pid, birth = os.getpid(), psutil.Process().create_time()
    options = {"exitStrategy": "ask", "markRunningSessionsOffline": False,
               "runningSessionIds": ["running-a", "running-b"]}
    args = dict(root=tmp_path, port=48879, old_pid=pid, old_birth=birth,
                operation="exit", options=options, request_id="request-observation-fixed")
    job, identity, plan = lifecycle_action.protected_plan(
        root=tmp_path, port=args["port"], old_pid=pid, old_pid_created_at=birth,
        operation="exit", options=options, request_id=args["request_id"])
    store = ExecutionStore(lifecycle_action.receipt_root(tmp_path))
    doc, created = store.admit(job_id=job, attempt=identity["attempt"],
                               effect_key=identity["effectKey"], frozen_plan=plan)
    assert created and doc["result"] is None
    observed = LifecycleObservation(tmp_path).read(**args)
    assert observed == doc and observed["frozenPlan"]["arguments"]["options"] == options
    assert observed["phase"] == "admitted" and observed["result"] is None
    assert store.get(doc["effectId"]) == doc
    with pytest.raises(ValueError, match="frozen plan mismatch"):
        LifecycleObservation(tmp_path).read(**{**args, "options": {**options, "markRunningSessionsOffline": True}})


@pytest.mark.parametrize("stage", ["after-submit-return", "before-submit-return"])
def test_controller_crash_retains_original_supervisor_and_cleans_owned_tree(tmp_path, stage):
    """A real controller dies after child publication; the original owner cleans up."""
    root = tmp_path / "store"
    root.mkdir()
    child_report = tmp_path / "child.json"
    controller_marker = tmp_path / "controller.json"
    request = {"jobId": "job_lifecycle_controller", "attempt": _attempt(), "effectKey": _key(),
        "frozenPlan": {"operation": "process", "cwd": str(tmp_path),
                       "argv": [sys.executable, str(Path(__file__).parent / "support" / "lifecycle_tree_owned.py"), str(child_report)]}}
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    controller = subprocess.Popen([sys.executable, str(Path(__file__).parent / "support" / "lifecycle_controller_owned.py"),
                                   str(root), str(request_path), stage, str(controller_marker)], cwd=str(Path(__file__).resolve().parents[1]),
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **command_window_options())
    wrapper_birth = psutil.Process(controller.pid).create_time()
    store = ExecutionStore(root)
    eid = effect_id(_key())
    doc = None
    try:
        def read_running():
            try:
                return store.get(eid)
            except KeyError:
                return None
        doc = _wait(read_running, lambda d: bool(d and d["phase"] == "running" and child_report.exists() and controller_marker.exists()))
        controller_id = json.loads(controller_marker.read_text(encoding="utf-8"))
        assert controller_id["stage"] == stage
        members = json.loads(child_report.read_text(encoding="utf-8"))
        supervisor = doc["supervisorIdentity"]
        assert supervisor_alive(supervisor)
        assert all(abs(psutil.Process(row["pid"]).create_time() - row["birth"]) < .01 for row in members)
        assert psutil.Process(members[1]["pid"]).ppid() == members[0]["pid"]
        root_pid = doc["evidence"]["processIdentity"]["pid"]
        assert root_pid in [p.pid for p in psutil.Process(members[0]["pid"]).parents()]
        actual_controller = psutil.Process(controller_id["pid"])
        assert abs(actual_controller.create_time() - controller_id["birth"]) < .01
        assert (actual_controller.pid == controller.pid or
                actual_controller.pid in [p.pid for p in psutil.Process(controller.pid).children(recursive=True)])
        actual_controller.kill()
        controller.wait(timeout=5)
        doc = store.get(eid)
        assert doc["attempt"] == _attempt() and doc["effectKey"] == _key()
        assert doc["phase"] == "running" and supervisor_alive(supervisor)
        assert all(abs(psutil.Process(row["pid"]).create_time() - row["birth"]) < .01 for row in members)
        requested = ExecutionHost(store).cleanup_execution(eid, _attempt())
        assert requested["cleanupRequest"]["status"] == "requested"
        final = _wait(lambda: store.get(eid), lambda d: d.get("result") is not None)
        assert final["attempt"] == _attempt() and final["effectKey"] == _key()
        assert final["cleanupRequest"]["status"] == "confirmed"
        proof = final["cleanupRequest"]["proof"]
        assert proof["confirmed"] and proof["rootExited"] and proof["identityCheck"]["ours"]
        assert proof["ownedTree"]["confirmed"] and proof["ownedTree"]["remaining"] == []
        supervisor_exit = _wait(lambda: supervisor_exit_evidence(supervisor), lambda p: p["confirmed"])
        for row in members:
            def exited():
                try:
                    return abs(psutil.Process(row["pid"]).create_time() - row["birth"]) >= .01
                except psutil.NoSuchProcess:
                    return True
            _wait(exited, bool)
        assert final["history"] == [] and len(store.events()["events"]) == 1
        # New runs preserve the immutable v2 proof as an ancestor.
        target = Path("evidence/cp-x1-strict-successors-20261029/controller-v2-rerun")
        target.mkdir(parents=True, exist_ok=True)
        (target / (stage + ".json")).write_text(json.dumps({
            "controller": controller_id, "controllerWrapperPid": controller.pid,
            "controllerWrapperBirth": wrapper_birth, "supervisor": supervisor,
            "members": members, "attempt": final["attempt"], "effectId": eid,
            "phase": final["phase"], "resultOutcome": final["result"]["outcome"],
            "cleanupRequest": final["cleanupRequest"], "supervisorExit": supervisor_exit,
            "membersExited": True, "outboxEvents": 1}, indent=2), encoding="utf-8")
    finally:
        if controller_marker.exists():
            owned = json.loads(controller_marker.read_text(encoding="utf-8"))
            try:
                process = psutil.Process(owned["pid"])
                if abs(process.create_time() - owned["birth"]) < .01:
                    process.kill()
            except psutil.NoSuchProcess:
                pass
        if controller.poll() is None:
            assert abs(psutil.Process(controller.pid).create_time() - wrapper_birth) < .01
            controller.kill()
            controller.wait(timeout=5)
        try:
            remaining = store.get(eid)
            if remaining["phase"] == "running" and remaining.get("result") is None:
                ExecutionHost(store).cleanup_execution(eid, _attempt())
            if remaining.get("supervisorIdentity"):
                _wait(lambda: supervisor_exit_evidence(remaining["supervisorIdentity"]), lambda p: p["confirmed"])
        except KeyError:
            pass
