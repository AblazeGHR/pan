"""Same-port, different-address listeners must never select a foreign service."""
from types import SimpleNamespace

import pytest

from packages.core import launcher


def connection(host, pid, port=25431):
    return SimpleNamespace(laddr=SimpleNamespace(ip=host, port=port), status="LISTEN", pid=pid)


@pytest.mark.parametrize("reverse", [False, True])
def test_loopback_listener_ignores_other_addresses_regardless_of_order(monkeypatch, reverse):
    import psutil
    entries = [connection("100.100.1.1", 90), connection("127.0.0.1", 41),
               connection("fd7a:115c::1", 90)]
    monkeypatch.setattr(psutil, "net_connections", lambda **kw: list(reversed(entries)) if reverse else entries)
    assert launcher.listener_owner(25431) == 41


def test_other_address_alone_does_not_occupy_loopback(monkeypatch):
    import psutil
    monkeypatch.setattr(psutil, "net_connections", lambda **kw: [connection("100.100.1.1", 90)])
    assert launcher.listener_owner(25431) is None


@pytest.mark.parametrize("host,entries,expected", [
    ("127.0.0.1", [("0.0.0.0", 41)], 41),
    ("127.0.0.1", [("::ffff:127.0.0.1", 41)], 41),
    ("::1", [("::1", 41), ("fd7a::1", 90)], 41),
    ("::1", [("::", 41)], 41),
    ("127.0.0.1", [("::", 41)], 41),
    ("0.0.0.0", [("100.100.1.1", 90)], 90),
    ("localhost", [("::1", 41), ("127.0.0.1", 41)], 41),
])
def test_endpoint_matching_contract(host, entries, expected):
    from packages.core.platform.listeners import select_owner
    assert select_owner([connection(h, pid) for h, pid in entries], 25431, host) == expected


@pytest.mark.parametrize("entries", [
    [("0.0.0.0", 41), ("127.0.0.1", 90)],
    [("127.0.0.1", None)],
    [("::", 90), ("127.0.0.1", 41)],
])
def test_unknown_and_conflicting_owners_are_errors(entries):
    from packages.core.platform.listeners import select_owner, ListenerQueryError
    with pytest.raises(ListenerQueryError):
        select_owner([connection(h, pid) for h, pid in entries], 25431, "127.0.0.1")


def test_enumeration_denied_is_not_reported_as_no_listener(monkeypatch):
    import psutil
    def denied(**kwargs):
        raise psutil.AccessDenied()
    monkeypatch.setattr(psutil, "net_connections", denied)
    with pytest.raises(launcher.LauncherError, match="enumeration unavailable"):
        launcher.listener_owner(25431)


def test_recorded_binding_is_not_overridden_by_agent_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("PAN_HOST", "100.100.1.1")
    launcher.save_state(tmp_path, {"bindHost": "::1"})
    assert launcher.binding_host(tmp_path) == "::1"


def test_real_distinct_loopback_addresses_same_ephemeral_port():
    import socket
    import os
    from packages.core.platform.listeners import listener_owner
    with socket.socket() as first, socket.socket() as second:
        first.bind(("127.0.0.1", 0))
        first.listen()
        port = first.getsockname()[1]
        second.bind(("127.0.0.2", port))
        second.listen()
        assert listener_owner(port, "127.0.0.1") == os.getpid()
        assert listener_owner(port, "127.0.0.2") == os.getpid()


def test_internal_shutdown_is_bound_to_verified_record(monkeypatch, tmp_path):
    from packages.core import local_transport
    seen = []
    def request(root, method, path, body, instance=None):
        seen.append((root, method, path, body, instance))
        return {"ok": True}
    monkeypatch.setattr(local_transport, "request", request)
    identity = {"pid": 41, "createdAt": 12.5}
    assert launcher._request_internal_shutdown(25431, None, tmp_path, instance=identity) == (True, None)
    assert seen == [(tmp_path, "POST", "/api/internal/main/shutdown", {}, identity)]


@pytest.mark.parametrize("path", ["/api/system-actions/main-lifecycle", "/api/main/exit", "/api/main/restart", "/api/internal/main/shutdown"])
def test_stale_ipc_identity_cannot_close_successor(path):
    import asyncio
    from packages.web import server
    sent = []
    async def app(scope, receive, send):
        pytest.fail("stale identity reached lifecycle endpoint")
    async def send(message):
        sent.append(message)
    asyncio.run(server.CLIInstanceGuardMiddleware(app)(
        {"type": "http", "method": "POST", "path": path,
         "headers": [(b"x-pan-expected-pid", b"999999999"), (b"x-pan-expected-created-at", b"0")]},
        None, send))
    assert sent[0]["status"] == 409
