import pytest
import asyncio
import json
import threading
import time
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from packages.core.adapters.cbc.native_bridge import NativeBridge
from packages.core.adapters.codex.control_owner import ControlRejected


def test_large_hook_output_preserves_exit_metadata_before_http_limit(monkeypatch):
    from packages.core.adapters.cbc import native_hook
    observed = []
    response = io.BytesIO(b"{}")
    opener = SimpleNamespace(open=lambda request, timeout: (observed.append(request.data) or response))
    monkeypatch.setattr(native_hook.urllib.request, "build_opener", lambda *args: opener)
    monkeypatch.setenv("PAN_CBC_HOOK_URL", "http://127.0.0.1:1/hook")
    monkeypatch.setenv("PAN_CBC_HOOK_TOKEN", "test-only-capability")
    monkeypatch.setattr(native_hook.sys, "stdin", io.StringIO(json.dumps({
        "hook_event_name": "PostToolUse", "generation_id": "run-1", "call_id": "call-1",
        "tool_response": {"output": "x" * 2_000_000,
                          "rawResponse": {"exitCode": 7, "interrupted": False, "is_error": True}},
    })))
    assert native_hook.main() == 0
    assert len(observed[0]) < 128 * 1024
    payload = json.loads(observed[0])
    assert payload["call_id"] == "call-1"
    assert payload["tool_response"]["truncated"] is True
    assert payload["tool_response"]["rawResponse"] == {"exitCode": 7, "interrupted": False, "is_error": True}


def test_native_process_death_retires_bridge_with_stdin_still_open(monkeypatch, capsys, tmp_path):
    from packages.core.adapters.cbc import native_bridge
    from packages.core.terminal.backend import ConPtyBackend

    release = threading.Event()
    cleanup = []

    class OpenInput:
        def __iter__(self):
            release.wait(5)
            return iter(())

    backend = SimpleNamespace(
        read=lambda *args, **kwargs: b"", alive=lambda: False,
        terminate=lambda **kwargs: cleanup.append("terminate"),
        close=lambda: cleanup.append("close"),
    )
    def spawn(argv, **kwargs):
        assert argv.count("--settings") == 1
        supplied = json.loads(Path(argv[argv.index("--settings") + 1]).read_text())
        assert supplied["alwaysThinkingEnabled"] is False
        assert supplied["trustAll"] is True
        assert supplied["autoUpdates"] is False
        assert supplied["hooks"]["FinalStop"]
        return backend
    monkeypatch.setattr(ConPtyBackend, "spawn", spawn)
    monkeypatch.setattr(native_bridge.sys, "stdin", OpenInput())
    started = time.monotonic()
    try:
        native_bridge.run(["mock-only", "--settings", '{"alwaysThinkingEnabled":false}',
                           "--settings", '{"trustAll":true,"autoUpdates":false}'], str(tmp_path), "dedicated")
    finally:
        release.set()
    assert time.monotonic() - started < 2
    assert cleanup == ["terminate", "close"]
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events == [{"type": "pan.native_busy", "busy": True},
                      {"type": "pan.native_ready"},
                      {"type": "pan.native_error", "error": "CBC TUI exited"}]


def test_bootstrap_confirmation_is_not_a_native_agent_prompt():
    out = []
    b = NativeBridge(Backend(), out.append, "dedicated")
    b.control({"type": "control_mode", "mode": "native", "generation": 0})
    b.control({"type": "native_input", "text": "\r", "generation": 1})
    assert out[-1]["ok"] is True
    assert not b.awaiting_native_submit and not b.draft_unknown
    assert not any(e["type"] == "pan.native_busy" for e in out)
    with pytest.raises(ControlRejected):
        b.submit("must wait for SessionStart")
    b.hook(event("SessionStart", model="hy3"))
    assert out[-1] == {"type": "pan.native_busy", "busy": False}
    b.hook(event("UserPromptSubmit", prompt="native turn"))
    b.hook(event("SessionStart", model="hy3"))
    assert out[-1]["type"] == "system"
    assert b.turn == "run-1"


class Backend:
    def __init__(self):
        self.writes = []
        self.sizes = []

    def write(self, data):
        self.writes.append(data)
        return len(data)

    def resize(self, rows, cols):
        self.sizes.append((rows, cols))


