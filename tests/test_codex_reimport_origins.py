"""Private rollout/Session fixtures; no live service or native user writes."""
import asyncio
import copy
import json
import threading
import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from packages.core import session as sess
from packages.core import worker
from packages.core.adapters.codex import sessions as codex
from packages.core.attachment_projection import project_message_parts
from packages.web import server


def origin(tmp_path, *, source="user", text="before\n", attachment=True):
    parts = [{"type": "text", "text": text}]
    if attachment:
        file = tmp_path / 'attachment.png'
        file.write_bytes(b"private attachment")
        parts.extend([{"type": "attachment", "attachmentId": "fixture",
                       "displayName": "attachment.png", "__serverPath": str(file.resolve())},
                      {"type": "text", "text": "\nafter"}])
    item = {"type": "task", "kind": "task", "id": "queue-fixture",
            "source": source, "sourceSessionId": "ses_sender", "taskId": "task-fixture",
            "parts": parts, "text": text + ("[attachment.png](/api/attachments/fixture)\nafter" if attachment else "")}
    row = worker._queue_history_row([item])
    row["messageId"] = "pan:" + "1" * 32
    return row


def private_rollout(tmp_path, monkeypatch, rows):
    path = tmp_path / "rollout.jsonl"
    events = [{"type": "session_meta", "payload": {"id": "private-thread"}},
              {"type": "turn_context", "payload": {"turn_id": "turn"}}]
    for index, row in enumerate(rows):
        events.append({"type": "response_item", "payload": {
            "type": "message", "id": f"item-{index}", "role": row["role"],
            "content": [{"type": "input_text" if row["role"] == "user" else "output_text",
                         "text": row["content"]}]}})
    path.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    # Exercise the actual rollout parser behind the HTTP production handler.
    monkeypatch.setattr(codex, "parse_history", lambda *_: codex._rollout_history(path, "private-thread"))
    monkeypatch.setattr(codex, "get_raw_usage", lambda *_: [])
    monkeypatch.setattr(codex, "session_exists", lambda *_: True)
    monkeypatch.setattr(codex, "get_reimport_snapshot", lambda *_: hashlib.sha256(path.read_bytes()).hexdigest(), raising=False)
    monkeypatch.setattr(server, "_sessions_provider", lambda _: codex)
    monkeypatch.setattr(server, "broadcast", AsyncMock())
    return path


def existing(row):
    s = sess.create("private", adapter="codex", cli_session_id="private-thread", history=[row])
    s.last_result = {"status": "done", "result": "old"}
    sess.save_full(s)
    return s


async def request(expected_status=200):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app, raise_app_exceptions=False), base_url="http://test") as client:
        response = await client.post("/api/adapters/codex/sessions/import", json={"session_id": "private-thread"})
        assert response.status_code == expected_status
        return response.json() if expected_status == 200 else response.text


@pytest.mark.parametrize("source", ["user", "agent", "automation"])
@pytest.mark.parametrize("attachment", [True, False])
def test_projection_http_reload_and_idempotence(tmp_path, monkeypatch, source, attachment):
    row = origin(tmp_path, source=source, attachment=attachment)
    wire = project_message_parts(row["parts"], row["content"])
    native = [{"role": "user", "content": wire}, {"role": "assistant", "content": "reply"},
              {"role": "user", "content": "native additional input"}]
    path = private_rollout(tmp_path, monkeypatch, native)
    before = path.read_bytes()
    s = existing(row)
    if attachment:
        Path(row["parts"][1]["__serverPath"]).unlink()  # historical proof needs no live file
    expected_ids = None
    for _ in range(2):
        result = asyncio.run(request())
        assert result.get("reimported") is True, result
        assert [(r["role"], r["content"]) for r in s.history] == [(r["role"], r["content"]) for r in native]
        first = s.history[0]
        for key in ("messageId", "taskId", "source", "sourceSessionId", "queueEnvelopes", "parts", "queueItemIds", "delivered_keys"):
            assert first[key] == row[key]
        ids = [r["messageId"] for r in s.history]
        if expected_ids is not None:
            assert ids == expected_ids
        expected_ids = ids
        sess._cache.clear()
        s = sess.get(s.id)
        assert [r["messageId"] for r in s.history] == ids
    assert path.read_bytes() == before


