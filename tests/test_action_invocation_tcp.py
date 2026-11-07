"""Real PanServer TCP disconnects and shutdown; no TestClient substitute.

Each server owns a temporary profile, dynamic loopback port and ProcessOwner
tree. The command waits on a file gate so disconnect precedes its final effect.
"""
import asyncio
import json
import os
from pathlib import Path
import socket
import sys
import time

import httpx
import pytest

from packages.core.platform.process import default_process_owner


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "tests/support/isolated_http_server.py"


def until(predicate, *, timeout=20):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = predicate()
            if last:
                return last
        except (httpx.HTTPError, OSError) as exc:
            last = exc
        time.sleep(.025)
    raise AssertionError(f"Owned TCP deadline exceeded: {last!r}")


class OwnedPan:
    def __init__(self, root, grace=None):
        self.root, self.grace = root, grace
        self.owner = default_process_owner()
        self.handle = None

    def start(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            self.port = reservation.getsockname()[1]
        assert self.port not in {8767, 8768}
        self.url = f"http://127.0.0.1:{self.port}"
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith("PAN_")}
        env.update(PYTHONPATH=str(ROOT), PAN_E2E_DATA_ROOT=str(self.root), PAN_TEST_DISABLE_QUEUE_WATCHDOG="1")
        argv = [sys.executable, str(LAUNCHER), "--port", str(self.port), "--data-root", str(self.root)]
        if self.grace is not None:
            argv += ["--graceful-timeout", str(self.grace)]
        with (self.root / "tcp-server.log").open("ab") as log:
            self.handle = self.owner.spawn(argv, cwd=str(ROOT), env=env, stdout=log, stderr=log)
        try:
            until(lambda: httpx.get(self.url + "/api/health", trust_env=False, timeout=.5).status_code == 200,
                  timeout=30)
            key = (self.root / "automation/user.key").read_text(encoding="utf-8").strip()
            response = httpx.post(self.url + "/api/automation/login", json={"key": key}, trust_env=False)
            assert response.status_code == 200, response.text
            self.headers = {"Authorization": "Bearer " + response.json()["token"]}
        except BaseException:
            self.cleanup()
            raise

    def request(self, method, path, **kwargs):
        response = httpx.request(method, self.url + path, headers=self.headers,
                                timeout=5, trust_env=False, **kwargs)
        assert response.status_code in {200, 201, 202}, response.text
        return response.json()

    def original(self, key):
        return self.request("GET", "/api/operations", params={"operation": "invoke", "requestKey": key})

    def send_socket(self, path, body):
        raw = json.dumps(body).encode()
        peer = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        headers = (f"POST {path} HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n"
                   f"Authorization: {self.headers['Authorization']}\r\nContent-Type: application/json\r\n"
                   f"Content-Length: {len(raw)}\r\nConnection: close\r\n\r\n").encode()
        peer.sendall(headers + raw)
        return peer

    def stop_request(self):
        self.request("POST", "/__test/stop-owned-runtime")

    def join(self):
        ended = self.owner.wait(self.handle, timeout=20)
        assert ended.exited and ended.exit_code == 0, (ended, (self.root / "tcp-server.log").read_text(errors="replace")[-6000:])

    def cleanup(self):
        if self.handle is not None:
            try:
                tree = self.owner.kill_tree(self.handle, timeout=5)
                assert tree.confirmed and not tree.remaining, tree
            finally:
                self.handle.close()
                self.handle = None


@pytest.fixture
def owned(tmp_path):
    servers = []
    def start(*, grace=None):
        server = OwnedPan(tmp_path / "profile", grace)
        servers.append(server)
        server.start()
        return server
    yield start
    for server in reversed(servers):
        if server.handle is not None:
            # Try normal lifespan shutdown first. A failed test still only
            # cleans the process tree created through this retained handle.
            try:
                if not server.owner.wait(server.handle, timeout=0).exited:
                    server.stop_request()
                    server.owner.wait(server.handle, timeout=2)
            except (AssertionError, httpx.HTTPError):
                pass
            server.cleanup()


def invocation(server, directory, key):
    catalog = server.request("GET", "/api/jobs/actions")
    ref = catalog["references"]["product_process"]
    # A real descendant must also retire if the owning request is cancelled
    # by a server shutdown deadline. The final effect remains behind a gate.
    code = ("import subprocess,sys,time,os,json;from pathlib import Path;"
            "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
            "Path('started').write_text(json.dumps({'pid':os.getpid(),'child':child.pid}));"
            "\nwhile not Path('release').exists(): time.sleep(.02)\n"
            "with Path('effect').open('a') as stream: stream.write('x')\n"
            "print('completed after caller disconnect',flush=True)")
    body = {"actionRef": ref, "requestKey": key,
            "arguments": {"argv": [sys.executable, "-u", "-c", code], "cwd": str(directory)}}
    return "/api/jobs/actions/product_process/invocations", body


