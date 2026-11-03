"""No-root listener probe controls: missing visibility is never absence."""
import socket
from types import SimpleNamespace

import psutil
import pytest

from packages.core.platform import service_network


def test_per_process_listener_returns_observed_owner(monkeypatch):
    connection = SimpleNamespace(laddr=SimpleNamespace(port=43210, ip="127.0.0.1"), status="LISTEN")
    process = SimpleNamespace(pid=42, net_connections=lambda **kw: [connection])
    monkeypatch.setattr(psutil, "process_iter", lambda: [process])
    assert service_network.loopback_listener(43210) == {"status": "listening", "owner": 42, "scope": "127.0.0.1"}


def test_inaccessible_listener_cannot_be_declared_offline(monkeypatch):
    # Real bound/listening socket, deliberately invisible to process enumeration.
    monkeypatch.setattr(psutil, "process_iter", lambda: [])
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0)); listener.listen(1)
        result = service_network.loopback_listener(listener.getsockname()[1])
    assert result["status"] == "unknown" and result["owner"] is None


def test_missing_process_visibility_requires_exclusive_listen(monkeypatch):
    monkeypatch.setattr(psutil, "process_iter", lambda: [])
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    result = service_network.loopback_listener(port)
    assert result["status"] == "offline" and result["method"] == "exclusive_loopback_listen"


def test_probe_permission_failure_stays_unknown(monkeypatch):
    monkeypatch.setattr(psutil, "process_iter", lambda: [])
    def denied(*a, **kw):
        raise PermissionError("synthetic denied")
    monkeypatch.setattr(socket, "socket", denied)
    assert service_network.loopback_listener(43210)["status"] == "unknown"
