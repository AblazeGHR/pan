"""Original business output through real default canonical process execution."""
import asyncio
import base64
import copy
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from packages.automation.service import Service as Automation
from packages.jobs import final_api, final_contracts as fc
from packages.jobs.final_service import bind, FinalService, object_ref
from packages.jobs.independent_executor import ExecutionStore, ExecutionHost, IndependentExecutor, effect_id
from packages.jobs.process_logs import open_log, reference, read_log
from packages.jobs.window import command_window_options
from tests.test_job_final_independent_execution import attempt, key, process_plan

USER = {"type": "user", "id": "local"}


def wait(read, predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = read()
        if predicate(value):
            return value
        time.sleep(.03)
    raise AssertionError("process log deadline: " + repr(value))


@pytest.fixture
def live(tmp_path):
    app = FastAPI()
    automation = Automation(tmp_path / "automation", SimpleNamespace(workers={}), None)
    automation.auth = final_api.CanonicalUserAuth(tmp_path / "automation")
    service = bind(app, automation, tmp_path / "canonical")
    app.state.automation = automation
    from packages.jobs.action_api import router as actions
    from packages.jobs.api import router as jobs
    final_api.install(app, actions, jobs)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    assert port not in (8767, 8768)
    server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    wait(lambda: server.started, bool)
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10,
        headers={"Authorization": "Bearer " + automation.auth.login(automation.auth.key())})
    item = SimpleNamespace(service=service, client=client, auth=automation.auth, tmp=tmp_path, root=service.root,
                           port=port, app=app, checks={})
    try:
        yield item
    finally:
        # Only documents in this fixture's own root can identify cleanup targets.
        from packages.core.platform.execution_host import supervisor_alive
        store = ExecutionStore(service.root / "executions")
        with store.connect() as db:
            documents = [json.loads(row[0]) for row in db.execute("SELECT document FROM executions")]
        for doc in documents:
            if doc.get("supervisorIdentity") and supervisor_alive(doc["supervisorIdentity"]):
                ExecutionHost(store).cleanup_execution(doc["effectId"], doc["attempt"])
                wait(lambda: supervisor_alive(doc["supervisorIdentity"]), lambda alive: not alive)
        client.close()
        server.should_exit = True
        thread.join(8)
        sock.close()
        assert not thread.is_alive()
        asyncio.run(service.close())
        with socket.socket() as probe:
            assert probe.connect_ex(("127.0.0.1", port)) != 0
        item.checks.update(portReleased=True, supervisorsRemaining=[], interpreter=sys.executable)
        out = Path("evidence/p23-process-logs") / ("venv" if sys.prefix != sys.base_prefix else "system")
        out.mkdir(parents=True, exist_ok=True)
        (out / (tmp_path.name + ".json")).write_text(json.dumps(item.checks, indent=2), encoding="utf-8")


def start(c, code):
    script = c.tmp / "business.py"
    script.write_text(code, encoding="utf-8")
    action = next(a for a in c.service.actions(actor=USER)["items"] if a["id"] == "product_process_cleanup_v1")
    body = {"name": "original process logging", "steps": [{"stepId": "process",
        "actionRef": object_ref("action", action), "arguments": {
            "argv": {"from": "literal", "value": [sys.executable, str(script)]},
            "cwd": {"from": "literal", "value": str(c.tmp)}}, "completion": {"contract": "process-exit"}}],
        "retention": {"policyRef": "test"}}
    result = c.client.post("/api/jobs", json={"definition": body, "expectedRevision": 0, "requestKey": "create"})
    assert result.status_code == 201, result.text
    job = result.json()["result"]["object"]
    result = c.client.post("/api/jobs/" + job["id"] + "/operations", json={"operation": "enable",
        "expectedRevision": job["configRevision"], "expectedStateRevision": job["stateRevision"],
        "parameters": {}, "requestKey": "enable"})
    assert result.status_code == 200, result.text
    c.job = result.json()["result"]["object"]
    result = c.client.post("/api/jobs/" + job["id"] + "/runs", json={"expectedRevision": c.job["configRevision"],
        "requestKey": "run", "input": {}, "cause": {"type": "manual"}})
    assert result.status_code == 202, result.text
    c.rid = result.json()["runId"]
    asyncio.run(c.service.dispatch_once())  # Actual default detached executor.
    state = c.service.final.store.get(job["id"])["finalRuntime"]
    c.aid, = state["attempts"]
    c.attempt = state["attempts"][c.aid]
    row, = state["effects"]
    c.key = row["effectKey"]
    c.store = ExecutionStore(c.root / "executions")
    c.eid = effect_id(c.key)
    c.url = f"/api/job-runs/{c.rid}/attempts/{c.aid}/log"


