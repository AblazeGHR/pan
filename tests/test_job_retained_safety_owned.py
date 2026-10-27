"""P23 real owned trees. Only the first identity probe may be fault-injected.

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
        with (root/'kills.jsonl').open('a') as out: out.write(json.dumps({'pid':handle.pid})+'\\n')
        tree = self.owner.kill_tree(handle, **kwargs)
        if (root/'uncertain-kill').exists():
            from packages.core.platform.process import TreeTerminationResult
            return TreeTerminationResult(tree.owned, (handle.pid,), False, 'injected lost termination acknowledgement')
        return tree
process.default_process_owner = ObservedOwner
raise SystemExit(run(root, sys.argv[2]))
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
    script.write_text('''import subprocess,sys,time,os,json
from pathlib import Path
ready,release,effects=map(Path,sys.argv[1:])
with effects.open('a') as f: f.write('effect\\n')
child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(90)'])
ready.write_text(json.dumps({'parent':os.getpid(),'child':child.pid}))
while not release.exists(): time.sleep(.02)
''', encoding="utf-8")
    runner = tmp_path / "supervisor.py"
    runner.write_text(RUNNER, encoding="utf-8")
    children = []
    store = ExecutionStore(root)

    def launch(executor_root, identity):
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith("PAN_")}
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
        child = subprocess.Popen([sys.executable, str(runner), str(executor_root), identity],
            env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
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
        release.touch()
        # Kill only our retained Popen supervisors if the assertion failed;
        # their original Job handles perform KILL_ON_JOB_CLOSE tree cleanup.
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
        assert not remaining(item.identities), "owned process leak"
        item.evidence.update(portReleased=True, ownedProcessesRemaining=[], supervisorsReaped=len(children))
        out = Path("evidence/p23-retained-safety") / (item.evidence.get("label", "owned") + ".json")
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
    owned.identities = [owner.identity(pid).as_dict() for pid in
                        [pids["parent"], pids["child"], owned.children[0].pid]]
    assert doc["supervisorIdentity"]["pid"] == owned.children[0].pid
    assert doc["evidence"]["processIdentity"]["pid"] == pids["parent"]
    assert all(owner.verify(record).verified_alive for record in owned.identities)
    assert all(record["kind"] == "filetime" and int(record["birth"]) > 0 for record in owned.identities)
    assert doc["evidence"]["processIdentity"]["birth"] == owned.identities[0]["birth"]
    owned.evidence.update(originalIdentityVerified=True, identityKind="filetime", ownedProcessCount=3)
    return doc, request


def exercise_cleanup(owned, first_unknown, request_cleanup=None):
    client, identity, attempt = owned.client, owned.identity, owned.attempt
    owner = default_process_owner()
    wrong = {**owned.identities[0], "birth": str(int(owned.identities[0]["birth"]) + 1)}
    check = owner.verify(wrong)
    assert check.ours is False and not check.verified_alive
    refused = owner.recover(wrong, terminate=True)
    assert refused.action == "refused_identity" and not refused.terminated
    assert len(remaining(owned.identities)) == 3
    owned.evidence["pidReuseStaleBirthRefused"] = True
    for field, value in [("leaseId", "stale"), ("attemptId", "stale"), ("ownerEpoch", 2), ("generation", 2)]:
        with pytest.raises(RuntimeError, match="409"):
            client.cleanup_execution(identity, {**attempt, field: value})
    if first_unknown:
        (owned.root / "deny-probe").touch()
    requested = (request_cleanup or (lambda: client.cleanup_execution(identity, attempt)))()
    assert requested["cleanupRequest"]["status"] in {"requested", "unknown", "confirmed"}
    doc = wait_for(lambda: client.get_execution(identity),
                   lambda row: row.get("cleanupRequest", {}).get("status") in {"confirmed", "unknown"})
    if first_unknown:
        assert doc["cleanupRequest"]["status"] == "unknown"
        assert doc["result"] is None and len(remaining(owned.identities)) == 3
        assert not (owned.root / "kills.jsonl").exists()
        # Cold process: real recovery, GET, and admission with the original
        # identity cannot execute, reset unknown, or enqueue another cleanup.
        cold = subprocess.run([sys.executable, "-c", """import sys
