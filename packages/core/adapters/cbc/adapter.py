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
from ...session import Session

_log = logging.getLogger(__name__)


def _parse_models_from_cbc_help() -> list[str]:
    """从 `cbc --help` 解析支持的模型列表（仅加载一次）。"""
    try:
        r = subprocess.run("cbc --help", capture_output=True, text=True, timeout=10, shell=True)
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
        # 2. 从 cbc --help 自动获取
        cli_models = _parse_models_from_cbc_help()
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

    # ── 进程启动 ──

    def base_args(self) -> list[str]:
        return [self._resolve_cbc_path(), "-p", "--output-format", "stream-json",
                "--input-format", "stream-json", "-y"]

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
        args.extend(self.model_args(s))
        args.extend(self.permission_mode_args(s))
        args.extend(self.effort_args(s))
        args.extend(self.thinking_args(s))
        args.extend(self.resume_args(s))
        args.extend(self.mcp_args(s))
        if extra_args:
            args.extend(extra_args)
        return args

    def mcp_args(self, s: Session) -> list[str]:
        """Write .mcp.json to workdir and pass --mcp-config with file path.

        --mcp-config with a file path works reliably, while
        enableAllProjectMcpServers requires cbc to have previously
        registered the directory as a known project.
        """
        servers = s.adapter_config.get("mcp_servers")
        if not servers:
            return []

        # Write .mcp.json to workdir
        workdir = s.workdir
        if workdir:
            mcp_servers: dict[str, dict] = {}
            for srv in servers:
                name = srv.get("name", "unnamed")
                entry: dict = {}
                if "command" in srv:
                    entry["command"] = srv["command"]
                if "args" in srv:
                    entry["args"] = srv["args"]
                if "cwd" in srv:
                    entry["cwd"] = srv["cwd"]
                if "env" in srv:
                    entry["env"] = srv["env"]
                mcp_servers[name] = entry

            mcp_json_path = os.path.join(workdir, ".mcp.json")
            os.makedirs(workdir, exist_ok=True)
            with open(mcp_json_path, "w", encoding="utf-8") as f:
                json.dump({"mcpServers": mcp_servers, "disabledMcpServers": []}, f, ensure_ascii=False, indent=2)

            return ["--mcp-config", mcp_json_path]

        return []

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
        if not s.cli_session_id:
            return []
        return ["cbc", "--resume", s.cli_session_id]

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
