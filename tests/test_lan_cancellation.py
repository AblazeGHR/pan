"""Event-gated caller cancellation; no providers or real application lifespan."""
import asyncio
import json
import socket

import httpx
import pytest
import websockets

from packages.remote import lan_runtime as lan
from test_lan_access import controller, free_port


async def turn():
    # Drain callbacks already scheduled on this loop, without polling time.
    await asyncio.sleep(0)


@pytest.mark.parametrize("exit_error", [False, True])
def test_cancelled_stop_keeps_owner_until_exit_and_competing_start(monkeypatch, exit_error):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        host = lan.addresses()[0]
        ctl, _ = controller(monkeypatch, host, free_port(host))
        real_serve = lan.LanServer.serve
        servers = []
        async def gated_serve(self, sockets=None):
            servers.append(self)
            await real_serve(self, sockets=sockets)
            if len(servers) == 1:
                entered.set()
                await release.wait()
                if exit_error:
                    raise RuntimeError("late listener exit failure")
        monkeypatch.setattr(lan.LanServer, "serve", gated_serve)
        initial = await ctl.operation("start")
        url = initial["urls"][0]
        async with httpx.AsyncClient(base_url=url, trust_env=False) as client:
            assert (await client.get("/api/health")).status_code == 200
        ws = await websockets.connect(url.replace("http://", "ws://") + "/ws", origin=url, proxy=None)
        await ws.send(json.dumps({"type": "ping"}))
        assert json.loads(await ws.recv()) == {"type": "pong"}
        first_socket = ctl.socket
        stopped = asyncio.create_task(ctl.operation("stop"))
        await entered.wait()
        await ws.wait_closed()
        with pytest.raises(websockets.ConnectionClosed):
            await ws.send(json.dumps({"type": "ping"}))
        assert servers[0].should_exit and not ctl.origins
        stopped.cancel()
        await turn()
        restarted = asyncio.create_task(ctl.operation("start"))
        await turn()
        try:
            assert ctl.lock.locked() and not stopped.done() and not restarted.done()
            assert not ctl.status()["running"]
            for _ in range(3):
                stopped.cancel()
                await turn()
                assert ctl.lock.locked() and not stopped.done() and not restarted.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await stopped
            result = await restarted
            assert result["running"] and not result["error"]
            assert first_socket.fileno() == -1 and ctl.server is not servers[0]
            assert not ctl.lock.locked()
            url = result["urls"][0]
            async with httpx.AsyncClient(base_url=url, trust_env=False) as client:
                assert (await client.get("/api/health")).status_code == 200
            async with websockets.connect(url.replace("http://", "ws://") + "/ws", origin=url, proxy=None) as ws:
                await ws.send(json.dumps({"type": "ping"}))
                assert json.loads(await ws.recv()) == {"type": "pong"}
        finally:
            release.set()
            await ws.close()
            await asyncio.gather(stopped, restarted, return_exceptions=True)
            await ctl.operation("stop")
        with socket.socket() as probe:
            probe.bind((host, result["activeConfig"]["port"]))
    asyncio.run(run())


@pytest.mark.parametrize("action", ["start", "startup"])
def test_cancelled_late_start_retains_lock_through_late_exit(monkeypatch, action):
    async def run():
        entered, late_start, exiting, release_exit = (asyncio.Event() for _ in range(4))
        host = lan.addresses()[0]
        ctl, raw = controller(monkeypatch, host, free_port(host))
        raw["lan"]["startupEnabled"] = True
        async def serve(self, sockets=None):
            entered.set()
            await late_start.wait()
            self.started = True
            assert self.should_exit
            exiting.set()
            await release_exit.wait()
        monkeypatch.setattr(lan.LanServer, "serve", serve)
        owner = asyncio.create_task(ctl.operation(action))
        await entered.wait()
        sock = ctl.socket
        owner.cancel()
        await turn()
        competing = asyncio.create_task(ctl.operation("status"))
        try:
            for _ in range(3):
                owner.cancel()
                await turn()
                assert ctl.lock.locked() and not owner.done() and not competing.done()
            late_start.set()
            await exiting.wait()
            owner.cancel()
            await turn()
            assert ctl.lock.locked() and not owner.done()
            release_exit.set()
            with pytest.raises(asyncio.CancelledError):
                await owner
            result = await competing
            assert not result["running"] and not result["urls"]
            assert ctl.task is None and ctl.server is None and sock.fileno() == -1
            assert not ctl.lock.locked()
            with socket.socket() as probe:
                probe.bind((host, raw["lan"]["port"]))
        finally:
            late_start.set()
            release_exit.set()
            await asyncio.gather(owner, competing, return_exceptions=True)
    asyncio.run(run())


def test_shutdown_repeated_cancel_while_waiting_for_operation_owner(monkeypatch):
    async def run():
        entered, release, cleanup_entered, release_exit = (asyncio.Event() for _ in range(4))
        host = lan.addresses()[0]
        ctl, raw = controller(monkeypatch, host, free_port(host))
        async def serve(self, sockets=None):
            entered.set()
            await release.wait()
            self.started = True
            await release_exit.wait()
        monkeypatch.setattr(lan.LanServer, "serve", serve)
        real_stop = ctl.stop
        async def marked_stop():
            cleanup_entered.set()
            await real_stop()
        monkeypatch.setattr(ctl, "stop", marked_stop)
        owner = asyncio.create_task(ctl.operation("start"))
        await entered.wait()
        shutdown = asyncio.create_task(ctl.shutdown())
        await turn()
        sock = ctl.socket
        try:
            assert ctl.closing
            for _ in range(3):
                shutdown.cancel()
                await turn()
                assert not shutdown.done() and ctl.lock.locked()
            release.set()
            await asyncio.wait_for(cleanup_entered.wait(), 3)
            assert not ctl.origins and not shutdown.done() and ctl.lock.locked()
            shutdown.cancel()
            await turn()
            assert not shutdown.done()
            release_exit.set()
            result = await owner
            assert not result["running"] and not result["urls"]
            assert result["error"] == "Pan is shutting down"
            with pytest.raises(asyncio.CancelledError):
                await shutdown
            assert ctl.task is None and sock.fileno() == -1 and not ctl.lock.locked()
            assert not (await ctl.operation("start"))["running"]
            with socket.socket() as probe:
                probe.bind((host, raw["lan"]["port"]))
        finally:
            release.set()
            release_exit.set()
            await asyncio.gather(owner, shutdown, return_exceptions=True)
    asyncio.run(run())
