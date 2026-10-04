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
    BackendBusyError,
    BackendCloseError,
    BackendClosedError,
    BackendProbeError,
    BackendPumpError,
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


def test_pump_buffer_bound_cap_plus_block(tmp_path):
    """缓冲口径（审查 round-2 probe4）：pump 缓冲边界 = ``cap + 单块（≤ read_size）``，
    **不是**严格 ``≤cap``；暂停（背压）期间不丢数据，消费恢复后全量到达。"""
    cap = 32 * 1024
    read_size = 64 * 1024  # 最大界 = 98304（审查同口径 32768+65536）
    lines = 3000
    child = write_script(
        tmp_path,
        "burst_child.py",
        "import sys\n"
        f"for i in range({lines}):\n"
        "    sys.stdout.write('B%05d ' % i + 'z' * 40 + '\\n')\n"
        "sys.stdout.write('ZEND\\n')\n"
        "sys.stdout.flush()\n",
    )
    b = ConPtyBackend.spawn(
        [PYTHON, child], cwd=str(tmp_path), buffer_cap=cap, read_size=read_size
    )
    try:
        peak = 0
        saw_overflow = False
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:  # 不消费：等 pump 暂停在块级峰值
            with b._cv:
                peak = max(peak, len(b._buf))
                if b._drain_done:
                    break
            if peak > cap:
                saw_overflow = True
                break
            time.sleep(0.01)
        assert saw_overflow, f"未观察到超过 cap 的块级峰值（peak={peak}）"
        t0 = time.monotonic()
        while time.monotonic() - t0 < 0.5:  # 暂停期间持续采样，确认不越界
            with b._cv:
                peak = max(peak, len(b._buf))
            time.sleep(0.01)
        assert peak <= cap + read_size, f"缓冲超出 cap+read_size：{peak} > {cap + read_size}"
        text = b""
        deadline = time.monotonic() + 25.0
        while time.monotonic() < deadline and b"ZEND" not in text:
            chunk = b.read(65536, timeout=0.3)
            if chunk:
                text += chunk
        assert b"ZEND" in text, text[-200:]
        assert text.count(b"B") >= lines  # 逐行完整，无丢失
        b.terminate(True)
        assert b.wait_dead(5.0) is True
        report = b.close()
        assert report["closed"] is True
        emit_evidence(
            "backend-pump-buffer-bound",
            {
                "test": "pump_buffer_bound_cap_plus_block",
                "cap": cap,
                "read_size": read_size,
                "observed_peak": peak,
                "bound_max": cap + read_size,
                "lines_expected": lines,
                "lines_seen_B": text.count(b"B"),
                "not_strict_cap": peak > cap,
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
        # R5：reader/pump 诊断显式（stop/cancel/error/EOF 可区分）
        reader = report["reader"]
        assert reader.get("stop_requested") is True
        assert reader.get("pump_exit") in ("cancelled", "stopped-requested", "eof")
        assert reader.get("drain_done") is True
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
        # R10：runtime interrupt 阶段直接 `write(b"\x03")`——必须与 terminate(False) 同预算
        # （0.5s 而非默认 1.5s）；锁竞争下以 BackendBusyError 有界返回。
        t0 = time.perf_counter()
        with pytest.raises(BackendBusyError):
            b.write(b"\x03")
        runtime_elapsed = time.perf_counter() - t0
        assert runtime_elapsed < 1.0, f"runtime interrupt 路径未按 0.5s 预算有界：{runtime_elapsed:.2f}s"
        t0 = time.perf_counter()
        b.terminate(False)  # interrupt：\x03 使用短预算 + 有界锁获取
        elapsed = time.perf_counter() - t0
        assert elapsed < 1.0, f"interrupt 路径被在途写拖住：{elapsed:.2f}s"
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
                "runtime_write_interrupt_seconds": round(runtime_elapsed, 3),
                "terminate_false_seconds": round(elapsed, 3),
                "note": "有界 ≠ 中断有效：Ctrl-C OS 语义负结论不变（报告 §2.6.1）",
                "writer_result": str(state["result"]),
            },
        )
    finally:
        stop_backend(b)
        thread.join(2.0)


