"""Real isolated HTTP/process coverage for the session error sample log.

Uses an ephemeral port (never 8767/8768), an owned temporary data root and a
deterministic local CLI.  No remote model or production data is involved.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

import tests.test_real_queue_http_e2e as base
from packages.jobs.window import command_window_options


FAKE_CLI = Path(__file__).resolve().parent / "support" / "session_error_fake_cli.py"


def _free_port() -> int:
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
        if port not in {8767, 8768}:
            return port


def _run_fake_cli(text: str) -> bytes:
    """Run the fixture with a deliberately non-UTF8 Windows text default."""
    payload = json.dumps({
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }, ensure_ascii=False) + "\n"
    env = dict(os.environ)
    env.update({"PYTHONUTF8": "0", "PYTHONIOENCODING": "cp936"})
    completed = subprocess.run(
        [sys.executable, str(FAKE_CLI), "-p", "--output-format", "stream-json",
         "--input-format", "stream-json", "-y"],
        input=payload.encode("utf-8"), capture_output=True, env=env,
        timeout=60, **command_window_options(),
    )
    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
    return completed.stdout


def test_fixture_uses_explicit_utf8_under_non_utf8_windows_pipe():
    """The fixture must be the UTF-8 contract, not rely on PYTHONUTF8.

    The child runs with PYTHONUTF8=0 and PYTHONIOENCODING=cp936.  Pan always
    decodes provider stdout as UTF-8, so the fixture's bytes must remain UTF-8.
    """
    done_stdout = _run_fake_cli("中文任务")
    done_text = done_stdout.decode("utf-8")  # strict: no replacement chars
    done_events = [json.loads(line) for line in done_text.splitlines() if line.strip()]
    result = [event for event in done_events if event.get("type") == "result"][-1]
    assert result["is_error"] is False
    assert result["result"] == "中文任务"

    error_stdout = _run_fake_cli("err-terminal")
    error_text = error_stdout.decode("utf-8")
    error_events = [json.loads(line) for line in error_text.splitlines() if line.strip()]
    error = [event for event in error_events if event.get("type") == "result"][-1]
    assert error["is_error"] is True
    assert error["error"]["message"] == "上游 429：请稍后重试\n第二行：完整错误正文"
    assert "上游".encode("utf-8") in error_stdout


def _make_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(base, "FAKE_CLI", FAKE_CLI)
    monkeypatch.setattr(base, "PORT", _free_port())
    return base._PanRuntime(tmp_path / "data-root")


@pytest.fixture(scope="module")
def runtime(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    rt = _make_runtime(tmp_path_factory.mktemp("session-error-e2e"), mp)
    assert base.PORT not in {8767, 8768}
    rt.start()
    try:
        yield rt
    finally:
        rt.stop()
        mp.undo()


def _log_path(runtime):
    return runtime.data_root / "logs" / "session-errors.jsonl"


def _records(runtime):
    path = _log_path(runtime)
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _wait_matching(runtime, predicate, *, minimum=1, timeout=30):
    def check():
        rows = [record for record in _records(runtime) if predicate(record)]
        return rows if len(rows) >= minimum else None
    return base._wait_for(check, timeout=timeout, label="error sample")


def _new_session(runtime, name, mode="stream"):
    response = runtime.request("POST", "/api/sessions", json={
        "name": name,
        "adapter": "cbc",
        "permissionMode": "bypassPermissions",
        "outputMode": mode,
    })
    assert "error" not in response, response
    return response["id"]


def _spawn(runtime, session_id):
    return runtime.request("POST", "/api/spawn", json={"sessionId": session_id})


def _enqueue(runtime, session_id, text, client_id=None):
    return runtime.request(
        "POST", f"/api/sessions/{session_id}/queue",
        json={"text": text, "clientMessageId": client_id or f"cm-{time.time_ns()}"},
    )


def _wait_last_result(runtime, session_id, status, *, timeout=30):
    def check():
        data = runtime.request("GET", f"/api/sessions/{session_id}")
        last = data.get("lastResult") or {}
        return last if last.get("status") == status else None
    return base._wait_for(check, timeout=timeout, label=f"lastResult={status}")


def _wait_done(runtime, session_id, timeout=30):
    return _wait_last_result(runtime, session_id, "done", timeout=timeout)


def test_stream_terminal_tool_transient_native_cancelled_and_duplicate(runtime):
    sid = _new_session(runtime, "stream-errors")
    _spawn(runtime, sid)

    # A normal turn must not create any sample.
    _enqueue(runtime, sid, "err-normal")
    _wait_done(runtime, sid)
    assert _records(runtime) == []

    before = len(_records(runtime))
    _enqueue(runtime, sid, "err-terminal")
    terminal = _wait_matching(
        runtime,
        lambda r: r.get("sessionId") == sid and r.get("kind") == "terminal_error",
        minimum=before + 1,
    )[-1]
    assert terminal["errorText"] == "上游 429：请稍后重试\n第二行：完整错误正文"
    assert terminal["errorTextSource"] == "cli.error"
    assert terminal["pan"]["status"] == "error"
    assert terminal["pan"]["terminalResult"] == "partial answer before failure"
    assert terminal["pan"]["terminalResultMayBeAssistantText"] is True
    assert terminal["cli"]["raw"]["error"]["code"] == "rate_limit_exceeded"
    assert terminal["cli"]["raw"]["error"]["status"] == 429
    assert terminal["cli"]["raw"]["error"]["retryable"] is True
    assert terminal["cli"]["raw"]["usage"] == {"input_tokens": 1, "output_tokens": 2}
    assert terminal["sessionName"] == "stream-errors"
    assert terminal["adapter"] == "cbc"
    assert terminal["model"] == "deepseek-v4-flash"
    assert isinstance(terminal["workerId"], str) and terminal["workerId"]
    assert isinstance(terminal["generation"], int)
    assert isinstance(terminal["taskSeq"], int)
    previous = terminal["previousMessage"]
    assert previous["role"] == "assistant"
    assert previous["content"] == "partial answer before failure"
    assert previous["messageId"]

    # A tool row is the last message before the failure; not the older user row.
    _wait_last_result(runtime, sid, "error")
    _enqueue(runtime, sid, "err-tool")
    tool_record = _wait_matching(
        runtime,
        lambda r: (r.get("sessionId") == sid and r.get("kind") == "terminal_error"
                   and r.get("errorText") == "tool failed, task failed"),
    )[-1]
    assert tool_record["previousMessage"]["role"] == "tool"
    assert "Command(" in tool_record["previousMessage"]["content"]

    # Transient reconnect error: recorded, non-terminal, task still succeeds.
    _enqueue(runtime, sid, "err-transient")
    _wait_done(runtime, sid)
    transient = _wait_matching(
        runtime,
        lambda r: r.get("sessionId") == sid and r.get("kind") == "transient_provider_error",
    )[-1]
    assert transient["terminal"] is False
    assert "Reconnecting... 2/5" in transient["cli"]["raw"]["error"]["message"]
    assert transient["cli"]["raw"]["error"]["codexErrorInfo"]["responseStreamDisconnected"]
    # The transient event did not become a terminal error.
    assert not any(
        record.get("sessionId") == sid and record.get("kind") == "terminal_error"
        and "Reconnecting" in (record.get("errorText") or "")
        for record in _records(runtime)
    )

    # Native operation error keeps the code that the Worker field flattens.
    _enqueue(runtime, sid, "err-native")
    _wait_done(runtime, sid)
    native = _wait_matching(
        runtime,
        lambda r: r.get("sessionId") == sid and r.get("kind") == "native_operation_error",
    )[-1]
    assert native["terminal"] is False
    assert native["cli"]["raw"]["error"] == "native-client-disconnected"
    assert native["pan"]["nativeErrorCode"] == "native-client-disconnected"

    # A failed tool item is a tool_failure sample, not a task failure.
    _enqueue(runtime, sid, "err-item")
    _wait_done(runtime, sid)
    tool_failure = _wait_matching(
        runtime,
        lambda r: r.get("sessionId") == sid and r.get("kind") == "tool_failure",
    )[-1]
    assert tool_failure["terminal"] is False
    assert tool_failure["panStatus"] is None
    assert tool_failure["cli"]["raw"]["item_status"] == "failed"
    assert tool_failure["errorText"] == "tool failed normally"

    # A recovered reconnect notice is non-terminal but explicitly recovered.
    _enqueue(runtime, sid, "err-recovered")
    _wait_done(runtime, sid)
    recovered = _wait_matching(
        runtime,
        lambda r: (r.get("sessionId") == sid
                   and r.get("kind") == "transient_provider_error"
                   and r.get("recovered") is True),
    )[-1]
    assert recovered["terminal"] is False
    assert recovered["cli"]["raw"]["turn_status"] == "completed"

    # Non-JSON stream text is raw diagnostic context, never the error text.
    _enqueue(runtime, sid, "err-rawdiag")
    _wait_last_result(runtime, sid, "error")
    rawdiag = _wait_matching(
        runtime,
        lambda r: (r.get("sessionId") == sid
                   and r.get("kind") == "terminal_error"
                   and r.get("errorText") == "raw diagnostic scenario failed"),
    )[-1]
    assert rawdiag["rawDiagnostics"].replace("\r\n", "\n") == (
        "第一行 stderr 诊断：磁盘警告\n[second raw line] 重连日志\n")
    assert rawdiag["rawDiagnosticLineCount"] == 2
    assert rawdiag["rawDiagnosticSource"] == "unparsed_stream_text"
    assert "第一行 stderr" not in rawdiag["errorText"]
    assert not any(
        record.get("sessionId") == sid
        and "第一行 stderr" in (record.get("errorText") or "")
        for record in _records(runtime)
    )

    # Cancellation is layered as cancelled, not as an error.
    _enqueue(runtime, sid, "err-cancelled")
    _wait_last_result(runtime, sid, "cancelled")
    cancelled = _wait_matching(
        runtime,
        lambda r: r.get("sessionId") == sid and r.get("kind") == "cancelled",
    )[-1]
    assert cancelled["terminal"] is True
    assert cancelled["cancelled"] is True
    assert cancelled["panStatus"] == "cancelled"

    # Duplicate provider terminal frames produce one sample.
    _enqueue(runtime, sid, "err-duplicate")
    _wait_last_result(runtime, sid, "error")
    duplicate_rows = _wait_matching(
        runtime,
        lambda r: (r.get("sessionId") == sid and r.get("kind") == "terminal_error"
                   and r.get("errorText") == "duplicate error"),
    )
    time.sleep(0.2)
    duplicate_rows = [
        r for r in _records(runtime)
        if r.get("sessionId") == sid and r.get("errorText") == "duplicate error"
    ]
    assert len(duplicate_rows) == 1


def test_oversized_error_detection_is_key_order_independent(runtime):
    cases = [
        ("error-first", "err-oversize-error-first", "partial before error-first"),
        ("is-error-first", "err-oversize-is-error-first",
         "partial before is_error-first"),
    ]
    for label, prompt, terminal_result in cases:
        sid = _new_session(runtime, f"oversize-{label}")
        _spawn(runtime, sid)
        _enqueue(runtime, sid, prompt)
        _wait_last_result(runtime, sid, "error")
        record = _wait_matching(
            runtime,
            lambda r, sid=sid: (r.get("sessionId") == sid
                                and r.get("kind") == "terminal_error"),
        )[-1]
        assert len(record["errorText"]) == 120000, label
        assert record["errorText"] == "错" * 120000, label
        assert record["errorTextSource"] == "cli.error", label
        assert record["cli"]["raw"]["error"]["message"] == "错" * 120000, label
        assert record["pan"]["terminalResult"] == terminal_result, label
        # The provider stream is still usable after the oversized error.
        _enqueue(runtime, sid, "err-normal")
        _wait_last_result(runtime, sid, "done")


def test_crash_huge_and_concurrent_sessions(runtime):
    crash_sid = _new_session(runtime, "crash-error")
    _spawn(runtime, crash_sid)
    _enqueue(runtime, crash_sid, "err-crash")
    crash = _wait_matching(
        runtime,
        lambda r: r.get("sessionId") == crash_sid and r.get("kind") == "worker_crash",
    )[-1]
    assert crash["terminal"] is True
    assert crash["pan"]["returncode"] == 7
    assert "returncode=7" in crash["errorText"]
    assert crash["previousMessage"]["role"] == "assistant"
    assert crash["previousMessage"]["content"] == "about to crash"

    huge_sid = _new_session(runtime, "huge-error")
    _spawn(runtime, huge_sid)
    _enqueue(runtime, huge_sid, "err-huge")
    _wait_last_result(runtime, huge_sid, "error")
    huge = _wait_matching(
        runtime,
        lambda r: r.get("sessionId") == huge_sid and r.get("kind") == "terminal_error",
    )[-1]
    assert len(huge["errorText"]) > 40000
    assert "超长错误负载" in huge["errorText"]
    assert huge["errorText"].endswith("尾部")
    assert huge["errorText"] != "partial answer before huge error"
    assert huge["pan"]["terminalResult"] == "partial answer before huge error"

    # A single structured error line above the 256 KiB parser cap is read back
    # from the spill file in full, and the stream keeps working afterwards.
    oversize_sid = _new_session(runtime, "oversize-error")
    _spawn(runtime, oversize_sid)
    _enqueue(runtime, oversize_sid, "err-oversize")
    _wait_last_result(runtime, oversize_sid, "error")
    oversize = _wait_matching(
        runtime,
        lambda r: (r.get("sessionId") == oversize_sid
                   and r.get("kind") == "terminal_error"),
    )[-1]
    assert len(oversize["errorText"]) == 120000
    assert oversize["errorText"] == "错" * 120000
    assert len(oversize["cli"]["raw"]["error"]["message"]) == 120000
    assert oversize["errorTextSource"] == "cli.error"
    assert oversize["pan"]["terminalResult"] == "partial answer before oversize error"
    _enqueue(runtime, oversize_sid, "err-normal")
    _wait_last_result(runtime, oversize_sid, "done")

    one = _new_session(runtime, "concurrent-one")
    two = _new_session(runtime, "concurrent-two")
    _spawn(runtime, one)
    _spawn(runtime, two)
    _enqueue(runtime, one, "err-terminal concurrent-one")
    _enqueue(runtime, two, "err-terminal concurrent-two")
    rows = _wait_matching(
        runtime,
        lambda r: (r.get("sessionId") in {one, two}
                   and r.get("kind") == "terminal_error"),
        minimum=2,
    )
    by_session = {record["sessionId"]: record for record in rows}
    assert set(by_session) == {one, two}
    for record in by_session.values():
        assert record["previousMessage"]["role"] == "assistant"
        assert record["previousMessage"]["content"] == "partial answer before failure"
        assert record["sessionId"] in (one, two)
    raw_lines = _log_path(runtime).read_text(encoding="utf-8").splitlines()
    assert len(raw_lines) == len(_records(runtime))


@pytest.mark.skip(reason="Oneshot mode retired by user; implementation retained")
def test_oneshot_structured_error(runtime):
    sid = _new_session(runtime, "oneshot-error", mode="oneshot")
    _spawn(runtime, sid)
    _enqueue(runtime, sid, "oneshot-error")
    _wait_last_result(runtime, sid, "error")
    record = _wait_matching(
        runtime,
        lambda r: r.get("sessionId") == sid and r.get("kind") == "terminal_error",
    )[-1]
    assert record["errorText"] == "oneshot structured failure\n第二行"
    assert record["errorTextSource"] == "cli.error"
    assert record["cli"]["error"]["code"] == "resume_not_found"
    assert record["cli"]["error"]["status"] == 404
    assert record["cli"]["error"]["retryable"] is False
    assert record["cli"]["errorEventCount"] == 1
    assert record["cli"]["origin"] == "worker.oneshot"
    assert record["previousMessage"]["role"] == "user"
    assert "oneshot-error" in record["previousMessage"]["content"]


def test_restart_keeps_existing_samples_and_appends(tmp_path, monkeypatch):
    runtime = _make_runtime(tmp_path / "restart", monkeypatch)
    runtime.start()
    try:
        first_sid = _new_session(runtime, "before-restart")
        _spawn(runtime, first_sid)
        _enqueue(runtime, first_sid, "err-terminal")
        _wait_last_result(runtime, first_sid, "error")
        first = _wait_matching(
            runtime,
            lambda r: r.get("sessionId") == first_sid and r.get("kind") == "terminal_error",
        )[-1]
        first_id = first["eventId"]
        first_bytes = _log_path(runtime).read_bytes()
    finally:
        runtime.stop()

    runtime.start()
    try:
        assert _log_path(runtime).read_bytes().startswith(first_bytes)
        second_sid = _new_session(runtime, "after-restart")
        _spawn(runtime, second_sid)
        _enqueue(runtime, second_sid, "err-terminal")
        _wait_last_result(runtime, second_sid, "error")
        _wait_matching(
            runtime,
            lambda r: r.get("sessionId") == second_sid and r.get("kind") == "terminal_error",
        )
        rows = _records(runtime)
        assert [r["eventId"] for r in rows].count(first_id) == 1
        assert any(r.get("sessionId") == second_sid for r in rows)
    finally:
        runtime.stop()