@pytest.fixture
def bridge():
    emitted = []
    instance = NativeBridge(Backend(), emitted.append, "dedicated")
    instance.hook({"hook_event_name": "SessionStart", "session_id": "dedicated", "model": "hy3"})
    return instance, emitted


def event(name, run="run-1", **kwargs):
    return {"hook_event_name": name, "session_id": "dedicated", "generation_id": run, **kwargs}


def test_same_turn_survives_switch_and_rejects_stale_input(bridge):
    b, out = bridge
    b.submit("managed")
    writes_before = len(b.backend.writes)
    b.hook(event("UserPromptSubmit", prompt="managed"))
    b.control({"type": "control_mode", "mode": "native", "generation": 0})
    b.control({"type": "native_input", "text": "x", "generation": 0})
    assert out[-1]["ok"] is False
    assert len(b.backend.writes) == writes_before
    b.control({"type": "control_mode", "mode": "pan", "generation": 1})
    assert b.turn == "run-1"
    b.hook(event("Stop", last_assistant_message="done"))
    assert not any(e["type"] == "result" for e in out)
    b.hook(event("FinalStop", final_stop_reason="completed"))
    assert out[-1]["type"] == "result" and out[-1]["result"] == "done"
    b.hook(event("FinalStop", final_stop_reason="completed"))
    assert sum(e["type"] == "result" for e in out) == 1


@pytest.mark.parametrize("report", ["\x1b[I", "\x1b[O", "\x1b[I\x1b[O"])
def test_xterm_focus_reports_do_not_create_an_agent_draft(bridge, report):
    b, out = bridge
    b.control({"type": "control_mode", "mode": "native", "generation": 0})
    b.control({"type": "native_input", "text": report, "generation": 1})
    assert b.backend.writes[-1] == report.encode()
    assert not b.draft_unknown and not b.awaiting_native_submit
    assert not b.input_uncertain
    b.control({"type": "control_mode", "mode": "pan", "generation": 1})
    assert out[-1]["ok"] is True


def test_focus_report_with_real_text_still_blocks_handoff(bridge):
    b, out = bridge
    b.control({"type": "control_mode", "mode": "native", "generation": 0})
    b.control({"type": "native_input", "text": "\x1b[Iunfinished", "generation": 1})
    b.control({"type": "control_mode", "mode": "pan", "generation": 1})
    assert out[-1]["ok"] is False and b.draft_unknown


def test_approval_handoff_keeps_same_request_and_turn(bridge):
    b, out = bridge
    b.submit("managed")
    b.hook(event("UserPromptSubmit", prompt="managed"))
    b.hook(event("Notification", notification_type="permission_prompt"))
    b.control({"type": "control_mode", "mode": "native", "generation": 0})
    b.control({"type": "native_input", "text": "\r", "generation": 1})
    b.control({"type": "control_mode", "mode": "pan", "generation": 1})
    assert b.turn == "run-1" and b.permission_pending
    assert not b.awaiting_native_submit
    assert not b.input_uncertain
    b.hook(event("PostToolUse", call_id="call-1", tool_name="PowerShell", tool_response={"exitCode": 0}))
    assert out[-1]["item_id"] == "call-1"
    assert not b.permission_pending


def test_unknown_draft_and_partial_write_cannot_be_overwritten(bridge):
    b, out = bridge
    b.control({"type": "control_mode", "mode": "native", "generation": 0})
    b.control({"type": "native_input", "text": "unfinished", "generation": 1})
    b.control({"type": "control_mode", "mode": "pan", "generation": 1})
    assert out[-1]["ok"] is False
    assert b.owner.snapshot()["mode"] == "native"
    with pytest.raises(ControlRejected):
        b.submit("overwrite")
    assert b.backend.writes == [b"unfinished"]


def test_enter_handoff_waits_for_native_submit_before_dispatch(bridge):
    b, out = bridge
    b.control({"type": "control_mode", "mode": "native", "generation": 0})
    b.control({"type": "native_input", "text": "prompt\r", "generation": 1})
    b.control({"type": "control_mode", "mode": "pan", "generation": 1})
    with pytest.raises(ControlRejected):
        b.submit("racing queue")
    # A submitted native turn remains native-origin even when ownership was
    # switched before the asynchronous submit hook arrived.
    assert b.awaiting_native_submit
    b.hook(event("UserPromptSubmit", prompt="prompt"))
    assert b.turn == "run-1" and not b.awaiting_native_submit
    assert sum(e["type"] == "pan.native_turn_started" for e in out) == 1


