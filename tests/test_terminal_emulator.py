"""Pan Terminal P1：权威仿真器 HeadlessEmulator（Windows + Node sidecar）。

覆盖任务卡与计划 §8/§8.5：

- headless↔headless 保真矩阵与协议 A 恢复（主/备屏、resize、split UTF-8/CSI/OSC、
  pending tail 的 cursor 边界）；
- browserless 大量输出（>256 KiB）与 OutputLog 游标再次驱逐后的快照游标；
- ``snapshot.cursor`` = 已应用绝对字节（滞后于 producer frontier，不冒充）；
- 控制命令有界 timeout；队列中过期的控制命令**不迟到执行**；
- feed 满队列 sticky（``feed_lag``）与严格有界诊断；
- MA 生产化缺口回归（**先失败后通过**，见 audit 证据：
  4096 事件上限不得掩盖未知序列；feed 执行异常 sticky 降级；
  rejected/诊断有界；reset 基线按命令实际执行位置；reset 清除旧 gap 且不假 full）；
- sidecar 崩溃/卡住/失败关闭重试；runner 硬死不残留 sidecar；无进程泄漏。

r2 返工回归（独立审查 `1daff2a6` 的确定失败/缺口，**先失败后通过**）：

- 根死孙活持有 stdout 时 ``close`` 必须自身有界（先整 Job 清理与核验，
  再 join 线程，最后关闭流）；
- 构造失败清理有界，且异常携带可重试 owner / cleanup 入口与残留信息；
- stderr join / 流关闭失败纳入 ``closed`` 记账并保留可重试；
- assign 后 resume 前的 Job 成员/active 核验（false/unknown/query 失败 fail-closed）；
- 迟到 reset ack 只回填一次实际执行账本；确认前结构性禁止旧 cursor 续流；
- ``resize_wait`` 提供确认面（非阻塞 ``resize`` 不得被推断三方一致）。

边界：本文件全部是 headless↔headless 对比；真实浏览器渲染保真属 P3，不在
本文件声称。仅使用自建 sidecar/fake sidecar 进程与 pytest 临时目录；等待均有界
（pytest.ini timeout=300 为兜底看门狗）。任何兜底终止只针对**自建有身份**
（同 handle raw FILETIME + Wait）的进程，不扫描陌生 PID。
"""

from __future__ import annotations

import io
import json
import shutil
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core.terminal import emulator as emulator_module
from packages.core.terminal import guard as guard_module
from packages.core.terminal import identity as identity_module
from packages.core.terminal.contracts import (
    Fidelity,
    ProcessIdentity,
    ProcessStatus,
    Recovery,
)
from packages.core.terminal.emulator import (
    EmulatorProtocolError,
    EmulatorStartupError,
    EmulatorUnavailableError,
    HeadlessEmulator,
    _FrameReader,
    encode_frame,
)
from packages.core.terminal.output import OutputLog

REPO_ROOT = Path(__file__).resolve().parent.parent
NODE_BINARY = shutil.which("node")

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or not NODE_BINARY,
    reason="HeadlessEmulator 生产宿主仅 Windows + Node sidecar",
)


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def emu_factory():
    created: list[HeadlessEmulator] = []

    def make(**kwargs) -> HeadlessEmulator:
        kwargs.setdefault("startup_timeout", 30.0)
        emu = HeadlessEmulator(**kwargs)
        created.append(emu)
        return emu

    yield make

    leaks = []
    for emu in created:
        report = emu.close(timeout=12.0)
        if not report.closed:
            leaks.append(report.as_dict())
    assert not leaks, f"残留 emulator 未收敛：{leaks}"


def _feed(emu: HeadlessEmulator, data: bytes, *, size: int = 4096, start: int | None = None) -> int:
    seq = emu.producer_frontier if start is None else int(start)
    for offset in range(0, len(data), size):
        part = data[offset : offset + size]
        emu.feed_at(seq, part)
        seq += len(part)
    return seq


