"""Pan Terminal launcher 生产宿主测试（注入门控 + 真实跨进程组合）。

- **注入门控**：走实际生产 :class:`packages.core.terminal.launcher.TerminalLauncher`
  对象（仅 emulator/runner 工厂被替身替换），覆盖引擎构造前 / ready 前 EOF / fatal /
  timeout（owner=None 与 owner 消费两种）、runner 构造/run 抛错、close false→同 owner
  重试、close 抛异常→重试、永久阻塞有界失败非零（单飞 worker 不叠加、不裸关）、
  收尾预算含等待（且之后同 owner 重试可收敛）、状态写失败不假成功、非法 id 零派生
  文件、异常文本 token 哨兵脱敏。
- **真实跨进程**：``python -m packages.core.terminal.launcher`` 生产入口 + 真实
  ConPTY / 命名管道 / HMAC / headless 引擎：bootstrap、独立心跳、snapshot 协议 A /
  partial 确认、显式 stop、lease 丢失自停、bootstrap 失败收尾、硬死布局清理、
  ambient detach 拒绝零变化（不做环境逃脱）。

纪律：清理只对**自有**资源做同 handle 身份核验（raw FILETIME + Wait），不按名广杀。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

import pytest

from packages.core.terminal import launcher as launcher_module
from packages.core.terminal import runner as runner_module
from packages.core.terminal import runner_client, secret_store, win_pipe
from packages.core.terminal.contracts import ProcessStatus
from packages.core.terminal.emulator import EmulatorStartupError
from packages.core.terminal.launcher import TerminalLauncher

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="launcher 依赖 Windows ConPTY / Job Object / DPAPI / 命名管道",
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SIDECAR_DIR = REPO_ROOT / "packages/core/terminal/emulator_sidecar"
LAUNCHER_HARD_TIMEOUT = 240.0


def _sidecar_deps_present() -> bool:
    return (SIDECAR_DIR / "node_modules/@xterm/headless/package.json").is_file()


requires_sidecar = pytest.mark.skipif(
    not _sidecar_deps_present(),
    reason="sidecar 依赖未安装（需在 emulator_sidecar/ 执行 npm ci）",
)


# ══════════════════════════════════════════════════════════════════════════
# 证据 / 工具（与组合套件同口径的小工具，独立实现）
# ══════════════════════════════════════════════════════════════════════════


def _evidence_dir() -> Path | None:
    value = os.environ.get("PAN_TERMINAL_LAUNCHER_EVIDENCE_DIR")
    if not value:
        return None
    path = Path(value)
    path.mkdir(parents=True, exist_ok=True)
    return path


def record_evidence(name: str, payload: dict[str, Any]) -> None:
    directory = _evidence_dir()
    if directory is None:
        return
    (directory / f"{name}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )


def _wait_until(predicate: Callable[[], Any], timeout: float, interval: float = 0.05) -> Any:
    deadline = time.monotonic() + float(timeout)
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return predicate()


def _pid_exists(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = ctypes.c_void_p
    k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k32.CloseHandle.argtypes = (ctypes.c_void_p,)
    ctypes.set_last_error(0)
    handle = k32.OpenProcess(0x00100000, False, int(pid))
    if not handle:
        return int(ctypes.get_last_error()) != 87  # ERROR_INVALID_PARAMETER
    k32.CloseHandle(ctypes.c_void_p(handle))
    return True


def process_dead(pid: int, filetime: int | None = None) -> bool:
    probe = win_pipe.probe_process(int(pid))
    if probe.status is ProcessStatus.DEAD:
        return True
    if probe.status is ProcessStatus.ALIVE:
        if (
            filetime is not None
            and probe.identity is not None
            and int(probe.identity.created_at_filetime or -1) != int(filetime)
        ):
            return True
        return False
    return not _pid_exists(int(pid))


def wait_dead(pid: int, timeout: float, filetime: int | None = None) -> bool:
    return bool(_wait_until(lambda: process_dead(int(pid), filetime), timeout))


def _enum_str(value: Any) -> str:
    return str(value).rsplit(".", 1)[-1].lower()


# ══════════════════════════════════════════════════════════════════════════
# 注入门控替身（只替换 emulator/runner 工厂；launcher 本体为生产对象）
# ══════════════════════════════════════════════════════════════════════════


class _FakeOwner:
    """可重试 owner 替身：第 ``converge_after`` 次调用起返回 closed=True。"""

    def __init__(self, *, converge_after: int = 1, reason: str = "injected-not-converged") -> None:
        self.calls = 0
        self.converge_after = int(converge_after)
        self.reason = reason

    def retry_cleanup(self, *, timeout: float = 10.0) -> dict[str, Any]:
        self.calls += 1
        closed = self.calls >= self.converge_after
        return {
            "closed": closed,
            "retryable": not closed,
            "reason": None if closed else self.reason,
            "seconds": 0.01,
        }


class _FakeEngine:
    """close 脚本化替身（记录调用数与**并发峰值** → 验证单飞不叠加）。"""

    def __init__(self, close_script: list[Any]) -> None:
        self.close_script = list(close_script)
        self.close_calls = 0
        self.concurrent = 0
        self.max_concurrent = 0
        self._lock = threading.Lock()

    def close(self, *, timeout: float | None = None) -> dict[str, Any]:
        with self._lock:
            self.close_calls += 1
            self.concurrent += 1
            self.max_concurrent = max(self.max_concurrent, self.concurrent)
        try:
            step = self.close_script[min(self.close_calls - 1, len(self.close_script) - 1)]
            if step == "true":
                return {"closed": True}
            if step == "false":
                return {"closed": False, "detail": "injected-close-not-converged"}
            if step == "raise":
                raise RuntimeError("injected-close-exception")
            if isinstance(step, tuple) and step[0] == "gate":
                step[1].wait(30.0)
                return {"closed": True}
            raise AssertionError(f"unknown close step: {step!r}")
        finally:
            with self._lock:
                self.concurrent -= 1


class _FakeRunner:
    def __init__(self, *, code: int = 0, error: BaseException | None = None) -> None:
        self.code = int(code)
        self.error = error

    def run(self) -> int:
        if self.error is not None:
            raise self.error
        return self.code


def _make_launcher(tmp_path: Path, terminal_id: str = "term_gate", **kwargs: Any) -> TerminalLauncher:
    kwargs.setdefault("status_dir", tmp_path / "status")
    return TerminalLauncher(
        terminal_id,
        tmp_path / "secrets" / f"{terminal_id}.secret",
        **kwargs,
    )


def _read_status(tmp_path: Path, terminal_id: str = "term_gate") -> dict[str, Any]:
    path = tmp_path / "status" / f"{terminal_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))


# ══════════════════════════════════════════════════════════════════════════
# ① 引擎构造失败四态（pre-spawn / EOF / fatal / timeout）
# ══════════════════════════════════════════════════════════════════════════


def test_pre_spawn_failure_owner_none_records_without_claim(tmp_path):
    """spawn 前失败（owner=None）：无自有资源可清——不伪造消费，exit 8 + 无引擎创建。"""

    def fail_factory():
        raise EmulatorStartupError(
            "node-missing", owner=None, residual={"reason": "node-missing"}
        )

    launcher = _make_launcher(
        tmp_path, "term_pre", emulator_factory=fail_factory,
        runner_factory=lambda engine: _FakeRunner(code=0),
    )
    code = launcher.run()
    assert code == launcher_module.LAUNCHER_EXIT_ENGINE_STARTUP_FAILED
    report = launcher.startup_report
    assert report is not None
    assert report["owner"] == "none" and report["converged"] is True
    assert report["attempts"] == 0 and report["residual"]["reason"] == "node-missing"
    status = _read_status(tmp_path, "term_pre")
    assert status["phase"] == "engine-startup-failed"
    assert status["exit_code"] == launcher_module.LAUNCHER_EXIT_ENGINE_STARTUP_FAILED
    assert status["engine"]["created"] is False
    record_evidence(
        "launcher_pre_spawn_failure",
        {"exit_code": code, "startup": report, "phase": status["phase"]},
    )


def test_ready_eof_failure_owner_actually_consumed_until_converged(tmp_path):
    """spawn 后 ready 前 EOF：owner 必须被**实际消费**（重试至收敛）→ exit 8。"""
    owner = _FakeOwner(converge_after=2)

    def fail_factory():
        raise EmulatorStartupError(
            "ready-eof", owner=owner,
            residual={"reason": "ready-eof", "pid": 4242, "identity_filetime": "133400000"},
        )

    launcher = _make_launcher(
        tmp_path, "term_eof", emulator_factory=fail_factory,
        runner_factory=lambda engine: _FakeRunner(code=0),
        engine_total_budget=5.0, engine_close_attempt_budget=1.0,
        engine_cleanup_retry_interval=0.05,
    )
    code = launcher.run()
    assert code == launcher_module.LAUNCHER_EXIT_ENGINE_STARTUP_FAILED
    assert owner.calls == 2, "owner 必须被实际消费（而非只记录）"
    report = launcher.startup_report
    assert report is not None and report["owner"] == "consumed"
    assert report["converged"] is True and report["attempts"] == 2
    assert report["residual"]["pid"] == 4242
    assert ("engine-startup-owner-consumed", "attempts=2") in launcher.events
    status = _read_status(tmp_path, "term_eof")
    assert status["engine"]["startup"]["converged"] is True
    record_evidence(
        "launcher_ready_eof_owner_consumed",
        {"exit_code": code, "owner_calls": owner.calls, "startup": report},
    )


def test_fatal_frame_failure_owner_unproven_bounded_nonzero(tmp_path):
    """spawn 后 fatal 帧：owner 不收敛 → 总预算内有界失败、exit 6、如实记录。"""
    owner = _FakeOwner(converge_after=10**9)

    def fail_factory():
        raise EmulatorStartupError(
            "fatal-frame", owner=owner, residual={"reason": "fatal-frame", "pid": 7}
        )

    launcher = _make_launcher(
        tmp_path, "term_fatal", emulator_factory=fail_factory,
        runner_factory=lambda engine: _FakeRunner(code=0),
        engine_total_budget=0.6, engine_close_attempt_budget=0.2,
        engine_cleanup_retry_interval=0.05,
    )
    started = time.monotonic()
    code = launcher.run()
    elapsed = time.monotonic() - started
    assert code == launcher_module.LAUNCHER_EXIT_CLEANUP_UNPROVEN
    assert elapsed < 0.6 + 0.6, f"owner 消费必须有界（实测 {elapsed:.2f}s）"
    assert owner.calls >= 2, "预算内应有多次有界尝试（不是单次即弃）"
    report = launcher.startup_report
    assert report is not None and report["owner"] == "unproven"
    assert report["converged"] is False
    assert report["last_error_type"] == "injected-not-converged"
    status = _read_status(tmp_path, "term_fatal")
    assert status["phase"] == "cleanup-unproven"
    record_evidence(
        "launcher_fatal_owner_unproven",
        {"exit_code": code, "owner_calls": owner.calls, "elapsed": round(elapsed, 3),
         "startup": report},
    )


def test_startup_timeout_failure_owner_consumed(tmp_path):
    """spawn 后握手 timeout：与 EOF 同契约（owner 消费 → exit 8）。"""
    owner = _FakeOwner(converge_after=1)

    def fail_factory():
        raise EmulatorStartupError(
            "startup-timeout", owner=owner, residual={"reason": "startup-timeout", "pid": 9}
        )

    launcher = _make_launcher(
        tmp_path, "term_timeout", emulator_factory=fail_factory,
        runner_factory=lambda engine: _FakeRunner(code=0),
        engine_total_budget=2.0, engine_close_attempt_budget=1.0,
    )
    code = launcher.run()
    assert code == launcher_module.LAUNCHER_EXIT_ENGINE_STARTUP_FAILED
    assert owner.calls == 1
    assert launcher.startup_report["owner"] == "consumed"
    record_evidence(
        "launcher_startup_timeout_owner_consumed",
        {"exit_code": code, "owner_calls": owner.calls},
    )


# ══════════════════════════════════════════════════════════════════════════
# ② runner 构造 / run 抛错（引擎仍必须被收尾）
# ══════════════════════════════════════════════════════════════════════════


def test_runner_construct_error_still_finalizes_engine(tmp_path):
    """runner 构造抛错：引擎仍走 finally 收尾；exit 5 + runner_error 只记类型名。"""
    engine = _FakeEngine(["true"])

    def fail_runner_factory(_engine):
        raise RuntimeError("construct-failed")

    launcher = _make_launcher(
        tmp_path, "term_ctor", emulator_factory=lambda: engine,
        runner_factory=fail_runner_factory,
        engine_total_budget=3.0, engine_close_attempt_budget=1.0,
    )
    code = launcher.run()
    assert code == launcher_module.LAUNCHER_EXIT_INTERNAL
    assert engine.close_calls == 1, "runner 构造失败不豁免引擎收尾"
    assert launcher.engine_cleanup["converged"] is True
    status = _read_status(tmp_path, "term_ctor")
    assert status["runner_error"] == "RuntimeError"
    assert status["phase"] == "internal"
    assert status["engine"]["cleanup"]["converged"] is True
    record_evidence(
        "launcher_runner_construct_error",
        {"exit_code": code, "close_calls": engine.close_calls, "cleanup": launcher.engine_cleanup},
    )


def test_runner_run_error_token_sentinel_redacted_and_engine_finalized(tmp_path, capsys):
    """runner.run 抛含 token 哨兵的异常：只记类型名（status/stderr 零泄漏）+ 引擎收尾。"""
    sentinel = "PAN-TOKEN-SENTINEL-9f3c2a"
    engine = _FakeEngine(["true"])
    launcher = _make_launcher(
        tmp_path, "term_sentinel", emulator_factory=lambda: engine,
        runner_factory=lambda _e: _FakeRunner(error=RuntimeError(f"leak {sentinel} now")),
        engine_total_budget=3.0, engine_close_attempt_budget=1.0,
    )
    code = launcher.run()
    captured = capsys.readouterr()
    assert code == launcher_module.LAUNCHER_EXIT_INTERNAL
    assert engine.close_calls == 1 and launcher.engine_cleanup["converged"] is True
    status_raw = (tmp_path / "status" / "term_sentinel.json").read_text(encoding="utf-8")
    assert sentinel not in status_raw, "状态文件不得泄漏异常文本（token 哨兵）"
    assert sentinel not in captured.err and sentinel not in captured.out
    assert json.loads(status_raw)["runner_error"] == "RuntimeError"
    record_evidence(
        "launcher_token_sentinel_redacted",
        {"exit_code": code, "sentinel_in_status": False, "sentinel_in_stderr": False},
    )


# ══════════════════════════════════════════════════════════════════════════
# ③ 引擎收尾：false→重试 / 异常→重试 / 永久阻塞有界非零
# ══════════════════════════════════════════════════════════════════════════


def test_engine_close_false_then_success_retries_same_owner_not_stacked(tmp_path):
    """close=false → 同 owner（同一引擎对象）重试成功；透传 runner 码；单飞不叠加。"""
    engine = _FakeEngine(["false", "true"])
    launcher = _make_launcher(
        tmp_path, "term_retry", emulator_factory=lambda: engine,
        runner_factory=lambda _e: _FakeRunner(code=0),
        engine_total_budget=5.0, engine_close_attempt_budget=1.0,
        engine_cleanup_retry_interval=0.05,
    )
    code = launcher.run()
    assert code == 0
    assert engine.close_calls == 2
    assert engine.max_concurrent == 1, "重试期间同一时刻只允许一个 close 调用在途"
    report = launcher.engine_cleanup
    assert report["converged"] is True and report["attempts"] == 2
    status = _read_status(tmp_path, "term_retry")
    assert status["phase"] == "finished" and status["exit_code"] == 0
    record_evidence(
        "launcher_close_false_retry",
        {"exit_code": code, "close_calls": engine.close_calls,
         "max_concurrent": engine.max_concurrent, "cleanup": report},
    )


def test_engine_close_exception_then_success_retried(tmp_path):
    """close 抛异常 → 记录类型名后同 owner 重试成功；异常文本不入状态。"""
    engine = _FakeEngine(["raise", "true"])
    launcher = _make_launcher(
        tmp_path, "term_exc", emulator_factory=lambda: engine,
        runner_factory=lambda _e: _FakeRunner(code=0),
        engine_total_budget=5.0, engine_close_attempt_budget=1.0,
        engine_cleanup_retry_interval=0.05,
    )
    code = launcher.run()
    assert code == 0
    assert engine.close_calls == 2 and engine.max_concurrent == 1
    report = launcher.engine_cleanup
    assert report["converged"] is True
    assert report["last_error_type"] == "RuntimeError"
    status_raw = (tmp_path / "status" / "term_exc.json").read_text(encoding="utf-8")
    assert "injected-close-exception" not in status_raw
    record_evidence(
        "launcher_close_exception_retry",
        {"exit_code": code, "close_calls": engine.close_calls, "cleanup": report},
    )


def test_engine_close_permanent_block_bounded_nonzero_not_stacked(tmp_path):
    """永久阻塞（close 永不返回）：总预算内有界失败非零；worker 复用不叠加、不裸关。"""
    gate = threading.Event()
    engine = _FakeEngine([("gate", gate)])
    launcher = _make_launcher(
        tmp_path, "term_block", emulator_factory=lambda: engine,
        runner_factory=lambda _e: _FakeRunner(code=0),
        engine_total_budget=0.8, engine_close_attempt_budget=0.25,
        engine_cleanup_retry_interval=0.05,
    )
    started = time.monotonic()
    code = launcher.run()
    elapsed = time.monotonic() - started
    try:
        assert code == launcher_module.LAUNCHER_EXIT_CLEANUP_UNPROVEN
        assert elapsed < 0.8 + 0.8, f"收尾必须有界（实测 {elapsed:.2f}s）"
        assert engine.max_concurrent == 1, "在途 worker 不得被叠加第二个 close 调用"
        report = launcher.engine_cleanup
        assert report["converged"] is False and report["in_flight"] is True
        assert report["retained"] == ["engine"]
        status = _read_status(tmp_path, "term_block")
        assert status["phase"] == "cleanup-unproven"
        assert status["engine"]["cleanup"]["in_flight"] is True
        record_evidence(
            "launcher_close_permanent_block",
            {"exit_code": code, "elapsed": round(elapsed, 3),
             "max_concurrent": engine.max_concurrent, "cleanup": report},
        )
    finally:
        gate.set()  # 释放注入 worker（daemon），避免悬挂
        _wait_until(lambda: engine.concurrent == 0, 2.0)


def test_finalize_budget_includes_wait_then_same_owner_retry_converges(tmp_path):
    """关闭预算含等待：预算内不静默延长（非零）；释放后**同 owner** 重试可收敛。"""
    gate = threading.Event()
    engine = _FakeEngine([("gate", gate), "true"])
    launcher = _make_launcher(
        tmp_path, "term_budget", emulator_factory=lambda: engine,
        runner_factory=lambda _e: _FakeRunner(code=0),
        engine_total_budget=0.4, engine_close_attempt_budget=0.25,
        engine_cleanup_retry_interval=0.05,
    )
    started = time.monotonic()
    code = launcher.run()
    elapsed = time.monotonic() - started
    assert code == launcher_module.LAUNCHER_EXIT_CLEANUP_UNPROVEN
    assert 0.35 <= elapsed <= 0.4 + 0.6, (
        f"预算必须包含 close 等待且不静默延长（实测 {elapsed:.2f}s）"
    )
    assert launcher.engine_cleanup["in_flight"] is True
    # T1 释放：迟到的 close 成功必须可被**同一 engine 对象**的后续收尾消费。
    gate.set()
    assert _wait_until(lambda: engine.concurrent == 0, 5.0), "注入 close 应完成"
    report2 = launcher._finalize_engine(engine)
    assert report2["converged"] is True and report2["attempts"] >= 1
    assert engine.max_concurrent == 1
    record_evidence(
        "launcher_budget_includes_wait",
        {"first": {"exit_code": code, "elapsed": round(elapsed, 3),
                   "in_flight": True},
         "retry": {"converged": report2["converged"], "attempts": report2["attempts"]},
         "max_concurrent": engine.max_concurrent},
    )


# ══════════════════════════════════════════════════════════════════════════
# ④ 状态写失败不假成功 / 非法 id 零派生文件
# ══════════════════════════════════════════════════════════════════════════


def test_status_write_failure_never_fakes_success(tmp_path, capsys):
    """status 写失败：不抛、不假成功；退出码不因写失败改变（收敛 0 / 未收敛 6）。"""
    blocked = tmp_path / "blocked-status"
    blocked.write_text("not a directory", encoding="utf-8")
    # 收敛场景
    engine_ok = _FakeEngine(["true"])
    launcher_ok = TerminalLauncher(
        "term_statusok", tmp_path / "secrets" / "term_statusok.secret",
        status_dir=blocked, emulator_factory=lambda: engine_ok,
        runner_factory=lambda _e: _FakeRunner(code=0),
        engine_total_budget=3.0, engine_close_attempt_budget=1.0,
    )
    code_ok = launcher_ok.run()
    assert code_ok == 0, "写失败不得把收敛结果改成失败"
    assert any(event == "status-write-failed" for event, _ in launcher_ok.events)
    # 未收敛场景
    engine_bad = _FakeEngine(["false"])
    launcher_bad = TerminalLauncher(
        "term_statusbad", tmp_path / "secrets" / "term_statusbad.secret",
        status_dir=blocked, emulator_factory=lambda: engine_bad,
        runner_factory=lambda _e: _FakeRunner(code=0),
        engine_total_budget=0.3, engine_close_attempt_budget=0.15,
        engine_cleanup_retry_interval=0.05,
    )
    code_bad = launcher_bad.run()
    assert code_bad == launcher_module.LAUNCHER_EXIT_CLEANUP_UNPROVEN, (
        "未收敛 + 写失败不得假成功"
    )
    captured = capsys.readouterr()
    assert "internal error" not in captured.err, "写失败不得触发顶层兜底"
    record_evidence(
        "launcher_status_write_failure",
        {"converged_case_exit": code_ok, "unproven_case_exit": code_bad,
         "events": [list(item) for item in launcher_bad.events]},
    )


def test_illegal_terminal_id_zero_derived_files_even_on_failure_path(tmp_path):
    """非法 id：_status_path 拒绝 + **零派生文件**（含启动失败状态路径不逃逸）。"""

    def fail_factory():
        raise EmulatorStartupError("node-missing", owner=None, residual={"reason": "node-missing"})

    launcher = _make_launcher(
        tmp_path, "term_bad/../escape", emulator_factory=fail_factory,
        runner_factory=lambda engine: _FakeRunner(code=0),
        engine_total_budget=1.0,
    )
    code = launcher.run()
    assert code == launcher_module.LAUNCHER_EXIT_ENGINE_STARTUP_FAILED
    assert any(event == "status-path-rejected" for event, _ in launcher.events)
    status_dir = tmp_path / "status"
    assert not status_dir.exists() or not list(status_dir.iterdir()), (
        "非法 id 不得写出任何派生文件"
    )
    assert not (tmp_path / "escape.json").exists()
    record_evidence(
        "launcher_illegal_id_zero_files",
        {"exit_code": code, "status_dir_empty": True,
         "events": [list(item) for item in launcher.events]},
    )


# ══════════════════════════════════════════════════════════════════════════
# 真实跨进程（生产入口 ``python -m packages.core.terminal.launcher``）
# ══════════════════════════════════════════════════════════════════════════


def _sidecar_pids_for(parent_pid: int) -> set[int]:
    """launcher 进程直系 node 子进程（sidecar，只读扫描，不杀任何进程）。"""
    try:
        import psutil
    except ImportError:  # pragma: no cover
        return set()
    pids: set[int] = set()
    for proc in psutil.process_iter(["pid", "name", "ppid"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if "node" in name and int(proc.info.get("ppid") or -1) == int(parent_pid):
                pids.add(int(proc.info["pid"]))
        except Exception:  # noqa: BLE001
            continue
    return pids


def _all_sidecar_pids() -> set[int]:
    """全局只读扫描 node sidecar（不杀任何进程；用于无 parent 关联的残差核对）。"""
    try:
        import psutil
    except ImportError:  # pragma: no cover
        return set()
    pids: set[int] = set()
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = (proc.info.get("name") or "").lower()
            if "node" not in name:
                continue
            cmdline = proc.info.get("cmdline") or []
            if any("emulator_sidecar" in str(part) for part in cmdline):
                pids.add(int(proc.info["pid"]))
        except Exception:  # noqa: BLE001
            continue
    return pids


def _read_until(
    client: "runner_client.RunnerClient",
    marker: bytes,
    *,
    timeout: float = 20.0,
    cursor: int = 0,
) -> bytes:
    deadline = time.monotonic() + timeout
    buf = bytearray()
    current = int(cursor)
    while time.monotonic() < deadline:
        page = client.read(current)
        if page["gap"] is not None:
            current = page["next_cursor"]
            continue
        if page["data"]:
            buf += page["data"]
            current = page["next_cursor"]
        if marker in buf:
            return bytes(buf)
        time.sleep(0.05)
    return bytes(buf)


def _wait_total_stable(
    client: "runner_client.RunnerClient", *, timeout: float = 20.0,
    interval: float = 0.15, stable_polls: int = 3,
) -> int:
    deadline = time.monotonic() + timeout
    last = -1
    stable = 0
    while time.monotonic() < deadline:
        total = int(client.describe()["total_bytes"])
        if total == last:
            stable += 1
            if stable >= stable_polls:
                return total
        else:
            stable = 0
            last = total
        time.sleep(interval)
    return last


class _RealHeartbeatKeeper:
    """独立连接上的持续心跳（维持 managed lease；记录延迟/错误）。"""

    def __init__(self, session: "_LauncherSession", *, interval: float = 0.35) -> None:
        self._session = session
        self._interval = float(interval)
        self._stop = threading.Event()
        self.client = session.client("pan-launcher-keeper")
        assert self.client.heartbeat()["status"] == "ok"
        self.latencies: list[float] = []
        self.errors: list[str] = []
        self._thread = threading.Thread(target=self._loop, name="launcher-heartbeat", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                if self.client.heartbeat(timeout_ms=3000).get("status") != "ok":
                    self.errors.append("non-ok")
                    return
            except Exception as exc:  # noqa: BLE001 - 断连/超时类型记录
                self.errors.append(type(exc).__name__)
                return
            self.latencies.append(round(time.monotonic() - started, 3))
            self._stop.wait(self._interval)

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=3.0)
        try:
            self.client.release_connection()
        except Exception:  # noqa: BLE001
            pass


class _LauncherSession:
    """生产 launcher 子进程会话（launcher 进程 == runner 进程，bootstrap 自证）。"""

    def __init__(
        self,
        proc: subprocess.Popen,
        terminal_id: str,
        root: Path,
        store: "secret_store.SecretStore",
        status_dir: Path,
    ) -> None:
        self.proc = proc
        self.proc_pid = int(proc.pid)  # Popen 句柄 pid（uv shim 下可能 != 真解释器 pid）
        self.terminal_id = terminal_id
        self.root = root
        self.store = store
        self.status_dir = status_dir
        self.runner_pid: int | None = None
        self.runner_filetime: int | None = None
        self.shell_pid: int | None = None
        self.shell_filetime: int | None = None
        self.sidecar_pids: set[int] = set()
        self._keeper: _RealHeartbeatKeeper | None = None
        self._stderr_chunks: list[str] = []
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()

    def _drain_stderr(self) -> None:
        try:
            for line in self.proc.stderr:  # type: ignore[union-attr]
                self._stderr_chunks.append(line)
        except Exception:  # noqa: BLE001
            pass

    def stderr_text(self) -> str:
        return "".join(self._stderr_chunks)

    def bootstrap(self, *, timeout: float = 60.0) -> Any:
        record = self.store.wait_for_bootstrap_identity(self.terminal_id, timeout=timeout)
        self.runner_pid = int(record.pid)
        self.runner_filetime = int(record.filetime)
        # 注意：uv 的 python 启动器可能以 shim 包装（Popen 句柄 pid != 真实解释器 pid），
        # 因此 runner/launcher 主体的身份权威 = **bootstrap 自证**（runner_pid）；
        # “同进程宿主”由 launcher-status.launcher_identity 与自证交叉核验（T1）。
        runner_client.complete_bootstrap(self.store, self.terminal_id)
        return record

    def wait_for_sidecar(self, *, timeout: float = 12.0) -> set[int]:
        """心跳/保活建立后再扫描（避免挤占 bootstrap→heartbeat 的 grace 窗口）。"""
        self.sidecar_pids = _wait_until(
            lambda: _sidecar_pids_for(self._host_pid()) or None, timeout
        ) or set()
        return self.sidecar_pids

    def _host_pid(self) -> int:
        """持有 sidecar/Job 的宿主进程 pid（真解释器；未知时退回 Popen 句柄 pid）。"""
        return int(self.runner_pid) if self.runner_pid else int(self.proc.pid)

    def client(self, client_id: str | None = None) -> "runner_client.RunnerClient":
        client = runner_client.RunnerClient(
            self.terminal_id,
            data_root=self.root,
            connect_timeout=15.0,
            request_timeout_ms=15_000,
            close_timeout_ms=25_000,
            client_id=client_id,
        )
        client.attach()
        return client

    def keepalive(self, *, interval: float = 0.35) -> _RealHeartbeatKeeper:
        if self._keeper is None:
            self._keeper = _RealHeartbeatKeeper(self, interval=interval)
        return self._keeper

    def note_shell(self, describe: dict[str, Any]) -> None:
        self.shell_pid = int(describe["pid"])
        self.shell_filetime = int(describe["process_created_at_filetime"])

    def wait_exit(self, timeout: float = 60.0) -> int:
        return int(self.proc.wait(timeout=timeout))

    def status(self) -> dict[str, Any]:
        return json.loads(
            (self.status_dir / f"{self.terminal_id}.json").read_text(encoding="utf-8")
        )

    def sidecar_gone(self, timeout: float = 12.0) -> bool:
        return bool(_wait_until(
            lambda: not _sidecar_pids_for(self._host_pid()), timeout
        ))

    def cleanup(self) -> dict[str, Any]:
        """只对自有资源做同 handle 核验清理（raw FILETIME + Wait），不按名广杀。"""
        if self._keeper is not None:
            self._keeper.stop()
        try:
            self.proc.wait(timeout=4.0)
        except subprocess.TimeoutExpired:
            pass
        trace: dict[str, Any] = {
            "alive_before_cleanup": bool(
                self.runner_pid and process_dead(self.runner_pid, self.runner_filetime) is False
            )
        }
        if self.runner_pid and not process_dead(self.runner_pid, self.runner_filetime):
            trace["runner"] = win_pipe.terminate_verified_process(
                self.runner_pid, self.runner_filetime
            )
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            trace["launcher_wait"] = "timeout"
        leftover = _sidecar_pids_for(self._host_pid())
        if leftover:
            trace["sidecar_leftover"] = sorted(leftover)
        if self.shell_pid and not process_dead(self.shell_pid, self.shell_filetime):
            trace["shell_alive"] = True
        if self._stderr_thread.is_alive():
            self._stderr_thread.join(timeout=2)
        for stream in (self.proc.stdout, self.proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception:  # noqa: BLE001
                pass
        trace["exit_code"] = self.proc.returncode
        return trace


def _spawn_launcher(
    tmp_path: Path, *, rows: int | None = None, cols: int | None = None
) -> tuple[_LauncherSession, Path, str]:
    terminal_id = "term_" + os.urandom(6).hex()
    root = tmp_path / "launcher-terminals"
    store = secret_store.SecretStore(root)
    store.ensure_secrets_dir()
    secret_file = store.secret_path(terminal_id)
    argv = [
        sys.executable, "-m", "packages.core.terminal.launcher",
        "--terminal-id", terminal_id, "--secret-file", str(secret_file),
    ]
    if rows is not None:
        argv += ["--rows", str(int(rows))]
    if cols is not None:
        argv += ["--cols", str(int(cols))]
    env = {k: v for k, v in os.environ.items() if not k.startswith("PAN_TERMINAL_")}
    proc = subprocess.Popen(
        argv,
        cwd=str(REPO_ROOT),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    session = _LauncherSession(proc, terminal_id, root, store, root / "launcher-status")
    return session, secret_file, terminal_id


@requires_sidecar
def test_real_launcher_bootstrap_snapshot_protocol_a_and_stop(tmp_path):
    """真实生产入口：bootstrap 自证 / 独立心跳 / 协议 A 快照（F5 bool-or-null）/ 显式 stop。"""
    session, _secret, _tid = _spawn_launcher(tmp_path, rows=24, cols=80)
    try:
        session.bootstrap()
        client = session.client("pan-launcher-owner")
        assert client.heartbeat()["status"] == "ok"
        keeper = session.keepalive()  # 先建立保活，再做扫描/长步骤（uv 下宽松启动窗口）
        session.note_shell(client.describe())
        assert session.shell_pid and session.shell_pid != session.runner_pid
        assert session.wait_for_sidecar(), "引擎 sidecar 应作为 launcher 直系子进程存在"

        marker = f"LAUNCH-MARK-{os.urandom(3).hex()}"
        client.input(f"echo {marker}\r".encode("utf-8"))
        assert marker.encode() in _read_until(client, marker.encode(), timeout=20)
        total = _wait_total_stable(client)

        time.sleep(0.9)  # 引擎消化小输出
        snap = client.snapshot(timeout_ms=8000)
        assert snap["status"] in ("running", "degraded", "starting"), snap["status"]
        assert "serialized_screen" in snap and "cursor" in snap
        assert marker in snap["serialized_screen"]
        fidelity, recovery = _enum_str(snap["fidelity"]), _enum_str(snap["recovery"])
        assert fidelity in ("unavailable", "partial", "full")
        assert recovery in ("none", "degraded", "partial", "full")
        for field in ("cursors_valid", "reset_unconfirmed"):
            assert field in snap, f"F5 字段缺失：{field}"
            assert snap[field] is None or isinstance(snap[field], bool), (
                f"{field} 必须是 bool 或 null：{snap[field]!r}"
            )
        # 独立心跳（不占主连接）：间隔期内全部 ok 且低延迟
        time.sleep(0.8)
        assert keeper.errors == [], keeper.errors
        assert len(keeper.latencies) >= 2
        assert max(keeper.latencies) < 1.0, keeper.latencies

        stop = client.close()
        assert stop["status"] == "exited", stop
        code = session.wait_exit(timeout=60)
        assert code == 0
        status = session.status()
        assert status["phase"] == "finished" and status["exit_code"] == 0
        assert status["engine"]["created"] is True
        assert status["engine"]["cleanup"]["converged"] is True
        # status 身份可交叉核验（真实 pid + raw FILETIME 字符串 == bootstrap 自证）
        assert int(status["launcher_identity"]["pid"]) == session.runner_pid
        assert str(status["launcher_identity"]["process_created_at_filetime"]) == str(
            session.runner_filetime
        )
        assert wait_dead(session.shell_pid, 8.0, session.shell_filetime)
        assert session.sidecar_gone(12.0), "sidecar 必须随 launcher 收尾消亡"
        stderr = session.stderr_text()
        assert "exit code=0" in stderr and "engine-cleanup=converged" in stderr
        record_evidence(
            "launcher_real_bootstrap_snapshot_stop",
            {
                "total_bytes": int(total),
                "snapshot_cursor": int(snap.get("cursor", -1)),
                "fidelity": fidelity,
                "recovery": recovery,
                "f5_fields": {
                    "cursors_valid": snap.get("cursors_valid"),
                    "reset_unconfirmed": snap.get("reset_unconfirmed"),
                },
                "heartbeat_latencies": keeper.latencies[:8],
                "exit_code": code,
                "engine_cleanup": status["engine"]["cleanup"],
                "shell_dead": True,
                "sidecar_dead": True,
                "launcher_identity": status["launcher_identity"],
                "process_pids": {
                    "popen": session.proc_pid,
                    "runner_self_reported": session.runner_pid,
                    "note": "uv python 启动器可能为 shim；身份权威 = bootstrap 自证且与 status 交叉核验",
                },
            },
        )
    finally:
        trace = session.cleanup()
        record_evidence("launcher_real_bootstrap_snapshot_stop_cleanup", trace)


@requires_sidecar
def test_real_launcher_lease_loss_self_stop(tmp_path):
    """lease 丢失（Pan 不再心跳）→ runner 自停（reason lease-expired）→ 收尾 exit 0。"""
    session, _secret, _tid = _spawn_launcher(tmp_path)
    try:
        session.bootstrap()
        client = session.client("pan-launcher-owner")
        assert client.heartbeat()["status"] == "ok"
        session.note_shell(client.describe())
        # 不 keepalive：lease grace(2s) 后 watchdog 触发 lease-expired 自停。
        code = session.wait_exit(timeout=60)
        assert code == 0, f"lease 丢失自停应收敛（实测 {code}）"
        status = session.status()
        assert status["phase"] == "finished" and status["exit_code"] == 0
        assert status["engine"]["cleanup"]["converged"] is True
        assert wait_dead(session.shell_pid, 10.0, session.shell_filetime)
        assert session.sidecar_gone(12.0)
        record_evidence(
            "launcher_real_lease_loss",
            {"exit_code": code, "engine_cleanup": status["engine"]["cleanup"],
             "shell_dead": True, "sidecar_dead": True},
        )
    finally:
        trace = session.cleanup()
        record_evidence("launcher_real_lease_loss_cleanup", trace)


@requires_sidecar
def test_real_launcher_bootstrap_refused_still_finalizes_engine(tmp_path):
    """故障收尾：bootstrap 未被首绑（secret 从未写入）→ runner exit 4，引擎仍被收尾。"""
    session, _secret, _tid = _spawn_launcher(tmp_path)
    baseline = _all_sidecar_pids()  # 串行测试环境下核对“不得新增残留”
    try:
        # 不 complete_bootstrap：runner 等 secret 至 bootstrap grace 超时。
        code = session.wait_exit(timeout=90)
        assert code == 4, f"bootstrap 超时应为 RUNNER_EXIT_BOOTSTRAP_FAILED（实测 {code}）"
        status = session.status()
        assert status["exit_code"] == 4
        assert status["engine"]["created"] is True
        assert status["engine"]["cleanup"]["converged"] is True
        assert status["engine"]["cleanup"]["retained"] == []
        ok = _wait_until(lambda: _all_sidecar_pids() <= baseline, 12.0)
        assert ok, f"bootstrap 失败后 sidecar 不得残留：{_all_sidecar_pids() - baseline}"
        stderr = session.stderr_text()
        assert "exit code=4" in stderr and "engine-cleanup=converged" in stderr
        record_evidence(
            "launcher_real_bootstrap_refused_finalized",
            {"exit_code": code, "engine_cleanup": status["engine"]["cleanup"],
             "sidecar_dead": True, "stderr_reason": "bootstrap-failed"},
        )
    finally:
        trace = session.cleanup()
        record_evidence("launcher_real_bootstrap_refused_cleanup", trace)


@requires_sidecar
def test_real_launcher_hard_kill_tree_cleanup(tmp_path):
    """硬死布局（同 handle 核验终止 launcher）：shell 与 sidecar 随 Job guard 消亡（不泛化）。"""
    session, _secret, _tid = _spawn_launcher(tmp_path)
    try:
        session.bootstrap()
        client = session.client("pan-launcher-owner")
        assert client.heartbeat()["status"] == "ok"
        session.keepalive()
        session.note_shell(client.describe())
        shell_pid, shell_filetime = session.shell_pid, session.shell_filetime
        assert session.wait_for_sidecar()
        trace = win_pipe.terminate_verified_process(session.runner_pid, session.runner_filetime)
        assert trace.get("terminated") is True, trace
        assert wait_dead(shell_pid, 10.0, shell_filetime), "PTY 整树必须随进程消亡"
        assert session.sidecar_gone(15.0), "sidecar 必须随进程消亡（Job 内核兜底，本场景实测）"
        assert session.proc.wait(timeout=20) != 0
        client.release_connection()
        record_evidence(
            "launcher_real_hard_kill",
            {"terminate": trace, "shell_dead": True, "sidecar_dead": True,
             "kernel_guard_note": "本场景实测（已测布局）；不泛化"},
        )
    finally:
        trace = session.cleanup()
        record_evidence("launcher_real_hard_kill_cleanup", trace)


@requires_sidecar
def test_real_launcher_detach_refused_zero_state(tmp_path):
    """真实 ambient 约束：detach 明确拒绝、零状态变化（不做环境逃脱实验）。"""
    if not runner_module.detect_ambient_job():
        pytest.skip("no ambient job in this environment: refusal path not applicable")
    session, _secret, _tid = _spawn_launcher(tmp_path)
    try:
        session.bootstrap()
        client = session.client("pan-launcher-owner")
        assert client.heartbeat()["status"] == "ok"
        before = client.describe()
        assert before["durability"]["capable"] is False
        result = client.detach()
        assert result["status"] == "detach-refused" and result["ok"] is False
        after = client.describe()
        assert after["runner_state"] == "running" and after["detached"] is False
        assert int(after["pid"]) == int(before["pid"])
        session.note_shell(after)
        assert not process_dead(session.shell_pid, session.shell_filetime)
        assert client.close()["status"] == "exited"
        assert session.wait_exit(timeout=60) == 0
        status = session.status()
        assert status["phase"] == "finished"
        record_evidence(
            "launcher_real_detach_refused",
            {"durability": before["durability"], "detach_status": result["status"],
             "zero_state_change": True},
        )
    finally:
        trace = session.cleanup()
        record_evidence("launcher_real_detach_refused_cleanup", trace)
