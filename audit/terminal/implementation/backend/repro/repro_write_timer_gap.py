"""Repro (pre-fix): write 预算 Timer 的“检查后/WriteFile 前”空隙。

430 实现：writer 在 `timer_fired` 检查之后、`write_raw` 之前存在窗口；若预算计时器恰好
在此窗口触发，取消（CancelSynchronousIo）无挂起 I/O 而落空，且取消**只发一次**——
之后 writer 进入一次长 WriteFile 就会**永久阻塞**（超出预算仍然返回不了），直到 close 介入。

确定性门控：monkeypatch `b._spawn.write_raw`：第一次调用先 sleep(0.9s)（>预算 0.3s，让
timer 在“无 I/O 挂起”时触发），再调真实 write_raw 写一个 64MiB 单块（conhost 节流，
单次 WriteFile 会阻塞数十秒）。修复前预期：write 线程在 5s 后仍存活（超出预算未返回）；
修复后预期：在 ~预算内返回 partial。

只创建/终止自有子进程；清理 = terminate→wait_dead→close（自有句柄）。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

WT = r"D:/project/pan-worktrees/terminal-backend-implement-20261003"
sys.path.insert(0, WT)
PY = sys.executable
TMP = Path(os.environ.get("TEMP", "C:/")) / "pan-rework-repro"
TMP.mkdir(parents=True, exist_ok=True)
EVID = Path(WT) / "audit" / "terminal" / "implementation" / "backend" / "evidence" / "r2"
EVID.mkdir(parents=True, exist_ok=True)

from packages.core.terminal.backend import ConPtyBackend  # noqa: E402

BUDGET = 0.3
GATE_SLEEP = 0.9
PAYLOAD = 64 * 1024 * 1024


def main() -> int:
    phase = sys.argv[1] if len(sys.argv) > 1 else "pre"
    result: dict = {
        "repro": "write_budget_timer_gap",
        "phase": phase,
        "budget_s": BUDGET,
        "gate_sleep_s": GATE_SLEEP,
        "checks": {},
    }
    b = ConPtyBackend.spawn(
        [PY, "-c", "import time; time.sleep(120)"],
        cwd=str(TMP),
        rows=24,
        cols=80,
        write_budget=BUDGET,
        write_chunk=PAYLOAD,  # 单次 WriteFile 巨大 -> 会被 conhost 长期节流
    )
    state: dict = {"done": threading.Event(), "result": None, "gate_used": False}
    real_write_raw = b._spawn.write_raw

    def gated_write_raw(chunk: bytes) -> int:
        if not state["gate_used"]:
            state["gate_used"] = True
            time.sleep(GATE_SLEEP)  # 让预算 timer 在“无 I/O 挂起”时触发
        return real_write_raw(chunk)

    b._spawn.write_raw = gated_write_raw  # type: ignore[method-assign]

    def writer() -> None:
        t0 = time.perf_counter()
        try:
            written = b.write(b"x" * PAYLOAD)
            state["result"] = ("ok", written)
        except Exception as exc:  # noqa: BLE001
            state["result"] = (type(exc).__name__, getattr(exc, "written", None))
        state["seconds"] = round(time.perf_counter() - t0, 3)
        state["done"].set()

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    try:
        time.sleep(5.0)
        stuck = not state["done"].is_set()
        result["writer_still_blocked_after_5s"] = stuck
        result["checks"]["reproduced_beyond_budget_block" if phase == "pre" else "bounded_within_budget"] = (
            stuck if phase == "pre" else (not stuck)
        )
        result["gate_used"] = state["gate_used"]
        if not stuck:
            result["writer_result"] = state["result"]
            result["writer_seconds"] = state.get("seconds")
    finally:
        try:
            if b.alive():
                b.terminate(True)
            b.wait_dead(5.0)
            t0 = time.perf_counter()
            result["cleanup"] = {"close": b.close().get("closed"), "seconds": round(time.perf_counter() - t0, 3)}
        except Exception as exc:  # noqa: BLE001
            result["cleanup_error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
        got = state["done"].wait(5.0)
        result["writer_done_after_close"] = bool(got)
        result["writer_result_final"] = state["result"]
        result["all_checks_pass"] = all(result["checks"].values())
        out = EVID / f"repro-write-timer-gap-{phase}-{os.getpid()}.json"
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, default=str))
        print("written:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
