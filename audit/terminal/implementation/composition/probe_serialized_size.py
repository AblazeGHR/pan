"""组合相关量化探针：协议 A serialized 载荷尺寸（真实 HeadlessEmulator，无 runner）。

用途：为接口冻结提案 I2（snapshot 字段上限 vs `data_b64` 载体）提供真实引擎数字：
- 80x24 + scrollback=1000，喂入与组合 T1 同形状的流（3800 行文本）；
- 记录 serialized 屏幕的 UTF-8 字节数、cursor、rows/cols、feed 吞吐（ops/秒）；
- 输出 JSON 到 stdout（可选落盘）。只创建自建 sidecar 并显式 close()。

不触碰生产文件；非 Windows 直接拒绝（sidecar 依赖 Job）。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

_here = Path(__file__).resolve()
REPO_ROOT = _here
while not (REPO_ROOT / "packages/core/terminal/emulator.py").is_file():
    REPO_ROOT = REPO_ROOT.parent
sys.path.insert(0, str(REPO_ROOT))

from packages.core.terminal.emulator import HeadlessEmulator  # noqa: E402

LINE = b"PAN-COMP-PAD-%05d " + b"x" * 60 + b"\r\n"


def main() -> int:
    emu = HeadlessEmulator(cols=80, rows=24, scrollback=1000)
    try:
        stream = b"".join(LINE % index for index in range(3800))
        started = time.monotonic()
        seq = 0
        # 模拟真实 ConPTY 小读块（组合实测 ~135B/feed op）
        for offset in range(0, len(stream), 135):
            part = stream[offset : offset + 135]
            emu.feed_at(seq, part)
            seq += len(part)
        snap = emu.snapshot(timeout=120.0)
        elapsed = round(time.monotonic() - started, 3)
        serialized_bytes = len(snap.serialized_screen.encode("utf-8"))
        diag = emu.diagnostics()
        payload = {
            "stream_bytes": len(stream),
            "feed_ops": diag["counters"].get("feed_ops"),
            "chunk_bytes": 135,
            "feed_and_snapshot_seconds": elapsed,
            "ops_per_second": round((diag["counters"].get("feed_ops") or 0) / max(elapsed, 1e-9), 1),
            "serialized_utf8_bytes": serialized_bytes,
            "cursor": int(snap.cursor),
            "rows": snap.rows,
            "cols": snap.cols,
            "fidelity": snap.fidelity.value,
            "recovery": snap.recovery.value,
            "reasons": diag["reasons"][:8],
            "findings": {
                "wire_field_limit": "ipc 响应 snapshot/detail 字段 ≤4096 字符；serialized 必须走 data_b64（≤128KiB）",
                "serialized_fits_data_b64": serialized_bytes <= 128 * 1024,
                "engine_throughput_note": "applier 单飞：每 op 一次往返；小读块下吞吐受 op 速率限制（本探针量化）",
            },
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        if len(sys.argv) > 1:
            Path(sys.argv[1]).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    finally:
        report = emu.close(timeout=15.0)
        print(json.dumps({"close": report.as_dict()}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
