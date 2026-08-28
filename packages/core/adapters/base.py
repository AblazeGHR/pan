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

    @property
    def execution_modes(self) -> list[str]:
        """Worker 对该 adapter 的可用"驱动方式"（见 docs/design/adapter-p1-oneshot.md）。

        取值子集：
        - ``"stream"``：worker 起一个常驻进程，跨消息复用 stdin/stdout；
        - ``"oneshot"``：worker 逐任务 spawn 一个一次性进程，prompt 作末参。

        语义边界（关键）：这是 **worker 与 adapter 之间的传输契约**，描述
        worker 如何驱动 adapter，而非 adapter 内部 CLI 是否"一次性"。
        kimi/opencode 用 wrapper 长驻，worker 只走 stream，故声明 ``["stream"]``；
        其 wrapper 内部再调 CLI 的一次性语义对 worker 透明，不在此暴露。
        只有 worker 会直接 spawn 短进程的 adapter（如 cbc）才声明 ``"oneshot"``。
        默认实现返回 ``["stream"]``（最保守；所有 adapter 至少支持 stream）。
        """
        ...

    # 可选能力（非协议必需，worker 用 getattr 探测，缺省 False）：
    #
    # supports_spawn_system_prompt: bool — stream spawn 的长驻进程是否接受
    # worker 传入的 ``--system-prompt <text>`` flag。True 的 adapter 必须真正
    # 消费该 flag（cbc CLI 原生支持；kimi 的 wrapper 转为其 CLI 原生
    # ``--agent-file``）。False/缺省时 worker 退化为首条消息注入——若强传给
    # 不认识的子进程，argparse 会报 unrecognized arguments 直接 exit 2，表现
    # 为「发消息后永不回复」（SMA(NoAdapter)+kimi 卡死根因）。

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

    def oneshot_args(self, s: Session, text: str) -> list[str]:
        """构建一次性执行的完整 argv（仅当 ``"oneshot"`` in execution_modes）。

        把 worker 原 ``_consumer_mcp`` 里 cbc 特定的拼装（base_args_stream →
        model/permission/effort/resume/mcp_args → ``--system-prompt``（仅首条）→
        prompt 作末参）搬进 adapter。worker 的通用 oneshot consumer 只负责：
        ``oneshot_args`` → spawn（无 stdin）→ 收集 stdout → 走既有 ``parse_event``
        事件模型。

        不在 ``execution_modes`` 中的 adapter 返回 ``[]``（不会被调用，防御兜底）。
        """
        ...

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


class SessionsProvider(Protocol):
    """adapter 原生 session 存储的统一读写接口（adapter-architecture P0-2）。

    cbc / kimi / opencode 的 ``<adapter>/sessions.py`` 各自实现了同一组能力
    （列 session / 解析历史 / usage / 标题 / fork），但命名签名不统一，导致
    server.py 按 adapter 硬分派 import/branch/rename。本协议定义统一方法名与
    签名，实现者是各 sessions **模块**（module 而非类），提供同名函数即可。
    server 按 adapter 名取 provider，每新增一个 adapter 只需在
    ``adapters/__init__.py`` 注册其 sessions 模块，无需再写分派逻辑。

    统一约定：
    - ``cwd``：工作目录上下文。cbc 即 project_cwd（自动 sanitize 到项目目录）、
      kimi/opencode 即 workdir。None 表示不限定/全量（各实现的默认语义）。

    可选能力（非协议必需，用 hasattr/getattr 探测）：
    - ``session_exists(session_id, cwd) -> bool``：import 时的存在性防御
      （cbc/opencode 提供；kimi 不提供则跳过 guard，保持旧行为）。
    - ``project_dir_to_path(project_dir) -> str | None``：cbc 独有，把 cbc
      项目目录名解析回真实路径（旧 /api/cbc/sessions/import 契约）。
    - ``browse_cbc_tree / list_cbc_projects / list_kimi_workspaces`` 等适配器
      独有能力保留在各自模块，由旧端点直接调用。
    """

    def list_sessions(self, cwd: str | None = None) -> list[dict]: ...

    def parse_history(self, session_id: str, cwd: str | None = None) -> list[dict]: ...

    def get_raw_usage(self, session_id: str, cwd: str | None = None) -> list[dict]: ...

    def get_session_title(self, session_id: str, cwd: str | None = None) -> str: ...

    def write_custom_title(self, session_id: str, title: str, cwd: str | None = None) -> None: ...

    def fork_session(self, parent_id: str, name: str, cwd: str | None = None) -> str: ...
