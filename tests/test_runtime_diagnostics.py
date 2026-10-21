import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time
import socket
import urllib.request

import uvicorn

import pytest

from packages.core import runtime_diagnostics as diag
from packages.core import session
from packages.web import server


@pytest.fixture(autouse=True)
def isolated_diagnostics(monkeypatch):
    monkeypatch.setattr(diag, "runtime", diag.RuntimeDiagnostics())


def test_executor_exposes_running_queued_and_cancelled_work_without_contents():
    entered = threading.Event()
    release = threading.Event()
    def blocked():
        entered.set()
        assert release.wait(3)

    async def run():
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = asyncio.create_task(diag.run_in_executor("store-read", pool, blocked))
            try:
                assert await asyncio.to_thread(entered.wait, 1)
                second = asyncio.create_task(diag.run_in_executor("store-read", pool, lambda: None))
                await asyncio.sleep(0)
                snap = diag.runtime.snapshot()
                assert snap["executors"]["store-read"] == {"queued": 1, "running": 1,
                                                            "completed": 0, "lastWaitMs": snap["executors"]["store-read"]["lastWaitMs"]}
                assert {a["stage"] for a in snap["active"]} == {"queued", "running"}
                second.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await second
                for _ in range(10):
                    await asyncio.sleep(0)
                assert diag.runtime.snapshot()["executors"]["store-read"]["queued"] == 0
            finally:
                release.set()
                await first
        assert diag.runtime.snapshot()["active"] == []
    asyncio.run(run())


def test_diagnostic_writer_captures_loop_stall_and_pending_stage(tmp_path):
    monitor = diag.RuntimeDiagnostics(stall_seconds=0.1)
    async def run():
        async with monitor.monitor(tmp_path):
            token = monitor.begin("executor", pool="store-read", operation="blocked_read")
            time.sleep(0.65)  # deliberately stall the loop; monitor is independent
            monitor.pulse()
            monitor.end(token)
            await asyncio.sleep(0)
    asyncio.run(run())
    records = [json.loads(line) for line in (tmp_path / "runtime-diagnostics.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(x["kind"] == "runtime.stall" and x["snapshot"]["active"] for x in records)
    assert any(x["kind"] == "runtime.loop_recovered" for x in records)
    assert not monitor.snapshot()["monitorRunning"]


def test_records_are_bounded_and_stack_locations_have_no_local_values():
    monitor = diag.RuntimeDiagnostics(capacity=2)
    for _ in range(10):
        monitor.begin("executor")
    assert len(monitor.snapshot()["active"]) == 2
    assert monitor.snapshot()["droppedRecords"] == 8
    private_local_value = "diagnostic-secret-not-to-serialize"
    assert private_local_value not in json.dumps(monitor.thread_stacks())


def test_log_rotation_keeps_three_backups(tmp_path):
    monitor = diag.RuntimeDiagnostics()
    path = tmp_path / "runtime-diagnostics.jsonl"
    for _ in range(5):
        path.write_bytes(b"x" * (5 * 1024 * 1024))
        monitor._write(tmp_path, [{"kind": "new"}])
    assert (tmp_path / "runtime-diagnostics.jsonl.3").is_file()
    assert not (tmp_path / "runtime-diagnostics.jsonl.4").exists()
    assert json.loads(path.read_text(encoding="utf-8"))["kind"] == "new"


def test_http_tracks_headers_body_completion_and_errors_without_query_or_body():
    async def run():
        messages = []
        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"private body", "more_body": True})
            raise RuntimeError("private exception text")
        async def send(message):
            messages.append(message)
        with pytest.raises(RuntimeError):
            await server.HTTPPolicyMiddleware(app)(
                {"type": "http", "path": "/api/sessions", "method": "GET",
                 "query_string": b"secret=query"}, None, send)
        assert b"x-pan-request-id" in dict(messages[0]["headers"])
        end = [x for x in diag.runtime.snapshot()["recent"] if x["kind"] == "http.end"][-1]
        assert end["outcome"] == "error" and end["complete"] is False and end["bodyBytes"] == 12
        serialized = json.dumps(diag.runtime.snapshot())
        assert "secret=query" not in serialized and "private body" not in serialized
        assert "private exception text" not in serialized
    asyncio.run(run())


