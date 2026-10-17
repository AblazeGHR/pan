"""One private pre-send admission; no provider bytes until durable Pan ACK."""
from __future__ import annotations

import secrets
import threading


class NativeAdmission:
    def __init__(self, thread_id, generation, emit, operation="turn/start"):
        self.id = secrets.token_hex(16)
        self.thread_id, self.generation, self.emit = thread_id, generation, emit
        self.operation = operation
        self.run_id = None
        self.turn_id = None
        self.state = "waiting"
        self.lock = threading.RLock()
        self.ready = threading.Event()

    def request(self):
        self.emit({"type": "pan.native_turn_admission", "requestId": self.id,
                   "threadId": self.thread_id, "controlGeneration": self.generation,
                   "operation": self.operation})

    def acknowledge(self, message):
        with self.lock:
            if self.state != "waiting":
                return
            run_id = message.get("runId")
            if message.get("ok") is True and isinstance(run_id, str) and run_id:
                self.run_id, self.state = run_id, "granted"
            else:
                self.state = "denied"
            self.ready.set()

    def cancel(self):
        with self.lock:
            if self.state not in {"waiting", "granted", "denied"}:
                return False
            self.state = "cancelled"
            self.ready.set()
            self.emit({"type": "pan.native_turn_not_forwarded", "requestId": self.id,
                       "runId": self.run_id})
            return True

    def wait(self, timeout):
        self.ready.wait(timeout)
        with self.lock:
            granted = self.state == "granted"
        if not granted:
            self.cancel()
        return granted

    def send(self, operation):
        with self.lock:
            if self.state != "granted":
                raise RuntimeError("Native admission is not granted")
            # Any attempted send may reach the provider, including an exception.
            self.state = "sending"
        try:
            operation()
        except BaseException:
            with self.lock:
                if self.state == "sending":
                    self.state = "unknown"
            raise

    def started(self, turn_id):
        with self.lock:
            if self.state not in {"sending", "unknown", "started"}:
                return False
            if self.turn_id is not None and self.turn_id != turn_id:
                return False
            self.turn_id, self.state = turn_id, "started"
            return True

    def rejected(self):
        with self.lock:
            if self.state not in {"sending", "unknown"}:
                return False
            self.state = "rejected"
            self.emit({"type": "pan.native_turn_not_forwarded", "requestId": self.id,
                       "runId": self.run_id, "providerRejected": True})
            return True

    def identity(self):
        return {"requestId": self.id, "runId": self.run_id}