def test_close_refused_on_live_backend_io_survives(tmp_path):
    """R3：close 先核验后拒绝（活进程），且**不破坏 IO**——read/write/resize 继续可用。"""
    child = write_script(
        tmp_path,
        "echo_child.py",
        "import sys\n"
        "print('ALIVE-1', flush=True)\n"
        "while True:\n"
        "    line = sys.stdin.readline()\n"
        "    if not line:\n"
        "        continue\n"
        "    if line.strip() == 'quit':\n"
        "        break\n"
        "    print('ECHO ' + line.strip(), flush=True)\n",
    )
    b = ConPtyBackend.spawn([PYTHON, child], cwd=str(tmp_path), rows=30, cols=100)
    try:
        buf, why = read_until(b, b"ALIVE-1")
        assert why == "found"
        with pytest.raises(BackendCloseError) as excinfo:
            b.close()
        report = excinfo.value.report
        assert report.get("io_untouched") is True
        assert report.get("process_state") == "alive"
        assert excinfo.value.retryable is True
        assert b.closed is False
        assert b._spawn.input_write, "input_write 不得提前释放"
        assert b._spawn.h_process, "h_process 不得提前释放"
        assert b._reader_thread.is_alive(), "pump 不得被停"
        # IO 继续可用：write 回显、read 继续、resize 正常、进程仍活
        assert b.write(b"ping\r\n") > 0
        buf2, why2 = read_until(b, b"ECHO ping")
        assert why2 == "found", buf2[-160:]
        b.resize(rows=43, cols=132)
        assert b.alive() is True
        b.terminate(True)
        assert b.wait_dead(5.0) is True
        rep = b.close()
        assert rep["closed"] is True
        emit_evidence(
            "backend-close-pregate",
            {
                "test": "close_refused_on_live_backend_io_survives",
                "refusal": {"io_untouched": True, "process_state": "alive"},
                "io_after_refusal": {"write": True, "read": True, "resize": True},
            },
        )
    finally:
        stop_backend(b)


def test_writer_convergence_requires_input_lock(tmp_path):
    """R4 门控负例：持锁未注册的写者存在 -> 收敛失败（先失败）；释放后重试成功（后通过）；
    失败阶段句柄不得提前释放。"""
    b = ConPtyBackend.spawn(sleep_child(), cwd=str(tmp_path))
    holder_started = threading.Event()
    holder_release = threading.Event()

    def _hold() -> None:
        with b._input_lock:
            holder_started.set()
            holder_release.wait(5.0)

    holder = threading.Thread(target=_hold, daemon=True)
    try:
        b.terminate(True)
        assert b.wait_dead(5.0) is True
        holder.start()
        assert holder_started.wait(5.0)
        got = b._input_lock.acquire(blocking=False)
        if got:
            b._input_lock.release()
        assert got is False, "门控必须确证持有输入锁（未注册）"
        with pytest.raises(BackendCloseError) as excinfo:
            b.close(writer_wait_timeout=0.3)
        writers = excinfo.value.report.get("writers") or {}
        assert writers.get("input_lock_acquired") is False, excinfo.value.report
        assert b.closed is False
        assert b._spawn.input_write, "失败阶段不得提前释放 input_write"
        assert b._spawn.h_process, "失败阶段不得提前释放 h_process"
        # 释放门控 -> 重试收敛
        holder_release.set()
        holder.join(3.0)
        report = b.close()
        assert report["closed"] is True
        assert report["writers"].get("input_lock_acquired") is True
        emit_evidence(
            "backend-writer-convergence-gate",
            {
                "test": "writer_convergence_requires_input_lock",
                "first": {"input_lock_acquired": False, "closed": False, "retained_input_write": True},
                "retry": {"input_lock_acquired": True, "closed": True},
            },
        )
    finally:
        holder_release.set()
        holder.join(2.0)
        stop_backend(b)


