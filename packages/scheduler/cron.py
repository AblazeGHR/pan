"""定时规则求值 —— 纯函数、零 I/O、零全局状态。

本模块只做「给一个 schedule spec 和一个时刻，算出下一个触发点」这一件事：
不读文件、不碰网络、不持有可变模块状态（唯一的 ``lru_cache`` 只是时区对象的
确定性记忆化，不改变任何返回值）。因此它极易单测，也永远不会被 Pan 的
进程生命周期拖累。

支持的 ``schedule.kind``（见 PLAN_SCHEDULER.md §2.1 / §3.1）::

    once      指定时间点，过期即 None
    interval  anchor + k * interval_sec，锚点不漂移
    cron      5 段表达式，在 schedule.timezone 的墙钟上求值

时间约定：一律**本地朴素 datetime**（与 ``packages/core/session.py`` 一致）。
时区只影响 cron 的墙钟求值，不编码进返回值。

cron 求值按「天 → 小时 → 分钟」三级候选推进，**绝不逐分钟步进**；
搜索上限 366 天，超出返回 ``None``。
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from functools import lru_cache

_log = logging.getLogger(__name__)

#: 5 段表达式的字段名与取值区间（dow 允许 0-7，0/7 均为周日）
_FIELD_SPECS: tuple[tuple[str, int, int], ...] = (
    ("minute", 0, 59),
    ("hour", 0, 23),
    ("dom", 1, 31),
    ("month", 1, 12),
    ("dow", 0, 7),
)

#: cron 求值向前搜索的天数上限（超过即认为无解）
MAX_SEARCH_DAYS = 366

#: next_n 的预览条数上限
MAX_PREVIEW = 20

_KINDS = ("once", "interval", "cron")


# ── 公共入口 ──


def parse_cron(expr: str) -> dict:
    """把 5 段 cron 表达式解析成字段取值集合。

    Args:
        expr: ``"分 时 日 月 周"``，支持 ``*``、``*/n``、``a-b``、``a-b/n``、
            ``a,b`` 与单值；dow 取值 0-7 且 0/7 均为周日。

    Returns:
        ``{"minute": set[int], "hour": set[int], "dom": set[int],
        "month": set[int], "dow": set[int]}``。dow 已把 7 归一成 0。

    Raises:
        ValueError: 段数不是 5、字段越界、步长非正或语法无法识别。
    """
    if not isinstance(expr, str):
        raise ValueError(f"cron 表达式必须是字符串，收到 {type(expr).__name__}")
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError(
            f"cron 表达式必须是 5 段（分 时 日 月 周），收到 {len(parts)} 段：{expr!r}"
        )
    spec: dict[str, set[int]] = {}
    for (name, lo, hi), raw in zip(_FIELD_SPECS, parts):
        values = _parse_field(raw, lo, hi, name)
        if name == "dow":
            values = {0 if v == 7 else v for v in values}
        spec[name] = values
    return spec


def next_fire_after(spec: dict, after: datetime,
                    tz_name: str | None = None) -> datetime | None:
    """返回严格晚于 ``after`` 的下一个触发点。

    Args:
        spec: 整个 schedule dict（``kind`` / ``at`` / ``interval_sec`` /
            ``anchor`` / ``cron`` / ``timezone``）。
        after: 基准时刻（本地朴素 datetime；aware 会先折成本地朴素）。
        tz_name: 覆盖 ``spec["timezone"]``。仅 cron 使用。

    Returns:
        本地朴素 datetime；``once`` 已过期或 366 天内无解时返回 ``None``。

    Raises:
        ValueError: ``kind`` 未知或 spec 缺必要字段。
    """
    if not isinstance(spec, dict):
        raise ValueError(f"schedule 必须是对象，收到 {type(spec).__name__}")
    if not isinstance(after, datetime):
        raise ValueError(f"after 必须是 datetime，收到 {type(after).__name__}")

    kind = str(spec.get("kind") or "").strip().lower()
    if kind not in _KINDS:
        raise ValueError(f"未知的 schedule.kind：{spec.get('kind')!r}")
    after = _naive(after)
    tz = _resolve_tz(tz_name or spec.get("timezone"))

    if kind == "once":
        at = parse_datetime(spec.get("at"))
        if at is None:
            raise ValueError("kind=once 缺少 at")
        return at if at > after else None
    if kind == "interval":
        return _next_interval(spec, after)
    return _next_cron(spec, after, tz)


def next_n(spec: dict, after: datetime, n: int,
           tz_name: str | None = None) -> list[datetime]:
    """预览接下来的 n 个触发点（``n`` 上限 20）。

    ``once`` 只会给出 1 个（其后即 ``None``，停止）。
    """
    try:
        n = int(n)
    except (TypeError, ValueError):
        return []
    if n <= 0:
        return []
    n = min(n, MAX_PREVIEW)
    out: list[datetime] = []
    cursor = after
    for _ in range(n):
        point = next_fire_after(spec, cursor, tz_name=tz_name)
        if point is None:
            break
        out.append(point)
        cursor = point
    return out


# ── 时间解析 ──


def parse_datetime(value) -> datetime | None:
    """把各种输入折成**本地朴素** datetime；无法解析返回 ``None``。

    接受 datetime、ISO-8601 字符串（``...Z`` 视作 UTC）、epoch 秒。
    aware datetime 会先转成本地朴素，保证全局「本地朴素」约定。
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return _naive(value)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value)
        except (OSError, OverflowError, ValueError):
            return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f",
                    "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        if parsed is None:
            return None
    return _naive(parsed)


def _naive(value: datetime) -> datetime:
    """aware → 本地朴素；naive 原样返回。"""
    if value.tzinfo is None:
        return value
    return value.astimezone().replace(tzinfo=None)


