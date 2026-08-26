"""OpenCode (sst/opencode) CLI 适配器。

OpenCode 的 `opencode run` 是一次性进程（无 stdin 长驻协议），故通过 wrapper.py
包装成一个长驻子进程，由 wrapper 在内部循环调用 `opencode run --format json`。
设计对齐 kimi 适配（wrapper 模式）。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from ...session import Session

_log = logging.getLogger(__name__)

# opencode/* 前缀 = opencode 网关免费模型（无需用户 API key，gateway 处理鉴权）。
# 实测可用（2026-08-26）：big-pickle、mimo-v2.5-free、nemotron-3-ultra-free。
# 实测不可用：deepseek-v4-flash-free（gateway 服务端 500 "Unexpected server error"）、
#             north-mini-code-free（"Model ... is not supported" 401）。
_BUILTIN_MODELS = [
    "opencode/big-pickle",
    "opencode/mimo-v2.5-free",
    "opencode/nemotron-3-ultra-free",
    "opencode/deepseek-v4-flash-free",
    "opencode/north-mini-code-free",
    "moonshotai/kimi-k2.6",
    "moonshotai/kimi-k2.7-code",
    "moonshotai-cn/kimi-k2.6",
]


class OpencodeAdapter:
    """OpenCode CLI 适配器。实现 CliAdapter 协议，实例无状态，可被多 worker 共享。"""

    name = "opencode"

    # 执行模式（adapter-p1-oneshot.md）：opencode 用 wrapper 长驻，worker 只走
    # stream（与 kimi 同形）；wrapper 内部逐条 `opencode run` 的一次性语义对
    # worker 透明，故不暴露 oneshot。oneshot_args 不会被调用，返回 [] 兜底。
    execution_modes = ["stream"]

    _DEFAULT_MODEL = "opencode/big-pickle"
    _DEFAULT_PERMISSION_MODE = ""
    _DEFAULT_ALWAYS_THINKING_ENABLED = False
    _DEFAULT_EFFORT = ""

    # ── 元信息 ──

    @property
    def default_model(self) -> str:
        return self._opencode_config.get("model", self._DEFAULT_MODEL)

    @property
    def default_permission_mode(self) -> str:
        return self._opencode_config.get("permission_mode", self._DEFAULT_PERMISSION_MODE)

    @property
    def _opencode_config(self) -> dict:
        from ...config import load_config
        return load_config().get("opencode", {})

    _cached_models: list[str] | None = None  # class-level cache

    @property
    def supported_models(self) -> list[str]:
        """模型列表：config.json > `opencode models` 解析 > 内置默认值（缓存）。"""
        if OpencodeAdapter._cached_models is not None:
            return OpencodeAdapter._cached_models
        models = self._opencode_config.get("models")
        if isinstance(models, list) and len(models) > 0:
            OpencodeAdapter._cached_models = [str(m) for m in models]
            return OpencodeAdapter._cached_models
        cli_models = _parse_models_from_opencode()
        if cli_models:
            OpencodeAdapter._cached_models = cli_models
            return OpencodeAdapter._cached_models
        OpencodeAdapter._cached_models = list(self._BUILTIN_MODELS)
        return OpencodeAdapter._cached_models

    supports_resume = True
    supports_fork = True
    effort_values = ["", "minimal", "low", "medium", "high", "max"]
    permission_modes = [
        {"value": "", "label": "default (config)"},
        {"value": "auto", "label": "auto (--auto, 绕过 ask)"},
    ]
    default_permission_mode = ""

    # thinking 由 --thinking 显示；effort 由 --variant 表达
    supported_settings = ["model", "permissionMode", "effort", "thinking"]

    # ── 路径解析 ──

    @property
    def _OPENCODE_PATH(self) -> str:
        env = os.environ.get("PAN_OPENCODE_PATH")
        if env:
            return env
        which = shutil.which("opencode")
        return which or "opencode"

    @property
    def _wrapper_path(self) -> str:
        return str(Path(__file__).resolve().parent / "wrapper.py")

    # ── 进程启动 ──

    def base_args(self) -> list[str]:
        return [sys.executable, "-u", self._wrapper_path,
                "--opencode-path", self._OPENCODE_PATH]

    def model_args(self, s: Session) -> list[str]:
        return ["--model", s.model or self.default_model]

    def thinking_args(self, s: Session) -> list[str]:
        if s.adapter_config.get("thinking", False):
            return ["--thinking"]
        return []

    def effort_args(self, s: Session) -> list[str]:
        effort = s.adapter_config.get("effort", "")
        if effort:
            return ["--variant", effort]
        return []

    def permission_mode_args(self, s: Session) -> list[str]:
        # OpenCode run 仅 --auto（自动批准未显式拒绝项）；无 --yolo/--permission-mode
        if (s.permission_mode or self.default_permission_mode) == "auto":
            return ["--auto"]
        return []

    def resume_args(self, s: Session) -> list[str]:
        # session 连续性由 wrapper 持有；恢复既有会话时通过 --session-id 传入
        if s.cli_session_id:
            return ["--session-id", s.cli_session_id]
        return []

    def fork_args(self, s: Session | None = None) -> list[str]:
        """Fork 由 server.py 的 branch 端点经 DB 行复制完成（fork_opencode_session），
        不经过 worker 的 _branch_worker（其要求非空 extra_args）。此处返回 []。
        """
        return []

    def build_spawn_args(self, s: Session,
                         extra_args: list[str] | None = None) -> list[str]:
        args = self.base_args()
        args.extend(self.model_args(s))
        args.extend(self.thinking_args(s))
        args.extend(self.effort_args(s))
        args.extend(self.permission_mode_args(s))
        args.extend(self.resume_args(s))
        if extra_args:
            args.extend(extra_args)
        return args

    def oneshot_args(self, s: Session, text: str) -> list[str]:
        # opencode 的 worker 驱动方式只有 stream（wrapper 长驻），never 进入
        # oneshot 路径，故返回 []（防御兜底，详见 execution_modes 注释）。
        return []

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
        return event.get("type") or event.get("role") or ""

    def is_init_event(self, event: dict) -> bool:
        # OpenCode 每个事件都带 sessionID；worker 仅在首次写入 cli_session_id，幂等安全
        return bool(event.get("sessionID"))

    def extract_session_id(self, event: dict) -> str | None:
        return event.get("sessionID")

    def extract_model(self, event: dict) -> str | None:
        # --format json 的 streaming 事件不含 model 字段；由 enrich_after_result 回补
        return None

    def is_assistant_event(self, event: dict) -> bool:
        return event.get("type") in ("text", "tool_use", "reasoning")

    def extract_assistant_blocks(self, event: dict) -> list[dict]:
        blocks: list[dict] = []
        etype = event.get("type")
        part = event.get("part") or {}

        if etype == "text":
            # 可能是 assistant 文本，或 reasoning/thinking 块（part.type 区分）
            ptype = part.get("type", "text")
            text = part.get("text", "")
            if not text:
                return blocks
            if ptype == "reasoning":
                blocks.append({"role": "thinking", "content": text})
            else:
                blocks.append({"role": "assistant", "content": text})
        elif etype == "reasoning":
            text = part.get("text", "")
            if text:
                blocks.append({"role": "thinking", "content": text})
        elif etype == "tool_use":
            tool = part.get("tool") or (part.get("state") or {}).get("tool") or "tool"
            state = part.get("state") or {}
            inp = state.get("input")
            out = state.get("output")
            inp_str = json.dumps(inp, ensure_ascii=False) if isinstance(inp, (dict, list)) else str(inp or "")
            content = f"{tool}({inp_str})"
            if out:
                content += f"\n→ {out}"
            blocks.append({"role": "tool", "content": content})

        return blocks

    def is_result_event(self, event: dict) -> bool:
        # 原生无 result 事件；由 wrapper 合成
        return event.get("role") == "result"

    def is_result_error(self, event: dict) -> bool:
        return event.get("is_error", False)

    def extract_result_text(self, event: dict) -> str | None:
        return event.get("result")

    # ── takeover ──

    def takeover_command(self, s: Session) -> list[str]:
        if not s.cli_session_id:
            return []
        return [self._OPENCODE_PATH, "--session", s.cli_session_id]

    # ── enrich ──

    def enrich_after_result(self, s: Session) -> list[dict] | None:
        """从 SQLite 读取本轮新增的 usage（增量游标，避免重复累加）。

        OpenCode 的 `session` 表只存**会话级聚合**用量（非逐轮明细），故这里保存
        上次的聚合快照，本次返回二者差值作为新增条目；同时用 session.model 回填
        s.model（streaming 事件无 model 字段，见 extract_model）。

        返回 list[dict]（cbc 同构：{"model","rawUsage","timestamp"}），或 None。
        """
        if not s.cli_session_id:
            return None
        try:
            from . import sessions as oc_sessions
            entries = oc_sessions.get_raw_usage(s.cli_session_id, s.workdir or None)
            if not entries:
                return None
            cur = entries[0]
            cur_usage = cur.get("rawUsage", {})
            cur_model = cur.get("model", "")
            cur_ts = cur.get("timestamp", "")

            # 回填 model
            if not s.model and cur_model:
                s.model = cur_model

            prev = s.adapter_config.get("opencode_prev_usage") or {}
            delta = {
                "prompt_tokens": max(0, int(cur_usage.get("prompt_tokens", 0) - prev.get("prompt_tokens", 0))),
                "completion_tokens": max(0, int(cur_usage.get("completion_tokens", 0) - prev.get("completion_tokens", 0))),
                "reasoning_tokens": max(0, int(cur_usage.get("reasoning_tokens", 0) - prev.get("reasoning_tokens", 0))),
                "cache_read_tokens": max(0, int(cur_usage.get("cache_read_tokens", 0) - prev.get("cache_read_tokens", 0))),
                "cache_write_tokens": max(0, int(cur_usage.get("cache_write_tokens", 0) - prev.get("cache_write_tokens", 0))),
                "cost": round(max(0.0, float(cur_usage.get("cost", 0.0)) - float(prev.get("cost", 0.0))), 6),
            }
            # 推进游标
            s.set_adapter_field("opencode_prev_usage", {
                "prompt_tokens": cur_usage.get("prompt_tokens", 0),
                "completion_tokens": cur_usage.get("completion_tokens", 0),
                "reasoning_tokens": cur_usage.get("reasoning_tokens", 0),
                "cache_read_tokens": cur_usage.get("cache_read_tokens", 0),
                "cache_write_tokens": cur_usage.get("cache_write_tokens", 0),
                "cost": cur_usage.get("cost", 0.0),
            })

            if not any(delta.values()):
                return None
            return [{
                "model": cur_model,
                "rawUsage": delta,
                "timestamp": cur_ts,
            }]
        except Exception:
            _log.debug("opencode enrich_after_result failed", exc_info=True)
            return None


def _parse_models_from_opencode() -> list[str]:
    """解析 `opencode models` 输出（每行一个 provider/model）。"""
    try:
        r = subprocess.run(
            [shutil.which("opencode") or "opencode", "models"],
            capture_output=True, text=True, timeout=15,
        )
    except Exception:
        return []
    out = (r.stdout or "") + (r.stderr or "")
    models: list[str] = []
    import re
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        # 形如 provider/model-id；跳过 provider 分组标题等
        if re.match(r"^[\w.\-]+/[\w.\-]+$", line):
            models.append(line)
    return models
