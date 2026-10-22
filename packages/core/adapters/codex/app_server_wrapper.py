"""Persistent bridge from Pan's line protocol to Codex app-server.

Pan workers speak a deliberately small protocol: one JSON object containing
``text`` per user turn, and JSON events on stdout.  ``codex exec`` can emulate
that protocol, but it starts a new CLI process for every turn and therefore
cannot expose the native streaming/control surface.  This bridge keeps the
Pan-side contract while keeping one native ``codex app-server --stdio`` alive
for the whole worker lifetime.

The bridge translates app-server notifications into the event shapes already
understood by Pan's Codex adapter and frontend.  Native server requests are
surfaced as ``approval.request``/``codex.user_input`` events.  Command,
file-change, and additional-permission requests remain pending until Pan sends
a decision; other request types receive a safe fallback response so headless
workers remain usable.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from queue import Empty, Queue
from typing import Any

# These bridges also run by absolute filename from a Session workdir.
if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
from packages.jobs.window import command_window_options


_APPROVAL_TIMEOUT_SEC = 120.0
_STDOUT_LOCK = threading.Lock()
_STARTUP_OUTPUT: list[tuple[bool, bytes]] | None = None
_STARTUP_BYTES = 0
_STARTUP_OVERFLOW = False
_LAUNCH_OUTPUT_SECRETS: tuple[bytes, ...] = ()
_INTERACTIVE_APPROVAL_METHODS = {
    "item/commandExecution/requestApproval",
    "item/fileChange/requestApproval",
    # Newer app-server protocol variants use these client callbacks instead
    # of item-scoped requestApproval methods.  They have the same response
    # shape and must remain pending in interactive permission modes.
    "applyPatchApproval",
    "execCommandApproval",
}
_INTERACTIVE_USER_INPUT_METHOD = "item/tool/requestUserInput"
_INTERACTIVE_PERMISSION_METHOD = "item/permissions/requestApproval"
_INTERACTIVE_ELICITATION_METHOD = "mcpServer/elicitation/request"
_TERMINAL_INTERACTION_METHOD = "item/commandExecution/terminalInteraction"


def _read_system_prompt_file(path: str | None) -> str | None:
    """Read and remove the worker-owned UTF-8 prompt file, preserving newlines."""
    if not path:
        return None
    prompt_path = Path(path)
    try:
        with prompt_path.open("r", encoding="utf-8", newline="") as handle:
            return handle.read()
    finally:
        try:
            prompt_path.unlink(missing_ok=True)
        except OSError:
            pass
_CANCELLED_TURN_STATUSES = {"interrupted", "cancelled", "canceled"}


def _write_stdout(event: dict[str, Any]) -> None:
    raw = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    _write_output(False, raw.encode("utf-8", errors="replace") + b"\n")


def _write_stderr(text: str) -> None:
    if not text:
        return
    _write_output(True, text.encode("utf-8", errors="replace"))


def _write_output(stderr: bool, raw: bytes) -> None:
    global _STARTUP_BYTES, _STARTUP_OVERFLOW
    for secret in _LAUNCH_OUTPUT_SECRETS:
        raw = raw.replace(secret, b'[private-launch-redacted]')
    with _STDOUT_LOCK:
        if _STARTUP_OUTPUT is not None:
            if _STARTUP_BYTES + len(raw) > 64 * 1024:
                _STARTUP_OVERFLOW = True
                return
            _STARTUP_BYTES += len(raw)
            _STARTUP_OUTPUT.append((stderr, raw))
            return
        stream = sys.stderr.buffer if stderr else sys.stdout.buffer
        stream.write(raw)
        stream.flush()


def _write_launch_receipt(event: dict[str, Any]) -> None:
    """Keep native diagnostics out of the authenticated startup frame sequence.

    Only the trusted attester calls this function. Native notifications cannot
    mark themselves as receipts. Overflow rejects startup before confirmation.
    """
    global _STARTUP_OUTPUT
    raw = json.dumps(event, separators=(",", ":")).encode("utf-8") + b"\n"
    with _STDOUT_LOCK:
        if _STARTUP_OVERFLOW:
            raise RuntimeError("Fixed launch startup output overflow")
        sys.stdout.buffer.write(raw)
        sys.stdout.buffer.flush()
        if event["type"] == "provider.launch.verified" and _STARTUP_OUTPUT is not None:
            for stderr, pending in _STARTUP_OUTPUT:
                stream = sys.stderr.buffer if stderr else sys.stdout.buffer
                stream.write(pending)
                stream.flush()
            _STARTUP_OUTPUT = None


def _json_value(raw: str) -> Any:
    """Parse the JSON-compatible TOML literals emitted by CodexAdapter."""
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return raw.strip().strip('"')


def _parse_extra_options(options: list[str]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    i = 0
    while i < len(options):
        if options[i] == "-c" and i + 1 < len(options):
            assignment = options[i + 1]
            if "=" in assignment:
                key, raw = assignment.split("=", 1)
                values[key] = _json_value(raw)
            i += 2
            continue
        i += 1
    return values


def _server_options(options: list[str]) -> list[str]:
    """Keep config overrides that are valid/useful for app-server startup.

    The bypass flag is an ``exec`` convenience flag, not an app-server option;
    its semantics are represented by typed thread/turn fields below.  ``-c``
    overrides are intentionally retained, including session-scoped MCP
    entries produced by the adapter.
    """
    out: list[str] = []
    i = 0
    while i < len(options):
        if options[i] == "-c" and i + 1 < len(options):
            out.extend(options[i:i + 2])
            i += 2
        else:
            i += 1
    return out


def _text_from_item(item: dict[str, Any]) -> str:
    text = item.get("text")
    if isinstance(text, str):
        return text
    summary = item.get("summary") or []
    if isinstance(summary, list):
        return "".join(str(x) for x in summary if x is not None)
    return ""


def _item_event(item: dict[str, Any]) -> dict[str, Any] | None:
    """Translate a native completed item to Pan's existing display events."""
    kind = str(item.get("type") or "").replace("_", "").lower()
    if kind == "agentmessage":
        text = _text_from_item(item)
        return {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": text}]},
            "codex_item": item,
        }
    if kind == "reasoning":
        text = _text_from_item(item)
        return {"type": "thinking", "content": text, "codex_item": item}
    if kind == "plan":
        text = _text_from_item(item)
        return {"type": "thinking", "content": text, "codex_item": item}
    if kind == "commandexecution":
        command = str(item.get("command") or "")
        output = str(item.get("aggregated_output") or item.get("aggregatedOutput")
                     or item.get("output") or "")
        args: dict[str, Any] = {"command": command}
        if output:
            args["output"] = output
        return {
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": "Command", "input": args}]},
            "codex_item": item,
        }
    if kind in ("mcptoolcall", "functioncall", "dynamictoolcall"):
        name = str(item.get("tool") or item.get("name") or item.get("server") or "MCP tool")
        args = item.get("arguments") or item.get("input") or item.get("parameters") or {}
        result = item.get("result") or item.get("output") or item.get("error")
        if result:
            if isinstance(args, dict):
                args = dict(args)
                args["result"] = result
        return {
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": name, "input": args}]},
            "codex_item": item,
        }
    if kind in ("filechange", "patchapply"):
        return {
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": "FileChange", "input": item}]},
            "codex_item": item,
        }
    native_tool_names = {
        "collabagenttoolcall": "Agent",
        "subagentactivity": "SubAgent",
        "websearch": "WebSearch",
        "imagegeneration": "ImageGeneration",
        "sleep": "Sleep",
        "enteredreviewmode": "ReviewMode",
        "exitedreviewmode": "ReviewMode",
        "contextcompaction": "ContextCompaction",
    }
    if kind in native_tool_names:
        tool_input = {key: value for key, value in item.items()
                      if key not in {"id", "type"}}
        name = native_tool_names[kind]
        if kind == "collabagenttoolcall" and item.get("tool"):
            name = f"{name}/{item['tool']}"
        return {
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": name, "input": tool_input}]},
            "codex_item": item,
        }
    # User messages are already persisted by Pan before the turn starts.  Do
    # not echo them into history, but retain unknown native items for callers
    # that want to inspect the raw stream.
    if kind == "usermessage":
        return None
    return {"type": "codex.item.completed", "item": item}


