"""Repro (pre-fix): `_converge_writers` fail-open —— 锁被持有且未注册时返回 converged=True。

确定性门控：普通线程持有 backend._input_lock（未注册），非阻塞获取失败自证“锁确被持有”；
随后 `_converge_writers(0.3)` 在当前实现下返回 converged=True（input_lock_acquired=False）
→ 若 close 在此判定下继续，会与“即将写入 input_write 的写者”竞态。

只创建/终止自有子进程；子进程经 stop_backend（terminate→wait_dead→close）清理。
输出 JSON 写入 audit/terminal/implementation/backend/evidence/r2/。
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


def main() -> int:
    phase = sys.argv[1] if len(sys.argv) > 1 else "pre"
    result: dict = {"repro": "converge_writers_failopen", "phase": phase, "checks": {}}
    b = ConPtyBackend.spawn([PY, "-c", "import time; time.sleep(120)"], cwd=str(TMP), rows=24, cols=80)
    holder_started = threading.Event()
    holder_release = threading.Event()

    def _hold() -> None:
        with b._input_lock:
            holder_started.set()
            holder_release.wait(5.0)

    try:
        holder = threading.Thread(target=_hold, daemon=True)
        holder.start()
        assert holder_started.wait(5.0)
        got = b._input_lock.acquire(blocking=False)
        if got:
            b._input_lock.release()
        result["lock_demonstrably_held"] = not got
        result["checks"]["lock_held"] = not got

        converged, detail = b._converge_writers(0.3)
        result["converged"] = converged
        result["detail"] = detail
        fail_open = bool(converged is True and detail.get("input_lock_acquired") is False)
        result["fail_open_demonstrated"] = fail_open
        result["checks"]["fail_open_demonstrated" if phase == "pre" else "fail_open_removed"] = (
            fail_open if phase == "pre" else (not fail_open)
        )
        holder_release.set()
    finally:
        holder_release.set()
        try:
            if b.alive():
                b.terminate(True)
            b.wait_dead(5.0)
            result["cleanup"] = {"close": b.close().get("closed")}
        except Exception as exc:  # noqa: BLE001
            result["cleanup_error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
        result["all_checks_pass"] = all(result["checks"].values())
        out = EVID / f"repro-converge-failopen-{phase}-{os.getpid()}.json"
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, default=str))
        print("written:", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
