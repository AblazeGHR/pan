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
