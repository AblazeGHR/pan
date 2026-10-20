"""Native completion uses the existing durable result/report lifecycle."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from packages.core import worker, session
from packages.core.adapters.codex import CodexAdapter
from packages.core.adapters.codex.app_server_wrapper import AppServer
from packages.core.adapters.codex.resident_tui import ResidentTui


def test_external_turn_keeps_queue_and_allocates_new_report_identity(monkeypatch):
    async def run():
        s = session.Session(id="ses-native-lifecycle", name="native", adapter="codex", task_seq=12)
        queued = {"id": "later", "type": "task", "text": "later", "seq": 12, "taskId": "queued-id"}
        s.queue_pending = [queued]
        monkeypatch.setattr(session, "save_async", AsyncMock())
        monkeypatch.setitem(session._cache, s.id, s)
        reports = AsyncMock()
        monkeypatch.setattr(worker, "_enqueue_report", reports)
        events = [
            {"type": "pan.native_busy", "busy": True},
            {"type": "pan.native_turn_admission", "requestId": "native-request", "threadId": "native-thread", "controlGeneration": 0},
            {"type": "pan.native_turn_started", "turn_id": "native-turn", "requestId": "native-request"},
            {"type": "pan.native_user", "text": "stale", "item_id": "old", "turn_id": "old-turn", "thread_id": "native-thread"},
            {"type": "pan.native_user", "text": "原生输入", "item_id": "u1", "turn_id": "native-turn", "thread_id": "native-thread"},
            {"type": "pan.native_user", "text": "原生输入", "item_id": "u1", "turn_id": "native-turn", "thread_id": "native-thread"},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "native answer"}]}},
            {"type": "result", "external_turn": True, "result": "native answer", "is_error": False},
        ]
        class Process:
            returncode = 0
            pid = 1
            async def read(self, size):
                return (json.dumps(events.pop(0)) + "\n").encode() if events else b""
        process = Process()
        process.stdout = process
        def ack(data):
            receipt = json.loads(data)
            for event in events:
                if event.get("type") in {"pan.native_turn_started", "pan.native_user", "result"}:
                    event.update(runId=receipt["runId"], requestId=receipt["requestId"])
        process.stdin = Mock(write=ack, drain=AsyncMock())
        s.cli_session_id = "native-thread"
        w = worker.Worker("native-lifecycle", s.id, CodexAdapter(), process=process,
                          native_control_mode="native", pending_signal=asyncio.Queue())
        w._current_task_id = "previous-pan-id"
        w._current_queue_item = {"taskId": "previous-pan-id"}
        w._terminal_handled = True
        monkeypatch.setitem(worker.workers, w.worker_id, w)
        await worker._read_stdout(w)
        assert s.last_result["taskSeq"] == 13
        assert s.last_result["result"] == "native answer"
        assert s.queue_pending == [queued]
        assert [(r["role"], r["content"]) for r in s.history] == [("user", "原生输入"), ("assistant", "native answer")]
        assert s.history[0]["nativeItemId"] == "u1" and s.history[0]["turnId"] == "native-turn"
        assert reports.await_args.args[3] is None
        assert w._task_started_at > 0
        assert not w._native_external_turn_active
        assert w.pending_signal.empty()
    asyncio.run(run())


def test_native_user_item_retains_identity_and_attachment_metadata(monkeypatch):
    out = []
    monkeypatch.setattr("packages.core.adapters.codex.app_server_wrapper._write_stdout", out.append)
    app = AppServer("node", "codex.js", ".", [], native_control=True)
    app._handle_server_message({"method": "item/completed", "params": {"item": {
        "type": "userMessage", "id": "u1", "content": [{"type": "text", "text": "中文"}, {"type": "image", "url": "private"}]
    }}}, {"external_turn": True})
    assert out[0]["nativeContent"] == [{"type": "text", "text": "中文"}, {"type": "image", "url": "private"}]
    assert out[0]["item_id"] == "u1"


def test_terminal_snapshot_projects_missing_items_once_without_body_dedup(monkeypatch):
    out = []
    monkeypatch.setattr("packages.core.adapters.codex.app_server_wrapper._write_stdout", out.append)
    app = AppServer("node", "codex.js", ".", [], native_control=True)
    state = {"external_turn": True, "turn_id": "t"}
    items = [{"type": "userMessage", "id": "u", "content": [{"type": "text", "text": "same"}]},
             {"type": "agentMessage", "id": "a", "text": "same"},
             {"type": "agentMessage", "id": "b", "text": "same"},
             {"type": "reasoning", "id": "thinking", "summary": ["full thinking"]},
             {"type": "commandExecution", "id": "tool", "command": "echo complete", "aggregatedOutput": "full output"}]
    app._handle_server_message({"method": "item/completed", "params": {"turnId": "t", "item": items[1]}}, state)
    app._handle_server_message({"method": "turn/completed", "params": {
        "turn": {"id": "t", "status": "completed", "items": items}}}, state)
    assert [event["item_id"] for event in out] == ["a", "u", "b", "thinking", "tool"]
    assert "full thinking" in json.dumps(out, ensure_ascii=False)
    assert "full output" in json.dumps(out, ensure_ascii=False)
    size = len(out)
    app._handle_server_message({"method": "turn/completed", "params": {
        "turn": {"id": "t", "status": "completed", "items": items}}}, state)
    assert len(out) == size


def test_native_reply_uses_original_connection_and_is_not_declined_again():
    app = AppServer("node", "codex.js", ".", [], native_control=True)
    primary, peer = [], []
    app._native_transport = SimpleNamespace(send=primary.append)
    record = {"id": "logical", "deadline": 0}
    state = {"pending_requests": {"logical": record}}
    app._open_requests["logical"] = ({"id": "logical"}, record)
    app._reply_routes["logical"] = (77, SimpleNamespace(send=peer.append))
    app._raw_send({"id": "logical", "result": {"decision": "accept"}})
    app._safe_decline_expired(state)
    assert peer == [{"id": 77, "result": {"decision": "accept"}}]
    assert primary == [] and state["pending_requests"] == {}
    assert not app._reply_routes and not app._open_requests


def test_pending_prompts_are_reissued_with_new_generation_only():
    app = AppServer("node", "codex.js", ".", [], native_control=True)
    tui = ResidentTui(app, lambda event: None)
    sent = []
    tui._down_send = sent.append
    message = {"id": 2, "method": "item/commandExecution/requestApproval", "params": {}}
    app._open_requests["2"] = (message, {})
    tui.offer_request(message)
    assert not sent
    app.control_owner.switch("native", 0)
    tui.mode_changed()
    first = sent[-1]["id"]
    tui.offer_request({**message, "id": 3})
    assert first in tui._answers  # New prompt mustn't invalidate an old prompt.
    app.control_owner.switch("pan", 1)
    tui.mode_changed()
    assert not tui._answers
    app.control_owner.switch("native", 2)
    tui.mode_changed()
    assert first not in tui._answers and len(tui._answers) == 2
