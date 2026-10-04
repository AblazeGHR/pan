"""有界输出日志：绝对字节偏移、整块驱逐、精确 gap（平台无关，P0）。

设计取舍（见契约报告 §2.2 / M15）：

- 序号 ``seq`` = 该字节在流中的**绝对偏移**，与保留窗口无关，跨重连可复用；
- 驱逐以**整块**为单位，只避免“因驱逐在保留窗口起点额外制造残片”；
  **不保证** VT 完整性，也不保证序列不被切割；
- 单块超过上界时只保留尾部并计入 dropped；
- 边界是**字节流，不是序列流**：UTF-8 码点、CSI、OSC 都可能跨块/跨窗口起点
  被切割；序列重组与屏幕状态由仿真器/快照负责。客户端遇到 ``gap`` 或窗口
  起点可疑时必须走快照恢复，不能从窗口起点开始解析。
"""

from __future__ import annotations

import threading

from .contracts import (
    DEFAULT_OUTPUT_LOG_BYTES,
    InvalidCursorError,
    OutputChunk,
    OutputPage,
)


class OutputLog:
    """按字节上界保留的追加日志。

    ``retained_bytes <= max_bytes`` 是断言级不变量；``total_bytes`` 只增不减，
    是绝对游标空间。
    """

    def __init__(self, max_bytes: int = DEFAULT_OUTPUT_LOG_BYTES) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.max_bytes = int(max_bytes)
        self._chunks: list[OutputChunk] = []
        self._retained = 0
        self._total = 0
        self._dropped = 0
        self._lock = threading.Lock()

    # -- 写入 ---------------------------------------------------------
    def append(self, data: bytes) -> int:
        """追加一段字节并返回其绝对起始序号。

        空 ``data`` 是 no-op（返回当前 ``total_bytes``）。``data`` 可任意落在
        UTF-8/CSI/OSC 中间：本层不做对齐、不补字节。
        """
        if not data:
            return self.total_bytes
        with self._lock:
            seq = self._total
            self._total += len(data)
            if len(data) > self.max_bytes:
                keep = data[-self.max_bytes :]
                self._dropped += len(data) - len(keep)
                self._chunks.append(OutputChunk(seq=seq + len(data) - len(keep), data=keep))
            else:
                self._chunks.append(OutputChunk(seq=seq, data=data))
            self._retained += len(self._chunks[-1].data)
            self._evict_locked()
            return seq

    def _evict_locked(self) -> None:
        # 至少保留最后一块（它可能就等于上界），保证窗口永远非空。
        while self._retained > self.max_bytes and len(self._chunks) > 1:
            victim = self._chunks.pop(0)
            self._retained -= len(victim.data)
            self._dropped += len(victim.data)

    # -- 读取 ---------------------------------------------------------
    def read_from(self, cursor: int, *, max_bytes: int | None = None) -> OutputPage:
        """读取 ``cursor`` 起的至多 ``max_bytes`` 字节（缺省 = 本日志上界）。

        - ``cursor < 0`` 或 ``cursor > total_bytes`` -> ``InvalidCursorError``；
        - ``cursor`` 落后于保留窗口 -> ``gap=(cursor, first_retained_seq)``，
          **不补零**；返回内容从保留窗口起点开始；
        - ``truncated`` 表示因本次读取预算截断（可继续用 ``next_cursor`` 分页）。
        """
        with self._lock:
            return self._read_locked(cursor, max_bytes)

    def _read_locked(self, cursor: int, max_bytes: int | None) -> OutputPage:
        if cursor < 0:
            raise InvalidCursorError(f"cursor must be >= 0, got {cursor}")
        if cursor > self._total:
            raise InvalidCursorError(f"cursor {cursor} > produced {self._total}")
        first_seq = self._chunks[0].seq if self._chunks else self._total
        gap: tuple[int, int] | None = (cursor, first_seq) if cursor < first_seq else None
        budget = self.max_bytes if max_bytes is None else max(0, int(max_bytes))
        taken: list[OutputChunk] = []
        truncated = False
        for chunk in self._chunks:
            data = chunk.data[max(0, cursor - chunk.seq) :]
            if not data:
                continue
            if budget <= 0:
                truncated = True
                break
            if len(data) > budget:
                data = data[:budget]
                truncated = True
            taken.append(OutputChunk(seq=max(cursor, chunk.seq), data=data))
            budget -= len(data)
            if truncated:
                break
        next_cursor = (
            taken[-1].seq + len(taken[-1].data)
            if taken
            else (cursor if budget <= 0 else self._total)
        )
        return OutputPage(
            chunks=tuple(taken),
            first_seq=taken[0].seq if taken else max(cursor, first_seq),
            next_cursor=next_cursor,
            gap=gap,
            truncated=truncated,
        )

    # -- 统计 ---------------------------------------------------------
    @property
    def total_bytes(self) -> int:
        with self._lock:
            return self._total

    @property
    def retained_bytes(self) -> int:
        with self._lock:
            return self._retained

    @property
    def dropped_bytes(self) -> int:
        with self._lock:
            return self._dropped

    @property
    def first_retained_seq(self) -> int:
        with self._lock:
            return self._chunks[0].seq if self._chunks else self._total
