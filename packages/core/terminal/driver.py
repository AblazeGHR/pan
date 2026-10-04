"""自动化 driver 边界（P0）。

公共核心只提供 ``AutomationContext``：driver 只能经 lease 写终端、经 observer
读快照、等稳定；**业务菜单/键位状态机不属于公共核心**（留在业务 driver 层，
例如回滚自动化）。

``AutomationDriver`` Protocol 定义在 ``contracts.py``。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .attachments import AttachmentRegistry
from .contracts import LeaseToken, ScreenObserver, WaitOutcome
from .runtime import PtyRuntime


@dataclass
class AutomationContext:
    """driver 的受限作用面：无 ``proc``、无 queue、无生命周期 API。"""

    terminal_id: str
    runtime: PtyRuntime
    observer: ScreenObserver
    control: LeaseToken
    attachments: AttachmentRegistry
    timeout: float = 30.0

    def send_text(self, value: str) -> int:
        """经控制权 lease 发送文本（UTF-8）。"""
        return self.attachments.send(self.control, value.encode("utf-8", "surrogatepass"))

    def send_keys(self, *keys: str) -> int:
        """发送原始键序列（如 ``\\x1b[A`` 方向键）；键序列是 ASCII。"""
        return self.attachments.send(self.control, "".join(keys).encode("ascii"))

    def screen_text(self) -> str:
        text = getattr(self.observer, "text", None)
        if callable(text):
            return str(text())
        snapshot = self.observer.snapshot()
        return "\n".join(snapshot.lines).rstrip()

    def wait_for(
        self,
        predicate: Callable[[str], bool],
        timeout: float,
        *,
        quiet_ms: float = 0.0,
    ) -> WaitOutcome:
        return self.observer.wait_for(predicate, timeout, quiet_ms=quiet_ms)
