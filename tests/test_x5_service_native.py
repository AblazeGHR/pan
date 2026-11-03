"""Opt-in owned full main/CLI/HTTP trial, including target POSIX candidates.

The isolated archived copy explicitly injects ONLY the lifecycle capability
for target execution. This does not change the shipped capability declaration.
"""
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tarfile
import time

import httpx
import psutil
import pytest

from packages.core import launcher, lifecycle_action


pytestmark = pytest.mark.skipif(os.environ.get("X5_RUN_NATIVE_SERVICE") != "1",
    reason="explicit opt-in: owned native service trial, no real accounts")


def test_owned_cli_http_service_and_lock_competition(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    source = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    root = (tmp_path / "owned-checkout").resolve()
    root.mkdir()
    archive = tmp_path / "source.tar"
    with archive.open("wb") as stream:
        subprocess.run(["git", "archive", source, "main.py", "packages", "scripts", "manifest.json"],
                       cwd=repo, stdout=stream, check=True)
    with tarfile.open(archive) as stream:
        stream.extractall(root, filter="data")
    # No production flag is added. Only this disposable, marker-fenced copy
    # performs a candidate capability trial across its independently spawned hosts.
    (root / ".x5-owned-trial").touch()
    (root / "sitecustomize.py").write_text(
        "import os\nfrom pathlib import Path\n"
        "r=Path(__file__).resolve().parent\n"
        "if str(r)==os.environ.get('X5_OWNED_TRIAL_ROOT') and (r/'.x5-owned-trial').is_file():\n"
        " import packages.core.platform as p\n"
        " old=p.CAPABILITIES['service.lifecycle']\n"
        " p.CAPABILITIES['service.lifecycle']=p.Capability(old.name, old.supported_on|{p.platform_name()}, 'owned X5 candidate trial')\n",
        encoding="utf-8")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    assert port not in {8767, 8768}
    config = {"port": port, "python": sys.executable, "startup": {"console_hidden": True},
        "qq": {"enabled": False}, "wechat": {"enabled": False}, "remote": {"enabled": False},
        "mcp_remote": {"enabled": False}, "memory": {"enabled": False},
        "session_lifecycle": {"exitStrategy": "ask", "startupPreference": "ask"}}
    (root / "config.json").write_text(json.dumps(config), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("PAN_") and k.upper() != "PYTHONPATH"}
    env.update(PYTHONPATH=str(root), X5_OWNED_TRIAL_ROOT=str(root), PYTHONIOENCODING="utf-8")
    observed = {"sourceSha": source, "trialCapabilityInjection": True, "protectedPortAvoided": True,
                "platform": sys.platform, "declaredCapabilityUnchanged": True}
    holder = None
    release = root / "release-lock"

    def cli(*args, success=True):
        result = subprocess.run([sys.executable, "-m", "packages.core.cli", "--root", str(root), *args],
            cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace", timeout=120)
        with (tmp_path / "cli.log").open("a", encoding="utf-8") as output:
            output.write("COMMAND " + " ".join(args) + "\n" + result.stdout + "\n")
        assert (result.returncode == 0) is success, result.stdout[-5000:]
        return result

    def http(method, path, **kw):
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=3, trust_env=False) as client:
            return client.request(method, path, **kw)

    def docs():
        path = root / "data/lifecycle-executions/executions.sqlite3"
        if not path.exists():
            return []
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
            return [json.loads(row[0]) for row in db.execute("SELECT document FROM executions")]

    def until(read, condition, timeout=110):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = read()
            if condition(value):
                return value
            time.sleep(.05)
        raise AssertionError("owned X5 evidence deadline")

    try:
        # This CLI subprocess is the startup caller/Worker surrogate. It must
        # exit before independently surviving main is queried below.
        cli("start", "--timeout", "25")
        old = http("GET", "/api/main/identity").json()
        assert Path(old["root"]).resolve() == root
        assert json.loads(cli("status").stdout)["state"] == "running"
        observed["startupCallerExitedServiceAlive"] = True
        stale = http("POST", lifecycle_action.ACTION_ROUTE, json={"operation": "restart",
            "pid": old["pid"], "createdAt": old["createdAt"] + 2,
            "options": {"markRunningSessionsOffline": True}})
        assert stale.status_code == 409 and not docs()
        nochoice = http("POST", lifecycle_action.ACTION_ROUTE, json={"operation": "restart"})
        assert nochoice.status_code in {400, 409} and not docs()
        response = http("POST", lifecycle_action.ACTION_ROUTE, json={"operation": "restart",
            "options": {"markRunningSessionsOffline": True}})
        response.raise_for_status()
        admitted = response.json()
        assert admitted["accepted"]
        doc = until(lambda: next((d for d in docs() if d["effectId"] == admitted["effectId"]), None),
                    lambda d: d is not None and d.get("result") is not None)
        assert doc["result"]["outcome"] == "succeeded", doc["result"]
        proof = doc["result"]["receipt"]
        assert proof["offlineProof"]["ok"] and proof["readyProof"]["ok"]
        if os.name == "posix":
            assert proof["offlineProof"]["serviceTreeProof"]["confirmed"] is True
        assert proof["readyProof"]["newPid"] != old["pid"]
        # Reconstruct the ORIGINAL identity from its original requestId only;
        # the HTTP endpoint derives it, and accepts no caller requestKey field.
        _, identity, _ = lifecycle_action.protected_plan(root=root, port=port, old_pid=old["pid"],
            old_pid_created_at=old["createdAt"], operation="restart", request_id=admitted["requestId"])
        from packages.jobs.independent_executor import effect_id
        assert effect_id(identity["effectKey"]) == doc["effectId"]
        original = lifecycle_action.wait_for_receipt(root=root, effect_id=doc["effectId"], operation="restart", timeout=1)
        assert original == doc["result"] and len(docs()) == 1
        observed["originalRequestLookupNoReplay"] = True
        cli("restart", "--running", "preserve", "--timeout", "110")
        cli("exit", "--running", "offline", "--timeout", "110")
        assert json.loads(cli("status").stdout)["state"] == "stopped"
        observed["cliRestartExitStatus"] = True
        cli("start", "--timeout", "25")
        # A separate real OS process holds the SAME start lock used by CLI
        # and canonical supervisor. A rejected CLI start creates no new main.
        holder = subprocess.Popen([sys.executable, "-c",
            "import pathlib,time; from packages.core.cli import start_lock; "
            f"r=pathlib.Path({str(root)!r});\nwith start_lock(r):\n (r/'lock-ready').touch()\n while not (r/'release-lock').exists(): time.sleep(.02)"],
            cwd=root, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        until(lambda: (root / "lock-ready").exists(), bool, timeout=10)
        failed = cli("start", success=False)
        assert "Another pan start" in failed.stdout
        before = {d["effectId"] for d in docs()}
        response = http("POST", lifecycle_action.ACTION_ROUTE, json={"operation": "restart",
            "options": {"markRunningSessionsOffline": True}})
        response.raise_for_status()
        failed_id = response.json()["effectId"]
        failed_doc = until(lambda: next((d for d in docs() if d["effectId"] == failed_id), None),
                           lambda d: d is not None and d.get("result") is not None)
        assert failed_doc["result"]["outcome"] == "unknown", failed_doc["result"]
        assert "Another pan start" in json.dumps(failed_doc["result"])
        release.touch(); assert holder.wait(timeout=10) == 0
        assert failed_id not in before and launcher.listener_owner(port) is None
        # Querying again, including after lock release, cannot launch a retry.
        assert lifecycle_action.wait_for_receipt(root=root, effect_id=failed_id, operation="restart", timeout=1) == failed_doc["result"]
        assert launcher.listener_owner(port) is None
        observed["realCliCanonicalLockConflictUnknownNoReplay"] = True
    finally:
        release.touch()
        if holder is not None:
            holder.wait(timeout=10)
        # Do not let a still-running original supervisor start another main
        # after listener cleanup. Bound the wait on its original birth identity.
        from packages.core.platform.execution_host import supervisor_exit_evidence
        for doc in docs():
            if doc.get("supervisorIdentity"):
                until(lambda: supervisor_exit_evidence(doc["supervisorIdentity"]), lambda p: p["confirmed"], timeout=15)
        state = launcher.load_state(root)
        record = state.get("main")
        if record and launcher.process_alive(record["pid"], record["createdAt"]):
            result = launcher._terminate_record(record, root, log_path=None)
            assert result["stopped"], result
        observed["portReleased"] = launcher.listener_owner(port) is None
        assert observed["portReleased"]
        target = Path(os.environ.get("X5_EVIDENCE_ROOT", str(tmp_path)))
        target.mkdir(parents=True, exist_ok=True)
        (target / "native-service.json").write_text(json.dumps(observed, indent=2), encoding="utf-8")
        # Export commands only. Full CLI receipts contain synthetic paths and
        # argv which add no acceptance evidence beyond the asserted JSON facts.
        (target / "cli-commands.txt").write_text("\n".join(line for line in
            (tmp_path / "cli.log").read_text(encoding="utf-8").splitlines()
            if line.startswith("COMMAND ")) + "\n", encoding="utf-8")
