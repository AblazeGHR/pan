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


@pytest.mark.parametrize("network_status", ["unknown", "listening"])
def test_status_without_pid_cannot_turn_network_unknown_into_stopped(monkeypatch, tmp_path, network_status):
    from packages.core import cli, launcher
    def unavailable(root):
        raise launcher.LauncherError("no recorded instance")
    monkeypatch.setattr(cli, "verified", unavailable)
    monkeypatch.setattr(launcher, "configured_port", lambda *a, **kw: 43210)
    monkeypatch.setattr(launcher, "listener_owner", lambda port: None)
    monkeypatch.setattr(launcher, "load_state", lambda root: {})
    monkeypatch.setattr(service_network, "needs_local_probe", lambda: True)
    monkeypatch.setattr(service_network, "loopback_listener", lambda port: {"status": network_status, "owner": None})
    assert cli.status(tmp_path)["state"] == "unknown"


def test_stop_without_pid_cannot_claim_unknown_listener_is_offline(monkeypatch, tmp_path):
    from packages.core import launcher
    monkeypatch.setattr(launcher, "load_state", lambda root: {})
    monkeypatch.setattr(launcher, "refresh_qq_state", lambda *a: None)
    monkeypatch.setattr(launcher, "listener_owner", lambda port: None)
    monkeypatch.setattr(launcher, "_cleanup_stale_children", lambda *a: pytest.fail("unknown is not offline authority"))
    monkeypatch.setattr(service_network, "needs_local_probe", lambda: True)
    monkeypatch.setattr(service_network, "loopback_listener", lambda port: {"status": "unknown", "owner": None})
    with pytest.raises(launcher.LauncherError, match="refusing offline success"):
        launcher.stop_service(tmp_path, 43210)
