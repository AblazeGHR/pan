"""Unit tests for the Kimi Terminal TUI bridge (PTY + wire.jsonl facts).

Covers the contract proven against real kimi 2.3.1 (2026-10-24):
- turn facts come from wire.jsonl (turn.prompt / agent.turn.started /
  content.part / turn.ended), never from PTY text;
- mid-turn input is *not* steer: the adapter must not advertise steer;
- Ctrl+C interception is a control-channel operation with a wire receipt
  (turn.ended.reason == "cancelled");
- workspace trust is a file marker keyed by root, not a screen-scraping guess;
- capability gates are data (supports_native_control), not adapter-name sets.

Real-PTY evidence lives in tests/test_kimi_native_integration.py (env-gated).
"""

import io
import json
import queue
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import session as _sess
from packages.core.adapters import adapter_supports_native_control, get_adapter
from packages.core.adapters.kimi import native_bridge as nb
from packages.core.adapters.kimi.native_adapter import KimiNativeAdapter


# ── wire facts → Pan events ────────────────────────────────────────────────


def _tracker(mode="pan"):
    out: list[dict] = []
    track = nb.WireTurnTracker(out.append, is_native_mode=lambda: mode == "native")
    return track, out


def _wire_prompt(text, turn=1, origin="user"):
    return {"type": "turn.prompt", "turnId": turn, "origin": {"kind": origin},
            "input": [{"type": "text", "text": text}], "promptId": f"msg_{turn}"}


def _wire_part(kind, value):
    return {"type": "context.append_loop_event",
            "event": {"type": "content.part", "part": {"type": kind, kind: value}}}


def test_wire_turn_completes_with_assistant_blocks_and_result():
    track, out = _tracker()
    track.feed(_wire_prompt("hello"))
    track.feed({"type": "agent.turn.started", "turnId": 1})
    track.feed(_wire_part("text", "PAN "))
    track.feed(_wire_part("text", "TUI_OK"))
    track.feed(_wire_part("think", "thinking..."))
    track.feed({"type": "context.append_loop_event",
                "event": {"type": "tool.call", "name": "Bash", "args": {"command": "echo 1"}}})
    track.feed({"type": "turn.ended", "turnId": 1, "reason": "completed"})

    assert out[0] == {"type": "pan.native_busy", "busy": True}
    assistant = [e for e in out if e.get("role") == "assistant"]
    assert len(assistant) == 1
    blocks = assistant[0]["content"]
    assert {"type": "text", "text": "PAN TUI_OK"} in blocks
    assert {"type": "think", "think": "thinking..."} in blocks
    assert any(b.get("type") == "tool_use" and b.get("name") == "Bash" for b in blocks)
    result = [e for e in out if e.get("role") == "result"][-1]
    assert result["is_error"] is False and result["cancelled"] is False
    assert result["result"] == "PAN TUI_OK"
    assert result["kimi_stop_reason"] == "completed"
    assert out[-1] == {"type": "pan.native_busy", "busy": False}


def test_wire_cancelled_turn_is_not_an_error():
    """Ctrl+C: wire reason=cancelled is the interrupt receipt (no fake success)."""
    track, out = _tracker()
    track.feed(_wire_prompt("long task"))
    track.feed(_wire_part("text", "partial"))
    track.feed({"type": "turn.ended", "turnId": 1, "reason": "cancelled"})
    result = [e for e in out if e.get("role") == "result"][-1]
    assert result["cancelled"] is True
    assert result["is_error"] is False
    assert result["kimi_stop_reason"] == "cancelled"


def test_wire_failed_turn_is_an_error():
    track, out = _tracker()
    track.feed(_wire_prompt("x"))
    track.feed({"type": "turn.ended", "turnId": 1, "reason": "error"})
    result = [e for e in out if e.get("role") == "result"][-1]
    assert result["is_error"] is True and result["cancelled"] is False


def test_native_origin_prompt_is_reported_only_in_native_mode():
    track, out = _tracker(mode="pan")
    track.feed(_wire_prompt("typed by user"))
    assert not [e for e in out if e["type"] == "pan.native_user"]

    track2, out2 = _tracker(mode="native")
    track2.feed(_wire_prompt("typed by user", turn=2))
    user_events = [e for e in out2 if e["type"] == "pan.native_user"]
    assert user_events and user_events[0]["text"] == "typed by user"
    assert user_events[0]["item_id"] == "msg_2"


