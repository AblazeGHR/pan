"""Session store — persistent, UUID-keyed, independent of Worker lifecycle.

Each session is stored as data/sessions/<id>.json.
The ID format is ses_<16-hex-chars> (e.g. ses_a1b2c3d4e5f67890).
"""

from __future__ import annotations

import asyncio
import json
import secrets
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path

SESSION_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "sessions"


def _path(session_id: str) -> Path:
    return SESSION_DIR / f"{session_id}.json"


def _new_id() -> str:
    return "ses_" + secrets.token_hex(8)


@dataclass
class Session:
    id: str
    name: str
    adapter: str = "cbc"   # CLI adapter name, default "cbc"
    model: str | None = None
    permission_mode: str | None = None
    role: str = "default"  # Pan-internal boundary role (e.g. "meta-agent"), default "default"
    adapter_config: dict = field(default_factory=dict)  # adapter-specific settings
    character_id: str | None = None   # bound character ID (for memory + system_prompt)
    system_prompt: str | None = None  # injected at Worker spawn
    game_id: str | None = None        # RuleWhisper game identifier for MCP tool calls
    raw_usage: dict | None = None
    total_usage: dict | None = None
    workdir: str = ""
    history: list[dict] = field(default_factory=list)
    last_result: dict | None = None
    created_at: str = ""
    updated_at: str = ""
    managed: list[str] = field(default_factory=list)  # session ids this session manages
    managed_by: str | None = None  # session id of the session managing this one
    queue_pending: list = field(default_factory=list)  # persisted message queue (for report consumption)
    report_subscriptions: set[str] = field(default_factory=set)  # managed sessions whose completion reports this session subscribes to

    # ── adapter_config convenience accessors ──

    @property
    def cli_session_id(self) -> str | None:
        """Adapter-native session ID (for --resume, --continue, etc.)."""
        return self.adapter_config.get("cli_session_id")

    @cli_session_id.setter
    def cli_session_id(self, value: str | None):
        if value:
            self.adapter_config["cli_session_id"] = value
        else:
            self.adapter_config.pop("cli_session_id", None)

    def adapter_field(self, key: str, default=None):
        """Read a value from adapter_config."""
        return self.adapter_config.get(key, default)

    def set_adapter_field(self, key: str, value):
        """Set a value in adapter_config in-place."""
        if value is not None and value != "" and value is not False:
            self.adapter_config[key] = value
        else:
            self.adapter_config.pop(key, None)

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        if not self.updated_at:
            self.updated_at = self.created_at
        # 落盘 JSON 里 set 序列化为 list → 读回时还原
        if isinstance(self.report_subscriptions, (list, tuple)):
            self.report_subscriptions = set(self.report_subscriptions)
        # migrate any legacy top-level fields that ended up on the instance
        # (from Session(**data) with old JSON having cbc_session_id, etc.)
        _migrate_legacy_fields(self)

    @classmethod
    def _from_data(cls, data: dict) -> Session:
        """Construct Session from legacy or new JSON data.

        Pops legacy adapter-specific fields from data and puts
        them into adapter_config before constructing the instance.
        """
        ac = data.pop("adapter_config", {}) or {}
        for old_key, new_key in [
            ("cbc_session_id", "cli_session_id"),
            ("always_thinking_enabled", "always_thinking_enabled"),
            ("effort", "effort"),
            ("max_thinking_tokens", "max_thinking_tokens"),
        ]:
            val = data.pop(old_key, None)
            if val is not None and val != "" and val is not False:
                ac[new_key] = val
        data["adapter_config"] = ac
        return cls(**data)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "adapter": self.adapter,
            "model": self.model,
            "permission_mode": self.permission_mode,
            "role": self.role,
            "adapter_config": self.adapter_config,
            "character_id": self.character_id,
            "system_prompt": self.system_prompt,
            "game_id": self.game_id,
            "raw_usage": self.raw_usage,
            "total_usage": self.total_usage,
            "workdir": self.workdir,
            "history": self.history,
            "last_result": self.last_result,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "managed": self.managed,
            "managed_by": self.managed_by,
            "queue_pending": self.queue_pending,
            "report_subscriptions": sorted(self.report_subscriptions),
        }


