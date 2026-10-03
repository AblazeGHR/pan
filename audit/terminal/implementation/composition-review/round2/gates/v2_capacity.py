"""v2（F2 独立容量口径）：单一 monotonic 首次提交→applied==total；producer ops 与引擎帧分列；
真实小块合并；partial 不假 full（探针级 OSC）。

- 口径：t0 = 首次 feed_at 之前；t1 = diagnostics.applied_cursor >= total（=全部应用）；
  dt = t1 - t0（**不扣除任何 probe 窗口**）；对照 r3 旧口径 35.1s。
- 分列：producer_feed_ops（本探针提交次数）vs engine feed_batches/feed_batched_ops/max_batch。
- partial：OSC 标题序列 → reason 记录且 fidelity != full。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from v_common import start_hard_watchdog, wait_until, write_evidence
from packages.core.terminal.emulator import HeadlessEmulator

LINE = b"PAN-CR2-PAD-%05d " + b"x" * 60 + b"\r\n"


def main() -> int:
    watchdog = start_hard_watchdog("v2", 300)
    out: dict = {}

    emu = HeadlessEmulator(cols=80, rows=24, scrollback=1000)
    try:
        stream = b"".join(LINE % i for i in range(3800))
        chunk = 135
        t0 = time.monotonic()
        seq = 0
        ops = 0
        for offset in range(0, len(stream), chunk):
            emu.feed_at(seq, stream[offset : offset + chunk])
            seq += len(stream[offset : offset + chunk])
            ops += 1
        applied = wait_until(
            lambda: (
                lambda a: a if a >= len(stream) else None
            )(int(emu.diagnostics().get("applied_cursor", -1))),
            120.0,
            interval=0.02,
        )
        dt = round(time.monotonic() - t0, 3)
        diag = emu.diagnostics()
        counters = diag.get("counters", {})
        out["capacity"] = {
            "stream_bytes": len(stream),
            "producer_feed_ops": ops,
            "applied_bytes": int(applied) if applied else None,
            "single_monotonic_seconds": dt,
            "engine_feed_batches": counters.get("feed_batches"),
            "engine_feed_batched_ops": counters.get("feed_batched_ops"),
            "max_feed_batch_bytes": counters.get("max_feed_batch_bytes"),
            "r3_reference_seconds_135b": 35.1,
            "improvement_factor_vs_r3": round(35.1 / max(dt, 1e-9), 1),
            "batches_lt_ops": (counters.get("feed_batches") or 10**9) < ops,
            "max_batch_within_64k": (counters.get("max_feed_batch_bytes") or 10**9)
            <= 64 * 1024,
            "applied_reached": bool(applied),
        }
        snap = emu.snapshot(timeout=30.0)
        out["capacity"]["fidelity_clean_stream"] = str(getattr(snap.fidelity, "value", snap.fidelity))
        out["capacity"]["recovery_clean_stream"] = str(getattr(snap.recovery, "value", snap.recovery))

        # partial：OSC 标题（未序列化类）→ 不假 full
        osc = b"\x1b]0;CR2-TITLE\x07"
        mark = b"CR2-OSC-MARK\r\n"
        emu.feed_at(len(stream), osc)
        emu.feed_at(len(stream) + len(osc), mark)
        wait_until(
            lambda: (
                lambda a: a if a >= len(stream) + len(osc) + len(mark) else None
            )(int(emu.diagnostics().get("applied_cursor", -1))),
            30.0,
            interval=0.02,
        )
        snap2 = emu.snapshot(timeout=30.0)
        diag2 = emu.diagnostics()
        out["partial_osc"] = {
            "fidelity": str(getattr(snap2.fidelity, "value", snap2.fidelity)),
            "recovery": str(getattr(snap2.recovery, "value", snap2.recovery)),
            "reasons": list(diag2.get("reasons", []))[:8],
            "note": str(getattr(snap2, "note", ""))[:220],
            "not_full": str(getattr(snap2.recovery, "value", snap2.recovery)) != "full",
            "note_carries_reason": "reasons=" in str(getattr(snap2, "note", "")),
        }
    finally:
        report = emu.close(timeout=15.0)
        out["close"] = {"closed": bool(report.closed), "job_verified": bool(report.job_verified)}

    c = out["capacity"]
    out["verdict"] = {
        "single_monotonic_reached": bool(c["applied_reached"]),
        "faster_than_r3_reference": c["single_monotonic_seconds"] < 5.0,
        "batching_effective": bool(c["batches_lt_ops"]) and bool(c["max_batch_within_64k"]),
        "clean_stream_full": c.get("fidelity_clean_stream") == "full",
        "osc_partial_not_upgraded": bool(out["partial_osc"]["not_full"]),
        "close_clean": out["close"]["closed"] and out["close"]["job_verified"],
    }
    write_evidence("v2_capacity", out)
    watchdog.cancel()
    print(json.dumps(out["verdict"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
