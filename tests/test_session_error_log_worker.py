"""Worker entry-point coverage for the session error sample log."""

from __future__ import annotations

import asyncio
import json

from packages.core import session as sess
from packages.core import session_error_log
from packages.core import worker


def _records(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_create_worker_spawn_failure_records_startup_sample(monkeypatch, tmp_path):
    s = sess.create("startup-failure-unit")
    s.adapter_config = {}
    sess.save(s)

    async def fake_spawn(session_id, adapter, extra_args=None):
        return "无法启动 CBC：缺少启动入口"

    async def fake_save(_session):
        return None

    monkeypatch.setattr(worker, "_spawn_process", fake_spawn)
    monkeypatch.setattr(sess, "save_async", fake_save)

    result = asyncio.run(worker.create_worker(s.id))
    assert isinstance(result, str)

    rows = _records(session_error_log.path())
    assert len(rows) == 1
    record = rows[0]
    assert record["kind"] == "worker_start_failure"
    assert record["terminal"] is True
    assert record["panStatus"] == "error"
    assert record["sessionId"] == s.id
    assert record["sessionName"] == "startup-failure-unit"
    assert record["errorText"] == result
    assert record["errorTextSource"] == "pan.runtime"
    assert record["pan"]["origin"] == "worker.create"
    assert record["previousMessage"] is None
    assert record["previousMessageMissing"] is True
    assert record["workerId"]


def test_normal_terminal_does_not_record(monkeypatch):
    """A done terminal is never an error sample (guards against over-recording)."""
    s = sess.create("normal-unit")
    s.adapter_config = {}
    sess.save(s)

    async def fake_spawn(session_id, adapter, extra_args=None):
        raise AssertionError("not used")

    monkeypatch.setattr(worker, "_spawn_process", fake_spawn)

    # Directly exercise the classifier boundary used by the terminal hook.
    w = worker.Worker(worker_id="worker-x", session_id=s.id,
                      adapter=object(), status="idle")
    worker._record_terminal_error_sample(w, s, "done", "ok", None)
    assert _records(session_error_log.path()) == []


class _NamedAdapter:
    name = "codex"


def test_codex_result_error_preserves_raw_payload_and_never_impersonates_assistant():
    s = sess.create("codex-raw-unit")
    sess.append_history(s, {"role": "assistant", "content": "partial answer"})
    w = worker.Worker(worker_id="worker-codex", session_id=s.id,
                      adapter=_NamedAdapter(), status="running", generation=2)
    w._current_seq = 3
    w._current_task_last_assistant_text = "partial answer"
    raw_error = {
        "message": "stream disconnected",
        "code": "rate_limit",
        "codexErrorInfo": {"responseStreamDisconnected": {"httpStatusCode": 429}},
    }
    worker._record_terminal_error_sample(
        w, s, "error", "partial answer",
        {
            "origin": "worker.stream.result",
            "cli_raw": {
                "type": "result", "is_error": True, "result": "partial answer",
                "error": raw_error, "error_source": "codex_error_payload",
            },
        },
    )
    record = _records(session_error_log.path())[0]
    assert record["errorText"] == "stream disconnected"
    assert record["errorTextSource"] == "codex_error_payload"
    assert record["cli"]["error"] == raw_error
    assert record["pan"]["terminalResult"] == "partial answer"
    assert record["pan"]["terminalResultMayBeAssistantText"] is True
    assert record["previousMessage"]["content"] == "partial answer"
    assert record["taskSeq"] == 3
    assert record["generation"] == 2
    assert record["kind"] == "terminal_error"


def test_tool_item_failure_is_non_terminal():
    s = sess.create("tool-failure-unit")
    w = worker.Worker(worker_id="worker-tool", session_id=s.id,
                      adapter=_NamedAdapter(), status="running")
    worker._record_transient_error_sample(
        w,
        {
            "type": "codex.item_error",
            "item_type": "commandexecution",
            "item_id": "item-1",
            "item_status": "failed",
            "item": {"type": "commandExecution", "id": "item-1",
                     "error": "tool failed normally"},
            "error": {"message": "tool failed normally"},
        },
        "tool_failure",
    )
    record = _records(session_error_log.path())[0]
    assert record["kind"] == "tool_failure"
    assert record["terminal"] is False
    assert record["panStatus"] is None
    assert record["errorText"] == "tool failed normally"


def test_diagnostics_expose_sample_log_counters():
    diagnostics = sess.save_diagnostics()
    assert "sessionErrorLog" in diagnostics
    assert diagnostics["sessionErrorLog"]["path"].endswith("session-errors.jsonl")
    assert "failures" in diagnostics["sessionErrorLog"]
