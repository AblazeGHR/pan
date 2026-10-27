"""CP-X2 dedicated POSIX owned fixture derived from the P23 HTTP harness. Only the first identity probe may be fault-injected.

No fake process, tree, kill, cleanup proof, or successful terminal receipt.
The test launcher runs the production canonical runner in an owned subprocess.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import psutil
import pytest

from packages.core.platform.process import default_process_owner
from packages.jobs.independent_client import IndependentJobClient
from packages.jobs.independent_executor import ExecutionHost, ExecutionStore, IndependentExecutor
from packages.jobs.independent_service import Handler, JobStore
from packages.jobs.window import command_window_options
from http.server import ThreadingHTTPServer


RUNNER = '''
import json,sys
from pathlib import Path
from packages.core.platform import process
from packages.core.platform.process import IdentityCheck, ProcessStatus
from packages.jobs.independent_execution_runner import run
import time
original = process.default_process_owner
root = Path(sys.argv[1])
class ObservedOwner:
    def __init__(self): self.owner = original()
    def __getattr__(self, name): return getattr(self.owner, name)
    def verify(self, record):
        if (root/'deny-probe').exists():
            return IdentityCheck(ProcessStatus.UNKNOWN, None, detail='injected access-denied probe')
        return self.owner.verify(record)
    def kill_tree(self, handle, **kwargs):
        tree = self.owner.kill_tree(handle, **kwargs)
        with (root/'kills.jsonl').open('a') as out: out.write(json.dumps({'pid':handle.pid})+'\\n')
        return tree
process.default_process_owner = ObservedOwner
code = run(root, sys.argv[2])
while (root/'hold-supervisor-exit').exists(): time.sleep(.02)
raise SystemExit(code)
'''


def wait_for(read, predicate, timeout=15):
    deadline = time.monotonic() + timeout
    value = None
    while time.monotonic() < deadline:
        value = read()
        if predicate(value):
            return value
        time.sleep(.02)
    raise AssertionError(f"owned test deadline: {value!r}")


def remaining(identities):
    alive = []
    for record in identities:
        check = default_process_owner().verify(record)
        if check.verified_alive:
            alive.append(record["pid"])
            continue
        # Windows cannot reopen a PID after process-object deletion. psutil's
        # NoSuchProcess is used ONLY for the postcondition, never to grant kill.
        try:
            proc = psutil.Process(record["pid"])
            if proc.is_running() and check.ours is not False:
                alive.append(record["pid"])
        except psutil.NoSuchProcess:
            pass
    return alive


@pytest.fixture
def owned(tmp_path):
    root = tmp_path / "executor"
    root.mkdir()
    script = tmp_path / "effect.py"
    ready, release, effects = (tmp_path / name for name in ("ready.json", "release", "effects.jsonl"))
    (tmp_path / "child.py").write_text("from pathlib import Path\nimport sys,time\nwhile not Path(sys.argv[1]).exists(): time.sleep(.02)\n", encoding="utf-8")
    script.write_text('''import subprocess,sys,time,os,json
from pathlib import Path
ready,release,effects=map(Path,sys.argv[1:])
with effects.open('a') as f: f.write('effect\\n')
child=subprocess.Popen([sys.executable,str(Path(__file__).with_name('child.py')),str(release)])
ready.write_text(json.dumps({'parent':os.getpid(),'child':child.pid}))
while not release.exists():
    if ready.with_name('leader-exit').exists(): sys.exit(0)
    time.sleep(.02)
child.wait(5)
''', encoding="utf-8")
    runner = tmp_path / "supervisor.py"
    runner.write_text(RUNNER, encoding="utf-8")
    children = []
    store = ExecutionStore(root)

    def launch(executor_root, identity):
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith("PAN_")}
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
        child = subprocess.Popen([sys.executable, str(runner), str(executor_root), identity],
            env=env, start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, **command_window_options())
        children.append(child)
        return child

    host = ExecutionHost(store, launch=launch)
    container = JobStore(str(root))
    container._execution_host = host
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    assert server.server_port not in (8767, 8768)
    server.daemon_threads = True
    server.store = container
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = IndependentJobClient(f"http://127.0.0.1:{server.server_port}")
    item = SimpleNamespace(root=root, store=store, host=host, client=client, children=children,
        ready=ready, release=release, effects=effects, identities=[], tmp_path=tmp_path,
        plan={"operation": "process", "effect": "write_local", "arguments": {
            "argv": [sys.executable, str(script), str(ready), str(release), str(effects)], "cwd": str(tmp_path)}},
        attempt={"attemptId": "attempt_original", "leaseId": "lease_original", "runId": "run_original",
                 "stepId": "step_original", "ownerEpoch": 1, "generation": 1, "state": "running"},
        key={"runId": "run_original", "stepId": "step_original", "targetId": "original", "operationOrdinal": 0},
        job_id="job_original", evidence={"executorPort": server.server_port})
    try:
        yield item
    finally:
        (root / "hold-supervisor-exit").unlink(missing_ok=True)
        (root / "deny-probe").unlink(missing_ok=True)
        if getattr(item, "identity", None):
            doc = client.get_execution(item.identity)
            if doc.get("result") is None and doc.get("evidence", {}).get("processIdentity"):
                client.cleanup_execution(item.identity, item.attempt)
                wait_for(lambda: client.get_execution(item.identity), lambda d: d.get("cleanupRequest", {}).get("status") in {"confirmed", "unknown"})
        release.touch()
        # The fixture owns these direct, retained Popen children. Reap first;
        # on a failed assertion a supervisor may deliberately be holding unknown.
        # Cooperative release reaps the effect's child before its leader exits.
        for child in children:
            try:
                child.wait(timeout=8)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
            if child.stderr:
                child.stderr.close()
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()
        with socket.socket() as probe:
            assert probe.connect_ex(("127.0.0.1", server.server_port)) != 0
        wait_for(lambda: remaining(item.identities), lambda pids: not pids)
        item.evidence.update(portReleased=True, ownedProcessesRemaining=[], supervisorsReaped=len(children))
        interpreter = "venv" if sys.prefix != sys.base_prefix else "native"
        item.evidence["interpreter"] = interpreter
        out = Path("artifacts/cp-x2") / interpreter / (item.evidence.get("label", "owned") + ".json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(item.evidence, indent=2), encoding="utf-8")


def submit(owned):
    request = {"jobId": owned.job_id, "attempt": owned.attempt, "effectKey": owned.key, "frozenPlan": owned.plan}
    doc = owned.client.submit_execution(request)
    owned.identity = doc["effectId"]
    doc = wait_for(lambda: owned.client.get_execution(owned.identity),
                   lambda row: row["phase"] == "running" and owned.ready.exists())
    pids = json.loads(owned.ready.read_text())
    owner = default_process_owner()
    # Bind both the effect root and original supervisor from trusted receipts.
    root_pid = doc["evidence"]["processIdentity"]["pid"]
    supervisor_pid = doc["supervisorIdentity"]["pid"]
    effect_pids = list(dict.fromkeys([root_pid, pids["parent"], pids["child"]]))
    supervisor_pids = list(dict.fromkeys([supervisor_pid, owned.children[0].pid]))
    owned.effect_identities = [owner.identity(pid).as_dict() for pid in effect_pids]
    owned.supervisor_identities = [owner.identity(pid).as_dict() for pid in supervisor_pids]
    owned.identities = owned.effect_identities + owned.supervisor_identities
    if pids["parent"] != root_pid:
        assert root_pid in [p.pid for p in psutil.Process(pids["parent"]).parents()]
    if supervisor_pid != owned.children[0].pid:
        assert owned.children[0].pid in [p.pid for p in psutil.Process(supervisor_pid).parents()]
    assert all(owner.verify(record).verified_alive for record in owned.identities)
    assert all(record["kind"] == "starttime" and record["boot_id"] for record in owned.identities)
    assert doc["evidence"]["processIdentity"]["birth"] == owned.identities[0]["birth"]
    assert doc["supervisorIdentity"]["birth"] == owned.supervisor_identities[0]["birth"]
    owned.evidence.update(originalIdentityVerified=True, identityKind="starttime", ownedProcessCount=len(owned.identities),
        launcherIsSupervisor=supervisor_pid == owned.children[0].pid, ownershipRootIsScript=root_pid == pids["parent"])
    return doc, request




import httpx
import uvicorn
from fastapi import FastAPI

from packages.automation.service import Service as Automation
from packages.jobs import final_api
from packages.jobs.final_service import bind, object_ref
from packages.jobs.independent_executor import effect_id

USER = {"type": "user", "id": "local"}


@pytest.fixture
def canonical(owned):
    app = FastAPI()
    automation = Automation(owned.tmp_path / "automation", SimpleNamespace(workers={}), None)
    automation.auth = final_api.CanonicalUserAuth(owned.tmp_path / "automation")
    service = bind(app, automation, owned.tmp_path / "canonical")
    app.state.automation = automation
    from packages.jobs.action_api import router as action_router
    from packages.jobs.api import router as facade_router
    final_api.install(app, action_router, facade_router)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    assert port not in (8767, 8768)
    server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    wait_for(lambda: server.started, bool)
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10,
        headers={"Authorization": "Bearer " + automation.auth.login(automation.auth.key())})
    state = SimpleNamespace(service=service, client=client, release_receipt=threading.Event(), dispatch=None,
                            dispatch_errors=[], owned=owned)
    try:
        yield state
    finally:
        owned.release.touch()
        state.release_receipt.set()
        if state.dispatch:
            if state.dispatch.is_alive() and owned.children:
                # Failure-only teardown of this fixture's retained supervisors.
                for child in owned.children:
                    if child.poll() is None:
                        child.kill()
                        child.wait(5)
                owned.store.recover(lambda _: False)
            state.dispatch.join(15)
            assert not state.dispatch.is_alive()
        client.close()
        server.should_exit = True
        thread.join(5)
        sock.close()
        assert not thread.is_alive()
        asyncio.run(service.close())
        with socket.socket() as probe:
            assert probe.connect_ex(("127.0.0.1", port)) != 0
        owned.evidence.update(canonicalPort=port, canonicalPortReleased=True)


def launch(c, *, default_pending=False, action_id="product_process_cleanup_v1", queued=False):
    o = c.owned
    action = next(item for item in c.service.actions(actor=USER)["items"] if item["id"] == action_id)
    definition = {"name": "retained safety canonical parent", "steps": [{"stepId": "owned",
        "actionRef": object_ref("action", action),
        "arguments": {key: {"from": "literal", "value": value} for key, value in o.plan["arguments"].items()},
        "completion": {"contract": "process-exit"}}], "retention": {"policyRef": "test"}}
    response = c.client.post("/api/jobs", json={"definition": definition, "expectedRevision": 0, "requestKey": "create"})
    assert response.status_code == 201, response.text
    job = response.json()["result"]["object"]
    response = c.client.post("/api/jobs/" + job["id"] + "/operations", json={"operation": "enable",
        "expectedRevision": job["configRevision"], "expectedStateRevision": job["stateRevision"],
        "parameters": {}, "requestKey": "enable"})
    assert response.status_code == 200, response.text
    job = response.json()["result"]["object"]
    c.job = job
    o.job_id = job["id"]
    adapter = IndependentExecutor(job_id=job["id"], client=o.client)

    class AwaitOriginalReceipt:
        """Executor port waits for the actual host result, never synthesizes it.

        Holding terminal delivery models an in-flight network callback. This
        does not claim default execution_pending recovery is already wired.
        """
        cleanup_execution = adapter.cleanup_execution
        lookup_execution = adapter.lookup_execution
        lookup_receipt = adapter.lookup_receipt

        async def execute(self, *, attempt, effect_key, frozen_plan, retry=False):
            o.attempt, o.key, o.plan = attempt, effect_key, frozen_plan
            pending = await adapter.execute(attempt=attempt, effect_key=effect_key, frozen_plan=frozen_plan, retry=retry)
            o.identity = effect_id(effect_key)
            if pending["receipt"].get("code") != "execution_pending":
                return pending
            while True:
                doc = await asyncio.to_thread(o.client.get_execution, o.identity)
                if doc["result"] is not None:
                    assert await asyncio.to_thread(c.release_receipt.wait, 20), "terminal delivery gate timed out"
                    return doc["result"]
                await asyncio.sleep(.02)

    if default_pending:
        from packages.actions.typed_executor import TypedExecutor
        c.service.executor = TypedExecutor(independent_executor=adapter)
    else:
        c.service.executor = AwaitOriginalReceipt()
    response = c.client.post("/api/jobs/" + job["id"] + "/runs", json={"expectedRevision": job["configRevision"],
        "requestKey": "run", "input": {}, "cause": {"type": "manual"}})
    assert response.status_code == 202, response.text
    c.run_id = response.json()["runId"]
    c.run_operation_id = response.json()["operationId"]
    if queued:
        return job

    def dispatch():
        try:
            asyncio.run(c.service.dispatch_once())
        except BaseException as exc:
            c.dispatch_errors.append(repr(exc))
    c.dispatch = threading.Thread(target=dispatch, daemon=True)
    c.dispatch.start()
    if default_pending:
        c.dispatch.join(10)
        assert not c.dispatch.is_alive() and c.dispatch_errors == []
        state = c.service.final.store.get(job["id"])["finalRuntime"]
        effect, = state["effects"]
        o.attempt, o.key = state["attempts"][effect["attemptId"]], effect["effectKey"]
        # The host's original Attempt remains running; only canonical's copy
        # became uncertain after execution_pending.
        o.attempt = {**o.attempt, "state": "running"}
        o.identity = effect_id(o.key)
    else:
        wait_for(lambda: getattr(o, "identity", None), bool)
    # Idempotent duplicate admission must query the same executor identity.
    doc, request = submit(o)
    assert doc["jobId"] == job["id"] and doc["attempt"]["runId"] == c.run_id
    assert len(o.children) == 1
    return job


def delete(c, key):
    job = c.client.get("/api/jobs/" + c.job["id"]).json()
    return c.client.request("DELETE", "/api/jobs/" + job["id"], json={"expectedRevision": job["configRevision"],
        "expectedStateRevision": job["stateRevision"], "requestKey": key})
