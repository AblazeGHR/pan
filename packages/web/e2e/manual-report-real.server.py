"""Isolated real-backend launcher for the manual-report real-chain E2E.

Test-only launcher: the real FastAPI app, the real Session store on real
disk, the real /ws path.  Nothing here replaces a product route; the only
writes are the disposable fixtures under PAN_E2E_RUNTIME.

Seeded fixtures (all children are managed by ses_mr_mgr):
    ses_mr_plain_seq     plain run, durable taskSeq + resultCursor
    ses_mr_plain_cursor  plain run, resultCursor only (no taskSeq anywhere)
    ses_mr_interrupted   a run whose completion is NOT an exposed durable
                         terminal (status outside done/error/cancelled)
    ses_mr_legacy_done   real LEGACY data: a done terminal with NEITHER
                         taskSeq nor resultCursor (exactly one such row)
    ses_mr_legacy_ambig  two such rows in one Session (compat cursor 0 twice)
    ses_mr_legacy_mixed  one such legacy row beside a REAL plain run (stored
                         taskSeq/resultCursor 1) — old and new blocks together
    ses_mr_cursor_zero   a raw resultCursor 0 row beside a derived-0 legacy row
                         (raw 0 is unreachable in production; this fixture
                         probes the resolver's exact-first precedence and the
                         UI's uniform withholding for indistinguishable views)
    ses_mr_task          formal assign run (taskId + taskSeq)
    ses_mr_broken        managed_by points at a missing manager session

The plain runs mirror `_append_plain_run` in tests/test_manual_task_report.py
exactly: user row (source user) → answer rows → the run's result row → the
terminal record written with the POST-append history epoch/revision, so the
run's tail block is the result row and the boundary arithmetic is real.
"""
from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path

import uvicorn

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))

RUNTIME = Path(os.environ["PAN_E2E_RUNTIME"]).resolve()
SESSION_DIR = RUNTIME / "sessions"
DATA_DIR = RUNTIME / "data"
PORT = int(os.environ.get("PAN_PORT", "0"))
if PORT in (8767, 8768):
    raise RuntimeError("E2E requires an owned dynamic port, never a Pan port")

RUNTIME.mkdir(parents=True, exist_ok=True)
os.environ["PAN_BACKGROUND_JOBS_DIR"] = str(RUNTIME / "background-jobs")
os.environ["PAN_SCHEDULER_DIR"] = str(RUNTIME / "scheduler")

from packages.core import config as fixture_config

fixture_config.CONFIG_FILE = RUNTIME / "config.json"
fixture_config.CONFIG_FILE.write_text(
    json.dumps({
        "plugin_manifests": [],
        "scheduler": {"enabled": False},
        # A real user's setup has the quick-location rail enabled, so the
        # live-overlay checks run against the production dock + rail.  The
        # dock starts folded (inert); only the checks that hover it mount it.
        "ui": {"showMessageNavigationRail": True},
    }),
    encoding="utf-8",
)

from packages.core import session as session_store
from packages.core import workspace as workspace_store

def _append_run(session, *, task_id, task_seq, answers, result_cursor=None,
                with_seq=True, with_cursor=True, status="done",
                user_text: str | None = None) -> str | None:
    """Append one run and its durable terminal, production-ordered.

    "No identity" honesty: the real identity view derives a compatibility
    cursor for every row (raw resultCursor, else taskSeq, else 0), so a done
    row is always addressable in the merged system.  The honest no-entry
    fixture is a completion the view does not expose at all — a status
    outside done/error/cancelled — never a faked missing id.
    """
    if task_id:
        session_store.append_history(session, {
            "role": "user", "content": f"do {task_id}",
            "source": "agent", "taskId": task_id, "taskIdSource": "assign",
        })
    else:
        session_store.append_history(session, {
            "role": "user", "content": user_text or f"plain message {task_seq}",
            "source": "user",
        })
    for answer in answers:
        session_store.append_history(session, {"role": "assistant", "content": answer})
    body = answers[-1] if answers else "done"
    # The run's result row is appended before the boundary is recorded.
    session_store.append_history(session, {"role": "assistant", "content": body})
    record: dict = {
        "status": status,
        "result": body,
        "taskId": task_id,
        "workerId": "worker-1",
        "terminalKey": f"terminal:{task_id}:{task_seq}" if task_id else f"seq:{task_seq}",
        "historyEpoch": session.history_epoch,
        "historyRevision": session.history_revision,
    }
    if with_seq:
        record["taskSeq"] = task_seq
    if with_cursor:
        record["resultCursor"] = task_seq if result_cursor is None else result_cursor
    session.terminal_results = list(session.terminal_results) + [record]
    session.last_result = dict(record)
    return session.history[-1]["messageId"]


