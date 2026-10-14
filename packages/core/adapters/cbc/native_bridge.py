"""One resident CBC native TUI with an independent structured hook channel.

The PTY owns the only Agent kernel. Control switches never spawn/resume it.
Stop supplies text; only FinalStop supplies the terminal reason. Raw ANSI is
presentation and never used to infer a turn, permission, or exit state.
"""
from __future__ import annotations

import argparse
import base64
from collections import deque
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import queue
import re
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import threading
import time

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from packages.core.adapters.codex.control_owner import ControlOwner, ControlRejected
from packages.core.adapters.cbc.native_hook import HOOK_REJECTIONS


class NativeBridge:
    def __init__(self, backend, emit, session_id):
        self.backend, self.emit, self.session_id = backend, emit, session_id
        self.owner = ControlOwner()
        self.lock = threading.RLock()
        self.turn = None
        self.turn_aliases = set()
        self.pending_prompt = None
        self.pending_origin = None
        self.draft_unknown = False
        self.awaiting_native_submit = False
        self.permission_pending = False
        self.input_uncertain = False
        self.last_text = {}
        self.finished = deque(maxlen=256)
        self.ready = threading.Event()

    def _write(self, data):
        # Called inside owner.write's admitted operation. Validation/epoch
        # rejection never reaches here; any backend attempt can accept bytes
        # before throwing. Only a full receipt can release this attempt, and
        # it cannot release uncertainty left by an earlier write.
        previously_uncertain = self.input_uncertain
        self.input_uncertain = True
        if not previously_uncertain:
            self.emit({"type": "pan.native_input_state", "uncertain": True})
        written = self.backend.write(data)
        if written != len(data):
            raise ControlRejected("PTY input acceptance is unknown")
        self.input_uncertain = previously_uncertain
        if not previously_uncertain:
            self.emit({"type": "pan.native_input_state", "uncertain": False})

    def submit(self, text):
        if not isinstance(text, str) or not text or "\x1b" in text:
            raise ControlRejected("Invalid CBC prompt")
        try:
            encoded = text.encode("utf-8")
        except UnicodeError:
            raise ControlRejected("Invalid CBC prompt") from None
        with self.lock:
            if (not self.ready.is_set() or self.turn or self.pending_prompt is not None
                    or self.draft_unknown or self.awaiting_native_submit or self.input_uncertain):
                raise ControlRejected("CBC input is not available")
            # Persist uncertainty in memory before touching the PTY. A partial
            # write may have submitted the prompt; never automatically resend.
            def deliver():
                self.pending_prompt = text
                self.pending_origin = "pan"
                self._write(b"\x1b[200~" + encoded + b"\x1b[201~")
                # CBC's React input commits bracketed paste on a later
                # render. Enter in the same input batch can submit the old
                # box. The submit hook remains the authoritative receipt.
                time.sleep(.2)
                self._write(b"\r")
            self.owner.write("pan", deliver)

    def control(self, control):
        kind = control.get("type")
        receipt = "pan.control_mode" if kind == "control_mode" else "pan.native_write"
        with self.lock:
            try:
                if kind == "control_mode":
                    if control.get("mode") == "pan" and (
                            self.input_uncertain or self.draft_unknown
                            or (self.awaiting_native_submit and self.turn)):
                        raise ControlRejected("Finish native input before handing back control")
                    self.owner.switch(control.get("mode"), control.get("generation"))
                elif kind in {"native_input", "native_resize"}:
                    if type(control.get("generation")) is not int:
                        raise ControlRejected("Invalid native generation")
                    text = control.get("text", "")
                    if not isinstance(text, str) or len(text.encode()) > 32768:
                        raise ControlRejected("Invalid native input")
                    resize = "rows" in control or "cols" in control
                    if kind == "native_resize" and not resize:
                        raise ControlRejected("Invalid terminal size")
                    if resize:
                        rows, cols = control.get("rows"), control.get("cols")
                        if (type(rows) is not int or type(cols) is not int
                                or not 1 <= rows <= 500 or not 1 <= cols <= 1000):
                            raise ControlRejected("Invalid terminal size")
                    if text and kind == "native_input":
                        # While a turn runs, raw keys answer its resident approval
                        # or cancel it. At idle they may leave a draft. Unknown
                        # editing sequences cannot be mistaken for an empty box.
                        def deliver():
                            focus_report = re.fullmatch(r"(?:\x1b\[[IO])+", text) is not None
                            if (self.ready.is_set() and not self.permission_pending
                                and text != "\x1b" and not focus_report):
                                if "\r" in text or "\n" in text:
                                    self.draft_unknown = False
                                    self.awaiting_native_submit = True
                                    self.emit({"type": "pan.native_busy", "busy": True})
                                else:
                                    self.draft_unknown = True
                            self._write(text.encode())
                        self.owner.write("native", deliver, generation=control.get("generation"))
                    if resize:
                        self.owner.write("native", lambda: self.backend.resize(rows, cols),
                                         generation=control.get("generation"))
                    elif not text:
                        self.owner.write("native", lambda: None, generation=control.get("generation"))
                elif kind == "interrupt":
                    self.owner.write("pan", lambda: self._write(b"\x1b"),
                                     generation=control.get("generation"))
                else:
                    raise ControlRejected("Unsupported CBC native operation")
                self.emit({"type": receipt, "ok": True, "requestId": control.get("requestId"),
                           **self.owner.snapshot()})
            except Exception as error:
                self.emit({"type": receipt, "ok": False, "requestId": control.get("requestId"),
                           "error": str(error), **self.owner.snapshot()})

    def hook(self, event):
        with self.lock:
            name = event.get("hook_event_name")
            sid = event.get("session_id")
            if sid is not None and sid != self.session_id:
                raise ControlRejected("Wrong CBC Session")
            run = event.get("generation_id")
            if name == "SessionStart":
                first_start = not self.ready.is_set()
                self.ready.set()
                self.emit({"type": "system", "subtype": "init", "session_id": self.session_id,
                           "model": event.get("model")})
                if first_start:
                    self.emit({"type": "pan.native_busy", "busy": False})
            elif name == "UserPromptSubmit":
                if not isinstance(run, str) or not run or run in self.finished:
                    raise ControlRejected("Missing or stale CBC turn identity")
                if event.get("is_internal_continuation"):
                    if self.turn is None:
                        raise ControlRejected("Continuation has no active CBC turn")
                    self.turn_aliases.add(run)
                    return {}
                if self.turn is not None and self.turn != run:
                    raise ControlRejected("Another CBC turn is active")
                if self.pending_origin == "pan":
                    if event.get("prompt") != self.pending_prompt:
                        raise ControlRejected("CBC prompt receipt does not match its input")
                    self.pending_prompt = self.pending_origin = None
                else:
                    if self.owner.snapshot()["mode"] != "native" and not self.awaiting_native_submit:
                        raise ControlRejected("Native submit has no input owner")
                    self.emit({"type": "pan.native_turn_started"})
                    self.emit({"type": "pan.native_user", "text": event.get("prompt", "")})
                self.turn = run
                self.turn_aliases = {run}
                self.awaiting_native_submit = self.draft_unknown = False
            elif name == "Notification" and event.get("notification_type") == "permission_prompt":
                self.permission_pending = True
                # The existing TUI remains the sole approval surface; returning
                # to Pan does not resolve or cancel its waiting request.
                self.emit({"type": "cbc.permission_pending", "pending": True})
            elif name in {"PreToolUse", "PostToolUse"} and run in self.turn_aliases:
                call = event.get("call_id") or event.get("tool_use_id")
                if not isinstance(call, str) or not call:
                    return {}
                args = dict(event.get("tool_input") or {})
                args["status"] = "running" if name == "PreToolUse" else "completed"
                if name == "PostToolUse":
                    args["result"] = event.get("tool_response")
                    self.permission_pending = False
                if len(json.dumps(args, ensure_ascii=True)) > 128 * 1024:
                    args = {"status": args["status"], "truncated": True,
                            "preview": json.dumps(args, ensure_ascii=False)[:16000]}
                self.emit({"type": "assistant", "item_id": call, "delta": name == "PreToolUse",
                           "replace": True, "message": {"content": [{"type": "tool_use",
                           "id": call, "name": event.get("tool_name"), "input": args}]}})
            elif name == "Stop" and run in self.turn_aliases:
                self.last_text[self.turn] = str(event.get("last_assistant_message") or "")
            elif name == "FinalStop" and run in self.turn_aliases and run not in self.finished:
                reason = event.get("final_stop_reason")
                text = self.last_text.pop(self.turn, "")
                truncated = len(text) > 16000
                if truncated:
                    text = text[:16000] + "\n[Structured response truncated; see CBC Terminal TUI]"
                if text:
                    self.emit({"type": "assistant", "item_id": run,
                               "message": {"content": [{"type": "text", "text": text}]}})
                self.finished.extend(self.turn_aliases)
                self.turn = None
                self.turn_aliases.clear()
                self.permission_pending = False
                self.emit({"type": "result", "result": text, "session_id": self.session_id,
                           "is_error": reason not in {"completed", "cancelled"},
                           "cancelled": reason == "cancelled", "cbc_stop_reason": reason,
                           "cbc_truncated": truncated})
            return {}


