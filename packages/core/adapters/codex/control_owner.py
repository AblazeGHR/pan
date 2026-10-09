"""Process-local control fencing; never owns or terminates an Agent process."""
from __future__ import annotations

import threading
from typing import Callable, TypeVar

T = TypeVar("T")


class ControlRejected(RuntimeError):
    pass


class ControlOwner:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._mode = "pan"
        self._generation = 0

    def snapshot(self) -> dict:
        with self._lock:
            return {"mode": self._mode, "generation": self._generation}

    def switch(self, mode: str, expected_generation: int) -> dict:
        if mode not in {"pan", "native"}:
            raise ControlRejected("Invalid control mode")
        if type(expected_generation) is not int:
            raise ControlRejected("Invalid control generation")
        with self._lock:
            if expected_generation != self._generation:
                raise ControlRejected("Stale control generation")
            if mode != self._mode:
                self._generation += 1
                self._mode = mode
            return self.snapshot()

    def write(self, source: str, operation: Callable[[], T], *, generation: int | None = None) -> T:
        # Check and actual handoff are one critical section. A switch cannot
        # acknowledge while an already-admitted write is still being handed off.
        with self._lock:
            if source != self._mode or (generation is not None and generation != self._generation):
                raise ControlRejected("Control is owned by another mode or generation")
            return operation()
