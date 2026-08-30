"""Kimi Code CLI 适配器。

Kimi 的 `-p/--prompt` 模式是一次性进程，因此通过 wrapper.py 包装成一个长驻子进程，
由 wrapper 在内部循环调用 Kimi 并转发 stream-json 事件。
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from ...session import SESSION_DIR, Session
from ..mcp import build_mcp_servers, write_mcp_json

_log = logging.getLogger(__name__)

# Pan 统一管理 kimi 隔离 HOME：data/kimi-homes/<session_id>/（方案 C，见
# docs/design/kimi-mcp-solution.md）。每会话一个独立目录承载 config.toml + mcp.json，
# 经 wrapper 以 KIMI_CODE_HOME 注入 kimi 子进程——绕过 folder-trust 且零污染真实
# ~/.kimi-code（与 cbc 的 data/mcp-configs 同一哲学：adapter 配置收敛到 Pan data/）。
KIMI_HOME_ROOT = SESSION_DIR.parent / "kimi-homes"


def _parse_kimi_models_from_toml() -> list[str]:
    """从 ~/.kimi-code/config.toml 的 [models.\"...\"] 段解析可用模型名。"""
    toml_path = Path.home() / ".kimi-code" / "config.toml"
    if not toml_path.is_file():
        return []
    try:
        text = toml_path.read_text(encoding="utf-8")
    except Exception:
        return []
    models = []
    for m in re.finditer(r'\[models\."([^"]+)"\]', text):
        models.append(m.group(1))
    return models


class KimiAdapter:
    """Kimi Code CLI 适配器。

    实现 CliAdapter 协议。实例无状态，可被多 worker 共享。
    由于 Kimi 没有 stdin stream-json 长驻模式，实际 Worker 进程是 wrapper.py，
    wrapper 内部逐条调用 `kimi -p ... --output-format stream-json`。
    """

    name = "kimi"

    # worker spawn 路径支持 --system-prompt flag：wrapper 接受它并在首轮转为
    # kimi 原生 --agent-file（kimi CLI 无 --system-prompt 参数，实测
    # --agent-file 与 -p 组合生效、-S resume 后人设保留）。缺 False 的 adapter
    # 由 worker 退回首条消息注入（见 worker.py spawn 块）。
    supports_spawn_system_prompt = True

    # 执行模式（adapter-p1-oneshot.md）：kimi 用 wrapper 长驻，worker 只走
    # stream；wrapper 内部逐条 `kimi -p` 的一次性语义对 worker 透明，故不暴露
    # oneshot。oneshot_args 不会被调用，返回 [] 作为防御兜底。
    execution_modes = ["stream"]

    # 实际有效默认模型（~/.kimi-code/config.toml 的 default_model）。旧值
    # kimi-code/kimi-for-coding 已不存在（不在 kimi 可选模型内）。
    _DEFAULT_MODEL = "moonshot-cn/kimi-k2.6"
    _DEFAULT_PERMISSION_MODE = ""
    _DEFAULT_ALWAYS_THINKING_ENABLED = False
    _DEFAULT_EFFORT = ""

    @property
    def default_model(self) -> str:
        # config.json 里的 model 可能是历史遗留的无效值（kimi-code/kimi-for-coding）。
        # 仅当它确实在可选模型列表内才采用，否则回退到有效默认值。
        model = self._kimi_config.get("model")
        if model and model in self.supported_models:
            return model
        return self._DEFAULT_MODEL

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

    _BUILTIN_MODELS = [
        "moonshot-cn/kimi-k2.6",
        "moonshot-cn/kimi-k2.7-code",
    ]

    _cached_models: list[str] | None = None  # class-level cache

    @property
    def supported_models(self) -> list[str]:
        """模型列表：config.json > kimi config.toml > 硬编码默认值（缓存）。"""
        if KimiAdapter._cached_models is not None:
            return KimiAdapter._cached_models
        # 1. config.json 显式配置
        models = self._kimi_config.get("models")
        if isinstance(models, list) and len(models) > 0:
            KimiAdapter._cached_models = [str(m) for m in models]
            return KimiAdapter._cached_models
        # 2. 从 kimi config.toml 自动获取
        toml_models = _parse_kimi_models_from_toml()
        if toml_models:
            KimiAdapter._cached_models = toml_models
            return KimiAdapter._cached_models
        # 3. 硬编码默认值
        KimiAdapter._cached_models = self._BUILTIN_MODELS
        return KimiAdapter._cached_models

    @classmethod
    def invalidate_models_cache(cls) -> None:
        """清空模型列表缓存（POST /api/config/reload 热重载用）。

        kimi 缓存无 TTL（读一次不再刷新），热重载是唯一不重启的刷新途径。
        """
        cls._cached_models = None

    # kimi `-S <id>` 恢复上下文但不重放历史事件（与 cbc --resume 的差异：
    # cbc 续写 JSONL 且 worker 在 one-shot MCP 路径 resume；kimi 仅恢复对话
    # 上下文，无 init 事件回放）。语义上等价于 cbc resume，故置 True 让 worker
    # 在 one-shot MCP 路径 resume，stream 路径由 wrapper 自己 -S（§4.8）。
    supports_resume = True
    supports_fork = True  # 通过文件复制实现 fork
    effort_values = ["low", "high", "max"]
    permission_modes = [
        {"value": "", "label": "default (interactive)"},
        {"value": "yolo", "label": "yolo (not available in -p mode)"},
        {"value": "auto", "label": "auto (not available in -p mode)"},
        {"value": "plan", "label": "plan (not available in -p mode)"},
    ]
    default_permission_mode = ""

    # wrapper 使用 `kimi -p` 一次性 prompt 模式。实测（2026-08-25）：`-p` 与
    # `-y`/`--auto`/`--plan` 均互斥——kimi 直接报错
    # "Cannot combine --prompt with --yolo/--auto/--plan"，故 -p 模式下权限参数
    # 不可用；thinking/effort 也无 CLI 参数（在 config.toml 全局配置）。
    # 前端只展示 model 设置（§4.6）。
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

    def resolved_cli_argv(self) -> list[str]:
        """Return the Kimi CLI path used by the wrapper."""
        return [self._KIMI_PATH]

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
        # Kimi 的 -y/--auto/--plan 不能和 -p 同时使用：实测 kimi 报错
        # "Cannot combine --prompt with --yolo/--auto/--plan"。wrapper 走 -p
        # 一次性模式，权限参数一律不可用，返回空（§4.6）。
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
                s.cli_session_id, s.name, workdir=s.workdir or None,
                kimi_home=s.adapter_config.get("kimi_home_dir"),
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
        args.extend(self.mcp_args(s))
        if extra_args:
            args.extend(extra_args)
        return args

    def oneshot_args(self, s: Session, text: str) -> list[str]:
        # kimi 的 worker 驱动方式只有 stream（wrapper 长驻），never 进入 oneshot
        # 路径，故返回 []（防御兜底，详见 execution_modes 注释）。
        return []

    def mcp_args(self, s: Session) -> list[str]:
        """MCP 注入（方案 C：KIMI_CODE_HOME 隔离 HOME）。

        kimi 无 `--mcp-config` 参数；项目级 mcp.json 受 folder-trust 门禁拦截
        （非交互 `-p` 无法应答信任提示，实测 project MCP 不注册，见 §4.5 / 调研文档）。

        本方法在会话配置了 mcp_servers 时，于 data/kimi-homes/<session_id>/ 准备
        隔离 HOME（拷贝真实 config.toml + 写 mcp.json），并以 `--kimi-home` 交给
        wrapper，由 wrapper 以 `KIMI_CODE_HOME` 环境变量注入 kimi 子进程。隔离
        HOME 对 kimi 而言即「用户级」，天然绕过信任门禁（实测验证）。

        无 mcp_servers 时不生成 HOME，返回 []（走原路径，使用真实用户目录）。
        返回的是 wrapper 的 `--kimi-home` 参数；kimi 本身无 MCP CLI flag。

        项目级 write_kimi_mcp_json 保留给交互/已信任场景，此处不调用。
        """
        home = self._prepare_kimi_home(s)
        if home is None:
            return []
        return ["--kimi-home", str(home)]

    def _prepare_kimi_home(self, s: Session) -> Path | None:
        """准备 data/kimi-homes/<session_id>/ 隔离 HOME。

        包含：
          a. 拷贝 ~/.kimi-code/config.toml（provider + api_key，只读，绝不写回真实目录）；
          b. 写 mcp.json（user-level 格式，含 pan/pan-qq 身份注入）。
        路径记录到 s.adapter_config["kimi_home_dir"] 供清理与 enrich 读取。
        无 mcp_servers 返回 None；写入失败返回 None（降级为无 MCP）。幂等。
        """
        servers = build_mcp_servers(s)
        if not servers:
            return None
        home = KIMI_HOME_ROOT / s.id
        try:
            home.mkdir(parents=True, exist_ok=True)
            # (a) 拷贝真实 config.toml（含 api_key）—— 仅读取，绝不写回 ~/.kimi-code
            src = Path.home() / ".kimi-code" / "config.toml"
            if src.is_file():
                shutil.copy(src, home / "config.toml")
            # (b) 写 mcp.json（隔离 HOME 内即用户级，绕过 folder-trust）
            write_mcp_json(home / "mcp.json", s)
            s.set_adapter_field("kimi_home_dir", str(home))
            _log.info("[KimiAdapter] prepared isolated kimi home -> %s", home)
            return home
        except OSError as e:
            _log.warning("[KimiAdapter] failed to prepare kimi home for %s: %s", s.id, e)
            return None

    def write_kimi_mcp_json(self, s: Session) -> None:
        """Write project-level mcp.json so kimi auto-loads Pan's MCP servers.

        kimi 没有 --mcp-config flag；MCP server 来自 `<workdir>/.kimi-code/mcp.json`
        （项目级，官方文档 https://www.kimi.com/code/docs/kimi-code-cli/customization/mcp.html
        确认）。kimi 在 cwd==workdir 的会话启动时会自动读取该文件，因此在 spawn 前
        写入即可让 pan / pan-qq server 生效。mcp_args() 返回 []（无 CLI flag），
        只负责触发本写入。

        NOTE（信任边界）：项目级 MCP 仅对「受信任文件夹」启用。若 workdir 未被信任，
        kimi 在 -p（非交互）模式下无法应答信任提示，可能跳过该项目 mcp.json。此时
        MCP 工具不会注册——属已知限制，需在 TUI 中 `Trust this folder` 或用户级
        ~/.kimi-code/mcp.json 兜底。
        """
        if not s.workdir:
            return
        path = Path(s.workdir) / ".kimi-code" / "mcp.json"
        # 描述符构造与幂等写由 adapters/mcp.py 共享 helper 收敛（P0-1）。
        mcp_servers = write_mcp_json(path, s)
        if mcp_servers:
            _log.info("[KimiAdapter] wrote mcp.json (%d servers) -> %s", len(mcp_servers), path)

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
        # 实测（2026-08-25）：kimi stream-json stdout 事件只有
        # meta(system.version) / assistant / meta(session.resume_hint)，
        # 均不含 model/modelAlias 字段。model 由两处兜底：
        # ① session 创建时用户显式指定；② enrich 时从 usage.record 的 model 回填
        # （见 enrich_after_result）。故此处固定返回 None。
        return None

    def is_assistant_event(self, event: dict) -> bool:
        role = event.get("role", "")
        # Also match thinking-content events so they feed into extract_assistant_blocks
        return role in ("assistant", "thinking")

    def extract_assistant_blocks(self, event: dict) -> list[dict]:
        blocks: list[dict] = []

        # ── flat string content (one-shot -p mode) ──
        content = event.get("content")
        if isinstance(content, str):
            role = event.get("role", "")
            blk_role = "thinking" if role == "thinking" else "assistant"
            blocks.append({"role": blk_role, "content": content})

        # ── structured content block array (some models emit this) ──
        elif isinstance(content, list):
            for b in content:
                if not isinstance(b, dict):
                    continue
                t = b.get("type", "")
                if t == "text":
                    blocks.append({"role": "assistant", "content": b.get("text", "")})
                elif t in ("think", "thinking"):
                    blocks.append({"role": "thinking", "content": b.get(t, "")})
                elif t == "tool_use":
                    n = b.get("name", "?")
                    i = b.get("input", {})
                    i_str = json.dumps(i, ensure_ascii=False) if isinstance(i, dict) else str(i)
                    blocks.append({"role": "tool", "content": f"{n}({i_str})"})

        # ── direct content.part events (same shape as wire.jsonl's inner event) ──
        if not blocks and event.get("type") == "content.part":
            part = event.get("part", {})
            ptype = part.get("type", "")
            text = part.get(ptype, "").strip()
            if text:
                role = "thinking" if ptype == "think" else "assistant"
                blocks.append({"role": role, "content": text})

        # ── tool calls ──
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
        # 用绝对路径 _KIMI_PATH（Windows 下 PATH 不一定有 kimi，裸 "kimi" 会失败）。
        return [self._KIMI_PATH, "-S", s.cli_session_id]

    # ── enrich ──

    def enrich_after_result(self, s: Session) -> list[dict] | None:
        """从 kimi 原生 wire.jsonl 读取本轮新增的 usage.record 条目（G2）。

        增量游标：用 usage.record 自带的递增 `time`（epoch ms）。Session 通过
        adapter_config["kimi_last_usage_ts"] 记录上次游标位置，只返回 time 大于
        游标的条目，然后推进游标。同时用最新 usage.record 的 model 回填 s.model
        （stdout 事件不含 model 字段，见 extract_model）。

        返回 list[dict]（cbc 同构：{"model","rawUsage","timestamp"}），或 None。
        """
        if not s.cli_session_id:
            return None
        try:
            return _read_kimi_new_entries(s)
        except Exception:
            _log.debug("kimi enrich_after_result failed", exc_info=True)
            return None


# ── enrich helpers（G2，参照 cbc._read_jsonl_new_entries 结构）──


def _iso_to_ms(iso: str) -> int | None:
    """Convert an ISO-8601 UTC timestamp string back to epoch ms (or None)."""
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
        return int(dt.timestamp() * 1000)
    except (ValueError, OSError):
        return None


def _read_kimi_new_entries(s: Session) -> list[dict] | None:
    """Return all NEW usage.record entries since the last enrichment.

    Uses a time cursor (usage.record's own increasing `time`, epoch ms) stored
    in ``s.adapter_config["kimi_last_usage_ts"]``. Returns only entries newer
    than the cursor, then advances the cursor. Also backfills ``s.model`` from
    the latest usage.record (stdout carries no model field — see extract_model).
    """
    from . import sessions as kimi_sessions

    # 短暂延迟，等待 kimi 完成 wire.jsonl 写入（解决时序竞态，cbc 同款 sleep）。
    time.sleep(0.3)

    all_entries = kimi_sessions.get_raw_usage(
        s.cli_session_id, s.workdir or None,
        kimi_home=s.adapter_config.get("kimi_home_dir"),
    )
    if not all_entries:
        return None

    last_ts = s.adapter_config.get("kimi_last_usage_ts", 0) or 0
    new_entries: list[dict] = []
    max_ts = last_ts
    for entry in all_entries:
        ts_ms = _iso_to_ms(entry.get("timestamp", ""))
        if ts_ms is None:
            continue
        if ts_ms > last_ts:
            new_entries.append(entry)
        if ts_ms > max_ts:
            max_ts = ts_ms

    # 推进游标（仅当确实看到更大的时间戳，避免把 0 写回清空游标）
    if max_ts > last_ts:
        s.set_adapter_field("kimi_last_usage_ts", max_ts)

    # 用最新 usage.record 的 model 回填 s.model（stdout 无 model 字段）
    if not s.model:
        for entry in reversed(all_entries):
            m = entry.get("model")
            if m:
                s.model = m
                break

    return new_entries if new_entries else None
