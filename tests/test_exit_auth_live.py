"""Real unauthenticated HTTP + same-user IPC + durable Exit + foreign listener."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx
import psutil
import pytest

from packages.core import launcher


@pytest.mark.skipif(os.name != "nt", reason="production service.lifecycle is Windows-only")
def test_cli_exit_without_browser_login_preserves_foreign_same_port_listener(tmp_path):
    source = Path(__file__).parents[1]
    root = tmp_path / "owned host"
    root.mkdir()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    assert port not in (8767, 8768)
    (root / "config.json").write_text(json.dumps({"port": port, "python": sys.executable,
                                                 "mcp_remote": {"enabled": False}}), encoding="utf-8")
    (root / "main.py").write_text("from tests.support.exit_auth_host import main\nmain()\n", encoding="utf-8")
    supervisor_marker = root / "packages/core/main_lifecycle.py"
    supervisor_marker.parent.mkdir(parents=True)
    supervisor_marker.write_text("# path existence marker; real module resolves from PYTHONPATH\n", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("PAN_")}
    env.update(PYTHONPATH=str(source), PYTHONIOENCODING="utf-8")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    log = (tmp_path / "host.log").open("wb")
    host = subprocess.Popen([sys.executable, str(root / "main.py"), f"--pan-root-marker={root}"],
                            cwd=root, env=env, stdout=log, stderr=log, creationflags=flags)
    foreign = None
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=2) as client:
            deadline = time.monotonic() + 20
            while True:
                try:
                    if client.get("/api/health").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                if time.monotonic() > deadline or host.poll() is not None:
                    pytest.fail((tmp_path / "host.log").read_text(encoding="utf-8", errors="replace"))
                time.sleep(0.05)
            state = json.loads((root / "data/process.json").read_text(encoding="utf-8"))
            main_pid = state["main"]["pid"]
            foreign_log = tmp_path / "foreign-ready"
            code = ("import socket,time,pathlib; s=socket.socket();"
                    f"s.bind(('127.0.0.2',{port}));s.listen();"
                    f"pathlib.Path({str(foreign_log)!r}).write_text('ready');time.sleep(90)")
            foreign = subprocess.Popen([sys._base_executable, "-c", code], env=env, creationflags=flags)
            for _ in range(100):
                if foreign_log.exists():
                    break
                assert foreign.poll() is None
                time.sleep(0.02)
            assert foreign_log.exists()
            assert launcher.listener_owner(port, host="127.0.0.1") == main_pid
            assert launcher.listener_owner(port, host="127.0.0.2") == foreign.pid
            assert client.post("/api/main/exit", json={}).status_code == 401
            assert client.get("/api/sessions").status_code == 401
            assert client.get("/api/health").status_code == 200
            result = subprocess.run([sys.executable, "-m", "packages.core.cli", "--root", str(root),
                                     "exit", "--timeout", "20"], cwd=root, env=env,
                                    capture_output=True, text=True, timeout=30, creationflags=flags)
            assert result.returncode == 0, result.stdout + result.stderr + (tmp_path / "host.log").read_text(encoding="utf-8", errors="replace")
            from tests.support.lifecycle_observation import ReadOnlyExecutionStore
            from packages.core.lifecycle_action import receipt_root
            store = ReadOnlyExecutionStore(receipt_root(root))
            with store.connect() as db:
                docs = [json.loads(row[0]) for row in db.execute("SELECT document FROM executions")]
            assert len(docs) == 1
            doc = docs[0]
            assert doc['frozenPlan']['operation'] == 'service.exit'
            assert doc['frozenPlan']['arguments']['oldPid'] == main_pid
            assert doc['frozenPlan']['arguments']['oldPidCreatedAt'] == state['main']['createdAt']
            assert doc['result']['outcome'] == 'succeeded'
            proof = doc['result']['receipt']
            assert proof['phase'] == 'offline' and proof['offlineProof']['ok'] is True
            assert proof['offlineProof']['oldPid'] == main_pid
            assert not list((root / 'data/background_jobs/jobs').glob('*.json'))
            assert not launcher.process_alive(main_pid, state["main"]["createdAt"])
            assert launcher.listener_owner(port, host="127.0.0.1") is None
            assert foreign.poll() is None
            assert launcher.listener_owner(port, host="127.0.0.2") == foreign.pid
            host.wait(timeout=5)
    finally:
        # Only fixtures spawned by this test, never a port-based kill.
        for process in [host, foreign]:
            if process is not None and process.poll() is None:
                for child in psutil.Process(process.pid).children(recursive=True):
                    try:
                        child.kill()
                    except psutil.NoSuchProcess:
                        pass
                process.kill()
            if process is not None:
                process.wait(timeout=5)
        log.close()