def terminal(c, expected="completed"):
    def refresh():
        asyncio.run(c.service.recover_external_receipts())
        return c.client.get("/api/job-runs/" + c.rid).json()
    wait(refresh, lambda run: run["state"] == expected)
    doc = c.store.get(c.eid)
    assert doc["result"]["receipt"]["logRef"] == doc["logRef"]
    assert doc["result"]["cleanupProof"]["ownedTree"]["confirmed"]
    from packages.core.platform.execution_host import supervisor_exit_evidence
    wait(lambda: supervisor_exit_evidence(doc["supervisorIdentity"]), lambda p: p["confirmed"])
    c.checks.update(originalFence=True, ownedTreeConfirmed=True, supervisorExited=True)
    return doc


def all_bytes(c, limit=65536):
    data, offset, texts = bytearray(), 0, []
    while True:
        response = c.client.get(c.url, params={"offset": offset, "limit": limit})
        assert response.status_code == 200, response.text
        page = response.json()
        raw = base64.b64decode(page["bytesBase64"])
        assert len(raw) <= limit and page["nextOffset"] == offset + len(raw)
        data.extend(raw)
        texts.append(page["text"])
        if page["eof"]:
            return bytes(data), texts
        assert page["nextOffset"] > offset, page
        offset = page["nextOffset"]


def cold_read(c):
    code = """import asyncio,json,sys
from packages.jobs.final_service import FinalService
from packages.jobs.final_api import CanonicalUserAuth
s=FinalService(sys.argv[1],CanonicalUserAuth(sys.argv[2]))
s.recover()
r=asyncio.run(s.read_process_log(sys.argv[3],sys.argv[4],actor={'type':'user','id':'local'},limit=65536))
print(json.dumps(r))
asyncio.run(s.close())
"""
    result = subprocess.run([sys.executable, "-c", code, str(c.root), str(c.tmp / "automation"), c.rid, c.aid],
                            capture_output=True, text=True, timeout=15, **command_window_options())
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_default_real_large_unicode_combined_log_cold_reopen_and_archive(live):
    c = live
    chunk = "输出🙂 stderr你好\n".encode()
    child_code = "import os,time; os.write(1,b'child-output\\n'); time.sleep(30)"
    start(c, "import os,sys,subprocess\n"
        "open('effects','a').write('once\\n')\n"
        f"chunk={chunk!r}\n"
        "for i in range(7000):\n os.write(1,chunk); os.write(2,b'ERR\\n')\n"
        f"child=subprocess.Popen([sys.executable,'-c',{child_code!r}])\n"
        "open('child-pid','w').write(str(child.pid))\n"
        "import time; time.sleep(.3)\n")
    doc = terminal(c)
    raw, texts = all_bytes(c, limit=4093)
    assert len(raw) > 150000 and b"child-output\n" in raw
    assert raw.count(chunk) == 7000 and raw.count(b"ERR\n") == 7000
    assert "".join(texts).encode() == raw
    import psutil
    assert not psutil.pid_exists(int((c.tmp / "child-pid").read_text()))
    # A cold interpreter queries by original fenced identity, without submit.
    code = """import asyncio,json,sys
from packages.jobs.final_service import FinalService
from packages.jobs.final_api import CanonicalUserAuth
s=FinalService(sys.argv[1],CanonicalUserAuth(sys.argv[2]))
r=asyncio.run(s.read_process_log(sys.argv[3],sys.argv[4],actor={'type':'user','id':'local'},limit=65536))
assert r['logRef']['attempt']['attemptId']==sys.argv[4]
print(json.dumps({'ref':r['logRef'],'nextOffset':r['nextOffset']}))
asyncio.run(s.close())
"""
    cold = subprocess.run([sys.executable, "-c", code, str(c.root), str(c.tmp / "automation"), c.rid, c.aid],
                          capture_output=True, text=True, timeout=15, **command_window_options())
    assert cold.returncode == 0, cold.stderr
    assert json.loads(cold.stdout)["ref"] == doc["logRef"]
    # Existing pin/deletion policy still applies, and archive retains logs.
    c.service.final.set_pin(c.job["id"], True, expected_revision=0)
    with pytest.raises(fc.ContractError, match="pinned"):
        c.service.final.delete(c.job["id"], expected_revision=c.job["configRevision"])
    c.service.final.set_pin(c.job["id"], False, expected_revision=1)
    retired = c.client.request("DELETE", "/api/jobs/" + c.job["id"], json={"expectedRevision": c.job["configRevision"],
        "expectedStateRevision": c.job["stateRevision"], "requestKey": "retire"})
    assert retired.status_code == 200, retired.text
    # Original cleanup policy refuses deletion while the dashboard notice is
    # unresolved. This isolated fixture explicitly suppresses its own notice;
    # it does not pretend a real user or websocket received that notification.
    with pytest.raises(fc.ContractError, match="unacknowledged_outbox"):
        c.service.final.delete(c.job["id"], expected_revision=c.job["configRevision"])
    state = c.service.final.store.get(c.job["id"])["finalRuntime"]
    for row in state["outbox"].values():
        c.service.final.settle_notification(c.job["id"], row["legacyId"], outbox_id=row["outboxId"],
            receipt={"outcome": "suppressed", "originalKey": row["legacyId"],
                     "outboxId": row["outboxId"], "reason": "isolated test has no dashboard listener"})
    latest = c.service.final.store.get(c.job["id"])
    c.service.final.delete(c.job["id"], expected_revision=latest["configRevision"])
    archived = c.client.get(f"/api/job-runs/{c.rid}/logs").json()["items"]
    assert len(archived) == 1 and archived[0]["logRef"] == doc["logRef"]
    assert c.client.get(archived[0]["href"]).json()["logRef"] == doc["logRef"]
    assert all_bytes(c)[0] == raw
    asyncio.run(c.service.dispatch_once())
    assert (c.tmp / "effects").read_text().splitlines() == ["once"]
    c.checks.update(coldReopenExit=cold.returncode, bytes=len(raw), mergedStreams=True,
                    unicodeExact=True, historyArchiveReadable=True, effects=1)


