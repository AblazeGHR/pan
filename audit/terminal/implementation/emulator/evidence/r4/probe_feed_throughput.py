"""r4 F2 吞吐探针：135B/op（真实 ConPTY 小读块形状）的提交/排空/积压/barrier 量化。

对齐组合验证（terminal-composition-20261003）的 probe 形状：
- 80x24 + scrollback=1000；固定输入 3800 行（307,800 B）；135 B/op 小块提交；
- 记录：提交耗时（feed_at 循环，应快速不阻塞）、积压快照（applied 落后）、
  排空/barrier 耗时（最终 snapshot）、积压期短超时 barrier 行为、ops/s 与 KB/s；
- 机器环境：platform / CPU / python / node 版本；
- 只创建自建 sidecar 并显式 close；非 Windows 直接拒绝（依赖 Job）。

用法（从仓库根）：
    E:/software/miniforge/python.exe audit/terminal/implementation/emulator/evidence/r4/probe_feed_throughput.py [out.json]
"""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve()
REPO_ROOT = _HERE
while not (REPO_ROOT / "packages" / "core" / "terminal" / "emulator.py").is_file():
    REPO_ROOT = REPO_ROOT.parent
sys.path.insert(0, str(REPO_ROOT))

from packages.core.terminal.emulator import HeadlessEmulator  # noqa: E402

LINE = b"PAN-COMP-PAD-%05d " + b"x" * 60 + b"\r\n"
CHUNK = 135  # 组合实测真实 ConPTY 小读块 ~135 B/feed op


def _cpu_name() -> str:
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "(Get-CimInstance Win32_Processor | Select-Object -First 1 -ExpandProperty Name)"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        )
        return (out.stdout or "").strip()[:120]
    except Exception:  # noqa: BLE001
        return "unknown"


def main() -> int:
    emu = HeadlessEmulator(cols=80, rows=24, scrollback=1000)
    try:
        stream = b"".join(LINE % index for index in range(3800))
        ops_total = (len(stream) + CHUNK - 1) // CHUNK

        t0 = time.monotonic()
        seq = 0
        for offset in range(0, len(stream), CHUNK):
            part = stream[offset : offset + CHUNK]
            emu.feed_at(seq, part)
            seq += len(part)
        submit_sec = round(time.monotonic() - t0, 3)

        diag_submit = emu.diagnostics()
        applied_at_submit = int(diag_submit["applied_cursor"])
        backlog_bytes = len(stream) - applied_at_submit
        # 积压 op 数 ≈ 已提交 op 数 - 已应用字节对应 op 数（近似：按 135B 块）
        backlog_ops = max(0, ops_total - (applied_at_submit + CHUNK - 1) // CHUNK)

        # 积压期短超时 barrier（组合报告：积压期 barrier 会超时 → degraded/unavailable）
        t1 = time.monotonic()
        probe_snap = emu.snapshot(timeout=0.5)
        backlog_barrier_sec = round(time.monotonic() - t1, 3)

        # 排空 barrier（等全部应用）
        t2 = time.monotonic()
        snap = emu.snapshot(timeout=600.0)
        drain_sec = round(time.monotonic() - t2, 3)

        diag = emu.diagnostics()
        applied_final = int(diag["applied_cursor"])
        total_sec = round(submit_sec + drain_sec, 3)
        payload = {
            "stream_bytes": len(stream),
            "feed_ops": int(diag["counters"].get("feed_ops", 0)),
            "chunk_bytes": CHUNK,
            "submit_seconds": submit_sec,
            "backlog_at_submit": {
                "applied_cursor": applied_at_submit,
                "backlog_bytes": backlog_bytes,
                "backlog_ops_est": backlog_ops,
                "queue_ops": diag_submit.get("queue_ops"),
                "queue_bytes": diag_submit.get("queue_bytes"),
            },
            "backlog_barrier": {
                "timeout_sec": 0.5,
                "elapsed_sec": backlog_barrier_sec,
                "fidelity": probe_snap.fidelity.value,
                "recovery": probe_snap.recovery.value,
            },
            "drain_seconds": drain_sec,
            "total_feed_and_drain_seconds": total_sec,
            "ops_per_second": round(ops_total / max(total_sec, 1e-9), 1),
            "kb_per_second": round((len(stream) / 1024.0) / max(total_sec, 1e-9), 2),
            "applied_final": applied_final,
            "applied_equals_total": applied_final == len(stream),
            "fidelity_final": snap.fidelity.value,
            "recovery_final": snap.recovery.value,
            "counters": {
                k: diag["counters"].get(k)
                for k in ("feed_ops", "feed_batches", "feed_batched_ops", "max_feed_batch_bytes")
            },
            "machine": {
                "platform": platform.platform(),
                "processor": _cpu_name(),
                "python": sys.version.split()[0],
                "python_binary": sys.executable,
                "node": (shutil.which("node") or "missing"),
            },
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        if len(sys.argv) > 1:
            Path(sys.argv[1]).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    finally:
        report = emu.close(timeout=30.0)
        print(json.dumps({"close": report.as_dict()}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
