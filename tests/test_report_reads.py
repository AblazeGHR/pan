"""Own report configuration, body privacy, and original-ID Force batches."""
import asyncio
import copy
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from packages.core import session as sess, worker, self_queue
from packages.mcp import server as mcp
from packages.web import server


class Adapter:
    name = "report-fixture"
    execution_modes = ["stream"]
    supports_native_steer = True
    supports_steer_receipt = True
    def encode_control_message(self, control):
        return json.dumps(control).encode()


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    worker.workers.clear()
    worker._queue_locks.clear()
    worker._spawn_locks.clear()
    worker.set_broadcaster(None)
    monkeypatch.setattr(worker, "_spawn_process", AsyncMock(side_effect=AssertionError("real provider spawn")))
    monkeypatch.setattr(worker, "_schedule_queue_retry", lambda *_: None)
    monkeypatch.setattr(worker, "_wake_after_queue_unit", lambda *_: None)
    yield
    worker.workers.clear()


def report(identity, **extra):
    return {"id": identity, "queueItemId": identity, "type": "report", "kind": "report",
            "source": "report", "sourceSessionId": "ses-child-"+identity,
            "sessionId": "ses-child-"+identity, "result": "SECRET-"+identity,
            "status": "done", "taskId": "T-"+identity, "workerId": "w-"+identity,
            "deliveryState": "queued", "revision": 1, **extra}


def setup():
    s = sess.create("report-owner", adapter="codex")
    s.report_subscriptions = {"ses-child-b", "ses-child-a"}
    a, b = report("a"), report("b")
    task = {"id": "t", "queueItemId": "t", "type": "task", "source": "user", "text": "TASK"}
    channel = {"id": "c", "queueItemId": "c", "type": "qq", "text": "CHANNEL"}
    notice = {"id": "n", "queueItemId": "n", "type": "notice", "noticeKind": "background_job_terminal", "result": "JOB"}
    hidden = report("hidden", queueLockManual=True)
    s.queue_pending = [task, a, channel, hidden, b, notice]
    sess.save_full(s)
    return s, a, b, [task, channel, hidden, notice]


def runtime(s, *, capable=True, status="running", disposition="accepted"):
    adapter = Adapter()
    adapter.supports_native_steer = capable
    stdin = Mock(drain=AsyncMock())
    w = worker.Worker(worker_id="w-own", session_id=s.id, adapter=adapter,
                      status=status, process=SimpleNamespace(returncode=None, stdin=stdin),
                      pending_signal=asyncio.Queue())
    worker.workers[w.worker_id] = w
    def write(data):
        ctl = json.loads(data)
        # Real native receipt routing, not a mocked queue transaction.
        worker._route_steer_receipt(w, {"type": "pan.steer_receipt", "requestId": ctl["requestId"],
            "ok": disposition == "accepted", "outcome": disposition,
            "error": None if disposition == "accepted" else "fixture rejection/uncertainty"})
    stdin.write.side_effect = write
    return w, stdin


def assert_private(value):
    text = json.dumps(value)
    for body in ("SECRET-", "ATTACHMENT-BODY", "SNIPPET-BODY"):
        assert body not in text
    assert "hidden" not in text


def test_status_and_repeated_reads_preserve_durable_queue_and_history():
    async def run():
        s, a, _, _ = setup()
        a.update(parts=[{"text": "ATTACHMENT-BODY"}], snippet="SNIPPET-BODY", envelope={"result": a["result"]})
        sess.save_full(s)
        disk = sess._path(s.id).read_bytes()
        before = copy.deepcopy((s.queue_pending, s.queue_delivery_ledger, s.history, s.queue_revision))
        status = await self_queue.report_status(s.id)
        assert status == {"ok": True, "sessionId": s.id, "reportSubscriptions": ["ses-child-a", "ses-child-b"], "agentReportsPaused": False}
        one = await self_queue.read(s.id)
        assert one == await self_queue.read(s.id)
        assert_private(one)
        assert [row["id"] for row in one["items"]] == ["t", "a", "c", "b", "n"]
        assert one["items"][0]["text"] == "TASK"
        assert one["items"][2]["text"] == "CHANNEL"
        assert "JOB" in one["items"][4]["content"]
        assert (s.queue_pending, s.queue_delivery_ledger, s.history, s.queue_revision) == before
        assert sess._path(s.id).read_bytes() == disk
    asyncio.run(run())


