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
else:
    from control_owner import ControlRejected

READ_METHODS = frozenset({
    "initialize", "initialized", "account/read", "account/rateLimits/read", "model/list",
    "config/read", "config/requirements/read", "configRequirements/read", "config/mcpServer/reload", "mcpServerStatus/list",
    "plugin/list", "skills/list", "collaborationMode/list", "hooks/list", "thread/read", "thread/list", "thread/loaded/list",
    "thread/items/list", "thread/turns/list", "thread/goal/get", "app/list",
}) - {"config/mcpServer/reload"}  # Reload mutates running tools: not a read.


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
        self._native_starts = set()

    def observe(self, message):
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
                            max_size=16 * 1024 * 1024, close_timeout=.5)
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

    def _from_server(self):
        try:
            for message in self.peer.messages():
                if message.get("id") == self._resume_request and "error" in message and not self._resume_fallback:
                    # A new loaded thread has no rollout yet; includeTurns can
                    # fail until its first item. Read the live metadata instead.
                    self._resume_fallback = True
                    self.peer.send({"id": self._resume_request, "method": "thread/read", "params": {
                        "threadId": self.app.thread_id, "includeTurns": False}})
                    continue
                if message.get("id") in self._native_starts and "method" not in message:
                    self._native_starts.discard(message["id"])
                    if "error" in message:
                        self.app._native_turn_pending.clear()
                        self.emit({"type": "pan.native_busy", "busy": False})
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
                        if params.get("threadId", self.app.thread_id) != self.app.thread_id:
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
                        if params.get("threadId", self.app.thread_id) != self.app.thread_id:
                            raise ControlRejected("Foreign thread")
                        # Limit native writes to the interaction surface needed
                        # for this thread, never config/auth/other-thread writes.
                        if method not in {"turn/start", "turn/steer", "turn/interrupt", "thread/compact/start",
                                          "command/exec/write", "command/exec/terminate"}:
                            raise ControlRejected("Unsupported native mutation")
                        def mutate():
                            if method == "turn/start":
                                if self.app._turn_active.is_set() or self.app._native_turn_pending.is_set():
                                    raise ControlRejected("Turn already executing")
                                self.app._native_turn_pending.set()
                                self._native_starts.add(message.get("id"))
                                self.emit({"type": "pan.native_busy", "busy": True})
                            try:
                                self.peer.send(message)
                            except Exception:
                                if method == "turn/start":
                                    # Delivery is ambiguous: keep the busy fence.
                                    self.emit({"type": "pan.native_error", "error": "native-turn-unconfirmed"})
                                raise
                        self.app.control_owner.write("native", mutate)
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
