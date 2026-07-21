"""Pan configuration system."""

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
    # 服务端口（环境变量 CLICONDUCTOR_PORT 可覆盖）
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
        return dict(DEFAULT_CONFIG)
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
