"""Owned test launcher: time real Locker handlers and SQLite transactions."""
import atexit
from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
from pathlib import Path
import re
import sys
import time

from packages.interactions.store import InteractionStore
from packages.web import server

current = ContextVar("cpx1_locker_request", default=None)
records = []
output = Path(os.environ["CPX1_DIAGNOSTIC_DIR"]) / "locker-server.json"
started_ns = time.monotonic_ns()
transaction_sequence = 0


def mark(phase, **extra):
    identity = current.get()
    if identity is not None:
        records.append({**identity, "phase": phase, "monotonic_ns": time.monotonic_ns(), **extra})


def persist():
    output.write_text(json.dumps({"started_monotonic_ns": started_ns, "events": records}, indent=2), encoding="utf-8")


atexit.register(persist)
original_db = InteractionStore.db


@contextmanager
def timed_db(self):
    global transaction_sequence
    transaction_sequence += 1
    transaction = transaction_sequence
    caller = sys._getframe(2).f_code.co_name
    if caller not in {"mark_message_read", "mark_read"}:
        caller = "other"
    details = {"transaction": transaction, "caller": caller}
    mark("db.enter", **details)
    try:
        with original_db(self) as db:
            mark("db.begin_complete", **details)
            try:
                yield db
            finally:
                mark("db.body_complete", **details)
    finally:
        mark("db.exit", **details)


InteractionStore.db = timed_db


class TimedRead:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        match = re.fullmatch(r"/api/(user-locker-messages|user-inbox|reminding-cards)/([^/]+)/read", scope.get("path", ""))
        if scope.get("type") != "http" or scope.get("method") != "PUT" or not match:
            return await self.app(scope, receive, send)
        token = current.set({"kind": match.group(1), "id": match.group(2)})
        mark("asgi.enter", wall_ns=time.time_ns())
        async def observed(message):
            if message.get("type") == "http.response.start":
                mark("response.start", status=message.get("status"))
            await send(message)
        try:
            return await self.app(scope, receive, observed)
        finally:
            mark("asgi.exit")
            current.reset(token)
            # Preserve a bounded prefix even when the owned server is interrupted.
            if len(records) % 48 == 0:
                persist()


server.app.add_middleware(TimedRead)
import tests.support.locker_product_http_server  # noqa: E402,F401