def test_write_timer_gap_canceller_retries(tmp_path):
    """R4/MA：预算 timer 在“检查后、WriteFile 前”落空也必须被**重试取消**——
    写不得超出预算进入无界阻塞（复现脚本见 audit/.../repro/repro_write_timer_gap.py）。"""
    budget = 0.3
    payload = 64 * 1024 * 1024
    b = ConPtyBackend.spawn(
        sleep_child(), cwd=str(tmp_path), write_budget=budget, write_chunk=payload
    )
    state: dict = {"done": threading.Event(), "result": None}
    real_write_raw = b._spawn.write_raw

    def gated_write_raw(chunk: bytes) -> int:
        if not state.get("gated"):
            state["gated"] = True
            time.sleep(0.9)  # > 预算：让 timer 在“无 I/O 挂起”时触发（落空窗口）
        return real_write_raw(chunk)

    b._spawn.write_raw = gated_write_raw  # type: ignore[method-assign]

    def writer() -> None:
        try:
            state["result"] = ("ok", b.write(b"x" * payload))
        except Exception as exc:  # noqa: BLE001
            state["result"] = (type(exc).__name__, getattr(exc, "written", None))
        state["done"].set()

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    try:
        t0 = time.perf_counter()
        finished = state["done"].wait(5.0)
        elapsed = time.perf_counter() - t0
        assert finished, "写超出预算进入无界阻塞：取消者未重试（R4）"
        assert elapsed < 4.0, f"写入返回过慢：{elapsed:.2f}s"
        b.terminate(True)
        assert b.wait_dead(5.0) is True
        report = b.close()
        assert report["closed"] is True
        assert state["result"][0] in ("ok", "BackendClosedError"), state["result"]
        emit_evidence(
            "backend-write-timer-gap",
            {
                "test": "write_timer_gap_canceller_retries",
                "budget_s": budget,
                "gate_sleep_s": 0.9,
                "finished_within_5s": True,
                "writer_result": str(state["result"]),
            },
        )
    finally:
        stop_backend(b)
        thread.join(2.0)


def test_write_budget_includes_lock_wait(tmp_path):
    """R9（审查 round-2 口径）：预算**含锁等待**——锁等待吃掉预算后，写阶段不得重新获得整份预算。

    审查实测旧实现：锁等待 1.4s + 全额 timer 1.5s ≈ 总 2.867s（≈2×budget，> runtime
    ``input_drain_timeout`` 2.0s）；修复后总上界 ≈ budget（1.5s）。本用例先失败（旧实现
    ~2.9s）后通过（<1.9s）。
    """
    b = ConPtyBackend.spawn(
        sleep_child(),
        cwd=str(tmp_path),
        write_budget=1.5,
        write_chunk=16 * 1024 * 1024,  # 单块阻塞写：只有取消能让它按时返回（审查 probe7-B 同款）
    )
    holder_started = threading.Event()
    holder_release = threading.Event()

    def _hold() -> None:
        with b._input_lock:
            holder_started.set()
            holder_release.wait(5.0)

    holder = threading.Thread(target=_hold, daemon=True)
    holder.start()
    state: dict = {"done": threading.Event(), "result": None, "seconds": None}

    def writer() -> None:
        t0 = time.perf_counter()
        try:
            state["result"] = b.write(b"x" * (16 * 1024 * 1024))
        except Exception as exc:  # noqa: BLE001
            state["result"] = (type(exc).__name__, getattr(exc, "written", None))
        state["seconds"] = round(time.perf_counter() - t0, 3)
        state["done"].set()

    thread = threading.Thread(target=writer, daemon=True)
    try:
        assert holder_started.wait(5.0)
        thread.start()
        time.sleep(1.4)  # 锁等待 1.4s（预算 1.5s）
        holder_release.set()
        holder.join(3.0)
        assert state["done"].wait(5.0) is True
        # 含锁等待的预算：总时长 ≈ 1.5s（旧实现会拿到第二份预算 -> ~2.9s）
        assert state["seconds"] is not None and state["seconds"] < 1.9, state
        b.terminate(True)
        assert b.wait_dead(5.0) is True
        report = b.close()
        assert report["closed"] is True
        emit_evidence(
            "backend-write-budget-lockwait",
            {
                "test": "write_budget_includes_lock_wait",
                "budget_s": 1.5,
                "lock_wait_s": 1.4,
                "write_seconds": state["seconds"],
                "old_impl_reference_s": 2.867,
                "result": str(state["result"]),
            },
        )
    finally:
        holder_release.set()
        holder.join(2.0)
        stop_backend(b)
        thread.join(2.0)


