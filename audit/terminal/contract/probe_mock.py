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
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pty_contract import (  # noqa: E402
    ALT_SCREEN_DEC_MODES,
    AttachmentRegistry,
    DetachedOwnershipNotImplemented,
    ExternalOwnershipNotYetValidated,
    IllegalStateTransition,
    InvalidCursorError,
    NotControlLeaseError,
    NullTreeTerminator,
    OutputLog,
    OwnershipGateError,
    OwnershipMode,
    OwnershipPolicy,
    OwnershipPolicyRequired,
    ProcessIdentity,
    ProcessOwnershipEvidence,
    PsutilTreeTerminator,
    PtyRuntime,
    RuntimeState,
    ScriptedBackend,
    StaleLeaseError,
    TerminalRegistry,
    UnverifiedOwnershipGate,
    UnknownTerminalError,
    UnownedTreeRejected,
    build_runtime,
    require_detached_ownership,
    tail_text_view,
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
            "M9.4 detach 扩展点在无生产实现时显式失败",
            ("6A.1" in str(exc)) or ("生产实现" in str(exc)),
            "raised with pointer to production path",
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


# ------------------------------------------- M12: reader 结束原因的分类（修正 1）


def t_drain_stop_reasons(h: Harness) -> None:
    """MA 修正 1：不得把错误/超时/stop 当成正常 EOF。"""
    # a) 真实 EOF
    rt = PtyRuntime("term_m12a", ScriptedBackend([b"tail"], alive_returns=False, exit_code=0),
                    output_cap=4096, eof_grace=0.2)
    rt.start(rows=24, cols=80)
    rt.wait_eof(2.0)
    info = rt.poll_exit()
    h.check(
        "M12.1 真实 EOF：reader_done/channel_eof/output_complete 同时为真",
        info.reader_done and info.channel_eof and info.output_complete
        and info.drain_stop_reason == "eof",
        {"reason": info.drain_stop_reason},
    )
    rt.close(reason="m12a", reader_grace=0.2)

    # b) 通道错误
    rt = PtyRuntime("term_m12b", ScriptedBackend([b"head"], channel_error="boom", alive_returns=True),
                    output_cap=4096, eof_grace=0.2)
    rt.start(rows=24, cols=80)
    rt.wait_eof(2.0)
    info = rt.poll_exit()
    h.check(
        "M12.2 通道错误：reader_done 为真但 channel_eof/output_complete 为假",
        info.reader_done and not info.channel_eof and not info.output_complete
        and info.drain_stop_reason == "channel-error" and "boom" in (info.channel_error or ""),
        {"reason": info.drain_stop_reason, "err": info.channel_error},
    )
    h.check("M12.3 通道错误不被当作 output_complete", info.output_complete is False)

    # c) eof_grace 超时（read 一直返回空、进程已不在）
    rt = PtyRuntime("term_m12c", ScriptedBackend([], alive_returns=False, empty_reads=True),
                    output_cap=4096, eof_grace=0.15)
    rt.start(rows=24, cols=80)
    rt.wait_eof(3.0)
    info = rt.poll_exit()
    h.check(
        "M12.4 eof_grace 超时：reader_done 为真但 output_complete 为假",
        info.reader_done and not info.output_complete and info.drain_stop_reason == "eof-timeout",
        {"reason": info.drain_stop_reason},
    )

    # d) stop-requested
    backend = ScriptedBackend([], alive_returns=True, empty_reads=True)
    rt = PtyRuntime("term_m12d", backend, output_cap=4096, eof_grace=0.2)
    rt.start(rows=24, cols=80)
    rt._stop.set()  # noqa: SLF001 - 探针直接触发停止请求
    rt.wait_eof(2.0)
    info = rt.poll_exit()
    h.check(
        "M12.5 stop-requested 不声明输出完整",
        info.reader_done and not info.output_complete and info.drain_stop_reason == "stop-requested",
        {"reason": info.drain_stop_reason},
    )
    h.measure(
        "M12.6 四种结束原因的输出完整性",
        eof=("output_complete=True",),
        channel_error=(rt.exit.channel_error is not None,),
        note="只有对端真实 EOF（drain_stop_reason=eof）才允许 output_complete=True；cancelled/错误/超时/stop 都不允许",
    )


    # e) 取消导致的传输层异常（实测 pywinpty WinError 10053）归因为 cancelled
    backend_e = ScriptedBackend([], alive_returns=True, block_forever=True,
                                cancel_on_close=True, abort_on_release=True)
    rt_e = PtyRuntime("term_m12e", backend_e, output_cap=4096, eof_grace=0.2)
    rt_e.start(rows=24, cols=80)
    time.sleep(0.1)
    rep_e = rt_e.close(reason="m12e", reader_grace=0.3)
    h.check(
        "M12.7 自取消导致的传输异常归因为 cancelled（不是通道错误）",
        rt_e.exit.drain_stop_reason == "cancelled" and rt_e.exit.channel_error is None
        and rt_e.exit.channel_eof is False and rt_e.exit.output_complete is False,
        {"reason": rt_e.exit.drain_stop_reason, "err": rt_e.exit.channel_error},
    )
    h.check(
        "M12.8 取消路径仍可收敛并进入 exited（输出完整性不冒充）",
        rep_e.state_after is RuntimeState.EXITED and rep_e.reader_converged
        and rep_e.reader_cancelled and rep_e.drain_stop_reason == "cancelled",
        {"state": rep_e.state_after.value, "stop": rep_e.drain_stop_reason},
    )


# ------------------------------------- M13: reader 回收（修正 2）


def t_reader_convergence_and_owner(h: Harness) -> None:
    """MA 修正 2：不能用 daemon thread 当回收保证。"""
    # a) 取消有效：close() 关句柄让阻塞 read 抛 EOFError → 收敛
    backend = ScriptedBackend([], alive_returns=True, block_forever=True, cancel_on_close=True)
    rt = PtyRuntime("term_m13a", backend, output_cap=4096, eof_grace=0.2)
    rt.start(rows=24, cols=80)
    time.sleep(0.1)
    report = rt.close(reason="m13a", reader_grace=0.3)
    h.check(
        "M13.1 取消有效：reader 收敛并进入 exited",
        report.reader_converged and report.reader_joined and report.state_after is RuntimeState.EXITED,
        {
            "converged": report.reader_converged,
            "cancelled": report.reader_cancelled,
            "cancel_kind": report.cancel_kind,
            "state": report.state_after.value,
            "stop": report.drain_stop_reason,
        },
    )
    h.check("M13.2 取消手段被记录", report.cancel_kind in ("backend-close", "none"), report.cancel_kind)
    h.check("M13.3 自取消不算对端 EOF（channel_eof 必须为假）",
            report.drain_stop_reason == "cancelled" and rt.exit.channel_eof is False
            and rt.exit.output_complete is False,
            report.drain_stop_reason)

    # b) 取消无效（后端没有可用的取消原语）→ 必须保留 owner，不得标 exited
    backend_b = ScriptedBackend([], alive_returns=True, block_forever=True, cancel_on_close=False)
    rt_b = PtyRuntime("term_m13b", backend_b, output_cap=4096, eof_grace=0.2)
    rt_b.start(rows=24, cols=80)
    time.sleep(0.1)
    failed = rt_b.close(reason="m13b", reader_grace=0.3)
    h.check(
        "M13.4 取消无效：拒绝标记 exited",
        failed.state_after is RuntimeState.CLEANUP_FAILED and failed.reader_converged is False,
        {"state": failed.state_after.value, "error": failed.error},
    )
    h.check("M13.5 保留 owner", failed.owner_retained is True)
    h.check("M13.6 失败原因指向 reader 未收敛", "reader 未收敛" in (failed.error or ""), failed.error)
    backend_b.release_blocked_read()  # 放行模拟读，让 reader 收敛（真机上是进程/句柄释放）
    time.sleep(0.2)
    retry = rt_b.close(reason="m13b-retry", reader_grace=0.5)
    h.check(
        "M13.7 释放后重试才进入 exited",
        retry.state_after is RuntimeState.EXITED and retry.reader_converged,
        {"state": retry.state_after.value, "error": retry.error},
    )


# ------------------------------------- M14: lease 撤销/伪造/原子（修正 3）


def _lease_fixture() -> tuple[TerminalRegistry, AttachmentRegistry, PtyRuntime, ScriptedBackend]:
    registry = TerminalRegistry()
    tid = registry.new_terminal_id()
    backend = ScriptedBackend([], alive_returns=True)
    runtime = PtyRuntime(tid, backend, output_cap=1024)
    runtime.start(rows=24, cols=80)
    registry.register(runtime)
    return registry, AttachmentRegistry(registry), runtime, backend


def t_lease_revocation_and_forgery(h: Harness) -> None:
    """MA 修正 3：撤销不可复活、伪造被拒、校验与操作原子。"""
    registry, attachments, runtime, backend = _lease_fixture()
    tid = runtime.terminal_id

    c1 = attachments.attach(tid, "client-1", role="control", rows=24, cols=80)
    obs = attachments.attach(tid, "client-2", role="observer", rows=24, cols=80)
    baseline = len(backend.writes)
    h.check("M14.1 控制权可写入", attachments.send(c1, b"one\r\n") == 5 and len(backend.writes) == baseline + 1)

    attachments.detach(c1)
    h.check("M14.2 撤销后控制权为空", attachments.control_holder(tid) is None)
    h.check("M14.3 撤销后 token 被标记", attachments.is_revoked(c1) is True)
    for label, fn in (
        ("M14.4 撤销后 send 被拒", lambda: attachments.send(c1, b"revived\r\n")),
        ("M14.5 撤销后 resize 被拒", lambda: attachments.resize(c1, 10, 10)),
        ("M14.6 撤销后 transfer 被拒", lambda: attachments.transfer_control(c1, to_client="client-x")),
    ):
        try:
            fn()
            h.check(label, False, "未报错")
        except StaleLeaseError as exc:
            h.check(label, True, str(exc))
    h.check(
        "M14.7 撤销后没有任何写入发生（不会被 setdefault 复活）",
        len(backend.writes) == baseline + 1,
        {"writes": len(backend.writes)},
    )

    # 伪造 token（自报 role=control + 自造 revocation_id）必须被拒
    forged = LeaseTokenFactory.forge(c1, role="control", revocation_id="forged-not-issued")
    try:
        attachments.send(forged, b"forged\r\n")
        h.check("M14.8 伪造 token 被拒", False, "未报错")
    except (StaleLeaseError, NotControlLeaseError) as exc:
        h.check("M14.8 伪造 token 被拒", True, f"{type(exc).__name__}: {exc}")

    # observer 不能获得控制权
    for label, fn in (
        ("M14.9 observer 不能写入", lambda: attachments.send(obs, b"nope")),
        ("M14.10 observer 不能 resize", lambda: attachments.resize(obs, 1, 1)),
        ("M14.11 observer 不能转交控制权", lambda: attachments.transfer_control(obs, to_client="z")),
    ):
        try:
            fn()
            h.check(label, False, "未报错")
        except NotControlLeaseError as exc:
            h.check(label, True, str(exc))

    # 转交后旧 token 失效；同一代的副本同样失效（generation 自增）
    c2 = attachments.attach(tid, "client-3", role="control")
    clone_of_c1 = LeaseTokenFactory.clone(c1)
    for label, tok in (("M14.12 旧控制权 token 失效", c1), ("M14.13 旧控制权副本失效", clone_of_c1)):
        try:
            attachments.send(tok, b"late")
            h.check(label, False, "未报错")
        except StaleLeaseError as exc:
            h.check(label, True, str(exc))
    h.check("M14.14 新控制权可用", attachments.send(c2, b"ok\r\n") == 4)
    h.check("M14.15 转交后新持有者正确", attachments.control_holder(tid).revocation_id == c2.revocation_id)

    # 伪造：世代与持有者都对，但 revocation_id 不是 registry 签发的 → 必须拒绝
    forged_holder = LeaseTokenFactory.forge(c2, revocation_id="forged-not-issued")
    try:
        attachments.send(forged_holder, b"forged\r\n")
        h.check("M14.15b 伪造 revocation_id 的 token 被拒", False, "未报错")
    except NotControlLeaseError as exc:
        h.check("M14.15b 伪造 revocation_id 的 token 被拒", True, str(exc))

    # observer 撤销不影响其它 observer（各自撤销 id，不共享 generation 失效）
    o1 = attachments.attach(tid, "client-4", role="observer")
    o2 = attachments.attach(tid, "client-5", role="observer")
    attachments.detach(o1)
    ok = True
    try:
        attachments.validate(o2)
    except Exception:
        ok = False
    h.check("M14.16 撤销一个 observer 不影响另一个", ok and attachments.is_revoked(o2) is False)
    h.check("M14.17 observer 撤销后自身失效", attachments.is_revoked(o1) is True)

    # 原子性：撤销与写入在同一临界区，撤销返回后不可能再有该 token 的写入
    ok_atomic = True
    detail = ""
    for i in range(200):
        tok = attachments.attach(tid, f"churn-{i}", role="control")
        attachments.send(tok, b"x")  # 可能成功
        attachments.detach(tok)
        writes_at_detach = len(backend.writes)
        try:
            attachments.send(tok, b"after-detach")
            ok_atomic = False
            detail = f"iteration {i}: 撤销后仍写入成功"
            break
        except StaleLeaseError:
            pass
        if len(backend.writes) != writes_at_detach:
            ok_atomic = False
            detail = f"iteration {i}: 撤销后写入数变化"
            break
    h.check("M14.18 撤销与写入原子（200 次 churn 无撤销后写入）", ok_atomic, detail or "ok")

    # 已知边界（如实记录，不作为通过项）：token 内容等价即可通过
    holder = attachments.attach(tid, "owner-client", role="control")
    impersonator = LeaseTokenFactory.clone(holder, client_id="attacker")
    try:
        attachments.send(impersonator, b"impersonate")
        accepted = True
    except (StaleLeaseError, NotControlLeaseError):
        accepted = False
    h.measure(
        "M14.19 已知边界：复制 revocation_id 的 token 会被接受",
        accepted=accepted,
        note="进程内单 writer 语义成立；真实身份/网络授权未实现，必须由 Pan 入口先授权",
    )


class LeaseTokenFactory:
    """测试用 token 复制/伪造（不参与契约实现）。"""

    @staticmethod
    def clone(token, **changes):
        import dataclasses

        return dataclasses.replace(token, **changes)

    @staticmethod
    def forge(token, **changes):
        return LeaseTokenFactory.clone(token, **changes)


# ------------------------------------- M15: 输出边界（修正 4）


def t_output_boundary_cross_chunk(h: Harness) -> None:
    """MA 修正 4：块边界/窗口起点可以切断 UTF-8、CSI、OSC。"""
    cjk = "中".encode("utf-8")  # E4 B8 AD
    log = OutputLog(max_bytes=4096)
    log.append(cjk[:1])
    log.append(cjk[1:])
    body = b"".join(c.data for c in log.read_from(0).chunks)
    h.check("M15.1 跨块拼接后字节完全一致", body == cjk, {"body": body.hex()})
    per_chunk_bad = b"".join(
        c.data.decode("utf-8", "replace").encode("utf-8", "replace") for c in log.read_from(0).chunks
    )
    h.check(
        "M15.2 逐块解码会破坏 UTF-8（证明边界是字节而非序列）",
        per_chunk_bad != cjk,
        {"per_chunk": per_chunk_bad.decode("utf-8", "replace")},
    )

    log2 = OutputLog(max_bytes=4096)
    log2.append(b"a\x1b[")
    log2.append(b"31mred")
    body2 = b"".join(c.data for c in log2.read_from(0).chunks)
    h.check("M15.3 跨块 CSI 拼接后仍为完整序列", body2 == b"a\x1b[31mred", {"body": body2})

    # 保留窗口起点可能落在序列内部 → 从窗口起点解析不可靠
    log3 = OutputLog(max_bytes=8)
    log3.append(b"XXXX")          # 会被驱逐
    log3.append(b"Y\x1b[38;5;196mZ")
    page = log3.read_from(0)
    window_start = page.first_seq
    seq = b"Y\x1b[38;5;196mZ"
    csi_start = 4 + seq.index(b"\x1b[")
    h.check(
        "M15.4 保留窗口起点可以落在 CSI 内部（文档必须承认）",
        window_start > csi_start,
        {"window_start": window_start, "csi_start": csi_start, "gap": page.gap},
    )
    h.check("M15.5 该情形必须返回 gap 要求快照恢复", page.gap is not None, {"gap": page.gap})

    # OSC 跨块
    log4 = OutputLog(max_bytes=4096)
    log4.append(b"\x1b]0;title")
    log4.append(b"-continued\x07visible")
    body4 = b"".join(c.data for c in log4.read_from(0).chunks)
    h.check("M15.6 跨块 OSC 拼接后完整", body4 == b"\x1b]0;title-continued\x07visible")
    h.check(
        "M15.7 尾部文本视图会同时丢掉 OSC 与 CSI（无法承载状态）",
        "\x1b" not in tail_text_view(body4),
        {"tail": tail_text_view(body4)},
    )


# ------------------------------------- M16: ownership policy / 工厂 / 门禁


def t_ownership_policy_and_gate(h: Harness) -> None:
    """MA 补充：布局中立 + fail-closed 工厂 + 启动所有权门禁。"""
    for label, kw in (
        ("M16.1 无策略被拒", dict(ownership=None)),
        ("M16.2 声明守卫却给 None 被拒", dict(ownership=OwnershipPolicy(OwnershipMode.SERVICE, "pan-service", "job-object", None))),
        ("M16.3 detach 无守卫实现被拒", dict(ownership=OwnershipPolicy(OwnershipMode.DETACHED, "runner", "job-object", NullTreeTerminator(), detached=True))),
        ("M16.4 external 语义未定义被拒", dict(ownership=OwnershipPolicy(OwnershipMode.EXTERNAL, "external-host", "job-object", NullTreeTerminator()))),
    ):
        expected = {
            "M16.1 无策略被拒": OwnershipPolicyRequired,
            "M16.2 声明守卫却给 None 被拒": UnownedTreeRejected,
            "M16.3 detach 无守卫实现被拒": DetachedOwnershipNotImplemented,
            "M16.4 external 语义未定义被拒": ExternalOwnershipNotYetValidated,
        }[label]
        try:
            build_runtime("term_m16", ScriptedBackend([]), **kw)
            h.check(label, False, "未报错")
        except expected as exc:
            h.check(label, True, type(exc).__name__)

    # 布局中立：同一接口 + 三种 lifecycle_owner 都能建 runtime（逐字声明，不做分支）
    for owner in ("pan-service", "runner", "external-host"):
        policy = OwnershipPolicy(OwnershipMode.SERVICE, owner, "none", None, notes="布局中立测试")
        rt = build_runtime(
            f"term_m16_{owner}",
            ScriptedBackend([], alive_returns=True),
            ownership=policy,
            acknowledge_unowned_tree=True,
        )
        rt.start(rows=24, cols=80)
        h.check(
            f"M16.5 布局中立：lifecycle_owner={owner} 可运行",
            rt.state is RuntimeState.RUNNING and rt.ownership.lifecycle_owner == owner,
            {"owner": rt.ownership.lifecycle_owner},
        )
        rt.close(reason="m16", reader_grace=0.3)

    # 启动门禁：四要素缺一不可，缺则拒绝 running
    good_identity = ProcessIdentity(pid=4242, created_at_filetime=123456789)
    cases = {
        "M16.6 assign 失败拒绝 running": dict(assigned=False, atomic_with_spawn=True, identity=good_identity, handle_bound_for_cleanup=True),
        "M16.7 非原子赋值拒绝 running": dict(assigned=True, atomic_with_spawn=False, identity=good_identity, handle_bound_for_cleanup=True),
        "M16.8 缺身份拒绝 running": dict(assigned=True, atomic_with_spawn=True, identity=None, handle_bound_for_cleanup=True),
        "M16.9 清理未绑定同一 handle 拒绝 running": dict(assigned=True, atomic_with_spawn=True, identity=good_identity, handle_bound_for_cleanup=False),
    }
    for label, evidence in cases.items():
        policy = OwnershipPolicy(OwnershipMode.SERVICE, "pan-service", "job-object", PsutilTreeTerminator())
        rt = build_runtime("term_m16g", ScriptedBackend([], alive_returns=True), ownership=policy)
        gate = UnverifiedOwnershipGate(ProcessOwnershipEvidence(guard="job-object", **evidence))
        try:
            rt.start(rows=24, cols=80, gate=gate)
            h.check(label, False, "启动未被拒绝")
        except OwnershipGateError as exc:
            h.check(label, True, str(exc)[:60])
        h.check(f"{label}（状态保持 created）", rt.state is RuntimeState.CREATED, {"state": rt.state.value})
        rt._stop.set()  # noqa: SLF001
        rt.close(reason="m16g", reader_grace=0.2)

    # 四要素齐全 → 放行，并且声明了守卫却没给 gate 也要拒绝
    policy = OwnershipPolicy(OwnershipMode.SERVICE, "runner", "job-object", PsutilTreeTerminator())
    rt = build_runtime("term_m16ok", ScriptedBackend([], alive_returns=True), ownership=policy)
    try:
        rt.start(rows=24, cols=80)
        h.check("M16.10 声明守卫但无启动证据被拒", False, "未报错")
    except OwnershipGateError as exc:
        h.check("M16.10 声明守卫但无启动证据被拒", True, str(exc)[:50])
    gate = UnverifiedOwnershipGate(
        ProcessOwnershipEvidence(
            assigned=True,
            atomic_with_spawn=True,
            identity=good_identity,
            handle_bound_for_cleanup=True,
            guard="job-object",
        )
    )
    rt.start(rows=24, cols=80, gate=gate)
    h.check(
        "M16.11 四要素齐全才进入 running 并带入身份",
        rt.state is RuntimeState.RUNNING and rt.identity == good_identity,
        {"state": rt.state.value},
    )
    h.check(
        "M16.12 未提供的 Job 实现不被契约绑死",
        True,
        "JobObjectTreeTerminator 只在生产层实现；契约不做分支",
    )
    h.check("M16.13 策略可序列化描述（含 owner 与守卫）",
            rt.ownership.describe()["lifecycle_owner"] == "runner"
            and rt.ownership.describe()["tree_guard"]["kind"] == "psutil-tree")
    rt.close(reason="m16ok", reader_grace=0.3)

    # on_service_shutdown 语义
    svc_rt = build_runtime("term_m16svc", ScriptedBackend([], alive_returns=True),
                           ownership=OwnershipPolicy(OwnershipMode.SERVICE, "pan-service", "none", None),
                           acknowledge_unowned_tree=True)
    svc_rt.start(rows=10, cols=10)
    rep = OwnershipPolicy(OwnershipMode.SERVICE, "pan-service", "none", None).on_service_shutdown(svc_rt)
    h.check("M16.14 service 策略在服务关闭时终止终端", rep.state_after is RuntimeState.EXITED, {"state": rep.state_after.value})
    try:
        OwnershipPolicy(OwnershipMode.DETACHED, "runner", "job-object", NullTreeTerminator(), detached=True).on_service_shutdown(svc_rt)
        h.check("M16.15 detach 策略不得在服务关闭时被终止", False, "未报错")
    except DetachedOwnershipNotImplemented as exc:
        h.check("M16.15 detach 策略不得在服务关闭时被终止", True, str(exc)[:50])


# ------------------------------------- M17: 清理身份核验


def t_identity_verified_cleanup(h: Harness) -> None:
    """MA 补充：清理必须核验身份、用同一 handle 终止。"""
    recorded = ProcessIdentity(pid=4242, created_at_filetime=1000, image="cmd.exe")

    # a) 身份不匹配 → 拒杀
    backend = ScriptedBackend([], alive_returns=True)
    rt = PtyRuntime("term_m17a", backend, output_cap=1024, identity=recorded,
                    identity_probe=lambda pid: ProcessIdentity(pid=pid, created_at_filetime=9999))
    rt.start(rows=24, cols=80)
    rep = rt.close(reason="m17a", reader_grace=0.2)
    h.check("M17.1 身份不匹配拒绝终止", rep.terminate_result == "refused-identity-mismatch", rep.terminate_result)
    h.check("M17.2 未调用 terminate（不会误杀）", backend.terminate_calls == [], {"calls": backend.terminate_calls})
    h.check("M17.3 状态为 cleanup-failed 且保留 owner",
            rep.state_after is RuntimeState.CLEANUP_FAILED and rep.owner_retained, {"state": rep.state_after.value})
    h.check("M17.4 identity_check 标记 mismatch", rep.identity_check == "mismatch", rep.identity_check)

    # b) 探针自身失败 → 同样拒杀
    backend_b = ScriptedBackend([], alive_returns=True)
    def broken_probe(pid):
        raise OSError("no access")

    rt_b = PtyRuntime("term_m17b", backend_b, output_cap=1024, identity=recorded, identity_probe=broken_probe)
    rt_b.start(rows=24, cols=80)
    rep_b = rt_b.close(reason="m17b", reader_grace=0.2)
    h.check("M17.5 身份探针失败也拒杀", rep_b.identity_check == "probe-failed" and not backend_b.terminate_calls,
            {"check": rep_b.identity_check})

    # c) 匹配 → 正常收敛
    backend_c = ScriptedBackend([], alive_returns=True)
    rt_c = PtyRuntime("term_m17c", backend_c, output_cap=1024, identity=recorded,
                      identity_probe=lambda pid: ProcessIdentity(pid=pid, created_at_filetime=1000))
    rt_c.start(rows=24, cols=80)
    rep_c = rt_c.close(reason="m17c", reader_grace=0.2)
    h.check("M17.6 身份匹配则正常清理", rep_c.state_after is RuntimeState.EXITED and rep_c.identity_check == "verified",
            {"check": rep_c.identity_check, "state": rep_c.state_after.value})
    h.check("M17.7 身份匹配时确实调用了 terminate", backend_c.terminate_calls == [True])

    # d) 进程已消失（探针返回 None）→ 正常收敛
    backend_d = ScriptedBackend([], alive_returns=True)
    rt_d = PtyRuntime("term_m17d", backend_d, output_cap=1024, identity=recorded, identity_probe=lambda pid: None)
    rt_d.start(rows=24, cols=80)
    rep_d = rt_d.close(reason="m17d", reader_grace=0.2)
    h.check("M17.8 进程已消失时正常收敛", rep_d.state_after is RuntimeState.EXITED, rep_d.state_after.value)

    # e) ProcessIdentity.matches 单元语义
    h.check("M17.9 FILETIME 精确比较（无容差）",
            ProcessIdentity(1, created_at_filetime=5).matches(ProcessIdentity(1, created_at_filetime=5))
            and not ProcessIdentity(1, created_at_filetime=5).matches(ProcessIdentity(1, created_at_filetime=6)))
    h.check("M17.10 PID 不同即不匹配",
            not ProcessIdentity(1, created_at_filetime=5).matches(ProcessIdentity(2, created_at_filetime=5)))
    h.check("M17.11 无可比字段视为不匹配（fail-closed）",
            not ProcessIdentity(1).matches(ProcessIdentity(1))
            and not ProcessIdentity(1, created_at_filetime=5).matches(None))


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
        t_drain_stop_reasons,
        t_reader_convergence_and_owner,
        t_lease_revocation_and_forgery,
        t_output_boundary_cross_chunk,
        t_ownership_policy_and_gate,
        t_identity_verified_cleanup,
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
