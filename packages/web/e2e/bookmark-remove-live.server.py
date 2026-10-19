"""Isolated real-backend launcher for the bookmark remove-live E2E.

The browser talks to the real FastAPI app, the real Session/Bookmark stores on
real disk and the real `/ws` fan-out.  This launcher only (a) redirects the
stores to an E2E-owned runtime root and (b) owns a few explicit fault/timing
seams under `/__e2e/*`:

* ``delay_next_bookmarks_get`` captures the next cold bookmark envelope, then
  holds the HTTP response for a requested time.  The response body is computed
  at request time, so the browser receives a genuinely OLD snapshot after a
  newer removal has already reached it.
* ``fail_next_bookmark_delete`` answers the next real DELETE with HTTP 500
  before it touches the store, so the client rollback/toast path is exercised
  against a real failing request.
* ``broadcast_stale_add`` emits an older-revision ``bookmarks.changed`` frame
  through the real broadcaster; the client must ignore it.
* ``state`` reports whether the held GET is still parked.

No product route is replaced.  The fixture is disposable and never reads the
practical data root; ports 8767/8768 are rejected.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import socket
import sys
from pathlib import Path

import uvicorn
from fastapi import Body

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))

RUNTIME = Path(os.environ["PAN_E2E_RUNTIME"]).resolve()
PORT = int(os.environ["PAN_E2E_PORT"])
if PORT in (8767, 8768) or PORT < 1024:
    raise RuntimeError("E2E requires an explicitly selected, unprotected port")

SESSION_DIR = RUNTIME / "sessions"
DATA_DIR = RUNTIME / "data"

RUNTIME.mkdir(parents=True, exist_ok=True)
os.environ["PAN_BACKGROUND_JOBS_DIR"] = str(RUNTIME / "background-jobs")
os.environ["PAN_SCHEDULER_DIR"] = str(RUNTIME / "scheduler")

from packages.core import config as fixture_config

fixture_config.CONFIG_FILE = RUNTIME / "config.json"
fixture_config.CONFIG_FILE.write_text(
    json.dumps({
        "plugin_manifests": [],
        "scheduler": {"enabled": False},
        "ui": {"showMessageNavigationRail": True, "showHistorySearch": False},
    }),
    encoding="utf-8",
)

from packages.core import session as session_store
from packages.core import session_bookmarks
from packages.core import workspace as workspace_store

FIXTURE_NAME = "Bookmark Remove Live"
TARGET_TEXT = "书签删除回归：第一答（将被删除的书签）"
KEEP_TEXT = "书签删除回归：第二答（保留的书签）"


class HarnessState:
    delay_next_bookmarks_get = 0.0
    held_get = False
    fail_next_bookmark_delete = False


STATE = HarnessState()


def _seed() -> dict:
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    session_store._cache.clear()
    session_store._all_loaded = False
    existing = {session.name: session for session in session_store.list_all(load_history=False)}
    session = existing.get(FIXTURE_NAME)
    if session is None:
        session = session_store.create(FIXTURE_NAME, adapter="cbc")
        for role, content in [
            ("user", "书签删除回归：第一问"),
            ("assistant", TARGET_TEXT),
            ("user", "书签删除回归：第二问"),
            ("assistant", KEEP_TEXT),
        ]:
            session_store.append_history(session, {"role": role, "content": content})
        session_store.save_full(session)
    else:
        session = session_store.get(session.id)
        if session is None:
            raise RuntimeError("bookmark fixture Session disappeared")

    target = next((row for row in session.history if row.get("content") == TARGET_TEXT), None)
    keep = next((row for row in session.history if row.get("content") == KEEP_TEXT), None)
    if not target or not keep or not target.get("messageId") or not keep.get("messageId"):
        raise RuntimeError("bookmark fixture rows are missing Pan identities")
    for message_id in (target["messageId"], keep["messageId"]):
        session_bookmarks.add_bookmark(session.id, message_id)
    fixtures = {
        "sessionId": session.id,
        "targetId": target["messageId"],
        "keepId": keep["messageId"],
        "port": PORT,
    }
    (RUNTIME / "fixtures.json").write_text(json.dumps(fixtures, indent=2), encoding="utf-8")
    return fixtures


def _install_test_isolation(server_module) -> None:
    session_store.SESSION_DIR = SESSION_DIR
    session_store._cache.clear()
    session_store._all_loaded = False
    workspace_store.WORKSPACE_DIR = RUNTIME / "workspaces"
    workspace_store._cache.clear()
    workspace_store._loaded = False
    server_module.DATA_DIR = DATA_DIR
    server_module.WORKDIRS_DIR = RUNTIME / "workdirs"
    server_module.ATTACHMENTS_DIR = RUNTIME / "attachments"
    for directory in (DATA_DIR, server_module.WORKDIRS_DIR, server_module.ATTACHMENTS_DIR):
        directory.mkdir(parents=True, exist_ok=True)


class HarnessMiddleware:
    """Delay/fault seams with the product app unchanged underneath."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        method = scope.get("method")
        path = scope.get("path") or ""
        if (method == "GET" and STATE.delay_next_bookmarks_get > 0
                and re.fullmatch(r"/api/sessions/[^/]+/bookmarks", path)):
            delay = STATE.delay_next_bookmarks_get
            STATE.delay_next_bookmarks_get = 0.0
            captured: list[dict] = []

            async def capture(message):
                captured.append(message)

            await self.app(scope, receive, capture)
            STATE.held_get = True
            try:
                await asyncio.sleep(delay)
                for message in captured:
                    await send(message)
            except (asyncio.CancelledError, RuntimeError):
                pass
            finally:
                STATE.held_get = False
            return
        if (method == "DELETE" and STATE.fail_next_bookmark_delete
                and re.fullmatch(r"/api/sessions/[^/]+/bookmarks/[^/]+", path)):
            STATE.fail_next_bookmark_delete = False
            body = json.dumps(
                {"detail": "书签删除失败（E2E 注入）"}, ensure_ascii=False,
            ).encode("utf-8")
            await send({
                "type": "http.response.start",
                "status": 500,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            })
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)