def disconnect(peer):
    peer.shutdown(socket.SHUT_RDWR)
    peer.close()


def cold_query(start, server, operation, path, body):
    server.join()
    server.cleanup()
    cold = start()
    queried = cold.request("GET", "/api/operations/" + operation["operationId"])
    assert queried == cold.original(body["requestKey"])
    assert queried["operationId"] == operation["operationId"]
    replayed = cold.request("POST", path, json=body)
    assert replayed["operationId"] == queried["operationId"] and replayed["replayed"] is True
    assert not cold.request("GET", "/api/jobs")["items"]  # No hidden Job.
    cold.stop_request()
    cold.join()
    return queried


def evidence(case, original, cold, directory):
    row = cold["result"]["commands"][0]
    print(json.dumps({"case": case, "requestKey": cold["requestKey"],
        "operationId": cold["operationId"], "coldSameIdentity": original["operationId"] == cold["operationId"],
        "commandStatus": row["status"], "exitCode": row["exitCode"],
        "effectCount": len((directory / "effect").read_text()) if (directory / "effect").exists() else 0,
        "cleanupConfirmed": row["cleanupProof"]["confirmed"],
        "remaining": row["cleanupProof"]["ownedTree"]["remaining"]}), flush=True)


@pytest.mark.parametrize("caller", ["socket-close", "httpx-task-cancel"])
def test_real_tcp_disconnect_completes_and_cold_queries_original(owned, tmp_path, caller):
    server = owned()
    directory = tmp_path / "effects"
    directory.mkdir()
    path, body = invocation(server, directory, "tcp-disconnect")
    if caller == "socket-close":
        peer = server.send_socket(path, body)
        try:
            until(lambda: (directory / "started").exists())
            original = server.original(body["requestKey"])
        finally:
            disconnect(peer)
    else:
        async def cancel_client():
            async with httpx.AsyncClient(base_url=server.url, headers=server.headers, timeout=15, trust_env=False) as client:
                task = asyncio.create_task(client.post(path, json=body))
                try:
                    for _ in range(400):
                        if (directory / "started").exists():
                            break
                        await asyncio.sleep(.025)
                    assert (directory / "started").exists()
                    observed = await client.get("/api/operations", params={"operation": "invoke", "requestKey": body["requestKey"]})
                    assert observed.status_code == 200, observed.text
                    return observed.json()
                finally:
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
        original = asyncio.run(cancel_client())
    assert original["status"] == "accepted" and original["result"]["phase"] == "calling"
    assert not (directory / "effect").exists()
    # A duplicate while the original TCP peer is gone must not create another
    # command; it returns the very same in-progress operation.
    duplicate = server.request("POST", path, json=body)
    assert duplicate["replayed"] and duplicate["operationId"] == original["operationId"]
    (directory / "release").touch()
    completed = until(lambda: (result if (result := server.original(body["requestKey"]))["status"] == "completed" else None))
    row = completed["result"]["commands"][0]
    assert row["status"] == "completed" and row["exitCode"] == 0
    assert row["cleanupProof"]["confirmed"] is True and row["cleanupProof"]["ownedTree"]["remaining"] == []
    assert row["stdout"].strip() == "completed after caller disconnect"
    assert (directory / "effect").read_text() == "x"
    server.stop_request()
    queried = cold_query(owned, server, original, path, body)
    assert queried == completed and (directory / "effect").read_text() == "x"
    evidence(caller, original, queried, directory)


@pytest.mark.parametrize("grace", [None, .1], ids=["graceful-drain", "shutdown-deadline-cancel"])
def test_real_tcp_server_shutdown_boundary(owned, tmp_path, grace):
    server = owned(grace=grace)
    directory = tmp_path / "effects"
    directory.mkdir()
    path, body = invocation(server, directory, "tcp-shutdown")
    peer = server.send_socket(path, body)
    try:
        until(lambda: (directory / "started").exists())
        original = server.original(body["requestKey"])
    finally:
        disconnect(peer)
    server.stop_request()
    if grace is None:
        time.sleep(.3)
        assert not server.owner.wait(server.handle, timeout=0).exited
        assert not (directory / "effect").exists()
        (directory / "release").touch()
    queried = cold_query(owned, server, original, path, body)
    row = queried["result"]["commands"][0]
    assert row["cleanupProof"]["confirmed"] is True and row["cleanupProof"]["ownedTree"]["remaining"] == []
    if grace is None:
        assert row["status"] == "completed" and row["exitCode"] == 0
        assert (directory / "effect").read_text() == "x"
    else:
        assert row["status"] == "cancelled" and row["code"] == "cancelled"
        assert not (directory / "effect").exists()
        assert "graceful shutdown exceeded" in (server.root / "tcp-server.log").read_text(errors="replace").lower()
    evidence("graceful-drain" if grace is None else "shutdown-deadline-cancel", original, queried, directory)
