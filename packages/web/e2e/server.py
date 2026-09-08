"""Isolated Pan server for real Chromium E2E.

This module is a test-only launcher.  It patches the imported Session store to
an E2E-owned directory, seeds disposable fixtures, and exposes one disposable
stream injection route.  The browser still talks to the real FastAPI routes
and the real /ws broadcast path; no product route or frontend mock is used.
"""

from __future__ import annotations

import json
import os
import signal
import sys
from pathlib import Path

import uvicorn
from fastapi import Body

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))

from packages.core import session as session_store


RUNTIME = Path(os.environ["PAN_E2E_RUNTIME"]).resolve()
SESSION_DIR = RUNTIME / "sessions"
WORKDIR = RUNTIME / "session-workdir"
DATA_DIR = RUNTIME / "data"
PORT = int(os.environ.get("PAN_PORT", "8767"))


def _write_fixture_files() -> None:
    WORKDIR.mkdir(parents=True, exist_ok=True)
    (WORKDIR / "notes.md").write_text(
        "\n".join(f"notes line {i}" for i in range(1, 81)) + "\n",
        encoding="utf-8",
    )
    (WORKDIR / "linked.ts").write_text(
        "\n".join(f"export const linkedLine{i} = {i};" for i in range(1, 81)) + "\n",
        encoding="utf-8",
    )


def _stream_markdown() -> str:
    windows_path = str((WORKDIR / "linked.ts").resolve()).replace("\\", "/")
    file_uri = f"file:///{windows_path}" if len(windows_path) > 2 and windows_path[1:3] == ":/" else f"file://{windows_path}"
    return "\n".join(
        [
            "## Browser file-link fixtures",
            "[relative file](notes.md#L42)",
            f"[windows server path]({windows_path}#L42-L48)",
            f"[file URI]({file_uri})",
            "[http link](https://example.com/pan-e2e)",
            "[mailto link](mailto:pan-e2e@example.com)",
            "[anchor link](#local-anchor)",
            "[missing file](missing-e2e-file.md#L1)",
            '<a id="local-anchor"></a>',
        ]
    )


def _seed_sessions() -> None:
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    _write_fixture_files()
    session_store._cache.clear()
    session_store._all_loaded = False

    history = []
    for i in range(1, 46):
        history.append({"role": "user", "content": f"history question {i}"})
        history.append({"role": "assistant", "content": f"history answer {i} " + ("x " * 18)})
    history.append({"role": "assistant", "content": _stream_markdown()})
    session_store.create(
        "Chat Stream",
        adapter="cbc",
        workdir=str(WORKDIR),
        history=history,
    )
    for name in ["Alpha Session", "Bravo Session", "Charlie Session"]:
        session_store.create(name, adapter="cbc", workdir=str(WORKDIR))
    for i in range(1, 41):
        session_store.create(f"Drag {i:02d}", adapter="cbc", workdir=str(WORKDIR))


def _install_test_isolation(server_module) -> None:
    # Session is imported before server.py, so this replacement is effective
    # for every real session API handler in the server process.
    session_store.SESSION_DIR = SESSION_DIR
    session_store._cache.clear()
    session_store._all_loaded = False
    server_module.DATA_DIR = DATA_DIR
    server_module.WORKDIRS_DIR = RUNTIME / "workdirs"
    server_module.ATTACHMENTS_DIR = RUNTIME / "attachments"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    server_module.WORKDIRS_DIR.mkdir(parents=True, exist_ok=True)
    server_module.ATTACHMENTS_DIR.mkdir(parents=True, exist_ok=True)


def main() -> None:
    # Bind preflight is intentionally limited to the permitted isolated port.
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        probe.bind(("127.0.0.1", PORT))

    import packages.web.server as server_module

    _install_test_isolation(server_module)
    _seed_sessions()

    @server_module.app.post("/__e2e/stream")
    async def e2e_stream(payload: dict = Body(...)):
        """Inject a stream event through the real dashboard WS broadcaster."""
        session_id = str(payload["sessionId"])
        event = dict(payload["event"])
        await server_module.broadcast(
            {
                "type": "worker.stream",
                "sessionId": session_id,
                "workerId": "e2e-browser-worker",
                "generation": 0,
                "event": event,
            }
        )
        return {"ok": True}

    RUNTIME.mkdir(parents=True, exist_ok=True)
    (RUNTIME / "server-identity.json").write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "port": PORT,
                "checkout": str(PROJECT_ROOT),
                "runtime": str(RUNTIME),
                "sessionDir": str(SESSION_DIR),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    config = uvicorn.Config(server_module.app, host="127.0.0.1", port=PORT, log_level="info", access_log=True)
    uvicorn.Server(config).run()


if __name__ == "__main__":
    main()