def test_write_cancel_worker_joined_before_handle_close(tmp_path, monkeypatch):
    """R8（审查 round-2）：取消 worker 必须**先真 join**，再释放 writer 的线程句柄——
    陈旧句柄不得被回调用（审查实测旧实现回调在句柄关闭后发起 CancelSynchronousIo ->
    ERROR_INVALID_HANDLE(6)）。

    确定性构造：预算 0.02s + 8MiB 节流写 -> timer 在写中途触发；把 cancel 原语替换为
    “先睡 0.35s、再执行真实取消”的观察版；断言：
    (a) 真实取消执行时句柄仍有效（err != 6）；
    (b) writer 的 thread handle 关闭时间 >= 取消 worker 的最后返回时间。
    """
    import packages.core.terminal.backend as backend_module

    real_cancel = backend_module.cancel_synchronous_io
    real_close = backend_module.close_handle_checked
    record: dict = {"cancel_enter": None, "cancel_leave": None, "cancel_err": None, "closes": []}
    armed = {"on": True}

    def slow_cancel(handle):
        if armed["on"] and record["cancel_enter"] is None:
            record["cancel_enter"] = time.monotonic()
            time.sleep(0.35)
            ok, err = real_cancel(handle)
            record["cancel_err"] = err
            record["cancel_leave"] = time.monotonic()
            return ok, err
        return real_cancel(handle)

    def spy_close(handle):
        record["closes"].append(time.monotonic())
        return real_close(handle)

    monkeypatch.setattr(backend_module, "cancel_synchronous_io", slow_cancel)
    monkeypatch.setattr(backend_module, "close_handle_checked", spy_close)
    # 子进程持续 drain stdin：写会在回调醒来（0.35s）之前自然完成（预算 0.02s 到期即 partial），
    # 从而在旧实现上确定性产生“先关句柄、后回调取消（陈旧句柄 -> err=6）”。
    drain_child = write_script(
        tmp_path,
        "drain_child.py",
        "import sys\n"
        "while True:\n"
        "    data = sys.stdin.buffer.read(65536)\n"
        "    if not data:\n"
        "        break\n",
    )
    b = ConPtyBackend.spawn([PYTHON, drain_child], cwd=str(tmp_path), write_budget=0.02)
    try:
        written = b.write(b"x" * (8 * 1024 * 1024))
        assert record["cancel_enter"] is not None, "预算 timer 必须触发取消 worker"
        # 有界等待取消 worker 返回（旧实现会在句柄关闭后仍发起真实取消 -> err=6）
        wait_deadline = time.monotonic() + 3.0
        while record["cancel_leave"] is None and time.monotonic() < wait_deadline:
            time.sleep(0.02)
        assert record["cancel_leave"] is not None, "取消 worker 未在有界时间内返回"
        assert record["cancel_err"] != 6, (
            f"取消执行时句柄已被关闭（陈旧句柄，R8）：err={record['cancel_err']}"
        )
        closes_after = [t for t in record["closes"] if t >= record["cancel_enter"]]
        assert closes_after, "writer 线程句柄必须被关闭（或转 orphan 保留）"
        assert min(closes_after) >= record["cancel_leave"] - 1e-6, (
            "句柄在取消 worker 返回前被关闭（R8 竞态）："
            f"first_close={min(closes_after):.4f} cancel_leave={record['cancel_leave']:.4f}"
        )
        assert 0 <= written <= 8 * 1024 * 1024
        b.terminate(True)
        assert b.wait_dead(5.0) is True
        report = b.close()
        assert report["closed"] is True
        emit_evidence(
            "backend-cancel-join",
            {
                "test": "write_cancel_worker_joined_before_handle_close",
                "cancel_err": record["cancel_err"],
                "handle_closed_after_cancel_return": True,
                "written": written,
            },
        )
    finally:
        armed["on"] = False
        stop_backend(b)


