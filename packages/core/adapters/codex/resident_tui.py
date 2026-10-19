"""Private, resident remote TUI. A mode change never disconnects this client."""
from __future__ import annotations

import base64
import json
import os
import secrets
import sys
import threading
from pathlib import Path

from websockets.http11 import Response
from websockets.datastructures import Headers
from websockets.sync.server import serve

if __package__:
    from .control_owner import ControlRejected
    from .native_transport import MAX_MESSAGE_BYTES
else:
    from control_owner import ControlRejected
    from native_transport import MAX_MESSAGE_BYTES

READ_METHODS = frozenset({
    "initialize", "initialized", "account/read", "account/rateLimits/read", "model/list",
    "config/read", "config/requirements/read", "configRequirements/read", "config/mcpServer/reload", "mcpServerStatus/list",
    "plugin/list", "skills/list", "collaborationMode/list", "hooks/list", "thread/read", "thread/list", "thread/loaded/list",
    "thread/items/list", "thread/turns/list", "thread/goal/get", "app/list",
}) - {"config/mcpServer/reload"}  # Reload mutates running tools: not a read.

THREAD_WRITES = frozenset({"turn/start", "turn/steer", "turn/interrupt", "thread/compact/start",
                           "command/exec/write", "command/exec/terminate"})
# Contracted local configuration writes do not launch execution. MCP reload
# deliberately stays opaque: its ordinary reply cannot prove tool retirement.
PURE_SETTING_METHODS = frozenset({"config/batchWrite", "config/value/write"})
CONTINUATION_METHODS = frozenset({"turn/steer", "turn/interrupt", "command/exec/write",
                                  "command/exec/terminate", "command/exec/resize"})
SETTING_OVERRIDES = frozenset({"model", "effort", "reasoningEffort", "serviceTier", "approvalPolicy",
                               "sandboxPolicy", "sandbox", "cwd", "config"})


