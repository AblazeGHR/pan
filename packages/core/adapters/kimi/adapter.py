"""Kimi Code CLI 适配器。

Kimi 的 `-p/--prompt` 模式是一次性进程，因此通过 wrapper.py 包装成一个长驻子进程，
由 wrapper 在内部循环调用 Kimi 并转发 stream-json 事件。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from ...session import Session


class KimiAdapter:
    """Kimi Code CLI 适配器。

    实现 CliAdapter 协议。实例无状态，可被多 worker 共享。
    由于 Kimi 没有 stdin stream-json 长驻模式，实际 Worker 进程是 wrapper.py，
    wrapper 内部逐条调用 `kimi -p ... --output-format stream-json`。
    """

    name = "kimi"

    _DEFAULT_MODEL = "kimi-code/kimi-for-coding"
    _DEFAULT_PERMISSION_MODE = ""
    _DEFAULT_ALWAYS_THINKING_ENABLED = False
    _DEFAULT_EFFORT = ""

    @property
    def default_model(self) -> str:
        return self._kimi_config.get("model", self._DEFAULT_MODEL)

    @property
    def default_permission_mode(self) -> str:
        return self._kimi_config.get("permission_mode", self._DEFAULT_PERMISSION_MODE)

    @property
    def default_always_thinking_enabled(self) -> bool:
        return self._kimi_config.get("always_thinking_enabled", self._DEFAULT_ALWAYS_THINKING_ENABLED)

    @property
    def default_effort(self) -> str:
        return self._kimi_config.get("effort", self._DEFAULT_EFFORT)

    @property
    def _kimi_config(self) -> dict:
        from ...config import load_config
        return load_config().get("kimi", {})

    supported_models = [
        "kimi-code/kimi-for-coding",
        "kimi-code/kimi-for-coding-highspeed",
        "kimi-code/k3",
    ]

    supports_resume = False  # Kimi -S 恢复上下文但不重放历史事件
    supports_fork = True  # 通过文件复制实现 fork
    effort_values = ["low", "high", "max"]
    permission_modes = [
        {"value": "", "label": "default (interactive)"},
        {"value": "yolo", "label": "yolo (not available in -p mode)"},
        {"value": "auto", "label": "auto (not available in -p mode)"},
        {"value": "plan", "label": "plan (not available in -p mode)"},
    ]
    default_permission_mode = ""

    # wrapper 使用 `kimi -p` 一次性 prompt 模式，该模式下不支持 -y/--auto/--plan
    # 等权限参数，也不支持 thinking/effort 命令行参数，因此前端只展示 model。
    supported_settings = ["model"]

    @property
    def _KIMI_PATH(self) -> str:
        env = os.environ.get("PAN_KIMI_PATH") or os.environ.get("CLICONDUCTOR_KIMI_PATH")
        if env:
            return env
        if sys.platform == "win32":
            return str(Path.home() / ".kimi-code" / "bin" / "kimi.exe")
        return "kimi"

    @property
    def _wrapper_path(self) -> str:
        return str(Path(__file__).resolve().parent / "wrapper.py")

    # ── 进程启动 ──

    def base_args(self) -> list[str]:
        return [sys.executable, "-u", self._wrapper_path,
                "--kimi-path", self._KIMI_PATH]

    def model_args(self, s: Session) -> list[str]:
        return ["--model", s.model or self.default_model]

    def thinking_args(self, s: Session) -> list[str]:
        # Kimi 的思考配置在 config.toml 中，CLI 没有独立的 --thinking 参数
        return []

    def effort_args(self, s: Session) -> list[str]:
        # Kimi prompt 模式暂不支持 effort 命令行参数
        return []

    def permission_mode_args(self, s: Session) -> list[str]:
        # Kimi 的 -y/--auto/--plan 不能和 -p 同时使用
        return []

    def resume_args(self, s: Session) -> list[str]:
        if s.cli_session_id:
            return ["--session-id", s.cli_session_id]
        return []

    def fork_args(self, s: Session | None = None) -> list[str]:
        """Fork a Kimi session by copying files.

        Kimi CLI has no stable --fork flag, so we copy the session directory and
        register the new session in session_index.jsonl. The new session id is
        written into s.cli_session_id; build_spawn_args will then resume from it.
        """
        if s is None or not s.cli_session_id:
            return []
        try:
            from . import sessions as kimi_sessions
            new_id = kimi_sessions.fork_kimi_session(
                s.cli_session_id, s.name, workdir=s.workdir or None
            )
            s.cli_session_id = new_id
        except Exception as exc:
            print(f"[KimiAdapter] fork failed: {exc}")
            return []
        return []

    def build_spawn_args(self, s: Session,
                          extra_args: list[str] | None = None) -> list[str]:
        args = self.base_args()
        args.extend(self.model_args(s))
        args.extend(self.resume_args(s))
        if extra_args:
            args.extend(extra_args)
        return args

    # ── stdin 消息编码 ──

    def encode_user_message(self, text: str) -> bytes:
        return json.dumps({"text": text}).encode("utf-8")

    # ── stdout 事件解析 ──

    def parse_event(self, line: str) -> dict | None:
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return None

    def event_type(self, event: dict) -> str:
        return event.get("role", "")

    def is_init_event(self, event: dict) -> bool:
        return (event.get("role") == "meta"
                and event.get("type") == "session.resume_hint")

    def extract_session_id(self, event: dict) -> str | None:
        return event.get("session_id")

    def extract_model(self, event: dict) -> str | None:
        return None

    def is_assistant_event(self, event: dict) -> bool:
        return event.get("role") == "assistant"

    def extract_assistant_blocks(self, event: dict) -> list[dict]:
        blocks: list[dict] = []
        if event.get("content"):
            blocks.append({"role": "assistant", "content": event["content"]})
        for tc in event.get("tool_calls", []):
            fn = tc.get("function", {})
            name = fn.get("name", "?")
            args = fn.get("arguments", "{}")
            blocks.append({"role": "tool", "content": f"{name}({args})"})
        return blocks

    def is_result_event(self, event: dict) -> bool:
        return event.get("role") == "result"

    def is_result_error(self, event: dict) -> bool:
        return event.get("is_error", False)

    def extract_result_text(self, event: dict) -> str | None:
        return event.get("result")

    # ── takeover ──

    def takeover_command(self, s: Session) -> list[str]:
        if not s.cli_session_id:
            return []
        return ["kimi", "-S", s.cli_session_id]

    # ── enrich ──

    def enrich_after_result(self, s: Session) -> list[dict] | None:
        """Kimi 的 usage 记录在 wire.jsonl 中，暂不从文件系统读取。"""
        return None
