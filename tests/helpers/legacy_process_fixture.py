"""Synthetic pre-R5 records for retained native cleanup tests only.

This is not a product creator or a migration path. Explicit fixture bytes let
the native failure tests keep exercising the old retained handles until R6,
while public and in-process legacy admission stay permanently retired.
"""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import uuid


def historical_process(root, argv, cwd, *, launch=True):
    root = Path(root).resolve()
    logs = root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    identity = "job_" + uuid.uuid4().hex[:24]
    payload = {"argv": argv, "cwd": str(cwd), "targetSessionId": "owned",
               "creatorSessionId": None, "label": None, "name": "Historical cleanup fixture",
               "description": None, "hideWindow": True}
    with sqlite3.connect(root / "jobs.sqlite3") as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, payload TEXT NOT NULL, status TEXT NOT NULL,
                created REAL NOT NULL, updated REAL NOT NULL,
                runner_pid INTEGER, runner_filetime TEXT, task_pid INTEGER,
                task_filetime TEXT, runner_claim TEXT, exit_code INTEGER, log_path TEXT NOT NULL,
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                notification_state TEXT NOT NULL DEFAULT 'pending',
                idempotency_key TEXT UNIQUE, payload_hash TEXT NOT NULL,
                execution_audit TEXT NOT NULL DEFAULT '{}');
            CREATE TABLE IF NOT EXISTS outbox (
                event_id TEXT PRIMARY KEY, job_id TEXT NOT NULL,
                payload TEXT NOT NULL, acked INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL);
        """)
        db.execute("INSERT INTO jobs(id,payload,status,created,updated,log_path,payload_hash) VALUES(?,?,?,?,?,?,?)",
                   (identity, json.dumps(payload), "starting", time.time(), time.time(),
                    str(logs / (identity + ".log")), "synthetic-fixture"))
    if launch:
        repo = Path(__file__).resolve().parents[2]
        env = {k: v for k, v in os.environ.items()
               if not k.upper().startswith("PAN_") and k.upper() != "PYTHONPATH"}
        env["PYTHONPATH"] = str(repo)
        options = ({"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
                   if os.name == "nt" else {"start_new_session": True})
        subprocess.Popen([sys.executable, "-m", "packages.jobs.independent_runner",
                          "--root", str(root), "--job-id", identity],
                         cwd=repo, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, close_fds=True, **options)
    from packages.jobs.independent_service import JobStore
    return JobStore(str(root)).get(identity)