# ── 时区 ──


@lru_cache(maxsize=64)
def _resolve_tz(name) -> object | None:
    """取 zoneinfo 时区；Windows 缺 tzdata 时降级为 ``None``（本地朴素）。

    用 ``lru_cache`` 兼作「warn once」：同一个不可用的名字只会告警一次。
    """
    text = str(name or "").strip()
    if not text:
        return None
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(text)
    except Exception as exc:  # 缺 tzdata / 名字非法 / 平台无 tz 数据库
        _log.warning("scheduler: 时区 %s 不可用（%s），降级为本地朴素时间", text, exc)
        return None


def _to_wall(value: datetime, tz) -> datetime:
    """本地朴素 → 目标时区的朴素墙钟。"""
    if tz is None:
        return value
    return datetime.fromtimestamp(value.timestamp(), tz).replace(tzinfo=None)


def _from_wall(value: datetime, tz) -> datetime:
    """目标时区的朴素墙钟 → 本地朴素。"""
    if tz is None:
        return value
    return datetime.fromtimestamp(value.replace(tzinfo=tz).timestamp())


# ── interval ──


def _next_interval(spec: dict, after: datetime) -> datetime:
    """``anchor + k * interval_sec``，锚点绝对不漂移。"""
    raw_interval = spec.get("interval_sec", spec.get("intervalSec"))
    try:
        interval = float(raw_interval)
    except (TypeError, ValueError):
        raise ValueError("kind=interval 需要 interval_sec（> 0）") from None
    if not math.isfinite(interval) or interval <= 0:
        raise ValueError(f"interval_sec 必须 > 0，收到 {raw_interval!r}")

    anchor = parse_datetime(spec.get("anchor") or spec.get("created_at"))
    if anchor is None:
        # 无锚点（数据残缺）时退化为「从现在起一个间隔」，调用方应始终带 anchor。
        anchor = after

    delta = (after - anchor).total_seconds()
    k = math.floor(delta / interval) + 1
    if k < 0:
        k = 0
    return anchor + timedelta(seconds=k * interval)


# ── cron ──


def _next_cron(spec: dict, after: datetime, tz) -> datetime | None:
    """按「天 → 小时 → 分钟」三级候选推进求下一个触发点。"""
    expr = spec.get("cron")
    if not expr:
        raise ValueError("kind=cron 缺少 cron 表达式")
    sets = parse_cron(str(expr))

    minutes = sorted(sets["minute"])
    hours = sorted(sets["hour"])
    months = sets["month"]
    doms = sets["dom"]
    dows = sets["dow"]
    dom_star = doms == set(range(1, 32))
    dow_star = dows == set(range(0, 7))

    wall_after = _to_wall(after, tz)
    cursor = wall_after.replace(second=0, microsecond=0)
    day = cursor.date()

    for _ in range(MAX_SEARCH_DAYS + 1):
        if day.month in months and _day_matches(day, doms, dows, dom_star, dow_star):
            same_day = day == cursor.date()
            for hour in hours:
                if same_day and hour < cursor.hour:
                    continue
                same_hour = same_day and hour == cursor.hour
                for minute in minutes:
                    if same_hour and minute <= cursor.minute:
                        continue
                    candidate = datetime(day.year, day.month, day.day, hour, minute)
                    if candidate > wall_after:
                        return _from_wall(candidate, tz)
                    # candidate == wall_after 不可能（已在上面排除），
                    # 只剩 wall_after 有秒/微秒的同一分钟情形，继续找下一分钟。
        day += timedelta(days=1)
    return None


def _day_matches(day, doms: set[int], dows: set[int],
                 dom_star: bool, dow_star: bool) -> bool:
    """标准 Vixie 语义：dom/dow 都非 ``*`` 时取「或」，否则取「与」。"""
    dom_ok = day.day in doms
    # Python: Monday=0..Sunday=6；cron: Sunday=0..Saturday=6
    dow_ok = ((day.weekday() + 1) % 7) in dows
    if dom_star and dow_star:
        return True
    if dom_star:
        return dow_ok
    if dow_star:
        return dom_ok
    return dom_ok or dow_ok


# ── 字段解析 ──


def _parse_field(raw: str, lo: int, hi: int, name: str) -> set[int]:
    values: set[int] = set()
    for chunk in raw.split(","):
        body = chunk.strip()
        step = 1
        if "/" in body:
            body, _, step_raw = body.partition("/")
            if not step_raw.strip().isdigit() or int(step_raw) <= 0:
                raise ValueError(f"cron 字段 {name} 步长非法：{chunk!r}")
            step = int(step_raw)
            body = body.strip()
        if not body:
            raise ValueError(f"cron 字段 {name} 语法非法：{raw!r}")
        if body == "*":
            start, end = lo, hi
        elif "-" in body:
            left, _, right = body.partition("-")
            if not (left.strip().isdigit() and right.strip().isdigit()):
                raise ValueError(f"cron 字段 {name} 范围非法：{chunk!r}")
            start, end = int(left), int(right)
            if start > end:
                raise ValueError(
                    f"cron 字段 {name} 范围倒置（不支持跨端回绕）：{chunk!r}"
                )
        elif body.isdigit():
            start = end = int(body)
        else:
            raise ValueError(f"cron 字段 {name} 语法非法：{chunk!r}")
        if start < lo or end > hi:
            raise ValueError(
                f"cron 字段 {name} 取值越界（{lo}-{hi}）：{chunk!r}"
            )
        values.update(range(start, end + 1, step))
    if not values:
        raise ValueError(f"cron 字段 {name} 解析为空：{raw!r}")
    return values