def test_runtime_assembly_requires_retained_probe(tmp_path):
    """R11（审查 round-2，集成约束）：runtime 装配必须接 ``backend.probe``。

    反例：接 fresh ``identity.probe_process``（对 signaled 的现查句柄只给 UNKNOWN）时，
    根已死会让清理被 identity-unknown **永久拒绝**（owner retained、无法自行收敛）；
    正确接线 ``b.probe``（spawn 时 retained handle）时同一场景收敛 EXITED。
    """
    script = write_script(
        tmp_path, "exit_child.py", "import sys; print('done', flush=True); sys.exit(0)"
    )

    def _policy(backend: ConPtyBackend) -> OwnershipPolicy:
        return OwnershipPolicy(
            mode=OwnershipMode.SERVICE,
            lifecycle_owner="runner",
            tree_guard_kind="job-object",
            tree_guard=backend.guard,
        )

    # 反例：fresh probe 接线 -> 清理被拒（先失败）
    bad = ConPtyBackend.spawn([PYTHON, script], cwd=str(tmp_path))
    try:
        rt_bad = build_runtime(
            "term_r11_bad",
            bad,
            ownership=_policy(bad),
            identity=bad.identity,
            identity_probe=identity.probe_process,  # 错误接线（fresh）
            eof_grace=1.0,
        )
        rt_bad.start(rows=30, cols=100, gate=bad.gate)
        assert rt_bad.wait_exit(15.0).process_exit_seen is True
        rep_bad = rt_bad.close(reason="r11-bad-probe")
        assert rep_bad.ok is False
        assert rep_bad.identity_check.value == "unknown"
        assert rep_bad.owner_retained is True
        assert bad.closed is False
        # 直接 backend.close（进程已死）仍可收敛释放（恢复路径存在）
        assert bad.close()["closed"] is True
    finally:
        stop_backend(bad)

    # 正例：retained 绑定 -> 收敛 EXITED（后通过）
    good = ConPtyBackend.spawn([PYTHON, script], cwd=str(tmp_path))
    try:
        rt_good = build_runtime(
            "term_r11_good",
            good,
            ownership=_policy(good),
            identity=good.identity,
            identity_probe=good.probe,  # 正确接线（spawn 时 retained handle）
            eof_grace=1.0,
        )
        rt_good.start(rows=30, cols=100, gate=good.gate)
        assert rt_good.wait_exit(15.0).process_exit_seen is True
        rep_good = rt_good.close(reason="r11-good-probe")
        assert rep_good.ok is True
        assert rt_good.state is RuntimeState.EXITED
        emit_evidence(
            "backend-probe-assembly",
            {
                "test": "runtime_assembly_requires_retained_probe",
                "fresh_probe_close": {
                    "ok": rep_bad.ok,
                    "identity_check": rep_bad.identity_check.value,
                    "owner_retained": rep_bad.owner_retained,
                },
                "retained_probe_close": {"ok": rep_good.ok, "state": rt_good.state.value},
            },
        )
    finally:
        stop_backend(good)