@pytest.mark.parametrize("exit_code", [7, 0])
def test_failed_and_cancelled_real_output_survives_and_no_rerun(live, exit_code):
    c = live
    start(c, "import os,time\nopen('effects','a').write('once\\n')\n"
        "os.write(1,'before取消🙂'.encode()); os.write(2,b'err-tail')\nopen('ready','w').write('yes')\n"
        + ("time.sleep(30)\n" if exit_code == 0 else "raise SystemExit(7)\n"))
    wait(lambda: (c.tmp / "ready").exists(), bool)
    if exit_code == 0:
        before = c.client.get(c.url)
        assert before.status_code == 200 and "before取消" in before.json()["text"]
        original = c.store.get(c.eid)
        assert original["result"] is None
        assert cold_read(c)["text"] == "before取消🙂err-tail"
        assert c.store.get(c.eid)["result"] is None  # Cold read/recover never kill or replay.
        run = c.client.get("/api/job-runs/" + c.rid).json()
        response = c.client.post("/api/job-runs/" + c.rid + "/operations", json={"operation": "cancel",
            "expectedStateRevision": run["stateRevision"], "requestKey": "cancel"})
        assert response.status_code == 202, response.text
        terminal(c, "cancelled")
    else:
        terminal(c, "failed")
    raw, _ = all_bytes(c)
    assert raw == "before取消🙂".encode() + b"err-tail"
    for _ in range(2):
        asyncio.run(c.service.recover_external_receipts())
        assert all_bytes(c)[0] == raw
    assert (c.tmp / "effects").read_text().splitlines() == ["once"]
    c.checks.update(outputRetained=True, effects=1, cancelled=exit_code == 0)


def test_late_terminal_log_keeps_retired_parent_and_original_reference(live):
    c = live
    start(c, "import os; os.write(1,'迟到terminal🙂'.encode()); open('effects','a').write('once\\n')\n")
    doc = wait(lambda: c.store.get(c.eid), lambda d: d["result"] is not None)
    result = c.client.request("DELETE", "/api/jobs/" + c.job["id"], json={"expectedRevision": c.job["configRevision"],
        "expectedStateRevision": c.job["stateRevision"], "requestKey": "retire-before-terminal"})
    assert result.status_code == 200, result.text
    retired = c.client.get("/api/jobs/" + c.job["id"]).json()
    assert retired["state"] == "retired"
    terminal(c)
    assert c.client.get("/api/jobs/" + c.job["id"]).json() == retired
    assert c.client.get(c.url).json()["logRef"] == doc["logRef"]
    assert all_bytes(c)[0] == "迟到terminal🙂".encode()
    assert (c.tmp / "effects").read_text().splitlines() == ["once"]
    c.checks.update(retiredParentUnchanged=True, lateReceiptLogPreserved=True, effects=1)


