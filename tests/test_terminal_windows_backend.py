"""Pan Terminal P1：ConPtyBackend 真实 ConPTY 后端（Windows）。

责任（任务卡）：纯 ctypes、非 Windows 明确 ``BackendUnavailableError``；
自有 Job 守卫；read 只对真实断链抛 ``EOFError``（进程退出 ≠ EOF，本机实测）；
write 串行 + partial + 有界预算 + 可取消；resize 到达真实子尺寸；alive 三态
（unknown 必须抛错，不用 False 冒充 dead）；exit_code 仅 signaled 时提供
（含真实 259）；close 分阶段、失败保留 owner 可重试、重复 close 幂等；
可装配 ``build_runtime`` 真实运行；句柄计数回归。

全部用例只用自建子进程与 pytest 临时目录；不接触任何既有服务/8768/daemon。
所有等待有界（pytest.ini timeout=300 为兜底看门狗）。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from packages.core.terminal import identity
from packages.core.terminal.backend import (
    BackendCloseError,
    BackendClosedError,
    BackendProbeError,
    ConPtyBackend,
    WinptyBackend,
)
from packages.core.terminal.contracts import (
    BackendUnavailableError,
    OwnershipMode,
    OwnershipPolicy,
    ProcessStatus,
    PtyBackend,
    RuntimeState,
)
from packages.core.terminal.guard import JobObjectGuard
from packages.core.terminal.identity import get_process_handle_count
from packages.core.terminal.ownership import build_runtime

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="ConPtyBackend 仅 Windows（生产路径）"
)

PYTHON = sys.executable
CAP = 256 * 1024


# --------------------------------------------------------------------------- helpers
def emit_evidence(name: str, payload: dict) -> Path | None:
    """可选证据落盘：仅当 PAN_TERMINAL_EVIDENCE_DIR 指定时写 JSON（不做默认副作用）。"""
    root = os.environ.get("PAN_TERMINAL_EVIDENCE_DIR")
    if not root:
        return None
    directory = Path(root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}-{os.getpid()}-{int(time.time())}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


def sleep_child(seconds: int = 90) -> list[str]:
    return [PYTHON, "-c", f"import time; time.sleep({seconds})"]


def write_script(tmp_path: Path, name: str, code: str) -> str:
    path = tmp_path / name
    path.write_text(code, encoding="utf-8")
    return str(path)


def read_until(backend: ConPtyBackend, needle: bytes, timeout: float = 10.0) -> tuple[bytes, str]:
    """有界读取直到出现 needle（返回 (buf, 'found'|'eof'|'timeout')）。"""
    buf = b""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        slice_timeout = min(0.2, max(0.0, deadline - time.monotonic()))
        try:
            chunk = backend.read(65536, timeout=slice_timeout)
        except EOFError:
            return buf, "eof"
        if chunk:
            buf += chunk
            if needle in buf:
                return buf, "found"
    return buf, "timeout"


def stop_backend(backend: ConPtyBackend, *, terminate: bool = True) -> dict:
    """测试收尾：terminate -> wait_dead -> close（失败重试一次）；只动自建资源。"""
    report: dict = {}
    try:
        if terminate and backend.alive():
            backend.terminate(True)
        report["dead"] = backend.wait_dead(5.0)
    except Exception as exc:  # noqa: BLE001
        report["terminate_error"] = f"{type(exc).__name__}: {exc}"
    try:
        report["close"] = backend.close()
    except BackendCloseError as exc:
        report["close_error"] = str(exc)
        try:
            report["close_retry"] = backend.close()
        except BackendCloseError as exc2:
            report["close_retry_error"] = str(exc2)
    return report


# --------------------------------------------------------------------------- runtime 装配
def test_runtime_assembly_tail_cjk_exit7(tmp_path):
    """四证据门禁 + build_runtime 真实运行：尾输出 + CJK + 退出码 7 + 收敛。"""
    b = ConPtyBackend.spawn(
        [
            PYTHON,
            "-c",
            "import sys; sys.stdout.write('中文尾输出 ✓ tail-marker\\n'); "
            "sys.stdout.flush(); sys.exit(7)",
        ],
        cwd=str(tmp_path),
        rows=30,
        cols=100,
    )
    try:
        assert b.gate is not None
        ev = b.ownership_evidence
        assert ev is not None and ev.assigned and ev.atomic_with_spawn
        assert ev.handle_bound_for_cleanup and ev.identity is not None
        assert isinstance(b, PtyBackend)

        policy = OwnershipPolicy(
            mode=OwnershipMode.SERVICE,
            lifecycle_owner="runner",
            tree_guard_kind="job-object",
            tree_guard=b.guard,
        )
        runtime = build_runtime(
            "term_wb_assembly",
            b,
            ownership=policy,
            identity=b.identity,
            identity_probe=b.probe,  # r3：DEAD 只来自 spawn 时同一 retained handle
            eof_grace=1.0,
        )
        runtime.start(rows=30, cols=100, gate=b.gate)
        assert runtime.state is RuntimeState.RUNNING

        info = runtime.wait_exit(20.0)
        assert info.process_exit_seen is True
        assert info.code == 7

        page = runtime.read_from(0)
        text = b"".join(chunk.data for chunk in page.chunks).decode("utf-8", "replace")
        assert "中文尾输出 ✓ tail-marker" in text

        report = runtime.close(reason="test-assembly")
        assert report.ok is True
        assert report.reader_converged is True
        assert runtime.state is RuntimeState.EXITED
        emit_evidence(
            "backend-runtime-assembly",
            {
                "test": "runtime_assembly_tail_cjk_exit7",
                "exit": info.as_dict(),
                "cleanup": report.as_dict(),
                "gate": b.spawn_evidence.as_dict() if b.spawn_evidence else None,
            },
        )
    finally:
        stop_backend(b)


def test_exit_code_259_real_signaled(tmp_path):
    """真实退出码 259：alive=False（signaled 证据）且 exit_code 提供 259。"""
    b = ConPtyBackend.spawn([PYTHON, "-c", "import sys; sys.exit(259)"], cwd=str(tmp_path))
    try:
        assert b.wait_dead(10.0) is True
        assert b.alive() is False
        assert b.exit_code() == 259
        report = b.close()
        assert report["closed"] is True
        emit_evidence(
            "backend-exit-259",
            {"test": "exit_code_259_real_signaled", "exit_code": 259, "alive": False},
        )
    finally:
        stop_backend(b)


def test_resize_reaches_child(tmp_path):
    """resize 到达真实子控制台尺寸（100x30 -> 132x43）。"""
    child = write_script(
        tmp_path,
        "resize_child.py",
        "import sys, ctypes, struct\n"
        "k = ctypes.windll.kernel32\n"
        "h = k.GetStdHandle(-11)\n"
        "def size():\n"
        "    b = ctypes.create_string_buffer(22)\n"
        "    k.GetConsoleScreenBufferInfo(h, b)\n"
        "    return struct.unpack('<hh', b[:4])\n"
        "while True:\n"
        "    c, r = size()\n"
        "    print('SIZE %dx%d' % (c, r), flush=True)\n"
        "    line = sys.stdin.readline()\n"
        "    if not line or line.strip() == 'quit':\n"
        "        break\n",
    )
    b = ConPtyBackend.spawn([PYTHON, child], cwd=str(tmp_path), rows=30, cols=100)
    try:
        buf, why = read_until(b, b"SIZE 100x30")
        assert why == "found", buf[-200:]
        b.resize(rows=43, cols=132)
        b.write(b"\r")
        buf2, why2 = read_until(b, b"SIZE 132x43")
        assert why2 == "found", buf2[-200:]
        emit_evidence(
            "backend-resize",
            {"test": "resize_reaches_child", "initial": "100x30", "resized": "132x43"},
        )
    finally:
        stop_backend(b)


def test_ctrl_c_input_channel_and_survival(tmp_path):
    """Ctrl-C 基本（本机实测边界）：

    - ``\\x03`` 注入到输入通道：阻塞在 ReadConsole 的读取被释放
      （子进程观察到 INPUT-RELEASED）；
    - 通道随后仍可用（PING 往返）；
    - **未证实** OS 级 CTRL_C_EVENT（本机 build 26200：对未阻塞读输入的进程
      无效；win32-input-mode 序列同样无效）——不得据此宣称已实现 Ctrl-C 语义。
    """
    child = write_script(
        tmp_path,
        "ctrl_c_child.py",
        "import sys\n"
        "print('READY', flush=True)\n"
        "while True:\n"
        "    line = sys.stdin.readline()\n"
        "    if line == '':\n"
        "        print('INPUT-RELEASED', flush=True)\n"
        "        continue\n"
        "    if line.strip() == 'quit':\n"
        "        print('QUIT', flush=True)\n"
        "        break\n"
        "    print('LINE ' + line.strip(), flush=True)\n",
    )
    b = ConPtyBackend.spawn([PYTHON, child], cwd=str(tmp_path), rows=30, cols=100)
    try:
        buf, why = read_until(b, b"READY")
        assert why == "found"
        b.write(b"\x03")
        buf2, why2 = read_until(b, b"INPUT-RELEASED")
        assert why2 == "found", buf2[-200:]
        b.write(b"PING\r\n")
        buf3, why3 = read_until(b, b"LINE PING")
        assert why3 == "found", buf3[-200:]
        emit_evidence(
            "backend-ctrl-c",
            {
                "test": "ctrl_c_input_channel_and_survival",
                "observed": "0x03 releases a pending console read; channel stays usable",
                "os_level_ctrl_c_event": "not verified (measured ineffective on build 26200)",
            },
        )
    finally:
        stop_backend(b)


def test_large_output_bounded_log(tmp_path):
    """>=1MiB 输出经 runtime：日志（客户端窗口）有界、PTY 不阻塞、reader 收敛。"""
    child = write_script(
        tmp_path,
        "bigout_child.py",
        "import sys\n"
        "for i in range(3000):\n"
        "    sys.stdout.write('LINE-%05d ' % i + 'y' * 400 + '\\n')\n"
        "sys.stdout.flush()\n",
    )
    b = ConPtyBackend.spawn([PYTHON, child], cwd=str(tmp_path), rows=30, cols=100)
    try:
        policy = OwnershipPolicy(
            mode=OwnershipMode.SERVICE,
            lifecycle_owner="runner",
            tree_guard_kind="job-object",
            tree_guard=b.guard,
        )
        runtime = build_runtime(
            "term_wb_bigout",
            b,
            ownership=policy,
            identity=b.identity,
            identity_probe=b.probe,  # r3：DEAD 只来自 spawn 时同一 retained handle
            eof_grace=1.0,
            output_cap=CAP,
        )
        runtime.start(rows=30, cols=100, gate=b.gate)
        info = runtime.wait_exit(60.0)
        assert info.process_exit_seen is True
        runtime.wait_eof(5.0)
        assert runtime.log.total_bytes >= 1_000_000
        assert runtime.log.retained_bytes <= CAP
        report = runtime.close(reason="test-bigout")
        assert report.ok is True
        emit_evidence(
            "backend-bigout",
            {
                "test": "large_output_bounded_log",
                "total_bytes": runtime.log.total_bytes,
                "retained_bytes": runtime.log.retained_bytes,
                "cap": CAP,
                "close": report.as_dict(),
            },
        )
    finally:
        stop_backend(b)


def test_natural_exit_is_not_eof(tmp_path):
    """诚实性：子进程自然退出后输出通道不 EOF；read 返回 b"" 而非 EOFError。"""
    b = ConPtyBackend.spawn(
        [PYTHON, "-c", "import sys; print('bye', flush=True); sys.exit(3)"],
        cwd=str(tmp_path),
    )
    try:
        assert b.wait_dead(10.0) is True
        time.sleep(0.4)  # 越过静默窗口
        text = b""
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            chunk = b.read(65536, timeout=0.3)  # 空读 = 此刻没有更多数据（非 EOF）
            if not chunk:
                break
            text += chunk
        assert b"bye" in text
        assert b.read(65536, timeout=0.3) == b""
        report = b.close()
        assert report["closed"] is True
        emit_evidence(
            "backend-natural-exit",
            {"test": "natural_exit_is_not_eof", "empty_read": True, "eof_raised": False},
        )
    finally:
        stop_backend(b)


def test_real_eof_only_after_peer_close(tmp_path):
    """真实 EOF 只在对端断链：关闭伪控制台后 read 抛 EOFError（本机实测）。"""
    b = ConPtyBackend.spawn(sleep_child(), cwd=str(tmp_path))
    try:
        assert b.alive() is True
        # 先排空当前缓冲（pty 初始序列），让断言只针对“关闭后”的新事件
        drain_deadline = time.monotonic() + 3.0
        while time.monotonic() < drain_deadline:
            if not b.read(65536, timeout=0.2):
                break
        assert b._spawn.close_pseudo_console(2.0) is True
        eof_raised = False
        deadline = time.monotonic() + 6.0
        while time.monotonic() < deadline:
            try:
                b.read(65536, timeout=0.3)
            except EOFError:
                eof_raised = True
                break
        assert eof_raised, (
            "关闭伪控制台后必须出现真实 EOF（EOFError）："
            f"drain_eof={b._drain_eof} drain_error={b._drain_error!r} "
            f"pump_alive={b._reader_thread.is_alive()} buf={len(b._buf)} "
            f"proc={identity.wait_state(b._spawn.h_process).value}"
        )
        b.wait_dead(8.0)  # HPCON 关闭会终止附着客户端（官方语义）
        report = b.close()
        assert report["closed"] is True
        emit_evidence(
            "backend-real-eof",
            {"test": "real_eof_only_after_peer_close", "eof_error": eof_raised},
        )
    finally:
        stop_backend(b)


def test_input_blocking_close_concurrency(tmp_path):
    """输入管道被节流堵塞时并发 close：close 有界完成、writer 被取消收敛。"""
    b = ConPtyBackend.spawn(
        sleep_child(), cwd=str(tmp_path), write_budget=60.0
    )
    state: dict = {"done": threading.Event(), "result": None}

    def writer() -> None:
        t0 = time.perf_counter()
        try:
            written = b.write(b"x" * (64 * 1024 * 1024))
            state["result"] = ("ok", written)
        except BackendClosedError as exc:
            state["result"] = ("BackendClosedError", getattr(exc, "written", None))
        except Exception as exc:  # noqa: BLE001
            state["result"] = (type(exc).__name__, str(exc)[:80])
        state["seconds"] = round(time.perf_counter() - t0, 3)
        state["done"].set()

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    try:
        time.sleep(1.0)
        assert thread.is_alive(), "大输入应在验证窗口内仍被 conhost 节流（未完成）"
        b.terminate(True)
        assert b.wait_dead(5.0) is True
        t0 = time.perf_counter()
        report = b.close()
        elapsed = time.perf_counter() - t0
        assert report["closed"] is True
        assert elapsed < 5.0
        assert state["done"].wait(5.0) is True
        kind, written = state["result"][0], state["result"][1]
        assert kind == "BackendClosedError", state["result"]
        assert written is None or written > 0
        thread.join(2.0)
        emit_evidence(
            "backend-write-close",
            {
                "test": "input_blocking_close_concurrency",
                "close_seconds": round(elapsed, 3),
                "writer_result": state["result"],
                "writer_seconds": state.get("seconds"),
            },
        )
    finally:
        stop_backend(b)


def test_read_cancel_join_on_close(tmp_path):
    """阻塞中的 read 被 close 取消：CancelSynchronousIo + stop/join 收敛。"""
    b = ConPtyBackend.spawn(sleep_child(), cwd=str(tmp_path))
    state: dict = {"done": threading.Event(), "exc": None, "blocked": threading.Event()}

    def reader() -> None:
        try:
            while True:
                chunk = b.read(65536, timeout=0.2)
                if chunk:
                    continue  # 先排空 pty 初始序列
                state["blocked"].set()
                try:
                    b.read(65536)  # 无 timeout 的阻塞读（close 的取消目标）
                except BackendClosedError:
                    raise
                # 进程死亡后的软空读（b""）：短睡后再试，直到 close 取消
                time.sleep(0.02)
        except BackendClosedError:
            state["exc"] = "BackendClosedError"
        except Exception as exc:  # noqa: BLE001
            state["exc"] = f"{type(exc).__name__}: {exc}"
        state["done"].set()

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    try:
        assert state["blocked"].wait(5.0), "reader 应进入阻塞读"
        assert thread.is_alive()
        b.terminate(True)
        assert b.wait_dead(5.0) is True
        report = b.close()
        assert report["closed"] is True
        assert report["reader"].get("thread_exited") is True or report["reader"].get(
            "reader"
        ) in ("already-exited", "not-started")
        assert state["done"].wait(3.0) is True
        assert state["exc"] == "BackendClosedError", state["exc"]
        thread.join(2.0)
        emit_evidence(
            "backend-read-cancel",
            {
                "test": "read_cancel_join_on_close",
                "reader": report["reader"],
            },
        )
    finally:
        stop_backend(b)


def test_probe_dead_bound_to_retained_handle(tmp_path):
    """r3 §13.4：DEAD 只来自 spawn 时同一 retained handle；陌生 PID 一律 UNKNOWN。"""
    b = ConPtyBackend.spawn(sleep_child(), cwd=str(tmp_path))
    try:
        probe = b.probe(b.pid)
        assert probe.status is ProcessStatus.ALIVE and probe.identity is not None
        # 陌生 PID：不现查（系统进程与任意 pid 都必须是 UNKNOWN）
        assert b.probe(4).status is ProcessStatus.UNKNOWN
        assert b.probe(int(b.pid) + 1).status is ProcessStatus.UNKNOWN
        b.terminate(True)
        assert b.wait_dead(5.0) is True
        assert b.probe(b.pid).status is ProcessStatus.DEAD  # retained handle signaled
        report = b.close()
        assert report["closed"] is True
        # 释放后：缓存自 retained/Job 证据 -> 仍 DEAD；fresh PID 探针 -> UNKNOWN
        assert b.probe(b.pid).status is ProcessStatus.DEAD
        assert identity.probe_process(b.pid).status is ProcessStatus.UNKNOWN
        emit_evidence(
            "backend-probe-binding",
            {
                "test": "probe_dead_bound_to_retained_handle",
                "stranger_pid": "UNKNOWN",
                "retained_after_release": "DEAD (cached from retained/Job evidence)",
                "fresh_pid_probe": "UNKNOWN (never DEAD)",
            },
        )
    finally:
        stop_backend(b)


def test_blocked_read_does_not_block_write_terminate_close(tmp_path):
    """r3 句柄级并发：阻塞的 read 不持全局锁，write/terminate/close 全部有界。"""
    b = ConPtyBackend.spawn(sleep_child(), cwd=str(tmp_path))
    state: dict = {"blocked": threading.Event(), "done": threading.Event(), "exc": None}

    def reader() -> None:
        try:
            while True:
                chunk = b.read(65536, timeout=0.2)
                if chunk:
                    continue  # 先排空 pty 初始序列
                state["blocked"].set()
                try:
                    b.read(65536)  # 阻塞读（无 timeout）
                except BackendClosedError:
                    raise
                time.sleep(0.02)  # 进程死亡后的软空读：等 close 取消
        except BackendClosedError:
            state["exc"] = "BackendClosedError"
        except Exception as exc:  # noqa: BLE001
            state["exc"] = f"{type(exc).__name__}: {exc}"
        state["done"].set()

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    try:
        assert state["blocked"].wait(5.0), "reader 应进入阻塞读"
        # 1) write 不被阻塞的 read 拖住
        t0 = time.perf_counter()
        assert b.write(b"\r") == 1
        assert time.perf_counter() - t0 < 1.0
        # 2) alive / 身份探针为快速内核查询
        t0 = time.perf_counter()
        assert b.alive() is True
        assert b.probe(b.pid).status is ProcessStatus.ALIVE
        assert time.perf_counter() - t0 < 0.5
        # 3) terminate 有界
        t0 = time.perf_counter()
        b.terminate(True)
        assert time.perf_counter() - t0 < 3.0
        assert b.wait_dead(5.0) is True
        # 4) close 有界：取消 / join 阻塞读并完成释放
        t0 = time.perf_counter()
        report = b.close()
        elapsed = time.perf_counter() - t0
        assert report["closed"] is True and elapsed < 5.0
        assert report["reader"].get("thread_exited") is True or report["reader"].get(
            "reader"
        ) in ("already-exited", "not-started")
        assert state["done"].wait(3.0) is True
        assert state["exc"] == "BackendClosedError", state["exc"]
        thread.join(2.0)
        emit_evidence(
            "backend-read-nonblocking",
            {
                "test": "blocked_read_does_not_block_write_terminate_close",
                "close_seconds": round(elapsed, 3),
                "reader": report["reader"],
            },
        )
    finally:
        stop_backend(b)
        thread.join(2.0)


def test_write_budget_bounds_blocked_pipe(tmp_path):
    """写预算强制点：单次被堵塞 WriteFile 也不得超预算；预算耗尽返回 partial。"""
    b = ConPtyBackend.spawn(sleep_child(), cwd=str(tmp_path), write_budget=0.6)
    try:
        # 预算 0：立即返回 partial(0)，不抛、不无限等
        t0 = time.perf_counter()
        assert b.write(b"x" * 4096, timeout=0.0) == 0
        assert time.perf_counter() - t0 < 0.5
        # 大输入被 conhost 节流：必须在上界内返回（watchdog 生效；partial 合法）
        t0 = time.perf_counter()
        written = b.write(b"x" * (32 * 1024 * 1024))
        elapsed = time.perf_counter() - t0
        assert elapsed < 3.0, f"write 超出预算上界（watchdog 未生效？）：{elapsed:.2f}s"
        assert 0 <= written <= 32 * 1024 * 1024
        b.terminate(True)
        assert b.wait_dead(5.0) is True
        t0 = time.perf_counter()
        report = b.close()
        assert report["closed"] is True and time.perf_counter() - t0 < 5.0
        emit_evidence(
            "backend-write-budget",
            {
                "test": "write_budget_bounds_blocked_pipe",
                "budget_s": 0.6,
                "write_seconds": round(elapsed, 3),
                "written": written,
                "payload": 32 * 1024 * 1024,
            },
        )
    finally:
        stop_backend(b)


def test_interrupt_write_bounded_when_input_busy(tmp_path):
    """runtime.close interrupt 阶段：输入锁被在途写占用时 terminate(False) 仍自身有界。"""
    b = ConPtyBackend.spawn(sleep_child(), cwd=str(tmp_path), write_budget=30.0)
    state: dict = {"done": threading.Event(), "result": None}

    def writer() -> None:
        try:
            state["result"] = ("ok", b.write(b"x" * (64 * 1024 * 1024)))
        except Exception as exc:  # noqa: BLE001
            state["result"] = (type(exc).__name__, getattr(exc, "written", None))
        state["done"].set()

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    try:
        time.sleep(0.8)
        assert thread.is_alive(), "writer 应在途（持串行锁 + 被 conhost 节流）"
        t0 = time.perf_counter()
        b.terminate(False)  # interrupt：\x03 使用短预算 + 有界锁获取
        elapsed = time.perf_counter() - t0
        assert elapsed < 1.5, f"interrupt 路径被在途写拖住：{elapsed:.2f}s"
        b.terminate(True)
        assert b.wait_dead(5.0) is True
        t0 = time.perf_counter()
        report = b.close()
        assert report["closed"] is True and time.perf_counter() - t0 < 5.0
        assert state["done"].wait(5.0) is True
        thread.join(2.0)
        emit_evidence(
            "backend-interrupt-bounded",
            {
                "test": "interrupt_write_bounded_when_input_busy",
                "interrupt_seconds": round(elapsed, 3),
                "writer_result": str(state["result"]),
            },
        )
    finally:
        stop_backend(b)
        thread.join(2.0)


def test_close_failure_retry_deadline(tmp_path):
    """清理失败重试（真实条件）：读者收敛预算用尽 -> 抛错保留 owner -> 重试成功。"""
    b = ConPtyBackend.spawn(sleep_child(), cwd=str(tmp_path))
    try:
        with pytest.raises(BackendCloseError) as excinfo:
            b.close(reader_wait_timeout=0.0)
        assert excinfo.value.retryable is True
        assert excinfo.value.report["retained"], "失败必须保留 owned 资源清单"
        assert b.closed is False  # 不谎报 closed
        b.terminate(True)
        assert b.wait_dead(5.0) is True
        report = b.close()
        assert report["closed"] is True
        assert b.closed is True
        emit_evidence(
            "backend-close-retry",
            {
                "test": "close_failure_retry_deadline",
                "first_retained": excinfo.value.report["retained"],
                "retry": {k: report[k] for k in ("closed", "already_closed")},
            },
        )
    finally:
        stop_backend(b)


def test_close_failure_retry_injected_release(tmp_path):
    """清理失败重试（注入）：单资源释放失败 -> 保留其余 -> 重试全部成功。"""
    b = ConPtyBackend.spawn(sleep_child(), cwd=str(tmp_path))
    try:
        b.terminate(True)
        assert b.wait_dead(5.0) is True
        b.inject_release_failure_for_test("output_read")
        with pytest.raises(BackendCloseError) as excinfo:
            b.close()
        release = excinfo.value.report["release"]
        assert release["closed"] is False
        assert "output_read" in release["retained"]
        # 失败不牵连其余：pseudo_console 等仍在 retained 清单中
        assert "hpc" in release["retained"] and "h_process" in release["retained"]
        assert b.closed is False
        report = b.close()
        assert report["closed"] is True
        assert "output_read" in report["release"]["released"]
        emit_evidence(
            "backend-close-retry-injected",
            {
                "test": "close_failure_retry_injected_release",
                "first": {
                    "closed": release["closed"],
                    "retained": release["retained"],
                    "errors": release["errors"],
                },
                "second_released": report["release"]["released"],
            },
        )
    finally:
        stop_backend(b)


def test_close_idempotent_and_probe_failclosed(tmp_path):
    """重复 close 幂等；死亡已证后 alive=False；无证据时 alive 抛错（不冒充 dead）。"""
    b = ConPtyBackend.spawn(
        [PYTHON, "-c", "import sys; print('x', flush=True); sys.exit(0)"],
        cwd=str(tmp_path),
    )
    try:
        assert b.wait_dead(10.0) is True
        first = b.close()
        assert first["closed"] is True and first["already_closed"] is False
        second = b.close()
        assert second["closed"] is True and second["already_closed"] is True
        assert b.alive() is False  # 释放前已确认 signaled：事实而非冒充
        # 无证据分支（白盒）：句柄清零且死亡证据清零 -> 必须抛 BackendProbeError
        saved = b._spawn.h_process
        saved_confirmed = b._spawn.death_confirmed
        b._spawn.h_process = 0
        b._spawn.death_confirmed = False
        b._dead_proven = False
        b._closed = False
        try:
            with pytest.raises(BackendProbeError):
                b.alive()
        finally:
            b._spawn.h_process = saved
            b._spawn.death_confirmed = saved_confirmed
            b._closed = True
        emit_evidence(
            "backend-close-idempotent",
            {"test": "close_idempotent_and_probe_failclosed", "idempotent": True},
        )
    finally:
        stop_backend(b)


def test_handle_count_regression(tmp_path):
    """句柄计数回归：spawn -> terminate -> close 循环不累积泄漏。"""
    def one_cycle() -> None:
        b = ConPtyBackend.spawn(sleep_child(30), cwd=str(tmp_path), rows=24, cols=80)
        b.terminate(True)
        assert b.wait_dead(5.0) is True
        report = b.close()
        assert report["closed"] is True

    one_cycle()  # 预热（RL/DLL 一次性句柄）
    baseline = get_process_handle_count()
    assert isinstance(baseline, int)
    for _ in range(3):
        one_cycle()
    after = get_process_handle_count()
    assert after - baseline <= 2, f"句柄计数增长 {after - baseline}（baseline={baseline}, after={after}）"
    emit_evidence(
        "backend-handle-count",
        {"test": "handle_count_regression", "baseline": baseline, "after": after, "delta": after - baseline},
    )


def test_non_windows_and_winpty_boundaries(monkeypatch):
    """非 Windows 构造明确 BackendUnavailableError；WinptyBackend 非生产依赖。"""
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(BackendUnavailableError):
        ConPtyBackend.spawn([PYTHON, "-c", "pass"])
    with pytest.raises(BackendUnavailableError):
        JobObjectGuard()
    monkeypatch.undo()
    with pytest.raises(BackendUnavailableError):
        WinptyBackend()
    emit_evidence(
        "backend-boundaries",
        {
            "test": "non_windows_and_winpty_boundaries",
            "non_windows": "BackendUnavailableError",
            "winpty": "not-a-production-dependency",
        },
    )
