"""Pan-independent canonical execution host with historical Job readers.

This module intentionally imports only the standard library. Run it with
``python -m packages.jobs.independent_service --root ABSOLUTE_PATH --port PORT``.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

MAX_BODY = 64 * 1024
REJECT_BODY_DRAIN_SECONDS = 1.0
JOB_ID_RE = re.compile(r"^job_[0-9a-f]{24}$")


def legacy_write(fn):
    # Lazy import keeps standalone module import stdlib-only.
    from functools import wraps
    @wraps(fn)
    def guarded(self, *args, **kwargs):
        from packages.jobs.runtime_owner import OwnerFence
        try:
            with OwnerFence(self.root.parent).writer():
                return fn(self, *args, **kwargs)
        except ValueError as exc:
            if getattr(exc, "code", None) == "revision_conflict":
                raise ServiceError(409, "canonical runtime owns this profile; legacy writer refused") from exc
            raise
    return guarded


class ServiceError(Exception):
    def __init__(self, status: int, message: str):
        self.status, self.message = status, message


class ServiceLock:
    """Compatibility name for the platform's process-lifetime root lock."""
    def __init__(self, root: Path):
        from packages.core.platform.runtime_lock import RuntimeLock
        self._lock = RuntimeLock(root / "service.lock")

    @property
    def path(self): return self._lock.path

    @property
    def file(self): return self._lock.file

    def acquire(self): self._lock.acquire()

    def release(self): self._lock.release()