def test_native_prompt_queued_during_tool_cannot_be_lost_on_pan_switch(bridge):
    b, out = bridge
    b.submit("managed")
    b.hook(event("UserPromptSubmit", prompt="managed"))
    b.control({"type": "control_mode", "mode": "native", "generation": 0})
    b.control({"type": "native_input", "text": "next native task\r", "generation": 1})
    b.control({"type": "control_mode", "mode": "pan", "generation": 1})
    assert out[-1]["ok"] is False and b.owner.snapshot()["mode"] == "native"
    b.hook(event("FinalStop", final_stop_reason="completed"))
    b.hook(event("UserPromptSubmit", run="next", prompt="next native task"))
    assert b.turn == "next"
    b.control({"type": "control_mode", "mode": "pan", "generation": 1})
    assert out[-1]["ok"] is True


def test_late_stop_and_unknown_reason_never_finish_another_turn(bridge):
    b, out = bridge
    b.submit("managed")
    b.hook(event("UserPromptSubmit", prompt="managed"))
    b.hook(event("FinalStop", run="previous", final_stop_reason="completed"))
    assert b.turn == "run-1"
    b.hook(event("FinalStop", final_stop_reason="unrecognized"))
    assert out[-1]["is_error"] is True


def test_cancel_and_prompt_receipt_mismatch(bridge):
    b, out = bridge
    b.submit("managed")
    with pytest.raises(ControlRejected):
        b.hook(event("UserPromptSubmit", prompt="other"))
    assert b.pending_prompt == "managed"
    b.hook(event("UserPromptSubmit", prompt="managed"))
    b.control({"type": "interrupt"})
    assert b.backend.writes[-1] == b"\x1b"
    assert not b.input_uncertain
    b.hook(event("FinalStop", final_stop_reason="cancelled"))
    assert out[-1]["cancelled"] is True


def test_continuation_generation_stays_in_one_managed_turn(bridge):
    b, out = bridge
    b.submit("managed")
    b.hook(event("UserPromptSubmit", prompt="managed"))
    b.hook(event("UserPromptSubmit", run="continuation", is_internal_continuation=True))
    b.hook(event("Stop", run="continuation", last_assistant_message="recovered"))
    b.hook(event("FinalStop", run="continuation", final_stop_reason="completed"))
    assert out[-1]["result"] == "recovered"
    assert sum(e["type"] == "result" for e in out) == 1
    b.hook(event("FinalStop", final_stop_reason="completed"))
    assert sum(e["type"] == "result" for e in out) == 1


def test_resize_and_missing_generation_are_fenced(bridge):
    b, out = bridge
    b.control({"type": "control_mode", "mode": "native", "generation": 0})
    b.control({"type": "native_resize", "rows": 37, "cols": 101, "generation": 1})
    assert out[-1]["ok"] is True and b.backend.sizes == [(37, 101)]
    b.control({"type": "native_input", "text": "unsafe"})
    assert out[-1]["ok"] is False and not b.backend.writes


def test_large_payload_stays_parseable_and_reports_truncation(bridge):
    b, out = bridge
    b.submit("managed")
    b.hook(event("UserPromptSubmit", prompt="managed"))
    b.hook(event("PostToolUse", call_id="large", tool_name="PowerShell", tool_input={"text": "中" * 100000}))
    b.hook(event("Stop", last_assistant_message="中" * 100000))
    b.hook(event("FinalStop", final_stop_reason="completed"))
    assert out[-1]["cbc_truncated"] is True
    assert "truncated" in out[-1]["result"]
    for frame in out:
        encoded = json.dumps(frame, ensure_ascii=True).encode()
        assert len(encoded) < 256 * 1024
        assert json.loads(encoded) == frame