def _install_controls(server_module) -> None:
    @server_module.app.post("/__e2e/control")
    async def control(payload: dict = Body(...)):
        action = payload.get("action")
        if action == "delay_next_bookmarks_get":
            STATE.delay_next_bookmarks_get = max(0, int(payload.get("ms", 0))) / 1000
            STATE.held_get = False
            return {"ok": True, "delayMs": int(STATE.delay_next_bookmarks_get * 1000)}
        if action == "fail_next_bookmark_delete":
            STATE.fail_next_bookmark_delete = True
            return {"ok": True}
        if action == "broadcast_stale_add":
            session_id = str(payload["sessionId"])
            message_ids = [str(value) for value in payload.get("messageIds", [])]
            await server_module.broadcast({
                "type": "bookmarks.changed",
                "sessionId": session_id,
                "revision": int(payload.get("revision", 0)),
                "action": "added",
                "messageIds": message_ids,
            })
            return {"ok": True}
        if action == "state":
            return {
                "ok": True,
                "heldGet": STATE.held_get,
                "delayPendingMs": int(STATE.delay_next_bookmarks_get * 1000),
                "failNextDelete": STATE.fail_next_bookmark_delete,
            }
        return {"ok": False, "error": "unknown action"}


def main() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        probe.bind(("127.0.0.1", PORT))

    import packages.web.server as server_module

    _install_test_isolation(server_module)
    fixtures = _seed()
    _install_controls(server_module)
    (RUNTIME / "server-identity.json").write_text(
        json.dumps({
            "pid": os.getpid(), "port": PORT, "runtime": str(RUNTIME),
            "checkout": str(PROJECT_ROOT), "sessionId": fixtures["sessionId"],
        }),
        encoding="utf-8",
    )
    uvicorn.Server(uvicorn.Config(
        HarnessMiddleware(server_module.app),
        host="127.0.0.1", port=PORT, log_level="info", access_log=False,
    )).run()


if __name__ == "__main__":
    main()
