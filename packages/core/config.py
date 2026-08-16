"""Pan configuration system."""

import copy
import json
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
        "model": "kimi-code/kimi-for-coding",
        "permission_mode": "",
        "always_thinking_enabled": False,
        "effort": "",
    },
    # 前端模式："coexist"（默认，/ 旧前端 + /react/ React）、"react"（/ React）、"legacy"（仅旧前端）
    "frontend": "coexist",
    # 服务端口（环境变量 PAN_PORT 可覆盖）
    "port": 8767,
    # Remote 通道默认配置
    "remote": {
        "enabled": False,
        "provider": "cloudflare",
        "quick_tunnel": True,
        "config_path": "",
        "binary_path": "",
        "status_port": 8769,
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
