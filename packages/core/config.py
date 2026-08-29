"""Pan configuration system."""

import copy
import json
import os
from pathlib import Path

CONFIG_FILE = Path(__file__).resolve().parent.parent.parent / "config.json"

DEFAULT_CONFIG: dict = {
    "cbc_import": {
        "min_message_count": 5,
        "max_sessions_shown": 30,
        "exclude_workdir_patterns": [],
        "project_dir_exact_match": False,
        "import_recent_days": 30,
        "min_resume_bytes": 200,
    },
    "cbc": {
        # 默认模型。可选值参见 adapter.py 的 supported_models
        "model": "deepseek-v4-flash",
        # 默认权限模式："" | "default" | "acceptEdits" | "bypassPermissions" | "plan" | "dontAsk" | "auto"
        "permission_mode": "bypassPermissions",
        # 默认是否开启 thinking（cbc alwaysThinkingEnabled）
        "always_thinking_enabled": False,
        # 默认 effort 级别："" | "none" | "off" | "auto" | "low" | "medium" | "high" | "xhigh" | "max" | "ultracode"
        "effort": "",
    },
    # Worker 生命周期管理
    "worker": {
        # 静默超时（秒）：running/queued 状态下持续无任何 stdout 输出超过该值时 kill。
        # stream 模式由 watchdog 判定；MCP one-shot 模式由读取超时承担（同一配置）。
        "timeout_sec": 300,
        # 空闲回收（秒）：idle 状态（任务完成）持续超过该值时回收进程。held/zombie 跳过。
        "idle_sec": 300,
    },
    # 本地日志（main.py 启动时配置）：文件大小/天轮转 + console 双输出
    "logging": {
        # 日志级别：DEBUG/INFO/WARNING/ERROR
        "level": "INFO",
        # 日志文件路径（相对路径以仓库根为基准）
        "file": "data/logs/pan.log",
        # 单文件大小上限（字节），超过即轮转
        "max_bytes": 10485760,
        # 保留的轮转日志份数
        "backup_count": 7,
        # 是否同时在控制台输出
        "console": True,
    },
    "kimi": {
        "model": "moonshot-cn/kimi-k2.6",
        "permission_mode": "",
        "always_thinking_enabled": False,
        "effort": "",
    },
    "claude": {
        # 默认模型。空字符串 = 不传 --model，由 claude 用其配置的默认模型
        # （如 claude-opus-4-8）。显式设置后传给 `claude --model`。
        "model": "",
        # 默认权限模式："" | "default" | "acceptEdits" | "bypassPermissions" | "plan"。
        # 非交互模式下默认 bypassPermissions 以避免权限确认挂起。
        "permission_mode": "bypassPermissions",
        # claude 在 -p + --verbose 下自动产出 thinking 块，无独立开关。
        "always_thinking_enabled": False,
        # 默认 reasoning effort（--effort）："" | low | medium | high | xhigh | max
        "effort": "",
    },
    "opencode": {
        # 默认模型（provider/model）。可选值见 adapter.py 的 supported_models
        # （`opencode models` 解析 > 内置）。无配置时回退 opencode/big-pickle（免费、无需 key）。
        "model": "opencode/big-pickle",
        # 默认权限模式：""（沿用 opencode.json 配置）| "auto"（--auto 绕过 ask）
        "permission_mode": "",
        # 默认是否开启 thinking 显示（--thinking）
        "always_thinking_enabled": False,
        # 默认 reasoning effort（--variant）："" | minimal | low | medium | high | max
        "effort": "",
    },
    "codex": {
        # 默认模型。可选值见 adapter.py 的 supported_models；
        # 不填时自动识别（读 CODEX_HOME/models_cache.json / config.toml，否则兜底）。
        "model": "deepseek-ai/DeepSeek-V4-Flash",
        # 可选模型白名单（填=限制可选项，不填=自动识别）
        "models": [],
        # 默认权限模式：""（沿用 codex config）| "read-only" | "workspace-write" |
        # "bypass"（--dangerously-bypass-approvals-and-sandbox）| "approve"（自动批准）
        "permission_mode": "bypass",
        # 默认 reasoning effort（-c model_reasoning_effort）："" | low | medium | high
        "effort": "",
    },
    # 前端模式："coexist"（默认，/ 旧前端 + /react/ React）、"react"（/ React）、"legacy"（仅旧前端）
    "frontend": "coexist",
    # 服务端口（环境变量 PAN_PORT 可覆盖）
    "port": 8768,
    # Remote 通道默认配置
    "remote": {
        "enabled": False,
        "provider": "cloudflare",
        "quick_tunnel": True,
        "config_path": "",
        "binary_path": "",
        "status_port": 8769,
    },
    # QQ 模块（packages/qq/bot.py，NoneBot，跑在独立解释器上）
    "qq": {
        # QQ bot 独立解释器路径（单一事实源）。空串 = 走默认解析链：
        # PAN_QQ_PYTHON 环境变量 > 此处 > 平台默认（nt: E盘 miniforge / POSIX: python3）。
        # scripts/setup.bat 首次运行时会把探测结果写入此处。
        "python": "",
    },
    # 前端 App 设置（config.json 为单一真源，跨浏览器/会话一致）。
    # 由 GET/PUT /api/settings/ui 读写，前端 appSettingsStore 消费。
    "ui": {
        # 会话列表默认分组方式
        "defaultGroupBy": "none",
        # 是否显示 meta-agent（////by agent）消息
        "showMetaAgent": True,
        # 是否显示 task-agent（@@@@by agent）消息
        "showTaskAgent": True,
        # 是否显示 QQ（@@@@by qq）注入消息
        "showQQ": True,
    },
}


def load_config() -> dict:
    """Load configuration from config.json, deep-merged with defaults."""
    if not CONFIG_FILE.exists():
        return copy.deepcopy(DEFAULT_CONFIG)
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        user_config = json.load(f)
    return _deep_merge(DEFAULT_CONFIG, user_config)


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base, returning a new dict."""
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def read_config_file() -> dict:
    """Read the raw on-disk config.json WITHOUT merging defaults.

    Returns {} when the file is missing or not a JSON object — callers that
    want to preserve the exact file contents while updating a single top-level
    key (e.g. the settings API) should read through here and write back with
    save_config().
    """
    if not CONFIG_FILE.exists():
        return {}
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def save_config(config: dict) -> None:
    """Persist `config` to config.json atomically (tmp + os.replace).

    The dict is written exactly as given — pass a merged ``load_config()``
    result to materialize every default, or a raw on-disk dict (from
    ``read_config_file()``) to change only the keys you touched. Writing to a
    temp file then ``os.replace``-ing it in means a crash mid-write never
    corrupts config.json.
    """
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = CONFIG_FILE.with_suffix(".json.tmp")
    tmp_path.write_text(
        json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp_path, CONFIG_FILE)