class AppServer:
    def __init__(self, node: str, codex_js: str, cwd: str,
                 extra_options: list[str], *, native_control: bool = False,
                 native_passthrough: bool = False) -> None:
        self.node = node
        self.codex_js = codex_js
        self.cwd = cwd
        self.extra_options = extra_options
        self.native_control = native_control
        self.native_passthrough = native_passthrough is True
        self._native_policy_lock = threading.RLock()
        self._native_transport = None
        self._native_disconnect_reason = None
        self._resident_tui = None
        self._open_requests = {}
        self._reply_routes = {}
        self._thread_resume_info = {}
        self._thread_has_turns = False
        if __package__:
            from .control_owner import ControlOwner
        else:
            from control_owner import ControlOwner
        self.control_owner = ControlOwner()
        self.config = _parse_extra_options(extra_options)
        self.process: subprocess.Popen[bytes] | None = None
        self.incoming: Queue[dict[str, Any] | None] = Queue()
        self._write_lock = threading.Lock()
        self._request_id = 0
        self.thread_id: str | None = None
        self.thread_started_emitted = False
        self.last_usage: dict[str, Any] | None = None
        self.auto_approve = "--dangerously-bypass-approvals-and-sandbox" in extra_options
        self._stdin_closed = False
        self.control_queue: Queue[dict[str, Any] | None] | None = None
        self._turn_active = threading.Event()
        # Set before forwarding a native turn/start, not after its notification.
        # This closes the handoff window in which Pan could admit a queued turn.
        self._native_turn_pending = threading.Event()
        self._native_admission_lock = threading.RLock()
        self._native_admission = None
        self._aux_admissions = {}
        self.native_admission_timeout = 5.0
        self._steer_requests: dict[int, str] = {}
        self._steer_requests_lock = threading.Lock()

    def native_admission_receipt(self, message):
        with self._native_admission_lock:
            admission = self._aux_admissions.get(message.get("requestId")) or self._native_admission
            if admission is not None and admission.id == message.get("requestId"):
                admission.acknowledge(message)

    def reserve_auxiliary_admission(self, thread_id, generation, emit, operation):
        from packages.core.adapters.codex.native_admission import NativeAdmission
        with self._native_admission_lock:
            admission = NativeAdmission(thread_id, generation, emit, operation)
            self._aux_admissions[admission.id] = admission
            return admission

    def reserve_native_admission(self, thread_id, generation, emit, operation="turn/start"):
        from packages.core.adapters.codex.native_admission import NativeAdmission
        with self._native_admission_lock:
            if self._turn_active.is_set() or self._native_turn_pending.is_set():
                raise RuntimeError("Turn already executing")
            admission = NativeAdmission(thread_id, generation, emit, operation)
            self._native_admission = admission
            self._native_turn_pending.set()
            return admission

    def retire_native_reservation(self, admission):
        with self._native_admission_lock:
            self._aux_admissions.pop(admission.id, None)
            if self._native_admission is admission:
                self._native_turn_pending.clear()
                self._native_admission = None

    def _send(self, message: dict[str, Any]) -> None:
        if self._native_transport is not None:
            def write():
                with self._write_lock:
                    self._raw_send(message)
            # Read-only observation continues in native mode. Unknown RPCs
            # and replies are writes: fail closed rather than guessing.
            if message.get("method") in {"thread/read", "thread/list", "account/read", "model/list"}:
                write()
            else:
                self.control_owner.write("pan", write)
            return
        if not self.process or self.process.stdin is None:
            raise RuntimeError("app-server is not running")
        # Node's stdio reader on Windows can inherit the active console/code
        # page even though the pipe itself is byte-oriented.  Escaping
        # non-ASCII characters keeps the wire payload pure ASCII; JSON parsing
        # in app-server reconstructs the original Unicode text exactly.
        raw = json.dumps(message, ensure_ascii=True, separators=(",", ":")) + "\n"
        with self._write_lock:
            self.process.stdin.write(raw.encode("utf-8"))
            self.process.stdin.flush()

    def _raw_send(self, message):
        route = self._reply_routes.get(str(message.get("id"))) if "method" not in message else None
        if route is not None:
            original, transport = route
            transport.send({**message, "id": original})
            self._reply_routes.pop(str(message["id"]), None)
        else:
            self._native_transport.send(message)
        if "method" not in message:
            pending = self._open_requests.pop(str(message.get("id")), None)
            if pending is not None:
                pending[1]["resolved"] = True
            if self._resident_tui is not None:
                self._resident_tui.resolve_request(message.get("id"))

    def _request(self, method: str, params: dict[str, Any]) -> int:
        self._request_id += 1
        request_id = self._request_id
        self._send({"method": method, "id": request_id, "params": params})
        return request_id

    def start(self) -> None:
        transport_args = ["--stdio"]
        if self.native_control:
            if __package__:
                from .native_transport import NativeTransport
            else:
                from native_transport import NativeTransport

            self._native_transport = NativeTransport()
            transport_args = self._native_transport.server_args()
        command = [self.node, self.codex_js, *_server_options(self.extra_options),
                   "app-server", *transport_args]
        if self.native_control:
            if __package__:
                from .owned_kernel import OwnedKernel
            else:
                from owned_kernel import OwnedKernel
            self.process = OwnedKernel(command, self.cwd or None)
        else:
            self.process = subprocess.Popen(
            command,
            cwd=self.cwd or None,
            stdin=subprocess.DEVNULL if self.native_control else subprocess.PIPE,
            stdout=subprocess.DEVNULL if self.native_control else subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            **command_window_options(),
            )
        threading.Thread(target=self._pump_stderr, daemon=True).start()
        if self._native_transport is not None:
            self._native_transport.connect(self.process.poll)
            threading.Thread(target=self._read_native, daemon=True).start()
        else:
            threading.Thread(target=self._read_stdout, daemon=True).start()

        initialize_id = self._request("initialize", {
            "clientInfo": {"name": "pan", "version": "0.1.0"},
            "capabilities": {},
        })
        self._wait_response(initialize_id)
        self._send({"method": "initialized", "params": {}})

        from packages.core.adapters.codex.provider_launch import attest
        attest(self, _write_launch_receipt)

        params = self._thread_params()
        if self._initial_thread_id:
            # ``threadSource`` and developer instructions are start-only
            # fields; resume must not send unknown keys to the strict v2 API.
            params.pop("threadSource", None)
            params.pop("developerInstructions", None)
            params["threadId"] = self._initial_thread_id
            # Pan already persists display history. Resume the model context,
            # not a second copy of every tool output; the resident TUI reads
            # historical turns separately when it attaches.
            params["excludeTurns"] = True
            response_id = self._request("thread/resume", params)
        else:
            response_id = self._request("thread/start", params)
        response = self._wait_response(response_id)
        result = response.get("result") or {}
        thread = result.get("thread") or {}
        self._thread_resume_info = dict(result)
        self._thread_has_turns = bool(thread.get("turns") or self._initial_thread_id)
        self.thread_id = thread.get("id") or self._initial_thread_id
        if not self.thread_id:
            raise RuntimeError(f"app-server returned no thread id: {response}")
        from packages.core.adapters.codex.provider_launch import attest_thread
        attest_thread(self, result)
        self._emit_thread_started(thread)

    @property
    def _initial_thread_id(self) -> str | None:
        return getattr(self, "initial_thread_id", None)

    def _thread_params(self) -> dict[str, Any]:
        params: dict[str, Any] = {"cwd": self.cwd, "threadSource": "pan"}
        from packages.core.adapters.codex.provider_launch import thread_params
        thread_params(self, params)
        model = self.config.get("model")
        if model:
            params["model"] = str(model)
        if self.config.get("developer_instructions"):
            params["developerInstructions"] = self.config["developer_instructions"]

        if "--dangerously-bypass-approvals-and-sandbox" in self.extra_options:
            params["approvalPolicy"] = "never"
            params["sandbox"] = "danger-full-access"
        else:
            sandbox = self.config.get("sandbox_mode")
            policy = self.config.get("approval_policy")
            if policy:
                params["approvalPolicy"] = policy
            if sandbox:
                params["sandbox"] = sandbox
        return params

    def _read_stdout(self) -> None:
        stream = self.process.stdout if self.process else None
        if stream is None:
            self.incoming.put(None)
            return
        try:
            for line in stream:
                try:
                    value = json.loads(line.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict) and not self._emit_steer_receipt(value):
                    self.incoming.put(value)
        finally:
            self.incoming.put(None)

    def _read_native(self) -> None:
        try:
            for message in self._native_transport.messages():
                if message.get("method") == "turn/started":
                    self._thread_has_turns = True
                    params = message.get("params") or {}
                    turn_id = (params.get("turn") or {}).get("id")
                    with self._native_admission_lock:
                        admission = self._native_admission
                        if admission is not None and params.get("threadId", self.thread_id) == admission.thread_id:
                            admission.started(turn_id)
                if self._resident_tui is not None and "method" in message and "id" not in message:
                    self._resident_tui.observe(message)
                # Detached resources belong to auxiliary collectors, never the
                # foreground accumulator or observational turn admission.
                if ((message.get("params") or {}).get("threadId", self.thread_id) != self.thread_id):
                    continue
                if not self._emit_steer_receipt(message):
                    self.incoming.put(message)
        except Exception as exc:
            # Never leak header/token or raw transport errors into history.
            sent = getattr(exc, "sent", None)
            received = getattr(exc, "rcvd", None)
            oversized = any(getattr(frame, "code", None) == 1009
                            for frame in (sent, received))
            self._native_disconnect_reason = (
                "Codex native transport message exceeded the bounded receive limit"
                if oversized else "Codex native transport disconnected")
            _write_stderr("[codex app-server] native transport disconnected\n")
        finally:
            self.incoming.put(None)

    def _pump_stderr(self) -> None:
        stream = self.process.stderr if self.process else None
        if stream is None:
            return
        try:
            for line in stream:
                text = line.decode("utf-8", errors="replace")
                _write_stderr(f"[codex app-server] {text}")
        except (OSError, ValueError):
            pass

    def _next(self, timeout: float = 300.0) -> dict[str, Any]:
        try:
            message = self.incoming.get(timeout=timeout)
        except Empty as exc:
            raise TimeoutError("timed out waiting for Codex app-server") from exc
        if message is None:
            code = self.process.poll() if self.process else None
            if self._native_transport is not None and code is None:
                raise RuntimeError(self._native_disconnect_reason or
                                   "Codex native transport disconnected while app-server is still running")
            raise RuntimeError(f"Codex app-server exited (returncode={code})")
        return message

    def _emit_steer_receipt(self, message: dict[str, Any]) -> bool:
        # The reader continues while idle. A late RPC response therefore
        # settles its original request without holding a completed turn open.
        with self._steer_requests_lock:
            request_id = self._steer_requests.pop(message.get("id"), None)
        if request_id is None:
            return False
        receipt = {"type": "pan.steer_receipt", "requestId": request_id,
                   "ok": "error" not in message, "outcome": "rejected" if "error" in message else "accepted"}
        if "error" in message:
            receipt["error"] = self._error_text(message["error"])
        _write_stdout(receipt)
        return True

    def _wait_response(self, request_id: int) -> dict[str, Any]:
        while True:
            message = self._next()
            if message.get("id") == request_id:
                if "error" in message:
                    raise RuntimeError(self._error_text(message["error"]))
                return message
            if "method" in message:
                self._handle_server_message(message, None)

    @staticmethod
    def _error_text(error: Any) -> str:
        if isinstance(error, dict):
            return str(error.get("message") or error.get("data") or error)
        return str(error)

    def _emit_thread_started(self, thread: dict[str, Any]) -> None:
        if self.thread_started_emitted:
            return
        self.thread_started_emitted = True
        # This is an identity/ready notification, not a history transport.
        # Old servers may still include turns despite metadata-only resume;
        # never forward these over Worker's bounded stdout init channel.
        # Keep live-resume metadata for TUI; history is read on demand there.
        _write_stdout({"type": "thread.started", "thread_id": self.thread_id})

    def _handle_server_message(self, message: dict[str, Any], state: dict[str, Any] | None) -> None:
        method = message.get("method")
        if state is not None and state.get("external_turn") and state.get("admission") is not None:
            admission = state["admission"]
            params = message.get("params") or {}
            turn_id = params.get("turnId") or (params.get("turn") or {}).get("id")
            if (params.get("threadId", admission.thread_id) != admission.thread_id
                    or (turn_id is not None and turn_id != admission.turn_id)):
                return  # Buffered notifications cannot terminate a newer admission.
        if ((method and method.endswith("/requestApproval")) or method in (
            "applyPatchApproval", "execCommandApproval",
            "currentTime/read",
            "item/tool/requestUserInput", "mcpServer/elicitation/request", "item/tool/call",
        )):
            self._handle_server_request(message, state)
            return
        if method == "thread/started":
            params = message.get("params") or {}
            thread = params.get("thread") or {}
            self.thread_id = thread.get("id") or self.thread_id
            self._emit_thread_started(thread)
            return
        if method == "thread/status/changed":
            params = message.get("params") or {}
            status = params.get("status")
            if isinstance(status, dict):
                _write_stdout({
                    "type": "codex.thread_status",
                    "native_status": status,
                    "thread_id": params.get("threadId", params.get("thread_id")),
                })
            return
        if method == "thread/tokenUsage/updated":
            params = message.get("params") or {}
            usage = params.get("tokenUsage")
            if isinstance(usage, dict):
                self.last_usage = usage
                _write_stdout({
                    "type": "codex.token_usage",
                    "token_usage": usage,
                    "thread_id": params.get("threadId", params.get("thread_id")),
                    "turn_id": params.get("turnId", params.get("turn_id")),
                })
            return
        if method == "account/rateLimits/updated":
            params = message.get("params") or {}
            rate_limits = params.get("rateLimits")
            if isinstance(rate_limits, dict):
                _write_stdout({
                    "type": "codex.rate_limits",
                    "rate_limits": rate_limits,
                })
            return
        if method == "mcpServer/startupStatus/updated":
            params = message.get("params") or {}
            _write_stdout({
                "type": "codex.mcp_status",
                "mcp_status": params,
            })
            return
        if method == "model/rerouted":
            params = message.get("params") or {}
            _write_stdout({
                "type": "codex.model_rerouted",
                "model_rerouted": params,
            })
            return
        if method == "turn/plan/updated":
            params = message.get("params") or {}
            plan = params.get("plan")
            if isinstance(plan, list):
                turn_id = params.get("turnId", params.get("turn_id"))
                _write_stdout({
                    "type": "codex.plan",
                    "plan": plan,
                    "explanation": params.get("explanation"),
                    "thread_id": params.get("threadId", params.get("thread_id")),
                    "turn_id": turn_id,
                    "item_id": f"plan:{turn_id}" if turn_id else "plan",
                    "delta": True,
                    "replace": True,
                })
            return
        if method == "turn/diff/updated":
            params = message.get("params") or {}
            diff = params.get("diff")
            if isinstance(diff, str):
                turn_id = params.get("turnId", params.get("turn_id"))
                _write_stdout({
                    "type": "codex.diff",
                    "diff": diff,
                    "thread_id": params.get("threadId", params.get("thread_id")),
                    "turn_id": turn_id,
                    "item_id": f"diff:{turn_id}" if turn_id else "diff",
                    "delta": True,
                    "replace": True,
                })
            return
        if method == "serverRequest/resolved":
            params = message.get("params") or {}
            request_id = params.get("requestId", params.get("request_id"))
            self._open_requests.pop(str(request_id), None)
            if self._resident_tui is not None:
                self._resident_tui.resolve_request(request_id)
            if state is not None and request_id is not None:
                (state.get("pending_requests") or {}).pop(str(request_id), None)
            _write_stdout({
                "type": "codex.request_resolved",
                "request_id": request_id,
                "params": params,
            })
            return
        if method == _TERMINAL_INTERACTION_METHOD:
            params = message.get("params") or {}
            item_id = params.get("itemId", params.get("item_id"))
            process_id = params.get("processId", params.get("process_id"))
            if state is not None and item_id is not None:
                state.setdefault("terminal_items", {})[str(item_id)] = dict(params)
            _write_stdout({
                "type": "codex.terminal_interaction",
                "method": method,
                "item_id": item_id,
                "process_id": process_id,
                "stdin": str(params.get("stdin") or ""),
                "thread_id": params.get("threadId", params.get("thread_id")),
                "turn_id": params.get("turnId", params.get("turn_id")),
                "params": params,
            })
            return
        if method == "item/agentMessage/delta":
            params = message.get("params") or {}
            delta = params.get("delta")
            if delta:
                item_key = params.get("itemId") or params.get("item_id")
                turn_key = params.get("turnId", params.get("turn_id"))
                accumulation_key = f"assistant:{item_key or turn_key or 'implicit'}"
                if state is not None:
                    cumulative = state.setdefault("item_cumulative", {})
                    cumulative[accumulation_key] = cumulative.get(accumulation_key, "") + str(delta)
                else:
                    cumulative = {accumulation_key: str(delta)}
                turn_id = params.get("turnId", params.get("turn_id"))
                if turn_id is None and state is not None:
                    turn_id = state.get("turn_id")
                _write_stdout({
                    "type": "content.part", "role": "assistant", "delta": True,
                    "part": {"type": "text", "text": str(delta)},
                    "stream_text": cumulative.get(accumulation_key, str(delta)),
                    "thread_id": params.get("threadId", params.get("thread_id")),
                    "turn_id": turn_id,
                    "item_id": params.get("itemId"),
                })
            return
        if method in ("item/reasoning/textDelta", "item/reasoning/summaryTextDelta"):
            params = message.get("params") or {}
            delta = params.get("delta")
            if delta:
                item_key = params.get("itemId") or params.get("item_id")
                turn_key = params.get("turnId", params.get("turn_id"))
                accumulation_key = f"thinking:{item_key or turn_key or 'implicit'}"
                if state is not None:
                    cumulative = state.setdefault("item_cumulative", {})
                    cumulative[accumulation_key] = cumulative.get(accumulation_key, "") + str(delta)
                else:
                    cumulative = {accumulation_key: str(delta)}
                _write_stdout({
                    "type": "content.part", "role": "thinking", "delta": True,
                    "part": {"type": "think", "think": str(delta)},
                    "stream_text": cumulative.get(accumulation_key, str(delta)),
                    "thread_id": params.get("threadId"), "turn_id": params.get("turnId"),
                    "item_id": params.get("itemId"),
                })
            return
        if method == "item/plan/delta":
            params = message.get("params") or {}
            delta = params.get("delta")
            if delta:
                item_key = params.get("itemId") or params.get("item_id")
                turn_key = params.get("turnId", params.get("turn_id"))
                accumulation_key = f"plan:{item_key or turn_key or 'implicit'}"
                if state is not None:
                    cumulative = state.setdefault("item_cumulative", {})
                    cumulative[accumulation_key] = cumulative.get(accumulation_key, "") + str(delta)
                else:
                    cumulative = {accumulation_key: str(delta)}
                _write_stdout({
                    "type": "content.part", "role": "thinking", "delta": True,
                    "part": {"type": "think", "think": str(delta)},
                    "stream_text": cumulative.get(accumulation_key, str(delta)),
                    "thread_id": params.get("threadId"), "turn_id": params.get("turnId"),
                    "item_id": params.get("itemId"),
                })
            return
        if method in ("item/commandExecution/outputDelta", "command/exec/outputDelta"):
            params = message.get("params") or {}
            item_id = str(params.get("itemId") or "")
            delta = params.get("delta")
            if item_id and delta:
                tool = state.setdefault("tool_items", {}).setdefault(
                    item_id, {"command": "", "output": ""}
                ) if state is not None else {"command": "", "output": ""}
                tool["output"] = str(tool.get("output") or "") + str(delta)
                args: dict[str, Any] = {"command": str(tool.get("command") or ""),
                                         "output": tool["output"]}
                content = {"type": "tool_use", "name": "Command", "input": args}
                _write_stdout({
                    "type": "assistant", "delta": True, "replace": True,
                    "stream_text": f"Command({json.dumps(args, ensure_ascii=True, separators=(',', ':'))})",
                    "message": {"content": [content]},
                    "thread_id": params.get("threadId"), "turn_id": params.get("turnId"),
                    "item_id": item_id,
                })
            return
        if method == "item/fileChange/outputDelta":
            params = message.get("params") or {}
            item_id = str(params.get("itemId") or "")
            delta = params.get("delta")
            if item_id and delta:
                item = state.setdefault("file_items", {}).setdefault(
                    item_id, {"type": "fileChange", "id": item_id,
                              "changes": [], "status": "inProgress"}
                ) if state is not None else {
                    "type": "fileChange", "id": item_id,
                    "changes": [], "status": "inProgress",
                }
                item["output"] = str(item.get("output") or "") + str(delta)
                args = dict(item)
                _write_stdout({
                    "type": "assistant", "delta": True, "replace": True,
                    "stream_text": f"FileChange({json.dumps(args, ensure_ascii=True, separators=(',', ':'))})",
                    "message": {"content": [{"type": "tool_use", "name": "FileChange", "input": args}]},
                    "thread_id": params.get("threadId"), "turn_id": params.get("turnId"),
                    "item_id": item_id,
                })
            return
        if method == "item/fileChange/patchUpdated":
            params = message.get("params") or {}
            item_id = str(params.get("itemId") or "")
            changes = params.get("changes")
            if item_id and isinstance(changes, list):
                item = state.setdefault("file_items", {}).setdefault(
                    item_id, {"type": "fileChange", "id": item_id,
                              "changes": [], "status": "inProgress"}
                ) if state is not None else {
                    "type": "fileChange", "id": item_id,
                    "changes": [], "status": "inProgress",
                }
                item["changes"] = changes
                args = dict(item)
                _write_stdout({
                    "type": "assistant", "delta": True, "replace": True,
                    "stream_text": f"FileChange({json.dumps(args, ensure_ascii=True, separators=(',', ':'))})",
                    "message": {"content": [{"type": "tool_use", "name": "FileChange", "input": args}]},
                    "thread_id": params.get("threadId"), "turn_id": params.get("turnId"),
                    "item_id": item_id,
                })
            return
        if method == "item/mcpToolCall/progress":
            params = message.get("params") or {}
            item_id = str(params.get("itemId") or "")
            progress = params.get("message")
            if item_id and progress:
                item = state.setdefault("mcp_items", {}).setdefault(
                    item_id, {"type": "mcpToolCall", "id": item_id,
                              "tool": "MCP tool", "arguments": {}}
                ) if state is not None else {
                    "type": "mcpToolCall", "id": item_id,
                    "tool": "MCP tool", "arguments": {},
                }
                item["progress"] = str(progress)
                name = str(item.get("tool") or item.get("name") or "MCP tool")
                raw_args = item.get("arguments") or item.get("input") or {}
                args = dict(raw_args) if isinstance(raw_args, dict) else {"input": raw_args}
                args["progress"] = str(progress)
                _write_stdout({
                    "type": "assistant", "delta": True, "replace": True,
                    "stream_text": f"{name}({json.dumps(args, ensure_ascii=True, separators=(',', ':'))})",
                    "message": {"content": [{"type": "tool_use", "name": name, "input": args}]},
                    "thread_id": params.get("threadId"), "turn_id": params.get("turnId"),
                    "item_id": item_id,
                })
            return
        if method == "item/started":
            params = message.get("params") or {}
            item = params.get("item") or {}
            kind = str(item.get("type") or "").replace("_", "").lower()
            if state is not None and kind == "commandexecution":
                item_id = str(item.get("id") or "")
                if item_id:
                    state.setdefault("tool_items", {})[item_id] = {
                        "command": str(item.get("command") or ""),
                        "output": str(item.get("aggregatedOutput") or item.get("aggregated_output") or ""),
                    }
                    args = {"command": state["tool_items"][item_id]["command"]}
                    _write_stdout({
                        "type": "assistant", "delta": True, "replace": False,
                        "stream_text": f"Command({json.dumps(args, ensure_ascii=True, separators=(',', ':'))})",
                        "message": {"content": [{"type": "tool_use", "name": "Command", "input": args}]},
                        "thread_id": params.get("threadId"), "turn_id": params.get("turnId"),
                        "item_id": item_id,
                    })
            if state is not None and kind in ("filechange", "patchapply"):
                item_id = str(item.get("id") or "")
                if item_id:
                    state.setdefault("file_items", {})[item_id] = dict(item)
                    args = dict(item)
                    _write_stdout({
                        "type": "assistant", "delta": True, "replace": False,
                        "stream_text": f"FileChange({json.dumps(args, ensure_ascii=True, separators=(',', ':'))})",
                        "message": {"content": [{"type": "tool_use", "name": "FileChange", "input": args}]},
                        "thread_id": params.get("threadId"), "turn_id": params.get("turnId"),
                        "item_id": item_id,
                    })
            if state is not None and kind == "mcptoolcall":
                item_id = str(item.get("id") or "")
                if item_id:
                    state.setdefault("mcp_items", {})[item_id] = dict(item)
                    name = str(item.get("tool") or item.get("name") or item.get("server") or "MCP tool")
                    raw_args = item.get("arguments") or item.get("input") or item.get("parameters") or {}
                    args = dict(raw_args) if isinstance(raw_args, dict) else {"input": raw_args}
                    _write_stdout({
                        "type": "assistant", "delta": True, "replace": False,
                        "stream_text": f"{name}({json.dumps(args, ensure_ascii=True, separators=(',', ':'))})",
                        "message": {"content": [{"type": "tool_use", "name": name, "input": args}]},
                        "thread_id": params.get("threadId"), "turn_id": params.get("turnId"),
                        "item_id": item_id,
                    })
            return
        if method == "item/completed":
            params = message.get("params") or {}
            item = params.get("item") or {}
            if state is not None and item.get("id") is not None:
                key = (params.get("turnId") or state.get("turn_id"), str(item["id"]))
                completed = state.setdefault("completed_items", set())
                if key in completed:
                    return
                completed.add(key)
            if (state is not None and state.get("external_turn")
                    and str(item.get("type") or "").replace("_", "").lower() == "usermessage"):
                content = item.get("content") or []
                text = "\n".join(part["text"] for part in content
                                 if isinstance(part, dict) and isinstance(part.get("text"), str))
                if text or content:
                    _write_stdout({"type": "pan.native_user", "text": text, "item_id": item.get("id"),
                                   "turn_id": params.get("turnId") or state.get("turn_id"),
                                   "thread_id": params.get("threadId") or self.thread_id,
                                   "nativeContent": content,
                                   **(state["admission"].identity() if state.get("admission") else {})})
            item_type = str(item.get("type") or "").replace("_", "").lower()
            item_status = str(item.get("status") or "").lower()
            item_error = item.get("error")
            if item_error or item_status in {"failed", "error"}:
                # Keep tool/item failures observable without changing the
                # existing history block or task-terminal semantics.
                _write_stdout({
                    "type": "codex.item_error",
                    "item": item,
                    "item_type": item_type,
                    "item_id": item.get("id"),
                    "item_status": item_status or None,
                    "error": item_error,
                    "thread_id": params.get("threadId"),
                    "turn_id": params.get("turnId", params.get("turn_id")),
                })
            event = _item_event(item)
            if event is not None:
                if state is not None and str(item.get("type") or "").replace("_", "").lower() == "agentmessage":
                    state["last_text"] = _text_from_item(item)
                if event.get("type") in ("assistant", "thinking"):
                    event["final"] = True
                if str(item.get("type") or "").replace("_", "").lower() == "commandexecution":
                    event["replace"] = True
                if str(item.get("type") or "").replace("_", "").lower() in ("filechange", "patchapply"):
                    event["replace"] = True
                if item.get("id") is not None:
                    event["item_id"] = item["id"]
                turn_id = params.get("turnId", params.get("turn_id"))
                if turn_id is None and state is not None:
                    turn_id = state.get("turn_id")
                if turn_id is not None:
                    event["turn_id"] = turn_id
                _write_stdout(event)
            return
        if method == "turn/completed":
            if state is not None:
                params = message.get("params") or {}
                turn = params.get("turn") or {}
                items = turn.get("items") or []
                # A terminal snapshot can contain items whose individual
                # completion was missed at an attach boundary. Reconcile by
                # turn/item identity; equal bodies are distinct messages.
                for item in items:
                    self._handle_server_message({"method": "item/completed", "params": {
                        "threadId": params.get("threadId", self.thread_id),
                        "turnId": turn.get("id") or state.get("turn_id"), "item": item}}, state)
                for item in reversed(items):
                    if str(item.get("type") or "").replace("_", "").lower() == "agentmessage":
                        state["last_text"] = _text_from_item(item)
                        break
                status = str(turn.get("status") or "completed")
                turn_error = turn.get("error")
                if turn_error:
                    error = turn_error
                    state["error_payload"] = turn_error
                elif status in ("completed", "complete"):
                    # The completed turn notification is authoritative: a
                    # successful turn must not inherit transient in-turn
                    # error notices (e.g. "Reconnecting... 2/5" emitted by
                    # the native client while falling back from WebSockets
                    # to HTTPS), otherwise correct results get flagged
                    # as errors.  Report the recovery as a structured CLI
                    # fact, then clear it as before.
                    recovered = state.get("error_payload")
                    if recovered is not None:
                        _write_stdout({
                            "type": "codex.transient_error_recovered",
                            "error": recovered,
                            "error_text": state.get("error") or "",
                            "turn_status": status,
                            "thread_id": params.get("threadId", self.thread_id),
                            "turn_id": (params.get("turnId", params.get("turn_id"))
                                        or turn.get("id")),
                        })
                    state["error_payload"] = None
                    error = None
                else:
                    # Non-successful completion without an explicit turn
                    # error: fall back to any error notification captured
                    # during the turn.  Keep its raw payload for Pan.
                    error = state.get("error")
                state["done"] = True
                state["turn_status"] = status
                state["is_cancelled"] = status.lower() in _CANCELLED_TURN_STATUSES and not error
                state["is_error"] = bool(error) or (
                    status not in ("completed", "complete") and not state["is_cancelled"]
                )
                state["error"] = self._error_text(error) if error else ""
            return
        if method == "error":
            params = message.get("params") or {}
            error = params.get("error") or params
            error_text = self._error_text(error)
            if state is not None:
                state["error"] = error_text
                state["error_payload"] = error
            _write_stdout({
                "type": "codex.turn_error",
                "error": error,
                "error_text": error_text,
                "thread_id": params.get("threadId", params.get("thread_id")),
                "turn_id": params.get("turnId", params.get("turn_id")),
            })
            return
        # Preserve useful native lifecycle/status events for diagnostics and
        # future UI consumers without making the worker treat them as results.
        if method:
            _write_stdout({"type": "codex.notification", "method": method,
                           "params": message.get("params") or {}})

    def _handle_server_request(self, message: dict[str, Any], state: dict[str, Any] | None) -> None:
        method = str(message.get("method") or "")
        request_id = message.get("id")
        params = message.get("params") or {}
        if method == "currentTime/read" and self._native_transport is not None:
            with self._write_lock:
                self._raw_send({"id": request_id, "result": {"currentTimeAt": int(time.time())}})
            return
        if method == _INTERACTIVE_ELICITATION_METHOD:
            event_type = "codex.elicitation"
        elif "Approval" in method or "approval" in method:
            event_type = "approval.request"
        else:
            event_type = "codex.user_input"
        _write_stdout({"type": event_type, "method": method, "request_id": request_id,
                       "params": params})
        native_owned = self.native_control and self.control_owner.snapshot()["mode"] == "native"

        # In interactive permission modes, keep the native JSON-RPC request
        # open until Pan sends the matching response control.  This is the same pause
        # point the native Codex UI exposes.  Bypass mode remains automatic;
        # non-interactive request kinds retain their conservative fallback.
        interactive_approval = method in _INTERACTIVE_APPROVAL_METHODS and not self.auto_approve
        interactive_user_input = method == _INTERACTIVE_USER_INPUT_METHOD
        interactive_permission = method == _INTERACTIVE_PERMISSION_METHOD and not self.auto_approve
        interactive_elicitation = method == _INTERACTIVE_ELICITATION_METHOD
        if (state is not None and request_id is not None
                and (native_owned or interactive_approval or interactive_user_input or interactive_permission
                     or interactive_elicitation)):
            if interactive_user_input:
                fallback_result: dict[str, Any] = {"answers": {}}
                auto_resolution_ms = params.get("autoResolutionMs")
                try:
                    timeout_sec = max(1.0, min(
                        _APPROVAL_TIMEOUT_SEC,
                        float(auto_resolution_ms) / 1000.0,
                    )) if auto_resolution_ms is not None else _APPROVAL_TIMEOUT_SEC
                except (TypeError, ValueError):
                    timeout_sec = _APPROVAL_TIMEOUT_SEC
            elif interactive_permission:
                fallback_result = {"permissions": {}, "scope": "turn"}
                timeout_sec = _APPROVAL_TIMEOUT_SEC
            elif interactive_elicitation:
                fallback_result = {"action": "cancel", "content": None}
                timeout_sec = _APPROVAL_TIMEOUT_SEC
            else:
                fallback_result = {"decision": "decline"}
                timeout_sec = _APPROVAL_TIMEOUT_SEC
            state.setdefault("pending_requests", {})[str(request_id)] = {
                "id": request_id,
                "method": method,
                "params": params,
                "fallback_result": fallback_result,
                "deadline": time.monotonic() + timeout_sec,
            }
            if self.native_control:
                self._open_requests[str(request_id)] = (message, state["pending_requests"][str(request_id)])
            if native_owned and self._resident_tui is not None:
                self._resident_tui.offer_request(message)
            return

        if method == "item/tool/requestUserInput":
            result: dict[str, Any] = {"answers": {}}
        elif method == "currentTime/read":
            # The app-server uses this for client/server clock coordination;
            # respond with the bridge host's whole Unix-second timestamp.
            self._send({"id": request_id, "result": {
                "currentTimeAt": int(time.time()),
            }})
            return
        elif method == "mcpServer/elicitation/request":
            result = {"action": "cancel", "content": None}
        elif method == "item/tool/call":
            # Dynamic Tool is intentionally out of scope: external tools use
            # MCP, so Pan does not expose a second runtime callback/permission
            # surface. Keep the failure explicit instead of silently hanging
            # the native request.
            self._send({"id": request_id, "error": {"code": -32000,
                           "message": "Pan does not expose dynamic tool callbacks"}})
            return
        elif method == "item/permissions/requestApproval":
            result = {"permissions": {"fileSystem": None, "network": None}, "scope": "turn"}
        else:
            decisions = params.get("availableDecisions") or []
            decision = "accept" if self.auto_approve else "decline"
            # A server may omit the preferred decision from an experimental
            # list; decline is the safe fallback in that case.
            available = {d if isinstance(d, str) else next(iter(d), "") for d in decisions}
            if decision not in available and available:
                decision = "decline" if "decline" in available else next(iter(available))
            result = {"decision": decision}
        self._send({"id": request_id, "result": result})

    def _safe_decline_expired(self, state: dict[str, Any]) -> None:
        if self.native_control and self.control_owner.snapshot()["mode"] != "pan":
            return
        pending = state.get("pending_requests") or {}
        now = time.monotonic()
        for key, request in list(pending.items()):
            if request.get("resolved"):
                pending.pop(key, None)
                continue
            if now < float(request.get("deadline") or 0):
                continue
            try:
                self._send({
                    "id": request["id"],
                    "result": request.get("fallback_result") or {"decision": "decline"},
                })
            except (OSError, RuntimeError):
                state["error"] = "failed to decline expired Codex approval"
            pending.pop(key, None)

    def _drain_controls(self, state: dict[str, Any],
                        control_queue: Queue[dict[str, Any] | None] | None) -> None:
        if control_queue is None:
            return
        # Pan can report a worker as running before turn/start has returned a
        # turn id. Flush controls captured during that small window as soon as
        # the native id becomes available.
        if state.get("turn_id"):
            if state.pop("interrupt_pending", False):
                try:
                    self._request("turn/interrupt", {
                        "threadId": self.thread_id, "turnId": state["turn_id"],
                    })
                    state["interrupt_requested"] = True
                except (OSError, RuntimeError):
                    state["error"] = "failed to interrupt Codex turn"
            pending_steer = state.pop("steer_pending", None)
            if pending_steer:
                try:
                    self._request("turn/steer", {
                        "threadId": self.thread_id,
                        "expectedTurnId": state["turn_id"],
                        "input": [{"type": "text", "text": str(pending_steer)}],
                    })
                except (OSError, RuntimeError):
                    _write_stderr("[codex app-server bridge] failed to steer turn\n")
        while True:
            self._safe_decline_expired(state)
            try:
                control = control_queue.get_nowait()
            except Empty:
                return
            if not isinstance(control, dict):
                continue
            kind = control.get("type")
            if kind == "interrupt":
                if not state.get("turn_id"):
                    state["interrupt_pending"] = True
                    continue
                try:
                    self._request("turn/interrupt", {
                        "threadId": self.thread_id, "turnId": state["turn_id"],
                    })
                    state["interrupt_requested"] = True
                except (OSError, RuntimeError):
                    state["error"] = "failed to interrupt Codex turn"
            elif kind == "steer" and control.get("text"):
                request_id = control.get("requestId")
                if state.get("done") or not state.get("turn_id"):
                    if request_id:
                        _write_stdout({"type": "pan.steer_receipt", "requestId": request_id,
                                       "ok": False, "outcome": "rejected", "error": "No active Codex turn"})
                        continue
                    state["steer_pending"] = str(control["text"])
                    continue
                try:
                    with self._steer_requests_lock:
                        if len(self._steer_requests) >= 128:
                            if request_id:
                                _write_stdout({"type": "pan.steer_receipt", "requestId": request_id,
                                               "ok": False, "outcome": "rejected", "error": "Too many unconfirmed Steer requests"})
                            continue
                        rpc_id = self._request("turn/steer", {
                            "threadId": self.thread_id,
                            "expectedTurnId": state["turn_id"],
                            "input": [{"type": "text", "text": str(control["text"])}],
                        })
                        if request_id:
                            self._steer_requests[rpc_id] = request_id
                except (OSError, RuntimeError):
                    _write_stderr("[codex app-server bridge] failed to steer turn\n")
                    if request_id:
                        _write_stdout({"type": "pan.steer_receipt", "requestId": request_id,
                                       "ok": False, "outcome": "unknown", "error": "Steer outcome unknown: Codex turn/steer request write failed"})
            elif kind == "approval_response":
                request_id = control.get("request_id", control.get("requestId"))
                pending = (state.get("pending_requests") or {}).pop(str(request_id), None)
                if pending is None:
                    continue
                result = control.get("result")
                if not isinstance(result, dict):
                    decision = str(control.get("decision") or "decline")
                    if decision not in {"accept", "acceptForSession", "decline", "cancel"}:
                        decision = "decline"
                    result = {"decision": decision}
                try:
                    self._send({"id": pending["id"], "result": result})
                except (OSError, RuntimeError):
                    state["error"] = "failed to send Codex approval response"
            elif kind == "user_input_response":
                request_id = control.get("request_id", control.get("requestId"))
                pending = (state.get("pending_requests") or {}).pop(str(request_id), None)
                if pending is None or pending.get("method") != _INTERACTIVE_USER_INPUT_METHOD:
                    continue
                result = control.get("result")
                if not isinstance(result, dict):
                    raw_answers = control.get("answers")
                    answers = raw_answers if isinstance(raw_answers, dict) else {}
                    result = {"answers": answers}
                else:
                    raw_answers = result.get("answers")
                    result = {"answers": raw_answers if isinstance(raw_answers, dict) else {}}
                try:
                    self._send({"id": pending["id"], "result": result})
                except (OSError, RuntimeError):
                    state["error"] = "failed to send Codex user input response"
            elif kind == "permission_response":
                request_id = control.get("request_id", control.get("requestId"))
                pending = (state.get("pending_requests") or {}).pop(str(request_id), None)
                if pending is None or pending.get("method") != _INTERACTIVE_PERMISSION_METHOD:
                    continue
                raw_permissions = control.get("permissions")
                permissions = raw_permissions if isinstance(raw_permissions, dict) else {}
                scope = control.get("scope")
                if scope not in {"turn", "session"}:
                    scope = "turn"
                try:
                    self._send({"id": pending["id"], "result": {
                        "permissions": permissions, "scope": scope,
                    }})
                except (OSError, RuntimeError):
                    state["error"] = "failed to send Codex permission response"
            elif kind == "elicitation_response":
                request_id = control.get("request_id", control.get("requestId"))
                pending = (state.get("pending_requests") or {}).pop(str(request_id), None)
                if pending is None or pending.get("method") != _INTERACTIVE_ELICITATION_METHOD:
                    continue
                action = str(control.get("action") or "cancel")
                if action not in {"accept", "decline", "cancel"}:
                    action = "cancel"
                content = control.get("content") if action == "accept" else None
                if action == "accept" and not isinstance(content, dict):
                    content = {}
                try:
                    self._send({"id": pending["id"], "result": {
                        "action": action, "content": content,
                    }})
                except (OSError, RuntimeError):
                    state["error"] = "failed to send Codex elicitation response"
            elif kind == "terminal_input":
                process_id = control.get("process_id", control.get("processId"))
                if not process_id:
                    continue
                raw_text = control.get("text", control.get("data", ""))
                if not isinstance(raw_text, str):
                    raw_text = str(raw_text or "")
                encoded = base64.b64encode(raw_text.encode("utf-8")).decode("ascii")
                if not encoded and not control.get("close_stdin", control.get("closeStdin", False)):
                    continue
                try:
                    self._request("command/exec/write", {
                        "processId": str(process_id),
                        "deltaBase64": encoded or None,
                        "closeStdin": bool(control.get("close_stdin", control.get("closeStdin", False))),
                    })
                except (OSError, RuntimeError):
                    state["error"] = "failed to write Codex terminal input"
            elif kind == "terminal_terminate":
                process_id = control.get("process_id", control.get("processId"))
                if not process_id:
                    continue
                try:
                    self._request("command/exec/terminate", {
                        "processId": str(process_id),
                    })
                except (OSError, RuntimeError):
                    state["error"] = "failed to terminate Codex terminal process"

    def run_turn(self, text: str, effort: str | None = None,
                 control_queue: Queue[dict[str, Any] | None] | None = None) -> None:
        self._turn_active.set()
        try:
            self._run_turn(text, effort, control_queue)
        finally:
            self._turn_active.clear()

    def _run_turn(self, text: str, effort: str | None = None,
                  control_queue: Queue[dict[str, Any] | None] | None = None) -> None:
        # A tokenUsage notification is emitted per turn. Do not accidentally
        # attach the previous turn's snapshot to a result if the native server
        # finishes a turn without sending a fresh usage notification.
        self.last_usage = None
        state: dict[str, Any] = {
            "last_text": "", "error": "", "done": False, "is_error": False,
            "item_cumulative": {},
        }
        params: dict[str, Any] = {
            "threadId": self.thread_id,
            "input": [{"type": "text", "text": text}],
        }
        if effort:
            params["effort"] = effort
        if "--dangerously-bypass-approvals-and-sandbox" in self.extra_options:
            params["approvalPolicy"] = "never"
            params["sandboxPolicy"] = {"type": "dangerFullAccess"}
        else:
            policy = self.config.get("approval_policy")
            sandbox = self.config.get("sandbox_mode")
            if policy:
                params["approvalPolicy"] = policy
            if sandbox:
                params["sandboxPolicy"] = {
                    "type": {"read-only": "readOnly", "workspace-write": "workspaceWrite"}.get(
                        str(sandbox), str(sandbox)
                    )
                }
        response_id = self._request("turn/start", params)
        while not state["done"]:
            self._drain_controls(state, control_queue)
            try:
                message = self._next(timeout=0.2)
            except TimeoutError:
                continue
            if self._emit_steer_receipt(message):
                continue
            if message.get("id") == response_id:
                if "error" in message:
                    state["done"] = True
                    state["is_error"] = True
                    state["error"] = self._error_text(message["error"])
                    state["error_payload"] = message["error"]
                else:
                    turn = (message.get("result") or {}).get("turn") or {}
                    state["turn_id"] = turn.get("id")
                continue
            if "method" in message:
                self._handle_server_message(message, state)

        result = state["last_text"]
        if state["is_error"] and not result:
            result = state["error"] or "Codex turn failed"
        # Reject late controls from the completed turn before emitting result.
        # The stdin reader rejects controls while idle, so none leak into the
        # next FIFO turn after a terminal race.
        self._turn_active.clear()
        self._drain_controls(state, control_queue)
        payload = {"type": "result", "is_error": bool(state["is_error"]),
                   "cancelled": bool(state.get("is_cancelled")),
                   "turn_status": state.get("turn_status", "completed"),
                   "result": result, "usage": self.last_usage}
        if state["is_error"]:
            # Pan must receive the provider error beside assistant text.  A
            # failed turn without a provider error object still needs a
            # non-assistant error identity: derive it from the turn status,
            # never from ``last_text``.
            raw_error = state.get("error_payload")
            if raw_error is None:
                raw_error = {
                    "message": state.get("error") or "Codex turn failed",
                    "source": "codex_turn_status",
                    "turnStatus": state.get("turn_status"),
                }
                payload["error_source"] = "codex_turn_status"
            else:
                payload["error_source"] = "codex_error_payload"
            payload["error"] = raw_error
            if state.get("error"):
                payload["error_text"] = state["error"]
        _write_stdout(payload)

    def close(self) -> None:
        if self._resident_tui is not None:
            try:
                self._resident_tui.close()
            except Exception:
                _write_stderr("[codex app-server] resident TUI cleanup unconfirmed\n")
        if self._native_transport is not None:
            try:
                self._native_transport.close()
            except Exception:
                _write_stderr("[codex app-server] native transport close failed\n")
        process = self.process
        if process is None:
            return
        try:
            if process.stdin:
                process.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            process.terminate()
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
            except OSError:
                pass
            try:
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                pass
        if self.native_control:
            process.close()