def test_native_adapter_keeps_legacy_and_first_spawn_separate(monkeypatch, tmp_path):
    from packages.core.adapters.cbc.adapter import CbcAdapter
    from packages.core.adapters.cbc.native_adapter import CbcNativeAdapter
    adapter = CbcNativeAdapter()
    for method, value in (("_resolve_cbc_argv", ["node", "cbc.js"]),
                          ("model_args", ["--model", "hy3"]),
                          ("permission_mode_args", ["--permission-mode", "default"]),
                          ("effort_args", []), ("thinking_args", []), ("mcp_args", [])):
        monkeypatch.setattr(adapter, method, lambda *args, value=value: list(value))
    s = SimpleNamespace(cli_session_id=None, workdir=str(tmp_path), system_prompt="instructions")
    args = adapter.build_spawn_args(s, ["--system-prompt", "instructions"])
    cli = json.loads(args[args.index("--cli-argv") + 1])
    assert "-p" not in cli and "--resume" not in cli
    assert cli.count("--system-prompt") == 1
    assert "--session-id" in cli
    s.cli_session_id = "prior-idle-session"
    cli = json.loads(adapter.build_spawn_args(s)[-1])
    assert cli[cli.index("--resume") + 1] == s.cli_session_id
    assert "--system-prompt" not in cli
    assert CbcAdapter.execution_modes == ["stream", "oneshot"]
    assert not getattr(CbcAdapter, "supports_native_interrupt", False)


def test_cbc_interrupt_uncertainty_does_not_restart_kernel(monkeypatch):
    from packages.core import worker
    w = SimpleNamespace(adapter=SimpleNamespace(name="cbc"), native_control_enabled=True,
                        native_control_generation=3, status="running")
    monkeypatch.setitem(worker.workers, "cbc-probe", w)
    send = AsyncMock(return_value={"ok": False})
    restart = AsyncMock()
    monkeypatch.setattr(worker, "write_native_control", send)
    monkeypatch.setattr(worker, "_restart_worker_unlocked", restart)
    result = asyncio.run(worker._interrupt_worker_unlocked("cbc-probe"))
    assert "not confirmed" in result
    restart.assert_not_awaited()
    send.assert_awaited_once_with("cbc-probe", {"type": "interrupt", "generation": 3})


@pytest.mark.parametrize("failure", ["partial", "raised"])
@pytest.mark.parametrize("text", ["draft", "\r", "\x1b[I", "\x1b"])
def test_attempted_native_write_keeps_gate_even_after_later_full_write(bridge, failure, text):
    b, out = bridge
    b.control({"type": "control_mode", "mode": "native", "generation": 0})
    calls = []
    def fault(data):
        calls.append(data)
        if failure == "raised":
            raise OSError("write accepted bytes before failing")
        return len(data) - 1
    b.backend.write = fault
    b.control({"type": "native_input", "text": text, "generation": 1})
    assert out[-1]["ok"] is False
    b.control({"type": "control_mode", "mode": "pan", "generation": 1})
    assert out[-1]["ok"] is False, "uncertain input must prevent Pan ownership"
    assert b.owner.snapshot() == {"mode": "native", "generation": 1}
    assert b.input_uncertain
    with pytest.raises(ControlRejected):
        b.submit("must not dispatch")
    assert calls == [text.encode()]
    b.backend.write = lambda data: len(data)
    b.control({"type": "native_input", "text": "\x1b[O", "generation": 1})
    assert out[-1]["ok"] is True
    assert b.input_uncertain, "another successful write cannot undo unknown bytes"
    b.control({"type": "control_mode", "mode": "pan", "generation": 1})
    assert out[-1]["ok"] is False


@pytest.mark.parametrize("control", [
    {"type": "native_input", "text": "draft", "generation": 0},
    {"type": "native_input", "text": "draft", "generation": "1"},
    {"type": "native_input", "text": "\ud800", "generation": 1},
    {"type": "native_input", "text": "draft", "generation": 1, "rows": 0, "cols": 80},
    {"type": "native_input", "text": "", "generation": 0},
    {"type": "native_resize", "generation": 1},
])
def test_prewrite_rejections_do_not_touch_pty_or_pollute_input_intent(bridge, control):
    b, out = bridge
    b.control({"type": "control_mode", "mode": "native", "generation": 0})
    b.control(control)
    assert out[-1]["ok"] is False
    assert not b.backend.writes
    assert not b.draft_unknown and not b.awaiting_native_submit
    assert not b.input_uncertain
    assert not any(e["type"] == "pan.native_input_state" for e in out)


