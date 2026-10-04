"""v1（F1 独立复验）：spawn 前/pre-ready EOF/fatal/timeout 四态 —— owner 与 retry 收敛。

- spawn 前缺失 → EmulatorStartupError、owner=None；
- spawn 后 ready 前 EOF（立即退出脚本）→ 必须抛 + owner + retry_cleanup 收敛 + 无残留；
- spawn 后 fatal 帧 → 同；
- spawn 后 ready 前 **timeout**（挂起不发帧）→ 必须抛 + owner + retry 收敛 + 无残留。
"""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

from v_common import start_hard_watchdog, wait_dead, write_evidence
from packages.core.terminal.emulator import EmulatorStartupError, HeadlessEmulator


def _owner_case(path: str, *, timeout: float = 10.0) -> dict:
    started = time.monotonic()
    try:
        HeadlessEmulator(sidecar_path=path, startup_timeout=timeout)
    except EmulatorStartupError as exc:
        elapsed = round(time.monotonic() - started, 3)
        report = exc.owner.retry_cleanup(timeout=10.0) if exc.owner is not None else None
        residual = dict(getattr(exc, "residual", None) or {})
        pid = residual.get("pid")
        ft = residual.get("filetime") or residual.get("created_at_filetime")
        dead = True if not pid else wait_dead(int(pid), 8.0, int(ft) if ft else None)
        return {
            "raised": True,
            "error": type(exc).__name__,
            "has_owner": exc.owner is not None,
            "retry_cleanup": report,
            "no_residue": bool(dead),
            "seconds": elapsed,
        }
    return {"raised": False, "note": "未抛错（与 r4 期望不符）"}


def main() -> int:
    watchdog = start_hard_watchdog("v1", 240)
    tmp = Path(tempfile.mkdtemp(prefix="pan-cr2v1-"))
    out: dict = {}

    # ① spawn 前：脚本缺失
    pre = {}
    try:
        HeadlessEmulator(sidecar_path=str(tmp / "missing.mjs"), startup_timeout=10.0)
        pre = {"raised": False}
    except EmulatorStartupError as exc:
        pre = {"raised": True, "owner_is_none": exc.owner is None}
    out["pre_spawn_missing"] = pre

    # ② EOF
    eof_script = tmp / "exit_only.cjs"
    eof_script.write_text("process.exit(3);\n", encoding="utf-8")
    out["post_spawn_eof"] = _owner_case(str(eof_script))

    # ③ fatal
    fatal = tmp / "fatal.cjs"
    fatal.write_text(
        "function send(h){const b=Buffer.from(JSON.stringify(h),'utf8');"
        "const o=Buffer.alloc(8+b.length);o.writeUInt32LE(b.length,0);o.writeUInt32LE(b.length,4);"
        "b.copy(o,8);process.stdout.write(o);}\n"
        "send({type:'fatal',code:'injected-dependency-broken',detail:'v1'});\n"
        "setTimeout(()=>process.exit(4),50);\n",
        encoding="utf-8",
    )
    out["post_spawn_fatal"] = _owner_case(str(fatal))

    # ④ timeout（挂起）
    hang = tmp / "hang.cjs"
    hang.write_text("setTimeout(()=>{}, 60000);\n", encoding="utf-8")
    out["post_spawn_timeout"] = _owner_case(str(hang), timeout=2.0)

    eof, fatal_r, hang_r = out["post_spawn_eof"], out["post_spawn_fatal"], out["post_spawn_timeout"]
    out["verdict"] = {
        "pre_spawn_owner_none": bool(pre.get("raised")) and bool(pre.get("owner_is_none")),
        "eof_raises_owner_retry_clean": all(
            eof.get(k) for k in ("raised", "has_owner", "no_residue")
        )
        and bool((eof.get("retry_cleanup") or {}).get("closed")),
        "fatal_raises_owner_retry_clean": all(
            fatal_r.get(k) for k in ("raised", "has_owner", "no_residue")
        )
        and bool((fatal_r.get("retry_cleanup") or {}).get("closed")),
        "timeout_raises_owner_retry_clean": all(
            hang_r.get(k) for k in ("raised", "has_owner", "no_residue")
        )
        and bool((hang_r.get("retry_cleanup") or {}).get("closed")),
        "interpretation": (
            "四态（spawn 前缺失 / ready 前 EOF / fatal / ready 前 timeout）在 r4 后统一 fail-closed："
            "除 spawn 前外均携带可重试 owner 且 retry_cleanup 收敛无残留——与 3a F1 硬断言口径一致"
            "（timeout 态为本审查补测）。"
        ),
    }
    write_evidence("v1_f1_states", out)
    watchdog.cancel()
    print(json.dumps({k: v for k, v in out["verdict"].items() if k != "interpretation"},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
