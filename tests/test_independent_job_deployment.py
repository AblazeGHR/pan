"""External Task Scheduler host plus real isolated Pan shutdown/restart."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import uuid
from urllib.request import Request, urlopen

import pytest

pytestmark = [
    pytest.mark.skipif(os.name != "nt", reason="Windows external-host acceptance"),
    pytest.mark.skipif(os.environ.get("PAN_TEST_INDEPENDENT_HOST_ACCEPTANCE") != "1",
                      reason="explicit opt-in required to register an owned temporary Windows task"),
]
REPO = Path(__file__).resolve().parents[1]


def wait_for(predicate, timeout=20):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = predicate()
            if last:
                return last
        except (OSError, RuntimeError, ValueError):
            pass
        time.sleep(.1)
    raise AssertionError(f"acceptance condition did not converge ({type(last).__name__})")


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def http(endpoint, path, body=None):
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = Request(endpoint + path, data=data,
                      headers={"Content-Type": "application/json"} if data is not None else {})
    with urlopen(request, timeout=3) as response:
        return json.load(response)


def ps(pwsh, script, *args):
    assert len(args) == 1
    env = dict(os.environ, PAN_TEST_TASK_NAME=args[0])
    script = script.replace("param($n)", "$n=$env:PAN_TEST_TASK_NAME;")
    script = "$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new(); " + script
    result = subprocess.run([pwsh, "-NoProfile", "-NonInteractive", "-Command", script], env=env,
                            capture_output=True, text=True, encoding="utf-8", timeout=25)
    assert result.returncode == 0, "owned Task Scheduler operation failed: " + result.stderr[-1500:]
    return result.stdout


def test_external_user_host_survives_real_pan_graceful_and_job_tree_exit(tmp_path):
    from packages.jobs.independent_client import IndependentJobClient
    from packages.core.terminal.guard import JobObjectGuard
    from packages.core.terminal.identity import read_creation_filetime, close_handle_checked, wait_state, ProcessStatus
    pwsh = shutil.which("pwsh")
    assert pwsh, "PowerShell 7 is required for acceptance"
    task_name = "PanIndependentAcceptance_" + uuid.uuid4().hex[:12]
    host_root = tmp_path / "external-host"
    pan_root = tmp_path / "isolated-pan"
    endpoint = f"http://127.0.0.1:{free_port()}"
    pan_port = free_port()
    pan_endpoint = f"http://127.0.0.1:{pan_port}"
    client = IndependentJobClient(endpoint, timeout=2)
    session_id = "ses_" + uuid.uuid4().hex[:16]
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("PAN_") and k.upper() != "PYTHONPATH"}
    env["PYTHONPATH"] = str(REPO)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    current = None
    service_handle = None
    guards = []
    streams = []
    report = {"taskName": task_name, "scenarios": [], "panIdentities": []}
    try:
        installed = subprocess.run([pwsh, "-NoProfile", "-NonInteractive", "-File",
            str(REPO / "scripts/register_independent_job_host.ps1"), "-Python", sys.executable,
            "-DataRoot", str(host_root), "-Port", endpoint.rsplit(":", 1)[1],
            "-TaskName", task_name, "-StartNow"], capture_output=True, text=True, encoding="utf-8", timeout=25)
        assert installed.returncode == 0, "external user-host registration failed: " + installed.stderr[-1500:]
        wait_for(lambda: client.health().get("ok") is True)
        report["scheduler"] = json.loads(ps(pwsh,
            "param($n) $t=Get-ScheduledTask -TaskName $n; @{state=[string]$t.State; user=$t.Principal.UserId; logon=[string]$t.Principal.LogonType; action=$t.Actions.Execute} | ConvertTo-Json -Compress", task_name))
        assert report["scheduler"]["state"] == "Running"
        assert Path(report["scheduler"]["action"]).name.lower() == "pythonw.exe"
        import psutil
        listener = next(connection for connection in psutil.net_connections(kind="tcp")
                        if connection.status == psutil.CONN_LISTEN and connection.laddr.port == int(endpoint.rsplit(":", 1)[1]))
        service_pid = listener.pid
        assert str(host_root) in " ".join(psutil.Process(service_pid).cmdline())
        service_handle = kernel.OpenProcess(0x1000 | 0x100000, False, service_pid)
        assert service_handle
        report["serviceIdentity"] = {"pid": service_pid, "filetime": str(read_creation_filetime(service_handle)),
            "parentChain": [{"pid": parent.pid, "name": parent.name()} for parent in psutil.Process(service_pid).parents()[:6]]}

        def start_pan():
            nonlocal current
            identity_file = pan_root / "pan-identity.json"
            if identity_file.exists():
                identity_file.unlink()
            stream = (tmp_path / f"pan-{len(report['panIdentities'])}.txt").open("wb")
            streams.append(stream)
            current = subprocess.Popen([sys.executable,
                str(REPO / "audit/independent-jobs/20261010/isolated_pan.py"),
                "--root", str(pan_root), "--port", str(pan_port), "--job-endpoint", endpoint,
                "--session-id", session_id], cwd=REPO, env=env, stdin=subprocess.DEVNULL,
                stdout=stream, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
            wait_for(lambda: identity_file.exists())
            identity = json.loads(identity_file.read_text(encoding="utf-8"))
            handle = kernel.OpenProcess(0x0100 | 0x0001 | 0x1000 | 0x100000, False, identity["pid"])
            assert handle and str(read_creation_filetime(handle)) == identity["filetime"]
            guard = JobObjectGuard(kill_on_close=True)
            try:
                guard.assign(handle)
                assert guard.is_member(handle)
                assert guard.is_member(service_handle) is False
            finally:
                close_handle_checked(handle)
            guards.append(guard)
            report["panIdentities"].append(identity)
            wait_for(lambda: http(pan_endpoint, "/api/health").get("status") == "ok")
            return guard

        for mode in ("graceful", "job-tree-kill"):
            guard = start_pan()
            marker = tmp_path / (mode + ".done")
            command = "import time; from pathlib import Path; time.sleep(5); Path(" + repr(str(marker)) + ").write_text('done')"
            created = http(pan_endpoint, "/api/jobs", {"kind": "background-process", "target": {"sessionId": session_id},
                "name": "Validate independent deployment", "argv": [sys.executable, "-c", command], "cwd": str(tmp_path)})
            assert created["ok"] is True
            job = created["job"]
            assert job["executionOwner"] == "independent-job-service"
            wait_for(lambda: client.get(job["jobId"])["status"] == "running")
            if mode == "graceful":
                assert http(pan_endpoint, "/api/internal/main/shutdown", {})["ok"] is True
                current.wait(timeout=15)
                assert current.returncode == 0
            else:
                _, remaining = guard.terminate_tree(report["panIdentities"][-1]["pid"], timeout=5)
                assert remaining == []
                current.wait(timeout=5)
                assert current.returncode != 0
            assert client.health()["ok"] is True
            completed = wait_for(lambda: (j if (j := client.get(job["jobId"]))["status"] == "completed" else None))
            assert marker.read_text() == "done"
            assert any(event["jobId"] == job["jobId"] for event in client.events()["events"])
            start_pan()
            wait_for(lambda: client.get(job["jobId"])["notificationState"] == "delivered")
            record = json.loads((pan_root / "sessions" / (session_id + ".json")).read_text(encoding="utf-8"))
            assert len([row for row in record["queue_pending"] if row.get("eventId") == job["jobId"] + ":terminal"]) == 1
            assert client.events() == {"events": []}
            assert http(pan_endpoint, "/api/internal/main/shutdown", {})["ok"] is True
            current.wait(timeout=15)
            assert current.returncode == 0
            report["scenarios"].append({"mode": mode, "jobId": job["jobId"], "exitCode": completed["exitCode"],
                                        "commandSurvived": True, "durableNotices": 1, "outboxAcked": True,
                                        "serviceOutsidePanJob": True})
        report["passed"] = True
    finally:
        for guard in guards:
            if guard.handle_open:
                guard.terminate_tree(0, timeout=5)
                assert guard.close()
        if current is not None:
            current.wait(timeout=5)
        for stream in streams:
            stream.close()
        ps(pwsh, "param($n) $t=Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue; if($t){Stop-ScheduledTask -TaskName $n; Unregister-ScheduledTask -TaskName $n -Confirm:$false}", task_name)
        if service_handle:
            report["serviceExited"] = bool(wait_for(lambda: wait_state(service_handle) is ProcessStatus.DEAD, timeout=5))
            assert close_handle_checked(service_handle)
        report["taskRemoved"] = True
        artifact = os.environ.get("PAN_TEST_INDEPENDENT_HOST_REPORT")
        if artifact:
            Path(artifact).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
