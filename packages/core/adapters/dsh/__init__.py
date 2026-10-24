"""DeepSeek Harness (dsh) adapter for Pan.

- adapter.py：实现 CliAdapter 协议（wrapper 长驻 + stream 模式）
- wrapper.py：常驻桥接；内部逐条 ``dsh headless --json``，翻成 Pan 事件
- events.py：DSH NDJSON → Pan 事件模型（wrapper 与 adapter 共用）
- sessions.py：SessionsProvider（读 ``$DSH_HOME/sessions`` 的多帧 zstd 日志）
"""

from __future__ import annotations

from .adapter import DshAdapter

__all__ = ["DshAdapter"]
