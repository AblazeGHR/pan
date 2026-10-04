"""MA 五项生产化缺口的独立复现/验证脚本（HeadlessEmulator）。

用法（从仓库根）：

    E:/software/miniforge/python.exe audit/terminal/implementation/emulator/repro_ma_gaps.py

- 对 **baseline**（`pre_fix/sidecar.baseline.mjs` + `pre_fix/emulator.baseline.py`）
  运行本脚本时五项检查全部失败（证据见 `pre_fix/pytest_pre_fix.log`）；
- 对当前生产实现运行时应全部通过（post-fix 证据）。
- 脚本自建/自清理 sidecar；stdout 输出机器可读 JSON。
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT))

from packages.core.terminal.contracts import Fidelity, Recovery  # noqa: E402
from packages.core.terminal.emulator import HeadlessEmulator  # noqa: E402

RESULTS: list[dict] = []


def _check(name: str, ok: bool, detail: dict) -> None:
    RESULTS.append({"gap": name, "ok": bool(ok), "detail": detail})


def gap_b1_unknown_after_saturation() -> None:
    emu = HeadlessEmulator()
    try:
        stream = b"\x1b[31m" * 5000 + b"\x1b[?7777h" + b"END"
        emu.feed_at(0, stream)
        snap = emu.snapshot(timeout=30)
        _check(
            "B1_4096_event_cap_masks_unknown",
            snap.fidelity is Fidelity.PARTIAL and "7777" in snap.note,
            {"fidelity": snap.fidelity.value, "note": snap.note[:200]},
        )
    finally:
        emu.close(timeout=10)


def gap_b2_unbounded_diagnostics() -> None:
    emu = HeadlessEmulator(max_queue_bytes=32 * 1024, max_queue_ops=64)
    try:
        emu._test_stall(800)
        for i in range(200):
            emu.feed_at(i * 4096, b"z" * 4096)
        time.sleep(1.2)
        diag = emu.diagnostics()
        bounded = len(diag["rejected_ranges"]) <= 64 and diag["overflow"].get("rejected_range", 0) > 0
        _check(
            "B2_unbounded_rejected_ranges",
            bounded,
            {
                "rejected_range_count": len(diag["rejected_ranges"]),
                "rejected_feed_blocks": diag["counters"]["rejected_feed_blocks"],
                "overflow": diag["overflow"],
            },
        )
    finally:
        emu.close(timeout=10)


def gap_b3_engine_error_not_sticky() -> None:
    emu = HeadlessEmulator()
    try:
        emu._test_inject_feed_error("next-feed")
        emu.feed_at(0, b"AAAA")
        emu.feed_at(4, b"BBBB")
        snap = emu.snapshot(timeout=20)
        snap2 = emu.snapshot(timeout=20)
        ok = (
            snap.fidelity is Fidelity.PARTIAL
            and snap2.fidelity is Fidelity.PARTIAL
            and "ENGINE_OP_ERROR" in snap2.note
            and "BBBB" in snap.serialized_screen
            and emu.engine_alive
        )
        _check(
            "B3_feed_error_not_sticky_degraded",
            ok,
            {
                "fidelity": snap.fidelity.value,
                "sticky_fidelity": snap2.fidelity.value,
                "note": snap2.note[:200],
                "engine_alive": emu.engine_alive,
            },
        )
    finally:
        emu.close(timeout=10)


def gap_b4_reset_uses_global_frontier() -> None:
    emu = HeadlessEmulator(control_timeout=15.0)
    try:
        emu._test_stall(500)
        emu.feed_at(0, b"A" * 1000)
        emu.feed_at(1000, b"B" * 1000)
        results: list = []
        thread = threading.Thread(target=lambda: results.append(emu.reset_baseline()))
        thread.start()
        time.sleep(0.15)
        emu.feed_at(2000, b"C" * 1000)  # 排在 reset 之后的 future feed
        thread.join(timeout=20)
        diag = emu.diagnostics()
        snap = results[0]
        ok = int(diag["baseline_cursor"]) == 2000 and snap.cursor == 2000
        _check(
            "B4_reset_takes_future_producer_frontier",
            ok,
            {
                "baseline_cursor": diag["baseline_cursor"],
                "reset_snapshot_cursor": snap.cursor,
                "producer_frontier": emu.producer_frontier,
            },
        )
    finally:
        emu.close(timeout=10)


def gap_b5_old_gap_poisons_cursor() -> None:
    emu = HeadlessEmulator(control_timeout=15.0)
    try:
        emu.feed_at(0, b"A" * 10)
        emu.feed_at(50, b"B" * 10)
        before = emu.snapshot(timeout=15)
        cursors_before = emu.cursors_valid
        emu.reset_baseline()
        after = emu.snapshot(timeout=15)
        ok = (
            cursors_before is False
            and emu.cursors_valid is True
            and "SOURCE_GAP_STICKY" not in after.note
            and after.fidelity is Fidelity.PARTIAL
            and "BASELINE_RESET_FRESH_VIEW" in after.note
        )
        _check(
            "B5_reset_does_not_clear_old_gap",
            ok,
            {
                "cursors_valid_before_reset": cursors_before,
                "cursors_valid_after_reset": emu.cursors_valid,
                "fidelity_after_reset": after.fidelity.value,
                "recovery_after_reset": after.recovery.value,
                "note_after_reset": after.note[:220],
                "note_before_reset": before.note[:120],
            },
        )
    finally:
        emu.close(timeout=10)


def main() -> int:
    for fn in (
        gap_b1_unknown_after_saturation,
        gap_b2_unbounded_diagnostics,
        gap_b3_engine_error_not_sticky,
        gap_b4_reset_uses_global_frontier,
        gap_b5_old_gap_poisons_cursor,
    ):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - 复现脚本如实上报
            RESULTS.append({"gap": fn.__name__, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
    payload = {"all_ok": all(item.get("ok") for item in RESULTS), "results": RESULTS}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["all_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
