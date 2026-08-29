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
import json
import os
import subprocess
import sys
import threading
import time
from queue import Empty, Queue
from typing import Any


_APPROVAL_TIMEOUT_SEC = 120.0
_INTERACTIVE_APPROVAL_METHODS = {
    "item/commandExecution/requestApproval",
    "item/fileChange/requestApproval",
}
_INTERACTIVE_USER_INPUT_METHOD = "item/tool/requestUserInput"
_INTERACTIVE_PERMISSION_METHOD = "item/permissions/requestApproval"


def _write_stdout(event: dict[str, Any]) -> None:
    raw = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    sys.stdout.buffer.write(raw.encode("utf-8", errors="replace") + b"\n")
    sys.stdout.buffer.flush()


def _write_stderr(text: str) -> None:
    if not text:
        return
    sys.stderr.buffer.write(text.encode("utf-8", errors="replace"))
    sys.stderr.buffer.flush()


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
    # User messages are already persisted by Pan before the turn starts.  Do
    # not echo them into history, but retain unknown native items for callers
    # that want to inspect the raw stream.
    if kind == "usermessage":
        return None
    return {"type": "codex.item.completed", "item": item}


class AppServer:
    def __init__(self, node: str, codex_js: str, cwd: str,
                 extra_options: list[str]) -> None:
        self.node = node
        self.codex_js = codex_js
        self.cwd = cwd
        self.extra_options = extra_options
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

    def _send(self, message: dict[str, Any]) -> None:
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

    def _request(self, method: str, params: dict[str, Any]) -> int:
        self._request_id += 1
        request_id = self._request_id
        self._send({"method": method, "id": request_id, "params": params})
        return request_id

    def start(self) -> None:
        command = [self.node, self.codex_js, *_server_options(self.extra_options),
                   "app-server", "--stdio"]
        self.process = subprocess.Popen(
            command,
            cwd=self.cwd or None,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
        )
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._pump_stderr, daemon=True).start()

        initialize_id = self._request("initialize", {
            "clientInfo": {"name": "pan", "version": "0.1.0"},
            "capabilities": {},
        })
        self._wait_response(initialize_id)
        self._send({"method": "initialized", "params": {}})

        params = self._thread_params()
        if self._initial_thread_id:
            # ``threadSource`` and developer instructions are start-only
            # fields; resume must not send unknown keys to the strict v2 API.
            params.pop("threadSource", None)
            params.pop("developerInstructions", None)
            params["threadId"] = self._initial_thread_id
            response_id = self._request("thread/resume", params)
        else:
            response_id = self._request("thread/start", params)
        response = self._wait_response(response_id)
        result = response.get("result") or {}
        thread = result.get("thread") or {}
        self.thread_id = thread.get("id") or self._initial_thread_id
        if not self.thread_id:
            raise RuntimeError(f"app-server returned no thread id: {response}")
        self._emit_thread_started(thread)

    @property
    def _initial_thread_id(self) -> str | None:
        return getattr(self, "initial_thread_id", None)

    def _thread_params(self) -> dict[str, Any]:
        params: dict[str, Any] = {"cwd": self.cwd, "threadSource": "pan"}
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
                if isinstance(value, dict):
                    self.incoming.put(value)
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
            code = self.process.returncode if self.process else None
            raise RuntimeError(f"Codex app-server exited (returncode={code})")
        return message

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
        _write_stdout({"type": "thread.started", "thread_id": self.thread_id,
                       "thread": thread})

    def _handle_server_message(self, message: dict[str, Any], state: dict[str, Any] | None) -> None:
        method = message.get("method")
        if ((method and method.endswith("/requestApproval")) or method in (
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
        if method == "thread/tokenUsage/updated":
            params = message.get("params") or {}
            usage = params.get("tokenUsage")
            if isinstance(usage, dict):
                self.last_usage = usage
            _write_stdout({"type": "codex.notification", "method": method, "params": params})
            return
        if method == "item/agentMessage/delta":
            params = message.get("params") or {}
            delta = params.get("delta")
            if delta:
                if state is not None:
                    state["assistant_text"] = state.get("assistant_text", "") + str(delta)
                _write_stdout({
                    "type": "content.part", "role": "assistant", "delta": True,
                    "part": {"type": "text", "text": str(delta)},
                    "stream_text": state.get("assistant_text", "") if state is not None else str(delta),
                    "thread_id": params.get("threadId"), "turn_id": params.get("turnId"),
                })
            return
        if method in ("item/reasoning/textDelta", "item/reasoning/summaryTextDelta"):
            params = message.get("params") or {}
            delta = params.get("delta")
            if delta:
                if state is not None:
                    state["reasoning_text"] = state.get("reasoning_text", "") + str(delta)
                _write_stdout({
                    "type": "content.part", "role": "thinking", "delta": True,
                    "part": {"type": "think", "think": str(delta)},
                    "stream_text": state.get("reasoning_text", "") if state is not None else str(delta),
                    "thread_id": params.get("threadId"), "turn_id": params.get("turnId"),
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
            return
        if method == "item/completed":
            params = message.get("params") or {}
            item = params.get("item") or {}
            event = _item_event(item)
            if event is not None:
                if state is not None and str(item.get("type") or "").replace("_", "").lower() == "agentmessage":
                    state["last_text"] = _text_from_item(item)
                if event.get("type") in ("assistant", "thinking"):
                    event["final"] = True
                if str(item.get("type") or "").replace("_", "").lower() == "commandexecution":
                    event["replace"] = True
                _write_stdout(event)
            return
        if method == "turn/completed":
            if state is not None:
                params = message.get("params") or {}
                turn = params.get("turn") or {}
                items = turn.get("items") or []
                for item in reversed(items):
                    if str(item.get("type") or "").replace("_", "").lower() == "agentmessage":
                        state["last_text"] = _text_from_item(item)
                        break
                status = str(turn.get("status") or "completed")
                error = turn.get("error") or state.get("error")
                state["done"] = True
                state["is_error"] = bool(error) or status not in ("completed", "complete")
                state["error"] = self._error_text(error) if error else ""
            return
        if method == "error":
            params = message.get("params") or {}
            if state is not None:
                state["error"] = self._error_text(params.get("error") or params)
            _write_stdout({"type": "error", "error": params})
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
        event_type = "approval.request" if "Approval" in method or "approval" in method else "codex.user_input"
        _write_stdout({"type": event_type, "method": method, "request_id": request_id,
                       "params": params})

        # In interactive permission modes, keep the native JSON-RPC request
        # open until Pan sends the matching response control.  This is the same pause
        # point the native Codex UI exposes.  Bypass mode remains automatic;
        # non-interactive request kinds retain their conservative fallback.
        interactive_approval = method in _INTERACTIVE_APPROVAL_METHODS and not self.auto_approve
        interactive_user_input = method == _INTERACTIVE_USER_INPUT_METHOD
        interactive_permission = method == _INTERACTIVE_PERMISSION_METHOD and not self.auto_approve
        if (state is not None and request_id is not None
                and (interactive_approval or interactive_user_input or interactive_permission)):
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
            return

        if method == "item/tool/requestUserInput":
            result: dict[str, Any] = {"answers": {}}
        elif method == "mcpServer/elicitation/request":
            result = {"action": "cancel"}
        elif method == "item/tool/call":
            # Pan does not currently expose a dynamic-tool callback channel.
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
        pending = state.get("pending_requests") or {}
        now = time.monotonic()
        for key, request in list(pending.items()):
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
        while True:
            self._safe_decline_expired(state)
            try:
                control = control_queue.get_nowait()
            except Empty:
                return
            if not isinstance(control, dict):
                continue
            kind = control.get("type")
            if kind == "interrupt" and state.get("turn_id"):
                try:
                    self._request("turn/interrupt", {
                        "threadId": self.thread_id, "turnId": state["turn_id"],
                    })
                    state["interrupt_requested"] = True
                except (OSError, RuntimeError):
                    state["error"] = "failed to interrupt Codex turn"
            elif kind == "steer" and state.get("turn_id") and control.get("text"):
                try:
                    self._request("turn/steer", {
                        "threadId": self.thread_id,
                        "expectedTurnId": state["turn_id"],
                        "input": [{"type": "text", "text": str(control["text"])}],
                    })
                except (OSError, RuntimeError):
                    _write_stderr("[codex app-server bridge] failed to steer turn\n")
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

    def run_turn(self, text: str, effort: str | None = None,
                 control_queue: Queue[dict[str, Any] | None] | None = None) -> None:
        state: dict[str, Any] = {"last_text": "", "error": "", "done": False, "is_error": False}
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
            if message.get("id") == response_id:
                if "error" in message:
                    state["done"] = True
                    state["is_error"] = True
                    state["error"] = self._error_text(message["error"])
                else:
                    turn = (message.get("result") or {}).get("turn") or {}
                    state["turn_id"] = turn.get("id")
                continue
            if "method" in message:
                self._handle_server_message(message, state)

        result = state["last_text"]
        if state["is_error"] and not result:
            result = state["error"] or "Codex turn failed"
        _write_stdout({"type": "result", "is_error": bool(state["is_error"]),
                       "result": result, "usage": self.last_usage})

    def close(self) -> None:
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


def _read_pan_stdin(task_queue: Queue[dict[str, Any] | None],
                    control_queue: Queue[dict[str, Any] | None]) -> None:
    stream = getattr(sys.stdin, "buffer", sys.stdin)
    while True:
        line = stream.readline()
        if not line:
            task_queue.put(None)
            return
        try:
            message = json.loads(line.decode("utf-8", errors="replace") if isinstance(line, bytes) else line)
        except json.JSONDecodeError:
            continue
        if not isinstance(message, dict):
            continue
        if message.get("type") in (
                "interrupt", "steer", "approval_response", "user_input_response",
                "permission_response"):
            control_queue.put(message)
        elif message.get("text"):
            task_queue.put(message)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pan bridge for Codex app-server")
    parser.add_argument("--codex-path", required=True)
    parser.add_argument("--node-path", default="node")
    parser.add_argument("--thread-id", default=None)
    parser.add_argument("--codex-extra-args", default="[]")
    parser.add_argument("--system-prompt", default=None)
    args = parser.parse_args(argv)
    try:
        extra = json.loads(args.codex_extra_args)
        if not isinstance(extra, list):
            extra = []
    except json.JSONDecodeError:
        extra = []

    app = AppServer(args.node_path, args.codex_path,
                    os.environ.get("PAN_CODEX_CWD") or os.getcwd(), extra)
    app.initial_thread_id = args.thread_id
    if args.system_prompt and not args.thread_id:
        app.config["developer_instructions"] = args.system_prompt
    pan_queue: Queue[dict[str, Any] | None] = Queue()
    control_queue: Queue[dict[str, Any] | None] = Queue()
    app.control_queue = control_queue
    reader = threading.Thread(target=_read_pan_stdin,
                              args=(pan_queue, control_queue), daemon=True)
    reader.start()
    try:
        app.start()
        while True:
            message = pan_queue.get()
            if message is None:
                break
            effort = app.config.get("model_reasoning_effort")
            app.run_turn(str(message.get("text") or ""),
                         str(effort) if effort else None, control_queue)
    except Exception as exc:  # wrapper errors must be visible to Pan, not silent hangs
        _write_stderr(f"[codex app-server bridge] {exc}\n")
        _write_stdout({"type": "result", "is_error": True, "result": str(exc)})
        return 1
    finally:
        app.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