def test_pump_handle_failure_visible_and_drained(tmp_path, monkeypatch):
    """R6：pump 获取线程句柄失败不得静默死线程——read 抛脱敏错误、pump 诊断显式、
    close 可收敛（R5 字段随报告公布）。"""
    import packages.core.terminal.backend as backend_module

    def boom() -> int:
        raise OSError(6, "injected OpenThread failure")

    monkeypatch.setattr(backend_module, "open_current_thread_handle", boom)
    b = ConPtyBackend.spawn(sleep_child(), cwd=str(tmp_path))
    try:
        with pytest.raises(BackendPumpError) as excinfo:
            b.read(65536, timeout=2.0)
        assert "OSError" in str(excinfo.value)  # 脱敏：只含类型名
        assert b._drain_done is True
        assert b._pump_exit == "fatal:OSError"
        assert b._pump_fatal == "OSError"
        desc = b.describe()
        assert desc["pump_exit"] == "fatal:OSError"
        assert desc["drain_done"] is True
        b.terminate(True)
        assert b.wait_dead(5.0) is True
        report = b.close()
        assert report["closed"] is True
        reader = report["reader"]
        assert reader.get("pump_exit") == "fatal:OSError"
        assert reader.get("pump_fatal") == "OSError"
        # pump 已先于 close 死亡：无需 stop（显式区分，而非静默）
        assert reader.get("stop_requested") is False
        assert reader.get("thread_exited") is True
        emit_evidence(
            "backend-pump-fatal",
            {
                "test": "pump_handle_failure_visible_and_drained",
                "read_error": "BackendPumpError",
                "pump_exit": "fatal:OSError",
                "close": {"closed": True, "reader": reader},
            },
        )
    finally:
        stop_backend(b)


def test_close_refuses_while_cancel_worker_in_flight(tmp_path, monkeypatch):
    """R12 门控（审查 round-3）：取消回调阻塞 > join(1.0s) 时——

    write 可以返回，但必须保留 **(timer, handle) 配对**（可追踪所有权）；
    ``close`` 在释放任何配对句柄前有界 join + ``is_alive`` 真核验，仍活则**拒绝**
    （cleanup-failed / owner retained / 句柄未裸关、无复用）；回调释放后再重试成功。
    """
    import packages.core.terminal.backend as backend_module

    real_cancel = backend_module.cancel_synchronous_io
    real_close = backend_module.close_handle_checked
    gate = threading.Event()
    entered = threading.Event()
    rec: dict = {"first": True, "handle": None, "err_after_release": None, "closed_handles": []}

    def gated_cancel(handle):
        if rec["first"]:
            rec["first"] = False
            rec["handle"] = int(handle)
            entered.set()
            gate.wait(15.0)  # 阻塞远超 join(1.0)
            ok, err = real_cancel(handle)
            rec["err_after_release"] = err
            return ok, err
        return real_cancel(handle)

    def spy_close(handle):
        rec["closed_handles"].append(int(handle))
        return real_close(handle)

    monkeypatch.setattr(backend_module, "cancel_synchronous_io", gated_cancel)
    monkeypatch.setattr(backend_module, "close_handle_checked", spy_close)
    drain = write_script(
        tmp_path,
        "drain_child2.py",
        "import sys\n"
        "while True:\n"
        "    data = sys.stdin.buffer.read(65536)\n"
        "    if not data:\n"
        "        break\n",
    )
    b = ConPtyBackend.spawn([PYTHON, drain], cwd=str(tmp_path), write_budget=0.15)
    real_write_raw = b._spawn.write_raw

    def hold_completed_write_until_cancel(chunk):
        # A successful real write is followed by an explicit test gate. Fast
        # conhost drains must not let the entire write finish before the timer.
        written = real_write_raw(chunk)
        assert entered.wait(3.0), "budget canceller did not enter the gate"
        return written

    monkeypatch.setattr(b._spawn, "write_raw", hold_completed_write_until_cancel)
    try:
        # The test gate, not payload throughput, holds the writer until the
        # canceller is in flight. All subsequent ownership checks remain real.
        b.write(b"x" * (8 * 1024 * 1024))
        assert entered.wait(3.0), "预算 timer 必须触发取消 worker"
        assert rec["handle"] is not None
        workers = b.describe().get("cancel_workers") or []
        assert any(w.get("alive") for w in workers), workers  # 配对保留（可追踪）
        b.terminate(True)
        assert b.wait_dead(5.0) is True
        with pytest.raises(BackendCloseError) as excinfo:
            b.close(writer_wait_timeout=0.5)
        rep = excinfo.value.report
        assert rep.get("retryable") is True
        assert any("cancel_worker" in str(x) for x in rep.get("retained", [])), rep
        assert (rep.get("cancel_workers") or {}).get("thread_exited") is False, rep
        # 取消者 in-flight：绝不裸关配对句柄
        assert rec["handle"] not in rec["closed_handles"], rec
        assert b.closed is False
        # 回调释放 -> 真实取消作用于仍有效的句柄（err != 6 = ERROR_INVALID_HANDLE）
        gate.set()
        deadline = time.monotonic() + 3.0
        while rec["err_after_release"] is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert rec["err_after_release"] is not None
        assert rec["err_after_release"] != 6, "句柄被提前释放/复用（R12）"
        report = b.close()
        assert report["closed"] is True
        assert (report.get("cancel_workers") or {}).get("thread_exited") is True
        emit_evidence(
            "backend-cancel-owner-pair",
            {
                "test": "close_refuses_while_cancel_worker_in_flight",
                "refusal": {
                    "retryable": True,
                    "retained": rep.get("retained"),
                    "thread_exited": (rep.get("cancel_workers") or {}).get("thread_exited"),
                },
                "handle_not_closed_while_in_flight": True,
                "cancel_err_after_release": rec["err_after_release"],
                "retry_close": {"closed": True},
            },
        )
    finally:
        gate.set()
        stop_backend(b)


