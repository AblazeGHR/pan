"""Owned real HTTP/Chromium regression. No real model, protected port or data.

Run through scripts/run_tests_isolated.py. --baseline reproduces the old
pending-File loss using a baseline frontend build supplied by the caller.
"""
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[3]
WEB = ROOT / "packages/web"
OUTPUT = WEB / "test-results/queue-draft-attachments"
OUTPUT.mkdir(parents=True, exist_ok=True)
RUNTIME = Path(tempfile.mkdtemp(prefix="run-", dir=OUTPUT))
with socket.socket() as probe:
    probe.bind(("127.0.0.1", 0))
    PORT = probe.getsockname()[1]
assert PORT not in (8767, 8768)
os.environ["PAN_E2E_RUNTIME"] = str(RUNTIME)
os.environ["PAN_PORT"] = str(PORT)
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("fixture", WEB / "e2e/server.py")
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
import packages.web.server as server_module
from packages.core import session as sessions, session_drafts, worker
from packages.core.adapters.cbc.adapter import CbcAdapter
from packages.jobs.window import command_window_options
import uvicorn

fixture._install_test_isolation(server_module)
session_drafts.DRAFT_DIR = RUNTIME / "session-drafts"
# Deterministic local stdin provider; no CBC executable/model/account is used.
cli = RUNTIME / "recording-cli.py"
cli.write_text('''import sys, json, re, hashlib, runpy
from pathlib import Path
root = Path(sys.argv[1]).resolve()
original = sys.stdin
class Recorder:
    def __iter__(self):
        for line in original:
            data = json.loads(line)
            text = data.get("message", {}).get("content", [{}])[0].get("text", "")
            resources = []
            for name in re.findall(r'@"([^"]+)"', text):
                path = Path(name).resolve()
                assert path.is_relative_to(root)
                resources.append({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
            with (root / "provider-inputs.jsonl").open("a", encoding="utf-8") as out:
                out.write(json.dumps({"text": text, "resources": resources}) + "\\n")
            yield line
sys.stdin = Recorder()
runpy.run_path(sys.argv[2], run_name="__main__")
''', encoding="utf-8")
CbcAdapter._resolve_cbc_argv = lambda self: [sys.executable, "-u", str(cli), str(RUNTIME), str(ROOT / "tests/support/frontend_stream_cli.py")]
fixture.WORKDIR.mkdir(parents=True, exist_ok=True)
server_file = fixture.WORKDIR / "server.txt"
server_file.write_bytes(b"server attachment bytes\x00\xff")
seed = []
for name in ("Attachment A", "Attachment B"):
    session = sessions.create(name, adapter="cbc", workdir=str(fixture.WORKDIR), history=[])
    session.mcp_servers = []
    session.queue_paused = True
    sessions.save(session)
    seed.append(session.id)
config = uvicorn.Config(server_module.app, host="127.0.0.1", port=PORT, log_level="warning")
server = uvicorn.Server(config)
thread = threading.Thread(target=server.run, daemon=True)
thread.start()
deadline = time.monotonic() + 15
while not server.started:
    if not thread.is_alive() or time.monotonic() > deadline:
        raise RuntimeError("owned server failed to bind")
    time.sleep(.05)
identity = {"pid": os.getpid(), "port": PORT, "checkout": str(ROOT), "runtime": str(RUNTIME),
            "sessions": seed, "serverFile": str(server_file), "baseline": "--baseline" in sys.argv}
(RUNTIME / "identity.json").write_text(json.dumps(identity), encoding="utf-8")
try:
    result = subprocess.run(["node", str(WEB / "e2e/queueDraftAttachments.chromium.mjs"),
                             str(RUNTIME / "identity.json")], cwd=ROOT,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            timeout=120,
                            **command_window_options())
    (RUNTIME / "chromium.log").write_bytes(result.stdout)
    print(result.stdout.decode("utf-8", errors="replace"))
    if result.returncode:
        raise SystemExit(result.returncode)
    # Real worker dispatch through deterministic stdin provider, after browser
    # verifies the queue edit and explicitly releases Pause everything.
    if not identity["baseline"]:
        async def deliver():
            session = sessions.get(seed[0])
            session.queue_paused = False
            await sessions.save_async(session)
            await worker.create_worker(session.id)
            deadline = time.monotonic() + 20
            while session.queue_pending and time.monotonic() < deadline:
                import asyncio
                await asyncio.sleep(.1)
            assert not session.queue_pending, "fake provider did not consume queue"
            deadline = time.monotonic() + 10
            while not any("answer:" in str(row) for row in session.history) and time.monotonic() < deadline:
                await asyncio.sleep(.1)
            history = json.dumps(session.history)
            assert "client.txt" in history and "server.txt" in history, history
            inputs = [json.loads(line) for line in (RUNTIME / "provider-inputs.jsonl").read_text(encoding="utf-8").splitlines()]
            assert len(inputs) == 1 and len(inputs[0]["resources"]) == 2
            assert "/api/attachments" not in inputs[0]["text"]
            assert {resource["sha256"] for resource in inputs[0]["resources"]} == {
                hashlib.sha256(server_file.read_bytes()).hexdigest(),
                hashlib.sha256("client real bytes\x00 中文\n".encode()).hexdigest(),
            }
            (RUNTIME / "provider-history.json").write_text(history, encoding="utf-8")
        import asyncio
        asyncio.run_coroutine_threadsafe(deliver(), server_module._DATA_RETENTION_LOOP).result(40)
finally:
    server.should_exit = True
    thread.join(15)
    assert not thread.is_alive(), "owned HTTP server did not terminate"
    identity["serverStopped"] = True
    (RUNTIME / "identity.json").write_text(json.dumps(identity), encoding="utf-8")
print(json.dumps({"runtime": str(RUNTIME), "status": "passed"}))