@pytest.mark.parametrize("failure", ["partial", "raised"])
@pytest.mark.parametrize("stage", [1, 2])
def test_pan_submit_unknown_paste_or_enter_cannot_redispatch(bridge, monkeypatch, failure, stage):
    from packages.core.adapters.cbc import native_bridge
    monkeypatch.setattr(native_bridge.time, "sleep", lambda _: None)
    b, out = bridge
    calls = []
    def fault(data):
        calls.append(data)
        if len(calls) == stage:
            if failure == "raised":
                raise OSError("write accepted bytes before failing")
            return len(data) - 1
        return len(data)
    b.backend.write = fault
    with pytest.raises((ControlRejected, OSError)):
        b.submit("managed")
    assert b.pending_prompt == "managed" and b.pending_origin == "pan"
    assert b.input_uncertain
    b.control({"type": "control_mode", "mode": "pan", "generation": 0})
    assert out[-1]["ok"] is False
    with pytest.raises(ControlRejected):
        b.submit("managed")
    assert len(calls) == stage


def test_wrong_owner_submit_and_input_never_reach_backend(bridge):
    b, out = bridge
    b.control({"type": "native_input", "text": "draft", "generation": 0})
    assert out[-1]["ok"] is False and not b.backend.writes
    assert not b.input_uncertain and not b.draft_unknown
    b.control({"type": "control_mode", "mode": "native", "generation": 0})
    with pytest.raises(ControlRejected):
        b.submit("managed")
    assert b.pending_prompt is b.pending_origin is None
    assert not b.input_uncertain and not b.backend.writes


@pytest.mark.parametrize("text", ["", "\x1b[200~bad", "\ud800", None])
def test_invalid_pan_prompt_has_no_pending_intent_or_backend_write(bridge, text):
    b, _ = bridge
    with pytest.raises(ControlRejected):
        b.submit(text)
    assert b.pending_prompt is b.pending_origin is None
    assert not b.input_uncertain and not b.backend.writes


def test_stale_enter_preserves_existing_native_draft(bridge):
    b, out = bridge
    b.control({"type": "control_mode", "mode": "native", "generation": 0})
    b.control({"type": "native_input", "text": "draft", "generation": 1})
    b.control({"type": "native_input", "text": "\r", "generation": 0})
    assert out[-1]["ok"] is False
    assert b.backend.writes == [b"draft"]
    assert b.draft_unknown and not b.awaiting_native_submit and not b.input_uncertain


@pytest.mark.parametrize("approval", [True, False])
def test_partial_approval_or_cancel_not_cleared_by_turn_completion(bridge, approval):
    b, out = bridge
    b.submit("managed")
    b.hook(event("UserPromptSubmit", prompt="managed"))
    if approval:
        b.hook(event("Notification", notification_type="permission_prompt"))
    b.control({"type": "control_mode", "mode": "native", "generation": 0})
    b.backend.write = lambda data: len(data) - 1
    b.control({"type": "native_input", "text": "\r" if approval else "\x1b", "generation": 1})
    b.hook(event("FinalStop", final_stop_reason="completed" if approval else "cancelled"))
    b.control({"type": "control_mode", "mode": "pan", "generation": 1})
    assert out[-1]["ok"] is False and b.input_uncertain


def test_input_intent_precedes_backend_call_and_success_has_receipt(bridge):
    b, out = bridge
    b.control({"type": "control_mode", "mode": "native", "generation": 0})
    def observe(data):
        assert b.draft_unknown and b.input_uncertain
        assert out[-1] == {"type": "pan.native_input_state", "uncertain": True}
        return len(data)
    b.backend.write = observe
    b.control({"type": "native_input", "text": "draft", "generation": 1, "requestId": "ack"})
    assert out[-1]["ok"] is True and out[-1]["requestId"] == "ack"
    assert b.draft_unknown and not b.input_uncertain