def test_cancel_worker_exception_retries_desensitized(tmp_path, monkeypatch):
    """R13：取消原语抛异常必须**可见（脱敏类型名）、有界重试（不静默死线程）**——
    一次异常后重试命中，write 按期返回；哨兵消息不得泄漏。"""
    import packages.core.terminal.backend as backend_module

    real_cancel = backend_module.cancel_synchronous_io
    sentinel = "SENTINEL-CANCEL-EXC-9f3a"
    once = {"fired": False, "attempts": 0}

    def flaky_cancel(handle):
        once["attempts"] += 1
        if not once["fired"]:
            once["fired"] = True
            raise OSError(87, sentinel)  # 一次异常（含哨兵消息）
        return real_cancel(handle)

    monkeypatch.setattr(backend_module, "cancel_synchronous_io", flaky_cancel)
    b = ConPtyBackend.spawn(
        sleep_child(), cwd=str(tmp_path), write_budget=0.05, write_chunk=16 * 1024 * 1024
    )
    state: dict = {"done": threading.Event(), "written": None}

    def writer() -> None:
        try:
            state["written"] = b.write(b"x" * (16 * 1024 * 1024))
        except Exception as exc:  # noqa: BLE001
            state["written"] = type(exc).__name__
        state["done"].set()

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    try:
        assert state["done"].wait(6.0) is True, "取消异常后必须重试命中而非静默死线程（R13）"
        diag = b.describe().get("cancel_worker_last") or {}
        assert diag.get("last_exception_type") == "OSError", diag
        assert diag.get("last_kind") == "cancelled", diag
        assert diag.get("attempts", 0) >= 2, diag
        blob = json.dumps(b.describe(), ensure_ascii=False)
        assert sentinel not in blob, "异常消息（哨兵）不得进入诊断（脱敏）"
        b.terminate(True)
        assert b.wait_dead(5.0) is True
        report = b.close()
        assert report["closed"] is True
        emit_evidence(
            "backend-cancel-exception",
            {
                "test": "cancel_worker_exception_retries_desensitized",
                "diag": diag,
                "sentinel_leaked": False,
                "write_returned": True,
            },
        )
    finally:
        stop_backend(b)
        thread.join(2.0)