def _read_pan_stdin(task_queue: Queue[dict[str, Any] | None],
                    control_queue: Queue[dict[str, Any] | None],
                    turn_active: threading.Event | None = None,
                    mode_handler=None, native_handler=None, admission_handler=None) -> None:
    # The reader must never wait on owner/policy locks: an ACK may be behind
    # a blocked mode/input operation. Preserve their order on one executor.
    callbacks = Queue()
    def controls():
        while True:
            operation = callbacks.get()
            if operation is None:
                return
            handler, value = operation
            handler(value)
    threading.Thread(target=controls, daemon=True).start()
    stream = getattr(sys.stdin, "buffer", sys.stdin)
    while True:
        line = stream.readline()
        if not line:
            callbacks.put((task_queue.put, None))
            callbacks.put(None)
            return
        try:
            message = json.loads(line.decode("utf-8", errors="replace") if isinstance(line, bytes) else line)
        except json.JSONDecodeError:
            continue
        if not isinstance(message, dict):
            continue
        if message.get("type") == "native_admission":
            if admission_handler is not None:
                admission_handler(message)
            continue
        if message.get("type") == "control_mode":
            if mode_handler is not None:
                callbacks.put((mode_handler, message))
            continue
        if message.get("type") in {"native_input", "native_resize", "native_passthrough"}:
            if native_handler is not None:
                callbacks.put((native_handler, message))
            continue
        if message.get("type") in (
                "interrupt", "steer", "approval_response", "user_input_response",
                "permission_response", "elicitation_response", "terminal_input",
                "terminal_terminate"):
            if (message.get("type") == "steer" and message.get("requestId")
                    and turn_active is not None and not turn_active.is_set()):
                _write_stdout({"type": "pan.steer_receipt", "requestId": message["requestId"],
                               "ok": False, "outcome": "rejected", "error": "No active Codex turn"})
            else:
                callbacks.put((control_queue.put, message))
        elif message.get("text"):
            callbacks.put((task_queue.put, message))