@pytest.mark.parametrize("failure", ["terminate", "close"])
def test_cleanup_continues_after_backend_failure(monkeypatch, tmp_path, failure):
    from packages.core.adapters.cbc import native_bridge
    from packages.core.terminal.backend import ConPtyBackend
    servers = []
    directories = []
    calls = []
    owned_alive = [True]
    real_server = native_bridge.ThreadingHTTPServer
    class Server(real_server):
        def __init__(self, *args):
            super().__init__(*args)
            servers.append(self)
        def shutdown(self):
            calls.append("server.shutdown")
            super().shutdown()
        def server_close(self):
            calls.append("server.close")
            super().server_close()
    def terminate(**kwargs):
        calls.append("terminate")
        if failure == "terminate":
            raise OSError("terminate failed")
        owned_alive[0] = False
    def guard_close():
        calls.append("guard.close")
        owned_alive[0] = False
        return True
    def close():
        calls.append("close")
        if failure == "close":
            raise OSError("close failed")
    backend = SimpleNamespace(read=lambda *args, **kwargs: b"", alive=lambda: True,
        terminate=terminate, close=close, guard=SimpleNamespace(close=guard_close))
    backend.alive = lambda: owned_alive[0]
    def spawn(argv, **kwargs):
        directories.append(Path(argv[-1]).parent)
        return backend
    monkeypatch.setattr(native_bridge, "ThreadingHTTPServer", Server)
    monkeypatch.setattr(native_bridge.sys, "stdin", io.StringIO(""))
    monkeypatch.setattr(ConPtyBackend, "spawn", spawn)
    try:
        with pytest.raises(OSError, match=failure + " failed"):
            native_bridge.run(["mock-only"], str(tmp_path), "dedicated")
        assert "terminate" in calls and "guard.close" in calls and "close" in calls
        assert "server.shutdown" in calls and "server.close" in calls
        assert not owned_alive[0] and not directories[0].exists()
        assert servers[0].socket.fileno() == -1
    finally:
        owned_alive[0] = False
        # The red baseline skips server retirement after a backend exception.
        # Ensure that the test itself never leaves its owned listener alive.
        if servers and servers[0].socket.fileno() != -1:
            real_server.shutdown(servers[0])
            real_server.server_close(servers[0])


@pytest.mark.parametrize("failure", ["spawn", "settings"])
def test_prelaunch_failure_releases_listener_and_temp(monkeypatch, tmp_path, failure):
    from packages.core.adapters.cbc import native_bridge
    from packages.core.terminal.backend import ConPtyBackend
    servers = []
    directories = []
    real_server = native_bridge.ThreadingHTTPServer
    real_temp = native_bridge.tempfile.TemporaryDirectory
    def server(*args):
        instance = real_server(*args)
        servers.append(instance)
        return instance
    def directory(**kwargs):
        instance = real_temp(**kwargs)
        directories.append(Path(instance.name))
        return instance
    def spawn(*args, **kwargs):
        raise OSError("spawn failed")
    monkeypatch.setattr(native_bridge, "ThreadingHTTPServer", server)
    monkeypatch.setattr(native_bridge.tempfile, "TemporaryDirectory", directory)
    monkeypatch.setattr(ConPtyBackend, "spawn", spawn)
    argv = ["mock-only"] + (["--settings", '{"broken":'] if failure == "settings" else [])
    try:
        with pytest.raises((OSError, ValueError)):
            native_bridge.run(argv, str(tmp_path), "dedicated")
        assert servers[0].socket.fileno() == -1
        assert directories and not directories[0].exists()
    finally:
        real_server.server_close(servers[0])


