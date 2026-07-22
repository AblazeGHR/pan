"""CLI Adapter 协议定义。

每个 CLI 工具（cbc / claude-cli / gemini-cli / ...）实现 CliAdapter，
worker.py 通过协议调用，不感知具体工具。
"""

from __future__ import annotations
from typing import Protocol, runtime_checkable
from ..session import Session


@runtime_checkable
class CliAdapter(Protocol):
    """CLI 工具适配器协议。

    实现者可以是简单的类（不需要继承），只要满足所有方法签名即可。
    实例应无状态（除了配置常量），可被多 worker 共享。
    """

    # ── 元信息 ──

    @property
    def name(self) -> str: ...

    @property
    def default_model(self) -> str: ...

    @property
    def supported_models(self) -> list[str]: ...

    @property
    def effort_values(self) -> list[str]:
        """允许的 effort 级别列表（空列表表示不支持 effort）。"""
        ...

    @property
    def permission_modes(self) -> list[dict]:
        """允许的权限模式列表，每项 {"value": str, "label": str}。"""
        ...

    @property
    def default_permission_mode(self) -> str:
        """默认权限模式值。"""
        ...

    @property
    def supports_resume(self) -> bool: ...

    @property
    def supports_fork(self) -> bool: ...

    @property
    def supported_settings(self) -> list[str]:
        """该 adapter 支持的设置项标识列表（如 model, permissionMode, thinking, effort）。"""
        ...

    # ── 进程启动 ──

    def base_args(self) -> list[str]: ...

    def model_args(self, s: Session) -> list[str]: ...

    def thinking_args(self, s: Session) -> list[str]: ...

    def effort_args(self, s: Session) -> list[str]: ...

    def permission_mode_args(self, s: Session) -> list[str]: ...

    def resume_args(self, s: Session) -> list[str]: ...

    def fork_args(self, s: Session | None = None) -> list[str]: ...

    def build_spawn_args(self, s: Session,
                         extra_args: list[str] | None = None) -> list[str]: ...

    # ── stdin 消息编码 ──

    def encode_user_message(self, text: str) -> bytes: ...

    # ── stdout 事件解析 ──

    def parse_event(self, line: str) -> dict | None: ...

    def event_type(self, event: dict) -> str: ...

    def is_init_event(self, event: dict) -> bool: ...

    def extract_session_id(self, event: dict) -> str | None: ...

    def extract_model(self, event: dict) -> str | None: ...

    def is_assistant_event(self, event: dict) -> bool: ...

    def extract_assistant_blocks(self, event: dict) -> list[dict]: ...

    def is_result_event(self, event: dict) -> bool: ...

    def is_result_error(self, event: dict) -> bool: ...

    def extract_result_text(self, event: dict) -> str | None: ...

    # ── takeover ──

    def takeover_command(self, s: Session) -> list[str]: ...

    # ── enrich ──

    def enrich_after_result(self, s: Session) -> list[dict] | None:
        """一轮对话结束后，从 CLI 原生存储获取消耗数据（token/credit 等）。

        返回 None 表示该 adapter 不支持或本轮无数据。
        cbc: 读 JSONL 最新一条 assistant message 的 raw_usage。
        """
        ...