def main(argv: list[str] | None = None) -> int:
    from packages.core.adapters.codex.provider_launch import bootstrap
    bootstrap()
    # Bootstrap itself must precede the gate. The parent is now retaining the
    # interpreter and its Job before any native process/reader can emit output.
    from packages.core.adapters.codex.provider_launch import ENV
    global _STARTUP_OUTPUT, _STARTUP_BYTES, _STARTUP_OVERFLOW, _LAUNCH_OUTPUT_SECRETS
    if os.environ.get(ENV):
        _STARTUP_OUTPUT, _STARTUP_BYTES, _STARTUP_OVERFLOW = [], 0, False
        _LAUNCH_OUTPUT_SECRETS = tuple(value.encode('utf-8') for key, value in os.environ.items()
                                       if key.startswith('PAN_CODEX_LAUNCH_TOKEN_') and value)
    parser = argparse.ArgumentParser(description="Pan bridge for Codex app-server")
    parser.add_argument("--codex-path", required=True)
    parser.add_argument("--node-path", default="node")
    parser.add_argument("--thread-id", default=None)
    parser.add_argument("--native-control", action="store_true")
    parser.add_argument("--native-passthrough", action="store_true")
    parser.add_argument("--codex-extra-args", default="[]")
    parser.add_argument("--system-prompt", default=None)
    parser.add_argument("--system-prompt-file", default=None)
    args = parser.parse_args(argv)
    try:
        extra = json.loads(args.codex_extra_args)
        if not isinstance(extra, list):
            extra = []
    except json.JSONDecodeError:
        extra = []

    from packages.core.adapters.codex.provider_launch import options as fixed_provider_options
    extra.extend(fixed_provider_options())

    system_prompt = args.system_prompt
    if args.system_prompt_file:
        system_prompt = _read_system_prompt_file(args.system_prompt_file)

    app = AppServer(args.node_path, args.codex_path,
                    os.environ.get("PAN_CODEX_CWD") or os.getcwd(), extra,
                    native_control=args.native_control, native_passthrough=args.native_passthrough)
    app.initial_thread_id = args.thread_id
    if system_prompt and not args.thread_id:
        app.config["developer_instructions"] = system_prompt
    pan_queue: Queue[dict[str, Any] | None] = Queue()
    control_queue: Queue[dict[str, Any] | None] = Queue()
    app.control_queue = control_queue
    def mode_handler(message):
        receipt = {"type": "pan.control_mode", "requestId": message.get("requestId"), "ok": False}
        try:
            if not app.native_control or not app.thread_id:
                raise RuntimeError("Native control transport is not ready")
            if message.get("mode") == "native" and app._resident_tui is None:
                if __package__:
                    from .resident_tui import ResidentTui
                else:
                    from resident_tui import ResidentTui
                candidate = ResidentTui(app, _write_stdout)
                try:
                    candidate.start()
                except Exception:
                    # Keep the object if cleanup fails: never lose its owner.
                    app._resident_tui = candidate
                    candidate.close()
                    app._resident_tui = None
                    raise RuntimeError("Native TUI startup failed") from None
                app._resident_tui = candidate
            if message.get("mode") == "native" and (not app._resident_tui.ready.is_set() or app._resident_tui.backend.wait_dead(0)):
                raise RuntimeError("Native TUI startup unconfirmed")
            receipt.update(app.control_owner.switch(message.get("mode"), message.get("generation")))
            receipt["ok"] = True
            receipt["thread_id"] = app.thread_id
            receipt["kernel_pid"] = app.process.pid
            identity = app.process.backend.identity if hasattr(app.process, "backend") else None
            receipt["kernel_filetime"] = str(identity.created_at_filetime) if identity else None
            receipt["tui_pid"] = app._resident_tui.backend.pid if app._resident_tui else None
            receipt["native_busy"] = app._native_turn_pending.is_set()
            if app._resident_tui is not None:
                app._resident_tui.mode_changed()
            if receipt["mode"] == "pan":
                for pending, record in list(app._open_requests.values()):
                    record["deadline"] = time.monotonic() + _APPROVAL_TIMEOUT_SEC
                    method = pending["method"]
                    event_type = ("codex.elicitation" if method == _INTERACTIVE_ELICITATION_METHOD
                                  else "approval.request" if "approval" in method.lower() else "codex.user_input")
                    _write_stdout({"type": event_type, "method": method, "request_id": pending["id"], "params": pending.get("params", {})})
        except RuntimeError:
            receipt.update(app.control_owner.snapshot())
            receipt["error"] = "Control mode rejected"
        _write_stdout(receipt)
    def native_handler(message):
        receipt = {"type": "pan.native_write", "requestId": message.get("requestId"), "ok": False}
        try:
            if message["type"] == "native_passthrough":
                if not app.native_control or type(message.get("enabled")) is not bool:
                    raise RuntimeError("Invalid native policy")
                with app._native_policy_lock:
                    app.native_passthrough = message["enabled"]
                receipt["ok"] = True
                _write_stdout(receipt)
                return
            if app._resident_tui is None:
                raise RuntimeError("Native TUI is not ready")
            if message["type"] == "native_input":
                app._resident_tui.input(message.get("text"), message.get("generation"))
            else:
                app._resident_tui.resize(message.get("rows"), message.get("cols"), message.get("generation"))
            receipt["ok"] = True
        except Exception:
            receipt["error"] = "native-operation-rejected"
        _write_stdout(receipt)
    reader = threading.Thread(target=_read_pan_stdin,
                              args=(pan_queue, control_queue, app._turn_active, mode_handler, native_handler,
                                    app.native_admission_receipt), daemon=True)
    try:
        app.start()
        # A failed startup must not leave a daemon holding buffered stdin
        # during interpreter shutdown and obscure the kernel's real error.
        reader.start()
        external = None
        while True:
            try:
                message = pan_queue.get(timeout=.05)
            except Empty:
                if app.native_control:
                    try:
                        event = app._next(timeout=.01)
                    except TimeoutError:
                        continue
                    if event.get("method") == "turn/started":
                        turn = (event.get("params") or {}).get("turn") or {}
                        admission = app._native_admission
                        if admission is None:
                            # Preserve existing passthrough execution/reporting.
                            # It is explicitly observational, never pre-admission.
                            from packages.core.adapters.codex.native_admission import NativeAdmission
                            snapshot = app.control_owner.snapshot()
                            admission = NativeAdmission(app.thread_id, snapshot["generation"], _write_stdout,
                                                        operation="observed-turn")
                            admission.state = "sending"
                            app._native_admission = admission
                        if not admission.started(turn.get("id")):
                            _write_stdout({"type": "pan.native_error", "error": "native-turn-identity-conflict"})
                            continue
                        if external is not None:
                            continue  # Duplicate started cannot reset the active result accumulator.
                        external = {"admission": admission, "last_text": "", "error": "", "done": False, "is_error": False,
                                    "item_cumulative": {}, "turn_id": turn.get("id"), "external_turn": True}
                        app._turn_active.set()
                        _write_stdout({"type": "pan.native_turn_started", "turn_id": turn.get("id"),
                                       **admission.identity(), "observedAfterStart": admission.operation == "observed-turn",
                                       "threadId": admission.thread_id, "controlGeneration": admission.generation})
                    app._handle_server_message(event, external)
                    if external is not None:
                        app._drain_controls(external, control_queue)
                        if external["done"]:
                            external_result = {
                                "type": "result", "external_turn": True,
                                "is_error": bool(external.get("is_error")),
                                "cancelled": bool(external.get("is_cancelled")),
                                "turn_status": external.get("turn_status"),
                                "result": (external.get("last_text")
                                           or external.get("error") or ""),
                                "usage": app.last_usage,
                                "turn_id": external["admission"].turn_id,
                                **external["admission"].identity(),
                            }
                            if external.get("is_error"):
                                raw_error = external.get("error_payload")
                                if raw_error is None:
                                    raw_error = {
                                        "message": (external.get("error")
                                                    or "Codex turn failed"),
                                        "source": "codex_turn_status",
                                        "turnStatus": external.get("turn_status"),
                                    }
                                    external_result["error_source"] = "codex_turn_status"
                                else:
                                    external_result["error_source"] = "codex_error_payload"
                                external_result["error"] = raw_error
                                if external.get("error"):
                                    external_result["error_text"] = external["error"]
                            _write_stdout(external_result)
                            app.retire_native_reservation(external["admission"])
                            app._turn_active.clear()
                            app._native_turn_pending.clear()
                            external = None
                continue
            if message is None:
                break
            effort = app.config.get("model_reasoning_effort")
            if external is not None or app._native_turn_pending.is_set() or app.control_owner.snapshot()["mode"] != "pan":
                # The Worker gate must prevent this. Do not create a second turn
                # or manufacture a result paired with the executing native turn.
                _write_stdout({"type": "pan.native_error", "error": "pan-turn-not-admitted"})
                continue
            app.run_turn(str(message.get("text") or ""),
                         str(effort) if effort else None, control_queue)
    except Exception as exc:  # wrapper errors must be visible to Pan, not silent hangs
        _write_stderr(f"[codex app-server bridge] {exc}\n")
        admission = app._native_admission
        if admission is not None:
            # A wrapper exception after send is not a safely caught provider
            # result. Keep False/owner through EOF; only proven no-send cancels.
            admission.cancel()
            _write_stdout({"type": "pan.native_error", "error": "native-wrapper-failed"})
        else:
            _write_stdout({
                "type": "result", "is_error": True, "result": str(exc),
                "error_source": "wrapper_exception",
                "error": {"message": str(exc), "type": type(exc).__name__},
            })
        return 1
    finally:
        app.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