@pytest.mark.parametrize("case", ["missing", "edited", "duplicate", "wrong-native-id", "legacy-no-path", "legacy-no-id"])
def test_unprovable_origin_retains_history(tmp_path, monkeypatch, case):
    row = origin(tmp_path)
    wire = project_message_parts(row["parts"], row["content"])
    native = [{"role": "user", "content": wire}]
    if case == "missing": native = [{"role": "assistant", "content": "reply"}]
    if case == "edited":
        row["nativeItemId"] = "item-0"
        native[0]["content"] += " edited"
    if case == "duplicate": native.append(dict(native[0]))
    if case == "wrong-native-id": row["nativeItemId"] = "different-id"
    if case == "legacy-no-path": row["parts"][1].pop("__serverPath")
    if case == "legacy-no-id":
        row.pop("messageId")
        native.append({"role": "system", "content": "not a body"})
    private_rollout(tmp_path, monkeypatch, native)
    s = existing(row)
    old = copy.deepcopy(s.history)
    disk = sess._history_path(s.id).read_bytes()
    result = asyncio.run(request())
    assert result.get("ok") is False
    assert "losslessly" in result["error"]
    assert s.history == old
    assert sess._history_path(s.id).read_bytes() == disk


def test_save_failure_restores_memory_and_both_disk_files(tmp_path, monkeypatch):
    row = origin(tmp_path, attachment=False)
    private_rollout(tmp_path, monkeypatch, [{"role": "user", "content": row["content"]},
                                          {"role": "assistant", "content": "new"}])
    s = existing(row)
    old = copy.deepcopy(s.history)
    files = {p: p.read_bytes() for p in (sess._path(s.id), sess._history_path(s.id))}
    real_save = sess._save_body
    def fail_after_write(*args, **kwargs):
        real_save(*args, **kwargs)
        raise OSError("injected post-write failure")
    monkeypatch.setattr(sess, "_save_body", fail_after_write)
    assert asyncio.run(request(expected_status=500)) == "Internal Server Error"
    assert s.history == old
    assert s.last_result == {"status": "done", "result": "old"}
    assert {p: p.read_bytes() for p in files} == files
    sess._cache.clear()
    assert sess.get(s.id).history == old


def test_queued_commit_refuses_concurrent_append(tmp_path):
    row = origin(tmp_path, attachment=False)
    s = existing(row)
    revision = s.history_revision
    entered, release = threading.Event(), threading.Event()
    async def run():
        blocker = asyncio.create_task(sess._persist_async(s.id, lambda: (entered.set(), release.wait(5))))
        assert await asyncio.to_thread(entered.wait, 2)
        replacement = asyncio.create_task(sess.replace_usage_totals_async(
            s, None, native_entries=[], force_full=True, history=[row], expected_history_revision=revision))
        await asyncio.sleep(0)
        sess.append_history(s, {"role": "user", "content": "concurrent"})
        release.set()
        await blocker
        with pytest.raises(ValueError, match="history changed"):
            await replacement
        await sess.save_async(s)
    asyncio.run(run())
    sess._cache.clear()
    assert sess.get(s.id).history[-1]["content"] == "concurrent"


def test_duplicate_projection_preserves_order_only_with_equal_occurrences(tmp_path):
    row = origin(tmp_path)
    second = copy.deepcopy(row)
    second["messageId"] = "pan:" + "2" * 32
    second["taskId"] = "task-second"
    wire = project_message_parts(row["parts"], row["content"])
    native = [{"role": "user", "content": wire}, {"role": "assistant", "content": "anchor"},
              {"role": "user", "content": wire}]
    old = [row, {"role": "assistant", "content": "anchor"}, second]
    result = sess.assign_pan_message_ids(native, previous_history=old)
    assert [result[i]["messageId"] for i in (0, 2)] == [row["messageId"], second["messageId"]]
    assert result[2]["taskId"] == "task-second"


