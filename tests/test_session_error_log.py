"""Unit coverage for the append-only session error sample log."""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from packages.core import session_error_log


class _FakeSession:
    def __init__(self, history=None, *, session_id="ses_unit", name="Unit Session",
                 adapter="cbc", model="model-a"):
        self.id = session_id
        self.name = name
        self.adapter = adapter
        self.model = model
        self.history = list(history or [])
        self.history_epoch = "epoch-1"
        self.history_revision = len(self.history)
        self._summary_lock = threading.RLock()


@pytest.fixture(autouse=True)
def _isolated_log(tmp_path):
    session_error_log.reset_state()
    session_error_log.configure(tmp_path / "logs" / "session-errors.jsonl")
    yield
    session_error_log.reset_state()


def _read_records(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _record(**overrides):
    payload = {
        "kind": "terminal_error",
        "error_text": "boom",
        "error_text_source": "pan.runtime",
        "terminal": True,
        "session_id": "ses_unit",
        "session_name": "Unit Session",
        "adapter": "cbc",
        "model": "model-a",
        "worker_id": "w_1",
        "generation": 1,
        "task_seq": 7,
    }
    payload.update(overrides)
    return session_error_log.record_session_error(**payload)


def test_schema_and_full_chinese_multiline_text(tmp_path):
    error_text = ("第一行：上游返回 429\n第二行：请稍后重试\n" + ("中文负载" * 20000))
    session = _FakeSession(history=[
        {"role": "user", "content": "请执行任务", "messageId": "pan:user"},
    ])
    event_id = _record(
        session=session,
        error_text=error_text,
        error_text_source="pan.runtime",
        cli_raw={
            "type": "result", "is_error": True, "result": "被截断的助手文本",
            "error": {"message": "上游错误", "code": "rate_limit",
                      "status": 429, "retryable": True},
        },
    )
    assert event_id
    records = _read_records(tmp_path / "logs" / "session-errors.jsonl")
    assert len(records) == 1
    record = records[0]
    assert record["schemaVersion"] == 1
    assert record["eventId"] == event_id
    assert record["sessionId"] == "ses_unit"
    assert record["sessionName"] == "Unit Session"
    assert record["name"] == "Unit Session"
    assert record["adapter"] == "cbc"
    assert record["model"] == "model-a"
    assert record["workerId"] == "w_1"
    assert record["generation"] == 1
    assert record["taskSeq"] == 7
    assert record["errorText"] == "上游错误"
    assert record["errorTextSource"] == "cli.error"
    assert record["cli"]["raw"]["error"]["retryable"] is True
    assert record["cli"]["raw"]["error"]["status"] == 429
    assert record["cli"]["error"]["code"] == "rate_limit"
    assert record["pan"].get("status") is None
    assert record["previousMessage"]["role"] == "user"
    assert record["previousMessage"]["messageId"] == "pan:user"
    assert record["previousMessage"]["content"] == "请执行任务"
    assert record["terminal"] is True


def test_full_text_is_not_truncated(tmp_path):
    text = "错" * 200000 + "\n尾部"
    assert _record(error_text=text)
    record = _read_records(tmp_path / "logs" / "session-errors.jsonl")[0]
    assert record["errorText"] == text
    assert len(record["errorText"]) == len(text)


def test_previous_message_is_last_row_not_last_user_and_survives_concurrent_append(tmp_path):
    session = _FakeSession(history=[
        {"role": "user", "content": "older user", "messageId": "pan:u1"},
        {"role": "assistant", "content": "answer", "messageId": "pan:a1"},
        {"role": "tool", "content": "Command(...) failed normally",
         "messageId": "pan:t1", "source": "native-tui"},
    ])
    previous = session_error_log.previous_message_snapshot(session)
    assert previous["role"] == "tool"
    assert previous["historyIndex"] == 2
    assert previous["historyEpoch"] == "epoch-1"

    # A later concurrent message must not replace the captured preceding row.
    session.history.append(
        {"role": "user", "content": "later user", "messageId": "pan:u2"})
    assert _record(session=session, previous_message=previous,
                   previous_captured=True)
    record = _read_records(tmp_path / "logs" / "session-errors.jsonl")[0]
    assert record["previousMessage"]["role"] == "tool"
    assert record["previousMessage"]["content"] == "Command(...) failed normally"
    assert record["previousMessage"]["source"] == "native-tui"
    assert record["previousMessage"]["raw"]["messageId"] == "pan:t1"


def test_cli_raw_payload_is_kept_separate_from_pan_fields(tmp_path):
    _record(
        kind="terminal_error",
        error_text="Pan 终端结果",
        error_text_source="pan.terminal_result",
        pan_status="error",
        cli_raw={
            "type": "error", "error": {
                "type": "rate_limit", "code": "429", "status": 429,
                "retryable": True, "message": "真实 CLI 错误",
            },
        },
    )
    record = _read_records(tmp_path / "logs" / "session-errors.jsonl")[0]
    assert record["errorText"] == "真实 CLI 错误"
    assert record["errorTextSource"] == "cli.error"
    assert record["panStatus"] == "error"
    assert record["cli"]["eventType"] == "error"
    assert record["cli"]["error"] == {
        "type": "rate_limit", "code": "429", "status": 429,
        "retryable": True, "message": "真实 CLI 错误",
    }
    # No fabricated retryable/code at the top level.
    assert "retryable" not in record
    assert "code" not in record


def test_terminal_duplicate_is_written_once(tmp_path):
    first = _record()
    second = _record()
    assert first == second
    assert len(_read_records(tmp_path / "logs" / "session-errors.jsonl")) == 1
    assert session_error_log.stats()["duplicates"] == 1


def test_out_of_order_distinct_transient_events_are_all_kept(tmp_path):
    for token in ("first", "second", "third"):
        _record(
            kind="transient_provider_error",
            terminal=False,
            dedupe_token=token,
            cli_raw={"type": "error", "error": {"message": token}},
            error_text=token,
            error_text_source="cli.error",
        )
    records = _read_records(tmp_path / "logs" / "session-errors.jsonl")
    assert [record["cli"]["error"]["message"] for record in records] == [
        "first", "second", "third",
    ]
    assert len({record["eventId"] for record in records}) == 3


def test_concurrent_appends_stay_valid_json_lines(tmp_path):
    def worker(index):
        return _record(
            kind="transient_provider_error",
            terminal=False,
            dedupe_token=f"thread-{index}",
            error_text=f"错误 {index}",
            error_text_source="pan.runtime",
        )

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(worker, range(120)))
    assert all(results)
    path = tmp_path / "logs" / "session-errors.jsonl"
    raw = path.read_text(encoding="utf-8")
    assert raw.endswith("\n")
    records = [json.loads(line) for line in raw.splitlines()]
    assert len(records) == 120
    assert len({record["eventId"] for record in records}) == 120


