"""cbc (CodeBuddy CLI) 适配器。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from ...session import Session


class CbcAdapter:
    """cbc (CodeBuddy CLI) 适配器。

    实现 CliAdapter 协议。实例无状态，可被多 worker 共享。
    默认值从 project-root/config.json 读取（无配置文件则使用内置默认值）。
    """

    name = "cbc"

    # 内置兜底默认值（config.json 不存在时使用）
    _DEFAULT_MODEL = "deepseek-v4-flash"
    _DEFAULT_PERMISSION_MODE = "bypassPermissions"
    _DEFAULT_ALWAYS_THINKING_ENABLED = False
    _DEFAULT_EFFORT = ""

    @property
    def default_model(self) -> str:
        return self._cbc_config.get("model", self._DEFAULT_MODEL)

    @property
    def default_permission_mode(self) -> str:
        return self._cbc_config.get("permission_mode", self._DEFAULT_PERMISSION_MODE)

    @property
    def default_always_thinking_enabled(self) -> bool:
        return self._cbc_config.get("always_thinking_enabled", self._DEFAULT_ALWAYS_THINKING_ENABLED)

    @property
    def default_effort(self) -> str:
        return self._cbc_config.get("effort", self._DEFAULT_EFFORT)

    @property
    def _cbc_config(self) -> dict:
        from ...config import load_config
        return load_config().get("cbc", {})
    supported_models = [
        "glm-5.2", "glm-5.1", "glm-5.0", "glm-5.0-turbo", "glm-5v-turbo", "glm-4.7",
        "minimax-m3-pay", "minimax-m2.7",
        "kimi-k2.7", "kimi-k2.6",
        "hy3",
        "deepseek-v4-pro", "deepseek-v4-flash", "deepseek-v3-2-volc",
        "custom-local:deepseek-v4-pro",
    ]
    supports_resume = True
    supports_fork = True
    effort_values = ["none", "off", "auto", "low", "medium", "high", "xhigh", "max", "ultracode"]
    permission_modes = [
        {"value": "default", "label": "default"},
        {"value": "acceptEdits", "label": "acceptEdits"},
        {"value": "bypassPermissions", "label": "bypass"},
        {"value": "plan", "label": "plan"},
        {"value": "dontAsk", "label": "dontAsk"},
        {"value": "auto", "label": "auto"},
    ]

    default_permission_mode = "bypassPermissions"

    _CBC_PATH = os.environ.get(
        "PAN_CBC_PATH",
        r"D:\node_npm\node_global\cbc.cmd",
    )

    # ── 进程启动 ──

    def base_args(self) -> list[str]:
        return [self._CBC_PATH, "-p", "--output-format", "stream-json",
                "--input-format", "stream-json", "-y"]

    def model_args(self, s: Session) -> list[str]:
        return ["--model", s.model or self.default_model]

    def thinking_args(self, s: Session) -> list[str]:
        """cbc 的 alwaysThinkingEnabled 默认 true，关闭时需 --settings 覆盖。"""
        if not s.adapter_config.get("always_thinking_enabled", False):
            return ["--settings", '{"alwaysThinkingEnabled": false}']
        return []

    _VALID_EFFORT = frozenset({"none", "off", "auto", "low", "medium", "high", "xhigh", "max", "ultracode"})

    def effort_args(self, s: Session) -> list[str]:
        ace = s.adapter_config.get("effort", "")
        if s.adapter_config.get("always_thinking_enabled", False) and ace:
            if ace not in self._VALID_EFFORT:
                print(f"[CbcAdapter] Ignoring invalid effort value: {ace!r}")
                return []
            return ["--effort", ace]
        return []

    def permission_mode_args(self, s: Session) -> list[str]:
        mode = s.permission_mode or self.default_permission_mode
        return ["--permission-mode", mode]

    def resume_args(self, s: Session) -> list[str]:
        if s.cbc_session_id:
            return ["--resume", s.cbc_session_id]
        return []

    def fork_args(self, s: Session | None = None) -> list[str]:
        """返回 fork 参数。若 session 没有 cbc_session_id，需要显式 --resume。"""
        if s and not s.cbc_session_id:
            return ["--resume", "", "--fork-session"]
        return ["--fork-session"]

    def build_spawn_args(self, s: Session,
                          extra_args: list[str] | None = None) -> list[str]:
        args = self.base_args()
        args.extend(self.model_args(s))
        args.extend(self.permission_mode_args(s))
        args.extend(self.effort_args(s))
        args.extend(self.thinking_args(s))
        args.extend(self.resume_args(s))
        if extra_args:
            args.extend(extra_args)
        return args

    # ── stdin 消息编码 ──

    def encode_user_message(self, text: str) -> bytes:
        return json.dumps({
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": text}],
            },
        }).encode("utf-8")

    # ── stdout 事件解析 ──

    def parse_event(self, line: str) -> dict | None:
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return None

    def event_type(self, event: dict) -> str:
        return event.get("type", "")

    def is_init_event(self, event: dict) -> bool:
        return (event.get("type") == "system"
                and event.get("subtype") == "init")

    def extract_session_id(self, event: dict) -> str | None:
        return event.get("session_id")

    def extract_model(self, event: dict) -> str | None:
        return event.get("model")

    def is_assistant_event(self, event: dict) -> bool:
        return event.get("type") == "assistant"

    def extract_assistant_blocks(self, event: dict) -> list[dict]:
        blocks: list[dict] = []
        for b in event.get("message", {}).get("content", []) or []:
            if b.get("type") == "text":
                blocks.append({"role": "assistant", "content": b["text"]})
            elif b.get("type") == "thinking":
                blocks.append({"role": "thinking", "content": b["thinking"]})
            elif b.get("type") == "tool_use":
                blocks.append({
                    "role": "tool",
                    "content": f"{b['name']}({json.dumps(b.get('input', {}), separators=(',', ':'))})",
                })
        return blocks

    def is_result_event(self, event: dict) -> bool:
        return event.get("type") == "result"

    def is_result_error(self, event: dict) -> bool:
        return event.get("is_error", False)

    def extract_result_text(self, event: dict) -> str | None:
        return event.get("result")

    # ── takeover ──

    def takeover_command(self, s: Session) -> list[str]:
        if not s.cbc_session_id:
            return []
        return ["cbc", "--resume", s.cbc_session_id]

    # ── enrich ──

    def enrich_after_result(self, s: Session) -> dict | None:
        """从 JSONL 读取本轮对话最新的 raw_usage。

        从文件尾部向前扫描，找到第一条 assistant message 的 raw_usage。
        失败时静默返回 None，不影响主路径。
        """
        if not s.cbc_session_id:
            return None
        try:
            return _read_jsonl_latest_raw_usage(s.cbc_session_id)
        except Exception:
            return None


def _read_jsonl_latest_raw_usage(cbc_session_id: str) -> dict | None:
    """从 cbc session JSONL 文件尾部读取最新 raw_usage。"""
    import re
    base = Path(os.path.expanduser("~/.codebuddy/projects"))
    # 尝试常见 project dir 名
    for child in base.iterdir():
        if not child.is_dir():
            continue
        fpath = child / f"{cbc_session_id}.jsonl"
        if not fpath.exists():
            continue

        # 从后向前读取约 16KB，应覆盖最近几条 assistant message
        try:
            tail = _tail_bytes(str(fpath), 16 * 1024)
        except OSError:
            return None
        lines = tail.split(b"\n")
        last_raw_usage = None
        for raw_line in reversed(lines):
            line = raw_line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "message" and event.get("role") == "assistant":
                pd = event.get("providerData", {})
                ru = pd.get("rawUsage")
                if ru:
                    last_raw_usage = ru
                    return {
                        "model": pd.get("model", ""),
                        "rawUsage": ru,
                        "timestamp": event.get("timestamp", 0),
                    }
        return last_raw_usage

    return None


def _tail_bytes(filepath: str, size: int) -> bytes:
    """读取文件尾部约 size 字节。"""
    with open(filepath, "rb") as f:
        from os import SEEK_END
        f.seek(0, SEEK_END)
        file_size = f.tell()
        read_size = min(file_size, size)
        f.seek(-read_size, SEEK_END)
        return f.read()
