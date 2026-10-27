"""Storage-free schedule normalization and legacy wall-clock calculations.

The second precision, anchor grid, timezone and misfire policy are retained for
existing callers. Canonical decimal/DST rules remain in scheduler.providers.
No registry, host, migration reader, or profile is imported here.
"""
from __future__ import annotations

import secrets
from datetime import datetime
from packages.jobs import cron

MISFIRE_POLICIES = ("fire_now", "skip")


def misfire_outcome(kind, policy, lateness, grace):
    """Shared expiry/skip/coalesce decision; callers retain their time precision."""
    if lateness <= grace:
        return None
    if kind == "once":
        return "expired"
    return "skipped" if policy == "skip" else None

ENTRY_ID_PREFIX = "sce_"
_ENTRY_CONTROL_KEYS = ("id", "nextFireAt", "lastFireAt", "enabled", "misfirePolicy")

def iso(value: datetime | None) -> str | None:
    """本地朴素 datetime → ISO-8601 字符串（秒精度）。"""
    if value is None:
        return None
    return value.replace(microsecond=0).isoformat()


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


def _new_entry_id() -> str:
    return ENTRY_ID_PREFIX + secrets.token_hex(4)


def _epoch_to_iso(value) -> str | None:
    """注册表 epoch 时间戳 → 本地朴素 ISO（task dict 出口用）。"""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return iso(datetime.fromtimestamp(float(value)))
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _normalize_schedule(raw) -> dict:
    """校验并清洗 schedule；非法一律 ValueError（API 层映射 invalid_schedule）。

    ``interval_sec`` 与 ``intervalSec`` 同时接受，落盘两个键都写。
    """
    if not isinstance(raw, dict):
        raise ValueError("schedule 必须是对象")
    kind = str(raw.get("kind") or "").strip().lower()
    if kind not in ("once", "interval", "cron"):
        raise ValueError("schedule.kind 必须是 once / interval / cron")

    out: dict = {"kind": kind}
    tz = raw.get("timezone")
    if tz:
        tz = str(tz).strip()
        if tz:
            out["timezone"] = tz

    if kind == "once":
        at = cron.parse_datetime(raw.get("at"))
        if at is None:
            raise ValueError("kind=once 需要合法的 at（ISO-8601 本地时间）")
        out["at"] = iso(at)
    elif kind == "interval":
        raw_sec = raw.get("interval_sec", raw.get("intervalSec"))
        try:
            sec = float(raw_sec)
        except (TypeError, ValueError):
            raise ValueError("kind=interval 需要 interval_sec（> 0）") from None
        if sec <= 0:
            raise ValueError(f"interval_sec 必须 > 0，收到 {raw_sec!r}")
        sec = int(sec) if float(sec).is_integer() else sec
        out["interval_sec"] = sec
        out["intervalSec"] = sec
        anchor = cron.parse_datetime(raw.get("anchor"))
        if anchor is not None:
            out["anchor"] = iso(anchor)
    else:
        expr = str(raw.get("cron") or "").strip()
        cron.parse_cron(expr)  # 非法表达式 -> ValueError
        out["cron"] = expr
    return out


def _schedule_from_entry(entry: dict) -> dict:
    """entry → PR 归一化 schedule dict（task dict 出口）。"""
    schedule = dict(entry or {})
    for key in ("id", "enabled", "nextFireAt", "lastFireAt", "misfirePolicy", "graceSec"):
        schedule.pop(key, None)
    return schedule


def _entry_from_schedule(schedule: dict, *, misfire_policy: str,
                         next_fire_at: str | None = None,
                         last_fire_at: str | None = None) -> dict:
    """归一化 schedule dict → job 记录里的 entry。"""
    return {
        "id": _new_entry_id(),
        "misfirePolicy": misfire_policy,
        "enabled": True,
        "nextFireAt": next_fire_at,
        "lastFireAt": last_fire_at,
        **dict(schedule),
    }


def effective_spec(task: dict) -> dict:
    """给 cron 求值时用的 spec：补上 interval 缺失的 anchor（= created_at）。"""
    schedule = dict(task.get("schedule") or {})
    if schedule.get("kind") == "interval" and not schedule.get("anchor"):
        created = cron.parse_datetime(task.get("created_at"))
        if created is not None:
            schedule["anchor"] = iso(created)
    return schedule


def _spec_to_entry(raw: dict, *, default_misfire: str,
                   created_iso: str) -> dict:
    """单个客户端 schedule spec → job 记录 entry（含校验与 nextFireAt 计算）。

    spec 里可选 ``misfirePolicy``（覆盖 job 级默认）与 ``enabled``（默认 True，
    entry 级启停）；``id``/``nextFireAt``/``lastFireAt`` 等控制键一律剥离。
    """
    if not isinstance(raw, dict):
        raise ValueError("schedule 列表的每一项都必须是对象")
    spec = _normalize_schedule(raw)
    if spec["kind"] == "interval" and not spec.get("anchor"):
        spec["anchor"] = created_iso
    misfire = str(raw.get("misfirePolicy") or default_misfire)
    if misfire not in MISFIRE_POLICIES:
        raise ValueError("misfirePolicy 必须是 fire_now / skip")
    clean = {k: v for k, v in spec.items() if k not in _ENTRY_CONTROL_KEYS}
    task_view = {"id": "spec", "enabled": True, "schedule": clean,
                 "created_at": created_iso}
    next_fire = compute_next_fire(task_view, _now())
    entry = _entry_from_schedule(clean, misfire_policy=misfire,
                                 next_fire_at=next_fire)
    entry["enabled"] = bool(raw.get("enabled", True))
    if "graceSec" in raw:
        grace = raw.get("graceSec")
        if isinstance(grace, bool) or not isinstance(grace, (int, float)):
            raise ValueError("graceSec must be a non-negative number")
        if not 0 <= float(grace) <= 86400:
            raise ValueError("graceSec must be between 0 and 86400")
        entry["graceSec"] = float(grace)
    return entry


def _entries_next_fire(entries: list[dict]) -> str | None:
    """全部 enabled entries 的最早 nextFireAt（无则 None）。"""
    points = [cron.parse_datetime(e.get("nextFireAt"))
              for e in entries if e.get("enabled", True)]
    points = [p for p in points if p is not None]
    return iso(min(points)) if points else None


def compute_next_fire(task: dict, after: datetime | None = None) -> str | None:
    """按锚点算出 next_fire_at（ISO 字符串或 None）。disabled 恒为 None。"""
    if not task.get("enabled"):
        return None
    when = after or _now()
    spec = effective_spec(task)
    tz = (task.get("schedule") or {}).get("timezone")
    try:
        point = cron.next_fire_after(spec, when, tz_name=tz)
    except ValueError:
        return None
    return iso(point)


def entry_next_fire(entry: dict, after: datetime) -> datetime | None:
    try:
        return cron.next_fire_after(entry, after, tz_name=entry.get("timezone"))
    except ValueError:
        return None