from packages.jobs.independent_executor import ExecutionStore
from packages.core.platform.execution_host import supervisor_alive
s=ExecutionStore(sys.argv[1]); s.recover(supervisor_alive)
d=s.get(sys.argv[2]); assert d['cleanupRequest']['status']=='unknown'
assert d['result'] is None
print('cold recovery retained unknown; no replay')
""", str(owned.root), identity], capture_output=True, text=True, timeout=10, **command_window_options())
        assert cold.returncode == 0, cold.stderr
        owned.evidence["coldRecoveryExit"] = cold.returncode
        (owned.root / "deny-probe").unlink()
        time.sleep(.15)
        assert client.get_execution(identity)["cleanupRequest"]["status"] == "unknown"
        assert not (owned.root / "kills.jsonl").exists()
        assert len(remaining(owned.identities)) == 3
        assert client.cleanup_execution(identity, attempt)["cleanupRequest"]["revision"] == 2
        doc = wait_for(lambda: client.get_execution(identity),
                       lambda row: row.get("cleanupRequest", {}).get("status") == "confirmed")
    proof = doc["cleanupRequest"]["proof"]
    assert proof["confirmed"] and proof["rootExited"]
    assert proof["identityCheck"]["ours"] is True
    assert proof["ownedTree"]["confirmed"] and proof["ownedTree"]["remaining"] == []
    assert {record["pid"] for record in owned.identities[:2]} <= set(proof["ownedTree"]["owned"])
    final = wait_for(lambda: client.get_execution(identity), lambda row: row["result"] is not None)
    assert final["result"]["outcome"] == "rejected"  # killing is not successful business execution
    assert final["result"]["cleanupProof"]["confirmed"] is True
    owned.children[0].wait(timeout=5)
    assert not remaining(owned.identities)
    again = client.cleanup_execution(identity, attempt)
    assert again["cleanupRequest"] == final["cleanupRequest"]
    assert len((owned.root / "kills.jsonl").read_text().splitlines()) == 1
    assert owned.effects.read_text().splitlines() == ["effect"]
    owned.evidence.update(firstUnknown=first_unknown, cleanupStatus="confirmed", treeMemberCount=len(proof["ownedTree"]["owned"]),
        cleanupCalls=1, effects=1, cleanupRevision=again["cleanupRequest"]["revision"])
    return final


@pytest.mark.skipif(os.name != "nt", reason="P23 Windows FILETIME/Job Object real-tree verification")
@pytest.mark.parametrize("first_unknown", [False, True])
def test_real_executor_cleanup_http(owned, first_unknown):
    owned.evidence["label"] = "backend-unknown-" + str(first_unknown).lower()
    doc, request = submit(owned)
    final = exercise_cleanup(owned, first_unknown)
    assert owned.client.submit_execution(request)["result"] == final["result"]
    adapter = IndependentExecutor(job_id=owned.job_id, client=owned.client)
    assert asyncio.run(adapter.cleanup_execution(attempt=owned.attempt, effect_key=owned.key))["cleanupRequest"]["status"] == "confirmed"
    assert len(owned.children) == 1
    events = owned.client.execution_events()["events"]
    assert len(events) == 1 and events[0]["attempt"] == doc["attempt"]
    assert owned.client.ack_execution(events[0]["eventId"])["ok"]
    assert owned.client.execution_events()["events"] == []


@pytest.mark.skipif(os.name != "nt", reason="P23 Windows FILETIME/Job Object real-tree verification")
def test_real_partial_cleanup_waits_for_explicit_recheck(owned):
    owned.evidence["label"] = "backend-partial-ack"
    submit(owned)
    (owned.root / "uncertain-kill").touch()
    owned.client.cleanup_execution(owned.identity, owned.attempt)
    doc = wait_for(lambda: owned.client.get_execution(owned.identity),
                   lambda row: row.get("cleanupRequest", {}).get("status") == "unknown")
    assert doc["result"] is None
    assert len((owned.root / "kills.jsonl").read_text().splitlines()) == 1
    assert not remaining(owned.identities[:2])
    assert remaining(owned.identities[2:])  # original supervisor/Job handle retained
    (owned.root / "uncertain-kill").unlink()
    time.sleep(.2)
    assert owned.client.get_execution(owned.identity)["cleanupRequest"]["status"] == "unknown"
    assert len((owned.root / "kills.jsonl").read_text().splitlines()) == 1
    owned.client.cleanup_execution(owned.identity, owned.attempt)
    final = wait_for(lambda: owned.client.get_execution(owned.identity), lambda row: row["result"] is not None)
    assert final["cleanupRequest"]["status"] == "confirmed"
    assert final["cleanupRequest"]["revision"] == 2
    assert final["result"]["cleanupProof"]["confirmed"] is True
    owned.children[0].wait(timeout=5)
    assert not remaining(owned.identities)
    assert len((owned.root / "kills.jsonl").read_text().splitlines()) == 2  # second explicit authorization
    assert owned.effects.read_text().splitlines() == ["effect"]
    owned.evidence.update(firstCleanupActuallyEndedTree=True, lostAcknowledgementKeptUnknown=True,
        noAutomaticCleanupRetry=True, explicitRecheckConfirmed=True, cleanupCalls=2, effects=1)