@pytest.mark.parametrize("gate", ["manual", "illegal", "stop-report", "pause", "readonly", "edit",
    "native-next", "manual-owner", "uncertain", "steer", "reserved", "sent", "held", "backoff"])
def test_consume_never_reveals_blocked_rows(monkeypatch, gate):
    async def run():
        s, a, _, _ = setup()
        s.queue_pending = [a]
        if gate == "manual": a["queueLockManual"] = True
        elif gate == "illegal": a.update(queueLockSource="illegal", queueLockIllegalBatch="batch")
        elif gate == "stop-report": a["queueLockAutoReport"] = True
        elif gate == "pause": s.queue_paused = True
        elif gate == "readonly": s.readonly_session = True
        elif gate == "edit": worker.acquire_queue_edit_lock(s, "a", "editor")
        elif gate == "native-next": s.adapter_config["native_control_next"] = True
        elif gate == "manual-owner":
            w, _ = runtime(s); w.native_control_mode = "manual"
        elif gate == "uncertain": a["selfConsumeUncertain"] = True
        elif gate == "steer": a["queueSteerPending"] = True
        elif gate == "reserved": a["deliveryState"] = "reserved"
        elif gate == "sent": a["deliveryState"] = "sent_to_cli"
        elif gate == "held": a["forceSendHeld"] = True
        elif gate == "backoff": a["nextAttemptAt"] = time.time()+60
        before = copy.deepcopy(s.queue_pending)
        dispatch = AsyncMock(side_effect=AssertionError("blocked dispatch"))
        monkeypatch.setattr(worker, "create_worker", dispatch)
        assert await self_queue.report_consume(s.id) == self_queue.UNAVAILABLE
        assert s.queue_pending == before
        assert (await self_queue.read(s.id))["items"] == []
    asyncio.run(run())


@pytest.mark.parametrize("disposition", ["accepted", "rejected", "unknown"])
def test_steer_is_one_real_batch_preserves_history_and_receipts(monkeypatch, disposition):
    async def run():
        s, a, b, others = setup()
        w, stdin = runtime(s, disposition=disposition)
        restart = AsyncMock(side_effect=AssertionError("Steer fallback"))
        monkeypatch.setattr(worker, "restart_or_start_worker", restart)
        before = copy.deepcopy([a, b])
        expected = worker._format_report_batch([a, b])
        one, two = await asyncio.gather(self_queue.report_consume(s.id), self_queue.report_consume(s.id))
        assert one == two
        assert_private(one)
        assert one["status"] == disposition
        assert one["queueItemIds"] == ["a", "b"]
        assert stdin.write.call_count == 1
        assert json.loads(stdin.write.call_args.args[0])["text"] == expected
        assert [row["id"] for row in s.queue_pending] == ([row["id"] for row in others] if disposition == "accepted" else ["a", "b"]+[row["id"] for row in others])
        if disposition == "accepted":
            rows = [row for row in s.history if row.get("queueItemIds") == ["a", "b"]]
            assert len(rows) == 1 and rows[0]["content"] == expected
            for original, envelope in zip(before, rows[0]["queueEnvelopes"]):
                assert envelope["id"] == original["id"]
                assert envelope["sourceSessionId"] == original["sourceSessionId"]
                assert envelope["taskId"] == original["taskId"]
            assert all(s.queue_delivery_ledger[key]["deliveryState"] == "sent_to_cli" for key in ("a", "b"))
        else:
            assert not s.history
            if disposition == "unknown":
                assert a["queueSteerPending"] and b["queueSteerPending"]
                assert worker._select_queue_unit(s) is None
        assert (await self_queue.report_consume(s.id, "a"))["status"] == disposition
        # Cold Session reload and normal migration retain the SAME identities.
        sess._cache.clear()
        cold = self_queue._own_session(s.id)
        worker._migrate_queue_delivery_state(cold)
        assert (await self_queue.report_consume(s.id, "b"))["queueItemIds"] == ["a", "b"]
        assert stdin.write.call_count == 1
        if disposition == "unknown":
            assert_private(await self_queue.report_consume(s.id))
            assert stdin.write.call_count == 1
    asyncio.run(run())


