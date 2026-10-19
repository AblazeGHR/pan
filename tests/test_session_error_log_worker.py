"""Worker entry-point coverage for the session error sample log."""

from __future__ import annotations

import asyncio
import json
import tempfile
from types import SimpleNamespace

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
    asyncio.run(worker._record_terminal_error_sample(w, s, "done", "ok", None))
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
    asyncio.run(worker._record_terminal_error_sample(
        w, s, "error", "partial answer",
        {
            "origin": "worker.stream.result",
            "cli_raw": {
                "type": "result", "is_error": True, "result": "partial answer",
                "error": raw_error, "error_source": "codex_error_payload",
            },
        },
    ))
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
    asyncio.run(worker._record_transient_error_sample(
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
    ))
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


def test_oversized_stdout_line_keeps_full_error_without_changing_cap():
    giant = "错" * 120000  # > 256 KiB after UTF-8 encoding
    event = {
        "type": "result", "is_error": True, "result": "partial",
        "error": {"message": giant, "code": "rate_limit", "retryable": True},
    }
    wire = (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
    assert len(wire) > 256 * 1024

    async def collect():
        stream = asyncio.StreamReader()
        stream.feed_data(wire)
        stream.feed_eof()
        probe = SimpleNamespace(process=SimpleNamespace(stdout=stream),
                                worker_id="oversize-probe")
        lines = []
        async for line in worker._iter_stdout_lines(probe):
            spill = getattr(line, "spill", None)
            prefix = bytes(line)[:worker._STDOUT_MAX_LINE]
            lines.append((line, prefix, spill.read() if spill is not None else b""))
        return lines

    lines = asyncio.run(collect())
    assert len(lines) == 1
    line, prefix, overflow = lines[0]
    assert len(prefix) == 256 * 1024
    assert line.endswith(b" ... [tool result truncated]")
    full = prefix + overflow
    assert json.loads(full.decode("utf-8")) == event
    assert len(json.loads(full.decode("utf-8"))["error"]["message"]) == 120000


def test_oversized_normal_result_is_not_reinterpreted_as_error():
    event = {"type": "result", "is_error": False, "result": "正" * 120000}
    wire = (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")

    async def collect():
        stream = asyncio.StreamReader()
        stream.feed_data(wire)
        stream.feed_eof()
        probe = SimpleNamespace(process=SimpleNamespace(stdout=stream),
                                worker_id="oversize-normal")
        async for line in worker._iter_stdout_lines(probe):
            spill = getattr(line, "spill", None)
            parsed = worker._oversized_error_event(
                SimpleNamespace(parse_event=lambda text: json.loads(text)),
                bytes(line), spill,
            )
            return parsed

    assert asyncio.run(collect()) is None


def test_raw_stream_diagnostics_are_attached_but_not_error_text():
    s = sess.create("raw-diagnostic-unit")
    w = worker.Worker(worker_id="worker-raw", session_id=s.id,
                      adapter=_NamedAdapter(), status="error")
    worker._capture_raw_line(w, "第一行 stderr 诊断".encode("utf-8"))
    worker._capture_raw_line(w, b"[second line]")
    asyncio.run(worker._record_worker_crash(
        w, s, 7, "process exited unexpectedly", origin="test.eof"))
    worker._close_raw_capture(w)

    record = _records(session_error_log.path())[0]
    assert record["kind"] == "worker_crash"
    assert record["errorText"] == "process exited unexpectedly"
    assert record["rawDiagnostics"] == "第一行 stderr 诊断\n[second line]\n"
    assert record["rawDiagnosticLineCount"] == 2
    assert record["rawDiagnosticSource"] == "unparsed_stream_text"
    assert record["rawDiagnosticComplete"] is True


def _split_oversized(event):
    wire = (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
    prefix = wire[:worker._STDOUT_MAX_LINE]
    spill = tempfile.TemporaryFile()
    spill.write(wire[worker._STDOUT_MAX_LINE:])
    spill.seek(0)
    return wire, prefix, spill


def test_oversized_error_detection_is_key_order_independent():
    big = "错" * 120000
    true_cases = [
        # type is after 256 KiB because the huge error object comes first.
        {"error": {"message": big}, "type": "result", "is_error": True},
        # type is the second key.
        {"is_error": True, "type": "result", "error": {"message": big}},
        # huge result before type, with is_error after it.
        {"result": big, "type": "result", "is_error": True},
        # type after an unrelated huge field.
        {"junk": big, "type": "result", "is_error": True},
        # non-result error event with type after the payload.
        {"error": {"message": big}, "type": "error"},
    ]
    for event in true_cases:
        _, prefix, spill = _split_oversized(event)
        try:
            assert worker._classify_oversized_json(prefix, spill) is True, event.keys()
        finally:
            spill.close()

    false_cases = [
        {"result": big, "type": "result", "is_error": False},
        {"type": "assistant", "message": {"content": [{"type": "result", "text": big}]}},
        {"message": {"content": [{"type": "result", "text": big}]}, "is_error": True},
        {"junk": big, "type": "assistant"},
    ]
    for event in false_cases:
        _, prefix, spill = _split_oversized(event)
        try:
            assert worker._classify_oversized_json(prefix, spill) is False, event.keys()
        finally:
            spill.close()


def test_oversized_line_classifier_reads_full_error_and_keeps_normal_capped():
    big = "错" * 120000
    error_first = {"error": {"message": big}, "type": "result", "is_error": True}
    wire, prefix, spill = _split_oversized(error_first)
    try:
        status, event = asyncio.run(worker._classify_oversized_line(
            SimpleNamespace(parse_event=lambda text: json.loads(text)), wire, spill))
    finally:
        spill.close()
    assert status == "error"
    assert event["error"]["message"] == big

    normal = {"result": big, "type": "result", "is_error": False}
    wire, prefix, spill = _split_oversized(normal)
    try:
        status, event = asyncio.run(worker._classify_oversized_line(
            SimpleNamespace(parse_event=lambda text: json.loads(text)), wire, spill))
    finally:
        spill.close()
    assert status == "drop"
    assert event is None

    raw_wire = ("stderr 诊断 " + "x" * 120000 + "\n").encode("utf-8")
    prefix = raw_wire[:worker._STDOUT_MAX_LINE]
    spill = tempfile.TemporaryFile()
    spill.write(raw_wire[worker._STDOUT_MAX_LINE:])
    spill.seek(0)
    try:
        status, event = asyncio.run(worker._classify_oversized_line(
            SimpleNamespace(parse_event=lambda text: None), raw_wire, spill))
    finally:
        spill.close()
    assert status == "raw"
    assert event is None
