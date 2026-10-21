"""Report batch/Action admission coexistence, with real local HTTP transactions."""
import asyncio
import json
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from packages.core import session as sess, worker, self_queue
from packages.plugins.lifecycle import coordinator, pin
from packages.plugins.catalog import Catalog
from packages.web import server
from tests.test_action_groups import setup as action_setup, api_action, group, binding
from tests.test_report_reads import isolated, setup as report_setup, runtime, report, assert_private


def prepare(action_setup, monkeypatch, *, status="running"):
    store, manifest = action_setup
    store.put("actions", api_action())
    release = group(store, ids=["list_sessions"])
    s,a,b,others = report_setup()
    server._set_plugin_config(s.adapter_config,[binding(release["plugin_id"])])
    sess.save(s)
    pin(s,Catalog(manifest))
    w,stdin = runtime(s,status=status)
    monkeypatch.setattr(worker,"find_alive_worker_by_session",lambda sid:w if sid == s.id else None)
    return store,s,a,b,others,w,stdin


def test_unbuild_first_fences_batch_steer_before_actual_control_write(action_setup,monkeypatch):
    async def run():
        store,s,a,b,_,w,stdin = prepare(action_setup,monkeypatch,status="idle")
        entered,proceed = asyncio.Event(),asyncio.Event()
        original = sess.commit_adapter_config_async
        async def detach(*args,**kwargs):
            entered.set();await proceed.wait()
            return await original(*args,**kwargs)
        monkeypatch.setattr(sess,"commit_adapter_config_async",detach)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url="http://fixture") as client:
            unbuild=asyncio.create_task(client.post("/api/actions/groups/tools/unbuild",json={}))
            await asyncio.wait_for(entered.wait(),3)
            w.status="running"
            consuming=asyncio.create_task(self_queue.report_consume(s.id))
            # Wait until the real Steer attempt exists, without waiting on time.
            for _ in range(100):
                if worker._queue_steer_tasks:break
                await asyncio.sleep(.005)
            assert worker._queue_steer_tasks and not consuming.done()
            assert not stdin.write.call_count
            proceed.set()
            assert (await unbuild).status_code==200
            result=await asyncio.wait_for(consuming,3)
            assert result["status"]=="queued"
            assert_private(result)
            assert not stdin.write.call_count and not s.history
            assert [row["id"] for row in s.queue_pending][:2]==["a","b"]
            server.worker.request_delayed_restart.assert_called()
            assert store.read()["groups"]["tools"].get("build") is None
        assert not coordinator().active
    asyncio.run(run())


@pytest.mark.parametrize("cancel_request",[False,True])
def test_batch_control_drain_keeps_action_reservation_until_actual_boundary(action_setup,monkeypatch,cancel_request):
    async def run():
        store,s,a,b,others,w,stdin=prepare(action_setup,monkeypatch)
        # A transport with no explicit RPC receipt proves admission on drain.
        w.adapter.supports_steer_receipt=False
        stdin.write.side_effect=None
        entered,proceed=asyncio.Event(),asyncio.Event()
        async def drain():
            entered.set();await proceed.wait()
        stdin.drain=AsyncMock(side_effect=drain)
        consuming=asyncio.create_task(self_queue.report_consume(s.id))
        await asyncio.wait_for(entered.wait(),3)
        assert stdin.write.call_count==1
        wire=json.loads(stdin.write.call_args.args[0])
        assert wire["text"]==worker._format_report_batch([a,b])
        assert not s.history
        assert coordinator().active
        owner=self_queue._report_attempts[s.id]
        if cancel_request:
            consuming.cancel()
            with pytest.raises(asyncio.CancelledError):await consuming
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url="http://fixture") as client:
            unbuild=asyncio.create_task(client.post("/api/actions/groups/tools/unbuild",json={}))
            await asyncio.sleep(.02)
            assert not unbuild.done() and coordinator().active
            # New reports stay outside the immutable in-flight batch.
            new=report("later");s.queue_pending.append(new)
            proceed.set()
            result=await asyncio.wait_for(owner,3)
            assert result["status"]=="accepted"
            assert (await unbuild).status_code==409
        assert_private(result)
        assert s.queue_pending==others+[new]
        row=s.history[-1]
        assert row["queueItemIds"]==["a","b"]
        assert [entry["sourceSessionId"] for entry in row["queueEnvelopes"]]==[a["sourceSessionId"],b["sourceSessionId"]]
        before=sess._path(s.id).read_bytes()
        monkeypatch.setattr(sess,"_save_body",Mock(side_effect=AssertionError("query persisted")))
        monkeypatch.setattr(worker,"_save_receipt",AsyncMock(side_effect=AssertionError("query persisted")))
        assert (await self_queue.report_consume(s.id,"b"))["status"]=="accepted"
        assert sess._path(s.id).read_bytes()==before and stdin.write.call_count==1
        assert not coordinator().active
    asyncio.run(run())


