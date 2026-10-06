"""自动化屏幕观察者（P0，非权威）。

- ``ScreenObserver`` Protocol 与 ``ScreenSnapshot`` / ``WaitOutcome`` 定义在
  ``contracts.py``；本模块提供基于 pyte 的参考实现 ``PyteScreenObserver``；
- **pyte 延迟导入**：模块导入不依赖 pyte，缺依赖时构造失败并给出可执行提示；
- 固定 ``fidelity=partial``：pyte 无备用屏缓冲、无滚动历史、私有模式存为
  ``mode << 5``（实测结论，见契约报告 §4 R6 / M10）。它只服务**自动化判定**
  （业务 driver、测试），不足以承担“网页 TUI 状态恢复”；权威快照由 runner 内
  常驻仿真器（``AuthoritativeEmulator``，TA-B 实现）产出；
- resize 必须与 PTY 尺寸同步（附件契约的一部分），否则 TUI 按错误列数换行。
"""

from __future__ import annotations

import codecs
import threading
import time
from typing import Callable, Iterable

from .contracts import (
    BackendUnavailableError,
    Fidelity,
    ScreenSnapshot,
    WaitOutcome,
)

#: 备用屏相关 DEC 私有模式（47 为旧式，1047/1049 为现代 xterm 形式）。
ALT_SCREEN_DEC_MODES = (47, 1047, 1049)


def require_pyte():
    """Check the host runtime before automation has external side effects."""
    try:
        from pyte import Screen, Stream
        return Screen, Stream
    except ImportError as exc:
        raise BackendUnavailableError(
            "屏幕自动化观察需要运行 Pan 的 Python 环境安装 pyte；"
            "请用该解释器执行 `-m pip install -r minimal-requirements.txt`，"
            "再执行 `scripts/check_terminal_deployment.py` 检查部署。"
            "仅在 uv 隔离测试环境安装不会修复正在运行的 Pan。"
        ) from exc


class PyteScreenObserver:
    """基于 pyte 的参考实现（自动化观察专用，``fidelity=partial``）。

    实测限制（2026-10-03，探针 R6/M7，源码与运行结果）：

    1. pyte 把 DEC 私有模式存为 ``mode << 5``（看到 33568 而非 1049），
       必须右移还原；
    2. pyte **没有**实现备用屏缓冲（全源码无 47/1047/1049 处理）；
    3. pyte 没有 ``history``/滚动历史，也没有鼠标追踪、bracketed paste、
       Win32 输入模式（``?9001``）、焦点上报（``?1004``）等。
    """

    ENGINE = "pyte"
    SUPPORTS_ALTERNATE_SCREEN_BUFFER = False
    SUPPORTS_SCROLLBACK = False

    def __init__(self, rows: int, cols: int) -> None:
        Screen, Stream = require_pyte()
        self.rows = int(rows)
        self.cols = int(cols)
        self._screen = Screen(self.cols, self.rows)
        self._stream = Stream(self._screen)
        # 增量 UTF-8 解码：跨块保留未完成的多字节序列（split CJK 不得被破坏）。
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._bytes = 0
        self._lock = threading.Lock()

    def feed(self, data: bytes) -> None:
        with self._lock:
            # 增量解码：跨块拼接完整码点；非法字节以替换字符呈现（不静默丢弃）。
            text = self._decoder.decode(data)
            self._stream.feed(text)
            self._bytes += len(data)

    def text(self) -> str:
        with self._lock:
            return "\n".join(self._screen.display).rstrip()

    def resize(self, rows: int, cols: int) -> None:
        """仿真器尺寸必须与 PTY 一起变更（否则 TUI 重绘错位）。"""
        with self._lock:
            self._screen.resize(lines=int(rows), columns=int(cols))
            self.rows, self.cols = int(rows), int(cols)

    @property
    def observed_bytes(self) -> int:
        return self._bytes

    @staticmethod
    def decode_modes(raw: Iterable[int]) -> tuple[frozenset[int], frozenset[int]]:
        """还原 pyte 的 ``mode << 5`` 私有位，返回 (私有模式, 普通模式)。"""
        raw_set = {int(m) for m in raw}
        private = frozenset(m >> 5 for m in raw_set if m > 31)
        plain = frozenset(m for m in raw_set if m <= 31)
        return private, plain

    def snapshot(self) -> ScreenSnapshot:
        with self._lock:
            raw = frozenset(int(m) for m in getattr(self._screen, "mode", frozenset()))
            private, _plain = self.decode_modes(raw)
            cursor = (int(self._screen.cursor.y), int(self._screen.cursor.x))
            return ScreenSnapshot(
                rows=self._screen.lines,
                cols=self._screen.columns,
                lines=tuple(self._screen.display),
                cursor=cursor,
                alternate_screen=bool(private & set(ALT_SCREEN_DEC_MODES)),
                private_modes=private,
                raw_mode_bits=frozenset(raw),
                saved_cursor=bool(getattr(self._screen, "saved_columns", None) is not None),
                scrollback_lines=0,
                engine=f"{self.ENGINE} (single-buffer, no alt-screen, no scrollback)",
                fidelity=Fidelity.PARTIAL,
                note="pyte 无备用屏缓冲/滚动历史/鼠标/粘贴/键盘协议；模式位需 <<5 还原",
            )

    def wait_for(
        self,
        predicate: Callable[[str], bool],
        timeout: float,
        *,
        quiet_ms: float = 0.0,
    ) -> WaitOutcome:
        """等待屏幕文本满足 predicate；``quiet_ms>0`` 还要求满足后屏幕静置该时长。

        ``timed_out`` 与 ``matched`` 严格区分，避免“超时后最后一次文本恰好满足”
        被当作命中。
        """
        deadline = time.monotonic() + timeout
        last = self.text()
        settled_at: float | None = None
        while True:
            now = time.monotonic()
            if now >= deadline:
                return WaitOutcome(False, True, self.snapshot(), last, self._bytes)
            value = self.text()
            if predicate(value):
                if quiet_ms <= 0:
                    return WaitOutcome(True, False, self.snapshot(), value, self._bytes)
                if value != last:
                    settled_at = now
                elif settled_at is not None and (now - settled_at) * 1000 >= quiet_ms:
                    return WaitOutcome(True, False, self.snapshot(), value, self._bytes)
            last = value
            time.sleep(0.02)