@pytest.mark.parametrize("capable,status", [(True,"idle"), (False,"idle"), (False,"queued"), (False,"running")])
def test_idle_ordinary_handoff_and_nonsteer_force_route(monkeypatch, capable, status):
    async def run():
        s, a, b, others = setup()
        w, stdin = runtime(s, capable=capable, status=status)
        w._queue_unit_active = True
        calls = []
        async def deliver(*args, **kwargs):
            calls.append((args, kwargs))
            assert worker._select_queue_unit(s) == [a, b]
            assert [row["id"] for row in s.queue_pending] == ["a", "b", "t", "c", "hidden", "n"]
            stored = json.loads(sess._path(s.id).read_text(encoding="utf-8"))
            assert [row["reportConsumeIds"] for row in stored["queue_pending"][:2]] == [["a","b"]]*2
            # Exercise real reservation/write gate/commit, no paid CLI/model.
            async def consumer(worker_, text, source, session_, *, on_handoff):
                assert text == worker._format_report_batch([a,b])
                assert not s.history
                async def write(): return None
                await worker._queue_handoff(w, s, write, on_handoff)
            monkeypatch.setattr(worker, "_consumer_stream", consumer)
            monkeypatch.setattr(worker, "_maybe_inject_memory", AsyncMock(side_effect=lambda s,t:t))
            await worker._deliver_queue_unit(w, s, [a,b])
            return w
        monkeypatch.setattr(worker, "_wake_worker", deliver)
        restart = AsyncMock(side_effect=deliver)
        monkeypatch.setattr(worker, "restart_or_start_worker", restart)
        result = await self_queue.report_consume(s.id)
        assert result["status"] == "accepted"
        assert_private(result)
        assert len(calls) == 1 and stdin.write.call_count == 0
        assert restart.await_count == (1 if status == "running" else 0)
        assert s.queue_pending == others
        assert s.history[-1]["queueItemIds"] == ["a","b"]
    asyncio.run(run())


def test_failed_ticket_rolls_back_without_delivery(monkeypatch):
    async def run():
        s, a, _, _ = setup()
        before = copy.deepcopy(s.queue_pending)
        original = sess._save_body
        def fail(candidate, **kwargs):
            if a.get("reportConsumeIds"): raise OSError("disk failure")
            return original(candidate, **kwargs)
        monkeypatch.setattr(sess, "_save_body", fail)
        spawn = AsyncMock(side_effect=AssertionError("failed persistence delivered"))
        monkeypatch.setattr(worker, "create_worker", spawn)
        assert (await self_queue.report_consume(s.id))["error"] == "persist_failed"
        await sess.save_async(s)
        assert s.queue_pending == before
        assert not s.history and not s.queue_delivery_ledger
    asyncio.run(run())


def test_batch_cannot_split_on_locks_reorder_or_force_race(monkeypatch):
    async def run():
        s, a, b, _ = setup()
        monkeypatch.setattr(worker,"create_worker",AsyncMock(return_value=object()))
        assert (await self_queue.report_consume(s.id))["status"] == "queued"
        assert worker._select_queue_unit(s) == [a,b]
        b["queueLockManual"] = True
        assert worker._select_queue_unit(s) is None
        assert await self_queue.report_consume(s.id,"a") == self_queue.UNAVAILABLE
        b.pop("queueLockManual")
        s.queue_pending[0], s.queue_pending[1] = b,a
        assert worker._select_queue_unit(s) is None
        s.queue_pending[0], s.queue_pending[1] = a,b
        runtime(s, capable=False)
        assert (await worker._auto_force_restart_item(s.id,"b"))["status"] == "skipped"
        assert worker._select_queue_unit(s) == [a,b]
    asyncio.run(run())


def test_unknown_normal_write_survives_restart_without_regrouping(monkeypatch):
    async def run():
        s, a, b, _ = setup()
        monkeypatch.setattr(worker,"create_worker",AsyncMock(return_value=object()))
        await self_queue.report_consume(s.id)
        for row in (a,b): row.update(selfConsumeAttempted=True,deliveryState="reserved")
        sess.save_full(s)
        worker.workers.clear(); sess._cache.clear()
        cold = self_queue._own_session(s.id)
        worker._migrate_queue_delivery_state(cold)
        assert worker._select_queue_unit(cold) is None
        assert (await self_queue.report_consume(s.id,"a"))["status"] == "unknown"
        cold.queue_pending.append(report("new"))
        assert (await self_queue.report_consume(s.id))["queueItemIds"] == ["a","b"]
        assert not cold.queue_pending[-1].get("reportConsumeIds")
    asyncio.run(run())


