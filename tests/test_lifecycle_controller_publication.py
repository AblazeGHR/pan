"""Real canonical Job cancellation after controller pre-publication crash."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
import venv

import psutil
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from packages.automation.service import Service as Automation
from packages.core.platform.execution_host import supervisor_exit_evidence
from packages.jobs import final_api
from packages.jobs.final_service import bind, object_ref
from packages.jobs.independent_executor import ExecutionHost, ExecutionStore
from packages.jobs.window import command_window_options


def wait(read, predicate, timeout=25):
    deadline = time.monotonic() + timeout
    value = None
    while time.monotonic() < deadline:
        value = read()
        if predicate(value):
            return value
        time.sleep(.03)
    raise AssertionError(f"owned canonical fixture deadline: {value!r}")


def alive(row):
    try:
        return abs(psutil.Process(row["pid"]).create_time() - row["birth"]) < .01
    except psutil.NoSuchProcess:
        return False


@pytest.mark.parametrize("runtime_kind", ["base", "named-venv"])
@pytest.mark.parametrize("crash_point", ["before-claim", "after-running"])
def test_canonical_job_cancel_after_controller_crash(tmp_path, runtime_kind, crash_point, request):
    runtime = Path(sys._base_executable)
    if runtime_kind == "named-venv":
        envdir = tmp_path / "named-cpx1-venv"
        venv.EnvBuilder(with_pip=False, system_site_packages=True).create(envdir)
        runtime = envdir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        assert runtime.is_file() and (envdir / "pyvenv.cfg").is_file()
    app = FastAPI()
    automation = Automation(tmp_path / "automation", SimpleNamespace(workers={}), None)
    automation.auth = final_api.CanonicalUserAuth(tmp_path / "automation")
    service = bind(app, automation, tmp_path / "canonical")
    app.state.automation = automation
    from packages.jobs.action_api import router as actions
    from packages.jobs.api import router as jobs
    final_api.install(app, actions, jobs)
    auth = automation.auth.login(automation.auth.key())
    controller = None
    controller_id = runner_id = None
    wrapper_birth = None
    store = None
    eid = attempt = None
    members = []
    with TestClient(app, headers={"Authorization": "Bearer " + auth}) as client:
        try:
            action = next(a for a in service.actions(actor={"type": "user", "id": "local"})["items"]
                          if a["id"] == "product_process_cleanup_v1")
            tree = Path(__file__).parent / "support" / "lifecycle_strict_job_tree.py"
            body = {"name": "owned publication crash", "steps": [{"stepId": "process",
                "actionRef": object_ref("action", action), "arguments": {
                    "argv": {"from": "literal", "value": [sys.executable, str(tree), str(tmp_path), "2"]},
                    "cwd": {"from": "literal", "value": str(tmp_path)}},
                "completion": {"contract": "process-exit"}}], "retention": {"policyRef": "test"}}
            response = client.post("/api/jobs", json={"definition": body, "expectedRevision": 0, "requestKey": "create"})
            assert response.status_code == 201, response.text
            job = response.json()["result"]["object"]
            response = client.post("/api/jobs/" + job["id"] + "/operations", json={"operation": "enable",
                "expectedRevision": job["configRevision"], "expectedStateRevision": job["stateRevision"],
                "parameters": {}, "requestKey": "enable"})
            assert response.status_code == 200, response.text
            job = response.json()["result"]["object"]
            response = client.post("/api/jobs/" + job["id"] + "/runs", json={"expectedRevision": job["configRevision"],
                "requestKey": "run", "input": {}, "cause": {"type": "manual"}})
            assert response.status_code == 202, response.text
            rid = response.json()["runId"]
            helper = Path(__file__).parent / "support" / "lifecycle_canonical_job_controller.py"
            controller = subprocess.Popen([sys.executable, str(helper), str(service.root),
                str(tmp_path / "automation"), str(runtime), str(tmp_path)],
                cwd=str(Path(__file__).resolve().parents[1]), stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, **command_window_options())
            wrapper_birth = psutil.Process(controller.pid).create_time()
            runner_ready, controller_ready = tmp_path / "runner-ready.json", tmp_path / "controller-ready.json"
            wait(lambda: runner_ready.exists() and controller_ready.exists(), bool)
            runner_id = json.loads(runner_ready.read_text(encoding="utf-8"))
            controller_id = json.loads(controller_ready.read_text(encoding="utf-8"))
            assert runner_id["stage"] == "after_bootstrap_before_claim" and runner_id["bootstrapExited"]
            assert runner_id["bootstrapExitCode"] == 0 and alive(runner_id) and alive(controller_id)
            if runtime_kind == "named-venv":
                assert Path(runner_id["prefix"]).resolve() == envdir.resolve()
                assert runner_id["prefix"] != runner_id["basePrefix"]
                assert Path(runner_id["executable"]).resolve() == runtime.resolve()
            else:
                assert runner_id["prefix"] == runner_id["basePrefix"]
                assert Path(runner_id["executable"]).resolve() == runtime.resolve()
            store = ExecutionStore(service.root / "executions")
            with store.connect() as db:
                documents = [json.loads(row[0]) for row in db.execute("SELECT document FROM executions")]
            assert len(documents) == 1
            before = documents[0]
            eid, attempt = before["effectId"], before["attempt"]
            assert before["jobId"] == job["id"] and attempt["runId"] == rid
            assert before["phase"] == "admitted" and before["claim"] is None and "supervisorIdentity" not in before
            assert not (tmp_path / "effects.txt").exists()
            assert all(not (tmp_path / f"member-{i}.json").exists() for i in range(3))
            actual_controller = psutil.Process(controller_id["pid"])
            assert actual_controller.pid == controller.pid or actual_controller.pid in [p.pid for p in psutil.Process(controller.pid).children(recursive=True)]
            if crash_point == "before-claim":
                actual_controller.kill()
                controller.wait(timeout=5)
                assert alive(runner_id) and store.get(eid)["claim"] is None
            (tmp_path / "runner-release").touch()
            doc = wait(lambda: store.get(eid), lambda value: value["phase"] == "running" and all(
                (tmp_path / f"member-{i}.json").exists() for i in range(3)))
            assert doc["attempt"] == attempt and doc["supervisorIdentity"]["pid"] == runner_id["pid"]
            members = [json.loads((tmp_path / f"member-{i}.json").read_text()) for i in range(3)]
            assert all(alive(row) for row in members)
            # Windows venv launchers insert real intermediary Python PIDs;
            # verify the recorded identities in the live ancestry instead of
            # assuming each Popen is the eventual interpreter process.
            assert members[1]["pid"] in [p.pid for p in psutil.Process(members[0]["pid"]).parents()]
            assert members[2]["pid"] in [p.pid for p in psutil.Process(members[1]["pid"]).parents()]
            if crash_point == "after-running":
                assert alive(controller_id)
                actual_controller.kill()
                controller.wait(timeout=5)
                assert alive(runner_id) and all(alive(row) for row in members)
                assert store.get(eid)["attempt"] == attempt
            run = client.get("/api/job-runs/" + rid).json()
            response = client.post("/api/job-runs/" + rid + "/operations", json={"operation": "cancel",
                "expectedStateRevision": run["stateRevision"], "requestKey": "cancel-original"})
            assert response.status_code == 202, response.text
            def refresh():
                asyncio.run(service.recover_external_receipts())
                return client.get("/api/job-runs/" + rid).json()
            terminal = wait(refresh, lambda value: value["state"] == "cancelled")
            doc = store.get(eid)
            assert doc["cleanupRequest"]["status"] == "confirmed"
            assert doc["cleanupRequest"]["proof"]["ownedTree"]["remaining"] == []
            wait(lambda: all(not alive(row) for row in members), bool)
            supervisor_exit = wait(lambda: supervisor_exit_evidence(doc["supervisorIdentity"]), lambda proof: proof["confirmed"])
            assert (tmp_path / "effects.txt").read_text().splitlines() == ["once"]
            override = os.environ.get("CPX1_LIFECYCLE_EVIDENCE_ROOT")
            target = Path(override) / "controller" if override else Path("evidence/cp-x1-strict-successors-20261029/controller")
            target.mkdir(parents=True, exist_ok=True)
            evidence = {"runtimeKind": runtime_kind,
                "crashPoint": crash_point,
                "controller": controller_id, "controllerWrapperPid": controller.pid,
                "controllerWrapperBirth": wrapper_birth, "beforeClaim": {"phase": before["phase"],
                "claim": before["claim"], "supervisorPublished": "supervisorIdentity" in before},
                "runnerBeforeClaim": runner_id, "jobId": job["id"], "runId": rid,
                "effectId": eid, "attempt": attempt, "supervisor": doc["supervisorIdentity"],
                "members": members, "cleanup": doc["cleanupRequest"], "runState": terminal["state"],
                "supervisorExit": supervisor_exit, "membersExited": True, "effectCount": 1}
            (target / (runtime_kind + "-" + crash_point + ".json")).write_text(json.dumps(evidence, indent=2), encoding="utf-8")
            if request.config.getoption("--cpx1-require-hidden-desktop", default=False):
                from tests.test_job_lifecycle_prevention import desktop_gone, until
                assert os.name == "nt"
                desktop = members[2]["desktop"]
                assert all(row["desktop"] == desktop for row in members)
                # Original desktop_gone assertion: use the actual observed
                # owned desktop name, never fabricate a name or accept Default.
                assert until(lambda: desktop_gone(desktop))
        finally:
            (tmp_path / "runner-abort").touch()
            if runner_id is None and (tmp_path / "runner-ready.json").exists():
                runner_id = json.loads((tmp_path / "runner-ready.json").read_text(encoding="utf-8"))
            if controller_id and alive(controller_id):
                psutil.Process(controller_id["pid"]).kill()
            if controller and controller.poll() is None:
                # A Windows venv shim can own the actual controller child.
                # Only this Popen tree and this exact helper command qualify.
                parent = psutil.Process(controller.pid)
                assert abs(parent.create_time() - wrapper_birth) < .01
                for child in parent.children(recursive=True):
                    try:
                        birth = child.create_time()
                        if str(helper) in " ".join(child.cmdline()) and abs(child.create_time() - birth) < .01:
                            child.kill()
                    except psutil.NoSuchProcess:
                        pass
                controller.kill()
                controller.wait(timeout=5)
            if store is not None and eid is not None:
                doc = store.get(eid)
                if doc["phase"] == "running" and doc.get("result") is None:
                    ExecutionHost(store).cleanup_execution(eid, attempt)
                if doc.get("supervisorIdentity"):
                    wait(lambda: supervisor_exit_evidence(doc["supervisorIdentity"]), lambda proof: proof["confirmed"])
            if runner_id and not (tmp_path / "runner-release").exists():
                wait(lambda: not alive(runner_id), bool)