def test_blocked_console_cannot_hold_response_or_grow_log_backlog(monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    writes = []
    def blocked(message):
        writes.append(message)
        entered.set()
        assert release.wait(3)
    monkeypatch.setattr(server, "_log", blocked)
    monkeypatch.setattr(server, "_LOG_SKIP", [])
    monkeypatch.setattr(server, "_HTTP_LOG_SLOT", threading.BoundedSemaphore(1))
    with ThreadPoolExecutor(max_workers=1) as pool:
        monkeypatch.setattr(server, "_HTTP_LOG_EXECUTOR", pool)
        async def run():
            async def app(scope, receive, send):
                await send({"type": "http.response.start", "status": 200, "headers": []})
                await send({"type": "http.response.body", "body": b"complete"})
            delivered = []
            async def send(message):
                delivered.append(message)
            started = time.monotonic()
            await server.HTTPPolicyMiddleware(app)({"type": "http", "path": "/react/", "method": "GET"}, None, send)
            assert entered.is_set() and delivered[-1]["body"] == b"complete"
            assert time.monotonic() - started < 0.5
            await server._write_access_log("second")
            assert len(writes) == 1
            assert any(x["kind"] == "http.log_dropped" for x in diag.runtime.snapshot()["recent"])
        try:
            asyncio.run(run())
        finally:
            release.set()


def test_list_projects_both_result_and_history_off_loop_without_mutating_session(monkeypatch):
    value = session.Session(id="ses-list-links", name="links", adapter="codex")
    value.last_result = {"result": "last body"}
    session.append_history(value, {"role": "assistant", "content": "history body"})
    session._cache[value.id] = value
    session.save_full(value)
    loop_thread = threading.get_ident()
    seen = []
    def project(sid, text):
        assert threading.get_ident() != loop_thread
        seen.append(text)
        return "projected:" + text
    monkeypatch.setattr(server, "_project_editor_links", project)
    result = asyncio.run(server.api_list_sessions(summary=0))
    row = result["sessions"][0]
    assert row["lastResult"]["result"] == "projected:last body"
    assert row["history"][0]["content"] == "projected:history body"
    assert value.last_result["result"] == "last body"
    assert value.history[0]["content"] == "history body"
    assert "last body" in seen and "history body" in seen


def test_persistence_diagnostics_does_not_take_store_locks_on_loop(monkeypatch):
    loop_thread = threading.get_ident()
    def snapshot(*args, **kwargs):
        assert threading.get_ident() != loop_thread
        return {"totalQueued": 2}
    monkeypatch.setattr(session, "save_diagnostics", snapshot)
    assert asyncio.run(server.api_persistence_diagnostics()) == {"totalQueued": 2}


def test_real_http_exposes_blocked_read_while_liveness_and_diagnostics_work(tmp_path, monkeypatch):
    from packages.core.host_server import PanServer
    from packages.core import logging_setup
    monkeypatch.setattr(logging_setup, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(server, "_LOG_SKIP", ["/"])
    value = session.Session(id="ses-live-probe", name="probe", adapter="codex")
    session._cache[value.id] = value
    session.save_full(value)
    entered = threading.Event()
    release = threading.Event()
    def blocked():
        entered.set()
        assert release.wait(5)

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    def get(path):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}" + path, timeout=2) as response:
            return json.loads(response.read())

    async def run():
        host = PanServer(uvicorn.Config(server.app, log_level="warning", access_log=False,
                                        lifespan="off", timeout_graceful_shutdown=2))
        host_task = asyncio.create_task(host.serve(sockets=[sock]))
        blocked_task = None
        try:
            for _ in range(100):
                if host.started:
                    break
                await asyncio.sleep(0.01)
            assert host.started
            blocked_task = asyncio.create_task(server._store_read(blocked))
            assert await asyncio.to_thread(entered.wait, 1)
            listing = asyncio.create_task(asyncio.to_thread(get, "/api/sessions?summary=1"))
            for _ in range(100):
                if diag.runtime.snapshot()["executors"]["store-read"]["queued"]:
                    break
                await asyncio.sleep(0.01)
            health = await asyncio.to_thread(get, "/api/health")
            snapshot = await asyncio.to_thread(get, "/api/diagnostics/runtime")
            assert health["status"] == "ok"
            assert snapshot["monitorRunning"]
            assert snapshot["executors"]["store-read"]["queued"] >= 1
            assert any(x["operation"].endswith("blocked") for x in snapshot["active"] if x["kind"] == "executor")
            assert any(x["function"] == "blocked" for t in snapshot["threads"] for x in t["frames"])
            release.set()
            assert (await listing)["sessions"][0]["id"] == value.id
        finally:
            release.set()
            if blocked_task is not None:
                await blocked_task
            host.should_exit = True
            await asyncio.wait_for(host_task, 5)
            sock.close()
    asyncio.run(run())
    assert (tmp_path / "data/logs/runtime-diagnostics.jsonl").is_file()
