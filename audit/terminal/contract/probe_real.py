"""契约真实 PTY 探针（REAL）。

性质声明
--------
本文件全部 PTY 用例都启动**真实 pywinpty / ConPTY 伪终端**与真实子进程
（``cmd.exe``、``less.exe``、``ping.exe``），因此可以作为真实 PTY 行为证据。
凡注入故障（如故意失败的 terminate）都在结果里显式标注 ``injected``。

安全边界（与 brief 一致）
- 只创建并操作本探针自己的进程；不触碰既有服务、Session、Worker、CLI thread。
- 不打开网络端口（除 ``ping`` 的 ICMP 自环，由本探针启动并在结束时清理）。
- 只在 ``%TEMP%`` 下的自有目录写测试文件；结束时删除自己创建的目录。
- 记录每个自建进程的 PID 与创建时间，结束后核对不再存活。

运行（依赖装在临时环境，不改正式依赖锁）：
    uv run --no-project --python E:/software/miniforge/python.exe \\
        --with pywinpty==3.0.5 --with pyte==0.8.2 --with psutil \\
        -- python audit/terminal/contract/probe_real.py --json-out <path>
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pty_contract import (  # noqa: E402
    AttachmentRegistry,
    PsutilTreeTerminator,
    PtyRuntime,
    PyteScreenObserver,
    RuntimeState,
    StaleLeaseError,
    TerminalRegistry,
    WinptyBackend,
    tail_text_view,
)
from probe_lib import (  # noqa: E402
    Harness,
    Watchdog,
    child_pids,
    cleanup_paths,
    decoded,
    emit,
    process_facts,
    strip_ansi,
)

HARD_TIMEOUT = 300.0
RESOURCES: list[PtyRuntime] = []
EXTRA_PIDS: list[int] = []
TMP_DIRS: list[str] = []


# ---------------------------------------------------------------- helpers


class LogFeeder:
    """把 runtime 的有界输出按游标喂给屏幕观察者，并保留有界原始副本。"""

    def __init__(self, runtime: PtyRuntime, observer: PyteScreenObserver, *, raw_cap: int = 1 << 20) -> None:
        self.runtime = runtime
        self.observer = observer
        self.cursor = 0
        self.gap: tuple[int, int] | None = None
        self.raw = bytearray()
        self.raw_cap = raw_cap

    def pump(self) -> int:
        page = self.runtime.read_from(self.cursor)
        if page.gap and self.gap is None:
            self.gap = page.gap
        data = b"".join(c.data for c in page.chunks)
        if page.chunks:
            self.cursor = page.next_cursor
        if data:
            self.observer.feed(data)
            self.raw.extend(data)
            if len(self.raw) > self.raw_cap:
                del self.raw[: len(self.raw) - self.raw_cap]
        return len(data)

    def idle(self, seconds: float, *, timeout: float = 10.0) -> bool:
        """等到连续 ``seconds`` 无新字节。"""
        deadline = time.monotonic() + timeout
        last = time.monotonic()
        while time.monotonic() < deadline:
            got = self.pump()
            now = time.monotonic()
            if got:
                last = now
            elif now - last >= seconds:
                return True
            time.sleep(0.02)
        return False

    def wait_for_raw(self, needle: bytes, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.pump()
            if needle in bytes(self.raw):
                return True
            time.sleep(0.02)
        self.pump()
        return needle in bytes(self.raw)

    def wait_for_text(self, needle: str, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.pump()
            if needle in self.observer.text():
                return True
            time.sleep(0.05)
        self.pump()
        return needle in self.observer.text()


def new_runtime(
    argv: list[str],
    *,
    rows: int = 24,
    cols: int = 80,
    cap: int = 1 << 20,
    terminator=None,
    cwd: str | None = None,
) -> tuple[PtyRuntime, WinptyBackend]:
    backend = WinptyBackend(argv, cwd or TMP_DIRS[0], rows=rows, cols=cols)
    runtime = PtyRuntime(
        f"term_probe_{len(RESOURCES):02d}", backend, output_cap=cap, terminator=terminator
    )
    runtime.start(rows=rows, cols=cols)
    RESOURCES.append(runtime)
    return runtime, backend


def parse_mode_con(text: str) -> tuple[int, int] | None:
    """解析 ``mode con`` 输出的前两个整数（顺序恒为 行/rows 再 列/cols）。

    不依赖界面语言：中文输出是「行/列」，英文是 "Lines/Columns"，两者都先出现行数。
    """
    idx = text.rfind("CON")
    if idx < 0:
        return None
    block = text[idx : idx + 400]
    nums = re.findall(r"(?<![0-9A-Za-z])(\d{1,4})(?![0-9A-Za-z])", block)
    if len(nums) < 2:
        return None
    return int(nums[0]), int(nums[1])


def force_stop_all() -> None:
    for runtime in RESOURCES:
        try:
            runtime.close(reason="probe-force-stop", terminate_timeout=1.0)
        except Exception:
            pass
    for pid in EXTRA_PIDS:
        try:
            import psutil

            if psutil.pid_exists(pid):
                psutil.Process(pid).kill()
        except Exception:
            pass


# ---------------------------------------------------------------- R1


def t_r1_short_lived_eof_drain(h: Harness) -> None:
    """短命命令：真实 PTY 上「alive()==False 但仍有尾部输出」的顺序。"""
    marker = "AAE-FINAL-MARKER-7"
    command = f"echo {marker} 0123456789 & exit /b 7"

    # --- A: 契约控制流（读到通道 EOF）
    runtime, backend = new_runtime(["cmd.exe", "/c", command], cap=1 << 20)
    facts = process_facts(backend.pid)
    h.measure("R1.A 子进程身份", **facts)
    info = runtime.wait_exit(20.0)
    eof = runtime.wait_eof(20.0)
    body = decoded(runtime.log)
    h.check("R1.A1 读到通道 EOF", eof and runtime.exit.channel_eof)
    h.check("R1.A2 output_complete 置位", runtime.exit.output_complete is True)
    h.check("R1.A3 捕获到完整标记行", marker in body, {"tail": strip_ansi(body)[-80:]})
    h.check("R1.A4 退出码来自真实进程", info.code == 7, {"code": info.code})
    h.check("R1.A5 根进程退出被单独公布", info.process_exit_seen is True)
    h.check("R1.A6 reader 线程收敛", runtime._reader is not None and not runtime._reader.is_alive())

    # --- B1: 确定性复现「先判 alive 再读」丢尾部
    # 条件：在第一次 read 之前先等进程真正退出（期间完全不读，输出留在 PTY 缓冲里）。
    delayed_runs = []
    for attempt in range(2):
        backend_b = WinptyBackend(["cmd.exe", "/c", command], TMP_DIRS[0], rows=24, cols=80)
        facts_b = process_facts(backend_b.pid)
        wait_deadline = time.monotonic() + 20.0
        while backend_b.alive() and time.monotonic() < wait_deadline:
            time.sleep(0.05)  # 不读，只等进程退出
        alive_before = backend_b.alive()
        pre: list[bytes] = []
        try:
            while backend_b.alive():  # 与 driver.py:495 相同顺序
                chunk = backend_b.read(65536)
                if chunk:
                    pre.append(chunk)
        except EOFError:
            pass
        post: list[bytes] = []
        try:
            while True:  # 契约要求：读到通道 EOF
                chunk = backend_b.read(65536)
                if chunk:
                    post.append(chunk)
        except EOFError:
            pass
        except Exception as exc:
            post.append(f"<exc:{type(exc).__name__}>".encode())
        pre_bytes = b"".join(pre)
        post_bytes = b"".join(post)
        delayed_runs.append(
            {
                "attempt": attempt + 1,
                "pid": backend_b.pid,
                "child_created_at": facts_b.get("created_at"),
                "alive_before_first_read": alive_before,
                "pr_flow_bytes": len(pre_bytes),
                "bytes_read_after_alive_false": len(post_bytes),
                "marker_in_pr_flow": marker in pre_bytes.decode("utf-8", "replace"),
                "marker_in_post": marker in post_bytes.decode("utf-8", "replace"),
            }
        )
        h.measure(f"R1.B1.{attempt + 1} 退出后才开始读（确定性）", **delayed_runs[-1])
        try:
            backend_b.close()
        except Exception:
            pass
    h.check(
        "R1.B1 复现条件成立：开始读之前进程已退出",
        all(r["alive_before_first_read"] is False for r in delayed_runs),
        {"runs": [r["alive_before_first_read"] for r in delayed_runs]},
    )
    h.check(
        "R1.B2 PR 控制流（先判 alive）捕获 0 字节，之后仍可读到输出",
        all(r["pr_flow_bytes"] == 0 and r["bytes_read_after_alive_false"] > 0 for r in delayed_runs),
        {
            "pr_flow_bytes": [r["pr_flow_bytes"] for r in delayed_runs],
            "after_alive_false": [r["bytes_read_after_alive_false"] for r in delayed_runs],
        },
    )
    h.check(
        "R1.B3 丢失的正是真实输出（标记只在 EOF 读取阶段出现）",
        all(r["marker_in_post"] and not r["marker_in_pr_flow"] for r in delayed_runs),
        {"marker_in_post": [r["marker_in_post"] for r in delayed_runs]},
    )

    # --- B2: 持续读取时该竞态是时序相关的（如实记录，不作为断言）
    timing_runs = []
    for attempt in range(2):
        backend_c = WinptyBackend(["cmd.exe", "/c", command], TMP_DIRS[0], rows=24, cols=80)
        facts_c = process_facts(backend_c.pid)
        pre_c: list[bytes] = []
        post_c: list[bytes] = []
        done = threading.Event()

        def pr_flow() -> None:
            try:
                while backend_c.alive():  # 与 driver.py:495 相同顺序，持续读取
                    chunk = backend_c.read(65536)
                    if chunk:
                        pre_c.append(chunk)
            except EOFError:
                pass
            except Exception:
                pass
            try:
                while True:
                    chunk = backend_c.read(65536)
                    if chunk:
                        post_c.append(chunk)
            except EOFError:
                pass
            except Exception:
                pass
            done.set()

        threading.Thread(target=pr_flow, daemon=True, name=f"pr-flow-{attempt}").start()
        finished = done.wait(25.0)
        timing_runs.append(
            {
                "attempt": attempt + 1,
                "pid": backend_c.pid,
                "child_created_at": facts_c.get("created_at"),
                "pr_flow_bytes": len(b"".join(pre_c)),
                "bytes_read_after_alive_false": len(b"".join(post_c)),
                "marker_in_pr_flow": marker in b"".join(pre_c).decode("utf-8", "replace"),
                "drain_finished": finished,
            }
        )
        h.measure(f"R1.B2.{attempt + 1} 持续读取（时序相关）", **timing_runs[-1])
        try:
            backend_c.close()
        except Exception:
            pass
    h.measure(
        "R1.B3 结论",
        deterministic_pr_flow_bytes=[r["pr_flow_bytes"] for r in delayed_runs],
        deterministic_after_alive_false=[r["bytes_read_after_alive_false"] for r in delayed_runs],
        timing_dependent_after_alive_false=[r["bytes_read_after_alive_false"] for r in timing_runs],
        note=(
            "进程先退出再开始读时可 100% 复现尾部丢失；持续读取时是否命中取决于时序，"
            "所以契约要求「读到 EOF」而不是「靠时序碰对」"
        ),
    )


# ---------------------------------------------------------------- R2


def t_r2_interactive_send_and_exit_code(h: Harness) -> None:
    runtime, backend = new_runtime(["cmd.exe"], cap=1 << 20)
    h.measure("R2.0 交互 shell 身份", **process_facts(backend.pid))
    observer = PyteScreenObserver(24, 80)
    feeder = LogFeeder(runtime, observer)
    feeder.pump()
    time.sleep(0.8)

    runtime.write(b"set AAEVAR=42\r\n")
    runtime.write(b"echo AAE-MARKER-%AAEVAR%\r\n")
    ok = feeder.wait_for_raw(b"AAE-MARKER-42", 10.0)
    raw = bytes(feeder.raw).decode("utf-8", "replace")
    h.check("R2.1 输入被真实 cmd 执行（变量已展开）", ok, {"marker_seen": ok})
    h.check("R2.2 回显的未展开形式也在流中（可区分回显与执行）", "AAE-MARKER-%AAEVAR%" in raw)
    h.check("R2.3 屏幕观察者看到执行结果", "AAE-MARKER-42" in observer.text())

    runtime.write(b"exit 5\r\n")
    info = runtime.wait_exit(20.0)
    h.check("R2.4 真实退出码被读回", info.code == 5, {"code": info.code})
    report = runtime.close(reason="R2-done")
    h.check("R2.5 正常清理成功", report.ok and report.state_after is RuntimeState.EXITED)


# ---------------------------------------------------------------- R3


def t_r3_resize_reaches_child_console(h: Harness) -> None:
    registry = TerminalRegistry()
    runtime, backend = new_runtime(["cmd.exe"], rows=24, cols=80, cap=1 << 20)
    record = registry.register(runtime)
    attachments = AttachmentRegistry(registry)
    token = attachments.attach(record.terminal_id, "probe-client", role="control", rows=24, cols=80)
    observer = PyteScreenObserver(24, 80)
    feeder = LogFeeder(runtime, observer)
    feeder.pump()
    time.sleep(0.8)

    def ask_size() -> tuple[int, int] | None:
        """只解析本次 ``mode con`` 追加的文本，避免读到上一次的旧输出。"""
        start = len(feeder.raw)
        runtime.write(b"mode con\r\n")
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            feeder.pump()
            new_text = bytes(feeder.raw)[start:].decode("utf-8", "replace")
            parsed = parse_mode_con(strip_ansi(new_text))
            if parsed:
                return parsed
            time.sleep(0.05)
        return None

    before = ask_size()
    h.check("R3.1 启动尺寸在子控制台可见", before == (24, 80), {"mode_con": before})
    h.measure("R3.1 启动尺寸", requested=(24, 80), child_reports=before, backend_winsize=backend.win_size())

    moved = attachments.resize(token, 40, 132)
    time.sleep(0.5)
    after = ask_size()
    h.check("R3.2 resize 传到子控制台", after == (40, 132), {"mode_con": after})
    h.measure(
        "R3.2 resize 实测",
        requested=(40, 132),
        child_reports=after,
        runtime_rows_cols=(runtime.rows, runtime.cols),
        backend_winsize=backend.win_size(),
    )

    # 控制权转交后，旧客户端的 resize 必须被拒绝，且对真实终端无影响
    attachments.transfer_control(token, to_client="probe-client-2")
    try:
        attachments.resize(token, 10, 10)
        h.check("R3.3 旧客户端 resize 被拒绝", False, "未报错")
    except StaleLeaseError as exc:
        h.check("R3.3 旧客户端 resize 被拒绝", True, str(exc))
    time.sleep(0.3)
    unchanged = ask_size()
    h.check("R3.4 迟到 resize 未影响真实终端", unchanged == (40, 132), {"mode_con": unchanged})

    report = runtime.close(reason="R3-done")
    h.check("R3.5 清理成功", report.ok, {"state": report.state_after.value})


# ---------------------------------------------------------------- R4


def t_r4_bounded_output_real_load(h: Harness) -> None:
    cap = 256 * 1024
    generator = os.path.join(TMP_DIRS[0], "bulk.cmd")
    with open(generator, "w", encoding="ascii", newline="\r\n") as fh:
        fh.write("@echo off\r\n")
        fh.write("for /L %%i in (1,1,20000) do @echo " + ("E" * 60) + "\r\n")
    runtime, backend = new_runtime(["cmd.exe", "/c", generator], cap=cap, terminator=None)
    h.measure("R4.0 批量输出子进程身份", **process_facts(backend.pid))
    t0 = time.monotonic()
    info = runtime.wait_exit(90.0)
    runtime.wait_eof(30.0)
    elapsed = round(time.monotonic() - t0, 3)
    log = runtime.log
    h.check("R4.1 无消费者时 PTY 仍被持续 drain（进程结束）", info.process_exit_seen is True)
    h.check("R4.2 读到通道 EOF", runtime.exit.channel_eof is True)
    h.check("R4.3 真实产出量超过保留上界", log.total_bytes > cap, {"total": log.total_bytes})
    h.check("R4.4 保留量有界", log.retained_bytes <= cap, {"retained": log.retained_bytes})
    h.check(
        "R4.5 dropped = total - retained",
        log.dropped_bytes == log.total_bytes - log.retained_bytes,
        {"dropped": log.dropped_bytes},
    )
    page = log.read_from(0)
    h.check(
        "R4.6 落后游标返回明确 gap",
        page.gap == (0, log.dropped_bytes),
        {"gap": page.gap, "dropped": log.dropped_bytes},
    )
    tail_page = log.read_from(page.next_cursor)
    h.check("R4.7 gap 之后可继续追平且无 gap", tail_page.gap is None)
    h.check("R4.8 退出码正确", info.code == 0, {"code": info.code})
    h.measure(
        "R4.9 真实吞吐与内存上界",
        total_bytes=log.total_bytes,
        retained_bytes=log.retained_bytes,
        dropped_bytes=log.dropped_bytes,
        cap=cap,
        seconds_to_exit=elapsed,
        note="客户端全程不读，PTY/进程未被拖死；内存上界由保留了 256KiB 证明",
    )
    h.measure(
        "R4.10 仅靠保留窗口中继重建屏幕的可行性",
        retained_bytes=log.retained_bytes,
        screen_bytes=24 * 80,
        retained_covers_screen=log.retained_bytes >= 24 * 80,
        note="保留窗口远大于一屏是必要的，但不是充分条件（需要快照恢复颜色/备用屏/模式）",
    )
    report = runtime.close(reason="R4-done")
    h.check("R4.11 清理成功", report.ok)


# ---------------------------------------------------------------- R5


def t_r5_cleanup_tree_ownership(h: Harness) -> None:
    terminator = PsutilTreeTerminator()
    runtime, backend = new_runtime(["cmd.exe", "/k"], cap=1 << 20, terminator=terminator)
    root_pid = backend.pid
    h.measure("R5.0 PTY 根进程身份", **process_facts(root_pid))
    obs = PyteScreenObserver(24, 80)
    feeder = LogFeeder(runtime, obs)
    feeder.pump()
    time.sleep(1.0)
    runtime.write(b"start /b ping -n 60 127.0.0.1\r\n")
    kids: list[dict] = []
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        feeder.pump()
        kids = [c for c in runtime.child_pids() if c and c != root_pid]
        if kids:
            break
        time.sleep(0.2)
    h.check("R5.1 建立真实后代进程", bool(kids), {"children": kids})
    facts = [process_facts(pid) for pid in kids]
    h.measure("R5.1 后代进程身份", children=facts)
    EXTRA_PIDS.extend(kids)

    report = runtime.close(reason="R5-explicit-close")
    remaining = [pid for pid in kids if process_facts(pid).get("exists")]
    h.check("R5.2 整树清理后无遗留（owned pids）", not report.tree_remaining_pids, {"remaining": report.tree_remaining_pids})
    h.check(
        "R5.3 后代进程确实消失",
        not remaining,
        {"recorded": kids, "still_alive": remaining},
    )
    h.check("R5.4 清理进入 exited", report.state_after is RuntimeState.EXITED and report.ok, {"error": report.error})
    h.measure(
        "R5.5 清理报告",
        owned=report.tree_owned_pids,
        remaining=report.tree_remaining_pids,
        terminate=report.terminate_result,
        seconds=report.seconds,
        interrupt_sent=report.interrupt_sent,
    )


def t_r5b_cleanup_failure_retains_owner(h: Harness) -> None:
    """注入终止失败（injected）：真实子进程必须仍然存活且 owner 被保留。"""
    runtime, backend = new_runtime(["cmd.exe", "/k"], cap=1 << 20, terminator=None)
    root_pid = backend.pid
    h.measure("R5b.0 PTY 根进程身份", **process_facts(root_pid))
    obs = PyteScreenObserver(24, 80)
    feeder = LogFeeder(runtime, obs)
    feeder.pump()
    time.sleep(1.0)
    runtime.write(b"start /b ping -n 60 127.0.0.1\r\n")
    kids: list[int] = []
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        feeder.pump()
        kids = [int(c["pid"]) for c in child_pids(root_pid)]
        if kids:
            break
        time.sleep(0.2)
    EXTRA_PIDS.extend(kids)
    h.measure("R5b.1 后代进程身份", children=[process_facts(p) for p in kids])

    def injected_failure(force: bool) -> None:
        raise RuntimeError("injected terminate failure (probe fault injection)")

    failed = runtime.close(reason="R5b-injected-failure", terminate=injected_failure)
    alive_after_failure = [pid for pid in ([root_pid] + kids) if pid and process_facts(pid).get("exists")]
    h.check(
        "R5b.2 终止失败进入 cleanup-failed",
        failed.state_after is RuntimeState.CLEANUP_FAILED,
        {"state": failed.state_after.value, "error": failed.error},
    )
    h.check("R5b.3 owner_retained 置位", failed.owner_retained is True)
    h.check("R5b.4 失败后后端句柄未关闭（保留 owner）", failed.backend_closed is False)
    h.check(
        "R5b.5 真实进程仍然存活（没有谎报退出）",
        bool(alive_after_failure),
        {"alive": alive_after_failure},
    )
    h.measure(
        "R5b.6 失败注入结果",
        injected=True,
        terminate_result=failed.terminate_result,
        terminate_error=failed.terminate_error,
        owner_retained=failed.owner_retained,
        alive_pids=alive_after_failure,
    )

    retry = runtime.close(reason="R5b-retry")
    alive_after_retry = [pid for pid in ([root_pid] + kids) if pid and process_facts(pid).get("exists")]
    h.check("R5b.7 重试后进入 exited", retry.state_after is RuntimeState.EXITED and retry.ok, {"error": retry.error})
    h.check("R5b.8 重试后真实进程消失", not alive_after_retry, {"alive": alive_after_retry})
    h.measure("R5b.9 重试清理报告", owned=retry.tree_owned_pids, remaining=retry.tree_remaining_pids, seconds=retry.seconds)


# ---------------------------------------------------------------- R6


def _make_sample(path: str, lines: int = 400) -> None:
    with open(path, "w", encoding="utf-8", newline="\r\n") as fh:
        for i in range(1, lines + 1):
            fh.write(f"LINE-{i:04d} lorem ipsum dolor sit amet consectetur adipiscing elit\n")


def t_r6_real_tui_snapshot_vs_tail(h: Harness) -> None:
    less_exe = shutil.which("less")
    h.measure("R6.0 TUI 程序", less=less_exe, vim=shutil.which("vim"), term="xterm-256color")
    if not less_exe:
        h.measure("R6.skip 本机无 less，跳过真实 TUI 用例")
        return
    sample = os.path.join(TMP_DIRS[0], "sample.txt")
    _make_sample(sample)

    runtime, backend = new_runtime(["cmd.exe"], rows=24, cols=80, cap=1 << 21)
    h.measure("R6.1 PTY 根进程身份", **process_facts(backend.pid))
    registry = TerminalRegistry()
    record = registry.register(runtime)
    attachments = AttachmentRegistry(registry)
    token = attachments.attach(record.terminal_id, "probe-tui-client", role="control", rows=24, cols=80)
    observer = PyteScreenObserver(24, 80)
    feeder = LogFeeder(runtime, observer)
    feeder.pump()
    time.sleep(0.8)
    runtime.write(b"echo AAE-BEFORE-TUI\r\n")
    h.check("R6.2 进入 TUI 前的屏幕标记可见（经 echo 显示）", feeder.wait_for_raw(b"AAE-BEFORE-TUI", 8.0))

    cursor_before_tui = feeder.cursor
    runtime.write(f'"{less_exe}" "{sample}"\r\n'.encode("ascii"))
    entered = feeder.wait_for_raw(b"\x1b[?1049h", 10.0)
    h.check("R6.3 真实 TUI 进入备用屏（less 实测）", entered, {"needle": "ESC[?1049h"})
    alt_enter_offset = bytes(feeder.raw).find(b"\x1b[?1049h")
    feeder.idle(0.4, timeout=6.0)
    snap_tui = observer.snapshot()
    h.check("R6.4 快照记录备用屏状态", snap_tui.alternate_screen is True, {"modes": sorted(snap_tui.private_modes)})
    on_screen = [ln.strip() for ln in snap_tui.lines if ln.strip()]
    sample_lines_on_screen = sum(1 for ln in on_screen if ln.startswith("LINE-"))
    h.check(
        "R6.5 快照给出 TUI 屏幕文本（真实样本行）",
        sample_lines_on_screen >= 20,
        {"sample_lines_on_screen": sample_lines_on_screen, "first": on_screen[:2]},
    )
    h.check("R6.6 快照含 TUI 私有模式（应用光标键等）", bool(snap_tui.private_modes), {"modes": sorted(snap_tui.private_modes)})
    h.measure(
        "R6.7 TUI 快照实测",
        engine=snap_tui.engine,
        fidelity=snap_tui.fidelity,
        cursor=snap_tui.cursor,
        private_modes=sorted(snap_tui.private_modes),
        scrollback_lines=snap_tui.scrollback_lines,
        observed_bytes=observer.observed_bytes,
        nonblank_lines=len(on_screen),
        sample_lines=400,
    )

    # 流中间加入的客户端（等价于重连/迟到观察者）无法从后续字节推断模式状态
    mid_stream = bytes(feeder.raw)[alt_enter_offset + len(b"\x1b[?1049h") :]
    mid_observer = PyteScreenObserver(24, 80)
    mid_observer.feed(mid_stream)
    mid_snap = mid_observer.snapshot()
    h.check(
        "R6.8 从流中间解析无法得知备用屏状态（需要快照）",
        snap_tui.alternate_screen is True and mid_snap.alternate_screen is False,
        {"mid_stream_alt": mid_snap.alternate_screen, "true_state": True},
    )
    h.check(
        "R6.9 无滚动历史：400 行样本只剩一屏（pyte 限制）",
        mid_snap.scrollback_lines == 0 and len(on_screen) <= 24,
        {"scrollback": mid_snap.scrollback_lines, "nonblank": len(on_screen)},
    )
    h.measure(
        "R6.9 结论：快照必须来自仿真器状态",
        alternate_screen_buffer_supported=PyteScreenObserver.SUPPORTS_ALTERNATE_SCREEN_BUFFER,
        scrollback_supported=PyteScreenObserver.SUPPORTS_SCROLLBACK,
        mid_stream_alt_screen=mid_snap.alternate_screen,
        mode_bits_in_tail_only_parse=sorted(mid_snap.private_modes),
        note="流中间加入的客户端既拿不到备用屏标志，也拿不到滚动历史",
    )

    # TUI 运行中的翻页与 resize：真实 TUI 会重绘，仿真器必须同步尺寸
    pre_page = feeder.cursor
    runtime.write(b" ")
    feeder.idle(0.3, timeout=5.0)
    page_bytes = feeder.cursor - pre_page
    h.measure("R6.10 TUI 翻页产生的重绘字节", bytes_after_page=page_bytes)

    pre_resize = feeder.cursor
    attachments.resize(token, 30, 100)
    observer.resize(30, 100)
    feeder.idle(0.3, timeout=5.0)
    resize_bytes = feeder.cursor - pre_resize
    snap_resized = observer.snapshot()
    h.check(
        "R6.11 resize 触发真实 TUI 重绘且仿真器同步尺寸",
        resize_bytes > 0 and (snap_resized.rows, snap_resized.cols) == (30, 100),
        {"resize_bytes": resize_bytes, "snapshot_size": (snap_resized.rows, snap_resized.cols)},
    )
    h.measure(
        "R6.11 resize 实测（TUI 运行中）",
        resize_bytes=resize_bytes,
        snapshot_size=(snap_resized.rows, snap_resized.cols),
        private_modes=sorted(snap_resized.private_modes),
        note="PTY 与客户端仿真器必须同步 resize，否则重绘换行错位",
    )

    runtime.write(b"q")
    left = feeder.wait_for_raw(b"\x1b[?1049l", 10.0)
    h.check("R6.12 退出 TUI 时恢复主屏序列出现", left)
    feeder.idle(0.4, timeout=6.0)
    snap_after = observer.snapshot()
    before_visible = any("AAE-BEFORE-TUI" in ln for ln in snap_after.lines)
    h.check("R6.13 退出备用屏后模式位被复位", snap_after.alternate_screen is False, {"modes": sorted(snap_after.private_modes)})
    h.measure(
        "R6.14 退出备用屏后主屏是否由 ConPTY 重绘（观察值，不设断言）",
        pyte_shows_pre_tui_line=before_visible,
        pyte_alt_buffer_supported=PyteScreenObserver.SUPPORTS_ALTERNATE_SCREEN_BUFFER,
        first_lines=[ln.strip() for ln in snap_after.lines if ln.strip()][:3],
        note=(
            "True 表示 ConPTY 在 ?1049l 后重发了主屏可见内容；False 表示 pyte 单缓冲"
            "保留了 TUI 残留，文本恢复必须依赖快照/重绘"
        ),
    )

    # 尾部文本 vs 快照：结构性对比（确定）
    raw_full = bytes(feeder.raw)
    tail = tail_text_view(raw_full, window=512)
    h.check(
        "R6.15 尾部文本既无备用屏/模式也无光标信息（控制序列全被剥掉）",
        "\x1b" not in tail and "1049" not in tail and "9001" not in tail,
        {"tail_head": tail[:60].replace("\n", "|")},
    )
    tail_only = raw_full[-512:]
    fresh = PyteScreenObserver(30, 100)
    fresh.feed(tail_only)
    snap_fresh = fresh.snapshot()
    h.measure(
        "R6.16 只用尾部 512B 重建的观察者状态",
        tail_bytes=len(tail_only),
        full_stream_bytes=len(raw_full),
        tail_alt_screen=snap_fresh.alternate_screen,
        full_alt_screen=snap_after.alternate_screen,
        tail_first_lines=[ln.strip() for ln in snap_fresh.lines if ln.strip()][:2],
    )
    h.check(
        "R6.17 尾部重建的屏幕文本与完整解析不一致",
        tuple(ln.strip() for ln in snap_fresh.lines) != tuple(ln.strip() for ln in snap_after.lines),
        {
            "tail_only_first": [ln.strip() for ln in snap_fresh.lines if ln.strip()][:2],
            "full_first": [ln.strip() for ln in snap_after.lines if ln.strip()][:2],
        },
    )
    h.measure("R6.18 TUI 前后游标", cursor_before_tui=cursor_before_tui, cursor_after=feeder.cursor)

    runtime.write(b"exit\r\n")
    runtime.wait_exit(15.0)
    report = runtime.close(reason="R6-done")
    h.check("R6.19 清理成功", report.ok, {"state": report.state_after.value})


def t_r7_reconnect_gap_and_snapshot(h: Harness) -> None:
    """重连语义：客户端离开期间 PTY 继续跑；回来时用序号/gap + 快照恢复。"""
    cap = 64 * 1024
    generator = os.path.join(TMP_DIRS[0], "reconnect.cmd")
    with open(generator, "w", encoding="ascii", newline="\r\n") as fh:
        fh.write("@echo off\r\n")
        fh.write("for /L %%i in (1,1,3000) do @echo " + ("R" * 70) + "\r\n")
        fh.write("echo AAE-RECONNECT-TAIL-MARKER\r\n")
    runtime, backend = new_runtime(["cmd.exe", "/c", generator], cap=cap)
    h.measure("R7.0 子进程身份", **process_facts(backend.pid))
    # 客户端“离线”：只在结束时读一次
    info = runtime.wait_exit(60.0)
    runtime.wait_eof(20.0)
    log = runtime.log
    offline_cursor = 0
    page = log.read_from(offline_cursor)
    h.check("R7.1 重连客户端收到明确 gap", page.gap == (0, log.dropped_bytes), {"gap": page.gap})
    recovered = b"".join(c.data for c in log.read_from(log.dropped_bytes).chunks)
    h.check(
        "R7.2 gap 之后仍能读到尾部标记（尾部未被丢弃）",
        b"AAE-RECONNECT-TAIL-MARKER" in recovered,
        {"retained": log.retained_bytes, "total": log.total_bytes},
    )
    h.measure(
        "R7.3 重连窗口实测",
        total_bytes=log.total_bytes,
        retained_bytes=log.retained_bytes,
        dropped_bytes=log.dropped_bytes,
        exit_code=info.code,
        note="序号是绝对字节偏移，跨重连可复用；gap 段必须用屏幕快照恢复而非补零",
    )
    report = runtime.close(reason="R7-done")
    h.check("R7.4 清理成功", report.ok)


# ---------------------------------------------------------------- cleanup


def cleanup(h: Harness) -> None:
    h.section("清理")
    leftover: list[dict] = []
    for runtime in RESOURCES:
        pid = runtime._backend.pid
        facts = process_facts(pid)
        if facts.get("exists") and runtime.state is not RuntimeState.EXITED:
            try:
                runtime.close(reason="probe-final-cleanup", terminate_timeout=1.5)
            except Exception:
                pass
        facts_after = process_facts(pid)
        if facts_after.get("exists"):
            leftover.append(facts_after)
    for pid in EXTRA_PIDS:
        facts = process_facts(pid)
        if facts.get("exists"):
            leftover.append(facts)
    h.check(
        "C1 所有自建进程已清理",
        not leftover,
        {"leftover": leftover},
    )
    removed = cleanup_paths(TMP_DIRS)
    h.check("C2 探针临时目录已删除", not [d for d in TMP_DIRS if os.path.exists(d)], {"removed": removed})
    h.measure(
        "C3 资源清单",
        runtimes=len(RESOURCES),
        runtimes_created=[r.terminal_id for r in RESOURCES],
        recorded_extra_pids=EXTRA_PIDS,
        tmp_dirs=TMP_DIRS,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json-out", default=None)
    parser.add_argument("--hard-timeout", type=float, default=HARD_TIMEOUT)
    parser.add_argument(
        "--only",
        default=None,
        help="只运行名字含该子串的用例（逗号分隔），用于迭代调试",
    )
    args = parser.parse_args()

    tmp = tempfile.mkdtemp(prefix="pan_pty_contract_probe_")
    TMP_DIRS.append(tmp)

    h = Harness(
        "real",
        note="真实 pywinpty/ConPTY PTY + 真实子进程（cmd.exe/less.exe/ping.exe）；注入故障已标注 injected。",
    )
    h.measure(
        "R0 环境",
        python=sys.version.split()[0],
        python_executable=sys.executable,
        probe_pid=os.getpid(),
        probe_process=process_facts(os.getpid()),
        tmp_root=tmp,
        cmd=shutil.which("cmd"),
        less=shutil.which("less"),
        network_ports_opened=[],
    )
    watchdog = Watchdog(args.hard_timeout, on_fire=force_stop_all)
    watchdog.start()
    cases = (
        t_r1_short_lived_eof_drain,
        t_r2_interactive_send_and_exit_code,
        t_r3_resize_reaches_child_console,
        t_r4_bounded_output_real_load,
        t_r5_cleanup_tree_ownership,
        t_r5b_cleanup_failure_retains_owner,
        t_r6_real_tui_snapshot_vs_tail,
        t_r7_reconnect_gap_and_snapshot,
    )
    if args.only:
        wanted = [part.strip() for part in args.only.split(",") if part.strip()]
        cases = tuple(fn for fn in cases if any(w in fn.__name__ for w in wanted))
        h.measure("R0.1 过滤后的用例", only=args.only, cases=[fn.__name__ for fn in cases])
    try:
        for fn in cases:
            h.section(fn.__name__)
            h.run(fn)
        cleanup(h)
    finally:
        try:
            force_stop_all()
        except Exception:
            pass
    return emit(
        h,
        json_out=args.json_out,
        extra={"hard_timeout": args.hard_timeout, "only": args.only},
    )


if __name__ == "__main__":
    sys.exit(main())
