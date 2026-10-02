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

边界：本文件全部是 headless↔headless 对比；真实浏览器渲染保真属 P3，不在
本文件声称。仅使用自建 sidecar 进程与 pytest 临时目录；等待均有界
（pytest.ini timeout=300 为兜底看门狗）。
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
from packages.core.terminal import identity as identity_module
from packages.core.terminal.contracts import (
    Fidelity,
    ProcessIdentity,
    ProcessStatus,
    Recovery,
)
from packages.core.terminal.emulator import (
    EmulatorProtocolError,
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
    """feed 执行异常必须 sticky 降级且解析循环继续（初版仅记录 -> full）。"""
    emu = emu_factory()
    assert emu._test_inject_feed_error("next-feed")
    emu.feed_at(0, b"AAAA")
    emu.feed_at(4, b"BBBB")
    snap = emu.snapshot(timeout=20)
    assert snap.fidelity is Fidelity.PARTIAL
    assert emu.engine_alive  # 错误被隔离，引擎仍可服务
    assert "BBBB" in snap.serialized_screen
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

    def failing_force():
        calls["force"] += 1
        return "injected-force-failure"

    monkeypatch.setattr(emu, "_force_terminate", failing_force)
    rep1 = emu.close(timeout=0.6)
    assert calls["force"] == 1
    assert rep1.closed is False
    assert rep1.process_exited is False
    assert rep1.guard_closed is False  # 资源保留、可重试
    assert "resources-retained-for-retry" in rep1.detail
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
