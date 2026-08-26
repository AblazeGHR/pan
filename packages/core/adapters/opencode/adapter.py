"""OpenCode (sst/opencode) CLI 适配器。

OpenCode 的 `opencode run` 是一次性进程（无 stdin 长驻协议），故通过 wrapper.py
包装成一个长驻子进程，由 wrapper 在内部循环调用 `opencode run --format json`。
设计对齐 kimi 适配（wrapper 模式）。
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from ...session import Session
from ..mcp import build_mcp_servers

_log = logging.getLogger(__name__)

# 模型列表缓存 TTL：opencode 模型列表经常变动（models.dev 源 / provider 上架
# 下架），永久 class 级缓存会让列表只会在重启后才刷新。参考 worker.py
# _TASK_STATUS_TTL_SEC / character.py _manifest_check_ttl 的先例，采用带过期
# 时间的 TTL 缓存，超时后下次访问自动重新拉取。config.json 白名单同样走 TTL
# （用户改配置后无需重启服务即可生效）。
_MODEL_CACHE_TTL: float = 300.0  # 5 分钟

# 模型行形态：provider[/org][/region/...]/model，任意段数（两段 provider/model、
# 三段 provider/org/model、四段 provider/region/org/model 均合法）。
# 段内字符取 models.dev 命名惯例：字母数字、`.`、`-`、`_`。
_MODEL_LINE_RE = re.compile(r"^[\w.\-]+(?:/[\w.\-]+)+$")

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

    _cached_models: list[str] | None = None  # class-level cache（TTL）
    _cached_models_ts: float = 0.0  # 最近一次填充/刷新的单调时钟时间戳

    @property
    def supported_models(self) -> list[str]:
        """模型列表：config.json > `opencode models` 解析 > 内置默认值（TTL 缓存）。

        缓存有效期 _MODEL_CACHE_TTL 秒；超时后下次访问自动重新拉取，避免
        opencode 模型列表变动时需要重启服务才能刷新。
        """
        if OpencodeAdapter._cached_models is not None and (
            time.monotonic() - OpencodeAdapter._cached_models_ts
        ) < _MODEL_CACHE_TTL:
            return OpencodeAdapter._cached_models
        OpencodeAdapter._cached_models = self._fetch_models()
        OpencodeAdapter._cached_models_ts = time.monotonic()
        return OpencodeAdapter._cached_models

    def _fetch_models(self) -> list[str]:
        """按优先级拉取模型列表：config.json 白名单 > CLI 自动识别 > 内置默认值。"""
        models = self._opencode_config.get("models")
        if isinstance(models, list) and len(models) > 0:
            return [str(m) for m in models]
        cli_models = _parse_models_from_opencode()
        if cli_models:
            return cli_models
        return list(_BUILTIN_MODELS)

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
        return _resolve_opencode_path()

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
        args.extend(self.mcp_args(s))
        if extra_args:
            args.extend(extra_args)
        return args

    def mcp_args(self, s: Session) -> list[str]:
        """opencode 无 --mcp-config；MCP 来自 opencode.json 的 mcp 段（项目级）。

        本方法返回空列表（无 CLI flag），但顺带把 session 的 mcp_servers 写入
        <workdir 的 opencode.json 项目配置>（对齐 kimi 的项目级 mcp.json 思路，
        §4.5 的「待定」项在此实现）。opencode run 以 cwd==workdir 启动时自动加载
        该文件——opencode 从 cwd 向上找到最近 .git 根作为项目配置位置；非 git
        目录直接用 cwd。

        写文件由本方法触发（build_spawn_args 调用），时机与 kimi.write_kimi_mcp_json
        对齐（spawn 前）。幂等写，未配置/写失败时返回 []（无 MCP flag）。
        """
        self.write_opencode_mcp_json(s)
        return []

    def write_opencode_mcp_json(self, s: Session) -> None:
        """把 session 的 mcp_servers 写为 opencode 可加载的项目级 opencode.json。

        描述符由共享 helper build_mcp_servers 构造（含 pan/pan-qq 的
        PAN_AGENT_SESSION_ID/TITLE 注入、type=stdio），本方法再将其映射为 opencode
        的 mcp 段格式：
          - stdio/local → {"type":"local","command":[cmd,*args],"cwd":...,"environment":...,"enabled":true}
          - remote/http/sse → {"type":...,"url":...,"headers":...,"environment":...,"enabled":true}

        **合并而非覆盖**：读取已存在的 opencode.json(c)，仅更新 mcp 段并保留其它键
        （model/provider/permission 等），避免破坏用户配置。JSONC（含 // 注释）做
        容错解析；解析失败则备份原名后重新写入（best-effort）。写入路径记录到
        adapter_config["opencode_mcp_config_path"] 以便后续清理。

        NOTE（信任边界）：opencode 不像 kimi 需要 trust 提示——实测项目级 mcp 在
        run 模式下直接加载，无交互式授权阻塞（见 E2E 验证报告）。
        """
        if not s.workdir:
            return
        mcp_servers = build_mcp_servers(s)
        if not mcp_servers:
            return

        path = self._opencode_project_config_path(Path(s.workdir))
        config = self._load_opencode_config(path)

        oc_mcp: dict[str, dict] = {}
        for name, entry in mcp_servers.items():
            oc_mcp[name] = self._to_opencode_mcp_entry(entry)

        config["mcp"] = {**config.get("mcp", {}), **oc_mcp}
        config.setdefault("$schema", "https://opencode.ai/config.json")

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
        except OSError as e:
            _log.warning("[OpencodeAdapter] failed to write opencode.json at %s: %s", path, e)
            return

        s.set_adapter_field("opencode_mcp_config_path", str(path))
        _log.info("[OpencodeAdapter] wrote opencode mcp config (%d servers) -> %s",
                  len(oc_mcp), path)

    @staticmethod
    def _to_opencode_mcp_entry(entry: dict) -> dict:
        """将 build_mcp_servers 的描述符映射为 opencode 的 mcp 段 entry。"""
        etype = entry.get("type", "stdio")
        # opencode 用 "local" 表示 stdio（命令数组，无独立 args 字段）
        if etype in ("stdio", "local"):
            cmd = entry.get("command")
            args = list(entry.get("args") or [])
            command = ([cmd, *args] if isinstance(cmd, str) else list(args))
            out: dict = {"type": "local", "command": command, "enabled": True}
            if entry.get("cwd"):
                out["cwd"] = entry["cwd"]
            env = dict(entry.get("env") or {})
            # 透传 PAN_API_URL（若存在），确保 pan server 指向正确的 Pan 服务
            pan_api_url = os.environ.get("PAN_API_URL")
            if pan_api_url and "PAN_API_URL" not in env:
                env["PAN_API_URL"] = pan_api_url
            if env:
                out["environment"] = env
            return out
        # remote / http / sse：透传 url/headers/env
        out = {"type": etype, "enabled": True}
        if entry.get("url"):
            out["url"] = entry["url"]
        if entry.get("headers"):
            out["headers"] = entry["headers"]
        if entry.get("env"):
            out["environment"] = entry["env"]
        return out

    @staticmethod
    def _opencode_project_config_path(workdir: Path) -> Path:
        """opencode 项目配置位置：从 workdir 向上找最近的 .git 根；非 git 目录用 cwd。

        opencode 启动时会从 cwd 向上遍历到最近的 .git 目录读取项目 opencode.json，
        故写入该位置才能被 run 模式发现。返回路径沿用已存在的 .jsonc 扩展名，否则
        用 .json（纯 JSON 也是合法 JSONC）。
        """
        cur = workdir.resolve()
        root = cur
        for parent in [cur, *cur.parents]:
            if (parent / ".git").exists():
                root = parent
                break
        jsonc = root / "opencode.jsonc"
        if jsonc.exists():
            return jsonc
        return root / "opencode.json"

    @staticmethod
    def _load_opencode_config(path: Path) -> dict:
        """容错读取 opencode 配置（支持 JSONC），解析失败则备份后返回空 dict。"""
        if not path.exists():
            return {}
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            try:
                return json.loads(_strip_jsonc(text))
            except json.JSONDecodeError:
                _log.warning("[OpencodeAdapter] cannot parse %s, backing up and starting fresh", path)
                try:
                    path.rename(path.with_suffix(path.suffix + ".bak"))
                except OSError:
                    pass
                return {}

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
        # opencode 接管 = 交互式 TUI 续接会话，用**顶层** `--session <id>`（见
        # `opencode --help`：`opencode [project]` 默认启动 TUI，`--session` 为顶层
        # "session id to continue" 选项）。不要加 `run` 子命令——`opencode run`
        # 是一次性（非交互）执行，会忽略后续交互，不适合接管。
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


def _strip_jsonc(text: str) -> str:
    """极简 JSONC → JSON：去除 /* */ 块注释、// 行注释、尾随逗号。

    仅用于 best-effort 解析用户已有的 opencode.jsonc；opencode 配置极少在字符串
    内含 //，故按行剥离 // 足够。失败由调用方回退到备份+重写。
    """
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    out_lines = []
    for line in text.splitlines():
        idx = line.find("//")
        if idx != -1:
            line = line[:idx]
        out_lines.append(line)
    out = "\n".join(out_lines)
    out = re.sub(r",\s*([}\]])", r"\1", out)
    return out


def _resolve_opencode_path() -> str:
    """解析 opencode 可执行文件路径，避开 npm 的 `.CMD` shim。

    Windows 下 ``shutil.which("opencode")`` 返回 ``opencode.CMD``（npm shim）。
    把它传给 subprocess 会经由 ``cmd.exe /c`` 启动，cmd.exe 用系统 ANSI 代码页
    重新切分命令行，导致非 ASCII 参数（如中文 text）被乱码化——opencode run 收到
    乱码后无 stdout，worker 读取超时，会话卡在 running。

    这里把 shim 解析为真实可执行文件
    （``<npm-global>/node_modules/opencode-ai/bin/opencode.exe``），参数经
    CreateProcess 原样传给 opencode，不再经过 cmd.exe 二次解析。

    优先级：``PAN_OPENCODE_PATH`` 环境变量 > 解析真实 exe > 回退 ``shutil.which``。
    """
    env = os.environ.get("PAN_OPENCODE_PATH")
    if env:
        return env
    which = shutil.which("opencode")
    if not which:
        return "opencode"
    resolved = _resolve_opencode_exe_from_shim(which)
    if resolved:
        return resolved
    return which


def _resolve_opencode_exe_from_shim(shim_path: str) -> str | None:
    """将由 `.CMD`/`.bat` shim 解析出真实 opencode 可执行文件。

    npm shim 布局：``<dir>/opencode.CMD`` 与
    ``<dir>/node_modules/opencode-ai/bin/opencode[.exe]`` 相邻。命中返回真实 exe
    路径，否则返回 ``None``（调用方回退到 shim 本身）。

    对齐 cbc adapter 的 ``_resolve_cbc_argv``：先查确定的包目录，再 glob 兜底。
    opencode 为原生二进制（非 node 脚本），故直接返回 exe，无需 ``node <entry>``。
    """
    if not shim_path.lower().endswith((".cmd", ".bat")):
        # 已是真实可执行文件（如 Linux/macOS 下的 bin 软链），无需解析
        return None
    shim_dir = os.path.dirname(os.path.abspath(shim_path))
    bin_name = "opencode.exe" if sys.platform == "win32" else "opencode"
    import glob as _glob
    candidates = [
        os.path.join(shim_dir, "node_modules", "opencode-ai", "bin", bin_name),
    ]
    candidates += _glob.glob(
        os.path.join(shim_dir, "node_modules", "*", "bin", "opencode*")
    )
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def _parse_models_from_opencode() -> list[str]:
    """解析 `opencode models` 输出（每行一个模型，形如 provider[/org]/model）。

    支持任意段数的模型名：两段 provider/model（如 ``opencode/big-pickle``）、
    三段 provider/org/model（如 ``siliconflow-cn/deepseek-ai/DeepSeek-R1``）、
    四段 provider/region/org/model（如 ``siliconflow-cn/Pro/deepseek-ai/DeepSeek-R1``）。

    杂行防御：跳过空行、注释行（# / // 开头）；分组标题/表头等含空格、冒号或
    方括号的非模型形态行会被 _MODEL_LINE_RE 自然排除，不会被误收为模型。
    """
    try:
        r = subprocess.run(
            [_resolve_opencode_path(), "models"],
            capture_output=True, text=True, timeout=15,
        )
    except Exception:
        return []
    out = (r.stdout or "") + (r.stderr or "")
    models: list[str] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#") or line.startswith("//"):
            continue
        if _MODEL_LINE_RE.match(line):
            models.append(line)
    return models
