"""HTTP/WS/CLI evidence for lease expiry, drain contention and Steer receipts.

The test-only launcher advertises synthetic native-control support and holds
one drain. The provider is a local fake CLI; no real LLM or credentials.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time

from probe_queue_send_latency import ROOT, _Runtime, _Recorder, _free_port, _measure_round, _finish_round, _source_hashes


def wait(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.005)
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True)
    parser.add_argument("--missing-receipt", action="store_true")
    args = parser.parse_args()
    root = ROOT / "evidence" / f"queue-control-latency-{args.label}"
    rt = _Runtime(root, _free_port())
    rt.client.timeout = 40
    rt.fake_cli = ROOT / "tests" / "support" / "fake_latency_cli.py"
    rt.extra_env["PAN_E2E_LATENCY_CONTROLS"] = "1"
    recorder = _Recorder(rt.base_url.replace("http", "ws", 1) + "/ws/agent")
    report = {"label": args.label, "port": rt.port, "dataRoot": str(root),
              "sourceSha256": _source_hashes(), "leaseTtlMs": 150, "drainGateMs": 350, "fakeProvider": True}
    try:
        rt.start()
        recorder.start()
        created = rt.request("POST", "/api/sessions", json={
            "name": "real-fifo", "adapter": "cbc", "model": "hy3",
            "permissionMode": "bypassPermissions", "outputMode": "stream"}).json()
        sid = created["id"]
        rt.request("POST", "/api/spawn", json={"sessionId": sid})
        _measure_round(rt, recorder, sid, "warmup", "warmup")
        queue = f"/api/sessions/{sid}/queue"
        rt.request("PATCH", queue+"/paused", json={"paused": True})
        head = _measure_round(rt, recorder, sid, "lease-head", "lease-head", enqueue_only=True)
        tail = _measure_round(rt, recorder, sid, "lease-tail", "lease-tail", enqueue_only=True)
        lease = rt.request("POST", queue+f'/{head["queueItemId"]}/edit',
                           json={"expectedRevision": 1, "editToken": "probe-editor"}).json()
        assert lease.get("ok"), lease
        rt.request("PATCH", queue+"/paused", json={"paused": False})
        recovered = wait(lambda: rt.received("lease-tail"), timeout=0.8)
        report["leaseExpiry"] = {
            "headId": head["queueItemId"], "tailId": tail["queueItemId"],
            "deliveredWithoutAnotherSend": bool(recovered),
            "expiryToFirstCliMs": round((rt.received("lease-head")[0]["at"]-lease["expiresAt"])*1000, 1)
            if recovered else None}
        if not recovered:
            # Release is an explicit wake on the old code; it never reposts.
            rt.request("POST", queue+f'/{head["queueItemId"]}/edit/release',
                       json={"editToken": lease["editToken"]})
        for row in (head, tail):
            _finish_round(rt, recorder, row)
        assert recorder.wait_for(lambda e: e.get("type") == "worker.status"
            and e.get("sessionId") == sid and e.get("status") == "idle" and e["_at"] >= tail["requestAt"])
        received = [r["text"] for r in rt.records() if r.get("kind") == "received"]
        assert received.index("lease-head") < received.index("lease-tail")

        held = _measure_round(rt, recorder, sid, "hold-A", "hold-A", enqueue_only=True)
        _finish_round(rt, recorder, held)
        steer_path = f"/api/sessions/{sid}/worker/steer"

        def timed_steer(text):
            wall = time.time()
            start = time.perf_counter()
            payload = rt.request("POST", steer_path, json={"text": text, "messageId": "steer:"+text}).json()
            return {"requestAt": wall, "ackAt": time.time(),
                    "ackMs": round((time.perf_counter()-start)*1000, 1), "payload": payload}

        with ThreadPoolExecutor(max_workers=2) as pool:
            steer = pool.submit(timed_steer, "blocked-control")
            assert wait(lambda: (root / "steer-write").exists())
            timer = threading.Timer(0.35, lambda: (root / "release-drain").touch())
            timer.start()
            try:
                appended = _measure_round(rt, recorder, sid, "during-steer", "during-steer", enqueue_only=True)
                steered = steer.result(timeout=3)
            finally:
                timer.join(timeout=1)
                (root / "release-drain").touch()
        receipt = next(r for r in rt.records() if r.get("kind") == "receipt" and r["text"] == "blocked-control")
        report["directSteerDrain"] = {**steered,
            "receiptToAckMs": round((steered["ackAt"]-receipt["at"])*1000, 1),
            "concurrentSendAckMs": appended["httpAckMs"]}
        queued = _measure_round(rt, recorder, sid, "late-receipt", "late-receipt", enqueue_only=True)
        start = time.perf_counter()
        pending = rt.request("POST", queue+f'/{queued["queueItemId"]}/steer',
                             json={"expectedRevision": 1}).json()
        queue_steer_ack_ms = round((time.perf_counter()-start)*1000, 1)
        snapshot = rt.request("GET", queue).json()
        item = next(it for it in snapshot["items"] if it["queueItemId"] == queued["queueItemId"])
        assert item["meta"]["steerPending"] is True
        (root / "release-receipt").touch()
        delivered = recorder.wait_for(lambda e: e.get("type") == "queue.item_delivered"
            and queued["queueItemId"] in (e.get("queueItemIds") or []))
        assert delivered is not None
        report["queueSteerLateReceipt"] = {
            "httpAckMs": queue_steer_ack_ms,
            "originalId": queued["queueItemId"], "pendingPayload": pending,
            "retainedUntilReceipt": item["meta"]["steerPending"], "lateReceiptDelivered": True}
        if args.missing_receipt:
            report["directSteerMissingReceipt"] = timed_steer("missing-receipt")
            second = timed_steer("do-not-resend")
            report["blockedSecondSteer"] = second
            assert not any(r.get("kind") == "control" and r["text"] == "do-not-resend" for r in rt.records())
        rt.gates["hold-A"].touch()
        _finish_round(rt, recorder, appended)
        final = rt.request("GET", queue).json()
        assert final["items"] == []
        report["finalQueueCount"] = 0
        out = ROOT / "evidence" / f"queue-control-latency-{args.label}.json"
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))
    finally:
        try:
            if recorder.thread.ident is not None:
                recorder.stop()
        finally:
            rt.stop()


if __name__ == "__main__":
    main()