def test_worker_uncertainty_survives_result_and_blocks_reserved_handoff(monkeypatch):
    from packages.core import worker, session
    from packages.core.adapters.cbc.native_adapter import CbcNativeAdapter
    async def run():
        s = session.Session(id="ses-cbc-uncertain", name="isolated", adapter="cbc")
        s.adapter_config["native_control"] = True
        queued = {"type": "task", "text": "must remain queued", "seq": 2}
        s.queue_pending = [queued]
        monkeypatch.setitem(session._cache, s.id, s)
        for name in ("_bcast", "_enqueue_report", "_flush_history_now"):
            monkeypatch.setattr(worker, name, AsyncMock())
        monkeypatch.setattr(session, "save_async", AsyncMock())
        frames = asyncio.Queue()
        for event_frame in [
            {"type": "pan.native_turn_started"},
            {"type": "pan.native_input_state", "uncertain": True},
            {"type": "result", "is_error": False, "result": "completed"},
        ]:
            frames.put_nowait((json.dumps(event_frame) + "\n").encode())
        class Process:
            returncode = None
            async def read(self, size):
                return await frames.get()
        process = Process()
        process.stdout = process
        w = worker.Worker("cbc-uncertain", s.id, CbcNativeAdapter(), process=process,
            native_control_enabled=True, native_control_mode="pan", pending_signal=asyncio.Queue())
        monkeypatch.setitem(worker.workers, w.worker_id, w)
        monkeypatch.setitem(worker._workers_by_session, s.id, w)
        reader = asyncio.create_task(worker._read_stdout(w))
        try:
            async with asyncio.timeout(3):
                while not s.last_result or w._native_external_turn_active:
                    await asyncio.sleep(0)
            assert w.native_input_uncertain
            assert worker._native_handoff_blocked(s)
            handoff = AsyncMock()
            with pytest.raises(worker._QueuePausedBeforeHandoff, match="acceptance is unknown"):
                await worker._queue_handoff(w, s, handoff, None)
            handoff.assert_not_awaited()
            assert s.queue_pending == [queued]
            worker.clear_native_runtime_state(w)
            assert w.native_input_uncertain
            process.returncode = 0
            worker.clear_native_runtime_state(w)
            assert not w.native_input_uncertain
        finally:
            reader.cancel()
            with pytest.raises(asyncio.CancelledError):
                await reader
    asyncio.run(run())


def test_windows_owned_job_retires_root_and_child_when_terminate_raises(monkeypatch, tmp_path):
    """Real OS cleanup fault, using only inert owned Node processes, no model."""
    import os
    import sys
    import shutil
    import psutil
    from packages.core.adapters.cbc import native_bridge
    from packages.core.terminal.backend import ConPtyBackend, BackendCloseError
    if sys.platform != "win32":
        pytest.skip("ConPTY owned Job cleanup requires Windows")
    node = shutil.which("node")
    assert node, "Node is required for this zero-provider OS probe"
    actual_spawn = ConPtyBackend.spawn
    retained = []
    owned = []
    directories = []
    def spawn(argv, **kwargs):
        directories.append(Path(argv[-1]).parent)
        backend = actual_spawn([node, "-e",
            "require('node:child_process').spawn(process.execPath,['-e','setInterval(()=>{},1000)'],{stdio:'ignore'});setInterval(()=>{},1000)"],
            cwd=str(tmp_path), env={"PATH": os.environ.get("PATH", ""),
            "SystemRoot": os.environ["SystemRoot"], "TERM": "xterm-256color"}, rows=30, cols=120)
        retained.append(backend)
        root = psutil.Process(backend.pid)
        owned.append(root)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            children = root.children(recursive=True)
            if children:
                owned.extend(children)
                break
            time.sleep(.02)
        assert len(owned) > 1, "owned child did not start"
        def fail_terminate(**kwargs):
            assert root.is_running()
            raise OSError("injected terminate failure")
        backend.terminate = fail_terminate
        return backend
    monkeypatch.setattr(ConPtyBackend, "spawn", spawn)
    monkeypatch.setattr(native_bridge.sys, "stdin", io.StringIO(""))
    try:
        with pytest.raises((OSError, BackendCloseError)):
            native_bridge.run(["mock-only"], str(tmp_path), "dedicated")
        _, alive = psutil.wait_procs(owned, timeout=5)
        assert not alive, "owned Job fallback failed to retire a process"
        assert not retained[0].guard.handle_open
        assert not directories[0].exists()
    finally:
        # Keep even a failed OS probe confined to the exact Job it created.
        for backend in retained:
            backend.guard.close()
        psutil.wait_procs(owned, timeout=5)
        for backend in retained:
            backend.close()