def test_public_discovery_pending_original_href_and_cold_reopen(live):
    c = live
    start(c, "import os,time\nos.write(1,'发现🙂'.encode()); open('ready','w').write('yes'); time.sleep(30)\n")
    wait(lambda: (c.tmp / "ready").exists(), bool)
    listing = c.client.get(f"/api/job-runs/{c.rid}/logs")
    assert listing.status_code == 200, listing.text
    item, = listing.json()["items"]
    assert item["status"] == "pending" and item["logRef"] == c.store.get(c.eid)["logRef"]
    assert c.client.get(item["href"]).json()["text"] == "发现🙂"
    assert "text" not in item and "bytesBase64" not in item
    code = """import asyncio,json,sys
from packages.jobs.final_service import FinalService
from packages.jobs.final_api import CanonicalUserAuth
s=FinalService(sys.argv[1],CanonicalUserAuth(sys.argv[2]))
s.recover()
print(json.dumps(asyncio.run(s.process_log_references(sys.argv[3],actor={'type':'user','id':'local'}))))
asyncio.run(s.close())
"""
    cold = subprocess.run([sys.executable, "-c", code, str(c.root), str(c.tmp / "automation"), c.rid],
                          capture_output=True, text=True, timeout=15, **command_window_options())
    assert cold.returncode == 0, cold.stderr
    assert json.loads(cold.stdout)["items"] == listing.json()["items"]
    assert c.store.get(c.eid)["result"] is None  # Discovery never kills or resubmits.
    # Model only the canonical handoff window before the pending callback is
    # recorded. The real original execution already owns its persistent ref.
    before = c.service.final.store.get(c.job["id"])
    def pending_handoff(job):
        state = job["finalRuntime"]
        original, = state["effects"]
        state["effects"] = []
        state["effectAdmissions"] = {"handoff": {k: v for k, v in original.items() if k != "receipt"}}
    c.service.final.store.mutate(c.job["id"], pending_handoff)
    handoff = c.client.get(f"/api/job-runs/{c.rid}/logs").json()["items"]
    assert handoff == listing.json()["items"]
    assert c.client.get(handoff[0]["href"]).json()["text"] == "发现🙂"
    c.service.final.store.mutate(c.job["id"], lambda job: job.update(finalRuntime=before["finalRuntime"]))
    c.checks.update(publicDiscovery=True, pendingHrefReadable=True, discoveryColdReopenExit=cold.returncode)


def test_discovery_pagination_replay_scopes_and_legacy_unavailable(live):
    c = live
    start(c, "import os; os.write(1,b'original')\n")
    doc = terminal(c)
    # Retained historical attempts need no active lease. These two additional
    # history fixtures deliberately have no original logRef: never invent one.
    def add_history(job):
        state = job["finalRuntime"]
        for generation in (2, 3):
            old = {**state["attempts"][c.aid], "attemptId": f"historic_{generation}",
                   "generation": generation, "ownerEpoch": generation}
            state["attempts"][old["attemptId"]] = old
            state["effects"].append({**copy.deepcopy(state["effects"][0]), "attemptId": old["attemptId"], "receipt": {}})
    c.service.final.store.mutate(c.job["id"], add_history)
    url = f"/api/job-runs/{c.rid}/logs"
    first = c.client.get(url, params={"limit": 1}).json()
    assert first["items"][0]["logRef"] == doc["logRef"]
    cursor = first["nextCursor"]
    assert cursor
    second = c.client.get(url, params={"limit": 1, "cursor": cursor})
    assert second.status_code == 200
    assert c.client.get(url, params={"limit": 1, "cursor": cursor}).json() == second.json()
    assert second.json()["items"][0]["status"] == "unavailable"
    assert second.json()["items"][0]["logRef"] is None and second.json()["items"][0]["href"] is None
    grants = c.auth._default_permissions()
    grants["job"]["read"] = True
    c.auth.change_permissions(grants, 0)
    agent = {"type": "agent", "id": "other", "revision": 1}
    with pytest.raises(fc.ContractError, match="cursor"):
        asyncio.run(c.service.process_log_references(c.rid, actor=agent, cursor=cursor, limit=1))
    other = FinalService(c.tmp / "other-profile" / "canonical", c.auth)
    # Same original records, different profile: the cursor still cannot cross.
    with c.service.transaction(readonly=True) as db:
        job = c.service._get(db, "job", c.job["id"])
    with other.transaction() as db:
        other._save(db, "job", c.job["id"], job)
    try:
        with pytest.raises(fc.ContractError, match="cursor"):
            asyncio.run(other.process_log_references(c.rid, actor=USER, cursor=cursor, limit=1))
    finally:
        asyncio.run(other.close())
    grants["job"]["read"] = False
    c.auth.change_permissions(grants, 1)
    with pytest.raises(fc.ContractError, match="revoked"):
        asyncio.run(c.service.process_log_references(c.rid, actor=agent))
    assert c.client.get("/api/job-runs/missing/logs").status_code == 404
    assert c.client.get(url, params={"limit": 101}).status_code == 400
    assert c.client.get(url, params={"path": "outside"}).status_code == 400
    c.checks.update(discoveryPagination=True, crossPrincipalCursorRejected=True,
                    crossProfileCursorRejected=True, legacyUnavailableExplicit=True)