def _seed() -> dict:
    """Seed the disposable fixtures on real disk and return name→id map."""
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    session_store._cache.clear()
    session_store._all_loaded = False

    # Idempotent across restarts: an already-seeded runtime keeps its exact
    # Sessions (ids, histories, terminals, receipts).  This is what makes the
    # restart-dedup assertion honest — the replay must hit the SAME durable
    # receipts on disk, never a freshly minted generation.
    existing = {session.name: session for session in session_store.list_all(load_history=False)}
    manager = existing.get("MR Manager")
    if manager is None:
        manager = session_store.create("MR Manager", adapter="cbc")
    # The delivered reports land in the manager's queue; keep that queue paused
    # so no worker (and no real CLI) picks the fixture deliveries up while the
    # receipts are being asserted.  The receipts are the durable evidence.
    manager.queue_paused = True
    session_store.save_full(manager)
    fixtures: dict = {"managerId": manager.id, "children": {}}

    def _child(name: str, managed_by: str, append) -> None:
        child = existing.get(name)
        if child is None:
            child = session_store.create(name, adapter="cbc")
            child.managed_by = managed_by
            append(child)
            session_store.save_full(child)
        fixtures["children"][name] = child.id

    _child("MR Plain Seq", manager.id, lambda s: _append_run(
        s, task_id=None, task_seq=1, answers=["普通回答一"], result_cursor=1))
    _child("MR Plain Cursor", manager.id, lambda s: _append_run(
        s, task_id=None, task_seq=7, answers=["游标回答"], result_cursor=7, with_seq=False))
    _child("MR Interrupted Run", manager.id, lambda s: _append_run(
        s, task_id=None, task_seq=9, answers=["中断回答"],
        with_seq=False, with_cursor=False, status="interrupted"))
    _child("MR Formal Task", manager.id, lambda s: _append_run(
        s, task_id="mr-task", task_seq=3, answers=["正式任务回答"], result_cursor=3))
    _child("MR Broken Manager", "ses_mr_absent", lambda s: _append_run(
        s, task_id=None, task_seq=2, answers=["坏 manager 回答"], result_cursor=2))
    # Legacy rows: no taskSeq and no resultCursor anywhere.  The identity view
    # derives a compatibility cursor (raw resultCursor -> taskSeq -> 0), so the
    # exactly-one case is addressable through the documented compat path while
    # the two-row case is ambiguous and must be refused server-side.
    _child("MR Legacy Done", manager.id, lambda s: _append_run(
        s, task_id=None, task_seq=0, answers=["遗留回答"], with_seq=False,
        with_cursor=False, user_text="遗留提问"))
    _child("MR Legacy Ambiguous", manager.id, lambda s: (
        _append_run(s, task_id=None, task_seq=0, answers=["歧义回答一"],
                    with_seq=False, with_cursor=False, user_text="歧义提问一"),
        _append_run(s, task_id=None, task_seq=0, answers=["歧义回答二"],
                    with_seq=False, with_cursor=False, user_text="歧义提问二"),
    ))
    _child("MR Legacy Mixed", manager.id, lambda s: (
        _append_run(s, task_id=None, task_seq=0, answers=["旧块回答"],
                    with_seq=False, with_cursor=False, user_text="旧块提问"),
        _append_run(s, task_id=None, task_seq=1, answers=["新块回答"],
                    result_cursor=1, user_text="新块提问"),
    ))
    _child("MR Cursor Zero", manager.id, lambda s: (
        _append_run(s, task_id=None, task_seq=0, answers=["派生零回答"],
                    with_seq=False, with_cursor=False, user_text="派生零提问"),
        _append_run(s, task_id=None, task_seq=0, answers=["原始零回答"],
                    with_seq=False, with_cursor=True, result_cursor=0,
                    user_text="原始零提问"),
    ))

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


def main() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        probe.bind(("127.0.0.1", PORT))

    import packages.web.server as server_module

    _install_test_isolation(server_module)
    fixtures = _seed()
    (RUNTIME / "server-identity.json").write_text(
        json.dumps({"port": PORT, "runtime": str(RUNTIME), "mode": "manual-report-real",
                    "managerId": fixtures["managerId"]}),
        encoding="utf-8",
    )
    config = uvicorn.Config(server_module.app, host="127.0.0.1", port=PORT,
                            log_level="info", access_log=True)
    uvicorn.Server(config).run()


if __name__ == "__main__":
    main()
