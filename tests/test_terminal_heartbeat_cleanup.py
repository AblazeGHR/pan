"""A fake owner's daemon thread must not survive its test scope."""
import threading
from types import SimpleNamespace

from packages.core.terminal.service import _Heartbeat
from tests.support.owned_heartbeats import owned_heartbeats


def test_scope_retires_heartbeat_even_when_service_loses_reference(tmp_path, monkeypatch):
    beat = threading.Event()
    released = threading.Event()
    client = SimpleNamespace(
        heartbeat=lambda **kwargs: (beat.set() or {"status": "ok"}),
        release_connection=released.set,
    )
    heartbeat = _Heartbeat(client_factory=lambda *a, **kw: client,
                           terminal_id="term_owned_cleanup", data_root=tmp_path,
                           client_id="owned", interval=5, grace=5)
    try:
        with owned_heartbeats(monkeypatch) as started:
            heartbeat.start()
            assert beat.wait(1), "owned heartbeat did not start"
            assert started == [heartbeat]
        assert not heartbeat._thread.is_alive(), "test leaked a daemon owner heartbeat"
        assert released.is_set(), "owned heartbeat connection was not released"
    finally:
        heartbeat.stop(timeout=2)
