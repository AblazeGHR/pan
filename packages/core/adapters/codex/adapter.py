"""OpenAI Codex CLI (codex) adapter.

Pan starts a stable ``wrapper.py`` entry point which bridges the native
long-lived ``codex app-server --stdio`` protocol. The worker keeps its
existing stream contract while thread/turn state, deltas, tool items and
native interruption stay alive inside one app-server process. The old exec
loop remains available as a fallback for manual wrapper invocations.

关键坑处理（详见任务 brief 实战教训）：
1. **npm .CMD shim 中文乱码**：Windows 下 ``shutil.which("codex")`` 返回 ``codex.CMD``，
   经 cmd.exe 二次切分会把中文参数乱码化。这里把 shim 解析为真实入口
   ``[node, <npm_global>/node_modules/@openai/codex/bin/codex.js]``，参数经
   CreateProcess 原样传给 node，不再经过 cmd.exe 二次解析（与 cbc/_resolve_cbc_argv、
   opencode/_resolve_opencode_exe_from_shim 同思路）。
2. **原生长驻协议**：默认使用 ``codex app-server --stdio``，Pan 只负责把自己的简化
   JSONL worker 协议映射到 Codex 的 thread/turn JSON-RPC。旧的 ``codex exec`` 路径仍保留
   在 wrapper 中，便于手工兼容调用。
3. **MCP 注入**：codex 无 ``--mcp-config``；MCP server 来自 ``~/.codex/config.toml`` 的
   ``[mcp_servers]`` 段。用 ``-c 'mcp_servers.<name>...'`` 内联覆盖（实测 ``codex mcp
   list -c '...'`` 生效），session 级、零文件污染、不触碰 auth.json（API key 不泄露）。
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
import time
from pathlib import Path
from ...session import Session
from ..mcp import build_mcp_servers

_log = logging.getLogger(__name__)

# 默认模型：对齐 ~/.codex/config.toml 的 model（本地 siliconflow 代理）。
# 留作兜底；default_model 优先读 config.json，其次读 ~/.codex/config.toml。
_DEFAULT_MODEL = "deepseek-ai/DeepSeek-V4-Flash"


class CodexAdapter:
    """OpenAI Codex CLI 适配器。实现 CliAdapter 协议，实例无状态，可被多 worker 共享。"""

    name = "codex"

    # 执行模式：codex 用原生 app-server 长驻，worker 只走 stream；wrapper 负责协议桥接，
    # 故不暴露 oneshot。
    execution_modes = ["stream"]

    # 元信息

    @property
    def _codex_config(self) -> dict:
        from ...config import load_config
        return load_config().get("codex", {})

    @property
    def default_model(self) -> str:
        """Return a model that is also present in the current model source.

        A stale Pan ``config.json`` model must not win over Codex's refreshed
        native catalog: otherwise the UI offers one list while a new session
        silently starts with a model that cannot be selected. Keep the
        configured model when it is valid, then prefer Codex's own config.toml
        selection, and finally use the first visible model.
        """
        cfg_model = self._codex_config.get("model")
        cfg_model = str(cfg_model).strip() if cfg_model else ""
        models = self.supported_models
        if cfg_model and cfg_model in models:
            return cfg_model

        toml_model = _read_codex_config_toml_model()
        if toml_model and toml_model in models:
            return toml_model

        # supported_models always has a fallback entry, but retain the final
        # constant for defensive callers during a partially initialized home.
        return models[0] if models else _DEFAULT_MODEL

    @property
    def default_permission_mode(self) -> str:
        return self._codex_config.get("permission_mode", "bypass")

    # 模型列表 TTL 缓存（对齐 opencode 思路，5 分钟超时自动重拉）
    _MODELS_TTL_SEC = 300.0
    _cached_models: list[str] | None = None  # class-level cache
    _models_cached_at: float = 0.0

    @property
    def supported_models(self) -> list[str]:
        """模型列表（优先级）：白名单 > Codex 动态缓存 > catalog > 内置默认。

        - config.codex.models：显式白名单（填=限制可选项）；
        - ~/.codex/models_cache.json（Codex CLI 自己维护的动态模型目录）：
          解析公开模型的 ``slug``；
        - ~/.codex/config.toml 的 model_catalog_json（cc-switch 生成的模型目录文件）：
          解析其 ``models[].slug``（回退 ``display_name``）得到完整模型列表；
        - 兜底：default_model 单元素列表。
        结果按 TTL 缓存 5 分钟（超时自动重拉），避免每次请求都读文件/解析。
        """
        now = time.monotonic()
        if (CodexAdapter._cached_models is not None
                and now - CodexAdapter._models_cached_at < CodexAdapter._MODELS_TTL_SEC):
            return CodexAdapter._cached_models
        models = self._codex_config.get("models")
        if isinstance(models, list) and len(models) > 0:
            result = [str(m) for m in models]
        else:
            dynamic_models = _parse_models_from_models_cache()
            if dynamic_models:
                result = dynamic_models
            else:
                catalog_models = _parse_models_from_catalog()
                # Do not call self.default_model here: it consults
                # supported_models and would recurse when no catalog exists.
                fallback = (self._codex_config.get("model")
                            or _read_codex_config_toml_model()
                            or _DEFAULT_MODEL)
                result = catalog_models or [str(fallback)]
        CodexAdapter._cached_models = result
        CodexAdapter._models_cached_at = now
        return result

    supports_resume = True
    supports_fork = True
    # The wrapper translates worker's --system-prompt into Codex's native
    # developer_instructions config override on the first fresh turn.
    supports_spawn_system_prompt = True
    # The native bridge accepts an out-of-band control JSON message, allowing
    # Pan's interrupt endpoint to stop a turn without killing the whole native
    # thread/app-server process.
    supports_native_interrupt = True
    # codex reasoning effort（config: model_reasoning_effort）。空表示不覆盖。
    # models_cache.json may add xhigh/max/ultra; keep the fallback useful even
    # before Codex has populated its cache.
    _BASE_EFFORT_VALUES = ["", "low", "medium", "high", "xhigh", "max", "ultra"]

    @property
    def effort_values(self) -> list[str]:
        values = list(self._BASE_EFFORT_VALUES)
        for effort in _parse_effort_values_from_models_cache():
            if effort not in values:
                values.append(effort)
        return values

    @property
    def model_efforts(self) -> dict[str, list[str]]:
        """Per-model reasoning levels from Codex's native model catalog.

        An empty mapping means that no native catalog is available and callers
        should use ``effort_values`` as the compatibility fallback.
        """
        result: dict[str, list[str]] = {}
        for model in _read_models_cache():
            if model.get("visibility") not in (None, "list"):
                continue
            ident = str(model.get("slug") or model.get("display_name") or "").strip()
            if not ident:
                continue
            levels: list[str] = []
            for level in model.get("supported_reasoning_levels") or []:
                effort = level.get("effort") if isinstance(level, dict) else level
                effort = str(effort or "").strip()
                if effort and effort not in levels:
                    levels.append(effort)
            if levels:
                result[ident] = levels
        return result

    # Settings are encoded into --codex-extra-args by build_spawn_args. The
    # wrapper itself intentionally does not accept CBC-style runtime flags.
    settings_via_session = True
    permission_modes = [
        {"value": "", "label": "default (config)"},
        {"value": "read-only", "label": "read-only (auto)"},
        {"value": "workspace-write", "label": "workspace-write (auto)"},
        {"value": "bypass", "label": "bypass (--dangerously-bypass-approvals-and-sandbox)"},
        {"value": "approve", "label": "approve-for-me (workspace-write + auto-approve)"},
    ]
    # 前端展示的设置项
    supported_settings = ["model", "permissionMode", "effort"]

    # ── 路径解析（避开 .CMD shim 中文乱码） ──

    @property
    def _codex_node(self) -> str:
        return _resolve_codex_node()

    @property
    def _codex_js(self) -> str:
        return _resolve_codex_js()

    @property
    def _wrapper_path(self) -> str:
        return str(Path(__file__).resolve().parent / "wrapper.py")

    # ── 进程启动 ──

    def base_args(self) -> list[str]:
        return [sys.executable, "-u", self._wrapper_path,
                "--app-server",
                "--codex-path", self._codex_js,
                "--node-path", self._codex_node]

    def model_args(self, s: Session) -> list[str]:
        # 用 `-c model="..."` 内联覆盖（codex 官方示例用法）而非 --model flag：
        # 它是 -c 类覆盖，wrapper 在 resume 时也透传（--model 等一次性 flag 会被
        # resume 过滤掉，而 thread 已记住原模型，不重传也无碍，但 -c 更通用）。
        return ["-c", _c_override("model", s.model or self.default_model)]

    def thinking_args(self, s: Session) -> list[str]:
        # codex 无独立 --thinking CLI 参数；reasoning 由 model_reasoning_effort 表达。
        return []

    def effort_args(self, s: Session) -> list[str]:
        """reasoning effort 经 ``-c model_reasoning_effort="<x>"`` 内联覆盖。"""
        effort = s.adapter_config.get("effort", "")
        if effort:
            return ["-c", _c_override("model_reasoning_effort", effort)]
        return []

    def permission_mode_args(self, s: Session) -> list[str]:
        """权限模式 → codex exec 参数（fresh 与 resume 均生效）。

        - ""（default）：沿用 codex/config.toml 默认（无参数）；
        - "bypass"：--dangerously-bypass-approvals-and-sandbox（headless 自动化默认；
          实测 `codex exec resume` 同样接受该 flag）；
        - "read-only"：-c 覆盖 sandbox_mode="read-only" + approval_policy="never"；
        - "workspace-write"：-c 覆盖 sandbox_mode="workspace-write" + approval_policy="never"；
        - "approve"：-c 覆盖 sandbox_mode="workspace-write" + approval_policy="never"
          （等效 --approve-for-me 的 workspace-write sandbox + 自动审批）。**不用**
          --approve-for-me flag，因为实测 `codex exec resume` 不接受该 flag（报
          unexpected argument）；-c 覆盖 fresh/resume 通用——这是任务 B 的关键：
          用户中途改 permission_mode 后，后续 resume 显式传当前模式的 -c 覆盖，
          而非依赖 thread 持久化的旧配置。
        """
        mode = s.permission_mode or self.default_permission_mode
        if mode == "bypass":
            return ["--dangerously-bypass-approvals-and-sandbox"]
        if mode in ("read-only", "workspace-write", "approve"):
            sandbox_mode = "read-only" if mode == "read-only" else "workspace-write"
            return ["-c", _c_override("sandbox_mode", sandbox_mode),
                    "-c", _c_override("approval_policy", "never")]
        return []

    def resume_args(self, s: Session) -> list[str]:
        """续接既有 thread：把已捕获的 cli_session_id 作为 wrapper 初始 thread_id 传入，
        wrapper 首条消息即 ``codex exec resume <id>``（跨 worker respawn 保持连续性）。
        """
        if s.cli_session_id:
            return ["--thread-id", s.cli_session_id]
        return []

    def fork_args(self, s: Session | None = None) -> list[str]:
        """Fork 由 server 经 SessionsProvider.fork_session（DB 行复制）完成，
        不经过 worker 的 _branch_worker（其要求非空 extra_args）。此处返回 []。
        """
        return []

    def build_spawn_args(self, s: Session,
                         extra_args: list[str] | None = None) -> list[str]:
        args = self.base_args()
        # 所有 codex 级 option flag（model / permission / effort / mcp）汇总成一份
        # JSON 列表，经 wrapper 的 --codex-extra-args 透传给每次 codex exec 调用。
        codex_opts: list[str] = []
        codex_opts.extend(self.model_args(s))
        codex_opts.extend(self.permission_mode_args(s))
        codex_opts.extend(self.effort_args(s))
        codex_opts.extend(self.mcp_args(s))
        if codex_opts:
            args.extend(["--codex-extra-args", json.dumps(codex_opts, ensure_ascii=False)])
        args.extend(self.resume_args(s))
        if extra_args:
            args.extend(extra_args)
        return args

    def mcp_args(self, s: Session) -> list[str]:
        """MCP 注入：构建 ``-c 'mcp_servers.<name>...'`` 内联覆盖列表（codex 级 flag）。

        这些 flag 由 build_spawn_args 汇总进 --codex-extra-args，wrapper 透传给每次
        ``codex exec``。session 级、零文件污染、不触碰 auth.json（API key 不泄露）。

        codex mcp_servers TOML 段格式（实测 ``codex mcp add`` 写入）：
          [mcp_servers.<name>]
          command = "node"
          args = ["...", "..."]
          [mcp_servers.<name>.env]
          KEY = "val"
        URL server 用 url + transport。对应用 ``-c`` 内联表达。
        """
        servers = build_mcp_servers(s)
        if not servers:
            return []
        opts: list[str] = []
        # 透传 PAN_API_URL（若存在）到各 server env，确保 pan/pan-qq server 指向正确的
        # Pan 服务（对齐 opencode._to_opencode_mcp_entry 的 PAN_API_URL 处理）。
        pan_api_url = os.environ.get("PAN_API_URL")
        for name, entry in servers.items():
            env = dict(entry.get("env") or {})
            if pan_api_url and "PAN_API_URL" not in env:
                env["PAN_API_URL"] = pan_api_url
            if entry.get("url"):
                opts.append("-c")
                opts.append(_c_override(f"mcp_servers.{name}.url", entry["url"]))
                transport = entry.get("transport") or "http"
                opts.append("-c")
                opts.append(_c_override(f"mcp_servers.{name}.transport", transport))
            else:
                cmd = entry.get("command")
                args = list(entry.get("args") or [])
                if cmd:
                    # codex 的 mcp_servers.<name>.args 是「不含 command」的参数列表
                    # （实测 `codex mcp add` 写入：command="<exe>", args=["-m", ...]）。
                    # 若把 command 也塞进 args 首位，codex 会执行 `exe exe -m ...`，
                    # MCP server 以 SyntaxError 退出 → handshake "connection closed"。
                    opts.append("-c")
                    opts.append(_c_override(f"mcp_servers.{name}.command", cmd))
                    if args:
                        opts.append("-c")
                        opts.append(_c_override(f"mcp_servers.{name}.args", list(args)))
            # MCP server 的 cwd（manifest 声明，如 pan → D:\project\pan-test）必须随
            # `-c mcp_servers.<name>.cwd=...` 透传；否则 codex 从 session workdir 启动
            # server，`-m packages.mcp.server` 会 ModuleNotFoundError → 握手即断开。
            cwd = entry.get("cwd")
            if cwd:
                opts.append("-c")
                opts.append(_c_override(f"mcp_servers.{name}.cwd", cwd))
            for k, v in env.items():
                opts.append("-c")
                opts.append(_c_override(f"mcp_servers.{name}.env.{k}", v))
        return opts

    def oneshot_args(self, s: Session, text: str) -> list[str]:
        # codex 的 worker 驱动方式只有 stream（wrapper 长驻），never 进入 oneshot
        # 路径（execution_modes == ["stream"]），故返回 []（防御兜底）。
        return []

    # ── stdin 消息编码 ──

    def encode_user_message(self, text: str) -> bytes:
        return json.dumps({"text": text}).encode("utf-8")

    def encode_control_message(self, control: dict) -> bytes:
        """Encode a bridge control message for interrupt, steer, or interactive requests."""
        return json.dumps(control, ensure_ascii=True).encode("utf-8")

    # ── stdout 事件解析 ──

    def parse_event(self, line: str) -> dict | None:
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return None

    def event_type(self, event: dict) -> str:
        return event.get("type") or ""

    def is_init_event(self, event: dict) -> bool:
        # thread.started 携带 thread_id，worker 仅在首次写入 cli_session_id，幂等安全
        return event.get("type") == "thread.started"

    def extract_session_id(self, event: dict) -> str | None:
        return event.get("thread_id")

    def extract_model(self, event: dict) -> str | None:
        # codex JSONL 事件不含 model 字段；s.model 在会话创建时已知，
        # enrich_after_result 还会尝试从存储回填。此处返回 None。
        return None

    def is_assistant_event(self, event: dict) -> bool:
        # app-server bridge emits canonical display events for completed
        # assistant/reasoning/tool items.  Incremental ``content.part`` events
        # are intentionally excluded from history; they are UI-only deltas.
        return (event.get("type") in ("item.completed", "assistant", "thinking")
                and not event.get("delta"))

    def extract_assistant_blocks(self, event: dict) -> list[dict]:
        if event.get("type") == "assistant":
            content = ((event.get("message") or {}).get("content") or [])
            blocks: list[dict] = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text" and part.get("text"):
                    blocks.append({"role": "assistant", "content": part["text"]})
                elif part.get("type") in ("thinking", "think"):
                    text = part.get("thinking") or part.get("think") or ""
                    if text:
                        blocks.append({"role": "thinking", "content": text})
                elif part.get("type") == "tool_use":
                    name = part.get("name") or "tool"
                    args = part.get("input") or {}
                    inp = json.dumps(args, ensure_ascii=False) if isinstance(args, (dict, list)) else str(args)
                    blocks.append({"role": "tool", "content": f"{name}({inp})"})
            return blocks
        if event.get("type") == "thinking":
            text = event.get("content") or ""
            return [{"role": "thinking", "content": text}] if text else []

        item = event.get("item", {}) or {}
        # live stdout 用 snake_case（agent_message），持久化 thread_items 用
        # camelCase（agentMessage）。统一去掉下划线归一后匹配。
        itype = (item.get("type") or "").replace("_", "").lower()
        blocks: list[dict] = []

        if itype == "agentmessage":
            text = item.get("text", "")
            if text:
                blocks.append({"role": "assistant", "content": text})
        elif itype == "reasoning":
            # reasoning 项用 text 或 summary[0]（持久化为 summary 数组）
            text = item.get("text") or ""
            if not text:
                summary = item.get("summary") or []
                if summary:
                    text = summary[0] if isinstance(summary[0], str) else str(summary[0])
            if text:
                blocks.append({"role": "thinking", "content": text})
        elif itype == "commandexecution":
            cmd = item.get("command", "")
            out = item.get("aggregated_output", "")
            content = cmd
            if out:
                content += "\n→ " + out
            blocks.append({"role": "tool", "content": content})
        elif itype == "functioncall":
            name = item.get("name") or item.get("pluginId") or "tool"
            args = item.get("arguments") or item.get("parameters") or {}
            inp = json.dumps(args, ensure_ascii=False) if isinstance(args, (dict, list)) else str(args or "")
            out = item.get("output") or item.get("result") or ""
            content = f"{name}({inp})"
            if out:
                content += "\n→ " + out
            blocks.append({"role": "tool", "content": content})
        elif itype == "usermessage":
            parts = item.get("content") or []
            text = "".join(
                b.get("text", "") for b in parts
                if isinstance(b, dict) and b.get("type") == "text"
            )
            if text:
                blocks.append({"role": "user", "content": text})

        return blocks

    def is_result_event(self, event: dict) -> bool:
        # 由 wrapper 合成的 result 事件（原生 codex 无此事件类型）
        return event.get("type") == "result"

    def is_result_error(self, event: dict) -> bool:
        return event.get("is_error", False)

    def extract_result_text(self, event: dict) -> str | None:
        return event.get("result")

    # ── takeover ──

    def takeover_command(self, s: Session) -> list[str]:
        if not s.cli_session_id:
            return []
        # codex 接管 = 交互式 TUI 续接 thread：``codex resume <thread_id>``
        return [*self._resolve_codex_argv(), "resume", s.cli_session_id]

    # ── enrich ──

    def enrich_after_result(self, s: Session) -> list[dict] | None:
        """从 codex 原生存储读取本轮新增 usage（增量游标，避免重复累加）。

        用法数据存于 rollout JSONL（``event_msg`` payload.type=token_count 的
        last_token_usage / total_token_usage）。返回 session 级聚合 usage 的增量
        （与 opencode/kimi 同构：{"model","rawUsage","timestamp"}），或 None。

        同时用存储中的 model 回填 s.model（JSONL 事件无 model 字段）。
        """
        if not s.cli_session_id:
            return None
        try:
            from . import sessions as codex_sessions
            entries = codex_sessions.get_raw_usage(s.cli_session_id, s.workdir or None)
            if not entries:
                return None
            cur = entries[0]
            cur_usage = cur.get("rawUsage", {})
            cur_model = cur.get("model", "")
            cur_ts = cur.get("timestamp", "")

            if not s.model and cur_model:
                s.model = cur_model

            prev = s.adapter_config.get("codex_prev_usage") or {}
            delta = {
                "prompt_tokens": max(0, int(cur_usage.get("input_tokens", 0) - prev.get("input_tokens", 0))),
                "completion_tokens": max(0, int(cur_usage.get("output_tokens", 0) - prev.get("output_tokens", 0))),
                "reasoning_tokens": max(0, int(cur_usage.get("reasoning_output_tokens", 0) - prev.get("reasoning_output_tokens", 0))),
                "cache_read_tokens": max(0, int(cur_usage.get("cached_input_tokens", 0) - prev.get("cached_input_tokens", 0))),
                "cache_write_tokens": max(0, int(cur_usage.get("cache_write_input_tokens", 0) - prev.get("cache_write_input_tokens", 0))),
                "total_tokens": max(0, int(cur_usage.get("total_tokens", 0) - prev.get("total_tokens", 0))),
            }
            s.set_adapter_field("codex_prev_usage", {
                "input_tokens": cur_usage.get("input_tokens", 0),
                "output_tokens": cur_usage.get("output_tokens", 0),
                "reasoning_output_tokens": cur_usage.get("reasoning_output_tokens", 0),
                "cached_input_tokens": cur_usage.get("cached_input_tokens", 0),
                "cache_write_input_tokens": cur_usage.get("cache_write_input_tokens", 0),
                "total_tokens": cur_usage.get("total_tokens", 0),
            })

            if not any(delta.values()):
                return None
            return [{
                "model": cur_model,
                "rawUsage": delta,
                "timestamp": cur_ts,
            }]
        except Exception:
            _log.debug("codex enrich_after_result failed", exc_info=True)
            return None

    # ── 内部：解析真实入口 ──

    def _resolve_codex_argv(self) -> list[str]:
        """返回 codex 真实启动前缀 ``[node, codex_js]``（避开 .CMD shim）。"""
        return [self._codex_node, self._codex_js]


def _c_override(key: str, value) -> str:
    """构造 ``-c`` 内联覆盖字符串 ``<key>=<toml-literal>``。

    value 经 json.dumps 生成 TOML 安全的字面量（字符串/数组/数字/bool 均兼容）。
    """
    return f"{key}={json.dumps(value, ensure_ascii=False)}"


def _resolve_codex_node() -> str:
    """解析 node 可执行文件。

    优先级：``PAN_CODEX_NODE`` 环境变量 > shim 同目录 ``node.exe`` > ``node``。
    """
    env = os.environ.get("PAN_CODEX_NODE")
    if env:
        return env
    which = shutil.which("codex")
    if which:
        shim_dir = os.path.dirname(os.path.abspath(which))
        node_exe = os.path.join(shim_dir, "node.exe")
        if os.path.exists(node_exe):
            return node_exe
    # node_global 目录通常不内嵌 node.exe（npm 用 PATH 上的 node），回退 which
    node = shutil.which("node")
    return node or "node"


def _resolve_codex_js() -> str:
    """解析 codex 真实入口 js 文件，避开 npm .CMD shim 的中文乱码。

    优先级：``PAN_CODEX_PATH`` 环境变量（指向 codex.js 或 shim）>
    解析 ``shutil.which("codex")`` 的 .CMD/.bat shim 同目录
    ``node_modules/@openai/codex/bin/codex.js`` > 回退 ``shutil.which("codex")``。
    """
    env = os.environ.get("PAN_CODEX_PATH")
    if env:
        if env.lower().endswith((".cmd", ".bat")):
            resolved = _codex_js_from_shim(env)
            if resolved:
                return resolved
        return env
    which = shutil.which("codex")
    if not which:
        return "codex"
    if which.lower().endswith((".cmd", ".bat")):
        resolved = _codex_js_from_shim(which)
        if resolved:
            return resolved
    return which


def _codex_js_from_shim(shim_path: str) -> str | None:
    """由 .CMD/.bat shim 解析出 codex 真实 js 入口。

    npm shim 布局：``<dir>/codex.CMD`` 与
    ``<dir>/node_modules/@openai/codex/bin/codex.js`` 相邻。命中返回真实 js 路径，
    否则返回 None（调用方回退到 shim 本身）。
    """
    if not shim_path.lower().endswith((".cmd", ".bat")):
        return None
    shim_dir = os.path.dirname(os.path.abspath(shim_path))
    candidate = os.path.join(
        shim_dir, "node_modules", "@openai", "codex", "bin", "codex.js"
    )
    if os.path.isfile(candidate):
        return candidate
    return None


def _codex_home() -> Path:
    """Codex 用户目录（尊重 CODEX_HOME，默认 ``~/.codex``）。"""
    configured = os.environ.get("CODEX_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".codex"


def _read_models_cache() -> list[dict]:
    """读取 Codex CLI 的动态模型缓存；缺失或损坏时返回空列表。"""
    path = _codex_home() / "models_cache.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return []
    models = data.get("models") if isinstance(data, dict) else None
    return [m for m in models if isinstance(m, dict)] if isinstance(models, list) else []


def _parse_models_from_models_cache() -> list[str]:
    """解析 Codex CLI 动态目录中的公开模型，去重并保持原目录顺序。"""
    out: list[str] = []
    seen: set[str] = set()
    for model in _read_models_cache():
        if model.get("visibility") not in (None, "list"):
            continue
        ident = str(model.get("slug") or model.get("display_name") or "").strip()
        if ident and ident not in seen:
            seen.add(ident)
            out.append(ident)
    return out


def _parse_effort_values_from_models_cache() -> list[str]:
    """返回动态目录声明过的 reasoning effort，去重并保持模型顺序。"""
    out: list[str] = []
    seen: set[str] = set()
    for model in _read_models_cache():
        if model.get("visibility") not in (None, "list"):
            continue
        levels = model.get("supported_reasoning_levels") or []
        for level in levels:
            effort = level.get("effort") if isinstance(level, dict) else level
            effort = str(effort or "").strip()
            if effort and effort not in seen:
                seen.add(effort)
                out.append(effort)
    return out


def _config_toml_text() -> str:
    """读取 ~/.codex/config.toml 全文（缺失/读取失败返回 ""）。"""
    toml_path = _codex_home() / "config.toml"
    if not toml_path.is_file():
        return ""
    try:
        return toml_path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _read_codex_config_toml_model() -> str:
    """从 ~/.codex/config.toml 读取 model 字段（不填=自动识别的兜底来源）。"""
    m = re.search(r'^\s*model\s*=\s*"([^"]+)"', _config_toml_text(), re.MULTILINE)
    if m:
        return m.group(1)
    return ""


def _model_catalog_path() -> Path | None:
    """解析 config.toml 的 model_catalog_json 路径（cc-switch 模型目录文件）。

    相对路径以 ~/.codex/（CODEX_HOME）为基准；文件缺失返回 None。
    """
    m = re.search(r'^\s*model_catalog_json\s*=\s*"([^"]+)"', _config_toml_text(), re.MULTILINE)
    if not m:
        return None
    raw = m.group(1)
    p = Path(raw)
    if not p.is_absolute():
        p = _codex_home() / p
    return p if p.is_file() else None


def _parse_models_from_catalog() -> list[str]:
    """从 cc-switch model_catalog_json 解析可用模型列表（容错）。

    文件结构：``{"models": [{"slug": "provider/model", "display_name": ...}, ...]}``
    （实测 2026-08-27，slug 与 display_name 均为完整模型标识）。逐项取 slug
    （回退 display_name），去重保序；缺失/解析失败返回 []（调用方回退默认）。
    """
    p = _model_catalog_path()
    if p is None:
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for m in models:
        if not isinstance(m, dict):
            continue
        ident = str(m.get("slug") or m.get("display_name") or "").strip()
        if ident and ident not in seen:
            seen.add(ident)
            out.append(ident)
    return out