def doc_with_log(tmp_path):
    store = ExecutionStore(tmp_path)
    doc, _ = store.admit(job_id="job_original", attempt=attempt(), effect_key=key(), frozen_plan=process_plan(tmp_path))
    return store, doc


def test_log_partial_utf8_and_binary_bytes_are_lossless(tmp_path):
    store, doc = doc_with_log(tmp_path)
    raw = "你好🙂".encode()
    with open_log(store.root, doc, write=True) as log:
        log.write(raw[:5])  # A real incomplete multi-byte write, no newline.
        first = read_log(store.root, doc, limit=4)
        assert first["text"] == "你" and first["nextOffset"] == 3
        assert first["pendingBytes"] == 1 and not first["eof"]
        assert base64.b64decode(first["pendingBase64"]) == raw[3:4]
        log.write(raw[5:])
        second = read_log(store.root, doc, offset=3, limit=8)
        assert second["text"] == "好🙂" and second["nextOffset"] == len(raw) and second["eof"]
        log.write(b"\xff\x80")
    binary = read_log(store.root, doc, offset=len(raw))
    assert binary["text"] is None and binary["encoding"] == "binary"
    assert base64.b64decode(binary["bytesBase64"]) == b"\xff\x80"


def test_log_original_attempt_history_and_unknown_does_not_relaunch(tmp_path):
    store, doc = doc_with_log(tmp_path)
    with open_log(store.root, doc, write=True) as log:
        log.write(b"original failed bytes")
    claimed = store.claim(doc["effectId"], attempt(), {"pid": 7, "birth": "original"})
    from packages.actions.typed_executor import EffectReceipt
    store.finish(doc["effectId"], attempt(), claimed["claim"], EffectReceipt(key(), "rejected", {}, {"confirmed": True}).as_dict())
    newer = attempt(attemptId="attempt_new", ownerEpoch=2, generation=2)
    second, _ = store.admit(job_id="job_original", attempt=newer, effect_key=key(), frozen_plan=process_plan(tmp_path), retry=True)
    assert second["logRef"] != doc["logRef"]
    assert store.read_execution_log(doc["effectId"], attempt())["text"] == "original failed bytes"
    with open_log(store.root, second, write=True) as log:
        log.write(b"partial unknown bytes")
    store.recover(lambda _: False)
    launches = []
    host = ExecutionHost(ExecutionStore(tmp_path), launch=lambda *a: launches.append(a))
    queried = host.submit_execution({"jobId": "job_original", "attempt": newer, "effectKey": key(), "frozenPlan": process_plan(tmp_path)})
    assert queried["result"]["outcome"] == "unknown" and launches == []
    assert host.read_execution_log(doc["effectId"], newer)["text"] == "partial unknown bytes"


def test_missing_log_and_stale_read_fence_are_errors_not_empty_success(tmp_path):
    store, doc = doc_with_log(tmp_path)
    with pytest.raises(fc.ContractError, match="directory unavailable"):
        store.read_execution_log(doc["effectId"], attempt())
    with pytest.raises(ValueError, match="identity not found"):
        store.read_execution_log(doc["effectId"], attempt(ownerEpoch=2))
    assert store.get(doc["effectId"])["phase"] == "admitted"