def test_submit_receipt_requires_matching_prompt_echo():
    track, out = _tracker()
    track.expect_submit("PAN task")
    track.feed(_wire_prompt("something else"))
    assert track.pending_submit == "PAN task"
    track.feed(_wire_prompt("PAN task"))
    assert track.pending_submit is None
    # Pan-origin prompts are not reported as native user rows.
    assert not [e for e in out if e["type"] == "pan.native_user"]


def test_non_user_prompt_origin_is_ignored():
    track, out = _tracker(mode="native")
    track.feed(_wire_prompt("internal", origin="system"))
    assert out == []


# ── trust marker (file fact, not screen text) ──────────────────────────────


def test_workspace_trust_matches_by_root_not_filename(tmp_path):
    home = tmp_path / "home"
    (home / "workspace-trust").mkdir(parents=True)
    cwd = tmp_path / "work"
    cwd.mkdir()
    assert nb.workspace_trusted(home, str(cwd)) is False
    # kimi's file name is an opaque workspace id; Pan must match the root.
    (home / "workspace-trust" / "wd_opaque_1234").write_text(
        json.dumps({"root": str(cwd), "trustedAt": 1}), encoding="utf-8")
    assert nb.workspace_trusted(home, str(cwd)) is True
    other = tmp_path / "elsewhere"
    other.mkdir()
    assert nb.workspace_trusted(home, str(other)) is False


def test_workspace_trust_ignores_broken_markers(tmp_path):
    home = tmp_path / "home"
    (home / "workspace-trust").mkdir(parents=True)
    (home / "workspace-trust" / "bad").write_text("{not json", encoding="utf-8")
    cwd = tmp_path / "work"
    cwd.mkdir()
    assert nb.workspace_trusted(home, str(cwd)) is False


# ── fresh-session discovery ────────────────────────────────────────────────


def test_discover_session_id_picks_newest_matching_cwd(tmp_path):
    home = tmp_path / "home"
    cwd = tmp_path / "work"
    cwd.mkdir()
    for sid, created, root in (("session_old", 1000, str(cwd)),
                               ("session_new", 5000, str(cwd)),
                               ("session_other", 9000, str(tmp_path / "other"))):
        sdir = home / "sessions" / "wd_x" / sid
        sdir.mkdir(parents=True)
        (sdir / "state.json").write_text(json.dumps(
            {"id": sid, "cwd": root, "createdAt": created}), encoding="utf-8")
    assert nb.discover_session_id(home, str(cwd), since_ms=0) == "session_new"
    # The 2s tolerance keeps a session created just before the spawn.
    assert nb.discover_session_id(home, str(cwd), since_ms=8000) is None


# ── bridge control channel (fake PTY backend) ──────────────────────────────


class _FakeBackend:
    def __init__(self):
        self.writes: list[bytes] = []
        self.resizes: list[tuple[int, int]] = []

    def write(self, data):
        self.writes.append(bytes(data))
        return len(data)

    def resize(self, rows, cols):
        self.resizes.append((rows, cols))


def _bridge(tmp_path):
    out: list[dict] = []
    backend = _FakeBackend()
    bridge = nb.KimiNativeBridge(backend, out.append, kimi_home=str(tmp_path), cwd=str(tmp_path))
    bridge.ready.set()
    # Simulate a painted, quiet TUI so submit() skips its startup settle wait.
    bridge.output_bytes = 1024
    bridge.last_output = time.monotonic() - 2.0
    return bridge, backend, out


def test_control_mode_switch_and_stale_generation(tmp_path):
    bridge, _, out = _bridge(tmp_path)
    bridge.control({"type": "control_mode", "mode": "native", "generation": 0, "requestId": "r1"})
    assert out[-1]["type"] == "pan.control_mode" and out[-1]["ok"] is True
    assert bridge.owner.snapshot() == {"mode": "native", "generation": 1}
    bridge.control({"type": "control_mode", "mode": "pan", "generation": 0, "requestId": "r2"})
    assert out[-1]["ok"] is False


