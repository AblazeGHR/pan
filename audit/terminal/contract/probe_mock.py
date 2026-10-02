"""契约逻辑探针（MOCK）。

性质声明
--------
本文件全部用例使用 ``ScriptedBackend``（确定性脚本后端）或纯内存对象，
**故意不启动真实 PTY**，因此：

- 可以作为契约逻辑（序号/gap、状态机、lease、清理失败、driver 边界）的回归证据；
- **不能**作为真实 CLI / ConPTY / pywinpty 行为的证据；真实行为见 ``probe_real.py``。

唯一例外是 M7：它导入真实 pyte 0.8.2 解析合成 VT 序列，用来证明"快照 ≠ 尾部文本"
与 pyte 的模式位编码（属于库行为，不是 PTY 行为），并在结果中明确标注。

运行：
    uv run --no-project --python E:/software/miniforge/python.exe \\
        --with pyte==0.8.2 --with psutil \\
        -- python audit/terminal/contract/probe_mock.py --json-out <path>
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pty_contract import (  # noqa: E402
    AttachmentRegistry,
    IllegalStateTransition,
    InvalidCursorError,
    NotControlLeaseError,
    OutputLog,
    PsutilTreeTerminator,
    PtyRuntime,
    RuntimeState,
    ScriptedBackend,
    StaleLeaseError,
    TerminalRegistry,
    UnknownTerminalError,
    ALT_SCREEN_DEC_MODES,
    DetachedOwnershipNotImplemented,
    require_detached_ownership,
)
from probe_lib import Harness, emit  # noqa: E402


# ---------------------------------------------------------------- M1-M4: 输出日志


def t_output_log_sequence_and_gap(h: Harness) -> None:
    log = OutputLog(max_bytes=100)
    log.append(b"A" * 60)
    log.append(b"B" * 60)
    h.check("M1.1 保留量不超过上界", log.retained_bytes <= 100, {"retained": log.retained_bytes})
    h.check("M1.2 序号是绝对字节偏移", log.total_bytes == 120, {"total": log.total_bytes})
    h.check("M1.3 驱逐量可核对", log.dropped_bytes == 60, {"dropped": log.dropped_bytes})

    page = log.read_from(0)
    h.check("M1.4 落后游标返回明确 gap", page.gap == (0, 60), {"gap": page.gap})
    h.check(
        "M1.5 gap 后从保留窗口起点继续",
        page.first_seq == 60 and b"".join(c.data for c in page.chunks) == b"B" * 60,
        {"first_seq": page.first_seq},
    )
    h.check("M1.6 游标推进到已产生字节数", page.next_cursor == 120, {"next": page.next_cursor})

    page2 = log.read_from(page.next_cursor)
    h.check(
        "M1.7 游标追平后无 gap 无数据",
        page2.gap is None and page2.chunks == () and page2.next_cursor == 120,
        {"gap": page2.gap, "chunks": len(page2.chunks)},
    )
    page3 = log.read_from(60)
    h.check("M1.8 命中保留窗口内不报 gap", page3.gap is None and page3.first_seq == 60)

    try:
        log.read_from(121)
        h.check("M1.9 超过已产生字节的游标被拒绝", False, "no error raised")
    except InvalidCursorError as exc:
        h.check("M1.9 超过已产生字节的游标被拒绝", True, str(exc))


def t_output_log_single_chunk_over_cap(h: Harness) -> None:
    log = OutputLog(max_bytes=16)
    log.append(b"C" * 40)
    h.check("M2.1 单块超容量仍保持有界", log.retained_bytes == 16, {"retained": log.retained_bytes})
    h.check("M2.2 溢出的字节计入 dropped", log.dropped_bytes == 24)
    page = log.read_from(0)
    h.check("M2.3 单块截断产生 gap", page.gap == (0, 24), {"gap": page.gap})
    h.check(
        "M2.4 保留的是尾部且长度正确",
        b"".join(c.data for c in page.chunks) == b"C" * 16 and page.next_cursor == 40,
        {"next": page.next_cursor},
    )


def t_output_log_sustained_bounded(h: Harness) -> None:
    cap = 4096
    log = OutputLog(max_bytes=cap)
    for _ in range(200):
        log.append(b"D" * 1000)
    h.check("M3.1 持续写入后有界", log.retained_bytes <= cap, {"retained": log.retained_bytes})
    h.check("M3.2 total 记录全部产出", log.total_bytes == 200_000)
    h.check(
        "M3.3 dropped = total - retained 恒等",
        log.dropped_bytes == log.total_bytes - log.retained_bytes,
        {"dropped": log.dropped_bytes},
    )
    page = log.read_from(log.dropped_bytes)
    h.check("M3.4 从正确游标读满保留窗口", sum(len(c.data) for c in page.chunks) == log.retained_bytes)
    h.check("M3.5 first_retained_seq == dropped_bytes", log.first_retained_seq == log.dropped_bytes)
    # 分页读取：游标单调、不重复、不漏（在保留窗口内）
    cursor = log.dropped_bytes
    pages = 0
    while True:
        p = log.read_from(cursor, max_bytes=512)
        if not p.chunks:
            break
        cursor = p.next_cursor
        pages += 1
        if pages > 100:
            break
    h.check("M3.6 分页读取收敛到末尾", cursor == log.total_bytes, {"cursor": cursor, "pages": pages})


def t_drain_reads_to_eof_after_process_exit(h: Harness) -> None:
    """契约核心：``alive()==False`` 不是停止读取的条件。"""
    backend = ScriptedBackend([b"head:", b"TAIL-AFTER-EXIT"], alive_returns=False, exit_code=7)
    runtime = PtyRuntime("term_mock_drain", backend, output_cap=4096, eof_grace=0.5)
    runtime.start(rows=24, cols=80)
    ok = runtime.wait_eof(2.0)
    h.check("M4.1 reader 到达通道 EOF", ok and runtime.exit.channel_eof)
    h.check("M4.2 EOF 后 output_complete 置位", runtime.exit.output_complete is True)
    body = b"".join(c.data for c in runtime.read_from(0).chunks)
    h.check(
        "M4.3 尾部输出在 alive()==False 之后仍然入账",
        body == b"head:TAIL-AFTER-EXIT",
        {"captured": body.decode()},
    )
    info = runtime.poll_exit()
    h.check("M4.4 根进程退出被单独公布", info.process_exit_seen and info.code == 7, {"code": info.code})

    # 对照组：复现 PR _PtySession._read 的控制流（先查 isalive 再 read）
    pr_backend = ScriptedBackend([b"head:", b"TAIL-AFTER-EXIT"], alive_returns=False, exit_code=7)
    captured = b""
    while True:
        if not pr_backend.alive():  # 与 PR driver.py:495 相同顺序
            break
        captured += pr_backend.read(4096)
    h.measure(
        "M4.contrast PR 控制流(先判 alive)捕获字节数",
        pr_flow_bytes=len(captured),
        contract_flow_bytes=len(body),
        note="同为 mock 后端；真实 PTY 对照见 probe_real.py R1",
    )
    h.check(
        "M4.5 对照组证明该控制流会丢尾部",
        len(captured) < len(body),
        {"pr_flow": captured.decode(), "contract": body.decode()},
    )
    report = runtime.close(reason="mock-close")
    h.check("M4.6 正常清理进入 exited", report.state_after is RuntimeState.EXITED and report.ok)


# ---------------------------------------------------------------- M5: 状态机


def t_state_machine(h: Harness) -> None:
    backend = ScriptedBackend([], alive_returns=True, exit_code=None)
    runtime = PtyRuntime("term_mock_sm", backend, output_cap=1024)
    h.check("M5.1 初始状态 created", runtime.state is RuntimeState.CREATED)
    try:
        runtime._set_state(RuntimeState.EXITED)
        h.check("M5.2 非法跃迁被拒绝", False, "created -> exited 未报错")
    except IllegalStateTransition as exc:
        h.check("M5.2 非法跃迁被拒绝", True, str(exc))
    # 未启动即可安全 close（独立实例，避免污染后续用例）
    unstarted = PtyRuntime("term_mock_unstarted", ScriptedBackend([], alive_returns=True), output_cap=256)
    report_unstarted = unstarted.close(reason="close-before-start")
    h.check(
        "M5.2b 未启动即可安全 close",
        report_unstarted.state_after is RuntimeState.EXITED and report_unstarted.backend_closed,
        {"state": report_unstarted.state_after.value},
    )
    runtime.start(rows=24, cols=80)
    h.check("M5.3 启动后 running", runtime.state is RuntimeState.RUNNING)
    try:
        runtime._set_state(RuntimeState.CREATED)
        h.check("M5.4 禁止回退到 created", False, "未报错")
    except IllegalStateTransition:
        h.check("M5.4 禁止回退到 created", True)
    report = runtime.close(reason="explicit-close")
    h.check("M5.5 关闭后 exited", report.state_after is RuntimeState.EXITED, {"state": report.state_after.value})
    try:
        runtime.write(b"late input")
        h.check("M5.6 退出后写入被拒绝", False, "未报错")
    except IllegalStateTransition as exc:
        h.check("M5.6 退出后写入被拒绝", True, str(exc))
    h.check("M5.7 退出后 resize 返回 False（迟到 resize 明确语义）", runtime.resize(40, 132) is False)
    again = runtime.close(reason="second-close")
    h.check("M5.8 重复 close 幂等", again.state_after is RuntimeState.EXITED and again.seconds >= 0)


# ---------------------------------------------------------------- M6: 清理失败保留 owner


def t_cleanup_failure_retains_owner(h: Harness) -> None:
    backend = ScriptedBackend([], alive_returns=True, exit_code=None, fail_terminate=True)
    runtime = PtyRuntime("term_mock_cleanup", backend, output_cap=1024)
    runtime.start(rows=24, cols=80)
    first = runtime.close(reason="close-with-failing-terminate")
    h.check(
        "M6.1 终止失败进入 cleanup-failed",
        first.state_after is RuntimeState.CLEANUP_FAILED,
        {"state": first.state_after.value, "error": first.error},
    )
    h.check("M6.2 owner_retained 置位", first.owner_retained is True)
    h.check("M6.3 失败时不移除/不关闭后端句柄", backend.closed is False)
    h.check("M6.4 报告不可当作成功", first.ok is False and bool(first.error))
    h.check("M6.5 终止结果被记录", first.terminate_result == "error", {"t": first.terminate_result})

    second = runtime.close(reason="retry-with-real-terminate", terminate=lambda force: None)
    h.check(
        "M6.6 重试成功才进入 exited",
        second.state_after is RuntimeState.EXITED and second.ok,
        {"state": second.state_after.value},
    )
    h.check("M6.7 成功后句柄关闭", backend.closed is True and second.backend_closed is True)


def t_registry_retains_failed_terminal(h: Harness) -> None:
    registry = TerminalRegistry()
    tid = registry.new_terminal_id()
    backend = ScriptedBackend([], alive_returns=True, exit_code=None, fail_terminate=True)
    runtime = PtyRuntime(tid, backend, output_cap=1024)
    runtime.start(rows=24, cols=80)
    registry.register(runtime)
    report = registry.close(tid, reason="failing-close")
    record = registry.get(tid)
    h.check(
        "M7.1 registry 保留失败终端记录",
        record.state is RuntimeState.CLEANUP_FAILED and report.owner_retained,
        {"state": record.state.value},
    )
    try:
        registry.remove(tid)
        h.check("M7.2 失败状态禁止移除", False, "未报错")
    except IllegalStateTransition as exc:
        h.check("M7.2 失败状态禁止移除", True, str(exc))
    registry.close(tid, reason="retry", terminate=lambda force: None)
    registry.remove(tid)
    try:
        registry.get(tid)
        h.check("M7.3 成功清理后可移除", False, "仍能取到")
    except UnknownTerminalError:
        h.check("M7.3 成功清理后可移除", True)
    h.check(
        "M7.4 未知 terminal id 报错",
        isinstance(_expect_unknown(registry), UnknownTerminalError),
    )


def _expect_unknown(registry: TerminalRegistry) -> Exception | None:
    try:
        registry.get("term_does_not_exist")
        return None
    except Exception as exc:
        return exc


# ---------------------------------------------------------------- M8: lease


def _leased_runtime(h: Harness) -> tuple[TerminalRegistry, AttachmentRegistry, PtyRuntime]:
    registry = TerminalRegistry()
    tid = registry.new_terminal_id()
    backend = ScriptedBackend([], alive_returns=True, exit_code=None)
    runtime = PtyRuntime(tid, backend, output_cap=1024)
    runtime.start(rows=24, cols=80)
    registry.register(runtime)
    return registry, AttachmentRegistry(registry), runtime


def t_control_lease(h: Harness) -> None:
    registry, attachments, runtime = _leased_runtime(h)
    tid = runtime.terminal_id
    c1 = attachments.attach(tid, "client-1", role="control", rows=24, cols=80)
    obs = attachments.attach(tid, "client-2", role="observer", rows=24, cols=80)
    h.check("M8.1 控制权 generation 从 1 开始", c1.generation == 1, {"gen": c1.generation})
    h.check("M8.2 observer 与控制者同 generation（只读）", obs.generation == 1)
    h.check("M8.3 send 走控制权", attachments.send(c1, b"echo hi\r\n") == len(b"echo hi\r\n"))
    h.check("M8.4 resize 走控制权", attachments.resize(c1, 40, 132) is True)
    h.check("M8.5 尺寸跟随当前控制客户端", (runtime.rows, runtime.cols) == (40, 132))

    try:
        attachments.send(obs, b"nope")
        h.check("M8.6 observer 不能写入", False, "未报错")
    except NotControlLeaseError as exc:
        h.check("M8.6 observer 不能写入", True, str(exc))
    try:
        attachments.resize(obs, 50, 200)
        h.check("M8.7 observer 不能改尺寸", False, "未报错")
    except NotControlLeaseError as exc:
        h.check("M8.7 observer 不能改尺寸", True, str(exc))

    c2 = attachments.transfer_control(c1, to_client="client-3")
    h.check("M8.8 控制权转交 generation 自增", c2.generation == 2, {"gen": c2.generation})
    for label, fn in (
        ("M8.9 旧客户端写入被拒绝", lambda: attachments.send(c1, b"stale")),
        ("M8.10 旧客户端 resize 被拒绝", lambda: attachments.resize(c1, 10, 10)),
    ):
        try:
            fn()
            h.check(label, False, "未报错")
        except StaleLeaseError as exc:
            h.check(label, True, str(exc))
    h.check("M8.11 新控制者可用", attachments.send(c2, b"ok\r\n") == len(b"ok\r\n"))
    h.check("M8.12 control_holder 指向新控制者", attachments.control_holder(tid) == c2)
    attachments.detach(c2)
    h.check("M8.13 detach 后控制者为空", attachments.control_holder(tid) is None)
    leftovers = [pid for pid in (runtime._backend.pid,) if pid]  # noqa: SLF001 - 探针核对
    h.measure("M8.runtime pid (mock 非真实进程)", pid=leftovers)


# ---------------------------------------------------------------- M9: 扩展点


def t_attachment_namespace_and_detach_point(h: Harness) -> None:
    ids = {TerminalRegistry.new_terminal_id() for _ in range(200)}
    h.check("M9.1 terminal id 唯一", len(ids) == 200)
    h.check("M9.2 terminal id 有独立前缀", all(i.startswith("term_") for i in ids))
    h.check(
        "M9.3 与 session/worker 命名空间不同前缀",
        not any(i.startswith(("ses_", "worker_", "job_")) for i in ids),
    )
    try:
        require_detached_ownership(None)
        h.check("M9.4 显式 detach 扩展点未定型时显式失败", False, "未报错")
    except DetachedOwnershipNotImplemented as exc:
        h.check(
            "M9.4 显式 detach 扩展点未定型时显式失败",
            "lifecycle" in str(exc),
            "raised with dependency note",
        )


# ---------------------------------------------------------------- M10: 观察者


def t_screen_snapshot_is_state_not_tail(h: Harness) -> None:
    """真实 pyte 解析合成 VT 序列（库行为，非 PTY 行为）。"""
    try:
        from pty_contract import PyteScreenObserver, tail_text_view
    except Exception as exc:  # pragma: no cover
        h.measure("M10.skip pyte 不可用", error=repr(exc))
        return
    stream = (
        b"shell prompt line\r\n"
        + b"\x1b[?1049h"          # 进入备用屏
        + b"\x1b[22;0;0t"
        + b"\x1b[2J\x1b[H"
        + b"\x1b[1;31mRED-TUI-LINE\x1b[0m\r\n"
        + b"\x1b[3;10Hneeds restore point"
        + b"\x1b[?1004h"          # 焦点上报（TUI 常用）
        + b"\x1b[?9001h"          # ConPTY Win32 输入模式
    )
    observer = PyteScreenObserver(24, 80)
    observer.feed(stream)
    snap = observer.snapshot()
    h.check("M10.1 私有模式需 <<5 还原（1049 可见）", 1049 in snap.private_modes, {"decoded": sorted(snap.private_modes)})
    h.check("M10.2 raw 位保留原始编码", (1049 << 5) in snap.raw_mode_bits)
    h.check("M10.3 备用屏标志由模式位给出", snap.alternate_screen is True)
    h.check("M10.4 光标位置属于快照", snap.cursor == (2, 28), {"cursor": snap.cursor})
    h.check("M10.5 无滚动历史（pyte 限制）", snap.scrollback_lines == 0)
    h.check("M10.6 保真度显式声明 partial", snap.fidelity == "partial")
    h.check(
        "M10.7 pyte 不支持备用屏缓冲（能力声明）",
        PyteScreenObserver.SUPPORTS_ALTERNATE_SCREEN_BUFFER is False,
    )
    tail = tail_text_view(stream, window=4096)
    h.check(
        "M10.8 反例：尾部文本不含任何模式/备用屏信息",
        "\x1b" not in tail and "1049" not in tail,
        {"tail": tail.replace("\n", "|")[:120]},
    )
    h.measure(
        "M10.9 快照 vs 尾部文本信息量",
        snapshot_modes=sorted(snap.private_modes),
        snapshot_cursor=snap.cursor,
        tail_text_only=True,
        alt_modes=ALT_SCREEN_DEC_MODES,
    )


# ---------------------------------------------------------------- M11: driver 边界


def t_automation_driver_boundary(h: Harness) -> None:
    """公共核心不承载 CBC 业务菜单：driver 只拿到 runtime/observer/lease。"""
    from pty_contract import AutomationContext

    registry, attachments, runtime = _leased_runtime(h)
    token = attachments.attach(runtime.terminal_id, "driver-host", role="control")
    observer = None
    try:
        from pty_contract import PyteScreenObserver

        observer = PyteScreenObserver(24, 80)
    except Exception:
        h.measure("M11.skip pyte 不可用")
        return
    ctx = AutomationContext(
        terminal_id=runtime.terminal_id,
        runtime=runtime,
        observer=observer,
        control=token,
        attachments=attachments,
    )
    ctx.send_keys("\x1b", "\x1b")           # driver 负责 CBC 的 Esc Esc 语义
    ctx.send_text("\r")
    h.check(
        "M11.1 driver 只能通过 lease 写终端",
        len(runtime._backend.writes) == 2,  # noqa: SLF001
        {"writes": [w for w in runtime._backend.writes]},  # noqa: SLF001
    )
    h.check("M11.2 driver 不含终端生命周期 API", not hasattr(ctx, "close"))
    contract_src = open(os.path.join(os.path.dirname(__file__), "pty_contract.py"), encoding="utf-8").read()
    h.check(
        "M11.3 公共核心不含 CBC 菜单字面量",
        all(
            term not in contract_src
            for term in ("Restore and fork the conversation", "Never Mind")
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json-out", default=None)
    args = parser.parse_args()

    h = Harness(
        "mock",
        note="确定性契约逻辑测试（ScriptedBackend）。不代表真实 CLI/PTY 行为。",
    )
    h.section("MOCK 契约逻辑测试")
    for fn in (
        t_output_log_sequence_and_gap,
        t_output_log_single_chunk_over_cap,
        t_output_log_sustained_bounded,
        t_drain_reads_to_eof_after_process_exit,
        t_state_machine,
        t_cleanup_failure_retains_owner,
        t_registry_retains_failed_terminal,
        t_control_lease,
        t_attachment_namespace_and_detach_point,
        t_screen_snapshot_is_state_not_tail,
        t_automation_driver_boundary,
    ):
        h.run(fn)
    return emit(h, json_out=args.json_out, extra={"psutil_terminator_available": _has_psutil()})


def _has_psutil() -> bool:
    try:
        PsutilTreeTerminator()
        return True
    except Exception:
        return False


if __name__ == "__main__":
    sys.exit(main())
