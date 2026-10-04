"""r3 窄修（N1..N4）的**先失败**证据：在 a704 固定副本上确定性复现。

- 基线树用 ``git archive a704abbb packages`` 解包到临时目录（**只读仓库、不改任何
  工作树**）；场景只使用 a704 已有 API（不依赖放大注入的概率命中）。
- N1 复现用**状态级线性化点**（关闭权已取得但 ``_closing`` 未置位的状态——即 g11
  确认的指令级窗口的可判定状态），不是"放大后碰运气"。
- 输出 JSON（stdout 与可选文件）；与修复后 ``tests/test_terminal_runner.py`` 的
  r3 门控用例一一对应（后者须全绿）。
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve()
REPO_ROOT = HERE
while not (REPO_ROOT / ".git").exists():
    REPO_ROOT = REPO_ROOT.parent
BASELINE = "a704abbb"
HARD_TIMEOUT = float(os.environ.get("R3_PRE_FIX_TIMEOUT", "180"))


def _hard_watchdog() -> threading.Timer:
    timer = threading.Timer(HARD_TIMEOUT, lambda: os._exit(97))
    timer.daemon = True
    timer.start()
    return timer


def build_baseline_tree(target: Path) -> None:
    archive = subprocess.run(
        ["git", "archive", "--format=tar", BASELINE, "packages"],
        cwd=str(REPO_ROOT), capture_output=True, timeout=120,
    )
    if archive.returncode != 0:
        raise RuntimeError(f"git archive failed: {archive.stderr[:200]!r}")
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as bundle:
        bundle.extractall(target)


class _FakeLog:
    total_bytes = 0
    first_retained_seq = 0


def _fake_runtime(contracts):
    class FakeRuntime:
        def __init__(self) -> None:
            self.state = contracts.RuntimeState.RUNNING
            self.identity = None
            self.rows, self.cols = 24, 80
            self.consumer_failed = False
            self.consumer_failure_count = 0
            self.log = _FakeLog()
            self.close_calls: list[str] = []

        def poll_exit(self):
            return contracts.ExitInfo()

        def close(self, *, reason="explicit-close", **_kw):
            self.close_calls.append(str(reason))
            self.state = contracts.RuntimeState.EXITED
            return contracts.CleanupReport(
                terminal_id="t", requested_reason=str(reason),
                terminate_result=contracts.TerminateOutcome.RETURNED,
                state_after=contracts.RuntimeState.EXITED,
            )

        def detach(self):
            raise AssertionError("unused")

        def resize(self, rows, cols):
            return True

    return FakeRuntime()


def run_scenarios(runner_module, contracts) -> dict:
    results: dict = {}
    tmp = Path(tempfile.mkdtemp(prefix="pan-r3-prefix-"))

    def make(tid, **kwargs):
        secret = tmp / "secrets" / f"{tid}.secret"
        return runner_module.TerminalRunner(str(tid), secret, **kwargs)

    def wire(runner, runtime):
        runner._runtime = runtime
        runner._runner_state = "running"
        runner._lease_established = True
        runner._last_heartbeat = time.monotonic() - 10.0

    # ── N1-a（缺陷面）：关闭权已取得（_closing 未置位）→ a704 hb 仍返回 ok ──
    runner = make("term_prefix_n1a", lease_grace_seconds=0.2, close_wait=1.0)
    runtime = _fake_runtime(contracts)
    wire(runner, runtime)
    runner._expiry_in_progress = True  # = expiry 已在 lease/lifecycle 门内取得关闭权
    hb_before = runner._last_heartbeat
    hb = runner.owner_heartbeat("late-owner")
    results["n1a_hb_after_close_right_acquired"] = {
        "hb_status": hb.get("status"),
        "hb_ok": hb.get("ok"),
        "lease_renewed_by_hb": runner._last_heartbeat != hb_before,
        "expected_r3": "closing（不假 ok、不续约）",
        "defect_present": hb.get("status") == "ok",
    }

    # ── N1-b（另一顺序，a704 已正确）：hb 先 → 旧 expiry 作废 ──
    runner = make("term_prefix_n1b", lease_grace_seconds=0.2, close_wait=1.0)
    runtime = _fake_runtime(contracts)
    wire(runner, runtime)
    epoch = runner._lease_epoch
    hb2 = runner.owner_heartbeat("new-owner")
    stale = runner.close(
        reason="lease-expired", source="lease",
        lease_epoch=epoch, lease_established=True,
    )
    results["n1b_hb_first_voids_expiry"] = {
        "hb_status": hb2.get("status"),
        "stale_expiry_status": stale.get("status"),
        "close_calls": list(runtime.close_calls),
        "expected_r3": "skipped（两版本都应如此）",
        "defect_present": stale.get("status") != "lease-skipped-renewed",
    }

    # ── N2（缺陷面）：非法 terminal_id 的 status 路径逃逸 ──
    root = tmp / "n2-root"
    (root / "secrets").mkdir(parents=True)
    illegal = runner_module.TerminalRunner(
        "term_x/../../escape", root / "secrets" / "term_x.secret"
    )
    illegal._write_status("bootstrap-failed", exit_code=4)
    escaped = root / "escape.json"
    results["n2_illegal_id_status_escape"] = {
        "escaped_file_written": escaped.is_file(),
        "escaped_path": str(escaped),
        "canonical_status_dir_exists": (root / "runner-status").exists(),
        "expected_r3": "零写（只 note status-path-rejected）",
        "defect_present": escaped.is_file(),
    }

    # ── N3（缺陷面）：status 无 runner 身份（raw FILETIME）绑定 ──
    runner = make("term_prefix_n3")
    runner._write_status("bootstrap-failed", exit_code=4)
    status_path = root / "runner-status" / "term_prefix_n3.json"
    # a704 的 _status_dir 基于自身 secret 路径；直接用 runner 的真实路径读取
    status_path = getattr(runner, "_status_dir", root / "runner-status") / "term_prefix_n3.json"
    data = json.loads(status_path.read_text(encoding="utf-8")) if status_path.is_file() else {}
    results["n3_status_identity_binding"] = {
        "has_runner_identity_block": "runner_identity" in data,
        "has_raw_filetime": bool((data.get("runner_identity") or {}).get("process_created_at_filetime")),
        "has_prior_record_flag": "prior_record_identity_mismatch" in data,
        "expected_r3": "runner_identity（pid + raw FILETIME 字符串）+ authority + prior_record 辨认",
        "defect_present": "runner_identity" not in data,
    }

    # ── N4（缺陷面）：finalize 预算不含 lifecycle 锁等待 ──
    old_budget = runner_module._FINALIZE_BUDGET_SECONDS
    runner_module._FINALIZE_BUDGET_SECONDS = 0.6
    runner = make("term_prefix_n4", close_wait=1.0)
    runtime = _fake_runtime(contracts)
    runner._runtime = runtime
    runner._runner_state = "running"
    holding = threading.Event()

    def hold_lifecycle():
        runner._lifecycle_lock.acquire()
        holding.set()
        try:
            time.sleep(1.5)
        finally:
            runner._lifecycle_lock.release()

    holder = threading.Thread(target=hold_lifecycle, daemon=True)
    holder.start()
    holding.wait(2.0)
    time.sleep(0.05)
    started = time.monotonic()
    runner._finalize()
    elapsed = round(time.monotonic() - started, 3)
    holder.join(timeout=5.0)
    results["n4_finalize_budget_excludes_lock_wait"] = {
        "budget_seconds": 0.6,
        "lock_held_seconds": 1.5,
        "finalize_seconds": elapsed,
        "close_calls": list(runtime.close_calls),
        "exit_code": runner._exit_code,
        "expected_r3": "≤预算内返回（锁忙→非零、不假 success、不关句柄）",
        "defect_present": elapsed > 1.0,
    }
    runner_module._FINALIZE_BUDGET_SECONDS = old_budget

    return results


def main() -> int:
    watchdog = _hard_watchdog()
    target = Path(tempfile.mkdtemp(prefix="pan-r3-baseline-"))
    build_baseline_tree(target)
    sys.path.insert(0, str(target))
    import importlib

    runner_module = importlib.import_module("packages.core.terminal.runner")
    contracts = importlib.import_module("packages.core.terminal.contracts")
    scenarios = run_scenarios(runner_module, contracts)
    results = {
        "baseline": BASELINE,
        "baseline_tree": str(target),
        "runner_module_file": getattr(runner_module, "__file__", ""),
        "injection_only": True,
        "note": "a704 固定副本（git archive，只读）上的确定性门控复现；非 OS 实测",
        "scenarios": scenarios,
        "verdict": {
            "defects_present": {
                key: value["defect_present"] for key, value in scenarios.items()
            },
            "n1_defect_reproduced": scenarios["n1a_hb_after_close_right_acquired"]["defect_present"],
            "n2_defect_reproduced": scenarios["n2_illegal_id_status_escape"]["defect_present"],
            "n3_defect_reproduced": scenarios["n3_status_identity_binding"]["defect_present"],
            "n4_defect_reproduced": scenarios["n4_finalize_budget_excludes_lock_wait"]["defect_present"],
        },
    }
    text = json.dumps(results, ensure_ascii=False, indent=2, default=str)
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(text, encoding="utf-8")
    print(text)
    watchdog.cancel()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