def test_native_input_requires_native_mode_and_generation(tmp_path):
    bridge, backend, out = _bridge(tmp_path)
    bridge.control({"type": "native_input", "text": "x", "generation": 0, "requestId": "a"})
    assert out[-1]["ok"] is False and backend.writes == []
    bridge.control({"type": "control_mode", "mode": "native", "generation": 0})
    bridge.control({"type": "native_input", "text": "ab", "generation": 1, "requestId": "b"})
    assert out[-1]["ok"] is True and backend.writes == [b"ab"]


def test_resize_validates_bounds(tmp_path):
    bridge, backend, out = _bridge(tmp_path)
    bridge.control({"type": "control_mode", "mode": "native", "generation": 0})
    bridge.control({"type": "native_resize", "rows": 40, "cols": 120, "generation": 1})
    assert out[-1]["ok"] is True and backend.resizes == [(40, 120)]
    bridge.control({"type": "native_resize", "rows": 0, "cols": 120, "generation": 1})
    assert out[-1]["ok"] is False


def test_interrupt_writes_ctrl_c_with_receipt(tmp_path):
    """Interrupt is Ctrl+C through the control channel; the wire ends the turn."""
    bridge, backend, out = _bridge(tmp_path)
    bridge.control({"type": "interrupt", "generation": 0, "requestId": "i1"})
    assert out[-1]["type"] == "pan.native_write" and out[-1]["ok"] is True
    assert backend.writes == [b"\x03"]


def test_submit_requires_workspace_trust(tmp_path):
    bridge, backend, _out = _bridge(tmp_path)
    with pytest.raises(nb.ControlRejected):
        bridge.submit("hello")
    assert backend.writes == []