@pytest.mark.parametrize("kind", ["report", "notice", "qq", "wechat"])
def test_report_system_channel_origins_keep_envelopes(tmp_path, monkeypatch, kind):
    items = [{"type": kind, "id": "queue-report", "source": kind,
              "sourceSessionId": "ses_sender", "sessionId": "ses_sender",
              "taskId": "task-report", "result": {"lines": ["one", "two"]},
              "text": "channel\nmessage", "channel": kind,
              "envelope": {"status": "done", "nested": {"value": 1}}}]
    if kind == "notice":
        items[0].update(source="system", noticeKind="background_job_terminal", jobId="job-private")
    if kind == "report":
        items.append({**items[0], "id": "queue-second", "taskId": "task-second", "result": "second"})
    row = worker._queue_history_row(items)
    row["messageId"] = "pan:" + "3" * 32
    private_rollout(tmp_path, monkeypatch, [{"role": "user", "content": row["content"]}])
    s = existing(row)
    assert asyncio.run(request()).get("reimported") is True
    for key in ("source", "kind", "queueEnvelopes", "taskId", "channel", "envelope", "messageId"):
        assert s.history[0][key] == row[key]


def test_multiblock_structured_native_and_legacy_history(tmp_path):
    path = tmp_path / "structured.jsonl"
    events = [{"type": "session_meta", "payload": {"id": "private-thread"}},
              {"type": "event_msg", "payload": {"type": "item_completed", "item": {
                  "type": "userMessage", "id": "native", "content": [
                      {"type": "text", "text": "one\n"},
                      {"type": "text", "text": {"structured": ["two", 3]}}]}}}]
    path.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    native = codex._rollout_history(path, "private-thread")
    assert native[0]["content"] == 'one\n{"structured": ["two", 3]}'
    # Ordinary old history without an origin ledger remains compatible.
    old = [{"role": "user", "content": native[0]["content"], "messageId": "pan:" + "4" * 32}]
    assert sess.assign_pan_message_ids(native, previous_history=old)[0]["messageId"] == old[0]["messageId"]


def test_cancelled_waiter_does_not_rollback_successful_reimport(tmp_path, monkeypatch):
    row = origin(tmp_path, attachment=False)
    s = existing(row)
    native = [row, {"role": "assistant", "content": "durable new reply"}]
    entered, release = threading.Event(), threading.Event()
    real_save = sess._save_body
    def blocked_save(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        real_save(*args, **kwargs)
    monkeypatch.setattr(sess, "_save_body", blocked_save)
    async def run():
        task = asyncio.create_task(sess.replace_usage_totals_async(
            s, None, native_entries=[], force_full=True, history=native,
            expected_history_revision=s.history_revision))
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(run())
    assert s.history[-1]["content"] == "durable new reply"
    sess._cache.clear()
    assert sess.get(s.id).history[-1]["content"] == "durable new reply"


@pytest.mark.parametrize("delivery", ["fifo", "steer"])
def test_handoff_receipt_attachment_reimport(tmp_path, monkeypatch, delivery):
    template = origin(tmp_path, source="agent")
    item = copy.deepcopy(template["queueEnvelopes"][0])
    item["parts"] = copy.deepcopy(template["parts"])
    item.update(deliveryState="writing", queueItemId=item["id"])
    s = sess.create("receipt-private", adapter="codex", cli_session_id="private-thread")
    s.queue_pending = [item]
    w = SimpleNamespace(worker_id="fixture-worker", generation=1, _current_handoff_acked=False, _execution_seq=1)
    monkeypatch.setattr(worker, "_queue_steer_bcast", AsyncMock())
    monkeypatch.setattr(worker, "_bcast", AsyncMock())
    async def handoff():
        if delivery == "fifo":
            assert await worker._commit_queue_handoff(w, s, [item])
        else:
            item.update(queueSteerPending=True, queueSteerRequestId="receipt",
                        queueSteerWorkerId=w.worker_id, queueSteerGeneration=1)
            await worker._settle_queue_steer(w, s, item, item["text"], None, "receipt", 1)
    asyncio.run(handoff())
    assert s.queue_pending == []
    original = copy.deepcopy(s.history[0])
    wire = project_message_parts(original["parts"], original["content"])
    private_rollout(tmp_path, monkeypatch, [{"role": "user", "content": wire}])
    assert asyncio.run(request()).get("reimported") is True
    assert s.history[0]["messageId"] == original["messageId"]
    assert s.history[0]["queueEnvelopes"] == original["queueEnvelopes"]
    assert s.history[0]["taskId"] == original["taskId"]