# ── in-memory cache ──
_cache: dict[str, Session] = {}


# ── CRUD ──

def create(name: str, model: str | None = None,
           permission_mode: str | None = None,
           role: str = "default",
           adapter: str = "cbc",
           adapter_config: dict | None = None,
           raw_usage: dict | None = None,
           total_usage: dict | None = None,
           workdir: str = "",
           history: list[dict] | None = None,
           character_id: str | None = None,
           system_prompt: str | None = None,
           game_id: str | None = None,
           # backward-compat kwargs (migrated to adapter_config)
           cli_session_id: str | None = None,
           always_thinking_enabled: bool = False,
           effort: str = "",
           max_thinking_tokens: int | None = None) -> Session:
    # build adapter_config
    ac = dict(adapter_config) if adapter_config else {}
    if cli_session_id and "cli_session_id" not in ac:
        ac["cli_session_id"] = cli_session_id
    if always_thinking_enabled and "always_thinking_enabled" not in ac:
        ac["always_thinking_enabled"] = True
    if effort and "effort" not in ac:
        ac["effort"] = effort
    if max_thinking_tokens and "max_thinking_tokens" not in ac:
        ac["max_thinking_tokens"] = max_thinking_tokens

    s = Session(
        id=_new_id(),
        name=name,
        adapter=adapter,
        model=model,
        permission_mode=permission_mode,
        role=role,
        adapter_config=ac,
        character_id=character_id,
        system_prompt=system_prompt,
        game_id=game_id,
        raw_usage=raw_usage,
        total_usage=total_usage,
        workdir=workdir,
        history=history or [],
    )
    save(s)
    _cache[s.id] = s
    return s