def test_legacy_identity_and_future_report_pause_configuration(monkeypatch):
    async def run():
        s,a,b,_ = setup()
        a.pop("queueItemId")
        legacy = {"type":"report","source":"report","result":"SECRET-legacy"}
        s.queue_pending = [legacy,a]
        s.agent_reports_paused = True
        monkeypatch.setattr(worker,"create_worker",AsyncMock(return_value=object()))
        result = await self_queue.report_consume(s.id)
        assert result["queueItemIds"] == ["a"]
        assert not legacy.get("id") and not legacy.get("queueItemId")
        assert_private(result)
    asyncio.run(run())


def test_http_own_identity_gate_rejects_body_session_override(monkeypatch):
    async def run():
        s,_,_,_ = setup()
        monkeypatch.setattr(worker,"create_worker",AsyncMock(return_value=object()))
        transport = httpx.ASGITransport(app=server.app,client=("127.0.0.1",12345))
        async with httpx.AsyncClient(transport=transport,base_url="http://local") as client:
            headers={"X-Pan-Queue-Caller":s.id}
            for op in ("report-status","report-consume"):
                assert_private((await client.post("/api/self-queue/"+op,headers=headers,json={})).json())
                for denied in ({},{**headers,"Origin":"https://browser"},{**headers,"Sec-Fetch-Site":"same-origin"},{"X-Pan-Queue-Caller":"ses-missing"}):
                    assert (await client.post("/api/self-queue/"+op,headers=denied,json={})).json()["error"] == "caller_unavailable"
                assert (await client.post("/api/self-queue/"+op,headers=headers,json={"sessionId":"other"})).json()["error"] == "invalid_request"
            assert (await client.post("/api/self-queue/report-next",headers=headers,json={})).json()["error"] == "invalid_request"
            assert (await client.post("/api/self-queue/report-consume",headers=headers,json={"itemId":"a"})).json()["queueItemIds"] == ["a","b"]
    asyncio.run(run())


@pytest.mark.parametrize("tool",[mcp.report_status,mcp.report_consume])
def test_mcp_remote_or_unbound_callers_never_borrow_identity(monkeypatch,tool):
    monkeypatch.setattr(mcp,"_terminal_transport","sse")
    monkeypatch.setenv("PAN_AGENT_SESSION_ID","ses-own")
    monkeypatch.setattr(mcp,"_pan_api_url","http://127.0.0.1:51999")
    assert tool() == {"ok":False,"error":"caller_unavailable"}
    monkeypatch.setattr(mcp,"_terminal_transport","stdio")
    monkeypatch.delenv("PAN_AGENT_SESSION_ID")
    assert tool() == {"ok":False,"error":"caller_unavailable"}


def test_real_http_mcp_report_handoff_metadata_only(monkeypatch,record_property):
    import os
    import socket
    import threading
    import uvicorn
    from fastapi import FastAPI
    from mcp.shared.memory import create_connected_server_and_client_session
    s,a,b,_ = setup()
    _,stdin = runtime(s)
    monkeypatch.setenv("PAN_AGENT_SESSION_ID",s.id)
    monkeypatch.setattr(mcp,"_terminal_transport","stdio")
    app=FastAPI()
    app.add_api_route("/api/self-queue/{operation}",server.api_self_queue,methods=["POST"])
    sock=socket.socket(); sock.bind(("127.0.0.1",0))
    port=sock.getsockname()[1]
    assert port not in (8767,8768)
    record_property("owned_http_port",port);record_property("owned_http_pid",os.getpid())
    monkeypatch.setattr(mcp,"_pan_api_url",f"http://127.0.0.1:{port}")
    http=uvicorn.Server(uvicorn.Config(app,lifespan="off",log_level="error",ws="none"))
    thread=threading.Thread(target=http.run,kwargs={"sockets":[sock]},daemon=True);thread.start()
    try:
        deadline=time.monotonic()+5
        while not http.started and thread.is_alive() and time.monotonic()<deadline:thread.join(.01)
        assert http.started
        async def exercise():
            async with create_connected_server_and_client_session(mcp.mcp) as protocol:
                tools={t.name:t for t in (await protocol.list_tools()).tools}
                assert "report_consume" in tools and "report_next" not in tools and "report_ack" not in tools
                assert list(tools["report_consume"].inputSchema["properties"]) == ["item_id"]
                async def call(name,args=None):
                    result=await protocol.call_tool(name,args or {})
                    assert not result.isError
                    return json.loads(next(c.text for c in result.content if c.type=="text"))
                assert (await call("report_status"))["sessionId"]==s.id
                assert_private(await call("self_queue_read"))
                result=await call("report_consume")
                assert result["status"]=="accepted" and result["queueItemIds"]==["a","b"]
                assert_private(result)
                assert await call("report_consume",{"item_id":"a"}) == result
                assert stdin.write.call_count==1
        asyncio.run(exercise())
    finally:
        http.should_exit=True;thread.join(5);sock.close()
        assert not thread.is_alive()
    with socket.socket() as probe:assert probe.connect_ex(("127.0.0.1",port))!=0


