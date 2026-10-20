"""cbc (CodeBuddy CLI) 适配器。"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import math
import os
import re
import shutil
import subprocess
from packages.jobs.window import command_window_options
import time
from pathlib import Path
from ...session import SESSION_DIR, Session
from ..mcp import write_mcp_json

_log = logging.getLogger(__name__)

# Pan 内统一目录：MCP 配置收敛到 data/mcp-configs/<session_id>.mcp.json（立项 4.9），
# 不再写 workdir/.codebuddy/mcp.json（workdir 可能在 Pan 外，写外部目录污染且可能不可写）。
MCP_CONFIG_DIR = SESSION_DIR.parent / "mcp-configs"

# 模型列表缓存 TTL：cbc CLI 侧模型经常变动（上架/下架），永久 class 级缓存会让
# 列表只会在重启后才刷新。参考 opencode adapter _MODEL_CACHE_TTL / worker.py
# _TASK_STATUS_TTL_SEC 的先例，采用带过期时间的 TTL 缓存，超时后下次访问自动
# 重新拉取。config.json 白名单同样走 TTL（用户改配置后无需重启服务即可生效）。
_MODEL_CACHE_TTL: float = 300.0  # 5 分钟

# ── 模型额度消耗倍率（credits） ──
#
# cbc CLI 的产品配置（AgentManager.getModels 的数据源，也是 `--help` 模型列表
# 与计费的同一份配置）落盘在本地 FileLocalStorage 缓存
# ~/.codebuddy/local_storage/entry_<md5("cloud_product_config_cache")>.info，
# 内容为 [{userId, data, ts}, ...]，其中 data.models[].credits（如
# "x0.08 credits"）即该模型的额度消耗倍率——CLI 自身的
# ModelUtils.parseCreditsValue / isFreeModel 消费同一字段，不是价格、剩余
# 额度、token usage 或推理 effort。
#
# 有效配置选择规则（CLI 源码证据，codebuddy 2.168.0 dist-server/codebuddy.js）：
#   1. 当前账号 ID = UserinfoProvider.provide()?.userId，源自登录 JWT 的
#      payload.sub（tryExtractUserIDFromJWTToken；JWT 位于 settings.json 的
#      env.CODEBUDDY_AUTH_TOKEN，CLI 只 decode 不验签）；
#   2. 有效配置 = 该缓存中 userId 精确匹配的条目（readDiskCache:
#      `.find(i => i.userId === t)`）——CLI 从不跨账号合并、从不按 ts 挑选；
#      writer（updateDiskCache）同 user 先 splice 再 push，正常文件里每个
#      userId 至多一条；
#   3. readDiskCache 不检查 ts 的新鲜度（陈旧判定只发生在更上层的 memCache
#      过期与远程拉取失败降级），因此这里也不自作主张加陈旧阈值。
#
# 读取方式为纯本地只读：不发起网络请求、不调用任何模型、不需要凭据（只读
# settings.json 的一个 JWT claim 与上述缓存文件；不记录、不外泄 token）。

# CLI credits 字段的真实格式（ModelUtils.parseCreditsValue 注释与实测数据）：
#   "x0.08 credits" / "x2.20 credits" / "x1.62" / "x0.00 credits" / "0.08x"
# 这里锚定整串匹配（可选 x 前缀 + 带符号数字 + 可选 credits/x 后缀）。与 CLI
# 的无符号 search 的差异是有意为之：CLI 会把 "-0.08" 读成 0.08，而 Pan 只做
# 显示，宁缺勿错——不符合真实格式的一律按"无倍率"处理，不猜测。
_CREDITS_VALUE_RE = re.compile(
    r"^\s*[xX]?\s*([+-]?\d+(?:\.\d+)?)\s*(?:credits?)?\s*[xX]?\s*$",
    re.IGNORECASE,
)
_CLOUD_PRODUCT_CONFIG_CACHE_KEY = "cloud_product_config_cache"
_LOCAL_STORAGE_MAX_FILE_BYTES = 4 * 1024 * 1024  # 单文件字节上限（有界读取）


def _codebuddy_home() -> Path:
    """cbc CLI 的用户目录（与 CLI PathUtils.getHomeDir()/".codebuddy" 一致）。"""
    return Path.home() / ".codebuddy"


def _current_cbc_user_id() -> str | None:
    """当前生效的 cbc 账号 ID：settings.json env.CODEBUDDY_AUTH_TOKEN 的 sub。

    与 CLI 的 tryExtractUserIDFromJWTToken / UserinfoProvider 同源：CLI 从该
    JWT 的 payload.sub 提取当前登录账号。只做 base64 解码、不验签、不记录
    token 内容；任何缺失/损坏都返回 None（调用方按"无法确定当前配置"处理）。
    """
    try:
        settings = json.loads(
            (_codebuddy_home() / "settings.json").read_text(encoding="utf-8", errors="replace")
        )
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    env = settings.get("env") if isinstance(settings, dict) else None
    token = env.get("CODEBUDDY_AUTH_TOKEN") if isinstance(env, dict) else None
    if not isinstance(token, str) or token.count(".") < 2:
        return None
    try:
        segment = token.split(".")[1]
        segment += "=" * (-len(segment) % 4)
        claims = json.loads(base64.urlsafe_b64decode(segment))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    sub = claims.get("sub") if isinstance(claims, dict) else None
    return sub if isinstance(sub, str) and sub.strip() else None


def _read_cloud_product_config_entries() -> list[dict]:
    """读取 cloud_product_config_cache 中的账号条目列表（CLI 同源缓存文件）。

    文件名 = entry_<md5("cloud_product_config_cache")>.info（与 CLI
    FileLocalStorage.resolveEntryPath 一致，已实测匹配）。内容为
    [{userId, data, ts}, ...]。只读这一个已知文件——不做目录 glob，从根上
    避免无界遍历与其他账号数据混入。读取/解析/超限失败均返回 []。
    """
    path = _codebuddy_home() / "local_storage" / (
        "entry_" + hashlib.md5(_CLOUD_PRODUCT_CONFIG_CACHE_KEY.encode("utf-8")).hexdigest() + ".info"
    )
    try:
        if path.stat().st_size > _LOCAL_STORAGE_MAX_FILE_BYTES:
            return []
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError, ValueError):
        return []
    return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []


def _parse_credit_multiplier(raw: object) -> float | None:
    """把模型 credits 字段解析为倍率；未配置/不合格式/异常值返回 None。

    只接受 CLI credits 字段的真实格式（见 _CREDITS_VALUE_RE 注释）：负数
    （"-0.08" / "x-0.08 credits"）、指数（"1e-3"）、嵌数字文本（"free 500"）
    一律拒绝；显式拒绝非有限值，防止 Infinity 泄漏到展示层。
    """
    if not isinstance(raw, str):
        return None
    m = _CREDITS_VALUE_RE.match(raw)
    if not m:
        return None
    try:
        value = float(m.group(1))
    except ValueError:
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return value


def _fetch_credit_multipliers() -> dict[str, float]:
    """按 CLI 的有效配置选择规则读取模型额度倍率（model id -> 倍率）。

    1. 当前账号 = settings.json JWT 的 sub；取不到 → 空 dict（无法确定当前
       配置，宁可不显示，也不用其他账号的数据猜测）；
    2. 有效配置 = cloud_product_config_cache 中 userId 精确匹配的条目
       （CLI readDiskCache 同规则）；无匹配 → 空 dict；
    3. 同一账号出现多条（CLI writer 不会产生，仅防御文件损坏）且某模型取值
       冲突时，该模型不显示——冲突不按 ts 覆盖，只保留无歧义的值；
    4. 过期语义：不额外判定陈旧——CLI 的 readDiskCache 本身不检查 ts；
       Pan 侧只由 model_credit_multipliers 的 TTL 控制重扫间隔。
    """
    user_id = _current_cbc_user_id()
    if not user_id:
        return {}
    entries = [e for e in _read_cloud_product_config_entries() if e.get("userId") == user_id]
    if not entries:
        return {}
    per_model: dict[str, set[float]] = {}
    for entry in entries:
        payload = entry.get("data")
        models = payload.get("models") if isinstance(payload, dict) else None
        if not isinstance(models, list):
            continue
        for model in models:
            if not isinstance(model, dict):
                continue
            ident = str(model.get("id") or "").strip()
            value = _parse_credit_multiplier(model.get("credits"))
            if ident and value is not None:
                per_model.setdefault(ident, set()).add(value)
    # 仅保留取值无歧义的模型（单条目时恒等价于该条目全量）。
    return {ident: next(iter(values)) for ident, values in per_model.items() if len(values) == 1}


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
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=10,
                           **command_window_options())
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
    # CBC >= 2.140: dedicated control_request, not another user/next turn.
    supports_native_steer = True
    supports_steer_receipt = True
    steer_requires_run_id = True

    def encode_control_message(self, control: dict) -> bytes:
        if control.get("type") != "steer":
            raise ValueError("CBC stream only supports Steer controls")
        text = control.get("text")
        run = control.get("expectedRequestId")
        sid = control.get("sessionId")
        request = control.get("requestId")
        if not all(isinstance(value, str) and value.strip() for value in (text, run, sid, request)):
            raise ValueError("CBC Steer requires confirmed session/run and request identity")
        return json.dumps({"type": "control_request", "request_id": request, "request": {
            "subtype": "steer", "session_id": sid, "expected_request_id": run,
            "content_blocks": [{"type": "text", "text": text}],
        }}, ensure_ascii=False).encode("utf-8")

    def normalize_steer_receipt(self, event: dict) -> dict | None:
        if event.get("type") != "control_response":
            return None
        response = event.get("response")
        if not isinstance(response, dict) or not isinstance(response.get("request_id"), str):
            return None
        payload = response.get("response")
        outcome = "unknown"
        error = "CBC Steer response unrecognized; requires CBC >= 2.140 and stream-json controls"
        sid = None
        if response.get("subtype") == "success" and isinstance(payload, dict):
            sid = payload.get("session_id")
            if payload.get("steered") is True and isinstance(sid, str) and sid:
                outcome, error = "accepted", None
            elif payload.get("steered") is False:
                outcome = "rejected"
                error = "CBC rejected Steer: " + ("stale run" if payload.get("reason") == "stale" else
                        "no injectable active run" if payload.get("reason") == "idle" else "non-injectable input")
        # A generic error could follow a partial handoff. Do not infer rejection
        # from arbitrary prose or expose raw CLI diagnostics/credentials.
        return {"type": "pan.steer_receipt", "requestId": response["request_id"],
                "ok": outcome == "accepted", "outcome": outcome, "error": error,
                "cliSessionId": sid, "consumption": "unconfirmed"}

    # cbc CLI 原生支持 --system-prompt（stream spawn 与 oneshot 路径均用）。
    supports_spawn_system_prompt = True

    # 内置兜底默认值（config.json 不存在时使用）
    _DEFAULT_MODEL = "deepseek-v4-flash"
    _DEFAULT_PERMISSION_MODE = "bypassPermissions"
    _DEFAULT_ALWAYS_THINKING_ENABLED = False
    _DEFAULT_EFFORT = ""

    @property
    def default_model(self) -> str:
        # 与 kimi/codex 同样的 stale-model guard：config.json 里的 model 可能
        # 已不在当前可选列表内（CLI 侧模型下线 / 白名单改动），仅当它确实
        # 可选时才采用，否则回退到内置有效默认值。
        model = self._cbc_config.get("model")
        if model and model in self.supported_models:
            return model
        return self._DEFAULT_MODEL

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

    _cached_models: list[str] | None = None  # class-level cache（TTL）
    _cached_models_ts: float = 0.0  # 最近一次填充/刷新的单调时钟时间戳
    _cached_multipliers: dict[str, float] | None = None  # 倍率缓存（TTL）
    _cached_multipliers_ts: float = 0.0

    @property
    def supported_models(self) -> list[str]:
        """模型列表：config.json > cbc --help 解析 > 内置默认值（TTL 缓存）。

        缓存有效期 _MODEL_CACHE_TTL 秒；超时后下次访问自动重新拉取，cbc CLI 侧
        模型变更或 config.json 白名单改动无需重启服务即可生效。
        """
        if CbcAdapter._cached_models is not None and (
            time.monotonic() - CbcAdapter._cached_models_ts
        ) < _MODEL_CACHE_TTL:
            return CbcAdapter._cached_models
        CbcAdapter._cached_models = self._fetch_models()
        CbcAdapter._cached_models_ts = time.monotonic()
        return CbcAdapter._cached_models

    @classmethod
    def invalidate_models_cache(cls) -> None:
        """清空模型列表 TTL 缓存（POST /api/config/reload 热重载用）。

        置空后下次访问 supported_models 立即重新拉取，config.json 白名单
        改动无需等 TTL 过期或重启服务。倍率缓存一并失效，保持两者同拍刷新。
        """
        cls._cached_models = None
        cls._cached_models_ts = 0.0
        cls._cached_multipliers = None
        cls._cached_multipliers_ts = 0.0

    @property
    def model_credit_multipliers(self) -> dict[str, float]:
        """模型额度消耗倍率（model id -> 倍率，如 glm-5.3-flash -> 0.08）。

        数据源与选择规则见 _fetch_credit_multipliers 注释（CLI 同源：当前
        账号 JWT sub + cloud_product_config_cache 中 userId 精确匹配的条目）。
        与 supported_models 共用 _MODEL_CACHE_TTL，不新增轮询；无可靠数据时
        返回空 dict，调用方按"无倍率"处理，不得默认 x1。

        同步读取（有界：单文件 ≤4MB）；HTTP 处理器应在线程中调用本属性，
        避免阻塞 event loop（见 server._model_credit_multipliers）。
        """
        if CbcAdapter._cached_multipliers is not None and (
            time.monotonic() - CbcAdapter._cached_multipliers_ts
        ) < _MODEL_CACHE_TTL:
            return CbcAdapter._cached_multipliers
        CbcAdapter._cached_multipliers = _fetch_credit_multipliers()
        CbcAdapter._cached_multipliers_ts = time.monotonic()
        return CbcAdapter._cached_multipliers

    def _fetch_models(self) -> list[str]:
        """按优先级拉取模型列表：config.json 白名单 > `cbc --help` 解析 > 内置默认值。"""
        # 1. config.json 显式配置
        models = self._cbc_config.get("models")
        if isinstance(models, list) and len(models) > 0:
            return [str(m) for m in models]
        # 2. 从 cbc --help 自动获取（用 node 解析的 argv，避免 .CMD shim 启动失败）
        cli_models = _parse_models_from_cbc_help(self._resolve_cbc_argv())
        if cli_models:
            return cli_models
        # 3. 硬编码默认值
        return list(self._BUILTIN_MODELS)
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

    # cbc 全部四类设置项均被真实消费：model_args / permission_mode_args /
    # thinking_args（always_thinking_enabled）/ effort_args。
    supported_settings = ["model", "permissionMode", "effort", "thinking"]

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

    def resolved_cli_argv(self) -> list[str]:
        """Return the resolved cbc executable components for preflight checks."""
        return self._resolve_cbc_argv()

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
        under the CWD-derived project).

        Does NOT re-inject --system-prompt: takeover only resumes an existing
        session (cli_session_id is already set), whose system prompt is carried
        by cbc's native context storage (JSONL). system_prompt is injected only
        once, at first spawn of a fresh session (no cli_session_id) — re-injecting
        on takeover would duplicate the system prompt as a user message after
        resume.
        """
        if not s.cli_session_id:
            return []
        cmd = self._resolve_cbc_argv()
        cmd.append("--resume")
        cmd.append(s.cli_session_id)
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
            raise

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
