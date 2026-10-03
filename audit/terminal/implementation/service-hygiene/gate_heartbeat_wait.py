"""确定性门控（F12 卫生）：心跳"有界等待"语义回归。

只注入原语（可编程假 service），**不派生真实进程**、不碰 sidecar、不用 8768、
不监听端口。回归对象是**实际随测试发布**的私有 helper
``tests/test_terminal_service.py::_await_heartbeat_advance``（以文件路径加载，
不依赖 ``tests`` 是 Python 包），因此钉的是真身而非副本。

覆盖三条语义：

  G1  心跳延迟后推进  → 在**有界预算**内正常返回（耗时 ≈ 延迟，而非固定大 sleep）
  G2  心跳持续不推进  → 预算耗尽抛 ``AssertionError``（**显式超时失败**，带诊断字段）
  G3  业务窗口见证    → 等待窗口内 ``on_tick`` 确被调用（业务下发计数 > 0）

诊断 JSON 写 ``evidence/gate_heartbeat_wait.json``。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[3]  # audit/terminal/implementation/service-hygiene -> repo root
TEST_MODULE = REPO_ROOT / "tests" / "test_terminal_service.py"
EVIDENCE_DIR = HERE / "evidence"


def _load_helper() -> Callable[..., tuple[int, int]]:
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    spec = importlib.util.spec_from_file_location(
        "pan_terminal_service_test_undertest", TEST_MODULE
    )
    assert spec and spec.loader, f"无法加载被测测试模块：{TEST_MODULE}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module._await_heartbeat_advance


class _ProgrammableBeats:
    """可编程 beats 源：``delay=None`` ⇒ 永不推进；否则达到 ``delay`` 后 beats=1。"""

    def __init__(self, *, terminal_id: str = "term_gate", delay: float | None) -> None:
        self.terminal_id = terminal_id
        self._delay = delay
        self._start = time.monotonic()
        self.inputs = 0

    def input(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        self.inputs += 1
        return {"accepted": True}

    def describe(self) -> dict[str, Any]:
        beats = 0
        if self._delay is not None and (time.monotonic() - self._start) >= self._delay:
            beats = 1
        return {
            "heartbeats": {
                self.terminal_id: {"beats": beats, "lost": False, "running": True}
            }
        }


def _g1_delayed_advance(helper: Callable[..., tuple[int, int]]) -> dict[str, Any]:
    service = _ProgrammableBeats(delay=0.30)
    started = time.monotonic()
    beats, ticks = helper(
        service, service.terminal_id, 0, budget=5.0, interval=0.05,
        on_tick=lambda _t: service.input(b"x"),
    )
    elapsed = time.monotonic() - started
    ok = beats == 1 and 0.25 <= elapsed < 5.0 and service.inputs >= 1
    return {
        "gate": "G1_delayed_advance",
        "ok": ok,
        "beats": beats,
        "ticks": ticks,
        "elapsed_seconds": round(elapsed, 3),
        "business_inputs": service.inputs,
        "detail": "延迟推进在有界预算内返回；耗时≈延迟，非固定 sleep",
    }


def _g2_never_advances_timeout(helper: Callable[..., tuple[int, int]]) -> dict[str, Any]:
    service = _ProgrammableBeats(delay=None)
    started = time.monotonic()
    raised: str | None = None
    message = ""
    try:
        helper(service, service.terminal_id, 0, budget=0.40, interval=0.05)
    except AssertionError as exc:  # noqa: PERF203 - 期望路径
        raised = "AssertionError"
        message = str(exc)
    elapsed = time.monotonic() - started
    has_before = "before=" in message
    ok = raised == "AssertionError" and elapsed >= 0.30 and has_before
    return {
        "gate": "G2_never_advances_timeout",
        "ok": ok,
        "raised": raised,
        "elapsed_seconds": round(elapsed, 3),
        "diagnostic_has_before_field": has_before,
        "diagnostic_sample": message[:160],
        "detail": "持续不推进必须以显式超时失败收场（不放宽成无条件 True）",
    }


def _g3_business_window_witness(helper: Callable[..., tuple[int, int]]) -> dict[str, Any]:
    service = _ProgrammableBeats(delay=0.20)
    seen: list[int] = []

    def _tick(tick: int) -> None:
        service.input(b"x")
        seen.append(tick)

    beats, _ticks = helper(
        service, service.terminal_id, 0, budget=5.0, interval=0.05, on_tick=_tick
    )
    ok = len(seen) >= 1 and service.inputs >= 1 and beats > 0
    return {
        "gate": "G3_business_window_witness",
        "ok": ok,
        "on_tick_calls": len(seen),
        "business_inputs": service.inputs,
        "detail": "窗口内业务确被下发 ⇒ 可作'业务进行期间推进'的窗口见证",
    }


def main() -> int:
    helper = _load_helper()
    helper_bytes = TEST_MODULE.read_bytes()
    results = [
        _g1_delayed_advance(helper),
        _g2_never_advances_timeout(helper),
        _g3_business_window_witness(helper),
    ]
    summary = {
        "gate": "service-hygiene F12 心跳有界等待语义回归",
        "helper_source": "tests/test_terminal_service.py::_await_heartbeat_advance",
        "helper_source_sha256_worktree": hashlib.sha256(helper_bytes).hexdigest(),
        "results": results,
        "passed": sum(1 for r in results if r["ok"]),
        "failed": sum(1 for r in results if not r["ok"]),
    }
    for r in results:
        print(f"[{'PASS' if r['ok'] else 'FAIL'}] {r['gate']}: {r['detail']}")
        payload = {k: v for k, v in r.items() if k not in ("gate", "ok", "detail")}
        print("       " + json.dumps(payload, ensure_ascii=False))
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    (EVIDENCE_DIR / "gate_heartbeat_wait.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print()
    print(json.dumps({"passed": summary["passed"], "failed": summary["failed"]}, ensure_ascii=False))
    print(f"gate rc={1 if summary['failed'] else 0}")
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
