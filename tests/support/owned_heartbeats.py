"""Track heartbeat thread ownership across one test, including lost service state."""
from contextlib import contextmanager


@contextmanager
def owned_heartbeats(monkeypatch):
    from packages.core.terminal.service import _Heartbeat

    started = []
    original_start = _Heartbeat.start
    original_stop = _Heartbeat.stop

    def start(heartbeat):
        if heartbeat not in started:
            started.append(heartbeat)
        return original_start(heartbeat)

    monkeypatch.setattr(_Heartbeat, "start", start)
    try:
        yield started
    finally:
        # Stop every owned loop before joining any one of them. References
        # may have been removed by a simulated service restart, so service
        # state alone is not a complete teardown inventory.
        for heartbeat in started:
            heartbeat._stop.set()
        for heartbeat in started:
            assert original_stop(heartbeat, timeout=2), (
                f"owned heartbeat did not stop: {heartbeat.terminal_id}")
