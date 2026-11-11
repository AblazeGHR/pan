"""P2 real isolated effects and original-identity crash/owner controls."""
import asyncio
import json
from contextlib import contextmanager
from dataclasses import replace
import shutil
import subprocess
import sys

import pytest

from packages.actions import command_execution as commands
from packages.jobs.final_service import FinalService, object_ref
from packages.jobs.final_runtime.ledger import aggregate
from packages.jobs import final_contracts as fc
from tests.test_job_final_http_service import h, USER, create, control, launch


def install(h, operation="process.command", config=None):
    value = commands.product_definition(operation)
    value.update(system=False)
    value["execution"]["config"] = config or {}
    item = h.service.publish_action("test_command", value)
    h.action = object_ref("action", item)
    if h.service.owner.token is None:
        h.service.owner.claim(0)
    return h.action


def request(h, arguments, key="invoke"):
    return {"actionRef": h.action, "arguments": arguments, "requestKey": key}


def call(h, arguments, key="invoke"):
    return h.client.post("/api/jobs/actions/" + h.action["id"] + "/invocations", json=request(h, arguments, key))


def py(tmp_path, code, **kwargs):
    return {"argv": [sys.executable, "-c", code], "cwd": str(tmp_path), **kwargs}


def test_direct_git_partial_receipts_idempotency_conflict_no_hidden_job(h, tmp_path):
    install(h)
    git = shutil.which("git")
    assert git
    subprocess.run([git, "init", str(tmp_path / "git")], check=True, capture_output=True)
    root = tmp_path / "git"
    trace = tmp_path / 'push-trace.jsonl'
    (root / "file.txt").write_text("first")
    args = {"cwd": str(root), "commands": [
        {"argv": [git, "add", "file.txt"]},
        {"argv": [git, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-m", "fixture"]},
        # Use Git's explicit local-transport rejection as the known failure.
        # A missing path alone can spawn a short-lived receive-pack helper;
        # uncertain helper cleanup must remain uncertain in the product.
        {"argv": [git, "-c", "protocol.file.allow=never", "push", "missing-remote", "HEAD"],
         "env": {"GIT_TRACE2_EVENT": str(trace)}},
        {"argv": [sys.executable, "-c", "open('must-not-run','w').write('x')"]}]}
    response = call(h, args)
    assert response.status_code == 200, response.text
    doc = response.json()
    rows = doc["result"]["commands"]
    assert [r["status"] for r in rows] == ["completed", "completed", "failed"], rows
    assert [r["exitCode"] for r in rows[:2]] == [0, 0]
    assert rows[1]["stdout"] and rows[2]["stderr"] and rows[2]["exitCode"] != 0
    assert rows[2]["exitCode"] == 128 and "not allowed" in rows[2]["stderr"], rows[2]
    assert rows[2]["cleanupProof"]["confirmed"] is True, rows[2]
    assert rows[2]["cleanupProof"]["ownedTree"]["remaining"] == [], rows[2]
    events = [json.loads(line) for line in trace.read_text(encoding='utf-8').splitlines()]
    assert not any(event['event'] == 'child_start' for event in events), events
    assert any(event['event'] == 'exit' and event['code'] == 128 for event in events), events
    assert not (root / "must-not-run").exists()
    assert call(h, args).json()["operationId"] == doc["operationId"]
    assert call(h, {**args, "timeout": 17}).status_code == 409
    assert len(subprocess.check_output([git, "-C", str(root), "rev-list", "HEAD"]).splitlines()) == 1
    with h.service.transaction(readonly=True) as db:
        assert db.execute("SELECT count(*) FROM final_documents WHERE kind='job'").fetchone()[0] == 0
    assert not any(k in doc for k in ("jobId", "runId", "stepId"))
    fetched = h.client.get("/api/operations", params={"operation": "invoke", "requestKey": "invoke"})
    assert fetched.json() == doc


@pytest.mark.parametrize("operation", ["process.command", "process.shell"])
def test_shell_argv_environment_window_output(h, tmp_path, operation):
    install(h, operation)
    args = ({"command": 'echo "a & b"', "cwd": str(tmp_path)} if operation == "process.shell" else
            py(tmp_path, "import os,sys;print(sys.argv[1]);print(os.environ['P2_VALUE']);print('err',file=sys.stderr)",
               env={"P2_VALUE": "owned"}, hideWindow=False))
    if operation == "process.command":
        args["argv"].append("a & b > c")
    result = call(h, args)
    assert result.status_code == 200, result.text
    row = result.json()["result"]["commands"][0]
    assert row["status"] == "completed", row
    assert "a & b" in row["stdout"]
    if operation == "process.command":
        assert "owned" in row["stdout"] and row["stderr"].strip() == "err"


def test_external_current_bytes_and_fixed_revision(h, tmp_path):
    script = tmp_path / "script.py"
    script.write_text("print('first')", encoding="utf-8")
    install(h, "process.shell", {"externalScript": {"path": str(script), "contentDigest": "a" * 64},
                                "interpreter": sys.executable, "cwd": str(tmp_path)})
    first = call(h, {}).json()
    script.write_text("print('second')", encoding="utf-8")
    second = call(h, {}, "second").json()
    assert first["result"]["actionRef"] == second["result"]["actionRef"]
    rows = [d["result"]["commands"][0] for d in (first, second)]
    assert [r["stdout"].strip() for r in rows] == ["first", "second"]
    assert rows[0]["externalScript"]["observedDigest"] != rows[1]["externalScript"]["observedDigest"]
    assert rows[1]["externalScript"]["mutable"] is True


def test_bounded_utf8_output(h, tmp_path):
    install(h)
    result = call(h, py(tmp_path, "import sys;sys.stdout.buffer.write(('你'*300000).encode());sys.stderr.write('still drained')"))
    row = result.json()["result"]["commands"][0]
    assert row["status"] == "completed"
    assert row["stdoutTruncated"] and len(row["stdout"].encode()) <= commands.OUTPUT_LIMIT
    assert "\ufffd" not in row["stdout"] and row["stderr"] == "still drained"


def test_serial_commands_keep_each_legacy_output_allowance(h, tmp_path):
    install(h)
    args = {"cwd": str(tmp_path), "commands": [
        py(tmp_path, "import sys;sys.stdout.write('x'*400000)") for _ in range(2)]}
    response = call(h, args)
    assert response.status_code == 200, response.text
    rows = response.json()["result"]["commands"]
    assert len(rows) == 2
    assert all(len(row["stdout"]) == 400000 and not row["stdoutTruncated"] for row in rows)


def test_timeout_owned_descendant_cleanup(h, tmp_path):
    install(h)
    code = "import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);print(p.pid,flush=True);time.sleep(60)"
    response = call(h, py(tmp_path, code, timeout=0.4))
    row = response.json()["result"]["commands"][0]
    assert row["code"] == "timeout" and row["status"] == "failed", row
    assert row["cleanupProof"]["confirmed"] is True
    assert row["cleanupProof"]["ownedTree"]["remaining"] == []
    assert row["stdout"].strip().isdigit()


def test_cancellation_cleans_tree_and_keeps_original_receipt(h, tmp_path):
    install(h)
    async def scenario():
        task = asyncio.create_task(h.service.invoke_action(h.action["id"],
            request(h, py(tmp_path, "import time;print('started',flush=True);time.sleep(60)")), actor=USER))
        await asyncio.sleep(0.3)
        task.cancel()
        return await task
    result = asyncio.run(scenario())
    row = result["result"]["commands"][0]
    assert row["status"] == "cancelled" and row["cleanupProof"]["confirmed"] is True
    assert call(h, py(tmp_path, "import time;print('started',flush=True);time.sleep(60)")).json()["replayed"] is True


@pytest.mark.parametrize("window", ["prepared", "calling", "checkpoint", "receipt"])
def test_crash_windows_cold_query_never_replay(h, tmp_path, monkeypatch, window):
    install(h)
    marker = tmp_path / "effects"
    args = py(tmp_path, "from pathlib import Path;p=Path('effects');p.write_text(p.read_text()+'x' if p.exists() else 'x')")
    real_execute = h.service.executor.execute_commands
    real_transaction = h.service.transaction
    if window == "prepared":
        counter = 0
        @contextmanager
        def crash_transaction(*args, **kwargs):
            nonlocal counter
            with real_transaction(*args, **kwargs) as db:
                yield db
            if not kwargs.get("readonly"):
                counter += 1
            if counter == 1 and not kwargs.get("readonly"):
                raise RuntimeError("injected after prepare commit, before calling")
        monkeypatch.setattr(h.service, "transaction", crash_transaction)
    elif window == "calling":
        async def crash(*args, **kwargs):
            raise RuntimeError("injected after durable intent")
        monkeypatch.setattr(h.service.executor, "execute_commands", crash)
    else:
        async def crash(*args, **kwargs):
            if window == "checkpoint":
                def lost_receipt(rows):
                    raise RuntimeError("injected after real effect before checkpoint")
                kwargs["context"].checkpoint = lost_receipt
            await real_execute(*args, **kwargs)
            raise RuntimeError("injected after effect and checkpoint before terminal receipt")
        monkeypatch.setattr(h.service.executor, "execute_commands", crash)
    with pytest.raises(RuntimeError):
        asyncio.run(h.service.invoke_action(h.action["id"], request(h, args), actor=USER))
    monkeypatch.setattr(h.service.executor, "execute_commands", real_execute)
    monkeypatch.setattr(h.service, "transaction", real_transaction)
    cold = FinalService(h.root, h.automation.auth)
    original = cold.operation_for_key("invoke", "invoke", actor=USER)
    repeated = asyncio.run(cold.invoke_action(h.action["id"], request(h, args), actor=USER))
    assert original["operationId"] == repeated["operationId"] and repeated["replayed"]
    assert original["result"]["status"] == "uncertain"
    assert marker.read_text() == "x" if window in {"checkpoint", "receipt"} else not marker.exists()


def test_unconfirmed_cleanup_stays_unknown_and_never_replays(h, tmp_path, monkeypatch):
    install(h)
    owner = commands.default_process_owner()
    kill_tree = owner.kill_tree
    def unconfirmed(*args, **kwargs):
        return replace(kill_tree(*args, **kwargs), confirmed=False)
    monkeypatch.setattr(owner, "kill_tree", unconfirmed)
    monkeypatch.setattr(commands, "default_process_owner", lambda: owner)
    args = {"cwd": str(tmp_path), "commands": [py(tmp_path, "open('once','a').write('x')"),
                                              py(tmp_path, "open('blocked','w').write('x')")]}
    first = call(h, args)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "unknown"
    assert first.json()["result"]["commands"][0]["cleanupProof"]["confirmed"] is False
    repeated = call(h, args).json()
    assert repeated["replayed"] and repeated["operationId"] == first.json()["operationId"]
    assert (tmp_path / "once").read_text() == "x" and not (tmp_path / "blocked").exists()


def test_named_successors_default_disabled_and_original_process_direct(h, tmp_path):
    catalog = h.service.actions(actor=USER)["items"]
    permissions = h.automation.auth.permissions_status()["permissions"]
    permissions["action"].update(lifecycle=True, manageSystemActions=True)
    granted = h.automation.auth.change_permissions(permissions, 0)
    manager = {"type": "agent", "id": "manager", "revision": granted["revision"]}
    for operation in commands.OPERATIONS:
        item = next(a for a in catalog if a["id"] == operation)
        assert item["enabled"] is False
        h.action = object_ref("action", item)
        if h.service.owner.token is None:
            h.service.owner.claim(0)
        assert call(h, py(tmp_path, "print('forbidden')"), operation).status_code == 403
        enabled = h.service.control("action", item["id"], {"operation": "enable", "expectedRevision": item["revision"],
            "expectedStateRevision": item["stateRevision"], "parameters": {}, "requestKey": "enable-" + operation}, actor=manager)
        assert enabled["result"]["object"]["enabled"] is True
        arguments = py(tmp_path, "print('named')") if operation == "process.command" else {"command": "echo named", "cwd": str(tmp_path)}
        executed = call(h, arguments, "enabled-" + operation)
        assert executed.status_code == 200, executed.text
        assert executed.json()["result"]["commands"][0]["stdout"].strip() == "named"
    original = next(a for a in catalog if a["id"] == "product_process")
    h.action = object_ref("action", original)
    response = call(h, py(tmp_path, "print('original')"))
    assert response.status_code == 200, response.text
    assert response.json()["result"]["commands"][0]["stdout"].strip() == "original"


def test_concurrent_same_key_single_effect_and_principal_scope(h, tmp_path):
    install(h)
    args = py(tmp_path, "import time;from pathlib import Path;Path('effect').write_text('x');time.sleep(.2)")
    async def scenario():
        one, two = await asyncio.gather(*[h.service.invoke_action(h.action["id"], request(h, args), actor=USER) for _ in range(2)])
        return one, two
    one, two = asyncio.run(scenario())
    assert one["operationId"] == two["operationId"]
    assert two["replayed"] and two["result"]["status"] == "uncertain"
    with pytest.raises(KeyError):
        h.service.operation(one["operationId"], actor={"type": "user", "id": "other"})


def test_owner_loss_between_commands_blocks_successor(h, tmp_path, monkeypatch):
    install(h)
    real_spawn = commands.default_process_owner
    owner = real_spawn()
    original_wait = owner.wait
    def wait(*args, **kwargs):
        result = original_wait(*args, **kwargs)
        if result.exited:
            h.service.owner.release()
        return result
    monkeypatch.setattr(owner, "wait", wait)
    monkeypatch.setattr(commands, "default_process_owner", lambda: owner)
    args = {"cwd": str(tmp_path), "commands": [py(tmp_path, "print('first')"), py(tmp_path, "open('forbidden','w').write('x')")]}
    with pytest.raises(fc.ContractError):
        asyncio.run(h.service.invoke_action(h.action["id"], request(h, args), actor=USER))
    assert not (tmp_path / "forbidden").exists()


@pytest.mark.parametrize("operation", ["process.command", "process.shell"])
def test_job_adapter_uses_existing_run_step_attempt_effect(h, tmp_path, operation):
    install(h, operation)
    raw = py(tmp_path, "print('job stdout')") if operation == "process.command" else {"command": "echo job stdout", "cwd": str(tmp_path)}
    args = {k: {"from": "literal", "value": v} for k, v in raw.items()}
    value = {"name": "shared command", "steps": [
        {"stepId": "first", "actionRef": h.action, "arguments": args, "completion": {"contract": "process-exit"}},
        {"stepId": "second", "actionRef": h.action, "arguments": args, "completion": {"contract": "process-exit"}}],
        "retention": {"policyRef": "test"}}
    item = create(h, value=value)
    enabled = control(h, "jobs", item, "enable")
    assert enabled.status_code == 200, enabled.text
    admitted = launch(h, enabled.json()["result"]["object"])
    asyncio.run(h.service.dispatch_once())
    state = aggregate(h.service.final.store.get(item["id"]), h.root)
    assert state["runs"][admitted["runId"]]["state"] == "completed", state
    assert len(state["runs"]) == 1 and len(state["attempts"]) == 2 and len(state["effects"]) == 2
    assert len(state["plans"][admitted["runId"]]["commandReceipts"]) == 2


def test_legacy_command_descriptor_whole_argv_binding(h, tmp_path):
    install(h, config={"executable": sys.executable,
        "args": ["-c", "import sys;print(sys.argv[1])", "{value}"], "cwd": str(tmp_path), "timeout_sec": 4})
    result = call(h, {"value": "literal & > shell chars"})
    assert result.json()["result"]["commands"][0]["stdout"].strip() == "literal & > shell chars"


def test_canonical_defaults_preserve_legacy_argv_binding(h, tmp_path):
    value = commands.product_definition("process.command")
    value.update(system=False, inputSchema={"type": "object", "properties": {
        "value": {"type": "string", "default": "default & literal"}}, "additionalProperties": False})
    value["execution"]["config"] = {"executable": sys.executable,
        "args": ["-c", "import sys;print(sys.argv[1])", "{value}"], "cwd": str(tmp_path)}
    h.action = object_ref("action", h.service.publish_action("defaults", value))
    h.service.owner.claim(0)
    response = call(h, {})
    assert response.json()["result"]["commands"][0]["stdout"].strip() == "default & literal"


def test_job_partial_effect_blocks_following_step_and_retry(h, tmp_path):
    install(h)
    args = {"cwd": str(tmp_path), "commands": [py(tmp_path, "open('once','a').write('x')"),
        py(tmp_path, "import sys;sys.exit(9)")]}
    steps = [{"stepId": sid, "actionRef": h.action,
        "arguments": {k: {"from": "literal", "value": v} for k, v in raw.items()},
        "completion": {"contract": "process-exit"}} for sid, raw in [
            ("first", args), ("blocked", py(tmp_path, "open('blocked','w').write('x')"))]]
    item = create(h, value={"name": "partial", "steps": steps, "retention": {"policyRef": "test"}})
    enabled = control(h, "jobs", item, "enable").json()["result"]["object"]
    admitted = launch(h, enabled)
    asyncio.run(h.service.dispatch_once())
    run = h.service.get_run(admitted["runId"], actor=USER)
    assert run["state"] == "uncertain"
    assert (tmp_path / "once").read_text() == "x" and not (tmp_path / "blocked").exists()
    from tests.test_job_final_http_service import run_control
    assert run_control(h, admitted["runId"], "retry", "forbidden-retry").status_code == 409
    asyncio.run(h.service.dispatch_once())
    assert (tmp_path / "once").read_text() == "x"


def test_job_active_cancel_original_owner_proof(h, tmp_path):
    install(h)
    args = py(tmp_path, "from pathlib import Path;import time;Path('started').write_text('yes');time.sleep(60)")
    item = create(h, value={"name": "cancel", "steps": [{"stepId": "active", "actionRef": h.action,
        "arguments": {k: {"from": "literal", "value": v} for k, v in args.items()},
        "completion": {"contract": "process-exit"}}], "retention": {"policyRef": "test"}})
    enabled = control(h, "jobs", item, "enable").json()["result"]["object"]
    rid = launch(h, enabled)["runId"]
    async def scenario():
        task = asyncio.create_task(h.service.dispatch_once())
        for _ in range(150):
            if (tmp_path / "started").exists():
                break
            await asyncio.sleep(.02)
        assert (tmp_path / "started").exists()
        run = h.service.get_run(rid, actor=USER)
        response = await h.service.control_run_postcommit(rid, {"operation": "cancel", "requestKey": "cancel",
            "expectedStateRevision": run["stateRevision"]}, actor=USER)
        await task
        return response
    response = asyncio.run(scenario())
    assert h.service.get_run(rid, actor=USER)["state"] == "cancelled"
    assert h.service.operation(response["operationId"], actor=USER)["status"] == "completed"
    state = aggregate(h.service.final.store.get(item["id"]), h.root)
    assert state["effects"][0]["receipt"]["cleanupProof"]["confirmed"] is True


def test_owner_invalid_before_spawn_no_effect_no_replay(h, tmp_path, monkeypatch):
    install(h)
    original_owner = commands.default_process_owner
    def owner_then_release():
        owner = original_owner()
        h.service.owner.release()
        return owner
    monkeypatch.setattr(commands, "default_process_owner", owner_then_release)
    args = py(tmp_path, "open('forbidden','w').write('x')")
    with pytest.raises(fc.ContractError):
        asyncio.run(h.service.invoke_action(h.action["id"], request(h, args), actor=USER))
    assert not (tmp_path / "forbidden").exists()
    original = h.service.operation_for_key("invoke", "invoke", actor=USER)
    assert original["result"]["status"] == "uncertain"


def test_unsupported_capability_before_intent(h, tmp_path):
    value = commands.product_definition("process.command")
    value.update(system=False, requiredCapabilities=["unknown.capability"])
    h.action = object_ref("action", h.service.publish_action("missing_capability", value))
    result = call(h, py(tmp_path, "open('forbidden','w').write('x')"))
    assert result.status_code == 422 and not (tmp_path / "forbidden").exists()
    with pytest.raises(KeyError):
        h.service.operation_for_key("invoke", "invoke", actor=USER)


def test_old_authorization_new_invoke_denied_and_explicit_grant(h, tmp_path):
    install(h)
    auth = h.automation.auth
    permissions = auth.permissions_status()["permissions"]
    permissions["action"].pop("invoke")
    old = auth._validate_permissions(permissions)
    assert old["action"]["invoke"] is False
    args = py(tmp_path, "print('allowed')")
    agent = {"type": "agent", "id": "test", "revision": 0}
    with pytest.raises(fc.ContractError, match="denied"):
        asyncio.run(h.service.invoke_action(h.action["id"], request(h, args), actor=agent))
    old["action"].update(invoke=True, read=True)
    granted = auth.change_permissions(old, 0)
    agent["revision"] = granted["revision"]
    result = asyncio.run(h.service.invoke_action(h.action["id"], request(h, args), actor=agent))
    assert result["result"]["status"] == "completed"
