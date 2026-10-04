"""r2 门控的**先失败**证据：在 d48 固定副本上复现 R1/R2/R3+O1/O3/O4 缺陷。

- 基线树用 ``git archive d48f967e packages`` 解包到临时目录（**只读仓库、不改任何
  工作树**）；场景代码只调用 d48 已有 API（review neg02/neg03/neg01/neg04/neg06
  的机制等价复现，非引用其脚本）。
- 输出 JSON（stdout 与可选文件）：每个场景的 d48 实测行为与"r2 期望"对照。
- 本脚本**不是** OS 实测：进程内注入替身（injection_only=true），与
  ``tests/test_terminal_runner.py`` 的 r2 门控用例一一对应（后者在修复后的树上须全绿）。
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
BASELINE = "d48f967e"
HARD_TIMEOUT = float(os.environ.get("R2_PRE_FIX_TIMEOUT", "180"))


def _hard_watchdog() -> threading.Timer:
    timer = threading.Timer(HARD_TIMEOUT, lambda: os._exit(97))
    timer.daemon = True
    timer.start()
    return timer


def build_baseline_tree(target: Path) -> None:
    """把 d48 的 ``packages/`` 只读导出到 target（git archive；不触碰其他树）。"""
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


def _fake_runtime(contracts, *, close_behavior="ok", close_delay=0.0, detach_delay=0.0):
    class FakeRuntime:
        def __init__(self) -> None:
            self.state = contracts.RuntimeState.RUNNING
            self.identity = None
            self.rows, self.cols = 24, 80
            self.consumer_failed = False
            self.consumer_failure_count = 0
            self.log = _FakeLog()
            self.close_behavior = close_behavior
            self.close_delay = float(close_delay)
            self.detach_delay = float(detach_delay)
            self.close_calls: list[str] = []
            self.detach_calls = 0

        def poll_exit(self):
            return contracts.ExitInfo()

        def close(self, *, reason="explicit-close", **_kw):
            self.close_calls.append(str(reason))
            if self.close_delay:
                time.sleep(self.close_delay)
            if self.close_behavior == "error":
                raise RuntimeError("injected-close-failure")
            if self.close_behavior == "not-ok":
                self.state = contracts.RuntimeState.CLEANUP_FAILED
                return contracts.CleanupReport(
                    terminal_id="t", requested_reason=str(reason),
                    terminate_result=contracts.TerminateOutcome.TIMED_OUT,
                    state_after=contracts.RuntimeState.CLEANUP_FAILED,
                    owner_retained=True, error="injected",
                )
            self.state = contracts.RuntimeState.EXITED
            return contracts.CleanupReport(
                terminal_id="t", requested_reason=str(reason),
                terminate_result=contracts.TerminateOutcome.RETURNED,
                state_after=contracts.RuntimeState.EXITED,
            )

        def detach(self):
            if self.detach_delay:
                time.sleep(self.detach_delay)
            self.detach_calls += 1
            return contracts.DetachReport(
                terminal_id="t", detached_at=time.time(), durable_owner="runner"
            )

        def resize(self, rows, cols):
            return True

    return FakeRuntime()


def run_scenarios(runner_module, contracts) -> dict:
    results: dict = {}
    tmp = Path(tempfile.mkdtemp(prefix="pan-r2-prefix-"))

    def make(tid, **kwargs):
        secret = tmp / "secrets" / f"{tid}.secret"
        return runner_module.TerminalRunner(str(tid), secret, **kwargs)

    def wire(runner, runtime):
        runner._runtime = runtime
        runner._runner_state = "running"
        runner._lease_established = True
        runner._last_heartbeat = time.monotonic() - 10.0

    # ── R1-a：close 在途（1.0s 后成功）+ lease 过期 → d48 watchdog 永久跳过 ──
    runner = make("term_prefix_r1a", lease_grace_seconds=0.2, close_wait=0.3,
                  lease_cleanup_retries=3)
    runtime = _fake_runtime(contracts, close_delay=1.0)
    wire(runner, runtime)
    first = runner.close(reason="explicit-close")
    thread = threading.Thread(target=runner._watchdog_loop, daemon=True)
    thread.start()
    time.sleep(2.5)  # > worker 的 1.0s 完成时长
    watchdog_alive = thread.is_alive()
    runner._watchdog_stop.set()
    thread.join(timeout=1.0)
    results["r1a_close_inflight_then_lease_loss"] = {
        "first_close_status": first.get("status"),
        "watchdog_kept_looping_without_action": bool(watchdog_alive),
        "late_success_worker_finished": bool(
            runner._close_worker is not None and runner._close_worker.finished
        ),
        "late_success_consumed": bool(runner._shutdown_flag),
        "exit_code": runner._exit_code,
        "runner_state": runner._runner_state,
        "expected_r2": "watchdog 消费迟到成功 → shutdown=True, exit=0",
        "defect_present": bool(watchdog_alive and not runner._shutdown_flag
                               and runner._exit_code is None),
    }

    # ── R1-b：close 失败 + lease 过期 → d48 无自动重试/无自停 ──
    runner = make("term_prefix_r1b", lease_grace_seconds=0.2, close_wait=0.3,
                  lease_cleanup_retries=2)
    runtime = _fake_runtime(contracts, close_behavior="error")
    wire(runner, runtime)
    first = runner.close(reason="explicit-close")
    thread = threading.Thread(target=runner._watchdog_loop, daemon=True)
    thread.start()
    time.sleep(1.6)
    watchdog_alive = thread.is_alive()
    runner._watchdog_stop.set()
    thread.join(timeout=1.0)
    results["r1b_close_failed_then_lease_loss"] = {
        "first_close_status": first.get("status"),
        "watchdog_kept_looping_without_action": bool(watchdog_alive),
        "close_calls": list(runtime.close_calls),
        "exit_code": runner._exit_code,
        "expected_r2": "有界重试（不叠加）→ 耗尽 exit=3",
        "defect_present": bool(watchdog_alive and runner._exit_code is None),
    }

    # ── R2-a：expiry 先取得关闭权 → d48 detach 非显式拒绝语义 ──
    runner = make("term_prefix_r2a", lease_grace_seconds=0.2, close_wait=1.0,
                  durability_probe=lambda: runner_module.DurabilityCapability(True, False, "cap"))
    runtime = _fake_runtime(contracts, close_behavior="not-ok")
    wire(runner, runtime)
    thread = threading.Thread(target=runner._lease_expired_close, daemon=True)
    thread.start()
    time.sleep(0.3)
    refused = runner.detach()
    thread.join(timeout=6.0)
    results["r2a_expiry_first_detach"] = {
        "detach_status": refused.get("status"),
        "detach_ok": refused.get("ok"),
        "expected_r2": "detach-refused（lease-close-in-progress）",
        "defect_present": refused.get("status") != "detach-refused",
    }

    # ── R2-b：detach 先完成 → d48 迟到 expiry 仍关闭整树（铁证）──
    runner = make("term_prefix_r2b", lease_grace_seconds=0.2, close_wait=1.0,
                  durability_probe=lambda: runner_module.DurabilityCapability(True, False, "cap"))
    runtime = _fake_runtime(contracts, detach_delay=0.5)
    wire(runner, runtime)
    detach_result: dict = {}
    thread = threading.Thread(target=lambda: detach_result.update(runner.detach()), daemon=True)
    thread.start()
    time.sleep(0.15)
    runner._lease_expired_close()  # d48 无参数；越过 detached 门后仍会 close
    thread.join(timeout=5.0)
    results["r2b_detach_first_late_expiry"] = {
        "detach_status": detach_result.get("status"),
        "detached_flag": bool(runner._detached),
        "close_calls_despite_detached": list(runtime.close_calls),
        "expected_r2": "跳过（close_calls=[]，不杀 detached）",
        "defect_present": bool(runtime.close_calls and runner._detached),
    }

    # ── R2-c：迟到的续约不能作废旧 expiry（d48 无世代复核）──
    runner = make("term_prefix_r2c", lease_grace_seconds=0.2, close_wait=1.0)
    runtime = _fake_runtime(contracts)
    wire(runner, runtime)
    runner.owner_heartbeat("pan-new")   # 检测后发生的续约
    runner._lease_expired_close()
    results["r2c_late_heartbeat"] = {
        "close_calls_after_renewal": list(runtime.close_calls),
        "expected_r2": "跳过（旧 expiry 作废，close_calls=[]）",
        "defect_present": bool(runtime.close_calls),
    }

    # ── R3：启动失败清理双失败 —— d48 无 owner 保留/无报告 ──
    class FakeBackend:
        def __init__(self) -> None:
            self.closed = False

        def terminate(self, force):
            raise RuntimeError("injected-terminate-failure")

        def close(self):
            raise RuntimeError("injected-close-failure")

    runner = make("term_prefix_r3")
    returned = runner._abort_backend(FakeBackend())
    results["r3_startup_cleanup_owner"] = {
        "abort_returns_report": returned is not None,
        "retained_backend_attr": bool(getattr(runner, "_retained_backend", None)),
        "expected_r2": "返回报告 + retained=['backend'] + 可重试；退出码区分 4/6",
        "defect_present": returned is None
        and getattr(runner, "_retained_backend", None) is None,
    }

    # ── O1：pipe CloseReport(converged=False) 被丢弃 ──
    class FakeReport:
        converged = False
        detail = "injected-not-converged"

    class FakeServer:
        def __init__(self) -> None:
            self.calls = 0

        def close(self, *, timeout=None):
            self.calls += 1
            return FakeReport()

    runner = make("term_prefix_o1")
    server = FakeServer()
    runner._pipe_server = server
    pipe_result = runner._close_pipe_server()
    results["o1_pipe_close_report_discarded"] = {
        "close_return": None if pipe_result is None else type(pipe_result).__name__,
        "retained_pipe_attr": bool(getattr(runner, "_retained_pipe_server", None)),
        "attempts": server.calls,
        "expected_r2": "保存报告 + 保引用 + attempts=2 + 可重试",
        "defect_present": pipe_result is None
        and getattr(runner, "_retained_pipe_server", None) is None,
    }

    # ── O3：连接 join 为 N×2s（共享总 deadline 缺失）；线程对象不回收 ──
    runner = make("term_prefix_o3")
    gate = threading.Event()

    def _blocked():
        gate.wait(30.0)

    threads = [threading.Thread(target=_blocked, daemon=True) for _ in range(3)]
    for item in threads:
        item.start()
    runner._conn_threads.extend(threads)
    started = time.monotonic()
    runner._join_connections()
    elapsed = time.monotonic() - started
    reaped = getattr(runner, "_reap_connection_threads", None)
    gate.set()
    for item in threads:
        item.join(timeout=1.0)
    results["o3_join_serial_deadline"] = {
        "join_seconds_for_3_blocked": round(elapsed, 2),
        "expected_r2": "共享总 deadline（3s 内返回）+ 未收敛计数 + 线程回收方法",
        "defect_present": elapsed > 4.5 and reaped is None,
    }

    # ── O4：payload detail 折叠路径字符串截断 → 非法 JSON ──
    runner = make("term_prefix_o4")
    runner._runtime = _fake_runtime(contracts)
    payload = runner._payload("x", extra={"note": "N" * 20000})
    detail_text = payload.get("detail", "")
    try:
        json.loads(detail_text)
        parseable = True
    except Exception:
        parseable = False
    with_extra = runner._detail_with({"note": "X" * 20000})
    results["o4_detail_truncation"] = {
        "detail_len": len(detail_text),
        "detail_parseable": parseable,
        "detail_with_len": len(with_extra),
        "detail_with_within_budget": len(with_extra) <= runner_module._DETAIL_BUDGET,
        "expected_r2": "字段级缩减：两处均 ≤3800 且可解析",
        "defect_present": (not parseable) or len(with_extra) > runner_module._DETAIL_BUDGET,
    }

    return results


def main() -> int:
    watchdog = _hard_watchdog()
    target = Path(tempfile.mkdtemp(prefix="pan-r2-baseline-"))
    build_baseline_tree(target)
    sys.path.insert(0, str(target))
    import importlib

    runner_module = importlib.import_module("packages.core.terminal.runner")
    contracts = importlib.import_module("packages.core.terminal.contracts")
    file_path = getattr(runner_module, "__file__", "")
    results = {
        "baseline": BASELINE,
        "baseline_tree": str(target),
        "runner_module_file": file_path,
        "injection_only": True,
        "note": "d48 固定副本（git archive，只读）上的机制等价复现；非 OS 实测",
        "scenarios": run_scenarios(runner_module, contracts),
    }
    results["verdict"] = {
        "defects_present": {
            key: value["defect_present"] for key, value in results["scenarios"].items()
        },
        "all_defects_reproduced": all(
            value["defect_present"] for value in results["scenarios"].values()
        ),
    }
    text = json.dumps(results, ensure_ascii=False, indent=2, default=str)
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(text, encoding="utf-8")
    print(text)
    watchdog.cancel()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