def _compare_after_protocol_a(emu_factory, stream: bytes, *, cols=80, rows=24, chunk=13):
    """协议 A 对拍：参考端连续喂 → 状态重建 + 从 cursor 重拉 → 终态一致。"""
    ref = emu_factory(cols=cols, rows=rows)
    _feed(ref, stream, size=chunk)
    ref_snap = ref.snapshot(timeout=60)
    cursor = ref_snap.cursor
    restored = emu_factory(cols=cols, rows=rows, start_cursor=cursor)
    # 1) 重建序列化屏幕状态；2) 从 cursor 重拉原字节（不预喂 pending tail）
    assert restored.restore_screen(ref_snap.serialized_screen)
    _feed(restored, stream[cursor:], start=cursor, size=max(1, chunk // 2))
    got = restored.snapshot(timeout=60)
    return ref_snap, got


TEXT = "中文A😀B mixed".encode("utf-8")


# ---------------------------------------------------------------------------
# 协议/构造边界
# ---------------------------------------------------------------------------


def test_construct_ready_identity_and_close(emu_factory):
    emu = emu_factory(cols=100, rows=30)
    assert emu.sidecar_pid and emu.sidecar_pid > 0
    assert emu.engine_alive
    diag = emu.diagnostics()
    # raw FILETIME 以十进制字符串对外（64 位身份，禁 JS 浮点）
    assert isinstance(diag["sidecar_filetime"], str)
    assert len(diag["sidecar_filetime"]) >= 10
    assert "xterm-headless/6.0.0" in diag["engine"]
    assert "serialize/0.14.0" in diag["engine"]
    # 真实原语证据：sidecar 确实在自有 Job 中（KILL_ON_CLOSE 兜底）
    members = emu._guard.member_pids()
    assert emu.sidecar_pid in members
    assert emu._guard.active_processes() and emu._guard.active_processes() >= 1
    report = emu.close(timeout=12.0)
    assert report.closed and report.graceful and report.process_exited and report.guard_closed
    assert not emu.engine_alive
    # close 幂等
    assert emu.close(timeout=5.0).closed is True


def test_start_cursor_beyond_53_bits_travels_as_string(emu_factory):
    start = 2**53 + 987654
    emu = emu_factory(start_cursor=start)
    emu.feed_at(start, b"XX")
    snap = emu.snapshot(timeout=15)
    assert snap.cursor == start + 2
    assert snap.fidelity is Fidelity.FULL
    # 64 位偏移在 Python 账本里保持精确（协议层用十进制字符串承载）
    assert emu.diagnostics()["applied_cursor"] == str(start + 2)


def test_missing_node_binary_is_unavailable():
    with pytest.raises(EmulatorUnavailableError):
        HeadlessEmulator(node_binary=str(REPO_ROOT / "no-such-node-binary.exe"))


def test_missing_sidecar_script_is_unavailable():
    with pytest.raises(EmulatorUnavailableError):
        HeadlessEmulator(sidecar_path=str(REPO_ROOT / "no-such-sidecar.mjs"))


def test_frame_helpers_reject_oversize_and_truncation():
    frame = encode_frame({"v": 1, "type": "hello"}, b"\x00" * 8)
    total, header_len = struct.unpack("<II", frame[:8])
    assert total == len(frame) - 8
    reader = _FrameReader(io.BytesIO(frame))
    header, payload = reader.read_frame()
    assert header["type"] == "hello"
    assert payload == b"\x00" * 8
    assert reader.read_frame() is None  # 干净 EOF
    with pytest.raises(EmulatorProtocolError):
        _FrameReader(io.BytesIO(frame[:10])).read_frame()  # 断帧
    with pytest.raises(EmulatorProtocolError):
        encode_frame({"v": 1}, b"\x00" * (emulator_module.FRAME_MAX_BYTES + 1))


# ---------------------------------------------------------------------------
# headless↔headless 矩阵与协议 A 恢复
# ---------------------------------------------------------------------------


def test_main_screen_matrix_and_recovery(emu_factory):
    stream = (
        b"$ echo hello\r\n"
        b"hello\r\n"
        b"\x1b[31mRED\x1b[0m \x1b[1;32mGREEN\x1b[0m\r\n"
        + b"".join(b"line-%03d\r\n" % i for i in range(60))
        + b"\x1b[2;1Hmiddle\x1b[K"
        + b"\x1b[10;5H\x1b[4mbold-under\x1b[0m"
    )
    ref_snap, got = _compare_after_protocol_a(emu_factory, stream)
    assert ref_snap.fidelity is Fidelity.FULL
    assert ref_snap.recovery is Recovery.FULL
    assert got.fidelity is Fidelity.FULL
    assert got.serialized_screen == ref_snap.serialized_screen


def test_alternate_screen_matrix_and_recovery(emu_factory):
    stream = (
        b"normal-before\r\n"
        b"\x1b[?1049h"
        b"\x1b[2J\x1b[H"
        b"ALT-SCREEN-CONTENT\r\n"
        b"second-line\x1b[?1049l"
        b"after-leave\r\n"
    )
    ref_snap, got = _compare_after_protocol_a(emu_factory, stream, chunk=7)
    assert ref_snap.fidelity is Fidelity.FULL
    assert got.serialized_screen == ref_snap.serialized_screen


def test_split_utf8_1_4_rest_matches_continuous(emu_factory):
    ref = emu_factory()
    _feed(ref, TEXT, size=len(TEXT))
    split = emu_factory()
    _feed(split, TEXT[:1], size=1)
    _feed(split, TEXT[1:5], size=1)
    _feed(split, TEXT[5:], size=1)
    a = ref.snapshot(timeout=30)
    b = split.snapshot(timeout=30)
    assert a.serialized_screen == b.serialized_screen
    assert b.fidelity is Fidelity.FULL


def test_holdback_split_csi_and_protocol_a_recovery(emu_factory):
    p1 = b"AB\x1b[3"
    p2 = b"1mRED\x1b[0m"
    ref = emu_factory()
    _feed(ref, p1, size=len(p1))
    snap = ref.snapshot(timeout=15)
    # 未完成的 CSI 被 hold back：cursor 停在 clean 边界，不冒充 producer frontier
    assert snap.cursor == 2
    assert ref.producer_frontier == len(p1)
    restored = emu_factory(start_cursor=snap.cursor)
    assert restored.restore_screen(snap.serialized_screen)
    _feed(restored, (p1 + p2)[snap.cursor :], start=snap.cursor, size=3)
    _feed(ref, p2, size=3)
    a = ref.snapshot(timeout=30)
    b = restored.snapshot(timeout=30)
    assert a.serialized_screen == b.serialized_screen
    assert a.fidelity is Fidelity.FULL


def test_holdback_split_osc_degrades_partial_and_recovers(emu_factory):
    p1 = b"X\x1b]0;title"
    p2 = b"-done\x07Y"
    ref = emu_factory()
    _feed(ref, p1, size=len(p1))
    snap = ref.snapshot(timeout=15)
    assert snap.cursor == 1
    restored = emu_factory(start_cursor=snap.cursor)
    assert restored.restore_screen(snap.serialized_screen)
    _feed(restored, (p1 + p2)[snap.cursor :], start=snap.cursor, size=2)
    _feed(ref, p2, size=2)
    a = ref.snapshot(timeout=30)
    b = restored.snapshot(timeout=30)
    assert a.serialized_screen == b.serialized_screen
    # OSC 标题不序列化（实测能力表）：检测到即 partial，不得静默 full
    assert a.fidelity is Fidelity.PARTIAL
    assert "WINDOW_TITLE_OSC" in a.note


def test_snapshot_cursor_excludes_pending_tail(emu_factory):
    emu = emu_factory()
    emu.feed_at(0, b"\xe4\xb8")  # "中" 的前两个字节（未完成 UTF-8）
    snap = emu.snapshot(timeout=15)
    assert snap.cursor == 0
    assert emu.producer_frontier == 2
    emu.feed_at(2, b"\xad")
    snap2 = emu.snapshot(timeout=15)
    assert snap2.cursor == 3


def test_resize_queue_ordering_matches_reference(emu_factory):
    ref = emu_factory(cols=80, rows=24)
    emu = emu_factory(cols=80, rows=24)
    first = b"first-line-" + b"a" * 60 + b"\r\n"
    for instance in (ref, emu):
        _feed(instance, first, size=len(first))
        instance.resize(30, 100)
        _feed(instance, b"after-resize\r\n", size=5)
    a = ref.snapshot(timeout=30)
    b = emu.snapshot(timeout=30)
    assert b.rows == 30 and b.cols == 100
    assert b.serialized_screen == a.serialized_screen


# ---------------------------------------------------------------------------
# browserless 大量输出 + OutputLog 游标再次驱逐
# ---------------------------------------------------------------------------


def test_browserless_large_output_and_log_cursor_eviction(emu_factory):
    log = OutputLog(max_bytes=64 * 1024)
    emu = emu_factory()
    ref = emu_factory()
    lines = b"".join(b"row-%06d-payload-abcdefghij\r\n" % i for i in range(9000))
    assert len(lines) > 256 * 1024
    for offset in range(0, len(lines), 8192):
        part = lines[offset : offset + 8192]
        seq = log.append(part)
        emu.feed_at(seq, part)
        ref.feed_at(seq, part)
    total = len(lines)
    snap = emu.snapshot(timeout=90)
    ref_snap = ref.snapshot(timeout=90)
    assert snap.cursor == total
    # 无浏览器：仿真器全程消费；窗口驱逐只影响客户端游标
    assert log.first_retained_seq > 0
    assert log.first_retained_seq < snap.cursor
    page = log.read_from(snap.cursor)
    assert page.gap is None
    assert snap.serialized_screen == ref_snap.serialized_screen


# ---------------------------------------------------------------------------
# applied 滞后 / 控制过期
# ---------------------------------------------------------------------------


def test_snapshot_cursor_is_applied_not_producer_frontier(emu_factory):
    emu = emu_factory()
    assert emu._test_stall(600)
    big = b"x" * 50000
    emu.feed_at(0, big)
    t0 = time.monotonic()
    snap = emu.snapshot(timeout=0.2)
    elapsed = time.monotonic() - t0
    assert elapsed < 1.5
    assert emu.producer_frontier == 50000
    assert snap.cursor < 50000  # 未应用的数据不得冒充 cursor
    assert snap.fidelity is Fidelity.UNAVAILABLE


def test_expired_control_never_executes_late(emu_factory):
    emu = emu_factory(control_timeout=0.3)
    assert emu._test_stall(1500)
    t0 = time.monotonic()
    snap = emu.reset_baseline()
    assert time.monotonic() - t0 < 1.2
    assert "timeout" in snap.note
    time.sleep(1.8)
    diag = emu.diagnostics()
    assert diag["counters"]["control_expired_skipped"] >= 1
    assert diag["reset_count"] == 0
    snap2 = emu.snapshot(timeout=15)
    assert "BASELINE_RESET_FRESH_VIEW" not in snap2.note


def test_feed_enqueue_never_waits_for_node(emu_factory):
    emu = emu_factory(max_queue_bytes=64 * 1024)
    assert emu._test_stall(1500)
    t0 = time.monotonic()
    for i in range(32):
        emu.feed_at(i * 4096, b"y" * 4096)
    elapsed = time.monotonic() - t0
    assert elapsed < 1.0  # 不等 sidecar（stall 1.5s）
    assert emu.feed_lag is True


# ---------------------------------------------------------------------------
# 满队列 sticky 与有界诊断
# ---------------------------------------------------------------------------


def test_feed_overflow_is_sticky_degraded_and_bounded(emu_factory):
    emu = emu_factory(max_queue_bytes=32 * 1024, max_queue_ops=64)
    assert emu._test_stall(800)
    block = b"z" * 4096
    for i in range(200):  # 800 KiB 远超 32 KiB 上限
        emu.feed_at(i * 4096, block)
    assert emu.feed_lag is True
    time.sleep(1.2)
    snap = emu.snapshot(timeout=20)
    assert snap.fidelity is Fidelity.PARTIAL
    assert snap.recovery is Recovery.DEGRADED
    assert snap.feed_lag is True
    # sticky：即使后续正常供料也不自动恢复 full
    _feed(emu, b"more\r\n", start=200 * 4096)
    snap2 = emu.snapshot(timeout=20)
    assert snap2.feed_lag is True
    assert snap2.recovery is Recovery.DEGRADED
    diag = emu.diagnostics()
    # 严格有界：拒绝区间/诊断不得随拒绝次数无界增长
    assert len(diag["rejected_ranges"]) <= 64
    assert diag["counters"]["rejected_feed_blocks"] > 64
    assert diag["overflow"].get("rejected_range", 0) > 0


def test_control_queue_overflow_is_visible(emu_factory):
    emu = emu_factory(max_queue_ops=8, max_queue_bytes=1024 * 1024)
    assert emu._test_stall(600)
    for _ in range(20):
        emu.resize(24, 80)
    diag = emu.diagnostics()
    assert diag["counters"]["control_queue_overflow"] >= 1


# ---------------------------------------------------------------------------
# MA 生产化缺口回归（先失败后通过）
# ---------------------------------------------------------------------------


def test_unknown_sequence_after_event_saturation_is_detected(emu_factory):
    """4096 事件上限不得掩盖后来的未知序列（初版漏检 -> full，修复后 partial）。"""
    emu = emu_factory()
    stream = b"\x1b[31m" * 5000 + b"\x1b[?7777h" + b"END"
    _feed(emu, stream, size=len(stream))
    snap = emu.snapshot(timeout=30)
    assert snap.fidelity is Fidelity.PARTIAL
    assert "7777" in snap.note


def test_engine_op_error_is_sticky_degraded(emu_factory):
    """feed 执行异常必须 sticky 降级且解析循环继续（初版仅记录 -> full）。

    r4 语义适配：相邻连续 feed 会合并为单帧，注入错误作用于**整批**（显式失败、
    不假成功）；因此用 barrier 让错误批先处理，再用**独立批**验证"循环继续"。
    """
    emu = emu_factory()
    assert emu._test_inject_feed_error("next-feed")
    emu.feed_at(0, b"AAAA")
    emu.barrier(timeout=20.0)  # 错误批先落账（其字节范围不被应用/不推进 applied）
    emu.feed_at(4, b"BBBB")    # 独立批：错误被隔离，引擎仍可服务
    snap = emu.snapshot(timeout=20)
    assert snap.fidelity is Fidelity.PARTIAL
    assert emu.engine_alive
    assert "BBBB" in snap.serialized_screen
    assert snap.cursor == 8
    snap2 = emu.snapshot(timeout=20)
    assert snap2.fidelity is Fidelity.PARTIAL  # sticky：不回到 full
    assert "ENGINE_OP_ERROR" in snap2.note


def test_reset_baseline_uses_execution_position_not_future_frontier(emu_factory):
    """reset 新基线 = 命令实际执行位置，不得误取含 future queued feed 的全局 frontier。"""
    emu = emu_factory(control_timeout=15.0)
    assert emu._test_stall(500)
    emu.feed_at(0, b"A" * 1000)
    emu.feed_at(1000, b"B" * 1000)
    results: list = []
    thread = threading.Thread(target=lambda: results.append(emu.reset_baseline()))
    thread.start()
    time.sleep(0.15)
    emu.feed_at(2000, b"C" * 1000)  # 已入队、排在 reset 之后
    thread.join(timeout=20)
    assert results and not thread.is_alive()
    snap = results[0]
    diag = emu.diagnostics()
    assert emu.producer_frontier == 3000
    # 修复后：2000（reset 执行时 A+B 已处理，C 尚未）；初版取 3000 -> 失败
    assert int(diag["baseline_cursor"]) == 2000
    assert snap.cursor == 2000
    # C 不丢：reset 之后正常应用
    after = emu.snapshot(timeout=20)
    assert emu.applied_cursor == 3000
    assert "C" in after.serialized_screen


def test_reset_clears_old_gap_and_never_fakes_full(emu_factory):
    """reset 后旧 gap 不得永久污染 cursor；但 reset 也不假恢复 full。"""
    emu = emu_factory(control_timeout=15.0)
    emu.feed_at(0, b"A" * 10)
    emu.feed_at(50, b"B" * 10)  # [10, 50) 缺口
    snap = emu.snapshot(timeout=15)
    assert "SOURCE_GAP_STICKY" in snap.note
    assert emu.cursors_valid is False
    emu.reset_baseline()
    snap2 = emu.snapshot(timeout=15)
    assert emu.cursors_valid is True  # 修复后清除；初版永久 False -> 失败
    assert "SOURCE_GAP_STICKY" not in snap2.note
    assert snap2.fidelity is Fidelity.PARTIAL
    assert "BASELINE_RESET_FRESH_VIEW" in snap2.note


def test_duplicate_feed_bytes_not_double_consumed(emu_factory):
    emu = emu_factory()
    emu.feed_at(0, b"one")
    emu.feed_at(1, b"ne")  # 覆盖已消费区间：拒绝再次投递
    snap = emu.snapshot(timeout=15)
    assert snap.serialized_screen.count("one") == 1
    diag = emu.diagnostics()
    assert diag["counters"]["duplicate_blocks"] == 1
    assert emu.cursors_valid is False  # 显式降级，不伪造对齐


# ---------------------------------------------------------------------------
# 崩溃 / 卡住 / 关闭重试 / 资源
# ---------------------------------------------------------------------------


def test_sidecar_crash_is_visible_and_close_converges(emu_factory):
    emu = emu_factory()
    pid = int(emu.sidecar_pid)
    identity = emu._sidecar_identity
    assert identity is not None
    result = identity_module.kill_verified(pid, identity, wait_timeout=5.0)
    assert result.killed
    deadline = time.monotonic() + 6.0
    while emu.engine_alive and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not emu.engine_alive
    emu.feed_at(0, b"after-crash")  # 入队快速返回，不抛错
    snap = emu.snapshot(timeout=5)
    assert snap.fidelity is Fidelity.UNAVAILABLE
    assert snap.recovery is Recovery.NONE
    report = emu.close(timeout=8)
    assert report.closed


def test_close_failure_retains_resources_then_retry_converges(emu_factory, monkeypatch):
    emu = emu_factory()
    assert emu._test_stall(3000)
    calls = {"force": 0}

    def failing_terminate(*args, **kwargs):
        calls["force"] += 1
        return {"errors": ["injected-force-failure"], "job_verified": False, "root_dead": False}

    monkeypatch.setattr(
        emulator_module, "_force_terminate_root", failing_terminate, raising=False
    )
    monkeypatch.setattr(guard_module.JobObjectGuard, "close", lambda self: False)
    rep1 = emu.close(timeout=0.8)
    assert calls["force"] >= 1
    assert rep1.closed is False
    assert rep1.process_exited is False
    assert rep1.guard_closed is False  # 资源保留、可重试
    assert emu.engine_alive  # 未被伪清理

    monkeypatch.undo()
    rep2 = emu.close(timeout=10.0)
    assert rep2.closed is True
    assert rep2.process_exited and rep2.guard_closed
    assert not emu.engine_alive


def test_guard_close_failure_is_retryable(emu_factory, monkeypatch):
    emu = emu_factory()
    guard = emu._guard
    original_close = guard.close
    monkeypatch.setattr(guard, "close", lambda: False)
    rep1 = emu.close(timeout=8)
    assert rep1.closed is False
    assert rep1.process_exited is True
    assert rep1.guard_closed is False
    monkeypatch.setattr(guard, "close", original_close)
    rep2 = emu.close(timeout=8)
    assert rep2.closed is True
    assert rep2.guard_closed is True


def test_runner_hard_death_kills_sidecar_via_job(tmp_path):
    """runner（持有 Job 句柄的宿主）硬死：内核关闭句柄 -> sidecar 不残留。"""
    info_path = tmp_path / "sidecar.json"
    script = (
        "import json, os, sys\n"
        f"sys.path.insert(0, {str(REPO_ROOT)!r})\n"
        "from packages.core.terminal.emulator import HeadlessEmulator\n"
        "emu = HeadlessEmulator(startup_timeout=30.0)\n"
        "open(r'" + str(info_path) + "', 'w', encoding='utf-8').write("
        "json.dumps({'pid': emu.sidecar_pid, "
        "'filetime': str(emu._sidecar_identity.created_at_filetime)}))\n"
        "os._exit(7)\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO_ROOT),
        timeout=120,
        capture_output=True,
    )
    assert proc.returncode == 7, proc.stderr.decode("utf-8", errors="replace")[-800:]
    info = json.loads(info_path.read_text(encoding="utf-8"))
    pid = int(info["pid"])
    filetime = int(info["filetime"])
    identity = ProcessIdentity(pid, created_at_filetime=filetime)
    try:
        deadline = time.monotonic() + 8.0
        evidence = None
        while time.monotonic() < deadline:
            probe = identity_module.probe_process(pid)
            if probe.status is ProcessStatus.DEAD:
                evidence = "dead"
                break
            if probe.status is ProcessStatus.UNKNOWN:
                time.sleep(0.2)
                probe2 = identity_module.probe_process(pid)
                if probe2.status is ProcessStatus.UNKNOWN:
                    evidence = "not-openable"
                    break
            elif probe.status is ProcessStatus.ALIVE and probe.identity is not None:
                if probe.identity.created_at_filetime != filetime:
                    evidence = "pid-reused"
                    break
            time.sleep(0.1)
        assert evidence, f"runner 硬死后 sidecar {pid} 仍存活（Job guard 未生效）"
    finally:
        # 无论断言如何都做身份核验清理，不留残留
        identity_module.kill_verified(pid, identity, wait_timeout=3.0)


def test_no_process_leaks_after_close():
    records: list[tuple[int, ProcessIdentity]] = []
    for _ in range(3):
        emu = HeadlessEmulator(startup_timeout=30.0)
        records.append((int(emu.sidecar_pid), emu._sidecar_identity))
        report = emu.close(timeout=10.0)
        assert report.closed
    for pid, identity in records:
        probe = identity_module.probe_process(pid)
        if probe.status is ProcessStatus.ALIVE and probe.identity is not None:
            assert probe.identity.created_at_filetime != identity.created_at_filetime, (
                f"sidecar {pid} 在 close 后仍存活"
            )


# ---------------------------------------------------------------------------
# r2 返工回归：有界关闭 / startup owner / 记账 / 成员门禁 / 迟到 reset ack
#（独立审查 1daff2a6 的确定失败与缺口；先失败后通过）
# ---------------------------------------------------------------------------

#: 测试专用协议替身（由测试生成到 tmp_path；node 运行，行为由环境变量控制）。
#: 只实现生产帧协议的被测形状；不参与任何生产路径。
FAKE_SIDECAR_SOURCE = r"""
const fs = require('node:fs');
const { spawn } = require('node:child_process');

const mode = process.env.FAKE_SIDECAR_MODE || 'normal';
const childPidFile = process.env.FAKE_CHILD_PID_FILE || '';
let buffer = Buffer.alloc(0);
let applied = 0n;

function frame(header, payload) {
  const body = Buffer.isBuffer(payload) ? payload : Buffer.alloc(0);
  const head = Buffer.from(JSON.stringify(header), 'utf8');
  const total = head.length + body.length;
  const buf = Buffer.alloc(8 + total);
  buf.writeUInt32LE(total, 0);
  buf.writeUInt32LE(head.length, 4);
  head.copy(buf, 8);
  body.copy(buf, 8 + head.length);
  return buf;
}
function send(header, payload) {
  process.stdout.write(frame(header, payload));
}
function spawnHolder() {
  // holder 继承 root 的 stdout/stderr（只持有，不写）；detached + unref
  // 使其在 root 退出后仍存活，用于制造 "root 退出但管道写端仍被存活
  // Job 成员后代持有" 的现场（不 detached 时 holder 会随父退出而消失）。
  const child = spawn(process.execPath, ['-e', 'setInterval(() => {}, 60000)'], {
    stdio: ['ignore', 'inherit', 'inherit'],
    detached: true,
  });
  child.unref();
  if (childPidFile) fs.writeFileSync(childPidFile, String(child.pid));
  return child;
}
function ack(op, extra) {
  send(Object.assign({
    v: 1, type: 'applied', op,
    applied_bytes: applied.toString(),
    processed_frontier: applied.toString(),
  }, extra || {}));
}
function handle(h, payload) {
  switch (h.type) {
    case 'hello':
      if (mode === 'no-ready-child') { spawnHolder(); return; }
      send({ v: 1, type: 'ready', pid: process.pid, engine: 'fake-sidecar/1.0.0' });
      if (mode === 'ready-exit-child') {
        spawnHolder();
        setTimeout(() => process.exit(0), 250);
      }
      return;
    case 'feed':
      applied += BigInt(payload.length);
      ack(h.op, { abs_start: h.abs_start, abs_end: h.abs_end });
      return;
    case 'resize':
      ack(h.op, { rows: Number(h.rows), cols: Number(h.cols) });
      return;
    case 'reset':
      if (mode === 'delayed-reset') {
        setTimeout(() => ack(h.op, { baseline_frontier: applied.toString(), resets: 1 }), 800);
        return;
      }
      ack(h.op, { baseline_frontier: applied.toString(), resets: 1 });
      return;
    case 'restore':
      ack(h.op);
      return;
    case 'snapshot':
      send({
        v: 1, type: 'snapshot', op: h.op,
        applied_bytes: applied.toString(), processed_frontier: applied.toString(),
        parsed_cursor: applied.toString(), cursors_valid: true,
        rows: 24, cols: 80, fidelity: 'full', recovery: 'full', reasons: [],
        engine: 'fake-sidecar/1.0.0',
      });
      return;
    case 'barrier':
      send({
        v: 1, type: 'barrier', op: h.op,
        applied_bytes: applied.toString(), processed_frontier: applied.toString(),
      });
      return;
    case 'shutdown':
      // ignore-shutdown（r3）：不回 ack、不退出 —— 迫使 close 走强杀路径
      // （kill_timeout 传递门控的确定性现场）。
      if (mode === 'ignore-shutdown') return;
      ack(h.op);
      setTimeout(() => process.exit(0), 20);
      return;
    default:
      send({ v: 1, type: 'error', op: h.op, code: 'unknown-op', detail: String(h.type) });
  }
}
process.stdin.on('data', (chunk) => {
  buffer = Buffer.concat([buffer, chunk]);
  while (buffer.length >= 8) {
    const total = buffer.readUInt32LE(0);
    const hlen = buffer.readUInt32LE(4);
    if (hlen > total || buffer.length < 8 + total) break;
    const header = JSON.parse(buffer.subarray(8, 8 + hlen).toString('utf8'));
    const payload = Buffer.from(buffer.subarray(8 + hlen, 8 + total));
    buffer = buffer.subarray(8 + total);
    handle(header, payload);
  }
});
"""


@pytest.fixture
def fake_sidecar(tmp_path):
    path = tmp_path / "fake_sidecar.cjs"
    path.write_text(FAKE_SIDECAR_SOURCE.strip() + "\n", encoding="utf-8")
    return path


def _wait_until(predicate, timeout: float, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + float(timeout)
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


def _same_handle_dead(proc) -> bool:
    handle = int(getattr(proc, "_handle", 0) or 0)
    if not handle:
        return False
    return identity_module.wait_state(handle) is ProcessStatus.DEAD


def _probe_retained(pid: int):
    handle = identity_module.open_process_for_probe(int(pid))
    if not handle:
        raise RuntimeError(f"OpenProcess(probe) failed for pid={pid}")
    probe = identity_module.probe_handle(int(handle), pid=int(pid))
    return int(handle), probe


def test_close_bounded_when_root_dead_descendant_holds_stdout(
    emu_factory, fake_sidecar, monkeypatch, tmp_path
):
    """P11：root 退出但（Job 成员）孙进程持有 stdout → close 必须自身有界并核验收敛。

    审查现场证明该后代是 Job 成员（guard.member_pids() 含其 pid）；清理以真实
    guard 所有权为准（整 Job 清理与核验），不扫描陌生 PID。
    """
    pid_file = tmp_path / "holder.pid"
    monkeypatch.setenv("FAKE_SIDECAR_MODE", "ready-exit-child")
    monkeypatch.setenv("FAKE_CHILD_PID_FILE", str(pid_file))
    emu = emu_factory(sidecar_path=str(fake_sidecar), startup_timeout=20.0)
    proc = emu._proc
    assert _wait_until(lambda: _same_handle_dead(proc), 8.0), "fake root 未退出"
    assert _wait_until(pid_file.exists, 8.0), "孙进程 pid 未落盘"
    holder_pid = int(pid_file.read_text(encoding="utf-8").strip())
    handle, probe = _probe_retained(holder_pid)
    holder_ft = int(probe.identity.created_at_filetime) if probe.identity else None
    try:
        assert probe.status is ProcessStatus.ALIVE
        assert holder_pid in emu._guard.member_pids()  # 真实 Job 成员证据
        reports: list = []
        errors: list = []

        def do_close():
            try:
                reports.append(emu.close(timeout=3.0))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        thread = threading.Thread(target=do_close, daemon=True)
        t0 = time.monotonic()
        thread.start()
        thread.join(timeout=12.0)
        blocked = thread.is_alive()
        if blocked and holder_ft is not None:
            # 旧实现现场：仅以身份核验兜底解除无界等待（不作为通过依据）
            identity_module.kill_verified(holder_pid, holder_ft, wait_timeout=5.0)
            thread.join(timeout=15.0)
        elapsed = time.monotonic() - t0
        assert not blocked, f"close 未被自身 timeout 约束：{elapsed:.2f}s 仍未返回"
        assert elapsed <= 3.0 + 4.0, f"close 远超预算：{elapsed:.2f}s"
        assert not errors, errors
        report = reports[0]
        assert report.closed and report.process_exited and report.guard_closed
        assert report.job_verified is True
        assert _wait_until(
            lambda: identity_module.wait_state(handle) is ProcessStatus.DEAD, 5.0
        ), "孙进程未被整 Job 清理终止"
    finally:
        if holder_ft is not None:
            identity_module.kill_verified(holder_pid, holder_ft, wait_timeout=3.0)
        identity_module.close_handle_checked(handle)


def test_startup_failure_bounded_and_owner_retry(fake_sidecar, monkeypatch, tmp_path):
    """P1b/P10：构造失败清理必须自身有界；异常须携带可重试 owner 与残留信息。"""
    pid_file = tmp_path / "holder.pid"
    monkeypatch.setenv("FAKE_SIDECAR_MODE", "no-ready-child")
    monkeypatch.setenv("FAKE_CHILD_PID_FILE", str(pid_file))
    captured: list = []
    real_popen = emulator_module.subprocess.Popen

    def spy_popen(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        captured.append(proc)
        return proc

    monkeypatch.setattr(emulator_module.subprocess, "Popen", spy_popen)
    outcome: dict = {}

    def ctor():
        try:
            HeadlessEmulator(sidecar_path=str(fake_sidecar), startup_timeout=2.0)
        except Exception as exc:  # noqa: BLE001
            outcome["exc"] = exc

    thread = threading.Thread(target=ctor, daemon=True)
    t0 = time.monotonic()
    thread.start()
    thread.join(timeout=15.0)
    blocked = thread.is_alive()
    if blocked:
        # 旧实现现场兜底：核验终止自有 root 与孙进程，解除清理阻塞
        for proc in captured:
            handle = int(getattr(proc, "_handle", 0) or 0)
            ft = identity_module.read_creation_filetime(handle) if handle else None
            if ft is not None and proc.poll() is None:
                identity_module.kill_verified(int(proc.pid), int(ft), wait_timeout=3.0)
        if pid_file.exists():
            try:
                holder_pid = int(pid_file.read_text(encoding="utf-8").strip())
                h, probe = _probe_retained(holder_pid)
                ft = probe.identity.created_at_filetime if probe.identity else None
                if ft is not None:
                    identity_module.kill_verified(holder_pid, int(ft), wait_timeout=3.0)
                identity_module.close_handle_checked(h)
            except Exception:  # noqa: BLE001
                pass
        thread.join(timeout=15.0)
    elapsed = time.monotonic() - t0
    exc = outcome.get("exc")
    try:
        assert not blocked, f"构造失败清理未被约束：{elapsed:.2f}s 仍未返回"
        assert elapsed <= 2.0 + 10.0, f"构造失败远超总预算：{elapsed:.2f}s"
        assert isinstance(exc, EmulatorUnavailableError)
        owner = getattr(exc, "owner", None)
        assert owner is not None, "异常未携带可重试 owner（仅有文本）"
        assert hasattr(owner, "retry_cleanup")
        residual = getattr(exc, "residual", None)
        assert isinstance(residual, dict) and residual.get("pid")
        outcome2 = owner.retry_cleanup(timeout=10.0)
        assert outcome2.get("closed") is True, outcome2
        for proc in captured:
            assert _same_handle_dead(proc) or proc.poll() is not None
    finally:
        if pid_file.exists():
            try:
                holder_pid = int(pid_file.read_text(encoding="utf-8").strip())
                h, probe = _probe_retained(holder_pid)
                ft = probe.identity.created_at_filetime if probe.identity else None
                if ft is not None:
                    identity_module.kill_verified(holder_pid, int(ft), wait_timeout=3.0)
                identity_module.close_handle_checked(h)
            except Exception:  # noqa: BLE001
                pass


def test_stderr_join_failure_is_accounted_and_retryable(emu_factory):
    """P8：stderr 未 join 必须阻止 closed=True；恢复后可重试收敛。"""
    emu = emu_factory()
    stubborn = threading.Thread(target=lambda: time.sleep(60), daemon=True, name="stub-stderr")
    stubborn.start()
    original = emu._stderr_thread
    emu._stderr_thread = stubborn
    try:
        rep1 = emu.close(timeout=8.0)
        assert rep1.closed is False
        assert rep1.stderr_joined is False
        assert rep1.process_exited is True
    finally:
        emu._stderr_thread = original
    rep2 = emu.close(timeout=10.0)
    assert rep2.closed is True
    assert rep2.stderr_joined is True


def test_stream_close_failure_is_accounted_and_retryable(emu_factory):
    """P8：流关闭失败必须阻止 closed=True，保留引用；恢复后重试收敛。"""
    emu = emu_factory()
    real_stdin = emu._proc.stdin

    class _BadStream:
        def close(self):
            raise OSError("injected stream close failure")

    emu._proc.stdin = _BadStream()
    try:
        rep1 = emu.close(timeout=8.0)
        assert rep1.closed is False
        assert rep1.streams_closed is False
        assert "stdin-close-error" in rep1.detail
    finally:
        emu._proc.stdin = real_stdin
    rep2 = emu.close(timeout=10.0)
    assert rep2.closed is True
    assert rep2.streams_closed is True


@pytest.mark.parametrize(
    "injection",
    ["is_member_false", "is_member_raises", "active_none", "active_raises", "active_zero"],
)
def test_assign_phase_membership_gate_fail_closed(monkeypatch, injection):
    """P3：assign 后 resume 前必须核验 Job 成员/active；false/unknown/查询失败均 fail-closed。"""
    captured: list = []
    real_popen = emulator_module.subprocess.Popen

    def spy_popen(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        captured.append(proc)
        return proc

    monkeypatch.setattr(emulator_module.subprocess, "Popen", spy_popen)
    if injection == "is_member_false":
        monkeypatch.setattr(guard_module.JobObjectGuard, "is_member", lambda self, h: False)
    elif injection == "is_member_raises":

        def _raising_member(self, h):
            raise OSError("injected is_member query failure")

        monkeypatch.setattr(guard_module.JobObjectGuard, "is_member", _raising_member)
    elif injection == "active_none":
        monkeypatch.setattr(
            guard_module.JobObjectGuard, "active_processes", lambda self: None
        )
    elif injection == "active_raises":

        def _raising_active(self):
            raise OSError("injected active query failure")

        monkeypatch.setattr(
            guard_module.JobObjectGuard, "active_processes", _raising_active
        )
    else:
        monkeypatch.setattr(guard_module.JobObjectGuard, "active_processes", lambda self: 0)
    emu = None
    exc = None
    try:
        try:
            emu = HeadlessEmulator(startup_timeout=20.0)
        except EmulatorUnavailableError as err:
            exc = err
    finally:
        if emu is not None:  # 旧实现现场：构造未被门禁拒绝，收尾防泄漏
            try:
                emu.close(timeout=6.0)
            except Exception:  # noqa: BLE001
                pass
        for proc in captured:
            handle = int(getattr(proc, "_handle", 0) or 0)
            ft = identity_module.read_creation_filetime(handle) if handle else None
            if ft is not None and proc.poll() is None:
                identity_module.kill_verified(int(proc.pid), int(ft), wait_timeout=5.0)
    assert exc is not None, f"成员核验缺失：注入 {injection} 下构造仍然成功"
    assert captured, "spy 未捕获 sidecar 进程"
    assert _wait_until(
        lambda: _same_handle_dead(captured[-1]) or captured[-1].poll() is not None, 8.0
    ), "构造失败后 sidecar 未被清理"


def test_late_reset_ack_updates_ledger_once_and_gates_cursor(
    emu_factory, fake_sidecar, monkeypatch
):
    """P9：迟到 reset ack 只回填一次实际执行账本；确认前结构性禁止旧 cursor 续流。"""
    monkeypatch.setenv("FAKE_SIDECAR_MODE", "delayed-reset")
    emu = emu_factory(
        sidecar_path=str(fake_sidecar), startup_timeout=20.0, control_timeout=0.4
    )
    emu.feed_at(0, b"abcd")
    t0 = time.monotonic()
    snap = emu.reset_baseline()
    elapsed = time.monotonic() - t0
    assert elapsed < 1.5
    assert "unconfirmed" in snap.note
    assert emu.cursors_valid is False, "reset 未确认期间不得允许旧 cursor 续流"
    diag0 = emu.diagnostics()
    assert diag0["cursors_valid"] is False
    assert diag0["reset_unconfirmed"] is True
    time.sleep(1.4)  # fake 在 800ms 后回 ack（引擎已执行 reset）
    diag = emu.diagnostics()
    assert diag["reset_count"] == 1
    assert int(diag["baseline_cursor"]) == 4
    assert diag["reset_unconfirmed"] is False
    assert diag["cursors_valid"] is True
    snap2 = emu.snapshot(timeout=10)
    assert snap2.cursor >= 4
    assert emu.diagnostics()["reset_count"] == 1  # 只应用一次


def test_resize_wait_confirmation_surface(emu_factory):
    """resize 确认面：非阻塞 resize 无回执（不得推断三方一致）；resize_wait 可确认。"""
    emu = emu_factory()
    assert emu.resize_wait(30, 100, timeout=8.0) is True
    assert emu.diagnostics()["applied_resize"] == [30, 100]

    emu2 = emu_factory(max_queue_ops=8, max_queue_bytes=1 << 20)
    assert emu2._test_stall(2500)
    for _ in range(20):
        emu2.resize(24, 80)  # 无回执；多数被拒
    assert emu2.resize_wait(31, 101, timeout=0.5) is False
    assert emu2.diagnostics()["counters"]["resize_rejected"] >= 1
    time.sleep(2.6)
    assert emu2.resize_wait(31, 101, timeout=8.0) is True
    assert emu2.diagnostics()["applied_resize"] == [31, 101]


# ---------------------------------------------------------------------------
# r3 并发窄修回归（F1/F2/F3；独立审查 ROUND2 N8b/N9a 确定失败 + F3 死参数）
#（先失败后通过：9cb 固定副本上本段测试失败，修复后通过）
# ---------------------------------------------------------------------------


def test_close_timeout_bounds_total_call_including_lock_wait(emu_factory):
    """F1：close(timeout) 是**本次调用**总预算（从入口计时，含 _close_lock 排队）。

    - T1 持锁时 T2 短 budget 必须在其预算内有界返回合法 closed=False + 锁忙诊断；
    - T2 不写另一调用的状态/缓存（保 owner）；
    - T1 收敛后重试成功且 seconds 为本次调用实际耗时（不用 T1 耗时冒充）；
    - timeout=0 边界明确不倒退（合法报告，重试收敛）。
    """
    emu = emu_factory()
    assert emu._test_stall(2500)
    r1: list = []

    def t1_worker():
        r1.append(emu.close(timeout=6.0))

    t1 = threading.Thread(target=t1_worker, daemon=True)
    t1.start()
    time.sleep(0.25)  # T1 已持 _close_lock（等待 stall 结束）
    t0 = time.monotonic()
    r2 = emu.close(timeout=0.5)
    t2_elapsed = time.monotonic() - t0
    assert t2_elapsed <= 0.5 + 0.5, f"T2 未被本次 timeout 约束：{t2_elapsed:.3f}s"
    assert r2.closed is False
    assert r2.seconds <= t2_elapsed + 0.05, (r2.seconds, t2_elapsed)
    assert "close-lock-busy" in r2.detail, r2.detail
    assert emu._close_report is None, "T2 不得写入另一调用（未完成）的状态/缓存"
    assert emu.engine_alive, "T2 抢锁失败不得触碰资源（保 owner）"
    t1.join(timeout=20)
    assert not t1.is_alive() and r1 and r1[0].closed is True
    # 释放后重试：缓存可返回，但 seconds 必须是本次调用耗时（不是 T1 的 2.x s）
    t3 = time.monotonic()
    r3 = emu.close(timeout=5.0)
    t3_elapsed = time.monotonic() - t3
    assert r3.closed is True
    assert r3.seconds <= max(t3_elapsed, 0.02) + 0.05, (r3.seconds, t3_elapsed)
    assert r3.seconds < 1.0
    # timeout=0 边界：合法报告 + 不破坏状态，重试收敛
    emu2 = emu_factory()
    r0 = emu2.close(timeout=0.0)
    assert isinstance(r0, type(r2))
    assert r0.seconds < 0.75
    assert emu2.close(timeout=10.0).closed is True


def test_owner_retry_serialized_bounded_and_convergent(
    fake_sidecar, monkeypatch, tmp_path
):
    """F2：StartupCleanupOwner.retry_cleanup 同 owner 内部串行化（总 deadline 含锁等待）。

    - 并发调用不得重叠终止（peak 并发 == 1）；
    - 每个调用在有界时间内返回，且都被证明 closed=True（后到者等锁后幂等重放）；
    - timeout=0 超时保引用可重试；完成后幂等。
    """
    pid_file = tmp_path / "holder.pid"
    monkeypatch.setenv("FAKE_SIDECAR_MODE", "no-ready-child")
    monkeypatch.setenv("FAKE_CHILD_PID_FILE", str(pid_file))
    real_term = emulator_module._force_terminate_root
    real_close = guard_module.JobObjectGuard.close
    term_calls = {"n": 0}
    close_calls = {"n": 0}

    def failing_term(*args, **kwargs):
        term_calls["n"] += 1
        if term_calls["n"] <= 1:  # 仅构造期间的首次清理失败 -> 保留活资源（retained owner）
            return {
                "root_dead": False,
                "root_killed": False,
                "job_terminate_called": False,
                "job_verified": False,
                "remaining": None,
                "errors": ["injected-construct"],
            }
        return real_term(*args, **kwargs)

    def failing_close(self):
        close_calls["n"] += 1
        if close_calls["n"] <= 1:  # 构造期间 guard 保留（不触发 KILL_ON_JOB_CLOSE 兜底）
            return False
        return real_close(self)

    monkeypatch.setattr(emulator_module, "_force_terminate_root", failing_term)
    monkeypatch.setattr(guard_module.JobObjectGuard, "close", failing_close)
    outcome: dict = {}
    try:
        HeadlessEmulator(sidecar_path=str(fake_sidecar), startup_timeout=2.0)
    except Exception as exc:  # noqa: BLE001
        outcome["exc"] = exc
    exc = outcome.get("exc")
    assert exc is not None and getattr(exc, "owner", None) is not None
    assert exc.residual.get("process_exited") is False  # retained（注入）
    owner = exc.owner

    active = {"now": 0, "peak": 0}
    active_lock = threading.Lock()

    def spy_term(*args, **kwargs):
        with active_lock:
            active["now"] += 1
            active["peak"] = max(active["peak"], active["now"])
        try:
            time.sleep(0.05)  # 放大重叠窗口（修复前两线程会同时进入）
            return real_term(*args, **kwargs)
        finally:
            with active_lock:
                active["now"] -= 1

    monkeypatch.setattr(emulator_module, "_force_terminate_root", spy_term)
    monkeypatch.setattr(guard_module.JobObjectGuard, "close", real_close)
    active["peak"] = 0
    results: list = []
    errors: list = []

    def worker():
        try:
            results.append(owner.retry_cleanup(timeout=6.0))
        except Exception as err:  # noqa: BLE001
            errors.append(err)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(2)]
    t0 = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    elapsed = time.monotonic() - t0
    try:
        assert not errors, errors
        assert len(results) == 2
        assert elapsed <= 6.0 + 2.0, f"并发 owner 重试未被总预算约束：{elapsed:.3f}s"
        assert active["peak"] == 1, f"终止原语重叠执行：peak={active['peak']}"
        assert all(r.get("closed") is True for r in results), results
        # timeout=0：超时保引用、可重试
        r0 = owner.retry_cleanup(timeout=0.0)
        assert r0.get("closed") is False and r0.get("retryable") is True, r0
        # 完成后幂等
        again = owner.retry_cleanup(timeout=5.0)
        assert again.get("closed") is True
    finally:
        if pid_file.exists():
            try:
                holder_pid = int(pid_file.read_text(encoding="utf-8").strip())
                h, probe = _probe_retained(holder_pid)
                ft = probe.identity.created_at_filetime if probe.identity else None
                if ft is not None:
                    identity_module.kill_verified(holder_pid, int(ft), wait_timeout=3.0)
                identity_module.close_handle_checked(h)
            except Exception:  # noqa: BLE001
                pass


def test_kill_timeout_wired_to_remaining_budget(
    emu_factory, fake_sidecar, monkeypatch, tmp_path
):
    """F3：kill_timeout 接入每次 kill 等待上限 = min(剩余总预算, kill_timeout)；
    close / 构造失败清理 / owner 重试三条路径参数一致（不误称硬 OS SLA）。"""
    captured: list = []
    real_kv = identity_module.kill_verified

    def spy_kv(pid, expected, *, exit_code=0xDEAD, wait_timeout=5.0):
        captured.append(float(wait_timeout))
        return real_kv(pid, expected, exit_code=exit_code, wait_timeout=wait_timeout)

    monkeypatch.setattr(identity_module, "kill_verified", spy_kv)
    kill_timeout = 1.0

    # (a) close 预算不足：等待上限跟随剩余总预算（< kill_timeout）
    emu = emu_factory(kill_timeout=kill_timeout)
    assert emu._test_stall(2500)
    captured.clear()
    emu.close(timeout=1.2)
    assert captured, "预算不足场景未触发身份核验终止"
    wait_a = captured[-1]
    assert 0.0 < wait_a < kill_timeout, f"预算不足时等待上限应跟随剩余：{wait_a}"

    # (b) close 预算充足（ignore-shutdown 迫使强杀）：等待上限 == kill_timeout
    monkeypatch.setenv("FAKE_SIDECAR_MODE", "ignore-shutdown")
    emu2 = emu_factory(
        sidecar_path=str(fake_sidecar), startup_timeout=20.0, kill_timeout=kill_timeout
    )
    captured.clear()
    rep_b = emu2.close(timeout=6.0)
    monkeypatch.delenv("FAKE_SIDECAR_MODE", raising=False)
    assert rep_b.closed is True, rep_b.as_dict()
    assert captured and abs(captured[-1] - kill_timeout) <= 0.05, captured

    # (c) 构造失败清理路径：同一 kill_timeout 参与（清理总预算 5s > kill_timeout）
    pid_file = tmp_path / "c.pid"
    monkeypatch.setenv("FAKE_SIDECAR_MODE", "no-ready-child")
    monkeypatch.setenv("FAKE_CHILD_PID_FILE", str(pid_file))
    captured.clear()
    outcome: dict = {}
    try:
        HeadlessEmulator(
            sidecar_path=str(fake_sidecar), startup_timeout=2.0, kill_timeout=kill_timeout
        )
    except Exception as exc:  # noqa: BLE001
        outcome["exc"] = exc
    monkeypatch.delenv("FAKE_SIDECAR_MODE", raising=False)
    monkeypatch.delenv("FAKE_CHILD_PID_FILE", raising=False)
    assert outcome.get("exc") is not None
    assert captured and abs(captured[-1] - kill_timeout) <= 0.05, captured

    # (d) owner.retry_cleanup 路径：retained 现场下同一 kill_timeout 参与
    real_term = emulator_module._force_terminate_root
    real_close = guard_module.JobObjectGuard.close
    term_calls = {"n": 0}

    def failing_term(*args, **kwargs):
        term_calls["n"] += 1
        if term_calls["n"] <= 1:
            return {
                "root_dead": False,
                "root_killed": False,
                "job_terminate_called": False,
                "job_verified": False,
                "remaining": None,
                "errors": ["injected-construct"],
            }
        return real_term(*args, **kwargs)

    close_calls = {"n": 0}

    def failing_close(self):
        close_calls["n"] += 1
        if close_calls["n"] <= 1:
            return False
        return real_close(self)

    pid_file2 = tmp_path / "d.pid"
    monkeypatch.setenv("FAKE_SIDECAR_MODE", "no-ready-child")
    monkeypatch.setenv("FAKE_CHILD_PID_FILE", str(pid_file2))
    monkeypatch.setattr(emulator_module, "_force_terminate_root", failing_term)
    monkeypatch.setattr(guard_module.JobObjectGuard, "close", failing_close)
    outcome2: dict = {}
    try:
        HeadlessEmulator(
            sidecar_path=str(fake_sidecar), startup_timeout=2.0, kill_timeout=kill_timeout
        )
    except Exception as exc:  # noqa: BLE001
        outcome2["exc"] = exc
    owner = getattr(outcome2.get("exc"), "owner", None)
    assert owner is not None and owner.kill_timeout == kill_timeout, "owner 未携带 kill_timeout"
    monkeypatch.setattr(emulator_module, "_force_terminate_root", real_term)
    monkeypatch.setattr(guard_module.JobObjectGuard, "close", real_close)
    monkeypatch.delenv("FAKE_SIDECAR_MODE", raising=False)
    monkeypatch.delenv("FAKE_CHILD_PID_FILE", raising=False)
    captured.clear()
    out_d = owner.retry_cleanup(timeout=6.0)
    assert out_d.get("closed") is True, out_d
    assert captured and abs(captured[-1] - kill_timeout) <= 0.05, captured
    for pid_file_path in (pid_file, pid_file2):
        if pid_file_path.exists():
            try:
                holder_pid = int(pid_file_path.read_text(encoding="utf-8").strip())
                h, probe = _probe_retained(holder_pid)
                ft = probe.identity.created_at_filetime if probe.identity else None
                if ft is not None:
                    identity_module.kill_verified(holder_pid, int(ft), wait_timeout=3.0)
                identity_module.close_handle_checked(h)
            except Exception:  # noqa: BLE001
                pass


# ---------------------------------------------------------------------------
# r4：组合发现 F1（ready 前 EOF 必须走统一启动失败）与 F2（相邻连续 feed 有界合并）
#（先失败后通过：cd876291 固定副本上本段失败，修复后通过）
# ---------------------------------------------------------------------------


def test_handshake_eof_before_ready_fails_with_owner(tmp_path):
    """F1：spawn 后、ready 前 EOF/读异常必须与 fatal/timeout 同走 `_fail_startup`：
    抛 `EmulatorStartupError` 并携带 owner/residual（可重试收敛、无残留）；
    spawn 前失败仍 owner=None（不误报）。"""
    # ① spawn 前（脚本不存在）→ owner=None（不误报无资源场景）
    with pytest.raises(EmulatorUnavailableError) as pre:
        HeadlessEmulator(sidecar_path=str(tmp_path / "missing_sidecar.mjs"), startup_timeout=5.0)
    assert pre.value.owner is None

    # ② spawn 后 ready 前 EOF（stub 立即退出）→ 必须抛 + owner + retry 收敛
    eof_stub = tmp_path / "exit_only.cjs"
    eof_stub.write_text("process.exit(3);\n", encoding="utf-8")
    captured: list = []
    real_popen = emulator_module.subprocess.Popen

    def spy_popen(*args, **kwargs):
        proc = real_popen(*args, **kwargs)
        captured.append(proc)
        return proc

    prev_popen = emulator_module.subprocess.Popen
    emulator_module.subprocess.Popen = spy_popen
    try:
        with pytest.raises(EmulatorStartupError) as eof_err:
            HeadlessEmulator(sidecar_path=str(eof_stub), startup_timeout=10.0)
    finally:
        emulator_module.subprocess.Popen = prev_popen
    err = eof_err.value
    assert err.owner is not None, "ready 前 EOF 必须携带可重试 owner"
    assert isinstance(err.residual, dict) and err.residual.get("pid")
    assert err.residual.get("process_exited") is True
    out_eof = err.owner.retry_cleanup(timeout=8.0)
    assert out_eof.get("closed") is True, out_eof
    assert captured and (_same_handle_dead(captured[-1]) or captured[-1].poll() is not None)

    # ③ spawn 后 fatal 帧 → 同一 `_fail_startup` 出口（owner/residual/重试收敛）
    fatal_stub = tmp_path / "fatal.cjs"
    fatal_stub.write_text(
        "function send(h){const b=Buffer.from(JSON.stringify(h),'utf8');"
        "const o=Buffer.alloc(8+b.length);o.writeUInt32LE(b.length,0);o.writeUInt32LE(b.length,4);"
        "b.copy(o,8);process.stdout.write(o);}\n"
        "send({v:1,type:'fatal',code:'injected-fatal',detail:'test'});\n"
        "setTimeout(()=>process.exit(4),80);\n",
        encoding="utf-8",
    )
    with pytest.raises(EmulatorStartupError) as fatal_err:
        HeadlessEmulator(sidecar_path=str(fatal_stub), startup_timeout=10.0)
    assert fatal_err.value.owner is not None
    assert fatal_err.value.owner.retry_cleanup(timeout=8.0).get("closed") is True


def test_feed_batching_merges_adjacent_feeds(emu_factory):
    """F2：相邻连续 feed 在有界上限内合并为单帧；批上限生效；内容与逐块一致。"""
    emu = emu_factory(feed_batch_limit=4096)
    ref = emu_factory(feed_batch_limit=1)  # 上限 1 == 禁用合并（等价旧行为）
    block = b"BATCH-PAD-" + b"m" * 500  # 510B/块
    total = 0
    for _ in range(24):
        emu.feed_at(total, block)
        ref.feed_at(total, block)
        total += len(block)
    emu.barrier(timeout=20.0)
    ref.barrier(timeout=20.0)
    diag = emu.diagnostics()
    counters = diag["counters"]
    assert counters["feed_batches"] >= 3
    assert counters["feed_batched_ops"] >= 12
    assert counters["max_feed_batch_bytes"] <= 4096
    assert int(diag["applied_cursor"]) == total
    assert emu.snapshot(timeout=20.0).serialized_screen == ref.snapshot(timeout=20.0).serialized_screen
    # 禁用合并的参照：每块一帧（batches == ops）
    ref_diag = ref.diagnostics()
    assert ref_diag["counters"]["feed_batches"] == ref_diag["counters"]["feed_ops"] == 24


def test_feed_batching_stops_at_gap_and_control(emu_factory):
    """F2：合并不跨 seq gap / duplicate，也不跨控制交错（resize/snapshot/barrier/
    reset/restore）；byte 绝对偏移与 gap 诊断语义不变。"""
    emu = emu_factory(feed_batch_limit=1 << 20)  # 大上限：若不设边界会全部合并
    emu.feed_at(0, b"A" * 100)      # A1
    emu.feed_at(100, b"B" * 100)    # A2（与 A1 连续 → 应合并）
    emu.feed_at(250, b"C" * 100)    # gap 50B → 不跨
    emu.resize(30, 100)             # 控制 op 打断
    emu.feed_at(350, b"D" * 100)    # resize 之后 → 不与 C 合并
    emu.barrier(timeout=20.0)
    diag = emu.diagnostics()
    counters = diag["counters"]
    assert counters["feed_batched_ops"] == 2, counters  # 只有 A1+A2 成批
    assert counters["feed_batches"] == 3, counters      # (A1+A2), C, D
    assert int(diag["applied_cursor"]) == 450
    assert diag["gap_ranges"], "gap 诊断必须保留"
    assert diag["applied_resize"] == [30, 100]


def test_feed_batching_prefix_split_utf8_csi_osc_equivalence(emu_factory):
    """F2：把跨块 UTF-8/CSI/OSC 序列拆进多个 feed 后合并，行为与不合并逐字节等价
    （协议 A 对拍 + clean 边界不变）。"""
    stream = (
        b"head-" + "中".encode("utf-8") + b"-mid\r\n"
        b"\x1b[31mRED\x1b[0m \x1b[1;32mGRN\x1b[0m\r\n"
        b"\x1b]0;title-split\x07tail"
    )
    batched = emu_factory(feed_batch_limit=1 << 20)
    plain = emu_factory(feed_batch_limit=1)
    for target in (batched, plain):
        seq = 0
        for offset in range(0, len(stream), 3):  # 3B 小块：序列大量跨块
            part = stream[offset : offset + 3]
            target.feed_at(seq, part)
            seq += len(part)
    snap_b = batched.snapshot(timeout=20.0)
    snap_p = plain.snapshot(timeout=20.0)
    assert snap_b.serialized_screen == snap_p.serialized_screen
    assert snap_b.cursor == len(stream) == snap_p.cursor
    assert snap_b.fidelity is Fidelity.PARTIAL  # OSC 标题 → 保守降级（不因合并变化）
    # 协议 A 恢复（从 cursor 重拉）与直喂等价
    restored = emu_factory(feed_batch_limit=1 << 20, start_cursor=snap_b.cursor)
    assert restored.restore_screen(snap_b.serialized_screen)
    assert restored.snapshot(timeout=20.0).serialized_screen == snap_b.serialized_screen


def test_feed_batching_error_ack_binds_batch(emu_factory):
    """F2：批次异常 ack 仍绑定批 op（sticky 降级、解析循环继续、后续批正常）。"""
    emu = emu_factory(feed_batch_limit=1 << 20)
    assert emu._test_inject_feed_error("next-feed")
    emu.feed_at(0, b"AAAA")
    emu.feed_at(4, b"BBBB")  # 与上块连续 → 同批（错误作用于整批）
    emu.barrier(timeout=20.0)  # 排空：确定错误批已处理（C 不会被并入错误批）
    emu.feed_at(8, b"CCCC")  # 错误批之后的新批 → 正常
    snap = emu.snapshot(timeout=20.0)
    assert snap.fidelity is Fidelity.PARTIAL
    assert "CCCC" in snap.serialized_screen
    assert snap.cursor == 12  # 错误批未应用（0），C 批应用至 12
    assert emu.diagnostics()["counters"].get("engine_op_errors", 0) >= 1
    assert emu.engine_alive


def test_feed_batching_accounting_in_flight_and_close(emu_factory, monkeypatch):
    """F2：在途合并 buffer 计账在原 FIFO 预算内（发送时未提前释放）；
    队列溢出/close 在途保持原语义（sticky/保 owner/收敛）。"""
    emu = emu_factory(feed_batch_limit=1 << 20)
    sent: list = []
    real_send = HeadlessEmulator._send_op

    def spy_send(self, op):
        with self._lock:
            snapshot = (len(op.data or b""), self._queue_ops, self._queue_bytes)
        sent.append({"kind": op.kind, "batch_bytes": snapshot[0],
                     "queue_ops": snapshot[1], "queue_bytes": snapshot[2]})
        return real_send(self, op)

    monkeypatch.setattr(HeadlessEmulator, "_send_op", spy_send)
    block = b"z" * 1024
    total = 0
    for _ in range(8):
        emu.feed_at(total, block)
        total += len(block)
    emu.barrier(timeout=20.0)
    feed_sends = [s for s in sent if s["kind"] == "feed"]
    assert feed_sends, "未观察到 feed 发送"
    for item in feed_sends:
        assert item["queue_bytes"] >= item["batch_bytes"], (
            "在途合并 buffer 必须仍计入 queue_bytes（不提前释放）",
            item,
        )
    diag = emu.diagnostics()
    assert int(diag["applied_cursor"]) == total
    # 队列溢出（stall 中）：feed_lag sticky 原语义
    emu2 = emu_factory(feed_batch_limit=4096, max_queue_bytes=8192, max_queue_ops=64)
    assert emu2._test_stall(1500)
    for i in range(40):
        emu2.feed_at(i * 1024, block)
    assert emu2.feed_lag is True
    time.sleep(1.8)
    snap2 = emu2.snapshot(timeout=20.0)
    assert snap2.fidelity is Fidelity.PARTIAL
    assert snap2.recovery is Recovery.DEGRADED
    # close 在途（合并批已发出/排队中）必须收敛且保 owner 语义
    emu3 = emu_factory(feed_batch_limit=1 << 20)
    assert emu3._test_stall(1200)
    seq = 0
    for _ in range(16):
        emu3.feed_at(seq, block)
        seq += len(block)
    report = emu3.close(timeout=10.0)
    assert report.closed is True, report.as_dict()