def hook_handler(get_bridge, token):
    """Bind one listener to one capability and resident kernel."""
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            if self.path != "/hook" or not secrets.compare_digest(
                    self.headers.get("Authorization", ""), "Bearer " + token):
                self.send_error(403)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 1024 * 1024:
                    raise ValueError("Invalid hook size")
                event = json.loads(self.rfile.read(length))
                bridge = get_bridge()
                if not isinstance(event, dict) or bridge is None:
                    raise ValueError("Invalid hook")
                result = bridge.hook(event)
                data = json.dumps(result).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            except (ValueError, TypeError, ControlRejected) as error:
                code = HOOK_REJECTIONS.get(str(error), "invalid_hook") if isinstance(error, ControlRejected) else "invalid_hook"
                data = json.dumps({"error": code}).encode()
                self.send_response(409)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
    return Handler


def run(cli_argv, cwd, session_id):
    from packages.core.terminal.backend import ConPtyBackend
    output_lock = threading.Lock()

    def emit(event):
        with output_lock:
            sys.stdout.write(json.dumps(event, ensure_ascii=True, separators=(",", ":")) + "\n")
            sys.stdout.flush()

    token = secrets.token_urlsafe(32)
    bridge = None

    stop = threading.Event()
    with ExitStack() as cleanup:
        server = ThreadingHTTPServer(("127.0.0.1", 0), hook_handler(lambda: bridge, token))
        server.daemon_threads = True
        cleanup.callback(server.server_close)
        directory = cleanup.enter_context(tempfile.TemporaryDirectory(prefix="pan-cbc-bridge-"))
        argv = list(cli_argv)
        settings = {}
        # Merge adapter-owned settings without overriding its thinking policy.
        while "--settings" in argv:
            index = argv.index("--settings")
            raw = argv[index + 1]
            supplied = json.loads(raw if raw.lstrip().startswith("{") else Path(raw).read_text(encoding="utf-8"))
            if not isinstance(supplied, dict):
                raise ValueError("CBC native settings must be an object")
            settings.update(supplied)
            del argv[index:index + 2]
        hooks = settings.setdefault("hooks", {})
        command = subprocess.list2cmdline([sys.executable.replace("\\", "/"),
                                          Path(__file__).with_name("native_hook.py").as_posix()])
        for name in ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse",
                     "Stop", "FinalStop", "Notification"):
            hooks.setdefault(name, []).append({"hooks": [{"type": "command", "command": command, "timeout": 12}]})
        settings_path = Path(directory) / "settings.json"
        settings_path.write_text(json.dumps(settings), encoding="utf-8")
        argv.extend(["--settings", str(settings_path)])
        env = dict(os.environ, TERM="xterm-256color",
                   PAN_CBC_HOOK_URL=f"http://127.0.0.1:{server.server_port}/hook",
                   PAN_CBC_HOOK_TOKEN=token)
        backend = ConPtyBackend.spawn(argv, cwd=cwd, env=env, rows=30, cols=120)
        # Independent callbacks run even if termination or close raises. The
        # backend owns this exclusive Job; closing it also retires tool children.
        cleanup.callback(backend.close)
        guard = getattr(backend, "guard", None)
        if guard is not None:
            def close_guard():
                if guard.close() is False:
                    raise RuntimeError("CBC owned Job cleanup was not confirmed")
            cleanup.callback(close_guard)
        cleanup.callback(backend.terminate, force=True)
        bridge = NativeBridge(backend, emit, session_id)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        cleanup.callback(server.shutdown)
        cleanup.callback(stop.set)
        # Control must be available before SessionStart: the real native CLI
        # can require workspace trust confirmation before Agent initialization.
        # This acknowledges the transport only; submit still waits for its hook.
        emit({"type": "pan.native_busy", "busy": True})
        emit({"type": "pan.native_ready"})

        def drain():
            while not stop.is_set():
                try:
                    data = backend.read(65536, timeout=.2)
                    if data:
                        emit({"type": "pan.native_output", "data": base64.b64encode(data).decode()})
                    elif not backend.alive():
                        emit({"type": "pan.native_error", "error": "CBC TUI exited"})
                        stop.set()
                        return
                except Exception:
                    emit({"type": "pan.native_error", "error": "CBC PTY output unavailable"})
                    stop.set()
                    return

        threading.Thread(target=drain, daemon=True).start()
        inbox = queue.Queue()

        def read_input():
            for line in sys.stdin:
                inbox.put(line)
            inbox.put(None)

        threading.Thread(target=read_input, daemon=True).start()
        while not stop.is_set():
            try:
                line = inbox.get(timeout=.2)
            except queue.Empty:
                continue
            if line is None:
                break
            try:
                message = json.loads(line)
                if not isinstance(message, dict):
                    raise ValueError("Invalid bridge message")
                if message.get("type") == "user":
                    if not bridge.ready.wait(30):
                        raise ControlRejected("CBC TUI startup is unconfirmed")
                    content = message.get("message", {}).get("content", [])
                    bridge.submit("".join(part.get("text", "") for part in content if part.get("type") == "text"))
                else:
                    bridge.control(message)
            except Exception:
                emit({"type": "pan.native_error", "error": "CBC input was not confirmed"})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cli-argv", required=True)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--session-id", required=True)
    args = parser.parse_args()
    run(json.loads(args.cli_argv), args.cwd, args.session_id)


if __name__ == "__main__":
    main()
