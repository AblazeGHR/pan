"""Real-HTTP checks for the composer URL-node contract.

Starts the real FastAPI app (the same routes the browser uses) on an isolated
loopback port, with the E2E fixture's Session/workspace/config directories
redirected to an owned runtime.  A deterministic fake CLI (no model/network)
is the provider, so the queue -> worker -> provider path is real.

Checks:
  * draft URL part PUT/GET round-trip, per-Session isolation, and the sidecar
    file that actually reaches disk;
  * POST /api/sessions/{id}/queue with the standard-Markdown text + text parts
    the composer sends, and the provider-boundary echo of that exact Markdown;
  * POST /api/send (legacy addressing layer) with standard-Markdown text.

Run from packages/web: python e2e/composerUrlNodeHttp.e2e.py
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve()
WEB = HERE.parent.parent
ROOT = WEB.parent.parent
OUTPUT = WEB / "test-results" / "composer-url-node-http"
RUNTIME = OUTPUT / "runtime"
OUTPUT.mkdir(parents=True, exist_ok=True)
if RUNTIME.exists():
    shutil.rmtree(RUNTIME)
RUNTIME.mkdir(parents=True)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


PORT = free_port()
assert PORT not in (8765, 8767, 8768), f"refusing shared port {PORT}"
os.environ["PAN_E2E_RUNTIME"] = str(RUNTIME)
os.environ["PAN_PORT"] = str(PORT)
sys.path.insert(0, str(ROOT))

# Import the E2E fixture (does not run main) and isolate the stores before the
# web app is imported.
import importlib.util

spec = importlib.util.spec_from_file_location(
    "browser_fixture", ROOT / "packages/web/e2e/server.py"
)
fixture = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(fixture)

from packages.core import session as sessions
from packages.core import worker as worker_module
from packages.core.adapters.cbc import adapter as cbc_module
from packages.core.adapters.cbc.adapter import CbcAdapter

CLI = ROOT / "tests/support/frontend_stream_cli.py"
CbcAdapter._resolve_cbc_argv = lambda self: [sys.executable, "-u", str(CLI)]
cbc_module.MCP_CONFIG_DIR = RUNTIME / "mcp-configs"
worker_module._GLOBAL_WATCHDOG_TICK_SEC = 0.5

import packages.web.server as server_module
from packages.core import session_drafts

fixture._install_test_isolation(server_module)
# session_drafts computes DRAFT_DIR at import time; the fixture patches the
# Session store afterwards, so redirect the sidecar explicitly.
session_drafts.DRAFT_DIR = RUNTIME / "session-drafts"


def seed() -> None:
    fixture.SESSION_DIR.mkdir(parents=True, exist_ok=True)
    for name in ("URL-A", "URL-B"):
        workdir = RUNTIME / "workdirs" / name
        workdir.mkdir(parents=True, exist_ok=True)
        session = sessions.create(name, adapter="cbc", workdir=str(workdir), history=[])
        session.mcp_servers = []
        session.model = "deterministic-fixture"
        sessions.save(session)


seed()

import uvicorn  # noqa: E402

config = uvicorn.Config(
    server_module.app, host="127.0.0.1", port=PORT, log_level="warning", access_log=False
)
server = uvicorn.Server(config)
thread = threading.Thread(target=server.run, daemon=True)
thread.start()

BASE = f"http://127.0.0.1:{PORT}"


def api(method: str, path: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_for(predicate, timeout: float = 20.0, interval: float = 0.1):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            last = predicate()
            if last:
                return last
        except Exception as exc:  # noqa: BLE001 - surfaced only if timeout expires
            last = exc
        time.sleep(interval)
    raise AssertionError(f"condition not met within {timeout}s; last={last!r}")


evidence: dict = {
    "port": PORT,
    "runtime": str(RUNTIME),
    "provider": "tests/support/frontend_stream_cli.py (deterministic, no model/network)",
    "checks": {},
}

try:
    sessions_payload = wait_for(
        lambda: api("GET", "/api/sessions?summary=1").get("sessions") or None
    )
    by_name = {item["name"]: item["id"] for item in sessions_payload}
    a, b = by_name["URL-A"], by_name["URL-B"]

    # ── Draft: URL part round-trip + per-Session isolation ──
    draft_a = {
        "text": "see ",
        "parts": [
            {"type": "text", "value": "see "},
            {
                "type": "url",
                "id": "url_http_a",
                "url": "https://example.test/draft-a?q=1&x=2#h",
                "label": "Docs",
            },
        ],
        "attachments": [],
    }
    draft_b = {
        "text": "",
        "parts": [
            {
                "type": "url",
                "id": "url_http_b",
                "url": "https://example.test/draft-b?q=3#other",
            },
        ],
        "attachments": [],
    }
    put_a = api("PUT", f"/api/sessions/{a}/draft", {"draft": draft_a, "baseRevision": None})
    put_b = api("PUT", f"/api/sessions/{b}/draft", {"draft": draft_b, "baseRevision": None})
    assert put_a["draft"] == draft_a, put_a
    assert put_b["draft"] == draft_b, put_b
    read_a = api("GET", f"/api/sessions/{a}/draft")
    read_b = api("GET", f"/api/sessions/{b}/draft")
    assert read_a["draft"] == draft_a, read_a
    assert read_b["draft"] == draft_b, read_b

    sidecar = RUNTIME / "session-drafts" / f"{a}.json"
    sidecar_payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert sidecar.is_file()
    assert any(part.get("type") == "url" for part in sidecar_payload["draft"]["parts"])

    evidence["checks"]["draft"] = {
        "a": read_a,
        "b": read_b,
        "sidecar": str(sidecar),
        "sidecarParts": sidecar_payload["draft"]["parts"],
    }

    # ── Queue: the actual Send payload (Markdown text + text parts) ──
    message = "See [Docs](https://example.test/send?q=1&x=2#h)"
    parts = [
        {"type": "text", "text": "See "},
        {"type": "text", "text": "[Docs](https://example.test/send?q=1&x=2#h)"},
    ]
    client_message_id = "url-node-http-queue-1"
    queued = api(
        "POST",
        f"/api/sessions/{b}/queue",
        {"text": message, "parts": parts, "clientMessageId": client_message_id},
    )
    assert queued.get("ok") is True, queued
    assert queued["item"]["text"] == message, queued
    queue_view = api("GET", f"/api/sessions/{b}/queue")
    queued_item = next(item for item in queue_view["items"] if item["text"] == message)
    assert queued_item["text"] == message, queued_item
    evidence["checks"]["queue"] = {
        "enqueue": queued,
        "readBack": queued_item,
        "parts": parts,
    }

    delivered = wait_for(
        lambda: (
            lambda history: history
            if any(
                row.get("role") == "assistant" and row.get("content") == f"answer:{message}"
                for row in history
            )
            else None
        )(api("GET", f"/api/sessions/{b}").get("history") or [])
    )
    user_rows = [row for row in delivered if row.get("role") == "user"]
    answer_rows = [row for row in delivered if row.get("role") == "assistant"]
    assert any(row.get("content") == message for row in user_rows), user_rows
    assert any(row.get("content") == f"answer:{message}" for row in answer_rows), answer_rows
    evidence["checks"]["queueDelivery"] = {
        "user": [row.get("content") for row in user_rows],
        "assistant": [row.get("content") for row in answer_rows],
    }

    # ── /api/send: the legacy addressing endpoint still carries the same MD ──
    legacy = "Legacy [Docs](https://example.test/legacy?q=1#h)"
    send_result = api(
        "POST", "/api/send", {"sessionId": b, "text": legacy, "source": "user"}
    )
    assert not send_result.get("error"), send_result
    legacy_delivery = wait_for(
        lambda: (
            lambda history: history
            if any(
                row.get("role") == "assistant" and row.get("content") == f"answer:{legacy}"
                for row in history
            )
            else None
        )(api("GET", f"/api/sessions/{b}").get("history") or [])
    )
    assert any(
        row.get("role") == "user" and row.get("content") == legacy for row in legacy_delivery
    ), legacy_delivery
    evidence["checks"]["apiSend"] = {
        "result": send_result,
        "user": [row.get("content") for row in legacy_delivery if row.get("role") == "user"],
        "assistant": [
            row.get("content") for row in legacy_delivery if row.get("role") == "assistant"
        ],
    }
finally:
    server.should_exit = True
    thread.join(timeout=15)
    (OUTPUT / "evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
    )

print(json.dumps(evidence, ensure_ascii=False, indent=2))