@pytest.mark.parametrize("failure", ["reservation-save", "pause-after-save", "readonly-after-save", "lease-after-save", "owner-after-save"])
def test_native_reservation_failure_or_final_gate_never_writes(monkeypatch, failure):
    async def run():
        s,a,b,_ = setup()
        _,stdin = runtime(s)
        original_save = sess._save_body
        if failure == "reservation-save":
            def save(candidate, **kwargs):
                if a.get("queueSteerPending"): raise OSError("reservation disk failure")
                return original_save(candidate, **kwargs)
            monkeypatch.setattr(sess,"_save_body",save)
        else:
            original_persist = sess._persist_async_outcome
            async def persist(*args, **kwargs):
                await original_persist(*args, **kwargs)
                if a.get("queueSteerPending"):
                    if failure == "pause-after-save": s.queue_paused = True
                    elif failure == "readonly-after-save": s.readonly_session = True
                    elif failure == "lease-after-save": worker.acquire_queue_edit_lock(s,"b","editor")
                    else: worker.find_alive_worker_by_session(s.id).native_control_mode = "manual"
            monkeypatch.setattr(sess,"_persist_async_outcome",persist)
        result = await self_queue.report_consume(s.id)
        assert_private(result)
        assert not stdin.write.call_count and not s.history
        assert all(row in s.queue_pending for row in (a,b))
        assert not a.get("queueSteerPending") and not b.get("queueSteerPending")
        assert [row["id"] for row in s.queue_pending][:2] == ["a","b"]
    asyncio.run(run())


def test_cold_consume_preserves_prior_history_and_shared_session(monkeypatch):
    async def run():
        s,a,b,_ = setup()
        sess.append_history(s,{"role":"assistant","content":"PRIOR"})
        sess.save_full(s)
        sid = s.id
        sess._cache.clear()
        cold = self_queue._own_session(sid)
        assert cold is self_queue._own_session(sid)
        assert not cold._history_loaded
        _,stdin = runtime(cold)
        result = await self_queue.report_consume(sid)
        assert result["status"] == "accepted" and stdin.write.call_count == 1
        sess._cache.clear()
        full = sess.get(sid)
        assert any(row.get("content") == "PRIOR" for row in full.history)
        assert len([row for row in full.history if row.get("queueItemIds") == ["a","b"]]) == 1
    asyncio.run(run())


def test_delayed_restart_and_missing_identity_are_not_bypassed(monkeypatch):
    async def run():
        s,a,b,_ = setup()
        w,stdin = runtime(s,capable=False)
        w.pending_restart = True
        wake = AsyncMock()
        restart = AsyncMock(side_effect=AssertionError("delayed retirement must win"))
        monkeypatch.setattr(worker,"_wake_worker",wake)
        monkeypatch.setattr(worker,"restart_or_start_worker",restart)
        assert (await self_queue.report_consume(s.id))["status"] == "queued"
        wake.assert_awaited_once()
        assert not stdin.write.call_count
        legacy = {"type":"report","source":"report","result":"SECRET-legacy"}
        s.queue_pending = [legacy]
        before = copy.deepcopy(legacy)
        assert (await self_queue.read(s.id))["items"] == []
        assert legacy == before
    asyncio.run(run())


def test_receipt_compaction_retains_membership_without_body(monkeypatch):
    async def run():
        s,_,_,_ = setup()
        runtime(s)
        await self_queue.report_consume(s.id)
        for key in ("a","b"):
            s.queue_delivery_ledger[key] = worker._compact_receipt_record(s.queue_delivery_ledger[key],key)
        sess.save_full(s); sess._cache.clear()
        result = await self_queue.report_consume(s.id,"b")
        assert result["status"] == "accepted" and result["queueItemIds"] == ["a","b"]
        assert_private(result)
        s = self_queue._own_session(s.id)
        s.queue_delivery_ledger.pop("a")
        assert (await self_queue.report_consume(s.id,"b"))["status"] == "unknown"
    asyncio.run(run())