def get(session_id: str) -> Session | None:
    if session_id in _cache:
        return _cache[session_id]
    path = _path(session_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        s = Session._from_data(data)
        _migrate_legacy_fields(s)
        _migrate_session_usage(s)
        _cache[session_id] = s
        return s
    except (json.JSONDecodeError, OSError):
        return None


def _save_sync(s: Session):
    s.updated_at = datetime.now().isoformat()
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    _path(s.id).write_text(
        json.dumps(s.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8")
    _cache[s.id] = s


def save(s: Session):
    """Sync save (for low-frequency server API calls)."""
    _save_sync(s)


async def save_async(s: Session):
    """Async save (for high-frequency worker stdout/consumer calls)."""
    await asyncio.to_thread(_save_sync, s)


def delete(session_id: str):
    path = _path(session_id)
    if path.exists():
        path.unlink()
    _cache.pop(session_id, None)


def claim(manager_id: str, session_id: str) -> str | None:
    """Set a bidirectional managed relationship (立项 4.2).

    Establishes: manager.managed += [session_id], session.managed_by = manager_id.

    Refuses (returns an error string) if the target session is already managed
    by a different session. No-op success when the relationship already holds.

    Returns None on success, or an error message string on refusal.
    """
    manager = get(manager_id)
    if manager is None:
        return f"Manager session {manager_id} not found"
    target = get(session_id)
    if target is None:
        return f"Session {session_id} not found"
    if target.managed_by and target.managed_by != manager_id:
        return f"Session {session_id} is managed by {target.managed_by}, not {manager_id}"
    if session_id not in manager.managed:
        manager.managed.append(session_id)
        save(manager)
    if target.managed_by != manager_id:
        target.managed_by = manager_id
        save(target)
    return None


def release(session_id: str) -> str | None:
    """Remove the managed relationship pointing at session_id.

    Called when a session is deleted: the managing session's `managed` list is
    cleaned up so it doesn't reference a deleted session (立项 待实现 #3).

    Returns None on success, or an error message string.
    """
    target = get(session_id)
    if target is None:
        return None  # nothing to clean up
    manager_id = target.managed_by
    if not manager_id:
        return None
    manager = get(manager_id)
    if manager is not None and session_id in manager.managed:
        manager.managed.remove(session_id)
        save(manager)
    target.managed_by = None
    save(target)
    return None


_all_loaded: bool = False


def list_all() -> list[Session]:
    global _all_loaded
    if not _all_loaded:
        if SESSION_DIR.exists():
            for f in sorted(SESSION_DIR.iterdir()):
                if f.suffix == ".json":
                    try:
                        data = json.loads(f.read_text(encoding="utf-8"))
                        sid = data.get("id")
                        # 不覆盖已缓存的 Session（worker 可能在 _read_stdout
                        # 里 append 了 history 但还没 save，磁盘版本更旧）
                        if sid and sid not in _cache:
                            s = Session._from_data(data)
                            _migrate_legacy_fields(s)
                            _migrate_session_usage(s)
                            _cache[sid] = s
                    except (json.JSONDecodeError, OSError):
                        pass
        _all_loaded = True
    # after initial load, cache is always current (create/save/delete sync it)
    return sorted(_cache.values(), key=lambda s: s.created_at)


# ── migration helpers ──

def _migrate_legacy_fields(s: Session):
    """Migrate old top-level adapter-specific fields into adapter_config."""
    changed = False
    # if old-style fields exist as attributes, move them to adapter_config
    legacy_map = {
        "cbc_session_id": "cli_session_id",
        "always_thinking_enabled": "always_thinking_enabled",
        "effort": "effort",
        "max_thinking_tokens": "max_thinking_tokens",
    }
    for old_key, new_key in legacy_map.items():
        value = getattr(s, old_key, None)
        if value and new_key not in s.adapter_config:
            s.adapter_config[new_key] = value
            changed = True
    if changed:
        _save_sync(s)


def _deep_sum_raw_usage(a: dict, b: dict) -> dict:
    """递归累加两个 rawUsage dict 中所有数值字段。"""
    result = dict(a)
    for k, v in b.items():
        if k not in result:
            result[k] = v
        elif isinstance(v, dict) and isinstance(result[k], dict):
            result[k] = _deep_sum_raw_usage(result[k], v)
        elif isinstance(v, (int, float)) and isinstance(result[k], (int, float)):
            result[k] += v
    return result


def accumulate_raw_usage(existing: dict | None, entries: list[dict]) -> dict:
    """将 raw_usage 条目按 model 累加，返回 {model: {model, request_count, rawUsage}}。

    existing: 已有的累加结果（dict keyed by model），None 表示无
    entries:  待合并的条目列表，每项 {"model": str, "rawUsage": dict, ...}
    """
    result: dict = dict(existing) if existing else {}
    for entry in entries:
        model = entry.get("model", "unknown")
        ru = entry.get("rawUsage")
        if not ru:
            continue
        if model in result:
            result[model]["rawUsage"] = _deep_sum_raw_usage(result[model]["rawUsage"], ru)
            result[model]["request_count"] += 1
        else:
            result[model] = {
                "model": model,
                "request_count": 1,
                "rawUsage": ru,
            }
    return result


def compute_total_usage(raw_usage: dict | None) -> dict | None:
    """从按模型累加的 raw_usage 汇总累计消耗。

    返回 {"prompt_tokens": int, "cache_hit_tokens": int, "cache_miss_tokens": int,
          "completion_tokens": int, "credit": float}
    或 None（raw_usage 为空时）。
    """
    if not raw_usage:
        return None
    total = {"prompt_tokens": 0, "cache_hit_tokens": 0, "cache_miss_tokens": 0,
             "completion_tokens": 0, "credit": 0.0}
    for entry in raw_usage.values():
        ru = entry.get("rawUsage", {})
        total["prompt_tokens"] += ru.get("prompt_tokens", 0)
        total["cache_hit_tokens"] += ru.get("prompt_cache_hit_tokens", 0)
        total["cache_miss_tokens"] += ru.get("prompt_cache_miss_tokens", 0)
        total["completion_tokens"] += ru.get("completion_tokens", 0)
        total["credit"] += ru.get("credit", 0)
    return total


def _migrate_session_usage(s: Session):
    """Migrate legacy list-format raw_usage to dict + compute total_usage."""
    if isinstance(s.raw_usage, list):
        s.raw_usage = accumulate_raw_usage(None, s.raw_usage)
    if s.total_usage is None and isinstance(s.raw_usage, dict):
        s.total_usage = compute_total_usage(s.raw_usage)


def clear_cache():
    _cache.clear()