class JobStore:
    def __init__(self, root: str):
        path = Path(root)
        if not path.is_absolute():
            raise ValueError("--root must be absolute")
        self.root = path.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "jobs.sqlite3"
        self.instance_id = secrets.token_hex(16)
        self._execution_host = None
        self._execution_lock = threading.Lock()

    @property
    def execution_host(self):
        # Canonical startup must not create or reserve a legacy registry/owner.
        from packages.jobs.independent_executor import ExecutionHost, ExecutionStore
        with self._execution_lock:
            if self._execution_host is None:
                self._execution_host = ExecutionHost(ExecutionStore(self.root))
            return self._execution_host

    def connect(self, *, readonly=False):
        if not self.db_path.exists():
            raise ServiceError(404, "historical job store not found")
        mode = "ro" if readonly else "rw"
        db = sqlite3.connect(self.db_path.as_uri() + "?mode=" + mode, uri=True, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        return db

    def create(self, request: dict):
        raise ServiceError(410, "legacy job creation retired; use canonical /executions")

    @legacy_write
    def set_unknown(self, job_id):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT execution_audit FROM jobs WHERE id=? AND status IN ('starting','running')",(job_id,)).fetchone()
            if row:
                from packages.jobs.system_actions import finish
                audit=json.loads(row['execution_audit']);finish(audit,'uncertain')
                db.execute("UPDATE jobs SET status='UNKNOWN',execution_audit=?,updated=? WHERE id=?",(json.dumps(audit),time.time(),job_id))
            db.execute('COMMIT')

    def get(self, job_id):
        with self.connect(readonly=True) as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise ServiceError(404, "job not found")
        return self._public(row)

    def list(self):
        if not self.db_path.exists():
            return {"jobs": []}
        with self.connect(readonly=True) as db:
            rows = db.execute("SELECT * FROM jobs ORDER BY created DESC").fetchall()
        return {"jobs": [self._public(r) for r in rows]}

    def _public(self, row):
        p = json.loads(row["payload"])
        audit=json.loads(row['execution_audit']);audit.pop('attemptSnapshot',None)
        result = {**audit,"jobId": row["id"], "kind": "background-process", "executionOwner": "independent-job-service", "status": row["status"],
                "argv": p["argv"], "cwd": p["cwd"], "label": p["label"],
                "hideWindow": p.get("hideWindow", True),
                "name": p.get("name"), "description": p.get("description"),
                "targetSessionId": p["targetSessionId"], "creatorSessionId": p["creatorSessionId"],
                "createdAt": row["created"], "updatedAt": row["updated"],
                "runnerPid": row["runner_pid"], "runnerFiletime": row["runner_filetime"],
                "exitCode": row["exit_code"], "logPath": row["log_path"],
                "notificationState": row["notification_state"]}
        result.update(legacy=True, readOnly=True)
        return result

    @legacy_write
    def request_cancel(self, job_id):
        job = self.get(job_id)
        if job["status"] not in ("starting", "running"):
            return job
        pid, expected = job.get("runnerPid"), job.get("runnerFiletime")
        if not pid or not expected or _live_identity(pid) != str(expected):
            raise ServiceError(409, "runner identity is not verified; cancellation refused")
        with self.connect() as db:
            db.execute("UPDATE jobs SET cancel_requested=1,updated=? WHERE id=?", (time.time(), job_id))
        return self.get(job_id)

    def retry(self, job_id):
        raise ServiceError(410, "legacy job retry retired; query the original execution")

    def events(self):
        if not self.db_path.exists():
            return {"events": []}
        with self.connect(readonly=True) as db:
            rows = db.execute("SELECT payload FROM outbox WHERE acked=0 ORDER BY created").fetchall()
        return {"events": [json.loads(r["payload"]) for r in rows]}

    @legacy_write
    def ack(self, event_id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT job_id FROM outbox WHERE event_id=?", (event_id,)).fetchone()
            if not row:
                raise ServiceError(404, "event not found")
            db.execute("UPDATE outbox SET acked=1 WHERE event_id=?", (event_id,))
            db.execute("UPDATE jobs SET notification_state='delivered',updated=? WHERE id=?", (time.time(), row["job_id"]))
            db.commit()
        return {"ok": True, "eventId": event_id}

    def recover(self):
        """Recover canonical receipts independently of the legacy writer fence."""
        if (self.root / "executions.sqlite3").exists():
            from packages.core.platform.execution_host import supervisor_alive
            self.execution_host.store.recover(supervisor_alive)
        if self.db_path.exists():
            from packages.jobs.runtime_owner import OwnerFence
            # A cut-over profile may retain legacy bytes for audit. Do not
            # reopen its writer or let it block canonical receipt recovery.
            if OwnerFence(self.root.parent).status() is None:
                self._recover_legacy()

    @legacy_write
    def _recover_legacy(self):
        with self.connect() as db:
            rows = db.execute("SELECT id,runner_pid,runner_filetime FROM jobs WHERE status IN ('starting','running')").fetchall()
        for row in rows:
            if row["runner_pid"] and row["runner_filetime"] and _live_identity(row["runner_pid"]) == row["runner_filetime"]:
                continue
            self.set_unknown(row["id"])


def _live_identity(pid):
    """Compatibility birth probe through the platform owner, never PID alone."""
    try:
        from packages.core.platform.process import default_process_owner, ProcessStatus
        owner = default_process_owner()
        identity = owner.identity(int(pid))
        if identity is not None:
            check = owner.check(identity)
            if check.status is ProcessStatus.ALIVE and check.ours is True:
                return identity.birth
    except Exception:
        return None
    return None


class Handler(BaseHTTPRequestHandler):
    server_version = "IndependentJobService/1"
    def log_message(self, *_):
        return
    @property
    def store(self): return self.server.store
    def _json(self, status, payload):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    def _discard_rejected_body(self):
        # Closing a Windows socket with an unread POST body can reset the
        # connection before the client receives our 403/415 response. Consume
        # only bounded, explicitly framed bytes; never parse JSON or touch the
        # store on this rejection path. Slow/incomplete bodies stay bounded.
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return
        if not 0 < length <= MAX_BODY:
            return
        previous_timeout = self.connection.gettimeout()
        deadline = time.monotonic() + REJECT_BODY_DRAIN_SECONDS
        try:
            while length:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("rejected request body drain timed out")
                self.connection.settimeout(remaining)
                chunk = self.rfile.read1(length)
                if not chunk:
                    break
                length -= len(chunk)
        finally:
            self.connection.settimeout(previous_timeout)

    def _body(self):
        try: length = int(self.headers.get("Content-Length", "-1"))
        except ValueError: raise ServiceError(400, "invalid Content-Length")
        if length < 0 or length > MAX_BODY: raise ServiceError(413 if length > MAX_BODY else 400, "invalid request body size")
        try: data = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError): raise ServiceError(400, "malformed JSON")
        if not isinstance(data, dict): raise ServiceError(400, "JSON object required")
        return data
    def _run(self, method):
        try:
            # Internal local client only: do not turn this command endpoint into
            # a browser-origin or DNS-rebinding execution interface.
            expected_host = f"127.0.0.1:{self.server.server_port}"
            if self.headers.get("Host") != expected_host or self.headers.get("Origin") is not None:
                raise ServiceError(403, "local client required")
            if method == "POST" and self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                raise ServiceError(415, "application/json required")
            path = unquote(urlsplit(self.path).path)
            if path == "/health" and method == "GET": return self._json(200, {"ok": True, "instanceId": self.store.instance_id})
            if path == "/executions" and method == "POST":
                from packages.jobs.independent_executor import ExecutionConflict
                try:
                    doc = self.store.execution_host.submit_execution(self._body())
                except ExecutionConflict as exc:
                    raise ServiceError(409, str(exc)) from exc
                except (KeyError, ValueError, TypeError) as exc:
                    raise ServiceError(400, "invalid canonical execution") from exc
                return self._json(202, doc)
            if path == "/execution-events" and method == "GET":
                return self._json(200, self.store.execution_host.store.events())
            if path.startswith("/execution-events/") and path.endswith("/ack") and method == "POST":
                try:
                    result = self.store.execution_host.store.ack(path[len("/execution-events/"):-4])
                except KeyError as exc:
                    raise ServiceError(404, "execution event not found") from exc
                return self._json(200, result)
            if path.startswith("/executions/"):
                parts = path.strip("/").split("/")
                if len(parts) in (2, 3) and re.fullmatch(r"effect_[0-9a-f]{64}", parts[1]):
                    from packages.jobs.independent_executor import ExecutionConflict
                    try:
                        if len(parts) == 2 and method == "GET":
                            return self._json(200, self.store.execution_host.store.get(parts[1]))
                        if len(parts) == 3 and parts[2] == "cancel" and method == "POST":
                            return self._json(200, self.store.execution_host.store.cancel(parts[1], self._body()["attempt"]))
                    except KeyError as exc:
                        raise ServiceError(404, "execution not found") from exc
                    except (ExecutionConflict, ValueError) as exc:
                        raise ServiceError(409, "execution fence/cancellation conflict") from exc
            if path == "/jobs" and method == "POST":
                self._body()
                raise ServiceError(410, "legacy job creation retired; use canonical /executions")
            if path == "/jobs" and method == "GET": return self._json(200, self.store.list())
            if path == "/events" and method == "GET": return self._json(200, self.store.events())
            if path.startswith("/events/") and method == "POST" and path.endswith("/ack"):
                return self._json(200, self.store.ack(path[len("/events/"):-4].rstrip("/")))
            parts = path.strip("/").split("/")
            if len(parts) in (2,3) and parts[0] == "jobs" and JOB_ID_RE.fullmatch(parts[1]):
                if len(parts) == 2 and method == "GET": return self._json(200, self.store.get(parts[1]))
                if len(parts) == 3 and method == "POST":
                    if parts[2] == "cancel": return self._json(200, self.store.request_cancel(parts[1]))
                    if parts[2] == "retry":
                        self._body()
                        raise ServiceError(410, "legacy job retry retired; query the original execution")
            raise ServiceError(404, "route not found")
        except ServiceError as e:
            if method == "POST" and e.status in {403, 415}:
                self._discard_rejected_body()
            self._json(e.status, {"error": e.message})
        except Exception: self._json(500, {"error": "internal service error"})
    def do_GET(self): self._run("GET")
    def do_POST(self): self._run("POST")


def serve(root: str, port: int):
    if not 1 <= port <= 65535: raise ValueError("--port must be 1..65535")
    root_path = Path(root)
    if not root_path.is_absolute(): raise ValueError("--root must be absolute")
    lock = ServiceLock(root_path.resolve())
    lock.acquire()
    httpd = None
    try:
        store = JobStore(str(root_path)); store.recover()
        httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        httpd.daemon_threads = True; httpd.store = store
        httpd.serve_forever()
    finally:
        if httpd is not None: httpd.server_close()
        lock.release()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", required=True); p.add_argument("--port", required=True, type=int)
    args = p.parse_args(argv); serve(args.root, args.port)

if __name__ == "__main__": main()