def test_restart_keeps_existing_samples_and_appends(tmp_path):
    _record(error_text="重启前")
    session_error_log.reset_state()  # process restart: caches/counters are gone
    session_error_log.configure(tmp_path / "logs" / "session-errors.jsonl")
    _record(error_text="重启后", dedupe_token="after-restart")
    records = _read_records(tmp_path / "logs" / "session-errors.jsonl")
    assert [record["errorText"] for record in records] == ["重启前", "重启后"]


def test_write_failure_is_observable_and_never_raises(tmp_path, monkeypatch, caplog):
    real_write = os.write

    def broken_write(fd, data):
        raise OSError("disk full")

    monkeypatch.setattr(os, "write", broken_write)
    with caplog.at_level("WARNING", logger="packages.core.session_error_log"):
        assert _record() is None
    assert session_error_log.stats()["failures"] == 1
    assert session_error_log.stats()["lastErrorType"] == "OSError"
    assert any("session-error-log: append failed" in msg for msg in caplog.messages)

    # A failed event is not remembered, so a later retry can still persist it.
    monkeypatch.setattr(os, "write", real_write)
    assert _record() is not None
    assert len(_read_records(tmp_path / "logs" / "session-errors.jsonl")) == 1


def test_partial_trailing_line_is_repaired(tmp_path):
    path = tmp_path / "logs" / "session-errors.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text('{"broken":', encoding="utf-8")
    assert _record(error_text="after crash")
    raw = path.read_text(encoding="utf-8")
    assert '{"broken":\n' in raw
    records = [json.loads(line) for line in raw.splitlines()[1:]]
    assert records[-1]["errorText"] == "after crash"


def test_credentials_are_redacted_but_diagnostic_text_kept(tmp_path):
    _record(
        cli_raw={
            "type": "result",
            "error": {
                "message": "request failed: Authorization: Bearer abc123secret\n"
                           "api_key=sk-abcdefghijklmnopqrstuvwxyz",
                "api_key": "sk-live-secret",
                "headers": {"Cookie": "session=topsecret"},
            },
            "argv": ["--token", "should-not-be-collected"],
        },
    )
    record = _read_records(tmp_path / "logs" / "session-errors.jsonl")[0]
    text = record["errorText"]
    assert "abc123secret" not in text
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in text
    assert "request failed" in text
    assert record["cli"]["error"]["api_key"] == "***redacted***"
    assert record["cli"]["error"]["headers"]["Cookie"] == "***redacted***"
    # argv is copied only because the caller passed it; this module never
    # collects process argv or environment on its own.
    assert record["cli"]["raw"]["argv"] == ["--token", "should-not-be-collected"]


