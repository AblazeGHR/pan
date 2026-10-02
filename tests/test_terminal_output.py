"""Pan Terminal P0：有界输出日志（跨平台纯逻辑）。

覆盖契约报告 M1-M3、M15 及增补边界：绝对字节序号、空 append no-op、非法游标
（负数/超过 total）、精确 gap 数学（整块驱逐不补零、单块超上限保留尾部）、
分页收敛、UTF-8/CSI 跨块与“窗口起点落在序列内部”、retained 不变量、
并发 append 不丢字节。
"""

from __future__ import annotations

import re
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from packages.core.terminal.contracts import InvalidCursorError, OutputChunk
from packages.core.terminal.output import OutputLog


def _join(page) -> bytes:
    return b"".join(chunk.data for chunk in page.chunks)


# ---------------------------------------------------------------------------
# 序号与基础读取
# ---------------------------------------------------------------------------


def test_append_returns_absolute_offsets():
    log = OutputLog(64)
    assert log.append(b"abc") == 0
    assert log.append(b"def") == 3
    assert log.total_bytes == 6
    assert log.retained_bytes == 6
    assert log.dropped_bytes == 0
    page = log.read_from(0)
    assert page.chunks == (OutputChunk(0, b"abc"), OutputChunk(3, b"def"))
    assert page.first_seq == 0
    assert page.next_cursor == 6
    assert page.gap is None
    assert page.truncated is False


def test_empty_append_is_noop():
    log = OutputLog(16)
    log.append(b"x")
    assert log.append(b"") == 1
    assert log.total_bytes == 1


def test_read_from_negative_cursor_raises():
    log = OutputLog(16)
    log.append(b"x")
    with pytest.raises(InvalidCursorError):
        log.read_from(-1)


def test_read_from_cursor_beyond_total_raises():
    log = OutputLog(16)
    log.append(b"x")
    with pytest.raises(InvalidCursorError):
        log.read_from(2)
    with pytest.raises(InvalidCursorError):
        log.read_from(log.total_bytes + 10)


def test_cursor_at_total_returns_empty_page():
    log = OutputLog(16)
    log.append(b"abc")
    page = log.read_from(3)
    assert page.chunks == ()
    assert page.next_cursor == 3
    assert page.gap is None
    assert page.truncated is False


# ---------------------------------------------------------------------------
# 驱逐与 gap 数学
# ---------------------------------------------------------------------------


def test_gap_math_whole_block_eviction():
    log = OutputLog(8)
    log.append(b"aaaa")  # 0..4
    log.append(b"bbbb")  # 4..8
    log.append(b"cccc")  # 8..12 -> 驱逐第一块
    assert log.total_bytes == 12
    assert log.retained_bytes == 8
    assert log.dropped_bytes == 4
    assert log.first_retained_seq == 4
    page = log.read_from(0)
    assert page.gap == (0, 4)
    assert [chunk.data for chunk in page.chunks] == [b"bbbb", b"cccc"]
    assert page.next_cursor == 12
    # gap 区间绝不补零：返回内容从保留窗口起点开始。
    assert _join(page) == b"bbbbcccc"
    page2 = log.read_from(4)
    assert page2.gap is None


def test_gap_inside_evicted_region():
    log = OutputLog(8)
    log.append(b"aaaa")
    log.append(b"bbbb")
    log.append(b"cccc")
    page = log.read_from(3)  # 3 落在已驱逐区域
    assert page.gap == (3, 4)
    assert _join(page) == b"bbbbcccc"


def test_single_chunk_over_cap_keeps_tail():
    log = OutputLog(8)
    seq = log.append(b"0123456789ABC")  # 13 字节 > cap
    assert seq == 0
    assert log.total_bytes == 13
    assert log.retained_bytes == 8
    assert log.dropped_bytes == 5
    assert log.first_retained_seq == 5
    page = log.read_from(0)
    assert page.gap == (0, 5)
    assert page.chunks == (OutputChunk(5, b"56789ABC"),)
    assert page.next_cursor == 13


def test_retained_identity_and_cap_invariant():
    log = OutputLog(32)
    for i in range(100):
        log.append(bytes([i % 251]) * (i % 7 + 1))
        assert log.retained_bytes <= 32
        assert log.total_bytes == log.retained_bytes + log.dropped_bytes


def test_paging_converges_without_gaps():
    log = OutputLog(1024)
    data = bytes(range(256)) * 4
    log.append(data)
    cursor = 0
    collected = b""
    pages = 0
    while cursor < log.total_bytes:
        page = log.read_from(cursor, max_bytes=64)
        assert page.gap is None
        collected += _join(page)
        assert page.next_cursor > cursor
        cursor = page.next_cursor
        pages += 1
        assert pages < 100
    assert collected == data


def test_zero_capacity_read_makes_no_progress():
    log = OutputLog(16)
    log.append(b"abc")
    page = log.read_from(0, max_bytes=0)
    assert page.chunks == ()
    assert page.next_cursor == 0
    assert page.truncated is True


# ---------------------------------------------------------------------------
# 字节流边界（M15）：跨块/窗口起点可能切断 UTF-8 / CSI / OSC
# ---------------------------------------------------------------------------


def test_utf8_split_across_chunks_joins_byte_exact():
    log = OutputLog(64)
    log.append(b"\xe4\xb8")
    log.append(b"\xad")
    page = log.read_from(0)
    assert _join(page) == "中".encode("utf-8")
    # 单块解码会破坏 UTF-8：这是契约边界，“日志是字节流不是序列流”。
    with pytest.raises(UnicodeDecodeError):
        page.chunks[0].data.decode("utf-8")


def test_window_start_can_land_inside_csi_sequence():
    log = OutputLog(8)
    log.append(b"\x1b[38;5")  # 6 字节：CSI 序列开始于 0
    log.append(b";196m")  # 5 字节；驱逐第一块后窗口起点 = 6
    assert log.dropped_bytes == 6
    assert log.first_retained_seq == 6
    page = log.read_from(0)
    assert page.gap == (0, 6)
    # 完整序列 "\x1b[38;5;196m" 长 11 字节（'m' 在 index 10）；
    # 窗口起点 6 落在序列内部：客户端不得从窗口起点解析，必须走快照恢复。
    csi_end = 10
    assert 0 < log.first_retained_seq < csi_end


def test_osc_split_across_chunks_kept_verbatim():
    log = OutputLog(64)
    log.append(b"\x1b]0;title")
    log.append(b"\x07rest")
    page = log.read_from(0)
    assert _join(page) == b"\x1b]0;title\x07rest"


# ---------------------------------------------------------------------------
# 并发
# ---------------------------------------------------------------------------


def test_concurrent_appends_preserve_all_bytes():
    log = OutputLog(1 << 20)
    threads_count, per_thread = 8, 25
    expected: list[bytes] = []

    def worker(index: int) -> None:
        for item in range(per_thread):
            log.append(f"[{index:02d}-{item:02d}]".encode())

    for index in range(threads_count):
        for item in range(per_thread):
            expected.append(f"[{index:02d}-{item:02d}]".encode())
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(threads_count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)

    assert log.total_bytes == threads_count * per_thread * 7  # "[00-00]" = 7 字节
    assert log.dropped_bytes == 0
    page = log.read_from(0, max_bytes=1 << 20)
    data = _join(page)
    assert len(data) == log.total_bytes
    found = re.findall(rb"\[\d\d-\d\d\]", data)
    assert sorted(found) == sorted(expected)
