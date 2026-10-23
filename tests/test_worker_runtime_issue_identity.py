"""Worker error sampling must survive identity rejection without losing diagnostics."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from packages.core import runtime_issues
from packages.core import session_error_log as error_log
from packages.core import worker


RAW = {
    "threadId": "one",
    "thread_id": "two",
    "error": {"message": "synthetic capacity", "code": "serverOverloaded"},
}


def _worker() -> worker.Worker:
    return worker.Worker(worker_id="worker-probe", session_id="synthetic",
                         adapter=SimpleNamespace(name="codex"), status="running")


def _records(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _prepare(tmp_path):
    path = tmp_path / "session-errors.jsonl"
    error_log.reset_state()
    error_log.configure(path)
    previous = runtime_issues._OBSERVER
    observed = []
    runtime_issues.set_observer(observed.append)
    return path, previous, observed


def _cleanup(previous, path):
    runtime_issues.set_observer(previous)
    error_log.configure(None)
    error_log.reset_state()
    path.unlink(missing_ok=True)


def test_terminal_identity_conflict_keeps_original_jsonl_and_status(tmp_path):
    path, previous, observed = _prepare(tmp_path)
    w = _worker()
    try:
        asyncio.run(worker._record_terminal_error_sample(
            w, None, "error", "synthetic capacity", {"cli_raw": RAW}))
        records = _records(path)
        assert len(records) == 1
        row = records[0]
        assert row["kind"] == "terminal_error" and row["terminal"] is True
        assert row["panStatus"] == "error" and row["cancelled"] is False
        assert row["errorText"] == "synthetic capacity"
        assert row["cli"]["raw"]["threadId"] == "one"
        assert row["cli"]["raw"]["thread_id"] == "two"
        assert "runtimeIssue" not in row
        assert observed == []
        assert w.status == "running"
    finally:
        _cleanup(previous, path)


def test_transient_identity_conflict_keeps_original_jsonl_and_status(tmp_path):
    path, previous, observed = _prepare(tmp_path)
    w = _worker()
    event = {"type": "codex.turn_error", **RAW}
    try:
        asyncio.run(worker._record_transient_error_sample(
            w, event, "transient_provider_error"))
        records = _records(path)
        assert len(records) == 1
        row = records[0]
        assert row["kind"] == "transient_provider_error" and row["terminal"] is False
        assert row["errorText"] == "synthetic capacity"
        assert row["cli"]["raw"]["threadId"] == "one"
        assert "runtimeIssue" not in row
        assert observed == []
        assert w.status == "running"
    finally:
        _cleanup(previous, path)
