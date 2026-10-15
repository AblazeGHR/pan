"""Registered MCP Send functions -> isolated HTTP -> fake provider timings.

The caller history is synthetic. This never connects to an installed Pan or
real provider. Transport startup/stdio framing is outside the measurement.
"""
from __future__ import annotations

import argparse
import json
import os
import time

from probe_queue_send_latency import (
    ROOT, _Runtime, _Recorder, _free_port, _measure_round, _finish_round, _percentiles, _source_hashes,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True)
    parser.add_argument("--history-rows", type=int, default=8000)
    parser.add_argument("--rounds", type=int, default=6)
    args = parser.parse_args()
    caller_id = "ses_mcp_latency"
    root = ROOT / "evidence" / f"mcp-send-latency-{args.label}"
    rt = _Runtime(root, _free_port())
    recorder = _Recorder(rt.base_url.replace("http", "ws", 1) + "/ws/agent")
    report = {"label": args.label, "port": rt.port, "dataRoot": str(root),
              "sourceSha256": _source_hashes(), "callerHistoryRows": args.history_rows, "rows": []}

    def seed(data_root):
        from packages.core import session as sess
        sess.SESSION_DIR = data_root / "sessions"
        sess._cache.clear()
        s = sess.Session(id=caller_id, name="probe-caller", queue_paused=True)
        for _ in range(args.history_rows):
            sess.append_history(s, {"role": "assistant", "content": "H" * 2048})
        sess.save(s)

    try:
        rt.start(seed=seed)
        recorder.start()
        created = rt.request("POST", "/api/sessions", json={
            "name": "real-fifo", "adapter": "cbc", "model": "hy3",
            "permissionMode": "bypassPermissions", "outputMode": "stream"}).json()
        sid = created["id"]
        spawned = rt.request("POST", "/api/spawn", json={"sessionId": sid}).json()
        worker_id = spawned["workerId"]
        _measure_round(rt, recorder, sid, "warmup", "cm-warmup")
        os.environ["PAN_API_URL"] = rt.base_url
        os.environ["PAN_AGENT_SESSION_ID"] = caller_id
        os.environ["PAN_AGENT_SESSION_TITLE"] = "probe-caller"
        from packages.mcp import server as mcp
        mcp._pan_api_url = rt.base_url
        original_api = mcp._api
        stages = []

        def measured_api(method, path, *pos, **kw):
            started = time.perf_counter()
            value = original_api(method, path, *pos, **kw)
            stages.append({"method": method, "path": path,
                           "ms": round((time.perf_counter()-started)*1000, 1)})
            return value

        mcp._api = measured_api
        for kind in ("agent_send", "worker_send"):
            for i in range(args.rounds):
                text = f"{kind}-{i}"
                prefixed = mcp._agent_message_prefix(text)
                mark = len(recorder.events)
                stages.clear()
                wall = time.time()
                started = time.perf_counter()
                payload = (mcp.agent_send(sid, text) if kind == "agent_send"
                           else mcp.worker_send(worker_id=worker_id, text=text))
                acked = time.perf_counter()
                assert payload.get("status") == "queued", payload
                added = recorder.wait_for(
                    lambda e: e.get("type") == "queue.item_added" and e.get("sessionId") == sid
                    and (e.get("item") or {}).get("text") == prefixed and e["_at"] >= wall)
                assert added is not None, payload
                row = {"kind": kind, "text": prefixed,
                       "queueItemId": added["queueItemId"], "eventMark": mark,
                       "requestAt": wall, "ackAt": time.time(),
                       "httpAckMs": round((acked-started)*1000, 1),
                       "stages": list(stages)}
                _finish_round(rt, recorder, row)
                assert recorder.wait_for(
                    lambda e: e.get("type") == "worker.status" and e.get("sessionId") == sid
                    and e.get("status") == "idle" and e["_at"] >= wall)
                report["rows"].append(row)
        report["aggregate"] = {
            kind: _percentiles([r["httpAckMs"] for r in report["rows"] if r["kind"] == kind])
            for kind in ("agent_send", "worker_send")}
        out = ROOT / "evidence" / f"mcp-send-latency-{args.label}.json"
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report["aggregate"]))
        print("wrote " + str(out))
    finally:
        try:
            if recorder.thread.ident is not None:
                recorder.stop()
        finally:
            rt.stop()


if __name__ == "__main__":
    main()
