"""Read-only deterministic probe for the server WS transport contract.

Drives the *real* `packages.web.server` broadcast path with a fake WebSocket so
the enqueue/coalesce/ordering rules can be asserted without any network or
provider.  The probe also deliberately tries to produce a reversed
snapshot/event order to decide whether that race is real.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import session as _sess  # noqa: E402
from packages.web import server as srv  # noqa: E402


class _FakeWs:
    def __init__(self):
        self.frames: list[dict] = []
        self.gate: asyncio.Event | None = None

    async def send_json(self, data):
        if self.gate is not None:
            await self.gate.wait()
        self.frames.append(data)

    async def accept(self):
        pass

    async def receive_text(self):
        raise RuntimeError("not used")

    async def close(self, *_a, **_k):
        pass


async def _drain():
    # Let each connection's sender task flush its queue.
    for _ in range(80):
        await asyncio.sleep(0.005)


def _mk_session(sid: str):
    s = _sess.Session(id=sid, name=sid, adapter="cbc")
    s.history = [{"role": "user", "content": "q"}]
    s._hist_persisted = 1
    s._history_loaded = True
    _sess._cache[sid] = s
    return s


def _stream(sid, item_id, text, *, delta=False, cumulative=None, final=False):
    event = {
        "type": "content.part" if delta else "assistant",
        "role": "assistant",
        "delta": delta,
        "item_id": item_id,
        "turn_id": "turn-1",
        "part": {"type": "text", "text": text},
        "message": {"content": [{"type": "text", "text": text}]},
    }
    if cumulative is not None:
        event["stream_text"] = cumulative
    if final:
        event["final"] = True
    return {
        "type": "worker.stream",
        "workerId": "worker-1",
        "sessionId": sid,
        "generation": 0,
        "taskSeq": 1,
        "taskId": "task-1",
        "event": event,
    }


async def case_ordering() -> dict:
    _sess._cache.clear()
    sid = "ses-server-order"
    _mk_session(sid)
    ws = _FakeWs()
    srv.ws_clients.add(ws)
    srv._ws_outbound.pop(ws, None)
    try:
        await srv.broadcast(_stream(sid, "a1", "Hel", delta=True, cumulative="Hel"))
        await srv.broadcast(_stream(sid, "a1", "lo", delta=True, cumulative="Hello"))
        await srv.broadcast(_stream(sid, "a1", "Hello", final=True))
        await srv.broadcast({
            "type": "worker.result", "workerId": "worker-1", "sessionId": sid,
            "generation": 0, "status": "done", "result": "Hello",
            "taskSeq": 1, "taskId": "task-1", "resultCursor": 1,
            "terminalKey": "task:task-1", "historyRevision": 1,
        })
    finally:
        srv.ws_clients.discard(ws)
    await _drain()
    return {"frames": ws.frames}


async def case_coalesce() -> dict:
    _sess._cache.clear()
    sid = "ses-server-coalesce"
    _mk_session(sid)
    ws = _FakeWs()
    ws.gate = asyncio.Event()
    srv.ws_clients.add(ws)
    srv._ws_outbound.pop(ws, None)
    try:
        # First broadcast starts a send that blocks on the gate, so subsequent
        # deltas accumulate in the bounded queue tail and can be coalesced.
        await srv.broadcast(_stream(sid, "a1", "A", delta=True, cumulative="A"))
        await srv.broadcast(_stream(sid, "a1", "B", delta=True, cumulative="AB"))
        await srv.broadcast(_stream(sid, "a1", "C", delta=True, cumulative="ABC"))
        await srv.broadcast(_stream(sid, "a1", "ABC", final=True))
        ws.gate.set()
    finally:
        srv.ws_clients.discard(ws)
    await _drain()
    return {"frames": ws.frames}


async def case_bad_coalesce_boundary() -> dict:
    """A non-prefix cumulative reset must not be silently merged."""
    _sess._cache.clear()
    sid = "ses-server-coalesce-bad"
    _mk_session(sid)
    ws = _FakeWs()
    ws.gate = asyncio.Event()
    srv.ws_clients.add(ws)
    srv._ws_outbound.pop(ws, None)
    try:
        await srv.broadcast(_stream(sid, "a1", "AB", delta=True, cumulative="AB"))
        # Same coalesce key but a completely different cumulative text without a
        # replace flag: the merge must refuse and keep both frames.
        await srv.broadcast(_stream(sid, "a1", "XY", delta=True, cumulative="XY"))
        ws.gate.set()
    finally:
        srv.ws_clients.discard(ws)
    await _drain()
    return {"frames": ws.frames}


async def case_snapshot_order() -> dict:
    """Try to force a snapshot with a stale eventSeq to arrive after a newer event."""
    _sess._cache.clear()
    sid = "ses-server-snap"
    _mk_session(sid)
    ws = _FakeWs()
    srv.ws_clients.add(ws)
    srv._ws_outbound.pop(ws, None)
    try:
        # A normal live event first.
        await srv.broadcast(_stream(sid, "a1", "A", delta=True, cumulative="A"))
        # Then a resync snapshot for the same session (what the browser sends
        # after a focus/reconnect or a client-detected gap).
        await srv._send_resync_snapshot(ws, [sid])
        # A concurrent live event right after.
        await srv.broadcast(_stream(sid, "a1", "AB", delta=True, cumulative="AB"))
    finally:
        srv.ws_clients.discard(ws)
    await _drain()
    return {"frames": ws.frames}


async def case_overflow_keeps_control() -> dict:
    """A bounded queue must retain worker.result and mark resync for deltas."""
    _sess._cache.clear()
    sid = "ses-server-overflow"
    _mk_session(sid)
    ws = _FakeWs()
    ws.gate = asyncio.Event()
    srv.ws_clients.add(ws)
    srv._ws_outbound.pop(ws, None)
    try:
        # Fill the outbound queue with non-coalescible control events while the
        # sender is blocked, then deliver a terminal result.
        for i in range(srv._WS_OUTBOUND_QUEUE_MAX + 5):
            await srv.broadcast({
                "type": "queue.snapshot", "sessionId": sid, "n": i,
            })
        await srv.broadcast({
            "type": "worker.result", "workerId": "worker-1", "sessionId": sid,
            "generation": 0, "status": "done", "result": "Hello",
            "taskSeq": 1, "taskId": "task-1", "resultCursor": 1,
        })
        ws.gate.set()
    finally:
        srv.ws_clients.discard(ws)
    await _drain()
    types = [f.get("type") for f in ws.frames]
    return {
        "max_queue": srv._WS_OUTBOUND_QUEUE_MAX,
        "frame_count": len(ws.frames),
        "has_resync_required": "resync_required" in types,
        "has_worker_result": "worker.result" in types,
        "tail_types": types[-4:],
    }


async def case_delta_flood_keeps_result() -> dict:
    """Delta flood + terminal result: deltas dropped, resync marked, result kept."""
    _sess._cache.clear()
    sid = "ses-server-flood"
    _mk_session(sid)
    ws = _FakeWs()
    ws.gate = asyncio.Event()
    srv.ws_clients.add(ws)
    srv._ws_outbound.pop(ws, None)
    try:
        # Distinct item ids so the deltas cannot be coalesced into one tail.
        for i in range(srv._WS_OUTBOUND_QUEUE_MAX + 5):
            await srv.broadcast(_stream(sid, f"item-{i}", "x", delta=True,
                                       cumulative="x"))
        await srv.broadcast({
            "type": "worker.result", "workerId": "worker-1", "sessionId": sid,
            "generation": 0, "status": "done", "result": "done",
            "taskSeq": 1, "taskId": "task-1", "resultCursor": 1,
        })
        ws.gate.set()
    finally:
        srv.ws_clients.discard(ws)
    await _drain()
    types = [f.get("type") for f in ws.frames]
    return {
        "max_queue": srv._WS_OUTBOUND_QUEUE_MAX,
        "frame_count": len(ws.frames),
        "has_resync_required": "resync_required" in types,
        "has_worker_result": "worker.result" in types,
        "tail_types": types[-3:],
    }


def summarise_frames(frames: list[dict]) -> list[dict]:
    out = []
    for f in frames:
        out.append({
            "type": f.get("type"),
            "eventSeq": f.get("eventSeq"),
            "deliverySeq": f.get("deliverySeq"),
            "sourceCursorStart": f.get("sourceCursorStart"),
            "sourceCursorEnd": f.get("sourceCursorEnd"),
            "delta": (f.get("event") or {}).get("delta"),
            "stream_text": (f.get("event") or {}).get("stream_text"),
            "result": f.get("result") if f.get("type") == "worker.result" else None,
        })
    return out


async def main() -> int:
    report = {}
    report["ordering"] = summarise_frames((await case_ordering())["frames"])
    report["coalesce"] = summarise_frames((await case_coalesce())["frames"])
    report["bad_coalesce_boundary"] = summarise_frames(
        (await case_bad_coalesce_boundary())["frames"])
    snap = await case_snapshot_order()
    report["snapshot_order"] = summarise_frames(snap["frames"])
    report["overflow"] = await case_overflow_keeps_control()
    report["delta_flood"] = await case_delta_flood_keeps_result()
    # The server logs to stdout; write the machine-readable report to the file
    # named on the command line so the evidence is not polluted by log lines.
    target = sys.argv[1] if len(sys.argv) > 1 else None
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if target:
        Path(target).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