def test_remote_log_page_cannot_expand_window_or_replace_original_reference(tmp_path):
    from packages.jobs.process_logs import validate_page
    store, doc = doc_with_log(tmp_path)
    with open_log(store.root, doc, write=True) as log:
        log.write(b"original bytes")
    page = read_log(store.root, doc, limit=4)
    for modified in ({**page, "nextOffset": 5}, {**page, "text": "fake"},
                     {**page, "bytesBase64": base64.b64encode(b"too long").decode()},
                     {**page, "logRef": {**page["logRef"], "path": "outside"}}):
        with pytest.raises(ValueError):
            validate_page(modified, doc, offset=0, limit=4)


def test_log_path_alias_and_forged_reference_refused(tmp_path):
    store, doc = doc_with_log(tmp_path / "root")
    outside = tmp_path / "outside"
    outside.write_bytes(b"secret")
    with open_log(store.root, doc, write=True) as log:
        log.write(b"owned")
    forged = copy.deepcopy(doc)
    forged["logRef"]["id"] = "../../outside"
    with pytest.raises(fc.ContractError):
        read_log(store.root, forged)
    path = store.root / "logs" / (doc["logRef"]["id"] + ".log")
    path.unlink()
    os.link(outside, path)
    with pytest.raises(fc.ContractError):
        read_log(store.root, doc)
    assert outside.read_bytes() == b"secret"


def test_log_directory_symlink_or_reparse_refused_before_effect(tmp_path):
    store, doc = doc_with_log(tmp_path / "root")
    outside = tmp_path / "outside"
    outside.mkdir()
    link = store.root / "logs"
    if os.name == "nt":
        from packages.jobs.window import command_window_options
        result = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(link), str(outside)],
                                capture_output=True, **command_window_options())
        assert result.returncode == 0, result.stderr
    else:
        link.symlink_to(outside, target_is_directory=True)
    try:
        claimed = store.claim(doc["effectId"], attempt(), {"pid": os.getpid(), "birth": "test-only"})
        from packages.jobs.independent_execution_runner import execute_claimed
        final = asyncio.run(execute_claimed(store, claimed))
        assert final["result"]["outcome"] == "unknown"
        assert final["result"]["receipt"]["code"] == "supervisor_error"
        assert final["result"]["receipt"]["logRef"] == doc["logRef"]
        assert not list(outside.iterdir())
        with pytest.raises(fc.ContractError):
            store.read_execution_log(doc["effectId"], attempt())
    finally:
        if os.name == "nt":
            link.rmdir()  # Remove only our junction entry, never its target.
        else:
            link.unlink()


@pytest.mark.parametrize("query", [{"offset": "-1"}, {"offset": "1.0"}, {"offset": "true"},
    {"limit": "65537"}, {"limit": "0"}, {"path": "../outside"}, {"delete": "true"}])
def test_public_log_rejects_arbitrary_path_and_unbounded_cursor(live, query):
    result = live.client.get("/api/job-runs/unknown/attempts/unknown/log", params=query)
    assert result.status_code == 400, result.text


def test_public_log_authentication_and_revoked_agent_read(live):
    c = live
    start(c, "import os; os.write(1,b'private-output\\n')\n")
    terminal(c)
    assert c.client.get(c.url, headers={"Authorization": "Bearer invalid"}).status_code == 401
    assert c.client.get(f"/api/job-runs/{c.rid}/logs", headers={"Authorization": "Bearer invalid"}).status_code == 401
    assert c.client.get(c.url.replace(c.aid, "other-attempt")).status_code == 404
    grants = c.auth._default_permissions()
    grants["job"]["read"] = True
    c.auth.change_permissions(grants, 0)
    agent = {"type": "agent", "id": "agent", "revision": 1}
    assert asyncio.run(c.service.read_process_log(c.rid, c.aid, actor=agent))["text"] == "private-output\n"
    grants["job"]["read"] = False
    c.auth.change_permissions(grants, 1)
    with pytest.raises(fc.ContractError, match="revoked"):
        asyncio.run(c.service.read_process_log(c.rid, c.aid, actor=agent))
    from packages.automation import authorization
    from fastapi.testclient import TestClient
    async def trusted(scope, receive, send):
        scope[authorization._SCOPE_KEY] = authorization._MARKER
        await c.app(scope, receive, send)
    with TestClient(trusted) as delegated:
        assert delegated.get(c.url).status_code == 403
        assert delegated.get(f"/api/job-runs/{c.rid}/logs").status_code == 403
    c.checks.update(invalidBearerRejected=True, revokedReadRejected=True)
