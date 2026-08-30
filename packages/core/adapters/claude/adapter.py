"""Claude Code (claude-cli) 适配器。

Claude Code 的 ``claude -p``（print / 非交互）模式是一个**一次性进程**：它把
整个回复以 ``--output-format stream-json`` 的事件流打到 stdout，最后打印一条
``result`` 事件并退出。因此本 adapter 走 **oneshot** 执行模式（对齐 cbc 的
oneshot 路径），而不像 kimi/opencode 那样需要长驻 wrapper：

- 每条消息 spawn 一个 ``claude -p --output-format stream-json --verbose "<prompt>"``；
- 上下文续接用 ``--resume <cli_session_id>``（claude 一次 print 运行天然对应一轮对话）；
- worker 的 ``_consumer_oneshot`` 收集 stdout、按既有事件模型解析，无需 wrapper；
- 天然规避 wrapper 模式的 stdin EOF 挂起坑（#2），也无需维护长驻进程。

MCP 经 ``--mcp-config <path>`` 注入（共享 helper 写 data/mcp-configs/<id>.mcp.json）。

CLI 入口解析（关键坑）：Windows 下 ``shutil.which("claude")`` 返回 ``claude.CMD``
（npm shim）。把它直接交给 subprocess 会经 ``cmd.exe /c`` 启动，cmd.exe 用系统
ANSI 代码页重新切分命令行，导致非 ASCII 参数（中文 prompt）乱码——正是 opencode
卡 running 的根因之一。本 adapter 把 shim 解析为真实入口
``<npm-global>/node_modules/@anthropic-ai/claude-code/bin/claude.exe``（本 npm 包是
编译二进制；若安装形态为 node 脚本则回退 ``node <cli.js>``），参数经 CreateProcess
原样传递，不再经过 cmd.exe 二次解析。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from pathlib import Path
from ...session import Session
from ..mcp import write_mcp_json

_log = logging.getLogger(__name__)


# 真实入口（绕过 .CMD shim）解析出的可执行文件 / node 入口。
# 命中后，base_args 直接用其 argv 前缀，参数经 CreateProcess 原样传递。
_BUILTIN_MODELS = [
    "claude-opus-4-8",
    "claude-sonnet-4-5",
    "claude-haiku-4-5",
    "opus",
    "sonnet",
    "haiku",
]


# 模块级缓存：worker 解析 stdout 的 result 事件时（extract_result_text）把其
# usage + cost 暂存于此，enrich_after_result 随后取出回填（见 enrich_after_result）。
# key = claude session_id（每会话唯一），读取即弹出，无内存泄漏、无跨会话污染
# （async 事件循环单线程，且按 session_id 隔离）。JSONL 兜底路径在缓存未命中时启用。
_PENDING_RESULT_USAGE: dict[str, dict] = {}


class ClaudeAdapter:
    """Claude Code CLI 适配器。实现 CliAdapter 协议，实例无状态，可多 worker 共享。"""

    name = "claude"

    # 执行模式：claude -p 是一次性进程，故只声明 oneshot（对齐 cbc 的 oneshot 分支，
    # 复用 worker 成熟的 _consumer_oneshot，无需 wrapper）。worker 据此永远走 oneshot。
    execution_modes = ["oneshot"]

    _DEFAULT_MODEL = ""  # 空 → 不传 --model，让 claude 用其配置的默认模型
    _DEFAULT_PERMISSION_MODE = "bypassPermissions"
    _DEFAULT_ALWAYS_THINKING_ENABLED = False
    _DEFAULT_EFFORT = ""

    @property
    def default_model(self) -> str:
        return self._claude_config.get("model", self._DEFAULT_MODEL)

    @property
    def default_permission_mode(self) -> str:
        return self._claude_config.get("permission_mode", self._DEFAULT_PERMISSION_MODE)

    @property
    def _claude_config(self) -> dict:
        from ...config import load_config
        return load_config().get("claude", {})

    _cached_models: list[str] | None = None  # class-level cache

    @property
    def supported_models(self) -> list[str]:
        """模型列表：config.json > 内置默认值（缓存）。

        claude 没有稳定可解析的 ``--list-models`` / ``--help`` 模型清单（不像
        cbc 的 "Currently supported" 段），故不跑 CLI 解析，仅用 config 显式配置或
        内置常用别名/全名（含 claude code 的 opus/sonnet/haiku 简写与完整 id）。
        """
        if ClaudeAdapter._cached_models is not None:
            return ClaudeAdapter._cached_models
        models = self._claude_config.get("models")
        if isinstance(models, list) and len(models) > 0:
            ClaudeAdapter._cached_models = [str(m) for m in models]
            return ClaudeAdapter._cached_models
        ClaudeAdapter._cached_models = list(_BUILTIN_MODELS)
        return ClaudeAdapter._cached_models

    @classmethod
    def invalidate_models_cache(cls) -> None:
        """清空模型列表缓存（POST /api/config/reload 热重载用）。

        claude 缓存无 TTL（读一次不再刷新），热重载是唯一不重启的刷新途径。
        """
        cls._cached_models = None

    supports_resume = True
    supports_fork = True  # 经 JSONL 复制实现（见 sessions.fork_session）
    effort_values = ["", "low", "medium", "high", "xhigh", "max"]
    permission_modes = [
        {"value": "default", "label": "default"},
        {"value": "acceptEdits", "label": "acceptEdits"},
        {"value": "bypassPermissions", "label": "bypass"},
        {"value": "plan", "label": "plan"},
    ]
    default_permission_mode = "bypassPermissions"

    # thinking 在 -p + --verbose 下由模型自动产出（stream-json 含 thinking 块），
    # 无独立 --thinking 开关；故 supported_settings 仅暴露模型/权限/effort。
    supported_settings = ["model", "permissionMode", "effort"]

    # ── 真实入口解析（绕过 .CMD shim）──

    @property
    def _claude_argv(self) -> list[str]:
        return self._resolve_claude_argv()

    def _resolve_claude_path(self) -> str:
        """确定 claude 可执行文件路径：config > 环境变量 > PATH 查找 > 回退名。"""
        cfg = self._claude_config.get("path")
        if cfg:
            return cfg
        env = os.environ.get("PAN_CLAUDE_PATH")
        if env:
            return env
        which = shutil.which("claude")
        return which or "claude"

    def _resolve_claude_argv(self) -> list[str]:
        """返回启动 claude 的完整 argv 前缀（绕过 .CMD shim）。

        Windows 上 ``shutil.which("claude")`` 返回 ``claude.CMD``，经 cmd.exe 启动会
        把中文参数乱码化。这里把 shim 解析为真实入口
        ``<dir>/node_modules/@anthropic-ai/claude-code/bin/claude.exe``（编译二进制），
        或回退 ``node <cli.js>``（node 脚本形态），或直接返回 shim 本身（Unix 软链）。
        """
        cfg = self._claude_config.get("path")
        if cfg:
            return [cfg]
        env = os.environ.get("PAN_CLAUDE_PATH")
        if env:
            return [env]
        which = shutil.which("claude")
        if not which:
            return ["claude"]
        if which.lower().endswith((".cmd", ".bat")):
            resolved = _resolve_claude_exe_from_shim(which)
            if resolved:
                return resolved
        return [which]

    def resolved_cli_argv(self) -> list[str]:
        """Return the resolved Claude executable components for preflight checks."""
        return self._resolve_claude_argv()

    # ── 进程启动 ──

    def base_args(self) -> list[str]:
        """oneshot 基础 argv：非交互 print 模式 + stream-json 事件流 + verbose。"""
        return self._claude_argv + [
            "-p", "--output-format", "stream-json", "--verbose",
        ]

    def model_args(self, s: Session) -> list[str]:
        m = s.model or self.default_model
        if not m:
            return []  # 空 → 不传 --model，claude 用其配置默认模型
        return ["--model", m]

    def thinking_args(self, s: Session) -> list[str]:
        # claude 在 -p + --verbose 下自动产出 thinking 块，无独立 --thinking 开关。
        return []

    def effort_args(self, s: Session) -> list[str]:
        effort = s.adapter_config.get("effort", "")
        if effort:
            return ["--effort", effort]
        return []

    def permission_mode_args(self, s: Session) -> list[str]:
        mode = s.permission_mode or self.default_permission_mode
        if not mode:
            return []
        return ["--permission-mode", mode]

    def resume_args(self, s: Session) -> list[str]:
        if s.cli_session_id:
            return ["--resume", s.cli_session_id]
        return []

    def fork_args(self, s: Session | None = None) -> list[str]:
        """Fork 由 server 的 branch 端点经 sessions provider 的 fork_session
        （JSONL 复制 + 新 cli_session_id）完成；此处在新 worker 创建时若已带
        cli_session_id，则返回 [] 让 worker 以 --resume 续接 fork 出的会话。
        """
        return []

    def build_spawn_args(self, s: Session,
                         extra_args: list[str] | None = None) -> list[str]:
        """stream 模式 argv 构建（oneshot 模式下 worker 实际不调用，仅防御/测试）。"""
        args = self.base_args()
        args.extend(self.model_args(s))
        args.extend(self.permission_mode_args(s))
        args.extend(self.effort_args(s))
        args.extend(self.resume_args(s))
        args.extend(self.mcp_args(s))
        if extra_args:
            args.extend(extra_args)
        return args

    def oneshot_args(self, s: Session, text: str) -> list[str]:
        """一次性执行的完整 argv（worker._consumer_oneshot 调用）。

        claude -p 是一次性进程：prompt 作末参，无需 stdin（规避 wrapper 的 stdin
        EOF 坑）。逐元素对齐 cbc oneshot 拼装：base_args(-p stream-json verbose) →
        model / permission / effort / resume / mcp → 首条任务的 --system-prompt →
        prompt 末参。
        """
        args = list(self.base_args())
        args.extend(self.model_args(s))
        args.extend(self.permission_mode_args(s))
        args.extend(self.effort_args(s))
        args.extend(self.resume_args(s))
        args.extend(self.mcp_args(s))
        # system_prompt 仅首条（cli_session_id 尚未捕获）注入；续接靠 --resume 承载。
        if s.system_prompt and not s.cli_session_id:
            args.extend(["--system-prompt", s.system_prompt])
        args.append(text)
        return args

    def mcp_args(self, s: Session) -> list[str]:
        """写 data/mcp-configs/<session_id>.mcp.json 并返回 --mcp-config <path>。

        claude 支持 ``--mcp-config <path>``（JSON 文件，含 ``mcpServers`` 键，与
        cbc 同格式；共享 helper 写）。未配置/写失败返回 []（无 MCP flag）。
        """
        servers = s.adapter_config.get("mcp_servers")
        if not servers:
            return []
        from ...session import SESSION_DIR
        mcp_json_path = SESSION_DIR.parent / "mcp-configs" / f"{s.id}.mcp.json"
        if write_mcp_json(mcp_json_path, s) is None:
            return []
        return ["--mcp-config", str(mcp_json_path)]

    # ── stdin 消息编码 ──
    # oneshot 模式不读 stdin（prompt 在 argv），此方法是协议要求的占位实现。

    def encode_user_message(self, text: str) -> bytes:
        return json.dumps({"type": "user", "text": text}).encode("utf-8")

    # ── stdout 事件解析 ──
    # claude stream-json 事件格式与 cbc 几乎同构：
    #   {"type":"system","subtype":"init","session_id":...,"model":...}
    #   {"type":"assistant","message":{"content":[{type:text|thinking|tool_use,...}]}}
    #   {"type":"result","is_error":bool,"result":...,"session_id":...,"usage":...}

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
        # init 事件的 model 即本会话配置模型（如 claude-opus-4-8[1m]）；assistant
        # 事件的实际生成模型在 message.model。worker 仅在 init 分支调用本方法。
        return event.get("model")

    def is_assistant_event(self, event: dict) -> bool:
        return event.get("type") == "assistant"

    def extract_assistant_blocks(self, event: dict) -> list[dict]:
        blocks: list[dict] = []
        msg = event.get("message", {}) or {}
        for b in msg.get("content", []) or []:
            if not isinstance(b, dict):
                continue
            bt = b.get("type")
            if bt == "text":
                text = b.get("text", "")
                if text:
                    blocks.append({"role": "assistant", "content": text})
            elif bt == "thinking":
                thinking = b.get("thinking", "")
                if thinking:
                    blocks.append({"role": "thinking", "content": thinking})
            elif bt == "tool_use":
                name = b.get("name", "?")
                inp = b.get("input", {})
                inp_str = (json.dumps(inp, ensure_ascii=False)
                           if isinstance(inp, (dict, list)) else str(inp or ""))
                blocks.append({"role": "tool", "content": f"{name}({inp_str})"})
        return blocks

    def is_result_event(self, event: dict) -> bool:
        return event.get("type") == "result"

    def is_result_error(self, event: dict) -> bool:
        return bool(event.get("is_error", False))

    def extract_result_text(self, event: dict) -> str | None:
        # 副作用：把 result 事件的 usage + cost 暂存到模块缓存，供 enrich_after_result
        # 回填（result 事件含完整 token 用量与 total_cost_usd，但 JSONL 不写 result
        # 事件，故此处是 cost 的唯一权威来源）。worker 在调用本方法后调用 enrich。
        if self.is_result_event(event):
            sid = event.get("session_id")
            if sid:
                _PENDING_RESULT_USAGE[sid] = _result_usage_entry(event)
        return event.get("result")

    # ── takeover ──

    def takeover_command(self, s: Session) -> list[str]:
        """交互式接管：``claude --resume <cli_session_id>``（cwd 由 server 终端设置）。

        不重注入 --system-prompt（接管仅 resume 既有会话，system prompt 由 claude
        原生上下文承载；重注入会把它当一条 user 消息重复）。权限沿用默认（交互式
        由用户在 TUI 内批准）。
        """
        if not s.cli_session_id:
            return []
        return [*self._claude_argv, "--resume", s.cli_session_id]

    # ── enrich ──

    def enrich_after_result(self, s: Session) -> list[dict] | None:
        """从原生存储获取本轮消耗（token/cost）。

        优先取 worker 解析 result 事件时暂存的 ``_PENDING_RESULT_USAGE``（含 cost，
        权威）；缓存未命中（如 re-import 路径不触发 extract_result_text）则回退读
        claude JSONL 的 assistant 事件 usage（token 准确，cost 记为 0）。

        返回 list[dict]（与 cbc 同构：{"model","rawUsage","timestamp"}），或 None。
        """
        if not s.cli_session_id:
            return None
        try:
            entry = _PENDING_RESULT_USAGE.pop(s.cli_session_id, None)
            if entry is not None:
                # 用最新 model 回填 s.model（result 事件无单 model 字段时用 s.model）
                if not s.model and entry.get("model"):
                    s.model = entry["model"]
                return [entry]
            # 兜底：JSONL（token 准确，cost=0）
            return _read_claude_jsonl_usage(s)
        except Exception:
            _log.debug("claude enrich_after_result failed", exc_info=True)
            return None


# ── 入口解析 helper ──

def _resolve_claude_exe_from_shim(shim_path: str) -> list[str] | None:
    """由 .CMD/.bat shim 解析出真实 claude 入口 argv。

    优先命中编译二进制 ``bin/claude.exe``（本 npm 包形态）；否则回退
    ``node <cli.js>``。命中返回完整 argv 前缀，否则返回 None（调用方回退 shim 本身）。
    """
    if not shim_path.lower().endswith((".cmd", ".bat")):
        return None  # 已是真实可执行文件（Linux/macOS 软链）
    shim_dir = os.path.dirname(os.path.abspath(shim_path))
    bin_name = "claude.exe" if sys.platform == "win32" else "claude"
    candidates = [
        os.path.join(shim_dir, "node_modules", "@anthropic-ai", "claude-code", "bin", bin_name),
        os.path.join(shim_dir, "node_modules", "@anthropic-ai", "claude-code", "bin", "claude"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return [c]
    # 回退 node 脚本形态
    cli_js = os.path.join(shim_dir, "node_modules", "@anthropic-ai", "claude-code", "cli.js")
    if os.path.isfile(cli_js):
        node_exe = os.path.join(shim_dir, "node.exe")
        if not os.path.exists(node_exe):
            node_exe = "node"
        return [node_exe, cli_js]
    return None


# ── enrich helper ──

def _result_usage_entry(event: dict) -> dict:
    """从 stdout 的 result 事件提取单条 raw_usage 条目。"""
    usage = event.get("usage", {}) or {}
    model = event.get("model") or ""
    # result 事件无单一 model 字段；modelUsage 是 per-model 明细，取首个 key 作 model 提示。
    if not model:
        mu = event.get("modelUsage") or {}
        model = next(iter(mu), "") if mu else ""
    raw = {
        "prompt_tokens": usage.get("input_tokens", 0),
        "completion_tokens": usage.get("output_tokens", 0),
        "cache_read_tokens": usage.get("cache_read_input_tokens", 0),
        "cache_write_tokens": usage.get("cache_creation_input_tokens", 0),
        "cost": float(event.get("total_cost_usd", 0.0) or 0.0),
    }
    ts = event.get("timestamp") or ""
    return {"model": model, "rawUsage": raw, "timestamp": ts}


def _read_claude_jsonl_usage(s: Session) -> list[dict] | None:
    """兜底：读 claude JSONL 最后一个 assistant 事件的 usage（token 准确，cost=0）。"""
    from . import sessions as claude_sessions
    try:
        return claude_sessions.get_raw_usage(s.cli_session_id, s.workdir or None)
    except Exception:
        return None
