"""Pan Terminal Launcher：独立 runner 进程的生产宿主（引擎归属与失败收尾）。

同一进程内：
1. 创建并**拥有**真实 :class:`HeadlessEmulator`（node sidecar；spawn 前
   ``EmulatorStartupError.owner is None``，spawn 后失败必须携带可重试 owner）；
2. 借入 :class:`TerminalRunner`（``emulator=`` 注入；``backend.probe`` 为默认身份
   核验；输出经 ``RunnerEmulatorBridge`` 的 ``feed_at`` 桥接）——runner **不**隐式
   关闭借入对象；
3. ``runner.run()``（terminal_id/secret/DPAPI/HMAC/原子 spawn 四门与 runner 入口
   一致；argv 只有 id 与 secret 路径，**无 token**）；
4. ``finally``：有界收尾引擎——单飞 worker（同一时刻至多一个 close 调用在途）、
   同 owner（同一引擎对象，close 幂等）重试、失败保引用；预算耗尽非零退出并
   如实记录（不把 Job 内核退出当清理已证明）；
5. 两种结局显式：清理确证收敛 → 透传 runner 退出码；未收敛 → 退出码 6 +
   脱敏持久状态（真实 pid/raw FILETIME 字符串、保留资源、失败原因类型、尝试次数）。

入口：

    python -m packages.core.terminal.launcher \
        --terminal-id <term_...> --secret-file <root>/secrets/<id>.secret

接口 / 退出码 / 预算策略见
``docs/design/PAN_TERMINAL_LAUNCHER_INTERFACES_20261003.md``。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import secret_store, win_pipe
from .emulator import EmulatorStartupError, HeadlessEmulator
from .runner import TerminalRunner

__all__ = [
    "DEFAULT_ROWS",
    "DEFAULT_COLS",
    "LAUNCHER_EXIT_INTERNAL",
    "LAUNCHER_EXIT_CLEANUP_UNPROVEN",
    "LAUNCHER_EXIT_ENGINE_STARTUP_FAILED",
    "ENGINE_TOTAL_BUDGET_SECONDS",
    "ENGINE_CLOSE_ATTEMPT_BUDGET_SECONDS",
    "ENGINE_CLEANUP_RETRY_INTERVAL_SECONDS",
    "TerminalLauncher",
    "main",
]

DEFAULT_ROWS = 24
DEFAULT_COLS = 80

#: launcher 兜底（runner.run 抛错 / 自身内部错误；与 runner 的 INTERNAL 同值）。
LAUNCHER_EXIT_INTERNAL = 5
#: 清理未证明（引擎收尾未收敛；与 runner 的 CLEANUP_UNPROVEN 同值）。
LAUNCHER_EXIT_CLEANUP_UNPROVEN = 6
#: 引擎启动失败且 owner 清理已确证收敛（launcher 新增）。
LAUNCHER_EXIT_ENGINE_STARTUP_FAILED = 8

#: 引擎收尾总预算（从收尾入口计时，含全部等待/重试/worker 等待）。
ENGINE_TOTAL_BUDGET_SECONDS = 20.0
#: 单次 ``close()`` 调用预算（含 emulator 内部等锁时间）。
ENGINE_CLOSE_ATTEMPT_BUDGET_SECONDS = 8.0
#: 重试间隔（包含在总预算内）。
ENGINE_CLEANUP_RETRY_INTERVAL_SECONDS = 0.5

_STATUS_DIRNAME = "launcher-status"

#: 静态退出原因（stderr 公告用；细节看状态文件）。
_EXIT_REASONS: dict[int, str] = {
    0: "exited",
    2: "usage",
    3: "cleanup-failed",
    4: "bootstrap-failed",
    LAUNCHER_EXIT_INTERNAL: "internal",
    LAUNCHER_EXIT_CLEANUP_UNPROVEN: "cleanup-unproven",
    7: "accept-failed",
    LAUNCHER_EXIT_ENGINE_STARTUP_FAILED: "engine-startup-failed",
}

_AUTHORITY_NOTE = (
    "launcher-status 是事实记录，不是存活权威；对象引用不可跨进程重试；"
    "Job 内核退出（进程终止关闭 guard）不代表清理已证明"
)


def _reported_closed(result: Any) -> bool:
    """收敛判定：``EmulatorCloseReport.closed`` 或 owner 报告的 ``closed``。"""
    if result is None:
        return False
    closed = getattr(result, "closed", None)
    if closed is None and isinstance(result, Mapping):
        closed = result.get("closed")
    return bool(closed)


class _EngineCloseWorker:
    """单飞 close worker：同一时刻至多一个 ``close()`` 在途。

    上一次调用未结束前再次 ``invoke`` **不会**叠加新调用（复用同一 worker 等待）；
    超时只是"本次等待结束"，绝不对引擎资源做任何强制动作（不裸关）。
    """

    def __init__(self, emulator: Any) -> None:
        self._emulator = emulator
        self._gate = threading.Lock()
        self._thread: threading.Thread | None = None
        self._done = threading.Event()
        self._result: Any = None
        self._error_type: str | None = None
        self.invocations = 0

    @property
    def in_flight(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def invoke(self, budget: float) -> tuple[bool, Any, str | None]:
        """等待本次/同一在途调用至多 ``budget``；返回 ``(finished, result, error_type)``。"""
        budget = max(0.0, float(budget))
        with self._gate:
            if not self.in_flight:
                self._done = threading.Event()
                self._result = None
                self._error_type = None
                self.invocations += 1
                self._thread = threading.Thread(
                    target=self._run, args=(budget,), daemon=True, name="launcher-engine-close"
                )
                self._thread.start()
        if self._done.wait(timeout=budget):
            return True, self._result, self._error_type
        return False, None, None

    def _run(self, budget: float) -> None:
        try:
            self._result = self._emulator.close(timeout=budget)
        except Exception as exc:  # noqa: BLE001 - 失败只记类型名（文本可能含敏感内容）
            self._error_type = type(exc).__name__
        finally:
            self._done.set()


class TerminalLauncher:
    """生产宿主：拥有引擎、借入 runner、负责收尾与退出码合并。"""

    def __init__(
        self,
        terminal_id: str,
        secret_file: str | Path,
        *,
        rows: int = DEFAULT_ROWS,
        cols: int = DEFAULT_COLS,
        engine_total_budget: float = ENGINE_TOTAL_BUDGET_SECONDS,
        engine_close_attempt_budget: float = ENGINE_CLOSE_ATTEMPT_BUDGET_SECONDS,
        engine_cleanup_retry_interval: float = ENGINE_CLEANUP_RETRY_INTERVAL_SECONDS,
        status_dir: str | Path | None = None,
        emulator_factory: Callable[[], Any] | None = None,
        runner_factory: Callable[[Any], Any] | None = None,
    ) -> None:
        self.terminal_id = str(terminal_id)
        self._secret_file = Path(secret_file)
        self._rows = int(rows)
        self._cols = int(cols)
        self._engine_total_budget = max(0.0, float(engine_total_budget))
        self._engine_attempt_budget = float(engine_close_attempt_budget)
        self._engine_retry_interval = max(0.0, float(engine_cleanup_retry_interval))
        self._status_dir = (
            Path(status_dir)
            if status_dir is not None
            else self._secret_file.parent.parent / _STATUS_DIRNAME
        )
        #: 仅测试/诊断注入（默认 = 真实 HeadlessEmulator / TerminalRunner）。
        self._emulator_factory = emulator_factory
        self._runner_factory = runner_factory

        self._engine_created = False
        self._engine_cleanup: dict[str, Any] = {
            "converged": False,
            "attempts": 0,
            "last_error_type": None,
            "retained": [],
            "in_flight": False,
            "seconds": 0.0,
            "outcome": "not-started",
        }
        self._startup_report: dict[str, Any] | None = None
        self._exit_reason: str | None = None
        self._events: list[tuple[str, str | None]] = []
        self._status_lock = threading.Lock()
        self._identity = self._probe_identity()

    # ------------------------------------------------------------- 只读视图
    @property
    def engine_cleanup(self) -> dict[str, Any]:
        return dict(self._engine_cleanup)

    @property
    def startup_report(self) -> dict[str, Any] | None:
        return dict(self._startup_report) if self._startup_report else None

    @property
    def events(self) -> tuple[tuple[str, str | None], ...]:
        return tuple(self._events)

    @property
    def exit_reason(self) -> str | None:
        return self._exit_reason

    def _note(self, event: str, detail: str | None = None) -> None:
        self._events.append((str(event), str(detail) if detail is not None else None))

    def _probe_identity(self) -> dict[str, Any]:
        """真实自身身份（pid + raw FILETIME 十进制字符串）；失败明确 unknown。"""
        try:
            own = win_pipe.current_process_identity()
            filetime = own.created_at_filetime
            return {
                "pid": int(own.pid),
                "process_created_at_filetime": (
                    str(int(filetime)) if filetime is not None else None
                ),
            }
        except Exception as exc:  # noqa: BLE001 - 身份探测失败不造值
            self._note("launcher-identity-probe-failed", type(exc).__name__)
            return {"pid": None, "process_created_at_filetime": None}

    # ---------------------------------------------------------------- 主流程
    def run(self) -> int:
        """创建引擎 → 借入 runner → run → finally 收尾 → 合并退出码 + 状态落盘。"""
        try:
            engine = self._build_engine()
        except EmulatorStartupError as exc:
            return self._engine_startup_failed(exc)
        except Exception as exc:  # noqa: BLE001 - 工厂/构造的非契约异常兜底
            self._note("engine-factory-error", type(exc).__name__)
            self._engine_cleanup = {
                "converged": True,  # 未创建任何引擎资源：无清理缺口（对象未产生）
                "attempts": 0,
                "last_error_type": type(exc).__name__,
                "retained": [],
                "in_flight": False,
                "seconds": 0.0,
                "outcome": "not-created",
            }
            self._exit_reason = "internal"
            self._write_status("internal", exit_code=LAUNCHER_EXIT_INTERNAL, reason="internal")
            self._announce(LAUNCHER_EXIT_INTERNAL)
            return LAUNCHER_EXIT_INTERNAL
        self._engine_created = True

        runner_error: str | None = None
        code = LAUNCHER_EXIT_INTERNAL
        try:
            runner = self._build_runner(engine)
        except Exception as exc:  # noqa: BLE001 - 构造失败也要收尾引擎
            runner_error = type(exc).__name__
        else:
            try:
                code = int(runner.run())
            except Exception as exc:  # noqa: BLE001 - 抛错不泄漏文本；引擎仍收尾
                runner_error = type(exc).__name__
                code = LAUNCHER_EXIT_INTERNAL
        if runner_error is not None:
            self._note("runner-error", runner_error)

        # 宿主责任：runner 返回后 finally 收尾引擎（runner 不关闭借入对象）。
        report = self._finalize_engine(engine)
        self._engine_cleanup = report

        if not report.get("converged"):
            effective = LAUNCHER_EXIT_CLEANUP_UNPROVEN
            reason = "engine-cleanup-unproven"
            phase = "cleanup-unproven"
        else:
            effective = int(code)
            reason = self._reason_for(effective, runner_error)
            phase = "internal" if runner_error is not None else "finished"
        self._exit_reason = reason
        self._write_status(
            phase,
            exit_code=effective,
            reason=reason,
            runner_error=runner_error,
        )
        self._announce(effective)
        return effective

    @staticmethod
    def _reason_for(code: int, runner_error: str | None) -> str:
        if runner_error is not None:
            return "internal"
        return _EXIT_REASONS.get(int(code), "unknown")

    def _build_engine(self) -> Any:
        if self._emulator_factory is not None:
            return self._emulator_factory()
        return HeadlessEmulator(cols=self._cols, rows=self._rows)

    def _build_runner(self, engine: Any) -> Any:
        if self._runner_factory is not None:
            return self._runner_factory(engine)
        # backend.probe 为 runner 装配默认（不注入 identity_probe）；feed_at 桥接自动装配。
        return TerminalRunner(
            self.terminal_id,
            self._secret_file,
            rows=self._rows,
            cols=self._cols,
            emulator=engine,
        )

    # ------------------------------------------------------ 引擎收尾（§4）
    def _finalize_engine(self, engine: Any) -> dict[str, Any]:
        started = time.monotonic()
        deadline = started + self._engine_total_budget
        attempt_cap = max(0.05, self._engine_attempt_budget)
        retry_interval = self._engine_retry_interval
        worker = _EngineCloseWorker(engine)
        attempts = 0
        last_error_type: str | None = None
        detail: list[str] = []
        converged = False

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                detail.append("budget-exhausted")
                break
            if worker.in_flight:
                # 复用同一在途 worker：只等待、不叠加、不裸关。
                finished, result, error_type = worker.invoke(min(attempt_cap, remaining))
            else:
                if attempts > 0:
                    gap = min(retry_interval, max(0.0, deadline - time.monotonic()))
                    if gap > 0.0:
                        time.sleep(gap)
                    remaining = deadline - time.monotonic()
                    if remaining <= 0.0:
                        detail.append("budget-exhausted")
                        break
                attempts += 1
                finished, result, error_type = worker.invoke(min(attempt_cap, remaining))
            if not finished:
                detail.append("close-in-flight")
                if time.monotonic() >= deadline:
                    break
                continue
            if error_type is None and _reported_closed(result):
                converged = True
                detail.append(f"closed-after-{attempts}-attempt(s)")
                break
            last_error_type = error_type or "close-not-converged"
            detail.append(f"attempt{attempts}:{last_error_type}")
            if time.monotonic() >= deadline:
                break

        in_flight = worker.in_flight
        if in_flight:
            detail.append("worker-retained-in-flight")
        return {
            "converged": bool(converged),
            "attempts": int(attempts),
            "last_error_type": last_error_type,
            "retained": [] if converged else ["engine"],
            "in_flight": bool(in_flight),
            "seconds": round(time.monotonic() - started, 3),
            "outcome": "closed" if converged else "unproven",
            "detail": "; ".join(detail) if detail else "ok",
        }

    # -------------------------------------------------- 启动失败 owner（§5）
    def _engine_startup_failed(self, exc: EmulatorStartupError) -> int:
        residual = dict(exc.residual) if isinstance(exc.residual, Mapping) else {}
        startup: dict[str, Any] = {
            "reason": str(residual.get("reason") or "startup-failed")[:64],
            "owner": "none",
            "attempts": 0,
            "converged": True,
            "residual": residual,
        }
        owner = getattr(exc, "owner", None)
        if owner is None:
            self._note("engine-startup-no-owner", startup["reason"])
        else:
            outcome = self._consume_startup_owner(owner)
            startup.update(outcome)
        self._startup_report = startup
        converged = bool(startup.get("converged"))
        if converged:
            code = LAUNCHER_EXIT_ENGINE_STARTUP_FAILED
            reason = "engine-startup-failed"
            phase = "engine-startup-failed"
        else:
            code = LAUNCHER_EXIT_CLEANUP_UNPROVEN
            reason = "engine-startup-cleanup-unproven"
            phase = "cleanup-unproven"
        self._exit_reason = reason
        self._write_status(phase, exit_code=code, reason=reason)
        self._announce(code)
        return code

    def _consume_startup_owner(self, owner: Any) -> dict[str, Any]:
        """实际消费可重试 owner：同 owner 循环（有界，含全部等待）。"""
        started = time.monotonic()
        deadline = started + self._engine_total_budget
        attempt_cap = max(0.05, self._engine_attempt_budget)
        retry_interval = self._engine_retry_interval
        attempts = 0
        last_error_type: str | None = None
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                break
            attempts += 1
            try:
                outcome = owner.retry_cleanup(timeout=min(attempt_cap, remaining))
            except Exception as exc:  # noqa: BLE001 - owner 契约不抛；防御兜底只记类型
                last_error_type = type(exc).__name__
                outcome = None
            if isinstance(outcome, Mapping) and outcome.get("closed"):
                self._note("engine-startup-owner-consumed", f"attempts={attempts}")
                return {
                    "owner": "consumed",
                    "attempts": attempts,
                    "converged": True,
                    "seconds": round(time.monotonic() - started, 3),
                }
            if isinstance(outcome, Mapping):
                last_error_type = str(
                    outcome.get("reason") or outcome.get("detail") or last_error_type or ""
                )[:64] or last_error_type
            elif last_error_type is None:
                last_error_type = "invalid-owner-report"
            gap = min(retry_interval, max(0.0, deadline - time.monotonic()))
            if gap <= 0.0:
                break
            time.sleep(gap)
        self._note("engine-startup-owner-unproven", f"attempts={attempts}")
        return {
            "owner": "unproven",
            "attempts": attempts,
            "converged": False,
            "last_error_type": last_error_type,
            "seconds": round(time.monotonic() - started, 3),
        }

    # ------------------------------------------------------ 状态文件（§6）
    def _status_path(self) -> Path:
        """先校验 terminal_id；非法 id → ValueError（调用方零写、只脱敏诊断）。"""
        terminal_id = secret_store.SecretStore.validate_terminal_id(self.terminal_id)
        path = self._status_dir / f"{terminal_id}.json"
        try:
            root = self._status_dir.resolve()
            parent = Path(path).parent.resolve()
        except OSError as exc:
            raise ValueError(f"status path unresolvable: {type(exc).__name__}") from exc
        if parent != root:
            raise ValueError("status path escapes the launcher status directory")
        return path

    def _write_status(
        self,
        phase: str,
        *,
        exit_code: int | None,
        reason: str | None,
        runner_error: str | None = None,
    ) -> bool:
        """原子、串行、脱敏；失败只记类型名（不抛、不假成功，退出码不因写失败改变）。"""
        payload = {
            "schema_version": 1,
            "terminal_id": self.terminal_id,
            "phase": str(phase)[:32],
            "exit_code": int(exit_code) if exit_code is not None else None,
            "reason": str(reason or self._exit_reason or "")[:64],
            "runner_error": runner_error,
            "launcher_identity": dict(self._identity),
            "engine": {
                "created": bool(self._engine_created),
                "cleanup": dict(self._engine_cleanup),
                "startup": dict(self._startup_report) if self._startup_report else None,
            },
            "authority": _AUTHORITY_NOTE,
            "updated_at": round(time.time(), 3),
        }
        with self._status_lock:
            try:
                path = self._status_path()
            except ValueError as exc:
                self._note("status-path-rejected", str(exc))
                return False
            try:
                self._status_dir.mkdir(parents=True, exist_ok=True)
                tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
                tmp.write_text(
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8",
                )
                os.replace(tmp, path)
                return True
            except Exception as exc:  # noqa: BLE001 - 可观测性失败不影响生命周期
                self._note("status-write-failed", type(exc).__name__)
                return False

    # ------------------------------------------------------------- 公告
    def _announce(self, code: int) -> None:
        reason = self._exit_reason or _EXIT_REASONS.get(int(code), "unknown")
        if self._startup_report is not None:
            converged = bool(self._startup_report.get("converged"))
        else:
            converged = bool(self._engine_cleanup.get("converged"))
        cleanup = "converged" if converged else "unproven"
        print(
            f"pan-terminal-launcher: exit code={int(code)} reason={reason} "
            f"engine-cleanup={cleanup}",
            file=sys.stderr,
        )


def _parse_argv(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m packages.core.terminal.launcher",
        description="Pan Terminal launcher（独立 runner 进程内拥有引擎的生产宿主）",
    )
    parser.add_argument("--terminal-id", required=True, help="term_... 终端标识")
    parser.add_argument("--secret-file", required=True, help="<root>/secrets/<terminal_id>.secret")
    parser.add_argument("--rows", type=int, default=DEFAULT_ROWS)
    parser.add_argument("--cols", type=int, default=DEFAULT_COLS)
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Sequence[str] | None = None) -> int:
    """命令行入口：argv 只有 id 与 secret 路径（无 token）。"""
    try:
        args = _parse_argv(argv)
    except SystemExit as exc:  # argparse 已输出用法
        return int(exc.code or 2)
    launcher = TerminalLauncher(
        args.terminal_id,
        args.secret_file,
        rows=int(args.rows),
        cols=int(args.cols),
    )
    try:
        return int(launcher.run())
    except Exception as exc:  # noqa: BLE001 - 顶层兜底：只输出类型名
        print(
            f"pan-terminal-launcher: internal error ({type(exc).__name__})",
            file=sys.stderr,
        )
        return LAUNCHER_EXIT_INTERNAL


if __name__ == "__main__":  # pragma: no cover - 由 -m 执行
    raise SystemExit(main())
