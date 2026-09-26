"""Read-only projection of Pan's registered persistent storage locations.

This module intentionally reports registered roots only. It never walks a
directory, reads a config/credential file, creates a missing directory, or
accepts caller-supplied paths.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = PROJECT_ROOT / "data"


def _resolved(value: str | Path) -> Path:
    return Path(value).expanduser().resolve(strict=False)


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def _path(label: str, value: str | Path, *, source: str = "default",
          overridden: bool = False, protected_external: bool = False) -> dict:
    resolved = _resolved(value)
    return {
        "label": label,
        "path": str(resolved),
        "exists": _exists(resolved),
        "source": source,
        "overridden": overridden,
        "external": protected_external or not _is_relative_to(resolved, _resolved(PROJECT_ROOT)),
    }


def _jobs_root() -> tuple[Path, str, bool]:
    # Keep the same precedence as packages.scheduler.store.data_root(), which
    # is also the root used by the unified /api/jobs API.
    for name in ("PAN_SCHEDULER_DIR", "PAN_BACKGROUND_JOBS_DIR"):
        raw = os.environ.get(name)
        if raw and raw.strip():
            return _resolved(raw), f"environment: {name}", True
    return _resolved(DATA_ROOT / "background_jobs"), "default", False


def _background_jobs_root() -> tuple[Path, str, bool]:
    raw = os.environ.get("PAN_BACKGROUND_JOBS_DIR")
    if raw and raw.strip():
        return _resolved(raw), "environment: PAN_BACKGROUND_JOBS_DIR", True
    return _resolved(DATA_ROOT / "background_jobs"), "default", False


def _job_storage_paths(suffix: str) -> list[dict]:
    """Project each distinct root used by the current Job entry points."""
    unified, unified_source, unified_overridden = _jobs_root()
    background, background_source, background_overridden = _background_jobs_root()
    candidates = [
        ("Jobs / Scheduler API", unified / suffix, unified_source, unified_overridden),
        ("Background Job API", background / suffix, background_source, background_overridden),
        ("Lifecycle Job registry", DATA_ROOT / "background_jobs" / suffix, "fixed lifecycle root", False),
    ]
    rows: dict[str, dict] = {}
    for label, value, source, overridden in candidates:
        resolved = _resolved(value)
        key = str(resolved)
        existing = rows.get(key)
        if existing:
            existing["label"] += f" / {label}"
        else:
            rows[key] = _path(label, resolved, source=source, overridden=overridden)
    return list(rows.values())


def _logging_path() -> tuple[Path, str, bool]:
    # The active file handler is the authoritative effective path and avoids
    # reading config.json (which may contain credentials). Before logging is
    # configured, expose the documented default without touching config.
    for handler in logging.getLogger().handlers:
        filename = getattr(handler, "baseFilename", None)
        if filename:
            resolved = _resolved(filename)
            default = _resolved(DATA_ROOT / "logs" / "pan.log")
            return resolved, "active logging handler", resolved != default
    return _resolved(DATA_ROOT / "logs" / "pan.log"), "documented default", False


def _category(category_id: str, name: str, purpose: str, status: str,
              paths: list[dict], *, note: str = "") -> dict:
    return {
        "id": category_id,
        "name": name,
        "purpose": purpose,
        "policyStatus": status,
        "paths": paths,
        "note": note,
    }


def get_data_catalog() -> dict:
    """Return names, purposes and resolved paths for known storage classes."""
    jobs_root, jobs_source, jobs_overridden = _jobs_root()
    quota_raw = os.environ.get("PAN_CODEX_QUOTA_DIR")
    quota_root = _resolved(quota_raw) if quota_raw else _resolved(DATA_ROOT / "codex" / "quota")
    codex_home_raw = os.environ.get("CODEX_HOME")
    codex_home = _resolved(codex_home_raw) if codex_home_raw else _resolved(Path.home() / ".codex")
    kimi_home_raw = os.environ.get("KIMI_CODE_HOME")
    kimi_home = _resolved(kimi_home_raw) if kimi_home_raw else _resolved(Path.home() / ".kimi-code")
    wechat_raw = os.environ.get("PAN_WECHAT_DATA_DIR")
    wechat_root = _resolved(wechat_raw) if wechat_raw else _resolved(DATA_ROOT)
    log_path, log_source, log_overridden = _logging_path()

    managed = "data_retention_policy"
    protected = "not_auto_cleanable"
    categories = [
        _category("sessions-history", "Sessions 元数据与 history", "Session JSON、history JSONL、queue_pending 与投递状态。", managed,
                  [_path("Sessions 与 history 目录", DATA_ROOT / "sessions")],
                  note="独立保留策略；活跃 Worker、队列、关系和任务引用会阻止到期删除。"),
        _category("attachments", "附件上传与 sidecar", "上传文件及 session-scoped .attachments.json 引用清单。", managed,
                  [_path("上传与 sidecar 根目录", DATA_ROOT / "attachments")],
                  note="独立于 Session/history；只清理登记的普通上传文件，历史链接到期后可能失效。"),
        _category("jobs-records", "Jobs 记录", "统一 Jobs JSON 记录及 schedule task 数据。", "jobs_api_managed",
                  _job_storage_paths("jobs"),
                  note="保留规则由 Jobs completed-retention API/config 管理；Data 策略不保存 Jobs 规则。"),
        _category("jobs-logs", "Jobs 日志", "后台 Job 的逐项日志文件。", "jobs_api_managed",
                  _job_storage_paths("logs")),
        _category("jobs-runs", "Jobs runs 与模板", "runs.jsonl 执行历史、自定义 schedule 模板和旧 scheduler 迁移目录。", "jobs_api_managed",
                  _job_storage_paths("runs.jsonl") + _job_storage_paths("schedule_templates.json") +
                  [_path("旧 scheduler 迁移目录", DATA_ROOT / "scheduler")]),
        _category("workspaces", "Workspaces", "Workspace 元数据；其 dirs 字段只是外部目录引用。", protected,
                  [_path("Workspace 元数据目录", DATA_ROOT / "workspaces")],
                  note="不会跟随 Workspace 引用遍历或清理 dirs。"),
        _category("workdirs", "Pan 默认 workdirs", "Pan 为 Session 默认创建的工作目录根。", "session_lifecycle_cleanup",
                  [_path("默认 workdirs 根目录", DATA_ROOT / "workdirs")],
                  note="只在 Session 到期且目录可证明专属、未被其他 Session 或 Workspace 引用时，随该 Session 删除。"),
        _category("qq-history", "QQ history", "QQ 对话历史 JSON。", managed,
                  [_path("QQ history 目录", DATA_ROOT / "qq_history")]),
        _category("qq-inbox", "QQ inbox", "selective 模式的待处理 QQ 消息。", protected,
                  [_path("QQ inbox 目录", DATA_ROOT / "qq_inbox")],
                  note="待处理 inbox 永远不会自动删除。"),
        _category("qq-media", "QQ media", "QQ 入站消息下载的媒体文件。", managed,
                  [_path("QQ media 目录", DATA_ROOT / "qq_media")]),
        _category("wechat-history-queues", "WeChat history 与 queues", "WeChat 对话历史、待处理 inbox 与发送失败 outbox。", protected,
                  [_path("WeChat history 目录", wechat_root / "wechat_history",
                         source="environment: PAN_WECHAT_DATA_DIR" if wechat_raw else "default",
                         overridden=bool(wechat_raw)),
                   _path("WeChat inbox 目录", wechat_root / "wechat_inbox",
                         source="environment: PAN_WECHAT_DATA_DIR" if wechat_raw else "default",
                         overridden=bool(wechat_raw)),
                   _path("WeChat outbox 目录", wechat_root / "wechat_outbox",
                         source="environment: PAN_WECHAT_DATA_DIR" if wechat_raw else "default",
                         overridden=bool(wechat_raw))]),
        _category("characters-memory", "Characters 与 memory", "Character JSON 和角色索引 SQLite。", protected,
                  [_path("Characters 目录", DATA_ROOT / "characters"),
                   _path("Memory 数据库目录", DATA_ROOT / "memory")],
                  note="Character 引用的 memory_dir 是外部知识文件目录，不在本目录清单内。"),
        _category("mcp-config", "MCP per-session config", "Session 隔离的 MCP 配置文件；内容不读取、不回显。", "session_lifecycle_cleanup",
                  [_path("MCP config 目录", DATA_ROOT / "mcp-configs")],
                  note="仅随已安全删除的 Session 清理对应 ID 文件；全局配置与凭据不纳入。"),
        _category("kimi-isolated-home", "Kimi isolated HOME", "Pan 为 Kimi Session 创建的隔离 HOME。", "session_lifecycle_cleanup",
                  [_path("Kimi isolated HOME 根目录", DATA_ROOT / "kimi-homes")],
                  note="仅随已安全删除的 Session 清理对应 ID 子目录；provider HOME 不纳入。"),
        _category("codex-quota-cache", "Codex quota cache", "Pan 的 Codex quota 缓存，不包含 Codex CLI 原始凭据。", protected,
                  [_path("quota cache 目录", quota_root,
                         source="environment: PAN_CODEX_QUOTA_DIR" if quota_raw else "default",
                         overridden=bool(quota_raw))]),
        _category("pan-logs", "Pan logs", "Pan 主日志及大小/日期轮转日志；当前活动文件仍由进程写入。", managed,
                  [_path("当前日志文件", log_path, source=log_source, overridden=log_overridden),
                   _path("轮转日志目录", log_path.parent, source=log_source, overridden=log_overridden)],
                  note="仅清理固定 data/logs 内、与当前日志同名的过期轮转普通文件；活动文件和外部日志路径保留。"),
        _category("startup-recovery", "Startup recovery", "Pan 启动恢复候选、claim 与 decision 快照。", protected,
                  [_path("startup recovery 目录", DATA_ROOT / "startup_recovery")]),
        _category("retention-status", "Data 清理扫描状态", "最近扫描、删除与跳过计数；仅保存汇总，不含文件内容。", protected,
                  [_path("扫描状态文件", DATA_ROOT / "retention" / "status.json")]),
        _category("config", "config.json", "Pan 项目配置；只投影路径，不读取配置内容。", protected,
                  [_path("Pan config.json", PROJECT_ROOT / "config.json")]),
        _category("external-provider-auth", "外部 provider 与 auth 数据", "Codex/Kimi CLI 用户目录、QQ 环境配置、WeChat token 等外部或凭据数据。", protected,
                  [_path("Codex CLI HOME", codex_home,
                         source="environment: CODEX_HOME" if codex_home_raw else "platform default",
                         overridden=bool(codex_home_raw), protected_external=True),
                   _path("Kimi CLI HOME", kimi_home,
                         source="environment: KIMI_CODE_HOME" if kimi_home_raw else "platform default",
                         overridden=bool(kimi_home_raw), protected_external=True),
                   _path("QQ .env（凭据可能存在）", PROJECT_ROOT / "packages" / "qq" / ".env",
                         source="code location"),
                   _path("WeChat bot token 文件（内容不读取）", wechat_root / "wechat" / "bot_token.json",
                         source="environment: PAN_WECHAT_DATA_DIR" if wechat_raw else "default",
                         overridden=bool(wechat_raw)),
                   _path("WeChat context_tokens.json（内容不读取）", wechat_root / "wechat" / "context_tokens.json",
                         source="environment: PAN_WECHAT_DATA_DIR" if wechat_raw else "default",
                         overridden=bool(wechat_raw)),
                   _path("WeChat login_qrcode.txt（内容不读取）", wechat_root / "wechat" / "login_qrcode.txt",
                         source="environment: PAN_WECHAT_DATA_DIR" if wechat_raw else "default",
                         overridden=bool(wechat_raw))],
                  note="Claude/OpenCode 等 CLI 的用户目录由其自身配置决定；Pan 不扫描或展示其目录内容。"),
    ]

    return {
        "categories": categories,
        "notice": (
            "仅列出代码登记的路径，不递归浏览目录、不读取文件内容或统计全盘。"
            "data/ 下其他用户自建目录未登记，也不属于任何自动清理目标。"
        ),
        "jobsRetention": {
            "slot": "jobs-retention-control",
            "status": "reserved",
            "message": "Jobs 保留规则由 /api/jobs/settings/completed-retention 和 config.jobs 管理；此处预留可复用设置组件槽位。",
        },
    }