def test_probe_identity_ignores_finished_hook_but_requires_live_kernel(monkeypatch):
    import importlib.util
    import psutil
    source = Path(__file__).resolve().parents[1] / "tools/probe_cbc_native_worker.py"
    spec = importlib.util.spec_from_file_location("cbc_probe_identity", source)
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    class FinishedHook:
        def cmdline(self):
            raise psutil.NoSuchProcess(99)
    kernel = SimpleNamespace(pid=101, cmdline=lambda: ["node", "/installed/bin/codebuddy"],
                             create_time=lambda: 3.5)
    children = [FinishedHook(), kernel]
    monkeypatch.setattr(psutil, "Process", lambda pid: SimpleNamespace(children=lambda **kwargs: children))
    assert probe.kernel_identity(7) == (101, 3.5)
    children.remove(kernel)
    with pytest.raises(AssertionError, match="exactly one"):
        probe.kernel_identity(7)
    children.extend([kernel, kernel])
    with pytest.raises(AssertionError, match="exactly one"):
        probe.kernel_identity(7)


@pytest.mark.parametrize("status", ["running", "queued", "idle"])
def test_watchdog_cannot_retire_or_auto_recover_uncertain_input(monkeypatch, status):
    from packages.core import worker
    from packages.core.adapters.cbc.native_adapter import CbcNativeAdapter
    w = worker.Worker("cbc-unknown-watchdog", "ses-cbc-unknown-watchdog", CbcNativeAdapter(),
        status=status, process=SimpleNamespace(returncode=None), native_control_enabled=True,
        native_control_mode="pan", native_input_uncertain=True)
    w.last_activity = w._task_started_at = 0
    monkeypatch.setattr(worker, "workers", {w.worker_id: w})
    for name in ("_WORKER_TASK_TIMEOUT_SEC", "_WORKER_TIMEOUT_SEC", "_WORKER_IDLE_SEC"):
        monkeypatch.setattr(worker, name, 0)
    monkeypatch.setattr(worker, "_worker_has_pending_work", lambda _: False)
    kill = AsyncMock()
    monkeypatch.setattr(worker, "kill_worker", kill)
    monkeypatch.setattr(worker, "_enqueue_zombie_report", AsyncMock())
    ticks = 0
    async def tick(_):
        nonlocal ticks
        ticks += 1
        if ticks == 2:
            raise asyncio.CancelledError
    monkeypatch.setattr(worker.asyncio, "sleep", tick)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker._watchdog(w))
    kill.assert_not_awaited()


@pytest.mark.parametrize("operation", ["restart", "respawn", "takeover"])
@pytest.mark.parametrize("confirmed", [False, True])
def test_input_gate_clears_only_after_confirmed_retirement(monkeypatch, operation, confirmed):
    from packages.core import worker, session
    from packages.core.adapters.cbc.native_adapter import CbcNativeAdapter
    s = session.Session(id="ses-cbc-retirement", name="retirement", adapter="cbc")
    s.adapter_config["native_control"] = True
    old = SimpleNamespace(returncode=None)
    w = worker.Worker("cbc-retirement", s.id, CbcNativeAdapter(), process=old,
        native_control_enabled=True, native_control_mode="native", native_input_uncertain=True)
    monkeypatch.setattr(worker, "workers", {w.worker_id: w})
    monkeypatch.setattr(worker, "_session", lambda _: s)
    for name in ("_stop_worker_tasks", "_kill_takeover_terminal", "_record_legal_worker_state",
                 "_restart_tasks", "_bcast"):
        monkeypatch.setattr(worker, name, AsyncMock())
    monkeypatch.setattr(worker, "resolve_execution_mode", lambda *args: "stream")
    admission = []
    async def kill(value):
        admission.append(value.native_input_uncertain)
        if confirmed:
            old.returncode = 0
    monkeypatch.setattr(worker, "_kill_process_tree", kill)
    async def spawn(*args, **kwargs):
        assert not w.native_input_uncertain and old.returncode == 0
        return SimpleNamespace(returncode=None)
    create = AsyncMock(side_effect=spawn)
    monkeypatch.setattr(worker, "_spawn_process", create)
    action = {"restart": worker._restart_worker_unlocked,
              "respawn": worker._respawn_worker_unlocked,
              "takeover": worker._takeover_worker_unlocked}[operation]
    result = asyncio.run(action(w.worker_id))
    assert admission == [True], "snapshot reset must not release unretired input"
    if confirmed:
        assert result is None and not w.native_input_uncertain
        assert create.await_count == (0 if operation == "takeover" else 1)
    else:
        assert "not confirmed" in result
        assert w.native_input_uncertain and w.process is old and w.status == "error"
        create.assert_not_awaited()
