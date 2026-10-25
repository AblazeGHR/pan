"""Small, Pan-independent host for argv background jobs.

This module intentionally imports only the standard library. Run it with
``python -m packages.jobs.independent_service --root ABSOLUTE_PATH --port PORT``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

MAX_BODY = 64 * 1024
REJECT_BODY_DRAIN_SECONDS = 1.0
JOB_ID_RE = re.compile(r"^job_[0-9a-f]{24}$")


class ServiceError(Exception):
    def __init__(self, status: int, message: str):
        self.status, self.message = status, message


class ServiceLock:
    """Non-blocking per-root single-writer lock held for the listener lifetime."""
    def __init__(self, root: Path):
        self.path = root / "service.lock"
        self.file = None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("a+b")
        self.file.seek(0)
        if os.name == "nt":
            import msvcrt
            if self.path.stat().st_size == 0:
                self.file.write(b"\0"); self.file.flush()
            self.file.seek(0)
            try: msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                self.file.close(); self.file = None
                raise RuntimeError("another independent Job service owns this root") from None
        else:
            import fcntl
            try: fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                self.file.close(); self.file = None
                raise RuntimeError("another independent Job service owns this root") from None

    def release(self):
        if self.file is None: return
        try:
            if os.name == "nt":
                import msvcrt
                self.file.seek(0); msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
        finally:
            self.file.close(); self.file = None


class JobStore:
    def __init__(self, root: str):
        path = Path(root)
        if not path.is_absolute():
            raise ValueError("--root must be absolute")
        self.root = path.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.logs = self.root / "logs"
        self.logs.mkdir(exist_ok=True)
        self.db_path = self.root / "jobs.sqlite3"
        self.instance_id = secrets.token_hex(16)
        self._init_db()

    @property
    def execution_host(self):
        # Lazy imports preserve the standalone legacy startup/import contract.
        from packages.jobs.independent_executor import ExecutionHost, ExecutionStore
        return ExecutionHost(ExecutionStore(self.root))

    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        return db

    def _init_db(self):
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                  id TEXT PRIMARY KEY, payload TEXT NOT NULL, status TEXT NOT NULL,
                  created REAL NOT NULL, updated REAL NOT NULL,
                  runner_pid INTEGER, runner_filetime TEXT, task_pid INTEGER,
                  task_filetime TEXT, runner_claim TEXT, exit_code INTEGER, log_path TEXT NOT NULL,
                  cancel_requested INTEGER NOT NULL DEFAULT 0,
                  notification_state TEXT NOT NULL DEFAULT 'pending',
                  idempotency_key TEXT UNIQUE, payload_hash TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS outbox (
                  event_id TEXT PRIMARY KEY, job_id TEXT NOT NULL,
                  payload TEXT NOT NULL, acked INTEGER NOT NULL DEFAULT 0,
                  created REAL NOT NULL,
                  FOREIGN KEY(job_id) REFERENCES jobs(id)
                );
            """)
            columns = {row["name"] for row in db.execute("PRAGMA table_info(jobs)")}
            if "runner_claim" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN runner_claim TEXT")
            if "execution_audit" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN execution_audit TEXT NOT NULL DEFAULT '{}'")

    def create(self, request: dict):
        from packages.jobs.window import validate_hide_window
        try:
            hide_window = validate_hide_window(request.get("hideWindow", True))
        except ValueError as exc:
            raise ServiceError(400, str(exc)) from exc
        argv = request.get("argv")
        cwd = request.get("cwd")
        target = request.get("targetSessionId")
        creator = request.get("creatorSessionId")
        label = request.get("label")
        name = request.get("name")
        description = request.get("description")
        idem = request.get("idempotencyKey")
        if not isinstance(argv, list) or not argv or any(not isinstance(x, str) or not x for x in argv):
            raise ServiceError(400, "argv must be a non-empty array of strings")
        if not isinstance(cwd, str) or not cwd or not Path(cwd).is_dir():
            raise ServiceError(400, "cwd must be an existing directory")
        if not isinstance(target, str) or not target:
            raise ServiceError(400, "targetSessionId is required")
        if creator is not None and not isinstance(creator, str):
            raise ServiceError(400, "creatorSessionId must be a string")
        if label is not None and not isinstance(label, str):
            raise ServiceError(400, "label must be a string")
        name = name.strip() if isinstance(name, str) else name
        if description is not None and not isinstance(description, str):
            raise ServiceError(400, "description must be a string")
        if idem is not None and (not isinstance(idem, str) or not idem or len(idem) > 256):
            raise ServiceError(400, "idempotencyKey must be a non-empty string up to 256 chars")
        payload = {"argv": argv, "cwd": str(Path(cwd).resolve()), "targetSessionId": target,
                   "creatorSessionId": creator, "label": label, "name": name,
                   "description": description, "hideWindow": hide_window}
        payload_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        now, job_id = time.time(), "job_" + secrets.token_hex(12)
        log_path = str((self.logs / f"{job_id}.log").resolve())
        from packages.jobs.system_actions import prepare
        admission = {"jobId":job_id,"kind":"background-process",**payload}
        # This host has already type-checked every declared process parameter.
        # Keep its stdlib-only standalone contract; no Pan runtime/schema import.
        prepare(admission,job_id+':initial',prevalidated=True)
        audit = {key:admission[key] for key in ('runId','attempts','attemptSnapshot','snapshotCleanup')}
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if idem:
                row = db.execute("SELECT id,payload_hash,payload FROM jobs WHERE idempotency_key=?", (idem,)).fetchone()
                if row:
                    db.execute("COMMIT")
                    previous = json.loads(row["payload"])
                    previous.setdefault("hideWindow", True)
                    if row["payload_hash"] != payload_hash and previous != payload:
                        raise ServiceError(409, "idempotencyKey already used with a different payload")
                    return self.get(row["id"])
            if not isinstance(name, str) or not name or len(name) > 200:
                raise ServiceError(400, "name must contain 1 to 200 characters")
            db.execute("INSERT INTO jobs(id,payload,status,created,updated,log_path,idempotency_key,payload_hash,execution_audit) VALUES(?,?,?,?,?,?,?,?,?)",
                       (job_id, payload_json, "starting", now, now, log_path, idem, payload_hash,json.dumps(audit)))
            db.execute("COMMIT")
        # The durable starting record exists before any runner can execute.
        runner_env = {k: v for k, v in os.environ.items() if not k.upper().startswith("PAN_") and k.upper() != "PYTHONPATH"}
        runner_env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
        command = [sys.executable, "-m", "packages.jobs.independent_runner", "--root", str(self.root), "--job-id", job_id]
        kwargs = {"cwd": str(Path(__file__).resolve().parents[2]), "env": runner_env,
                  "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                  "close_fds": True}
        if os.name == "nt":
            # Preserve a windowless console through Python venv redirectors.
            # DETACHED_PROCESS is not inherited by their flags=0 child launch.
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        try:
            subprocess.Popen(command, **kwargs)
        except Exception:
            # Do not re-run an ambiguous launch. Leave durable starting facts for inspection.
            self.set_unknown(job_id)
            raise ServiceError(503, "runner launch outcome is uncertain; job left UNKNOWN")
        return self.get(job_id)

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
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise ServiceError(404, "job not found")
        return self._public(row)

    def list(self):
        with self.connect() as db:
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
        from packages.jobs.unified import project
        result['unified']=project(result)
        return result

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
        original = self.get(job_id)
        if original["status"] == "UNKNOWN":
            raise ServiceError(409, "UNKNOWN job ownership is unresolved; retry refused")
        if original["status"] not in ("failed", "cancelled"):
            raise ServiceError(409, "only failed or cancelled jobs may be retried")
        req = {k: original.get(k) for k in ("argv", "cwd", "targetSessionId", "creatorSessionId", "label", "name", "description", "hideWindow")}
        req["name"] = (req.get("name") or "Retry background process").strip()[:200] or "Retry background process"
        return self.create(req)

    def events(self):
        with self.connect() as db:
            rows = db.execute("SELECT payload FROM outbox WHERE acked=0 ORDER BY created").fetchall()
        return {"events": [json.loads(r["payload"]) for r in rows]}

    def ack(self, event_id):
        with self.connect() as db:
            row = db.execute("SELECT job_id FROM outbox WHERE event_id=?", (event_id,)).fetchone()
            if not row:
                raise ServiceError(404, "event not found")
            db.execute("UPDATE outbox SET acked=1 WHERE event_id=?", (event_id,))
            db.execute("UPDATE jobs SET notification_state='delivered',updated=? WHERE id=?", (time.time(), row["job_id"]))
        return {"ok": True, "eventId": event_id}

    def recover(self):
        """Active registrations without a verified live runner become explicit UNKNOWN."""
        if (self.root / "executions.sqlite3").exists():
            from packages.core.platform.execution_host import supervisor_alive
            self.execution_host.store.recover(supervisor_alive)
        with self.connect() as db:
            rows = db.execute("SELECT id,runner_pid,runner_filetime FROM jobs WHERE status IN ('starting','running')").fetchall()
        for row in rows:
            if row["runner_pid"] and row["runner_filetime"]:
                try:
                    from packages.core.terminal.identity import probe_process
                    probe = probe_process(int(row["runner_pid"]))
                    alive = (getattr(probe.status, "value", probe.status) == "alive" and
                             probe.identity and str(probe.identity.created_at_filetime) == row["runner_filetime"])
                    if alive:
                        continue
                except Exception:
                    pass
            self.set_unknown(row["id"])


def _live_identity(pid):
    """Return a live PID's raw creation identity; inability to verify is None."""
    if os.name != "nt":
        try:
            stat = Path(f"/proc/{int(pid)}/stat").read_text()
            rest = stat[stat.rfind(")") + 2:].split()
            return rest[19] if len(rest) > 19 else None
        except OSError:
            return None
    try:
        from packages.core.terminal.identity import probe_process
        probe = probe_process(int(pid))
        if getattr(probe.status, "value", probe.status) != "alive" or not probe.identity:
            return None
        return str(probe.identity.created_at_filetime)
    except Exception:
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
            if path == "/jobs" and method == "POST": return self._json(201, self.store.create(self._body()))
            if path == "/jobs" and method == "GET": return self._json(200, self.store.list())
            if path == "/events" and method == "GET": return self._json(200, self.store.events())
            if path.startswith("/events/") and method == "POST" and path.endswith("/ack"):
                return self._json(200, self.store.ack(path[len("/events/"):-4].rstrip("/")))
            parts = path.strip("/").split("/")
            if len(parts) in (2,3) and parts[0] == "jobs" and JOB_ID_RE.fullmatch(parts[1]):
                if len(parts) == 2 and method == "GET": return self._json(200, self.store.get(parts[1]))
                if len(parts) == 3 and method == "POST":
                    if parts[2] == "cancel": return self._json(200, self.store.request_cancel(parts[1]))
                    if parts[2] == "retry": return self._json(201, self.store.retry(parts[1]))
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
