"""One resident Kimi Code TUI under a PTY, with wire.jsonl as the fact channel.

Why not hooks: kimi-code exposes no hook surface (unlike cbc). The structured
facts Pan needs — turn start/end, prompt origin, assistant text, tool calls,
terminal reason — are all journaled by kimi itself in
``<KIMI_CODE_HOME>/sessions/<ws>/<session>/agents/main/wire.jsonl``. This
bridge therefore never infers a turn from PTY bytes; PTY output is presentation
only (terminal view), exactly like cbc's raw ANSI rule.

Verified against kimi 2.3.1 (2026-10-24):
- ``kimi -S <id>`` renders a full TUI under ConPTY; bracketed paste + Enter
  submits a multi-line prompt in one turn.
- Mid-turn typed input is **queued as the next turn** (wire ``turn.prompt``
  appears only after the running ``turn.ended``) — kimi has no steer channel.
- Ctrl+C cancels the running turn: wire ``turn.cancel`` → ``turn.step.interrupted``
  → ``turn.ended`` (``reason: "cancelled"``).
- Workspace trust is a file marker (``<home>/workspace-trust/*`` with
  ``{"root": ...}``); the TUI blocks on the prompt until it exists.
- One session has exactly one owning CLI process: ``kimi web`` answers 40931 and
  ACP ``session/resume`` returns "in use by another Kimi Code CLI instance"
  while this TUI holds it. The bridge is that single owner.

Controls (stdin, one JSON object per line) — same vocabulary as cbc/codex:
  user{message.content[].text}, control_mode{mode,generation}, native_input{text},
  native_resize{rows,cols}, interrupt{}
Events (stdout JSONL):
  pan.native_ready / pan.native_trust_required / pan.native_output /
  pan.native_busy / pan.native_error / pan.native_user / pan.control_mode /
  pan.native_write, plus the ordinary kimi-shaped assistant / result / meta
  events consumed by KimiAdapter.parse_event.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import queue
import sys
import threading
import time
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from packages.core.adapters.codex.control_owner import ControlOwner, ControlRejected  # noqa: E402

# kimi's React input commits bracketed paste on a later render; Enter in the
# same write batch can submit the previous box content (same defect cbc hit).
_PASTE_SETTLE_SECONDS = 0.2
_SUBMIT_TIMEOUT_SECONDS = 20.0
_SUBMIT_RETRY_TIMEOUT_SECONDS = 5.0


def _home_dir(kimi_home: str | None) -> Path:
    if kimi_home:
        return Path(kimi_home)
    return Path.home() / ".kimi-code"


def workspace_trusted(home: Path, cwd: str) -> bool:
    """True when kimi already trusts *cwd* (file marker keyed by root).

    kimi writes ``workspace-trust/<workspace-id>`` = ``{"root": <abs path>,
    "trustedAt": <epoch ms>}``; the file name is opaque, so match by root the
    same way kimi resolves it. Absence is not an error — the TUI shows its
    trust prompt, and Pan refuses pan-mode submits until it is answered.
    """
    trust_dir = home / "workspace-trust"
    if not trust_dir.is_dir():
        return False
    target = os.path.normcase(os.path.abspath(cwd))
    for marker in trust_dir.iterdir():
        try:
            data = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        root = data.get("root")
        if isinstance(root, str) and os.path.normcase(os.path.abspath(root)) == target:
            return True
    return False


class WireTurnTracker:
    """Turn facts from wire.jsonl events; emits Pan events (no PTY guessing).

    ``mode`` mirrors the control owner: in native mode a user-origin prompt is
    reported as ``pan.native_user`` so the worker records the typed message;
    pan-mode prompts are Pan's own queue items and already recorded there.
    """

    def __init__(self, emit, *, is_native_mode):
        self.emit = emit
        self.is_native_mode = is_native_mode
        self.turn_id = None
        self.text: list[str] = []
        self.thinking: list[str] = []
        self.tools: list[dict] = []
        self.pending_submit: str | None = None
        self.busy = False

    # ── submit receipt ──
    def expect_submit(self, text: str) -> None:
        self.pending_submit = text

    def _match_submit(self, text: str) -> bool:
        if self.pending_submit is None:
            return False
        if text.strip() == self.pending_submit.strip():
            self.pending_submit = None
            return True
        return False

    # ── event handling ──
    def feed(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "turn.prompt":
            self._on_prompt(event)
        elif kind == "agent.turn.started":
            self._on_started(event)
        elif kind == "context.append_loop_event":
            self._on_loop(event)
        elif kind == "turn.ended":
            self._on_ended(event)

    def _text_of(self, event: dict) -> str:
        parts = event.get("input") or []
        out = []
        if isinstance(parts, list):
            for part in parts:
                if isinstance(part, dict) and part.get("type") == "text":
                    out.append(str(part.get("text") or ""))
        return "".join(out)

    def _on_prompt(self, event: dict) -> None:
        origin = (event.get("origin") or {}).get("kind")
        if origin != "user":
            return
        text = self._text_of(event)
        turn = event.get("turnId")
        if isinstance(turn, int):
            self.turn_id = turn
        matched = self._match_submit(text)
        if not matched and self.is_native_mode() and text:
            self.emit({"type": "pan.native_user", "text": text, "item_id": event.get("promptId"),
                       "turn_id": turn})
        if self.turn_id is None:
            self.turn_id = turn
        self._begin_busy()

    def _on_started(self, event: dict) -> None:
        turn = event.get("turnId")
        if isinstance(turn, int):
            self.turn_id = turn
        self._begin_busy()

    def _begin_busy(self) -> None:
        if not self.busy:
            self.busy = True
            self.emit({"type": "pan.native_busy", "busy": True})

    def _on_loop(self, event: dict) -> None:
        inner = event.get("event") or {}
        sub = inner.get("type")
        if sub == "content.part":
            part = inner.get("part") or {}
            ptype = part.get("type")
            if ptype == "text":
                text = str(part.get("text") or "")
                if text:
                    self.text.append(text)
            elif ptype == "think":
                text = str(part.get("think") or "")
                if text:
                    self.thinking.append(text)
        elif sub == "tool.call":
            name = str(inner.get("name") or "tool")
            args = inner.get("args") or {}
            if len(json.dumps(args, ensure_ascii=False)) > 16000:
                args = {"truncated": True}
            self.tools.append({"name": name, "input": args if isinstance(args, dict) else {"value": args}})

    def _on_ended(self, event: dict) -> None:
        reason = str(event.get("reason") or "")
        text = "".join(self.text).strip()
        blocks: list[dict] = []
        if text:
            blocks.append({"type": "text", "text": text})
        thinking = "".join(self.thinking).strip()
        if thinking:
            blocks.append({"type": "think", "think": thinking})
        for tool in self.tools:
            blocks.append({"type": "tool_use", "name": tool["name"], "input": tool["input"]})
        if blocks:
            self.emit({"role": "assistant", "content": blocks})
        self.emit({
            "role": "result",
            "is_error": reason not in {"completed", "cancelled"},
            "cancelled": reason == "cancelled",
            "result": text,
            "turn_id": event.get("turnId"),
            "kimi_stop_reason": reason,
        })
        self.turn_id = None
        self.text, self.thinking, self.tools = [], [], []
        if self.busy:
            self.busy = False
            self.emit({"type": "pan.native_busy", "busy": False})


class KimiNativeBridge:
    def __init__(self, backend, emit, *, kimi_home: str | None, cwd: str):
        self.backend = backend
        self.emit = emit
        self.home = _home_dir(kimi_home)
        self.cwd = cwd
        self.owner = ControlOwner()
        self.lock = threading.RLock()
        # Transport readiness (PTY spawned) vs workspace trust (kimi's gate).
        # The terminal view must open before trust is answered, so `ready` is
        # announced at spawn and only prompt submission waits for `trusted`.
        self.ready = threading.Event()
        self.trusted = threading.Event()
        # Transport-only readiness: the TUI has painted and gone quiet. This
        # never infers turn state (that stays wire.jsonl); it only avoids
        # pasting into a still-booting screen.
        self.settled = threading.Event()
        self.last_output = 0.0
        self.output_bytes = 0
        self.tracker = WireTurnTracker(emit, is_native_mode=lambda: self.owner.snapshot()["mode"] == "native")
        self.input_uncertain = False

    # ── PTY write primitives ──
    def _write(self, data: bytes) -> None:
        previous = self.input_uncertain
        self.input_uncertain = True
        if not previous:
            self.emit({"type": "pan.native_input_state", "uncertain": True})
        written = self.backend.write(data)
        if written != len(data):
            raise ControlRejected("PTY input acceptance is unknown")
        self.input_uncertain = previous
        if not previous:
            self.emit({"type": "pan.native_input_state", "uncertain": False})

    def note_output(self, size: int) -> None:
        """Called by the PTY drain thread with raw byte count."""
        self.output_bytes += size
        self.last_output = time.monotonic()

    def await_settled(self, timeout: float = 30.0, quiet: float = 1.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if (self.output_bytes >= 512 and self.last_output
                    and time.monotonic() - self.last_output >= quiet):
                self.settled.set()
                return True
            time.sleep(0.1)
        return False

    def submit(self, text: str) -> None:
        """Submit a Pan-origin prompt; confirmed by the wire prompt echo."""
        if not isinstance(text, str) or not text.strip() or "\x1b" in text:
            raise ControlRejected("Invalid Kimi prompt")
        if not self.await_settled():
            raise ControlRejected("Kimi TUI did not finish starting")
        with self.lock:
            if (not self.trusted.is_set() or self.tracker.busy
                    or self.tracker.pending_submit is not None or self.input_uncertain):
                raise ControlRejected(
                    "Kimi TUI is not ready (workspace trust pending or turn busy)")
            if self.owner.snapshot()["mode"] != "pan":
                raise ControlRejected("Kimi input is owned by the terminal")

            def deliver():
                self.tracker.expect_submit(text)
                # Bracketed paste preserves inner newlines as one prompt.
                self._write(b"\x1b[200~" + text.encode("utf-8") + b"\x1b[201~")
                time.sleep(_PASTE_SETTLE_SECONDS)
                self._write(b"\r")

            try:
                self.owner.write("pan", deliver)
            except Exception:
                self.tracker.pending_submit = None
                raise
        # Receipt wait happens outside the control lock so interrupt/resize
        # remain serviceable while the TUI renders the submitted box.
        if not self._await_submit_receipt(_SUBMIT_TIMEOUT_SECONDS):
            # A first Enter can be eaten while the input box is still
            # committing the paste; one retry is safe (text is already in).
            with self.lock:
                if self.tracker.pending_submit is not None:
                    self.owner.write("pan", lambda: self._write(b"\r"))
            if not self._await_submit_receipt(_SUBMIT_RETRY_TIMEOUT_SECONDS):
                raise ControlRejected(
                    "Kimi TUI did not confirm the prompt (startup/trust pending or turn busy)")

    def _await_submit_receipt(self, timeout: float) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.tracker.pending_submit is None:
                return True
            time.sleep(0.1)
        return False

    # ── control channel ──
    def control(self, control: dict) -> None:
        kind = control.get("type")
        receipt = "pan.control_mode" if kind == "control_mode" else "pan.native_write"
        with self.lock:
            try:
                if kind == "control_mode":
                    self.owner.switch(control.get("mode"), control.get("generation"))
                elif kind == "native_input":
                    generation = control.get("generation")
                    if type(generation) is not int:
                        raise ControlRejected("Invalid native generation")
                    text = control.get("text", "")
                    if not isinstance(text, str) or len(text.encode()) > 32768:
                        raise ControlRejected("Invalid native input")
                    if text:
                        self.owner.write("native", lambda: self._write(text.encode()),
                                         generation=generation)
                elif kind == "native_resize":
                    generation = control.get("generation")
                    rows, cols = control.get("rows"), control.get("cols")
                    if (type(generation) is not int or type(rows) is not int or type(cols) is not int
                            or not 1 <= rows <= 500 or not 1 <= cols <= 1000):
                        raise ControlRejected("Invalid terminal size")
                    self.owner.write("native", lambda: self.backend.resize(rows, cols),
                                     generation=generation)
                elif kind == "interrupt":
                    # Ctrl+C is kimi's documented in-TUI cancel; the wire
                    # turn.ended(reason=cancelled) is the real receipt.
                    self.owner.write("pan", lambda: self._write(b"\x03"),
                                     generation=control.get("generation"))
                else:
                    raise ControlRejected("Unsupported Kimi native operation")
                self.emit({"type": receipt, "ok": True, "requestId": control.get("requestId"),
                           **self.owner.snapshot()})
            except Exception as error:  # noqa: BLE001 - receipt must always answer
                self.emit({"type": receipt, "ok": False, "requestId": control.get("requestId"),
                           "error": str(error), **self.owner.snapshot()})


# ── wire tail ──────────────────────────────────────────────────────────────


def find_wire_file(home: Path, session_id: str) -> Path | None:
    for path in home.glob(f"sessions/wd_*/{session_id}/agents/main/wire.jsonl"):
        return path
    return None


def discover_session_id(home: Path, cwd: str, since_ms: int) -> str | None:
    """Newest session for *cwd* created after *since_ms* (fresh TUI launch)."""
    target = os.path.normcase(os.path.abspath(cwd))
    best: tuple[int, str] | None = None
    for state_path in home.glob("sessions/wd_*/session_*/state.json"):
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        root = state.get("cwd")
        created = state.get("createdAt")
        if not isinstance(root, str) or os.path.normcase(os.path.abspath(root)) != target:
            continue
        if not isinstance(created, int) or created < since_ms - 2000:
            continue
        sid = state.get("id") or state_path.parent.name
        if best is None or created > best[0]:
            best = (created, sid)
    return best[1] if best else None


def wire_tail_loop(bridge: KimiNativeBridge, session_id: str, stop: threading.Event,
                   initial_offset: int = 0) -> None:
    """Tailing reader: only lines written after the bridge started are fed.

    *initial_offset* is the wire size captured at spawn: historical turns are
    the sessions provider's business (history/usage), and replaying them here
    would fabricate results and native-user rows for turns Pan never owned.
    A session whose wire file does not exist yet starts at 0 — every line then
    belongs to a turn this bridge observes.
    """
    offset = initial_offset
    path: Path | None = None
    while not stop.is_set():
        if path is None or not path.exists():
            path = find_wire_file(bridge.home, session_id)
            if path is None:
                time.sleep(0.2)
                continue
        try:
            with open(path, "rb") as handle:
                handle.seek(offset)
                chunk = handle.read()
                offset = handle.tell()
        except OSError:
            time.sleep(0.2)
            continue
        if not chunk:
            time.sleep(0.15)
            continue
        for raw in chunk.splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                bridge.tracker.feed(event)


def ready_watch_loop(bridge: KimiNativeBridge, stop: threading.Event) -> None:
    """Workspace trust is a file fact; never scraped from the PTY.

    A paste into the trust prompt would corrupt that UI, so Pan refuses
    pan-mode submits until the marker exists. Input readiness itself is PTY
    quiescence (see ``await_settled``), which says nothing about turn state.
    """
    announced_trust = False
    while not stop.is_set():
        if workspace_trusted(bridge.home, bridge.cwd):
            if not bridge.trusted.is_set():
                bridge.trusted.set()
                # Startup reservation ends once the TUI is usable, mirroring
                # cbc's first SessionStart (a running turn keeps it busy).
                if not bridge.tracker.busy:
                    bridge.emit({"type": "pan.native_busy", "busy": False})
            return
        if not announced_trust:
            announced_trust = True
            bridge.emit({"type": "pan.native_trust_required", "root": bridge.cwd})
        time.sleep(0.25)


def run(kimi_path: str, cwd: str, session_id: str | None, kimi_home: str | None,
        model: str | None, agent_file: str | None = None,
        initial_size: tuple[int, int] = (32, 110)) -> None:
    from packages.core.terminal.backend import ConPtyBackend

    output_lock = threading.Lock()

    def emit(event: dict) -> None:
        with output_lock:
            sys.stdout.write(json.dumps(event, ensure_ascii=True, separators=(",", ":")) + "\n")
            sys.stdout.flush()

    home = _home_dir(kimi_home)
    env = dict(os.environ, TERM="xterm-256color")
    if kimi_home:
        env["KIMI_CODE_HOME"] = str(kimi_home)
    argv = [kimi_path]
    if session_id:
        argv.extend(["-S", session_id])
    if model:
        argv.extend(["-m", model])
    if agent_file and not session_id:
        # kimi forbids --agent-file together with -S/--continue.
        argv.extend(["--agent-file", agent_file])

    started_ms = int(time.time() * 1000)
    backend = ConPtyBackend.spawn(argv, cwd=cwd, env=env, rows=initial_size[0], cols=initial_size[1])
    stop = threading.Event()
    bridge = KimiNativeBridge(backend, emit, kimi_home=kimi_home, cwd=cwd)
    try:
        # Transport is ready before the TUI finishes booting; prompt submission
        # additionally waits for the workspace trust marker (see submit()).
        bridge.ready.set()
        emit({"type": "pan.native_busy", "busy": True})
        emit({"type": "pan.native_ready"})

        def drain():
            while not stop.is_set():
                try:
                    data = backend.read(65536, timeout=0.2)
                except Exception:  # noqa: BLE001
                    if not stop.is_set():
                        emit({"type": "pan.native_error", "error": "Kimi PTY output unavailable"})
                        stop.set()
                    return
                if data:
                    bridge.note_output(len(data))
                    emit({"type": "pan.native_output",
                          "data": base64.b64encode(data).decode("ascii")})
                elif not backend.alive():
                    emit({"type": "pan.native_error", "error": "Kimi TUI exited"})
                    stop.set()
                    return

        threading.Thread(target=drain, daemon=True).start()

        sid = session_id
        if not sid:
            deadline = time.time() + 20
            while time.time() < deadline and not stop.is_set():
                sid = discover_session_id(home, cwd, started_ms)
                if sid:
                    break
                time.sleep(0.2)
            if not sid:
                emit({"type": "pan.native_error", "error": "Kimi TUI session was not created"})
                return
            # Same init shape as stream mode: the worker persists cli_session_id.
            emit({"role": "meta", "type": "session.resume_hint", "session_id": sid})

        # Baseline for the fact channel: skip everything already on disk.
        existing_wire = find_wire_file(home, sid)
        wire_offset = existing_wire.stat().st_size if existing_wire is not None else 0
        threading.Thread(target=wire_tail_loop, args=(bridge, sid, stop, wire_offset),
                         daemon=True).start()
        threading.Thread(target=ready_watch_loop, args=(bridge, stop), daemon=True).start()

        inbox: queue.Queue = queue.Queue()

        def read_input():
            for line in sys.stdin:
                inbox.put(line)
            inbox.put(None)

        threading.Thread(target=read_input, daemon=True).start()
        while not stop.is_set():
            try:
                line = inbox.get(timeout=0.2)
            except queue.Empty:
                continue
            if line is None:
                break
            try:
                message = json.loads(line)
                if not isinstance(message, dict):
                    raise ValueError("Invalid bridge message")
                if message.get("type") == "user":
                    content = (message.get("message") or {}).get("content") or []
                    text = "".join(part.get("text", "") for part in content
                                   if isinstance(part, dict) and part.get("type") == "text")
                    if not bridge.trusted.wait(30):
                        raise ControlRejected(
                            "Kimi workspace trust is pending — answer the prompt in the Terminal TUI")
                    try:
                        bridge.submit(text)
                    except Exception as error:  # noqa: BLE001 - a failed task must terminate
                        if bridge.tracker.busy:
                            # A late echo means the prompt did get in (queued or
                            # running); its turn result is the authoritative end.
                            continue
                        emit({"role": "result", "is_error": True, "cancelled": False,
                              "result": f"Kimi TUI did not accept the prompt: {error}",
                              "kimi_stop_reason": "input-rejected"})
                else:
                    bridge.control(message)
            except Exception:  # noqa: BLE001 - control errors already answered by receipt
                continue
    finally:
        stop.set()
        backend.terminate(force=True)
        try:
            backend.close()
        except Exception:  # noqa: BLE001 - closing is best effort at shutdown
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Kimi Code TUI bridge for Pan")
    parser.add_argument("--kimi-path", required=True)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--session-id", default="")
    parser.add_argument("--kimi-home", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--agent-file", default="")
    args = parser.parse_args()
    run(args.kimi_path, args.cwd, args.session_id or None, args.kimi_home or None,
        args.model or None, args.agent_file or None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
