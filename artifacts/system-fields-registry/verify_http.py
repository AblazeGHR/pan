"""Owned real-socket HTTP check for the system-fields discovery contract.

Starts only the FastAPI router on an ephemeral 127.0.0.1 port. It never starts
the Pan application, a provider, a Worker, the practical service, or ports
8767/8768. Run through scripts/run_tests_isolated.py.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import uvicorn
from fastapi import FastAPI

from packages.automation.api import install

ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN_PORTS = {8767, 8768}


def main() -> int:
    app = FastAPI()
    install(SimpleNamespace(app=app))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error"))
    thread = threading.Thread(target=server.run, name="system-fields-http", daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started, "owned HTTP listener did not start"
    port = server.servers[0].sockets[0].getsockname()[1]
    assert port not in FORBIDDEN_PORTS, port
    try:
        with httpx.Client(trust_env=False, timeout=5) as client:
            listing = client.get(f"http://127.0.0.1:{port}/api/automation/system-fields")
            schema = client.get(f"http://127.0.0.1:{port}/api/automation/schema")
            post = client.post(f"http://127.0.0.1:{port}/api/automation/system-fields", json={})
        assert listing.status_code == 200, listing.text
        assert schema.status_code == 200, schema.text
        assert post.status_code == 405, post.status_code
        body = listing.json()
        names = [row["name"] for row in body["fields"]]
        assert len(names) == 10 and names[0] == "sessionId"
        assert body["canonicalOnly"] is True
        assert body["aliases"]["source_session_id"] == "sourceSessionId"
        assert body["actionTargets"] == ["sessionId", "sourceSessionId"]
        assert schema.json()["systemFields"]["path"] == "/api/automation/system-fields"
        evidence = {
            "ok": True,
            "host": "127.0.0.1",
            "port": port,
            "fallbackPortsUsed": False,
            "fields": names,
            "actionTargets": body["actionTargets"],
            "readOnlyPostStatus": post.status_code,
            "schemaPointer": schema.json()["systemFields"],
        }
    finally:
        server.should_exit = True
        thread.join(5)
    out = ROOT / "artifacts" / "system-fields-registry"
    out.mkdir(parents=True, exist_ok=True)
    (out / "http-evidence.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(json.dumps(evidence, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
