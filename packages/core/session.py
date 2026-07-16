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
    cbc_session_id: str | None = None
    adapter: str = "cbc"   # CLI adapter name, default "cbc" (backward compatible)
    model: str | None = None
    permission_mode: str | None = None
    always_thinking_enabled: bool = False
    effort: str = ""
    max_thinking_tokens: int | None = None
    raw_usage: dict | None = None
    total_usage: dict | None = None
    workdir: str = ""
    history: list[dict] = field(default_factory=list)
    last_result: dict | None = None
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()
        if not self.updated_at:
            self.updated_at = self.created_at

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "cbc_session_id": self.cbc_session_id,
            "adapter": self.adapter,
            "model": self.model,
            "permission_mode": self.permission_mode,
            "always_thinking_enabled": self.always_thinking_enabled,
            "effort": self.effort,
            "max_thinking_tokens": self.max_thinking_tokens,
            "raw_usage": self.raw_usage,
            "total_usage": self.total_usage,
            "workdir": self.workdir,
            "history": self.history,
            "last_result": self.last_result,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# ── in-memory cache ──
_cache: dict[str, Session] = {}


# ── CRUD ──

def create(name: str, model: str | None = None,
           permission_mode: str | None = None,
           always_thinking_enabled: bool = False,
           effort: str = "",
           max_thinking_tokens: int | None = None,
           raw_usage: dict | None = None,
           total_usage: dict | None = None,
           workdir: str = "",
           cbc_session_id: str | None = None,
           history: list[dict] | None = None) -> Session:
    s = Session(
        id=_new_id(),
        name=name,
        cbc_session_id=cbc_session_id,
        model=model,
        permission_mode=permission_mode,
        always_thinking_enabled=always_thinking_enabled,
        effort=effort,
        max_thinking_tokens=max_thinking_tokens,
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
        s = Session(**data)
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
                            s = Session(**data)
                            _migrate_session_usage(s)
                            _cache[sid] = s
                    except (json.JSONDecodeError, OSError):
                        pass
        _all_loaded = True
    # after initial load, cache is always current (create/save/delete sync it)
    return sorted(_cache.values(), key=lambda s: s.created_at)


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

    返回 {"prompt_tokens": int, "cache_hit_tokens": int, "cache_miss_tokens": int, "credit": float}
    或 None（raw_usage 为空时）。
    """
    if not raw_usage:
        return None
    total = {"prompt_tokens": 0, "cache_hit_tokens": 0, "cache_miss_tokens": 0, "credit": 0.0}
    for entry in raw_usage.values():
        ru = entry.get("rawUsage", {})
        total["prompt_tokens"] += ru.get("prompt_tokens", 0)
        total["cache_hit_tokens"] += ru.get("prompt_cache_hit_tokens", 0)
        total["cache_miss_tokens"] += ru.get("prompt_cache_miss_tokens", 0)
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