def test_cancel_worker_cap_exhausted_diagnosed_and_recovers(tmp_path, monkeypatch):
    """R13：取消**持续异常** -> cap 耗尽必须显式诊断（``cap_exhausted``），
    **不得把 Timer 退出等同 writer 退出**；恢复取消原语后由 close 兜底正确收敛。"""
    import packages.core.terminal.backend as backend_module

    monkeypatch.setattr(backend_module, "_CANCEL_CAP_BASE_SECONDS", 0.3, raising=False)
    real_cancel = backend_module.cancel_synchronous_io
    armed = {"raise": True}
    sentinel = "SENTINEL-CAP-7c1"

    def raising_cancel(handle):
        if armed["raise"]:
            raise OSError(87, sentinel)
        return real_cancel(handle)

    monkeypatch.setattr(backend_module, "cancel_synchronous_io", raising_cancel)
    b = ConPtyBackend.spawn(
        sleep_child(), cwd=str(tmp_path), write_budget=0.05, write_chunk=16 * 1024 * 1024
    )
    state: dict = {"done": threading.Event()}

    def writer() -> None:
        try:
            b.write(b"x" * (16 * 1024 * 1024))
        except Exception:  # noqa: BLE001
            pass
        state["done"].set()

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    try:
        diag: dict = {}
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            diag = b.describe().get("cancel_worker_last") or {}
            if diag.get("cap_exhausted"):
                break
            time.sleep(0.05)
        assert diag.get("cap_exhausted") is True, diag
        assert diag.get("last_kind") == "exception", diag
        assert diag.get("last_exception_type") == "OSError", diag
        blob = json.dumps(b.describe(), ensure_ascii=False)
        assert sentinel not in blob
        # Timer 退出 != writer 退出：writer 仍阻塞（事实边界，不假称硬界）
        assert state["done"].is_set() is False
        b.terminate(True)
        assert b.wait_dead(5.0) is True
        armed["raise"] = False  # 恢复取消原语
        report = b.close()  # close 的 _converge_writers 兜底取消注册句柄
        assert report["closed"] is True
        assert state["done"].wait(3.0) is True
        emit_evidence(
            "backend-cancel-cap",
            {
                "test": "cancel_worker_cap_exhausted_diagnosed_and_recovers",
                "diag_at_cap": diag,
                "writer_still_blocked_at_cap": True,
                "recovered_by_close": True,
            },
        )
    finally:
        armed["raise"] = False
        stop_backend(b)
        thread.join(2.0)


def test_close_failure_retry_deadline(tmp_path, monkeypatch):
    """清理失败重试（确定性）：**取消落空** -> 读者未收敛 -> 抛错保 owner（句柄/线程不丢）
    -> 取消恢复后重试成功。

    注入说明（包装层模拟，非真实 OS 失败）：把 `cancel_synchronous_io` 换成“落空” stub，
    模拟“取消未命中挂起 I/O”的真实语义（1168/ERROR_NOT_FOUND）；不注入时该路径为
    亚毫秒竞态，无法确定性演示。进程先用自有句柄 terminate+wait_dead（R3 前置门禁之后
    活进程会先在门禁处拒绝，不再走到 reader 收敛）。
    """
    import packages.core.terminal.backend as backend_module

    real_cancel = backend_module.cancel_synchronous_io
    fail_cancel = {"on": True}

    def stubbed_cancel(handle):
        if fail_cancel["on"]:
            return False, 1168  # ERROR_NOT_FOUND：取消落空
        return real_cancel(handle)

    monkeypatch.setattr(backend_module, "cancel_synchronous_io", stubbed_cancel)
    b = ConPtyBackend.spawn(sleep_child(), cwd=str(tmp_path))
    try:
        b.terminate(True)
        assert b.wait_dead(5.0) is True
        with pytest.raises(BackendCloseError) as excinfo:
            b.close(reader_wait_timeout=0.2)
        assert excinfo.value.retryable is True
        assert excinfo.value.report["retained"], "失败必须保留 owned 资源清单"
        reader = excinfo.value.report["reader"]
        assert reader.get("thread_exited") is False, reader
        assert reader.get("stop_requested") is True, reader
        assert b.closed is False  # 不谎报 closed
        assert b._spawn.output_read, "失败阶段不得提前释放 output_read"
        fail_cancel["on"] = False  # 取消恢复（真实取消）
        report = b.close()
        assert report["closed"] is True
        assert b.closed is True
        emit_evidence(
            "backend-close-retry",
            {
                "test": "close_failure_retry_deadline",
                "first": {"thread_exited": False, "retained": excinfo.value.report["retained"]},
                "retry": {k: report[k] for k in ("closed", "already_closed")},
            },
        )
    finally:
        fail_cancel["on"] = False
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
