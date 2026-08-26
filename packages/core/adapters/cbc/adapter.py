"""cbc (CodeBuddy CLI) 适配器。"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from ...session import SESSION_DIR, Session
from ..mcp import write_mcp_json

_log = logging.getLogger(__name__)

# Pan 内统一目录：MCP 配置收敛到 data/mcp-configs/<session_id>.mcp.json（立项 4.9），
# 不再写 workdir/.codebuddy/mcp.json（workdir 可能在 Pan 外，写外部目录污染且可能不可写）。
MCP_CONFIG_DIR = SESSION_DIR.parent / "mcp-configs"


def _parse_models_from_cbc_help(argv_prefix: list[str] | None = None) -> list[str]:
    """从 `cbc --help` 解析支持的模型列表（仅加载一次）。

    *argv_prefix* is the resolved launch prefix (e.g. ``["node", <entry>]``
    from CbcAdapter._resolve_cbc_argv). Passing a bare ``["cbc"]`` fails on
    Windows because the npm shim is a `.CMD` batch file, which CreateProcess
    cannot execute directly (FileNotFoundError) — the caller must pass the
    node-resolved argv.
    """
    try:
        cmd = (argv_prefix or ["cbc"]) + ["--help"]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except Exception:
        return []
    output = r.stdout or r.stderr or ""
    m = re.search(r"Currently supported:\s*\(([^)]+)\)", output)
    if not m:
        return []
    return [name.strip() for name in m.group(1).split(",") if name.strip()]


class CbcAdapter:
    """cbc (CodeBuddy CLI) 适配器。

    实现 CliAdapter 协议。实例无状态，可被多 worker 共享。
    默认值从 project-root/config.json 读取（无配置文件则使用内置默认值）。
    """

    name = "cbc"

    # 执行模式（adapter-architecture P1 建议 4 / adapter-p1-oneshot.md）：
    # cbc 同时支持 stream 长驻（原生 stdin/stdout）与 oneshot 逐任务短进程
    # （prompt 作末参，配合 --mcp-config）。故声明两种。
    execution_modes = ["stream", "oneshot"]

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

    _BUILTIN_MODELS = [
        "glm-5.2", "glm-5.1", "glm-5.0", "glm-5.0-turbo", "glm-5v-turbo", "glm-4.7",
        "minimax-m3-pay", "minimax-m2.7",
        "kimi-k2.7", "kimi-k2.6",
        "hy3",
        "deepseek-v4-pro", "deepseek-v4-flash", "deepseek-v3-2-volc",
        "custom-local:deepseek-v4-pro",
    ]

    _cached_models: list[str] | None = None  # class-level cache

    @property
    def supported_models(self) -> list[str]:
        """模型列表：config.json > cbc --help 解析 > 硬编码默认值（缓存）。"""
        if CbcAdapter._cached_models is not None:
            return CbcAdapter._cached_models
        # 1. config.json 显式配置
        models = self._cbc_config.get("models")
        if isinstance(models, list) and len(models) > 0:
            CbcAdapter._cached_models = [str(m) for m in models]
            return CbcAdapter._cached_models
        # 2. 从 cbc --help 自动获取（用 node 解析的 argv，避免 .CMD shim 启动失败）
        cli_models = _parse_models_from_cbc_help(self._resolve_cbc_argv())
        if cli_models:
            CbcAdapter._cached_models = cli_models
            return CbcAdapter._cached_models
        # 3. 硬编码默认值
        CbcAdapter._cached_models = self._BUILTIN_MODELS
        return CbcAdapter._cached_models
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

    def _resolve_cbc_path(self) -> str:
        """确定 cbc 可执行文件路径：配置 > 环境变量 > PATH 查找 > 回退名。"""
        from ...config import load_config
        config = load_config()
        cbc_path = config.get("cbc", {}).get("path")
        if cbc_path:
            return cbc_path
        env_path = os.environ.get("PAN_CBC_PATH")
        if env_path:
            return env_path
        which_path = shutil.which("cbc")
        return which_path or "cbc"

    def _resolve_cbc_argv(self) -> list[str]:
        """Return the full argv prefix to launch cbc.

        On Windows, `shutil.which` resolves npm shims to a `.CMD` batch file.
        Passing a `.CMD` straight into asyncio.create_subprocess_exec goes
        through cmd.exe, which mangles long/multiline/unicode args (e.g. a
        700-char system prompt with quotes) — cbc then exits in ~30ms.
        Resolve the shim to `node <entry.js>` so args pass through intact.
        """
        path = self._resolve_cbc_path()
        if path.lower().endswith((".cmd", ".bat")):
            shim_dir = os.path.dirname(os.path.abspath(path))
            node_exe = os.path.join(shim_dir, "node.exe")
            if not os.path.exists(node_exe):
                node_exe = "node"
            # npm shim layout: <dir>/node_modules/<pkg>/bin/<name>
            candidates = [
                os.path.join(shim_dir, "node_modules", p, "bin", name)
                for p in ("@tencent-ai/codebuddy-code", "@tencent-ai/codebuddy")
                for name in ("codebuddy", "codebuddy.js")
            ]
            # fallback: glob the whole node_modules for a codebuddy bin entry
            import glob as _glob
            if not any(os.path.exists(c) for c in candidates):
                hits = _glob.glob(os.path.join(shim_dir, "node_modules", "*", "*", "bin", "codebuddy*"))
                candidates += hits
            for c in candidates:
                if os.path.exists(c):
                    return [node_exe, c]
            return [path]
        return [path]

    # ── 进程启动 ──

    def base_args(self) -> list[str]:
        """Stream mode: long-running with stdin/stdout stream-json."""
        return self._resolve_cbc_argv() + ["-p", "--output-format", "stream-json",
                "--input-format", "stream-json", "-y"]

    def base_args_stream(self) -> list[str]:
        """One-shot MCP mode: prompt as CLI arg, no stdin streaming.
        
        --input-format stream-json is incompatible with --mcp-config,
        so we omit it here. cbc processes the prompt as a one-shot.
        """
        return self._resolve_cbc_argv() + ["-p", "--output-format", "stream-json", "-y"]

    def model_args(self, s: Session) -> list[str]:
        return ["--model", s.model or self.default_model]

    def thinking_args(self, s: Session) -> list[str]:
        """Build --settings JSON for alwaysThinkingEnabled."""
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
        if s.cli_session_id:
            return ["--resume", s.cli_session_id]
        return []

    def fork_args(self, s: Session | None = None) -> list[str]:
        """返回 fork 参数。若 session 没有 cli_session_id，需要显式 --resume。"""
        if s and not s.cli_session_id:
            return ["--resume", "", "--fork-session"]
        return ["--fork-session"]

    def build_spawn_args(self, s: Session,
                          extra_args: list[str] | None = None) -> list[str]:
        args = self.base_args()
        # No -d: cbc derives the project dir from the process CWD (set by
        # create_subprocess_exec cwd=s.workdir), which also fixes the JSONL
        # storage location for --resume. -d only mattered for the old
        # enableAllProjectMcpServers discovery — --mcp-config replaced it
        # (tested 2026-08-16: -d is redundant for connect/resume).
        args.extend(self.model_args(s))
        args.extend(self.permission_mode_args(s))
        args.extend(self.effort_args(s))
        args.extend(self.thinking_args(s))
        args.extend(self.resume_args(s))
        args.extend(self.mcp_args(s))
        if extra_args:
            args.extend(extra_args)
        return args

    def oneshot_args(self, s: Session, text: str) -> list[str]:
        """One-shot 执行 argv（替代 worker 原 _consumer_mcp 的 cbc 特定拼装）。

        用于 worker 通用 ``_consumer_oneshot``：逐任务 spawn 一个 cbc ``-p``
        短进程，prompt 作末参，``--mcp-config`` 才能生效（与
        ``--input-format stream-json`` 互斥）。逐元素对齐旧 ``_consumer_mcp``
        拼装（见 docs/design/adapter-p1-oneshot.md §1）。

        注意：跳过 ``thinking_args``（``--settings`` 会破坏 MCP init，旧路径
        同样跳过）；``--system-prompt`` 仅首条（``cli_session_id`` 捕获前）注入，
        之后靠 ``--resume`` 延续上下文。
        """
        args = list(self.base_args_stream())   # 无 --input-format stream-json
        args.extend(self.model_args(s))
        args.extend(self.permission_mode_args(s))
        args.extend(self.effort_args(s))
        if s.cli_session_id and self.supports_resume:
            args.extend(self.resume_args(s))
        args.extend(self.mcp_args(s))          # 写入 data/mcp-configs/<id>.mcp.json 并返回 --mcp-config
        if s.system_prompt and not s.cli_session_id:
            args.extend(["--system-prompt", s.system_prompt])
        args.append(text)
        return args

    def mcp_args(self, s: Session) -> list[str]:
        """Write data/mcp-configs/<session_id>.mcp.json and return --mcp-config arg.

        The ONLY thing that makes MCP servers connect is passing --mcp-config
        explicitly (tested 2026-08-16, cbc 2.136.0):
        - `-d` does NOT auto-discover .codebuddy/mcp.json — MCP stays unconnected.
        - A project-level `<workdir>/.mcp.json` is discovered as a project-scope
          MCP server. Without -d that registration blocks --mcp-config (pan
          shows "Needs approval"/"Failed to connect"), so we no longer write it.
        With --mcp-config, tools load as directly connected (not deferred).

        Config lives in Pan's own data dir (data/mcp-configs/<session_id>.mcp.json,
        立项 4.9) — never in the workdir, which may sit outside Pan where writing
        .codebuddy/ would pollute external dirs or be unwritable.

        For the "pan" server, the MA session identity is injected into its env
        (PAN_AGENT_SESSION_ID / PAN_AGENT_SESSION_TITLE) so the MCP server's
        worker_send tool can tag agent-originated messages (立项 4.8).
        描述符构造与注入由 adapters/mcp.py 共享 helper 收敛（P0-1）。
        """
        servers = s.adapter_config.get("mcp_servers")
        if not servers:
            return []

        mcp_json_path = MCP_CONFIG_DIR / f"{s.id}.mcp.json"
        # 描述符构造（含 pan/pan-qq 身份注入、type=stdio）由共享 helper 收敛
        # （adapter-architecture P0-1）；未配置/写失败时返回 None → 无 MCP flag。
        if write_mcp_json(mcp_json_path, s) is None:
            return []

        return ["--mcp-config", str(mcp_json_path)]

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
        """Build the cbc command for interactive takeover of a session.

        Uses the resolved node entry (not a .CMD shim, which PowerShell/cmd
        mangles). The terminal is opened with cwd=<workdir> (see
        _open_terminal), so cbc resolves its project dir from the process CWD —
        passing -d here actually *breaks* resume when CWD differs (JSONL lives
        under the CWD-derived project). Re-applies --system-prompt since
        --resume alone won't re-inject it.
        """
        if not s.cli_session_id:
            return []
        cmd = self._resolve_cbc_argv()
        cmd.append("--resume")
        cmd.append(s.cli_session_id)
        if s.system_prompt:
            cmd.extend(["--system-prompt", s.system_prompt])
        return cmd

    # ── enrich ──

    def enrich_after_result(self, s: Session) -> list[dict] | None:
        """从 JSONL 读取本轮对话新增的所有 raw_usage 条目。

        与旧版不同，不再只读尾部最新的 16KB，而是读取全文件，
        然后与 session 已累积的 request_count 比较，只返回新增条目。
        避免因 cbc 写入延迟或同一轮多次 API 调用导致的遗漏。

        返回 list[dict]：新增的 rawUsage 条目列表，或 None（无新数据/失败）。
        """
        if not s.cli_session_id:
            return None
        try:
            return _read_jsonl_new_entries(s)
        except Exception:
            _log.debug("enrich_after_result failed", exc_info=True)
            return None

    # ── enrich helpers ──

    @staticmethod
    def _find_project_dir(cli_session_id: str) -> tuple[Path | None, str | None]:
        """Find the cbc project directory containing the session JSONL file.

        Returns (fpath, project_dir_name) or (None, None).
        """
        base = Path(os.path.expanduser("~/.codebuddy/projects"))
        for child in base.iterdir():
            if not child.is_dir():
                continue
            fpath = child / f"{cli_session_id}.jsonl"
            if fpath.exists():
                return fpath, child.name
        return None, None


def _read_jsonl_new_entries(s: Session) -> list[dict] | None:
    """Get all NEW rawUsage entries since the last enrichment.

    1. Wait briefly for cbc to finish writing JSONL (mitigates race condition).
    2. Read ALL rawUsage entries from the JSONL file.
    3. Compare with session's accumulated request_count per model.
    4. Return only entries beyond what's already been accumulated.
    """
    from .sessions import get_raw_usage

    fpath, proj_dir_name = CbcAdapter._find_project_dir(s.cli_session_id)
    if not fpath or not proj_dir_name:
        return None

    # 短暂延迟，等待 cbc 完成 JSONL 写入（解决时序竞态）
    time.sleep(0.2)

    # 读取文件中所有 rawUsage 条目
    all_entries = get_raw_usage(s.cli_session_id, project_dir=proj_dir_name)
    if not all_entries:
        return None

    # 筛选新增条目：使用 per-model request_count 作为已累积标记
    acc = s.raw_usage or {}
    new_entries = []
    passed = {}  # per-model counter of entries already seen/passed

    for entry in all_entries:
        model = entry.get("model", "")
        acc_model = acc.get(model, {})
        acc_count = acc_model.get("request_count", 0)
        passed_count = passed.get(model, 0)

        if passed_count >= acc_count:
            # 此条目尚未累积
            new_entries.append(entry)
        passed[model] = passed_count + 1

    if new_entries:
        total_new_credit = sum(
            e.get("rawUsage", {}).get("credit", 0) for e in new_entries
        )
        _log.debug("enrich: %d new entries, credit delta=%.2f", len(new_entries), total_new_credit)
    return new_entries if new_entries else None
