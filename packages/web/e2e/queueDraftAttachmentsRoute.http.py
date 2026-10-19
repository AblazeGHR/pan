"""Owned real HTTP/Chromium regression for the composer draft across routes.

Chat → Editor/Jobs → Chat must keep text, URL structure, attachment occurrence
identity and real pending File bytes. Reuses the same isolated fixture, real
FastAPI/WS paths and deterministic stdin provider as
queueDraftAttachments.http.py; no protected port, real model or user data.
"""
import asyncio
import hashlib
import importlib.util
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
RUNTIME = Path(tempfile.mkdtemp(prefix="route-", dir=OUTPUT))
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
session = sessions.create("Route A", adapter="cbc", workdir=str(fixture.WORKDIR), history=[])
session.mcp_servers = []
session.queue_paused = True
sessions.save(session)
pending_text = "pending route bytes \u4e2d\u6587\n"
uploaded_text = "uploaded route bytes \u4e2d\u6587\n"
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
            "session": session.id, "pendingText": pending_text, "uploadedText": uploaded_text,
            "serverFile": str(server_file)}
(RUNTIME / "identity.json").write_text(json.dumps(identity), encoding="utf-8")
try:
    result = subprocess.run(["node", str(WEB / "e2e/queueDraftAttachmentsRoute.chromium.mjs"),
                             str(RUNTIME / "identity.json")], cwd=ROOT,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            timeout=180,
                            **command_window_options())
    (RUNTIME / "chromium.log").write_bytes(result.stdout)
    print(result.stdout.decode("utf-8", errors="replace"))
    if result.returncode:
        raise SystemExit(result.returncode)

    # The browser only enqueues. Real worker hand-off happens here through the
    # deterministic stdin provider, so the recorded resources are the provider's
    # own evidence rather than the UI's claim.
    async def deliver():
        row = sessions.get(session.id)
        row.queue_paused = False
        await sessions.save_async(row)
        await worker.create_worker(row.id)
        deadline = time.monotonic() + 25
        while row.queue_pending and time.monotonic() < deadline:
            await asyncio.sleep(.1)
        assert not row.queue_pending, "fake provider did not consume queue"
        deadline = time.monotonic() + 15
        while not any("answer:" in str(item) for item in row.history) and time.monotonic() < deadline:
            await asyncio.sleep(.1)
        history = json.dumps(row.history)
        assert "route-pending.txt" in history and "route-upload.txt" in history, history
        inputs = [json.loads(line) for line in
                  (RUNTIME / "provider-inputs.jsonl").read_text(encoding="utf-8").splitlines()]
        assert len(inputs) == 1, inputs
        assert len(inputs[0]["resources"]) == 2, inputs[0]["resources"]
        assert "/api/attachments" not in inputs[0]["text"], inputs[0]["text"]
        expected = {
            hashlib.sha256(pending_text.encode()).hexdigest(),
            hashlib.sha256(uploaded_text.encode()).hexdigest(),
        }
        actual = {resource["sha256"] for resource in inputs[0]["resources"]}
        assert actual == expected, (actual, expected)
        (RUNTIME / "provider-history.json").write_text(history, encoding="utf-8")
        (RUNTIME / "provider-inputs.json").write_text(json.dumps(inputs, indent=2), encoding="utf-8")

    asyncio.run_coroutine_threadsafe(deliver(), server_module._DATA_RETENTION_LOOP).result(60)
finally:
    server.should_exit = True
    thread.join(15)
    assert not thread.is_alive(), "owned HTTP server did not terminate"
    identity["serverStopped"] = True
    (RUNTIME / "identity.json").write_text(json.dumps(identity), encoding="utf-8")
print(json.dumps({"runtime": str(RUNTIME), "status": "passed"}))