def test_ordinary_report_batch_uses_action_handoff_reservation(action_setup,monkeypatch):
    async def run():
        _,s,a,b,_,w,_=prepare(action_setup,monkeypatch,status="idle")
        entered,proceed=asyncio.Event(),asyncio.Event()
        async def consumer(worker_,text,source,session_,*,on_handoff):
            async def operation():
                entered.set();await proceed.wait();w.status="running"
            await worker._queue_handoff(w,s,operation,on_handoff)
        monkeypatch.setattr(worker,"_consumer_stream",consumer)
        monkeypatch.setattr(worker,"_maybe_inject_memory",AsyncMock(side_effect=lambda s,t:t))
        async def wake(*args,**kwargs):await worker._deliver_queue_unit(w,s,[a,b])
        monkeypatch.setattr(worker,"_wake_worker",wake)
        consuming=asyncio.create_task(self_queue.report_consume(s.id))
        await asyncio.wait_for(entered.wait(),3)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url="http://fixture") as client:
            unbuild=asyncio.create_task(client.post("/api/actions/groups/tools/unbuild",json={}))
            await asyncio.sleep(.02)
            assert not unbuild.done() and not s.history
            proceed.set()
            assert (await consuming)["status"]=="accepted"
            assert (await unbuild).status_code==409
        assert s.history[-1]["queueItemIds"]==["a","b"]
        assert not coordinator().active
    asyncio.run(run())


def test_action_bound_unknown_batch_cold_receipt_and_force_do_not_replay(action_setup,monkeypatch):
    async def run():
        _,s,a,b,_,w,stdin=prepare(action_setup,monkeypatch)
        def unknown(data):
            control=json.loads(data)
            worker._route_steer_receipt(w,{"type":"pan.steer_receipt","requestId":control["requestId"],
                "ok":False,"outcome":"unknown","error":"fixture unknown"})
        stdin.write.side_effect=unknown
        result=await self_queue.report_consume(s.id)
        assert result["status"]=="unknown" and stdin.write.call_count==1
        assert not coordinator().active
        sess._cache.clear()
        cold=self_queue._own_session(s.id)
        worker._migrate_queue_delivery_state(cold)
        assert worker._select_queue_unit(cold) is None
        disk=sess._path(s.id).read_bytes()
        monkeypatch.setattr(sess,"_save_body",Mock(side_effect=AssertionError("query persisted")))
        monkeypatch.setattr(worker,"_save_receipt",AsyncMock(side_effect=AssertionError("query persisted")))
        assert (await self_queue.report_consume(s.id,"a"))["status"]=="unknown"
        assert sess._path(s.id).read_bytes()==disk
        cold.queue_pending.append(report("later"))
        assert (await self_queue.report_consume(s.id))["queueItemIds"]==["a","b"]
        assert (await worker._auto_force_item(s.id,"b","report"))["status"]=="skipped"
        assert await worker.steer_queue_item(s.id,"a",1) is not None
        assert stdin.write.call_count==1
        assert_private(await self_queue.read(s.id))
        assert not cold.history and not coordinator().active
    asyncio.run(run())
