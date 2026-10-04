"""Natural real-output pressure: a non-reading observer must not stall the PTY."""
import asyncio
import base64
import json
import socket
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="real ConPTY")


def test_real_slow_observer_does_not_stall_fast_connection_or_shell(tmp_path):
    sidecar = Path(__file__).resolve().parents[1] / "packages/core/terminal/emulator_sidecar/node_modules/@xterm/headless/package.json"
    if not sidecar.exists():
        pytest.skip("sidecar dependencies absent")

    async def scenario():
        import httpx
        import uvicorn
        import websockets
        from fastapi import FastAPI
        from packages.core.terminal.service import TerminalService
        from packages.web import terminal_api, terminal_ws

        with socket.socket() as reserve:
            reserve.bind(("127.0.0.1", 0))
            port = reserve.getsockname()[1]
        assert port != 8768
        origin = f"http://127.0.0.1:{port}"
        service = TerminalService(str(tmp_path / "terminals"), log_stderr=False)
        runtime = terminal_api.build_runtime(env={}, platform=sys.platform, host="127.0.0.1", port=port,
                                             service_factory=lambda: service, shutdown_budget=20)

        @asynccontextmanager
        async def lifespan(app):
            await terminal_api.start_runtime(app, runtime=runtime)
            try:
                async with terminal_ws.websocket_lifespan(app, runtime=runtime):
                    yield
            finally:
                await terminal_api.stop_runtime(app, runtime=runtime)

        app = FastAPI(lifespan=lifespan)
        app.include_router(terminal_api.router)
        app.include_router(terminal_ws.router)
        host = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                             log_level="error", timeout_graceful_shutdown=5))
        task = asyncio.create_task(host.serve())
        terminal_id = None
        try:
            for _ in range(300):
                if host.started and runtime.state == "ready":
                    break
                await asyncio.sleep(.1)
            assert host.started
            async with httpx.AsyncClient(base_url=origin, headers={"Origin": origin}, timeout=30, trust_env=False) as client:
                created = await client.post("/api/terminals", json={"rows": 24, "cols": 120, "cwd": str(tmp_path)})
                assert created.status_code == 200, created.text
                terminal_id = created.json()["result"]["terminal_id"]
                original = service.get(terminal_id)
                script, done = tmp_path / "produce.py", tmp_path / "done"
                script.write_text("import sys,time\nfrom pathlib import Path\n"
                                  "for i in range(60000):\n print(f'{i:06d}|'+('x'*80))\n"
                                  " if i%100==0: sys.stdout.flush(); time.sleep(.01)\n"
                                  "print('PRESSURE_FINAL_MARKER');sys.stdout.flush()\n"
                                  "Path(sys.argv[1]).write_text('done')\n", encoding="utf-8")
                url = f"ws://127.0.0.1:{port}/ws/terminal/{terminal_id}"
                async with websockets.connect(url, origin=origin, max_queue=1) as slow, \
                           websockets.connect(url, origin=origin) as fast:
                    assert json.loads(await slow.recv())["type"] == "hello"
                    assert json.loads(await fast.recv())["type"] == "hello"
                    # Slow observer deliberately stops consuming here. No fake sender or
                    # injected transport delay; only its natural receive queue is bounded.
                    async def command(op, **fields):
                        await fast.send(json.dumps({"v": 1, "type": "command", "terminal_id": terminal_id,
                                                    "op": op, **fields}))
                    await command("claim")
                    while True:
                        event = json.loads(await asyncio.wait_for(fast.recv(), 20))
                        if event["type"] == "claim-result":
                            generation = event["generation"]
                            break
                    invocation = f'"{sys._base_executable}" "{script}" "{done}"\r'
                    await command("input", generation=generation,
                                  data_b64=base64.b64encode(invocation.encode()).decode())
                    deadline = time.monotonic() + 40
                    bytes_seen, gaps, marker, tail = 0, 0, False, b""
                    while time.monotonic() < deadline and not marker:
                        event = json.loads(await asyncio.wait_for(fast.recv(), 10))
                        if event["type"] == "output":
                            data = base64.b64decode(event["data_b64"])
                            bytes_seen += len(data)
                            tail = (tail + data)[-256:]
                            marker = b"PRESSURE_FINAL_MARKER" in tail
                            await command("ack", next_seq=event["next_seq"])
                        elif event["type"] == "gap":
                            gaps += 1
                            await command("snapshot", timeout_ms=2000)
                        elif event["type"] == "snapshot":
                            assert event["recovery"] != "full" or event["fidelity"] == "full"
                            if event.get("cursors_valid") is True and not event.get("applied_evicted"):
                                marker = b"PRESSURE_FINAL_MARKER" in base64.b64decode(event["data_b64"])
                                await command("resume", cursor=event["cursor"])
                            else:
                                await command("snapshot", timeout_ms=2000)
                        elif event["type"] == "error":
                            assert event["code"] == "busy", event
                    assert marker
                    for _ in range(100):
                        if done.exists():
                            break
                        await asyncio.sleep(.05)
                    assert done.read_text() == "done"
                    assert bytes_seen > 262144, (bytes_seen, gaps)
                    current = service.get(terminal_id)
                    assert current["pid"] == original["pid"]
                    assert current["process_created_at_filetime"] == original["process_created_at_filetime"]
                    raw = service.read(terminal_id, 0, max_bytes=1)
                    assert raw["total_bytes"] > 5_000_000 and raw["gap"] is not None
                    print(json.dumps({"bytes_seen": bytes_seen, "fast_gaps": gaps,
                                      "total_bytes": raw["total_bytes"], "same_pid": True}))
                cleanup_deadline = time.monotonic() + 20
                while time.monotonic() < cleanup_deadline:
                    response = await client.post(f"/api/terminals/{terminal_id}/close", json={})
                    if response.status_code == 200:
                        break
                    assert response.status_code == 409, response.text
                    await asyncio.sleep(.2)
                assert response.status_code == 200, response.text
                assert response.json()["result"]["status"] == "exited"
                terminal_id = None
        finally:
            host.should_exit = True
            await asyncio.wait_for(task, 30)
            if terminal_id is not None:
                # Service retains the original owner and verifies raw identity;
                # never kill a process based on PID/name scanning.
                service.shutdown(budget=20)

    asyncio.run(scenario())
