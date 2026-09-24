"""BE-3: history/list cold reads must not block the FastAPI event loop.

The dashboard cold load is ``GET /api/sessions`` plus
``GET /api/sessions/{id}/history``.  Both parse the Session main file and
stream the companion ``.history.jsonl`` with one ``json.loads`` per row, so
serving them inline on the event loop stalled WebSocket traffic and worker
streaming for the whole scan.  These tests pin the offload, the paging
semantics, the copy-only page boundary and the concurrent-read guarantees.

The process-level counterpart (real uvicorn, real HTTP + dashboard WebSocket,
real cold-read burst) lives in ``tests/test_real_history_coldload_e2e.py``.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest

import packages.web.server as server
from packages.core import session as _sess


ROW_SUFFIX = "x" * 80


def _rows(count: int, *, start: int = 0) -> list[dict]:
    return [
        {
            "role": "assistant" if index % 2 else "user",
            "content": f"msg-{index:06d} {ROW_SUFFIX}",
        }
        for index in range(start, start + count)
    ]


def _seed(session_id: str, rows: list[dict]) -> None:
    """Persist a Session through the real store writers, then go cold."""
    value = _sess.Session(
        id=session_id, name=session_id, adapter="cbc", model="test-model",
    )
    _sess.replace_history(value, rows)
    _sess._cache[value.id] = value
    _sess.save_full(value)
    _sess._cache.clear()
    _sess._all_loaded = False


def _max_loop_gap(coro_factory):
    """Run one async request while sampling the event loop's scheduling gap.

    The sampler reschedules itself with ``sleep(0)`` rather than a timer: the
    Windows event loop timer granularity is ~15ms, which is too coarse to
    distinguish "the loop is free" from "the loop is blocked".  A blocked
    loop produces exactly one gap the size of the block.
    """

    async def scenario():
        loop = asyncio.get_running_loop()
        gaps: list[float] = []
        ticks = 0
        done = asyncio.Event()

        async def ticker():
            nonlocal ticks
            last = loop.time()
            while not done.is_set():
                await asyncio.sleep(0)
                now = loop.time()
                gaps.append(now - last)
                last = now
                ticks += 1

        ticker_task = asyncio.create_task(ticker())
        await asyncio.sleep(0)
        try:
            result = await coro_factory()
        finally:
            done.set()
            await ticker_task
        return result, gaps, ticks

    return asyncio.run(scenario())


# ── event loop liveness ──

def test_session_list_cold_read_does_not_block_event_loop():
    """A 60k-row store must not freeze the loop while /api/sessions is built."""
    _seed("ses-cold-list", _rows(60_000))

    response, gaps, ticks = _max_loop_gap(
        lambda: server.api_list_sessions(summary=0))

    listed = next(item for item in response["sessions"] if item["id"] == "ses-cold-list")
    assert listed["historyTotal"] == 60_000
    assert listed["historyTruncated"] is True
    assert [row["content"] for row in listed["history"]] == [
        row["content"] for row in _rows(50, start=59_950)
    ]
    # The loop must keep scheduling while the JSONL is parsed off-thread.
    assert ticks > 50, f"loop starved during cold read (ticks={ticks})"
    assert max(gaps) < 0.05, f"event loop blocked for {max(gaps) * 1000:.1f}ms"


def test_cold_tail_page_uses_complete_projection_without_parsing_full_jsonl(
    monkeypatch,
):
    sid = "ses-cold-tail-known-total"
    rows = _rows(1_000)
    _seed(sid, rows)
    # A complete durable projection is the count authority for the optimized
    # default tail-page path.  Unknown or out-of-sync totals retain the full
    # compatibility scan.
    original_loads = json.loads
    decoded_rows = 0

    def count_row_decodes(value, *args, **kwargs):
        nonlocal decoded_rows
        if ((isinstance(value, bytes) and value.startswith(b"{"))
                or (isinstance(value, str) and value.lstrip().startswith("{"))):
            decoded_rows += 1
        return original_loads(value, *args, **kwargs)

    monkeypatch.setattr(_sess.json, "loads", count_row_decodes)
    page = _sess.history_page(sid, limit=25)
    assert page["total"] == 1_000
    assert page["start"] == 975
    assert [row["content"] for row in page["history"]] == [
        row["content"] for row in rows[-25:]
    ]
    assert decoded_rows == 26  # one metadata file plus the requested 25 rows
    assert _sess.get(sid, load_history=False)._history_loaded is False


def test_summary_projection_120k_history_stays_jsonl_free_and_keeps_heartbeat(
    monkeypatch,
):
    """A long cold summary uses metadata only and leaves the loop schedulable."""
    count = 120_000
    sid = "ses-summary-120k"
    _sess.SESSION_DIR.mkdir(parents=True, exist_ok=True)
    projection = _sess._summary_projection_from_history(
        [{"role": "assistant", "content": "latest"}],
    )
    projection["history_total"] = count
    main_path = _sess.SESSION_DIR / f"{sid}.json"
    main_path.write_text(json.dumps({
        "id": sid, "name": sid, "adapter": "cbc", "history": [],
        "summary_projection": projection,
    }), encoding="utf-8")
    (_sess.SESSION_DIR / f"{sid}.history.jsonl").write_text(
        (json.dumps({"role": "assistant", "content": "row"}) + "\n") * count,
        encoding="utf-8",
    )
    monkeypatch.setattr(_sess, "_read_jsonl", lambda *_args: (_ for _ in ()).throw(
        AssertionError("summary path must not scan a long JSONL")
    ))

    response, gaps, ticks = _max_loop_gap(
        lambda: server.api_list_sessions(summary=1),
    )
    item = next(row for row in response["sessions"] if row["id"] == sid)
    assert item["historyTotal"] == count
    assert item["lastMessage"] == "latest"
    # The metadata-only request can finish in only a few scheduler turns on a
    # warm Windows filesystem; at least one heartbeat proves the loop yielded.
    assert ticks > 0
    assert max(gaps) < 0.05, f"event loop blocked for {max(gaps) * 1000:.1f}ms"


def test_incomplete_120k_summary_backfill_runs_off_loop_and_persists_exact_projection(
    monkeypatch,
):
    """The real repair path, not only GET, must stay off-loop for long JSONL."""
    count = 120_000
    sid = "ses-backfill-120k"
    _sess.SESSION_DIR.mkdir(parents=True, exist_ok=True)
    main_path = _sess.SESSION_DIR / f"{sid}.json"
    history_path = _sess.SESSION_DIR / f"{sid}.history.jsonl"
    main_path.write_text(json.dumps({
        "id": sid,
        "name": sid,
        "adapter": "cbc",
        "history": [],
        "summary_projection": {"revision": 7},
    }), encoding="utf-8")
    history_path.write_text(
        "".join(json.dumps({
            "role": "assistant" if index % 2 else "user",
            "content": f"backfill-{index}",
        }) + "\n" for index in range(count)),
        encoding="utf-8",
    )
    monkeypatch.setattr(_sess, "_read_jsonl", lambda *_args: (_ for _ in ()).throw(
        AssertionError("backfill must use the streaming projection reader")
    ))
    streaming_calls = 0
    original_streaming_reader = _sess._summary_projection_from_jsonl

    def instrumented_streaming_reader(path, **kwargs):
        nonlocal streaming_calls
        streaming_calls += 1
        return original_streaming_reader(path, **kwargs)

    monkeypatch.setattr(_sess, "_summary_projection_from_jsonl", instrumented_streaming_reader)

    async def scenario():
        loop = asyncio.get_running_loop()
        done = asyncio.Event()
        ticks = 0
        gaps: list[float] = []
        last = loop.time()

        async def ticker():
            nonlocal ticks, last
            while not done.is_set():
                await asyncio.sleep(0)
                now = loop.time()
                gaps.append(now - last)
                last = now
                ticks += 1

        ticker_task = asyncio.create_task(ticker())
        try:
            result = await _sess.backfill_summary_projections()
        finally:
            done.set()
            await ticker_task
        return result, ticks, gaps

    result, ticks, gaps = asyncio.run(scenario())
    assert result["state"] == "completed"
    assert result["repaired"] == 1
    assert streaming_calls == 1
    assert ticks > 0
    assert max(gaps) < 0.05
    persisted = json.loads(main_path.read_text(encoding="utf-8"))
    projection = persisted["summary_projection"]
    assert projection["history_total"] == count
    assert projection["last_display_preview"] == f"backfill-{count - 1}"


def test_session_history_cold_read_does_not_block_event_loop():
    """A single 60k-row history must not freeze the loop while it is paged."""
    _seed("ses-cold-history", _rows(60_000))

    response, gaps, ticks = _max_loop_gap(
        lambda: server.api_session_history("ses-cold-history", before=0, limit=50))

    assert response["total"] == 60_000
    assert response["start"] == 59_950
    assert response["hasMore"] is True
    assert response["history"][0]["content"] == _rows(1, start=59_950)[0]["content"]
    assert ticks > 50, f"loop starved during cold read (ticks={ticks})"
    assert max(gaps) < 0.05, f"event loop blocked for {max(gaps) * 1000:.1f}ms"


def test_dashboard_broadcast_is_delivered_during_cold_read(monkeypatch):
    """A dashboard event produced mid-read must reach the client immediately."""
    _seed("ses-cold-broadcast", _rows(60_000))
    original_history_pages = server._history_pages_for
    read_started = threading.Event()
    release_read = threading.Event()

    def blocked_history_pages(session_ids, limit):
        read_started.set()
        assert release_read.wait(timeout=5)
        return original_history_pages(session_ids, limit)

    monkeypatch.setattr(server, "_history_pages_for", blocked_history_pages)

    class _FastWS:
        def __init__(self):
            self.sent: list[dict] = []
            self.closed: list[tuple[int | None, str | None]] = []

        async def send_json(self, data: dict):
            self.sent.append(data)

        async def close(self, code=None, reason=None):
            self.closed.append((code, reason))

    client = _FastWS()
    server.ws_clients.add(client)
    server._ws_outbound.pop(client, None)

    async def scenario():
        read_task = asyncio.create_task(server.api_list_sessions(summary=0))
        deadline = time.monotonic() + 5
        while not read_started.is_set() and time.monotonic() < deadline:
            await asyncio.sleep(0.001)
        assert read_started.is_set(), "cold read did not reach the instrumented page read"
        started = time.monotonic()
        await server.broadcast({"type": "worker.stream", "sessionId": "ses-cold-broadcast"})
        deadline = started + 0.05
        while not client.sent and time.monotonic() < deadline:
            await asyncio.sleep(0.001)
        delivered = time.monotonic() - started
        in_flight = not read_task.done()
        release_read.set()
        response = await read_task
        return response, delivered, in_flight

    try:
        response, delivered, in_flight = asyncio.run(scenario())
    finally:
        release_read.set()
        for entry in tuple(server._ws_outbound.values()):
            if entry._sender_task is not None:
                entry._sender_task.cancel()
        server._ws_outbound.clear()
        server.ws_clients.discard(client)

    assert client.sent and client.sent[0]["type"] == "worker.stream"
    assert in_flight, "event was only delivered after the cold read completed"
    assert delivered < 0.05, f"event stalled behind the cold read: {delivered * 1000:.1f}ms"
    assert any(item["id"] == "ses-cold-broadcast" for item in response["sessions"])


# ── concurrent reads ──

def test_concurrent_cold_reads_return_identical_pages():
    """Parallel offloaded reads must not diverge or duplicate hydration."""
    for index in range(4):
        _seed(f"ses-parallel-{index}", _rows(4_000))
    expected = {
        index: _rows(50, start=3_950)
        for index in range(4)
    }

    async def scenario():
        tasks = []
        for round_index in range(3):
            for index in range(4):
                tasks.append(server.api_session_history(
                    f"ses-parallel-{index}", before=0, limit=50))
        return await asyncio.gather(*tasks)

    responses = asyncio.run(scenario())

    assert len(responses) == 12
    for offset, response in enumerate(responses):
        index = offset % 4
        assert response["total"] == 4_000
        assert response["start"] == 3_950
        assert response["hasMore"] is True
        assert [row["content"] for row in response["history"]] == [
            row["content"] for row in expected[index]
        ]
    # Shallow pages never promote a Session into resident full history.
    for index in range(4):
        cached = _sess._cache.get(f"ses-parallel-{index}")
        if cached is not None:
            assert cached._history_loaded is False


def test_concurrent_list_reads_return_identical_payloads():
    for index in range(3):
        _seed(f"ses-list-parallel-{index}", _rows(3_000))

    async def scenario():
        return await asyncio.gather(*[
            server.api_list_sessions(summary=0) for _ in range(4)
        ])

    responses = asyncio.run(scenario())

    baseline = {
        item["id"]: (item["historyTotal"], item["historyStart"], item["historyTruncated"])
        for item in responses[0]["sessions"]
    }
    assert set(baseline) == {f"ses-list-parallel-{index}" for index in range(3)}
    for response in responses:
        current = {
            item["id"]: (item["historyTotal"], item["historyStart"], item["historyTruncated"])
            for item in response["sessions"]
        }
        assert current == baseline
        for item in response["sessions"]:
            assert item["historyTotal"] == 3_000
            assert len(item["history"]) == 50


# ── page boundary is copy-only ──

def test_history_page_never_rewrites_live_session_history():
    """The read boundary must not clean or publish rows owned by the Session."""
    _seed("ses-page-copy", _rows(1_000))
    live = _sess.get("ses-page-copy")
    assert live is not None and live._history_loaded is True
    marker = "[delivered: task:q_legacy:0123456789ab]\nreal user text"
    live.history[0]["content"] = marker
    live.history[0]["delivered_keys"] = ["keep-me"]

    page = _sess.history_page("ses-page-copy", before=2, limit=10)

    assert page is not None
    assert page["history"][0]["content"] == "real user text"
    # The live Session keeps its own row untouched: the page returned a copy.
    assert live.history[0]["content"] == marker
    assert live.history[0]["delivered_keys"] == ["keep-me"]

    row = page["history"][1]
    assert row is not live.history[1]
    row["content"] = "mutated by caller"
    assert live.history[1]["content"] != "mutated by caller"


# ── paging semantics ──

def test_history_paging_semantics_are_unchanged():
    _seed("ses-paging", _rows(1_000))

    tail = asyncio.run(server.api_session_history("ses-paging", before=0, limit=25))
    assert (tail["total"], tail["start"], tail["hasMore"]) == (1_000, 975, True)
    assert [row["content"] for row in tail["history"]] == [
        row["content"] for row in _rows(25, start=975)
    ]

    middle = asyncio.run(server.api_session_history("ses-paging", before=500, limit=10))
    assert (middle["total"], middle["start"], middle["hasMore"]) == (1_000, 490, True)
    assert [row["content"] for row in middle["history"]] == [
        row["content"] for row in _rows(10, start=490)
    ]

    head = asyncio.run(server.api_session_history("ses-paging", before=25, limit=10))
    assert (head["total"], head["start"], head["hasMore"]) == (1_000, 15, True)

    first = asyncio.run(server.api_session_history("ses-paging", before=10, limit=10))
    assert (first["total"], first["start"], first["hasMore"]) == (1_000, 0, False)

    bounded = asyncio.run(server.api_get_session("ses-paging", historyLimit=12))
    assert bounded["historyTotal"] == 1_000
    assert bounded["historyStart"] == 988
    assert bounded["historyTruncated"] is True
    assert len(bounded["history"]) == 12

    capped = asyncio.run(server.api_session_history("ses-paging", before=0, limit=99_999))
    assert len(capped["history"]) == _sess.HISTORY_PAGE_MAX
    assert capped["start"] == 1_000 - _sess.HISTORY_PAGE_MAX


def test_empty_history_page_semantics():
    _seed("ses-empty", [])

    page = asyncio.run(server.api_session_history("ses-empty", before=0, limit=50))
    assert page["total"] == 0
    assert page["history"] == []
    assert page["hasMore"] is False
    assert page["start"] == 0

    listed = asyncio.run(server.api_list_sessions(summary=0))
    item = next(entry for entry in listed["sessions"] if entry["id"] == "ses-empty")
    assert item["history"] == []
    assert item["historyTotal"] == 0
    assert item["historyTruncated"] is False


def test_corrupt_tail_line_does_not_shift_pagination():
    """A crash-tail half-line stays invisible to before/limit/total."""
    _seed("ses-corrupt", _rows(200))
    jsonl = _sess._history_path("ses-corrupt")
    with open(jsonl, "ab") as handle:
        handle.write(b'{"role": "assistant", "content": "torn')

    page = asyncio.run(server.api_session_history("ses-corrupt", before=0, limit=20))
    assert page["total"] == 200
    assert page["start"] == 180
    assert len(page["history"]) == 20
    assert page["history"][-1]["content"] == _rows(1, start=199)[0]["content"]

    # Recovery: the next real append repairs the half-line instead of losing data.
    value = _sess.get("ses-corrupt")
    assert value is not None
    value.history.append({"role": "user", "content": "after-crash"})
    _sess.save(value)
    recovered = asyncio.run(server.api_session_history("ses-corrupt", before=0, limit=5))
    assert recovered["total"] == 201
    assert recovered["history"][-1]["content"] == "after-crash"


def test_missing_and_legacy_sessions_keep_their_contracts():
    missing = asyncio.run(server.api_session_history("ses-does-not-exist", before=0, limit=5))
    assert missing == {"error": "Session not found"}
    assert asyncio.run(server.api_get_session("ses-does-not-exist", historyLimit=5)) == {
        "error": "Session not found",
    }
    assert asyncio.run(server.api_get_session("ses-does-not-exist", view="metadata")) == {
        "error": "Session not found",
    }

    # Legacy main-file-only store: no companion JSONL.
    legacy_rows = _rows(30)
    _sess.SESSION_DIR.mkdir(parents=True, exist_ok=True)
    _sess._path("ses-legacy-only").write_text(json.dumps({
        "id": "ses-legacy-only",
        "name": "ses-legacy-only",
        "adapter": "cbc",
        "model": "test-model",
        "history": legacy_rows,
    }), encoding="utf-8")
    _sess._cache.clear()
    _sess._all_loaded = False

    legacy = asyncio.run(server.api_session_history("ses-legacy-only", before=0, limit=10))
    assert legacy["total"] == 30
    assert legacy["start"] == 20
    assert [row["content"] for row in legacy["history"]] == [
        row["content"] for row in _rows(10, start=20)
    ]


# ── read / write race ──

def test_history_reads_racing_loop_side_appends_stay_consistent():
    """Offloaded reads must not lose or corrupt concurrent appends."""
    _seed("ses-race", _rows(5_000))
    live = _sess.get("ses-race")
    assert live is not None
    baseline = [row["content"] for row in live.history]
    appended = 40

    async def scenario():
        responses: list[dict] = []

        async def reader():
            for _ in range(8):
                responses.append(await server.api_session_history(
                    "ses-race", before=0, limit=50))

        async def writer():
            for index in range(appended):
                _sess.append_history(live, {
                    "role": "user", "content": f"appended-{index:03d}",
                })
                await asyncio.sleep(0)

        await asyncio.gather(reader(), writer())
        return responses

    responses = asyncio.run(scenario())

    # The live object kept every append and every original row in order.
    assert len(live.history) == 5_000 + appended
    assert [row["content"] for row in live.history[:5_000]] == baseline
    assert [row["content"] for row in live.history[5_000:]] == [
        f"appended-{index:03d}" for index in range(appended)
    ]

    # Every page is a well-formed ordered suffix snapshot.  Appends only ever
    # extend the tail, so a page recorded mid-append must still equal the very
    # same [start, total) slice of the final history.
    final = [row["content"] for row in live.history]
    previous_total = 5_000
    for response in responses:
        total = response["total"]
        assert 5_000 <= total <= 5_000 + appended
        assert total >= previous_total
        previous_total = total
        assert response["start"] == total - len(response["history"])
        assert [row["content"] for row in response["history"]] == final[
            response["start"]:total]
        assert response["hasMore"] is (response["start"] > 0)
    assert _sess.get("ses-race")._history_loaded is True


def test_list_reads_racing_persistence_do_not_raise():
    """A concurrent save must not break an offloaded list read."""
    _seed("ses-race-list", _rows(3_000))
    live = _sess.get("ses-race-list")
    assert live is not None

    async def scenario():
        async def reader():
            return await server.api_list_sessions(summary=0)

        async def writer():
            for index in range(20):
                _sess.append_history(live, {"role": "user", "content": f"w-{index}"})
                await _sess.save_async(live)

        list_result, _ = await asyncio.gather(reader(), writer())
        return list_result

    result = asyncio.run(scenario())

    item = next(entry for entry in result["sessions"] if entry["id"] == "ses-race-list")
    assert item["historyTotal"] >= 3_000
    assert item["historyTotal"] <= 3_000 + 20
    assert len(item["history"]) == 50


def test_offloaded_store_reads_stay_serialized(monkeypatch):
    """Cold-read storms must not fan out into many competing Python threads.

    The store reads are GIL-bound JSON parsing, so extra threads cannot make
    them faster; they only let a burst starve the event loop.  Offloading them
    onto one dedicated thread keeps the serialization these reads had when they
    ran inline on the loop.
    """
    for index in range(3):
        _seed(f"ses-serial-{index}", _rows(2_000))

    lock = threading.Lock()
    running = 0
    peak = 0
    real_pages = server._history_pages_for

    def instrumented(session_ids, limit):
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        try:
            time.sleep(0.05)
            return real_pages(session_ids, limit)
        finally:
            with lock:
                running -= 1

    monkeypatch.setattr(server, "_history_pages_for", instrumented)

    async def scenario():
        return await asyncio.gather(*[
            server.api_list_sessions(summary=0) for _ in range(8)
        ])

    responses = asyncio.run(scenario())

    assert peak == 1, f"store reads fanned out to {peak} concurrent threads"
    assert len(responses) == 8
    for response in responses:
        assert len(response["sessions"]) == 3
        for item in response["sessions"]:
            assert item["historyTotal"] == 2_000
            assert len(item["history"]) == 50


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