def test_cancelled_request_keeps_batch_owner_and_concurrent_queue_selection(monkeypatch):
    async def run():
        s,a,b,_ = setup()
        entered = asyncio.Event();release = asyncio.Event()
        original = sess._persist_async_outcome
        async def persist(*args, **kwargs):
            await original(*args, **kwargs)
            if not entered.is_set():
                entered.set();await release.wait()
        monkeypatch.setattr(sess,"_persist_async_outcome",persist)
        w,stdin = runtime(s)
        call = asyncio.create_task(self_queue.report_consume(s.id))
        await entered.wait()
        owner = self_queue._report_attempts[s.id]
        call.cancel()
        with pytest.raises(asyncio.CancelledError):await call
        # A normal consumer selected a before regrouping: real reservation
        # reselects under lock and cannot write its stale single-item unit.
        assert worker._select_queue_unit(s) == [a,b]
        stale = asyncio.create_task(worker._reserve_queue_unit(w,s,[a],"stale",expected_revisions=[1]))
        release.set()
        assert not await stale
        result = await owner
        assert result["status"] == "accepted" and stdin.write.call_count == 1
        assert (await self_queue.report_consume(s.id,"a"))["status"] == "accepted"
    asyncio.run(run())


@pytest.mark.parametrize("failure", ["attempt-save", "write"])
def test_ordinary_batch_failure_has_no_history_and_never_replays_uncertain_write(monkeypatch, failure):
    async def run():
        s,a,b,_ = setup()
        w,_ = runtime(s,capable=False,status="idle")
        calls = []
        real_save = sess._save_body
        if failure == "attempt-save":
            def save(candidate, **kwargs):
                if a.get("selfConsumeAttempted"): raise OSError("attempt persistence failure")
                return real_save(candidate, **kwargs)
            monkeypatch.setattr(sess,"_save_body",save)
        async def consumer(worker_,text,source,session_,*,on_handoff):
            async def write():
                calls.append(text)
                raise BrokenPipeError("ambiguous write")
            await worker._queue_handoff(w,s,write,on_handoff)
        async def wake(*args, **kwargs):
            monkeypatch.setattr(worker,"_consumer_stream",consumer)
            monkeypatch.setattr(worker,"_maybe_inject_memory",AsyncMock(side_effect=lambda s,t:t))
            await worker._deliver_queue_unit(w,s,[a,b])
        monkeypatch.setattr(worker,"_wake_worker",wake)
        result = await self_queue.report_consume(s.id)
        assert_private(result)
        assert not s.history and all(row in s.queue_pending for row in (a,b))
        if failure == "write":
            assert result["status"] == "unknown" and len(calls) == 1
            assert all(row.get("selfConsumeUncertain") for row in (a,b))
            assert worker._select_queue_unit(s) is None
            assert (await self_queue.report_consume(s.id,"a"))["status"] == "unknown"
            assert len(calls) == 1
        else:
            assert result["status"] == "queued" and not calls
            assert not a.get("selfConsumeAttempted") and not b.get("selfConsumeAttempted")
    asyncio.run(run())


def test_post_acceptance_persistence_failure_is_unconfirmed_not_resend(monkeypatch):
    async def run():
        s,_,_,_ = setup()
        _,stdin = runtime(s)
        original = sess._save_body
        def save(candidate, **kwargs):
            if any(worker._delivery_state(row) == "sent_to_cli" for row in s.queue_delivery_ledger.values()):
                raise OSError("receipt disk failure")
            return original(candidate, **kwargs)
        monkeypatch.setattr(sess,"_save_body",save)
        # Avoid persistence retry sleeps; still exercise the real accepted
        # transaction and _confirmed's durable-outcome failure.
        async def pending_persistence(w,s):
            return False
        monkeypatch.setattr(worker,"_persist_queue_steer_state",pending_persistence)
        result = await self_queue.report_consume(s.id)
        assert result["status"] == "unconfirmed" and stdin.write.call_count == 1
        assert_private(result)
        assert (await self_queue.report_consume(s.id,"a"))["status"] == "unconfirmed"
        assert stdin.write.call_count == 1
        monkeypatch.setattr(sess,"_save_body",original)
        # The persistence owner, not a query, repairs durable proof.
        await sess.save_async(s)
        assert (await self_queue.report_consume(s.id,"a"))["status"] == "accepted"
        assert stdin.write.call_count == 1
    asyncio.run(run())