class ResidentTui:
    def __init__(self, app, emit):
        self.app, self.emit = app, emit
        self.backend = None
        self.server = self.down = self.peer = None
        self.ready = threading.Event()
        self.stopped = threading.Event()
        self._lock = threading.RLock()
        self._send_lock = threading.Lock()
        self._pending = {}
        self._answers = {}
        self._sequence = 0
        self._resumed = False
        self._resume_request = None
        self._resume_fallback = False
        self._delivery_generation = None
        self._native_starts = {}
        self.connection_epoch = secrets.token_hex(16)
        self._aux_starts = {}
        self._reviews = {}
        self._commands = {}
        self._early_review_events = []
        self._review_overflow = False

    def observe(self, message):
        self._aux_notification(message)
        # The primary client owns the loaded thread subscription. Native TUI
        # bootstrap reads that object; it never resumes/replaces the Agent.
        if self.down is not None and self.ready.is_set() and not self.stopped.is_set():
            self._down_send(message)

    def start(self):
        # wrapper is executable from an arbitrary project directory.
        root = str(Path(__file__).resolve().parents[4])
        if root not in sys.path:
            sys.path.insert(0, root)
        from packages.core.terminal.backend import ConPtyBackend
        self._capability = secrets.token_urlsafe(32)
        def gate(connection, request):
            # Native client only. A browser must use Pan's existing authenticated
            # HTTP surface, never this private app-server protocol endpoint.
            if (request.path != "/" or request.headers.get("Origin") is not None
                    or not secrets.compare_digest(request.headers.get("Authorization", ""), "Bearer " + self._capability)):
                return Response(403, "Forbidden", Headers(), b"denied")
        self.server = serve(self._handle, "127.0.0.1", 0, process_request=gate,
                            max_size=MAX_MESSAGE_BYTES, close_timeout=.5)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        port = self.server.socket.getsockname()[1]
        url = f"ws://127.0.0.1:{port}"
        argv = [self.app.node, self.app.codex_js, "-c", "check_for_update_on_startup=false",
                "--remote", url, "--remote-auth-token-env", "PAN_NATIVE_TUI_CAPABILITY",
                "resume", self.app.thread_id]
        env = dict(os.environ, TERM="xterm-256color", PAN_NATIVE_TUI_CAPABILITY=self._capability)
        self.backend = ConPtyBackend.spawn(argv, cwd=self.app.cwd, env=env, rows=30, cols=120)
        threading.Thread(target=self._output, daemon=True).start()
        if not self.ready.wait(10):
            raise RuntimeError("Resident native TUI did not attach")

    def _down_send(self, message):
        with self._send_lock:
            if self.down is not None:
                self.down.send(json.dumps(message, ensure_ascii=True))

    def _deliver_pending(self):
        snapshot = self.app.control_owner.snapshot()
        if snapshot["mode"] != "native":
            return
        with self._lock:
            if self._delivery_generation != snapshot["generation"]:
                self._answers.clear()
                self._delivery_generation = snapshot["generation"]
            delivered = {str(answer[0]) for answer in self._answers.values()}
            for key, message in self._pending.items():
                if key in delivered:
                    continue
                self._sequence += 1
                routed = f"pan-native-{snapshot['generation']}-{self._sequence}"
                self._answers[routed] = (message["id"], snapshot["generation"])
                self._down_send({**message, "id": routed})

    def mode_changed(self):
        snapshot = self.app.control_owner.snapshot()
        with self._lock:
            if snapshot["mode"] != "native":
                self._answers.clear()
            else:
                for key, (message, _) in list(self.app._open_requests.items()):
                    self._pending[key] = message
        self._deliver_pending()

    def offer_request(self, message):
        with self._lock:
            if len(self._pending) >= 128 and str(message["id"]) not in self._pending:
                raise RuntimeError("Native pending request capacity exhausted")
            self._pending[str(message["id"])] = message
        self._deliver_pending()

    def resolve_request(self, request_id):
        with self._lock:
            self._pending.pop(str(request_id), None)
            for routed, answer in list(self._answers.items()):
                if str(answer[0]) == str(request_id):
                    self._answers.pop(routed, None)
                    self._down_send({"method": "serverRequest/resolved", "params": {"requestId": routed}})

    def _aux_emit(self, admission, event_type, **values):
        self.emit({"type": event_type, **admission.identity(), **values})

    def _aux_notification(self, message):
        if message.get("method") != "turn/completed":
            return
        params = message.get("params") or {}
        turn = params.get("turn") or {}
        binding = {"connectionEpoch": self.connection_epoch,
                   "threadId": params.get("threadId"), "turnId": turn.get("id")}
        if not binding["threadId"] or not binding["turnId"]:
            return
        with self._lock:
            for key, (admission, expected, done) in list(self._reviews.items()):
                if binding == expected:
                    if not done:
                        self._reviews[key] = (admission, expected, True)
                        self._aux_emit(admission, "pan.native_operation_terminal", binding=expected,
                                       proof="review-turn-completed")
                        self.app.retire_native_reservation(admission)
                    return
            if any(a.operation == "review/detached" for a in self._aux_starts.values()):
                if len(self._early_review_events) < 128:
                    self._early_review_events.append(message)
                else:
                    self._review_overflow = True

    def _aux_response(self, message):
        if "method" in message:
            return
        with self._lock:
            admission = self._aux_starts.pop(message.get("id"), None)
            if admission is None:
                return
            if "error" in message:
                admission.rejected()
                self.app.retire_native_reservation(admission)
                self._commands = {key: value for key, value in self._commands.items() if value is not admission}
                return
            if admission.operation == "command/exec":
                result = message.get("result") or {}
                if type(result.get("exitCode")) is int:
                    self._aux_emit(admission, "pan.native_operation_terminal", proof="command-deferred-final")
                    self.app.retire_native_reservation(admission)
                    self._commands = {key: value for key, value in self._commands.items() if value is not admission}
                else:
                    self._aux_emit(admission, "pan.native_operation_uncertain", reason="command-final-missing-exit-code")
            elif admission.operation in {"compact/active", "opaque"}:
                self._aux_emit(admission, "pan.native_operation_uncertain", reason=(
                    "compact-request-has-no-unique-resource-proof" if admission.operation == "compact/active"
                    else "rpc-replied-execution-retirement-unconfirmed"))
            elif admission.operation == "review/detached":
                result = message.get("result") or {}
                turn = result.get("turn") or {}
                binding = {"connectionEpoch": self.connection_epoch,
                           "threadId": result.get("reviewThreadId"), "turnId": turn.get("id")}
                if not binding["threadId"] or not binding["turnId"] or self._review_overflow:
                    self._aux_emit(admission, "pan.native_operation_uncertain", reason="review-resource-unconfirmed")
                    return
                self._reviews[admission.id] = (admission, binding, False)
                self._aux_emit(admission, "pan.native_operation_bind", binding=binding)
                for event in self._early_review_events:
                    self._aux_notification(event)
                if turn.get("status") in {"completed", "failed", "interrupted"}:
                    self._aux_notification({"method": "turn/completed", "params": {
                        "threadId": binding["threadId"], "turn": turn}})
                if not any(a.operation == "review/detached" for a in self._aux_starts.values()):
                    self._early_review_events.clear()

    def _from_server(self):
        try:
            for message in self.peer.messages():
                self._aux_response(message)
                self._aux_notification(message)
                if message.get("id") == self._resume_request and "error" in message and not self._resume_fallback:
                    # A new loaded thread has no rollout yet; includeTurns can
                    # fail until its first item. Read the live metadata instead.
                    self._resume_fallback = True
                    self.peer.send({"id": self._resume_request, "method": "thread/read", "params": {
                        "threadId": self.app.thread_id, "includeTurns": False}})
                    continue
                if message.get("id") in self._native_starts and "method" not in message:
                    admission = self._native_starts.pop(message["id"])
                    if "error" in message:
                        if admission.rejected():
                            self.app.retire_native_reservation(admission)
                            self.emit({"type": "pan.native_busy", "busy": False})
                        else:
                            self.emit({"type": "pan.native_error", "error": "native-start-rejection-conflict"})
                if message.get("id") == self._resume_request and "result" in message:
                    message = {**message, "result": {**self.app._thread_resume_info, **message["result"]}}
                    self.ready.set()
                if "method" in message and "id" in message:
                    if message["method"] == "currentTime/read":
                        import time
                        self.peer.send({"id": message["id"], "result": {"currentTimeAt": int(time.time())}})
                        continue
                    # Server requests are connection-scoped. Move them through
                    # the same Pan observation/approval lifecycle, while retaining
                    # the actual response connection (never reply on the wrong WS).
                    logical = "native-rpc-" + secrets.token_hex(12)
                    if len(self.app._reply_routes) >= 128:
                        raise RuntimeError("Native request capacity exhausted")
                    self.app._reply_routes[logical] = (message["id"], self.peer)
                    self.app.incoming.put({**message, "id": logical})
                    continue
                if message.get("method") == "serverRequest/resolved":
                    params = message.get("params") or {}
                    for logical, (original_id, transport) in list(self.app._reply_routes.items()):
                        if transport is self.peer and original_id == params.get("requestId"):
                            self.app.incoming.put({**message, "params": {**params, "requestId": logical}})
                            self.app._reply_routes.pop(logical, None)
                    with self._lock:
                        original = str(params.get("requestId"))
                        self._pending.pop(original, None)
                        routed = [key for key, answer in self._answers.items() if str(answer[0]) == original]
                        for key in routed:
                            self._answers.pop(key, None)
                            self._down_send({**message, "params": {**params, "requestId": key}})
                    continue
                if "method" in message and "id" not in message:
                    # Thread notifications are mirrored from the authoritative
                    # primary subscription, not emitted twice by two clients.
                    continue
                self._down_send(message)
        except Exception:
            if not self.stopped.is_set():
                self.emit({"type": "pan.native_error", "error": "native-client-disconnected"})

    def _handle(self, down):
        with self._lock:
            if self.down is not None:
                down.close(1008)
                return
            self.down = down
        try:
            self.peer = self.app._native_transport.peer(self.app.process.poll)
            threading.Thread(target=self._from_server, daemon=True).start()
            for raw in down:
                message = json.loads(raw)
                if not isinstance(message, dict):
                    continue
                method = message.get("method")
                try:
                    if method in READ_METHODS:
                        params = message.get("params") or {}
                        if (params.get("threadId", self.app.thread_id) != self.app.thread_id
                                and not any(binding["threadId"] == params.get("threadId")
                                            for _, binding, _ in self._reviews.values())):
                            raise ControlRejected("Foreign thread")
                        if (method in {"thread/turns/list", "thread/items/list"} and not self.app._thread_has_turns
                                and params.get("threadId") == self.app.thread_id):
                            # Exactly the empty, freshly-created live thread.
                            # There is no persisted history page until its first
                            # turn. Never apply this to resumed/unknown threads.
                            self._down_send({"id": message["id"], "result": {
                                "data": [], "nextCursor": None, "backwardsCursor": None}})
                        else:
                            self.peer.send(message)
                    elif method == "thread/resume":
                        if (message.get("params") or {}).get("threadId") != self.app.thread_id:
                            raise ControlRejected("Foreign thread")
                        self._resumed = True
                        self._resume_request = message.get("id")
                        self._resume_fallback = False
                        # Never let native bootstrap overwrite Pan's model,
                        # permission policy or developer instructions.
                        self.peer.send({**message, "method": "thread/read", "params": {
                            "threadId": self.app.thread_id, "includeTurns": True}})
                    elif method is None:
                        with self._lock:
                            answer = self._answers.pop(message.get("id"), None)
                        if answer is None:
                            continue
                        original, generation = answer
                        self.app.control_owner.write("native", lambda: self.app._raw_send({**message, "id": original}), generation=generation)
                        with self._lock:
                            self._pending.pop(str(original), None)
                    else:
                        params = message.get("params") or {}
                        if (method not in THREAD_WRITES and method not in CONTINUATION_METHODS
                                and not self.app.native_passthrough):
                            raise ControlRejected("Unsupported native mutation")
                        foreign = params.get("threadId", self.app.thread_id) != self.app.thread_id
                        if foreign and not (method in {"turn/interrupt", "turn/steer"} and any(
                                not done and binding["threadId"] == params.get("threadId")
                                and binding["turnId"] == params.get("turnId")
                                for _, binding, done in self._reviews.values())):
                            raise ControlRejected("Foreign thread")
                        generation = self.app.control_owner.snapshot()["generation"]
                        admission = None
                        active_compact = method == "thread/compact/start" and (
                            self.app._turn_active.is_set() or self.app._native_turn_pending.is_set())
                        opaque = (method not in READ_METHODS and method not in PURE_SETTING_METHODS
                                  and method not in CONTINUATION_METHODS
                                  and method not in {"turn/start", "thread/compact/start", "review/start"})
                        if method.startswith("command/exec/") and params.get("processId") not in self._commands:
                            opaque = True
                        auxiliary = (method == "command/exec" or active_compact or opaque
                                     or (method == "review/start" and params.get("delivery") == "detached"))
                        if auxiliary:
                            if method not in THREAD_WRITES and not self.app.native_passthrough:
                                raise ControlRejected("Unsupported native mutation")
                            kind = ("command/exec" if method == "command/exec" else
                                    "compact/active" if active_compact else "opaque" if opaque
                                    else "review/detached")
                            admission = self.app.control_owner.write("native", lambda:
                                self.app.reserve_auxiliary_admission(self.app.thread_id, generation, self.emit, kind),
                                generation=generation)
                            admission.context = {"connection": {"epoch": self.connection_epoch,
                                "rpcId": message.get("id"), "processId": params.get("processId"), "method": method}}
                            admission.request()
                            if not admission.wait(self.app.native_admission_timeout):
                                self.app.retire_native_reservation(admission)
                                raise ControlRejected("Auxiliary admission was not confirmed")
                        # Idle manual compaction creates a new protected turn.
                        # Active compaction has independent liability; no FIFO
                        # guess connects its empty RPC reply to a later turn.
                        if (method == "turn/start" or (method == "review/start" and params.get("delivery") != "detached")
                                or (method == "thread/compact/start" and not active_compact)):
                            def reserve():
                                if params.get("threadId") != self.app.thread_id:
                                    raise ControlRejected("Foreign thread")
                                try:
                                    value = self.app.reserve_native_admission(self.app.thread_id, generation, self.emit,
                                        "turn/start" if method == "review/start" else method)
                                except RuntimeError:
                                    raise ControlRejected("Turn already executing") from None
                                self.emit({"type": "pan.native_busy", "busy": True})
                                return value
                            admission = self.app.control_owner.write("native", reserve, generation=generation)
                            admission.request()
                            if not admission.wait(self.app.native_admission_timeout):
                                self.app.retire_native_reservation(admission)
                                self.emit({"type": "pan.native_busy", "busy": False})
                                raise ControlRejected("Native admission was not confirmed")
                        def mutate():
                            with self.app._native_policy_lock:
                                if method not in THREAD_WRITES and method not in CONTINUATION_METHODS and not self.app.native_passthrough:
                                    raise ControlRejected("Unsupported native mutation")
                                if foreign and not (method in {"turn/interrupt", "turn/steer"} and any(
                                        not done and binding["threadId"] == params.get("threadId")
                                        and binding["turnId"] == params.get("turnId")
                                        for _, binding, done in self._reviews.values())):
                                    raise ControlRejected("Foreign thread")
                                if admission is not None and (
                                        (self.app._aux_admissions.get(admission.id) is not admission if auxiliary
                                         else self.app._native_admission is not admission)
                                        or self.app.thread_id != admission.thread_id):
                                    raise ControlRejected("Native admission owner changed")
                                if method not in THREAD_WRITES or SETTING_OVERRIDES.intersection(params):
                                    self.emit({"type": "pan.native_settings_uncertain"})
                                if admission is None:
                                    self.peer.send(message)
                                else:
                                    with self._lock:
                                        starts = self._aux_starts if auxiliary else self._native_starts
                                        if message.get("id") in self._aux_starts or message.get("id") in self._native_starts:
                                            raise ControlRejected("RPC identity still executing")
                                        starts[message.get("id")] = admission
                                        if method == "command/exec" and params.get("processId") is not None:
                                            self._commands.setdefault(params["processId"], admission)
                                    try:
                                        admission.send(lambda: self.peer.send(message))
                                    except Exception:
                                        self.emit({"type": "pan.native_error", "error": "native-turn-unconfirmed"})
                                        raise
                        try:
                            self.app.control_owner.write("native", mutate, generation=generation)
                        except ControlRejected:
                            if admission is not None and admission.cancel():
                                self.app.retire_native_reservation(admission)
                                self.emit({"type": "pan.native_busy", "busy": False})
                            raise
                except ControlRejected:
                    if "id" in message and method is not None:
                        self._down_send({"id": message["id"], "error": {"code": -32000, "message": "Native control is not available"}})
        except Exception:
            if not self.stopped.is_set():
                self.emit({"type": "pan.native_error", "error": "native-client-disconnected"})

    def _output(self):
        try:
            while not self.stopped.is_set():
                data = self.backend.read(4096, timeout=.2)
                if data:
                    self.emit({"type": "pan.native_output", "data": base64.b64encode(data).decode("ascii")})
                elif self.backend.wait_dead(0):
                    if not self.stopped.is_set():
                        self.emit({"type": "pan.native_error", "error": "native-tui-exited"})
                    break
        except Exception:
            if not self.stopped.is_set():
                self.emit({"type": "pan.native_error", "error": "native-output-failed"})

    def input(self, text: str, generation: int):
        if not isinstance(text, str) or len(text.encode("utf-8")) > 65536:
            raise ControlRejected("Invalid native input")
        self.app.control_owner.write("native", lambda: self.backend.write(text.encode("utf-8")), generation=generation)

    def resize(self, rows: int, cols: int, generation: int):
        if type(rows) is not int or type(cols) is not int or not 1 <= rows <= 500 or not 1 <= cols <= 1000:
            raise ControlRejected("Invalid native size")
        self.app.control_owner.write("native", lambda: self.backend.resize(rows, cols), generation=generation)

    def close(self):
        self.stopped.set()
        if self.backend is not None:
            _, remaining = self.backend.guard.terminate_tree(self.backend.pid, timeout=2)
            if remaining or not self.backend.wait_dead(2):
                raise RuntimeError("Native TUI cleanup unconfirmed")
            if self.backend.close().get("closed") is not True:
                raise RuntimeError("Native TUI handle cleanup unconfirmed")
        if self.down is not None:
            self.down.close()
        if self.peer is not None:
            self.peer.close()
        if self.server is not None:
            self.server.shutdown()
