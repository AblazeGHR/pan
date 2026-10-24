"""DSH (DeepSeek Harness) adapter.

DSH 44.7.0 的 headless 入口是 ``dsh --profile headless [--json] [--session-id id] [task]``：

- **没有** stdin 长驻协议，也没有 model / permission / system-prompt / mcp 的 CLI
  flag；每次调用跑完一个任务就退出（``--json`` 输出 NDJSON 运行事件）。
- 因此本 adapter 与 kimi/codex 同构地使用 **wrapper 长驻**（``execution_modes =
  ["stream"]``）：Pan worker 起一个 ``wrapper.py``，wrapper 内部**逐条** spawn
  ``dsh headless --json``，把 NDJSON 翻译成 Pan 事件并合成 ``result``。
- session 续接用 ``--session-id <id>``（实测：未知 id 报错、已知 id 从 turn N+1
  继续）；Pan 侧 ``cli_session_id`` 由首条 run 的 ``session`` 事件捕获。
- model / reasoning effort / MCP server 全部通过 **profile patch 叠加层**
  （``--patch <file>``）注入，patch 文件落在 Pan 自己的数据目录
  （``data/dsh-configs/<session>.patch.yml``），不写 DSH 安装目录、不改用户
  profile。实测 ``--dump-config`` 与真实 run 均接受该注入。
- 权限模式通过环境变量 ``DSH_PERMISSION_MODE`` 控制（DSH 的 sandbox-policy /
  approval 行都读它）：``read-only`` / ``workspace-write`` / ``danger-full-access``。
- 凭据：headless profile **显式禁用**托管凭据库，只认 ``DEEPSEEK_API_KEY``
  （进程环境 → ``<启动 cwd>/.env`` → ``$DSH_HOME/.env``）。本机实测缺凭据时
  turn_end 报 ``MISSING_CREDENTIAL``；adapter 不伪造凭据，只在错误里透出可执行
  的提示。

详见 ``docs/design/dsh-adapter.md``。
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
from pathlib import Path

from ...session import Session
from ..mcp import build_mcp_servers

_log = logging.getLogger(__name__)

# 默认值：DSH 官方 deepseek-official 路由的出厂模型目录（见 llm-deepseek 文档：
# 省略 models 时公告 text+image 的 deepseek-flash 与 text-only 的 deepseek-v4-pro）。
_DEFAULT_MODEL = "deepseek-flash"
_DEFAULT_MODELS = ["deepseek-flash", "deepseek-v4-pro"]
_DEFAULT_PROVIDER = "deepseek-official"

# DSH reasoning effort 值域（llm-deepseek / agent-default-model 的
# reasoningEffort: off | low | high | max；空字符串=不覆盖、沿用 profile）。
_BASE_EFFORT_VALUES = ["", "off", "low", "high", "max"]

# DSH mcp-client 的 serverName 约束：[A-Za-z0-9_-]{1,32}
_SERVER_NAME_RE = re.compile(r"[^A-Za-z0-9_-]+")

_CLI_JS_RELATIVE = (
    "resources", "app.asar", "dsh", "node_modules", "@deepseek-ai",
    "dsh-desktop-host", "lib", "cli.js",
)


def _dsh_config() -> dict:
    from ...config import load_config
    return load_config().get("dsh", {})


def _patch_dir() -> Path:
    """Pan 自有的 DSH 叠加层目录（与 cbc 的 data/mcp-configs 同思路）。"""
    from ...session import SESSION_DIR
    return Path(SESSION_DIR).parent / "dsh-configs"


def _qt(value: str) -> str:
    """YAML 标量：JSON 双引号字符串是合法 YAML，无需引入 PyYAML 依赖。"""
    return json.dumps(str(value), ensure_ascii=False)


class DshAdapter:
    """DeepSeek Harness 适配器。无状态，可被多 worker 共享。"""

    name = "dsh"

    # 只有 stream：真正的"常驻"是 Pan 自己的 wrapper.py，DSH 侧逐任务一次性。
    execution_modes = ["stream"]

    supports_resume = True
    supports_fork = True
    # headless 没有 --system-prompt flag：声明 False 让 worker 走首条消息注入
    # （worker 只在「尚无 cli_session_id」时注入一次，DSH 会把该消息持久化进
    # session 历史，续接天然延续）。
    supports_spawn_system_prompt = False

    effort_values = list(_BASE_EFFORT_VALUES)

    # DSH 的权限由 DSH_PERMISSION_MODE 环境变量驱动：
    #   sandbox-policy.mode = $DSH_PERMISSION_MODE ?? 'workspace-write'
    #   approval.policy     = mode === 'danger-full-access' ? 'never' : 'ask'
    # headless 没有 approval answerer，'ask' 的请求 fail closed，因此无人值守
    # 场景实际可用的只有 danger-full-access（= 无 fs sandbox + never 审批）。
    permission_modes = [
        {"value": "", "label": "default (profile: workspace-write, approvals fail closed)"},
        {"value": "read-only", "label": "read-only (sandbox read-only, approvals fail closed)"},
        {"value": "workspace-write", "label": "workspace-write (sandbox = workdir, approvals fail closed)"},
        {"value": "danger-full-access", "label": "danger-full-access (no sandbox, approvals never)"},
    ]

    # 仅这三项被真实消费（model/effort → patch，permissionMode → env）。
    supported_settings = ["model", "permissionMode", "effort"]

    # ── 元信息 ──

    @property
    def default_model(self) -> str:
        model = str(_dsh_config().get("model") or "").strip()
        models = self.supported_models
        if model and model in models:
            return model
        return models[0] if models else _DEFAULT_MODEL

    @property
    def supported_models(self) -> list[str]:
        """config.json ``dsh.models`` 白名单 > DSH 出厂目录。

        DSH 没有 ``dsh models`` 之类的目录查询入口，模型 id 直接透传给 wire
        （llm-deepseek 文档：新模型无需重新注册），所以这里不探测网络、不扫描
        profile，只给"可核实的出厂目录 + 显式白名单"。目录不可用时回退
        ``_DEFAULT_MODEL`` 单元素列表，保证 UI 永远有可选值。
        """
        models = _dsh_config().get("models")
        if isinstance(models, list) and models:
            return [str(m) for m in models]
        return list(_DEFAULT_MODELS)

    @classmethod
    def invalidate_models_cache(cls) -> None:
        """/api/config/reload 兼容（本 adapter 无缓存，无需动作）。"""
        return None

    @property
    def default_permission_mode(self) -> str:
        mode = _dsh_config().get("permission_mode")
        values = {item["value"] for item in self.permission_modes}
        if isinstance(mode, str) and mode in values:
            return mode
        # 与 cbc/codex 一致：无人值守默认拿到完整能力；需要收紧时在 UI 里改。
        return "danger-full-access"

    @property
    def provider(self) -> str:
        """模型 provider 路由（patch 里写入 agent-default-model.provider）。"""
        return str(_dsh_config().get("provider") or _DEFAULT_PROVIDER)

    # ── 路径解析（避开 .CMD shim 的 cmd.exe 二次切分） ──

    def _resolve_dsh_path(self) -> str:
        """dsh 启动器路径：config.path > PAN_DSH_PATH > PATH 查找 > "dsh"。"""
        configured = _dsh_config().get("path")
        if configured:
            return str(configured)
        env_path = os.environ.get("PAN_DSH_PATH")
        if env_path:
            return env_path
        return shutil.which("dsh") or "dsh"

    def _resolve_entry(self) -> tuple[str, str | None]:
        """解析真实启动入口 ``(可执行文件, cli.js)``。

        Windows 上 ``shutil.which("dsh")`` 得到 ``dsh.cmd``，它内部是
        ``DeepSeek Harness.exe --expose-internals <app.asar>/dsh/.../cli.js``
        （ELECTRON_RUN_AS_NODE=1）。经 cmd.exe 二次解析会把中文/长参数切坏
        （与 cbc/codex/opencode 的既有结论一致），所以这里把 shim 还原成
        ``[exe, --expose-internals, cli.js]`` 直接交给 CreateProcess。

        返回 ``(exe, cli_js)``；cli.js 解析不到时为 ``(shim, None)``。
        """
        env_js = os.environ.get("PAN_DSH_CLI_JS")
        if env_js:
            return self._resolve_dsh_path(), env_js
        path = self._resolve_dsh_path()
        if path.lower().endswith((".cmd", ".bat")):
            exe, cli_js = _resolve_from_shim(path)
            if exe and exe.lower().endswith(".exe"):
                return exe, cli_js
            # 解析不出真实 exe 时退回 shim（cmd.exe 会处理），此时不能再传
            # --expose-internals/cli.js（shim 自己已经带上了）。
            return path, None
        # 直接给了 exe 的情况：同目录下按标准布局找 app.asar
        candidate = _cli_js_beside_exe(path)
        return path, candidate

    def resolved_cli_argv(self) -> list[str]:
        """CLI 可用性探测用 argv（不 spawn）。

        asar 内的 cli.js 无法用 ``Path.is_file()`` 核验，诊断端点（``/api/cli/status``
        逐个路径判断是否存在）因此只检查可执行文件本身。
        """
        exe, cli_js = self._resolve_entry()
        if cli_js and exe.lower().endswith(".exe") and ".asar" not in cli_js.lower():
            return [exe, "--expose-internals", cli_js]
        return [exe]

    @property
    def _wrapper_path(self) -> str:
        return str(Path(__file__).resolve().parent / "wrapper.py")

    # ── 进程启动 ──

    def base_args(self) -> list[str]:
        from ...config import resolve_pan_python_argv

        exe, cli_js = self._resolve_entry()
        args = [*resolve_pan_python_argv(), "-u", self._wrapper_path,
                "--dsh-path", exe,
                "--profile", str(_dsh_config().get("profile") or "headless")]
        if cli_js:
            args.extend(["--dsh-cli-js", cli_js])
        return args

    def model_args(self, s: Session) -> list[str]:
        # 无 CLI flag：model 走 patch 文件（见 patch_args）。
        return []

    def thinking_args(self, s: Session) -> list[str]:
        # DSH 的 thinking 由模型/effort 决定，没有独立开关。
        return []

    def effort_args(self, s: Session) -> list[str]:
        return []

    def permission_mode_args(self, s: Session) -> list[str]:
        """权限模式 → wrapper 的 ``--permission-mode``（最终落到 DSH 的环境变量）。"""
        mode = s.permission_mode or self.default_permission_mode
        if mode and mode in {item["value"] for item in self.permission_modes}:
            return ["--permission-mode", mode]
        return []

    def resume_args(self, s: Session) -> list[str]:
        if s.cli_session_id:
            return ["--session-id", s.cli_session_id]
        return []

    def fork_args(self, s: Session | None = None) -> list[str]:
        """Fork 由 SessionsProvider.fork_session（复制原生 session 目录）完成。"""
        return []

    def build_spawn_args(self, s: Session,
                         extra_args: list[str] | None = None) -> list[str]:
        args = self.base_args()
        args.extend(self.patch_args(s))
        args.extend(self.permission_mode_args(s))
        args.extend(self.resume_args(s))
        if extra_args:
            args.extend(extra_args)
        return args

    def oneshot_args(self, s: Session, text: str) -> list[str]:
        # execution_modes 只有 stream，worker 不会走 oneshot 路径（防御兜底）。
        return []

    # ── profile patch 叠加层（model / effort / MCP） ──

    def patch_args(self, s: Session) -> list[str]:
        """写 session 级 patch 文件并返回 ``--patch <path>``（无内容时返回 []）。

        patch 内容（每项都是 DSH loader 支持的 patch 语义，实测有效）：
        - ``- id: agent-default-model`` + 完整 config：**config 是整体替换**，
          故必须给出 provider/model/effort 三项；DSH 该行只有这三个字段；
        - ``- insert: [...]``：在根列表追加 ``@deepseek-ai/dsh-mcp-client`` 条目
          （headless bundle 本身不含 MCP client，必须显式挂载）。
        """
        text = self._patch_text(s)
        if not text:
            return []
        path = _patch_dir() / f"{s.id}.patch.yml"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        except OSError as exc:
            # 写失败不应阻断会话：退化为"无 patch"（profile 默认模型/无 MCP）。
            _log.warning("failed to write dsh patch at %s: %s", path, exc)
            return []
        return ["--patch", str(path)]

    def mcp_args(self, s: Session) -> list[str]:
        """与 cbc 的 mcp_args 同义：返回注入本次 spawn 的 flag（无 MCP 时仍含
        model patch，故本方法等价于 patch_args；保留命名便于既有探测逻辑）。"""
        return self.patch_args(s)

    def _patch_text(self, s: Session) -> str:
        model = str(s.model or self.default_model).strip()
        effort = str(s.adapter_config.get("effort") or "").strip()
        servers = {}
        try:
            servers = build_mcp_servers(s)
        except Exception:  # noqa: BLE001 - 配置错误不应阻断 spawn（无 MCP 起步）
            _log.warning("dsh: failed to build MCP descriptors", exc_info=True)
            servers = {}

        lines = [
            "# Pan-generated DSH profile overlay (session-scoped).",
            "# Rewritten on every spawn; safe to delete while the session is idle.",
        ]
        if model:
            lines.append("- id: agent-default-model")
            lines.append("  config:")
            lines.append(f"    provider: {_qt(self.provider)}")
            lines.append(f"    model: {_qt(model)}")
            if effort:
                lines.append(f"    reasoningEffort: {_qt(effort)}")
        if servers:
            lines.append("- insert:")
            used: set[str] = set()
            for index, (name, entry) in enumerate(servers.items()):
                lines.extend(_mcp_entry_lines(name, entry, index, used))
        if len(lines) == 2:
            return ""
        # 结尾空行既让 YAML 干净，也避免末行无换行。
        return "\n".join(lines) + "\n"

    # ── stdin 消息编码 ──

    def encode_user_message(self, text: str) -> bytes:
        return json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")

    # ── stdout 事件解析 ──

    def parse_event(self, line: str) -> dict | None:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return None
        return event if isinstance(event, dict) else None

    def event_type(self, event: dict) -> str:
        return event.get("type") or ""

    def is_init_event(self, event: dict) -> bool:
        # wrapper 把 DSH 的 session 事件翻成 cbc 同形 system/init。
        return event.get("type") == "system" and event.get("subtype") == "init"

    def extract_session_id(self, event: dict) -> str | None:
        return event.get("session_id") or event.get("sessionId")

    def extract_model(self, event: dict) -> str | None:
        # DSH 运行事件不含 model；s.model 在会话创建时已知。
        return None

    def is_assistant_event(self, event: dict) -> bool:
        # wrapper 翻译后是 assistant；原生 NDJSON 回放时也接受 text/thinking/tool_call。
        return event.get("type") in ("assistant", "text", "thinking", "tool_call")

    def extract_assistant_blocks(self, event: dict) -> list[dict]:
        """assistant 事件 → Pan 历史块。

        同时兼容 wrapper 的 cbc 形状（``message.content``）与 DSH 原始事件
        （``text`` / ``thinking`` / ``tool_call``），便于直接回放原生 NDJSON。
        """
        etype = event.get("type")
        if etype == "assistant":
            content = ((event.get("message") or {}).get("content") or [])
            return _content_blocks(content)
        if etype == "text":
            text = event.get("text") or ""
            return [{"role": "assistant", "content": text}] if text else []
        if etype == "thinking":
            text = event.get("text") or ""
            return [{"role": "thinking", "content": text}] if text else []
        if etype == "tool_call":
            name = str(event.get("tool") or "tool")
            payload = event.get("input")
            return [{"role": "tool", "content": _render_tool(name, payload)}]
        return []

    def is_result_event(self, event: dict) -> bool:
        # 正常路径：wrapper 合成的 result；原生回放时 DSH 的 final 也算终止。
        return event.get("type") in ("result", "final")

    def is_result_error(self, event: dict) -> bool:
        if event.get("type") == "final":
            return False
        return bool(event.get("is_error", False))

    def extract_result_text(self, event: dict) -> str | None:
        if event.get("type") == "final":
            return event.get("text")
        return event.get("result")

    # ── takeover ──

    def takeover_command(self, s: Session) -> list[str]:
        """接管 = 用 DSH 自己的交互式 profile 续接同一 session。

        headless 是"一个任务即退出"，不能在终端里接管；交互表面是 ``tui``（或
        桌面 ``desktop``）profile，且 **profile 必须先存在**：实测 ``dsh --profile
        tui`` 在未创建时报 `profile "tui" does not exist`。因此这里只在 profile
        目录存在时返回命令，否则返回 []，让上层给出明确的「不支持 takeover」。
        """
        if not s.cli_session_id:
            return []
        exe, cli_js = self._resolve_entry()
        base = [exe, "--expose-internals", cli_js] if (cli_js and exe.lower().endswith(".exe")) else [exe]
        for profile in ("tui", "desktop"):
            if (_dsh_home() / "profiles" / profile).is_dir():
                return [*base, "--profile", profile, "--resume", s.cli_session_id]
        return []

    # ── enrich ──

    def enrich_after_result(self, s: Session) -> list[dict] | None:
        """从 DSH 原生 session 日志读取本轮新增 token usage（增量游标）。

        DSH 把每个 step 的 provider usage 写进 ``assistant/message`` 事件，
        session 日志在进程退出前落盘（wrapper 也是读到 ``final`` 才收尾），
        因此这里读到的是权威数据而非投影缓存。返回与 codex/opencode 同构的
        session 级增量（``{"model","rawUsage","timestamp"}``）。
        """
        if not s.cli_session_id:
            return None
        from . import sessions as dsh_sessions

        entries = dsh_sessions.get_raw_usage(s.cli_session_id, s.workdir or None)
        if not entries:
            return None
        current = entries[0]
        raw = current.get("rawUsage") or {}
        previous = s.adapter_config.get("dsh_prev_usage") or {}
        keys = ("prompt_tokens", "completion_tokens", "reasoning_tokens",
                "cache_read_tokens", "cache_write_tokens", "total_tokens")
        delta = {}
        for key in keys:
            old = previous.get(key, 0)
            new = raw.get(key, 0)
            try:
                delta[key] = max(0, int(new) - int(old))
            except (TypeError, ValueError):
                delta[key] = 0
        s.set_adapter_field("dsh_prev_usage", {key: raw.get(key, 0) for key in keys})
        if not any(delta.values()):
            return None
        return [{
            "model": current.get("model") or s.model or "",
            "rawUsage": delta,
            "timestamp": current.get("timestamp") or "",
        }]


# ── 内部 helper ──


def _dsh_home() -> Path:
    """DSH 用户目录（尊重 DSH_HOME，默认 ``~/.dsh``）。"""
    configured = os.environ.get("DSH_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".dsh"


def _cli_js_from_shim(shim_path: str) -> str | None:
    """由 ``dsh.cmd`` 还原 ``app.asar/.../cli.js``（标准安装布局兜底）。"""
    shim_dir = Path(shim_path).resolve().parent           # .../runtime/cli/bin
    if len(shim_dir.parents) < 4:
        return None
    install = shim_dir.parents[3]                         # .../<install>
    candidate = install.joinpath(*_CLI_JS_RELATIVE)
    return str(candidate) if _script_exists(str(candidate)) else None


def _exe_from_shim(shim_path: str) -> str | None:
    """由 ``dsh.cmd`` 还原同安装内的 ``DeepSeek Harness.exe``（布局兜底）。"""
    shim_dir = Path(shim_path).resolve().parent
    if len(shim_dir.parents) < 4:
        return None
    install = shim_dir.parents[3]
    for name in ("DeepSeek Harness.exe", "DeepSeek Harness"):
        candidate = install / name
        if candidate.is_file():
            return str(candidate)
    return None


def _script_exists(path: str) -> bool:
    """cli.js 是否存在。

    DSH 的 cli.js 打包在 ``app.asar``（单文件归档）里，Electron 能把它当虚拟路径
    加载，但操作系统层面 ``is_file()`` 为 False——因此 asar 内的路径改判"归档
    文件本身存在"，只有普通文件系统路径才校验脚本自身。
    """
    low = path.lower()
    marker = low.find(".asar")
    if marker >= 0:
        archive = Path(path[: marker + len(".asar")])
        return archive.is_file()
    return Path(path).is_file()


def _expand_shim_path(raw: str, shim_dir: Path) -> str:
    """展开 .cmd 里的 ``%~dp0`` 前缀（其余部分按 shim 目录拼接）。"""
    value = raw.strip()
    low = value.lower()
    if low.startswith("%~dp0"):
        return str(shim_dir.joinpath(value[5:].replace("/", os.sep)))
    return value


def _resolve_from_shim(shim_path: str, depth: int = 0) -> tuple[str | None, str | None]:
    """解析 dsh 启动链，返回 ``(exe, cli.js)``。

    两种实际形态都要支持：

    1. **转发 shim**（npm 全局目录）：``call "D:\\project\\DSH\\resources\\runtime\\cli\\bin\\dsh.cmd" %*``；
    2. **安装 shim**（DSH 自带）：``set ELECTRON_RUN_AS_NODE=1`` +
       ``"%~dp0..\\..\\..\\..\\DeepSeek Harness.exe" --expose-internals "%~dp0..\\..\\..\\app.asar\\...\\cli.js"``。

    先按内容解析（跟着 ``call`` 递归，最多 4 层），再退回标准目录布局猜测。
    这样既不依赖安装路径写死，也不会把 cmd.exe 放进启动链。
    """
    if depth > 4:
        return None, None
    path = Path(shim_path)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    shim_dir = path.resolve().parent

    delegated = re.search(r'"([^"]*?(?:dsh|ds)[^"]*?\.(?:cmd|bat))"', text, re.IGNORECASE)
    if delegated:
        target = _expand_shim_path(delegated.group(1), shim_dir)
        exe, cli_js = _resolve_from_shim(target, depth + 1)
        if exe or cli_js:
            return exe, cli_js

    exe_match = re.search(r'"([^"]*?\.exe)"', text, re.IGNORECASE)
    exe = _expand_shim_path(exe_match.group(1), shim_dir) if exe_match else None
    js_match = re.search(r'"([^"]*?cli\.js)"', text, re.IGNORECASE)
    cli_js = _expand_shim_path(js_match.group(1), shim_dir) if js_match else None
    if exe:
        exe = str(Path(exe).resolve()) if Path(exe).is_file() else None
    if cli_js:
        cli_js = str(Path(cli_js).resolve()) if _script_exists(cli_js) else None

    return (
        exe or _exe_from_shim(shim_path),
        cli_js or _cli_js_from_shim(shim_path),
    )


def _cli_js_beside_exe(path: str) -> str | None:
    """给定 exe（或安装根）时按标准布局找 cli.js。"""
    p = Path(path)
    roots = [p.parent] if p.suffix.lower() == ".exe" else [p, p.parent]
    for root in roots:
        candidate = root.joinpath(*_CLI_JS_RELATIVE)
        if _script_exists(str(candidate)):
            return str(candidate)
    return None


def _content_blocks(content) -> list[dict]:
    """cbc 形状的 message.content → Pan 历史块（text/thinking/tool_use）。"""
    blocks: list[dict] = []
    for part in content or []:
        if not isinstance(part, dict):
            continue
        ptype = part.get("type")
        if ptype == "text":
            text = part.get("text") or ""
            if text:
                blocks.append({"role": "assistant", "content": text})
        elif ptype in ("thinking", "think", "reasoning"):
            text = part.get("thinking") or part.get("text") or ""
            if text:
                blocks.append({"role": "thinking", "content": text})
        elif ptype in ("tool_use", "tool-call", "tool_call"):
            name = str(part.get("name") or "tool")
            blocks.append({"role": "tool", "content": _render_tool(name, part.get("input"))})
    return blocks


def _render_tool(name: str, payload) -> str:
    if payload is None:
        payload = {}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = {"input": payload}
    try:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        body = str(payload)
    return f"{name}({body})"


def _server_name(name: str) -> str:
    """DSH mcp-client 的 serverName 约束：[A-Za-z0-9_-]{1,32}。"""
    cleaned = _SERVER_NAME_RE.sub("-", str(name or "mcp")).strip("-") or "mcp"
    return cleaned[:32]


def _mcp_entry_lines(name: str, entry: dict, index: int, used: set[str]) -> list[str]:
    """单个 MCP server → patch 的 insert 条目行。"""
    server_name = _server_name(name)
    if server_name in used:
        server_name = f"{server_name[:28]}-{index}"[:32]
    used.add(server_name)
    entry_id = f"pan-mcp-{server_name}"
    if entry_id in used:
        entry_id = f"{entry_id}-{index}"
    used.add(entry_id)

    lines = [
        f"  - id: {_qt(entry_id)}",
        "    name: \"@deepseek-ai/dsh-mcp-client\"",
        "    config:",
        f"      serverName: {_qt(server_name)}",
    ]
    url = entry.get("url")
    if url:
        # DSH 只支持 stdio / streamable-http；http 与 sse 都按 streamable-http
        # 表达（sse 老传输 DSH 未实现，见 docs/design/dsh-adapter.md）。
        lines.append("      transport: \"streamable-http\"")
        lines.append(f"      url: {_qt(url)}")
        headers = entry.get("headers") or {}
        if isinstance(headers, dict) and headers:
            lines.append("      headers:")
            for key, value in headers.items():
                lines.append(f"        {_qt(key)}: {_qt(value)}")
    else:
        lines.append("      transport: \"stdio\"")
        lines.append(f"      command: {_qt(entry.get('command') or '')}")
        args = entry.get("args") or []
        if args:
            lines.append("      args:")
            for arg in args:
                lines.append(f"        - {_qt(arg)}")
        env = entry.get("env") or {}
        if isinstance(env, dict) and env:
            lines.append("      env:")
            for key, value in env.items():
                lines.append(f"        {_qt(key)}: {_qt(value)}")
        cwd = entry.get("cwd")
        if cwd:
            lines.append(f"      cwd: {_qt(cwd)}")
    return lines