def test_missing_previous_message_is_explicit(tmp_path):
    _record(previous_message=None, previous_captured=True)
    record = _read_records(tmp_path / "logs" / "session-errors.jsonl")[0]
    assert record["previousMessage"] is None
    assert record["previousMessageMissing"] is True


def test_cancelled_and_tool_failure_are_not_terminal_errors(tmp_path):
    _record(kind="cancelled", terminal=True, cancelled=True,
            pan_status="cancelled", error_text="", error_text_source="pan.terminal_result")
    _record(kind="tool_failure", terminal=False, dedupe_token="tool-1",
            error_text="", error_text_source="cli.item_status")
    records = _read_records(tmp_path / "logs" / "session-errors.jsonl")
    assert records[0]["kind"] == "cancelled"
    assert records[0]["cancelled"] is True
    assert records[1]["kind"] == "tool_failure"
    assert records[1]["terminal"] is False
    assert records[1]["panStatus"] is None


def test_lock_failure_drops_sample_and_never_writes_unlocked(tmp_path, monkeypatch, caplog):
    class _FailedLock:
        acquired = False

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(
        session_error_log, "_cross_process_lock", lambda _path: _FailedLock())
    with caplog.at_level("WARNING", logger="packages.core.session_error_log"):
        assert _record(error_text="must not be written") is None
    assert not (tmp_path / "logs" / "session-errors.jsonl").exists()
    stats = session_error_log.stats()
    assert stats["lockFailures"] == 1
    assert stats["failures"] == 1
    assert any("sample lost" in message for message in caplog.messages)


def test_async_write_does_not_block_event_loop(monkeypatch):
    real_write = session_error_log._write_prepared
    calls = []

    def slow_write(prepared):
        calls.append(prepared[1])
        time.sleep(0.4)
        return real_write(prepared)

    monkeypatch.setattr(session_error_log, "_write_prepared", slow_write)

    async def run():
        ticks = 0

        async def heartbeat():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        task = asyncio.create_task(heartbeat())
        event_id = await session_error_log.record_session_error_async(
            kind="terminal_error",
            error_text="async error",
            error_text_source="pan.runtime",
            terminal=True,
            session_id="ses_async",
            worker_id="worker-async",
        )
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return event_id, ticks

    event_id, ticks = asyncio.run(run())
    assert event_id
    assert calls == [event_id]
    assert ticks >= 20, f"event loop was blocked (ticks={ticks})"
    records = _read_records(session_error_log.path())
    assert records[0]["errorText"] == "async error"


def test_async_submission_sequence_matches_file_order():
    async def run():
        await asyncio.gather(*[
            session_error_log.record_session_error_async(
                kind="transient_provider_error",
                terminal=False,
                error_text=f"e{index}",
                error_text_source="pan.runtime",
                session_id="ses_seq",
                worker_id="worker-seq",
                dedupe_token=f"seq-{index}",
            )
            for index in range(20)
        ])

    asyncio.run(run())
    records = _read_records(session_error_log.path())
    assert len(records) == 20
    assert [record["processSequence"] for record in records] == list(range(20))
    assert [record["errorText"] for record in records] == [
        f"e{index}" for index in range(20)
    ]


def test_flush_waits_for_inflight_write(monkeypatch):
    real_write = session_error_log._write_prepared
    started = threading.Event()

    def slow_write(prepared):
        started.set()
        time.sleep(0.2)
        return real_write(prepared)

    monkeypatch.setattr(session_error_log, "_write_prepared", slow_write)

    async def run():
        task = asyncio.create_task(session_error_log.record_session_error_async(
            kind="terminal_error",
            error_text="flush me",
            error_text_source="pan.runtime",
            terminal=True,
            session_id="ses_flush",
            worker_id="worker-flush",
        ))
        await asyncio.sleep(0.01)
        for _ in range(100):
            if started.is_set():
                break
            await asyncio.sleep(0.01)
        assert started.is_set()
        assert session_error_log.flush()
        assert await task

    asyncio.run(run())
    assert _read_records(session_error_log.path())[0]["errorText"] == "flush me"