def test_submit_pastes_and_waits_for_wire_echo(tmp_path, monkeypatch):
    monkeypatch.setattr(nb, "_SUBMIT_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(nb, "_SUBMIT_RETRY_TIMEOUT_SECONDS", 0.3)
    bridge, backend, _out = _bridge(tmp_path)
    bridge.trusted.set()
    with pytest.raises(nb.ControlRejected):
        # No wire echo arrives → the bridge must not claim acceptance.
        bridge.submit("multi\nline")
    assert backend.writes[0] == b"\x1b[200~multi\nline\x1b[201~"
    assert backend.writes[-1] == b"\r"


def test_submit_rejects_when_turn_is_busy(tmp_path):
    bridge, backend, _out = _bridge(tmp_path)
    bridge.trusted.set()
    bridge.tracker.busy = True
    with pytest.raises(nb.ControlRejected):
        bridge.submit("late input")
    assert backend.writes == []


# ── startup loop: the trust prompt stays answerable ────────────────────────


class _FakeStdin:
    def __init__(self):
        self.lines: queue.Queue = queue.Queue()

    def feed(self, payload: dict) -> None:
        self.lines.put(json.dumps(payload))

    def close(self) -> None:
        self.lines.put(None)

    def __iter__(self):
        while True:
            line = self.lines.get()
            if line is None:
                return
            yield line + "\n"


class _FakeBackend:
    """Minimal PTY backend: one painted frame, then a quiet screen."""

    def __init__(self):
        self.writes: list[bytes] = []
        self.resizes: list[tuple[int, int]] = []
        self.terminated: list[bool] = []
        self.closed = False
        self.alive_flag = True
        self._output = b"x" * 600
        self._lock = threading.Lock()

    def spawn(self, argv, **kwargs):  # patched as the classmethod below
        self.argv = list(argv)
        return self

    def read(self, size, *, timeout=None):
        with self._lock:
            if self._output:
                data, self._output = self._output, b""
                return data
        time.sleep(0.02)
        return b""

    def write(self, data):
        with self._lock:
            self.writes.append(bytes(data))
        return len(data)

    def resize(self, rows, cols):
        self.resizes.append((rows, cols))

    def alive(self):
        return self.alive_flag

    def terminate(self, force):
        self.terminated.append(bool(force))
        self.alive_flag = False

    def close(self):
        self.closed = True


class _TeeStdout:
    def __init__(self, original):
        self.original = original
        self.buffer = io.StringIO()
        self.lock = threading.Lock()

    def write(self, text):
        with self.lock:
            self.buffer.write(text)
        return len(text)

    def flush(self):
        pass

    def getvalue(self):
        with self.lock:
            return self.buffer.getvalue()


class _RunHarness:
    def __init__(self, tmp_path, session_id=""):
        self.home = tmp_path / "home"
        self.work = tmp_path / "work"
        self.home.mkdir()
        self.work.mkdir()
        self.session_id = session_id
        self.stdin = _FakeStdin()
        self.backend = _FakeBackend()
        self.thread = None
        self.stdout = None

    def start(self, monkeypatch):
        backend = self.backend

        class _Spawn:
            @staticmethod
            def spawn(argv, **kwargs):
                return backend.spawn(argv, **kwargs)

        monkeypatch.setattr("packages.core.terminal.backend.ConPtyBackend", _Spawn)
        monkeypatch.setattr(sys, "stdin", self.stdin)
        self.stdout = _TeeStdout(sys.stdout)
        monkeypatch.setattr(sys, "stdout", self.stdout)
        monkeypatch.setattr(nb, "_SUBMIT_TIMEOUT_SECONDS", 0.3)
        monkeypatch.setattr(nb, "_SUBMIT_RETRY_TIMEOUT_SECONDS", 0.3)
        monkeypatch.setattr(nb, "_PASTE_SETTLE_SECONDS", 0.01)
        self.thread = threading.Thread(
            target=nb.run,
            args=("kimi.exe", str(self.work), self.session_id or None,
                  str(self.home), "kimi-k2.6"),
            daemon=True,
        )
        self.thread.start()

    def events(self):
        out = []
        for line in self.stdout.getvalue().splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    def wait_for(self, predicate, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for event in self.events():
                if predicate(event):
                    return event
            time.sleep(0.02)
        return None

    def trust(self):
        trust = self.home / "workspace-trust"
        trust.mkdir(exist_ok=True)
        (trust / "wd_probe").write_text(
            json.dumps({"root": str(self.work)}), encoding="utf-8")

    def stop(self):
        self.stdin.close()
        if self.thread is not None:
            self.thread.join(10.0)
            assert not self.thread.is_alive(), "bridge loop did not stop"


def test_run_keeps_untrusted_tui_answerable_without_a_session(tmp_path, monkeypatch):
    """A fresh untrusted TUI has no session yet: that must not be fatal."""
    h = _RunHarness(tmp_path)
    h.start(monkeypatch)
    try:
        assert h.wait_for(lambda e: e.get("type") == "pan.native_trust_required", 5)
        h.stdin.feed({"type": "control_mode", "mode": "native", "generation": 0,
                      "requestId": "m1"})
        receipt = h.wait_for(lambda e: e.get("requestId") == "m1", 2.0)
        assert receipt is not None and receipt.get("ok") is True
        assert receipt.get("mode") == "native"
        # No session exists and none was declared a startup failure.
        assert not [e for e in h.events() if e.get("type") == "pan.native_error"]
        assert h.backend.writes == []
    finally:
        h.stop()
    assert h.backend.terminated == [True]
    assert h.backend.closed is True


def test_run_holds_pan_prompt_until_trust_and_keeps_input_live(tmp_path, monkeypatch):
    """Waiting for trust must not block the keystrokes that answer the prompt."""
    h = _RunHarness(tmp_path, session_id="session_x")
    h.start(monkeypatch)
    try:
        assert h.wait_for(lambda e: e.get("type") == "pan.native_trust_required", 5)
        h.stdin.feed({"type": "user", "message": {"content": [
            {"type": "text", "text": "pan prompt"}]}})
        time.sleep(0.4)
        # Never pasted into the trust screen.
        assert h.backend.writes == []

        h.stdin.feed({"type": "control_mode", "mode": "native", "generation": 0,
                      "requestId": "m1"})
        assert h.wait_for(lambda e: e.get("requestId") == "m1", 2.0) is not None

        h.trust()
        assert h.wait_for(
            lambda e: e.get("type") == "pan.native_busy" and e.get("busy") is False, 3)
        time.sleep(0.4)
        # Terminal ownership still wins: the prompt waits, nothing is typed.
        assert h.backend.writes == []

        h.stdin.feed({"type": "control_mode", "mode": "pan", "generation": 1,
                      "requestId": "m2"})
        assert h.wait_for(lambda e: e.get("requestId") == "m2", 2.0) is not None
        result = h.wait_for(lambda e: e.get("role") == "result", 3.0)
        assert result is not None and result["kimi_stop_reason"] == "input-rejected"
        assert h.backend.writes[0] == b"\x1b[200~pan prompt\x1b[201~"
        assert h.backend.writes[-1] == b"\r"
    finally:
        h.stop()
    assert h.backend.terminated == [True]
    assert h.backend.closed is True


def test_run_reports_pending_prompt_when_tui_exits_before_trust(tmp_path, monkeypatch):
    h = _RunHarness(tmp_path)
    h.start(monkeypatch)
    try:
        assert h.wait_for(lambda e: e.get("type") == "pan.native_trust_required", 5)
        h.stdin.feed({"type": "user", "message": {"content": [
            {"type": "text", "text": "never pasted"}]}})
        time.sleep(0.4)
        h.backend.alive_flag = False
        result = h.wait_for(lambda e: e.get("role") == "result", 5.0)
        assert result is not None and result["kimi_stop_reason"] == "tui-closed"
        assert h.wait_for(lambda e: e.get("type") == "pan.native_error", 2.0) is not None
        assert h.backend.writes == []
    finally:
        h.stop()
    assert h.backend.terminated == [True]
    assert h.backend.closed is True


# ── adapter + capability gates ─────────────────────────────────────────────


def _session(**kw):
    return _sess.Session(id="ses_native", name="native", adapter="kimi", **kw)


def test_native_adapter_capabilities_are_honest():
    a = KimiNativeAdapter()
    assert a.supports_native_control is True
    assert a.supports_native_interrupt is True
    assert a.interrupt_via_native_control is True
    # kimi queues mid-turn input as the next turn; no steer channel exists.
    assert a.supports_native_steer is False
    assert a.supports_steer_receipt is False
    assert a.execution_modes == ["stream"]


def test_native_adapter_spawn_args_point_at_bridge():
    a = KimiNativeAdapter()
    s = _session(workdir="C:/work", adapter_config={"cli_session_id": "session_x"})
    args = a.build_spawn_args(s)
    assert args[0] == sys.executable
    assert args[2].endswith("native_bridge.py")
    assert "--kimi-path" in args
    assert args[args.index("--cwd") + 1] == "C:/work"
    assert args[args.index("--session-id") + 1] == "session_x"
    assert "--model" in args


def test_native_adapter_agent_file_only_for_fresh_sessions(tmp_path, monkeypatch):
    a = KimiNativeAdapter()
    fresh = _session(workdir=str(tmp_path), system_prompt="You are PAN.",
                     adapter_config={})
    args = a.build_spawn_args(fresh)
    assert "--agent-file" in args
    resumed = _session(workdir=str(tmp_path), system_prompt="You are PAN.",
                       adapter_config={"cli_session_id": "session_x"})
    args2 = a.build_spawn_args(resumed)
    assert "--agent-file" not in args2, "kimi forbids --agent-file with -S"


def test_native_adapter_user_message_shape():
    payload = json.loads(KimiNativeAdapter().encode_user_message("hi"))
    assert payload == {"type": "user", "message": {"content": [{"type": "text", "text": "hi"}]}}


def test_native_transport_selection_is_explicit():
    """worker picks the PTY bridge per adapter; codex keeps its own adapter."""
    from packages.core import worker

    kimi = worker._native_transport_adapter(get_adapter("kimi"))
    assert isinstance(kimi, KimiNativeAdapter)
    assert kimi.name == "kimi"
    assert worker._native_transport_adapter(get_adapter("cbc")) is not None
    # codex's native transport is the adapter itself (app-server bridge).
    assert worker._native_transport_adapter(get_adapter("codex")) is None
    assert worker._native_transport_adapter(get_adapter("claude")) is None


def test_capability_gate_is_data_driven():
    assert adapter_supports_native_control("codex") is True
    assert adapter_supports_native_control("cbc") is True
    assert adapter_supports_native_control("kimi") is True
    assert adapter_supports_native_control("claude") is False
    assert adapter_supports_native_control("opencode") is False
    assert adapter_supports_native_control("nope") is False
    # The stream adapter declares the capability too (the native transport is
    # selected per-session), but never advertises steer.
    assert get_adapter("kimi").supports_native_control is True
    assert get_adapter("kimi").supports_native_steer is False
