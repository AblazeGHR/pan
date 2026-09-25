"""packages/scheduler/cron.py 的单元测试 —— 纯函数，无 I/O、无隔离需求。"""

import time
from datetime import datetime, timedelta

import pytest

from packages.scheduler import cron


# ── parse_cron ──


def test_parse_wildcard_and_single_values():
    spec = cron.parse_cron("30 9 * * *")
    assert spec == {
        "minute": {30},
        "hour": {9},
        "dom": set(range(1, 32)),
        "month": set(range(1, 13)),
        "dow": set(range(0, 7)),
    }


def test_parse_step():
    spec = cron.parse_cron("*/15 */6 * * *")
    assert spec["minute"] == {0, 15, 30, 45}
    assert spec["hour"] == {0, 6, 12, 18}


def test_parse_range_and_list():
    spec = cron.parse_cron("0 9-12 1,15 * *")
    assert spec["minute"] == {0}
    assert spec["hour"] == {9, 10, 11, 12}
    assert spec["dom"] == {1, 15}


def test_parse_step_inside_range():
    spec = cron.parse_cron("5-20/5 * * * *")
    assert spec["minute"] == {5, 10, 15, 20}


def test_parse_dow_zero_and_seven_are_sunday():
    assert cron.parse_cron("0 9 * * 0")["dow"] == {0}
    assert cron.parse_cron("0 9 * * 7")["dow"] == {0}
    assert cron.parse_cron("0 9 * * 1-5")["dow"] == {1, 2, 3, 4, 5}


@pytest.mark.parametrize(
    "expr",
    [
        "",
        "0 9 * *",            # 段数不足
        "0 9 * * * *",        # 段数过多
        "60 * * * *",         # minute 越界
        "* 24 * * *",         # hour 越界
        "* * 0 * *",          # dom 越界
        "* * * 13 *",         # month 越界
        "* * * * 8",          # dow 越界
        "*/0 * * * *",        # 步长 0
        "*/x * * * *",        # 步长非数字
        "a-b * * * *",        # 非数字范围
        "12-9 * * * *",       # 范围倒置（不支持回绕）
        "* * * * MON",        # 不支持名字
        "@daily",             # 不支持宏
    ],
)
def test_parse_invalid_raises(expr):
    with pytest.raises(ValueError):
        cron.parse_cron(expr)


def test_parse_rejects_non_string():
    with pytest.raises(ValueError):
        cron.parse_cron(None)


# ── once ──


def test_once_returns_at_when_future():
    spec = {"kind": "once", "at": "2026-09-16T09:00:00"}
    after = datetime(2026, 9, 16, 8, 0)
    assert cron.next_fire_after(spec, after) == datetime(2026, 9, 16, 9, 0)


def test_once_returns_none_when_past_or_equal():
    spec = {"kind": "once", "at": "2026-09-16T09:00:00"}
    assert cron.next_fire_after(spec, datetime(2026, 9, 16, 9, 0)) is None
    assert cron.next_fire_after(spec, datetime(2026, 9, 16, 9, 1)) is None


def test_once_requires_at():
    with pytest.raises(ValueError):
        cron.next_fire_after({"kind": "once"}, datetime(2026, 9, 16, 9, 0))


# ── interval ──


def test_interval_anchor_does_not_drift():
    anchor = datetime(2026, 9, 16, 9, 0, 30)  # 故意错开整点
    spec = {"kind": "interval", "interval_sec": 3600, "anchor": anchor.isoformat()}
    assert cron.next_fire_after(spec, datetime(2026, 9, 16, 9, 0, 0)) == anchor
    assert cron.next_fire_after(spec, anchor) == anchor + timedelta(hours=1)
    # 连续推进 5 次后仍严格落在锚点网格上
    cursor = anchor
    for _ in range(5):
        cursor = cron.next_fire_after(spec, cursor)
    assert cursor == anchor + timedelta(hours=5)


def test_interval_jumps_over_downtime():
    """停机 10 小时后：下一个点仍在锚点网格上，不是 now + interval。"""
    anchor = datetime(2026, 9, 16, 0, 0)
    spec = {"kind": "interval", "interval_sec": 1800, "anchor": anchor.isoformat()}
    after = datetime(2026, 9, 16, 10, 0)  # 正好落在网格点上
    assert cron.next_fire_after(spec, after) == datetime(2026, 9, 16, 10, 30)
    # 停机 10 小时 07 分：只结算到下一格，绝不补跑 20 次
    assert cron.next_fire_after(spec, datetime(2026, 9, 16, 10, 7)) == datetime(
        2026, 9, 16, 10, 30
    )


def test_interval_requires_positive_interval():
    with pytest.raises(ValueError):
        cron.next_fire_after(
            {"kind": "interval", "interval_sec": 0, "anchor": "2026-09-16T00:00:00"},
            datetime(2026, 9, 16, 0, 0),
        )
    with pytest.raises(ValueError):
        cron.next_fire_after(
            {"kind": "interval", "anchor": "2026-09-16T00:00:00"},
            datetime(2026, 9, 16, 0, 0),
        )


# ── cron 求值 ──


def test_cron_daily_fire():
    spec = {"kind": "cron", "cron": "0 9 * * *"}
    assert cron.next_fire_after(spec, datetime(2026, 9, 16, 8, 0)) == datetime(
        2026, 9, 16, 9, 0
    )
    # 09:00 已过（含 09:00:00 当刻）→ 次日 09:00
    assert cron.next_fire_after(spec, datetime(2026, 9, 16, 9, 0)) == datetime(
        2026, 9, 17, 9, 0
    )
    assert cron.next_fire_after(spec, datetime(2026, 9, 16, 9, 0, 1)) == datetime(
        2026, 9, 17, 9, 0
    )


def test_cron_weekdays_skips_weekend():
    """2026-09-18 是周五；'0 9 * * 1-5' 的下一次是周五 09:00，再下周一 09:00。"""
    spec = {"kind": "cron", "cron": "0 9 * * 1-5"}
    friday_morning = datetime(2026, 9, 18, 8, 0)
    assert cron.next_fire_after(spec, friday_morning) == datetime(2026, 9, 18, 9, 0)
    after_friday = cron.next_fire_after(spec, datetime(2026, 9, 18, 9, 0))
    assert after_friday == datetime(2026, 9, 21, 9, 0)  # 周一
    assert after_friday.weekday() == 0


def test_cron_sunday_accepts_zero_and_seven():
    for expr in ("0 9 * * 0", "0 9 * * 7"):
        spec = {"kind": "cron", "cron": expr}
        point = cron.next_fire_after(spec, datetime(2026, 9, 16, 0, 0))  # 周三
        assert point == datetime(2026, 9, 20, 9, 0)
        assert point.weekday() == 6


def test_cron_step_and_list_fields():
    spec = {"kind": "cron", "cron": "*/15 9,17 * * *"}
    assert cron.next_fire_after(spec, datetime(2026, 9, 16, 8, 0)) == datetime(
        2026, 9, 16, 9, 0
    )
    assert cron.next_fire_after(spec, datetime(2026, 9, 16, 9, 1)) == datetime(
        2026, 9, 16, 9, 15
    )
    assert cron.next_fire_after(spec, datetime(2026, 9, 16, 9, 46)) == datetime(
        2026, 9, 16, 17, 0
    )


def test_cron_crosses_month_boundary():
    spec = {"kind": "cron", "cron": "0 0 1 * *"}
    assert cron.next_fire_after(spec, datetime(2026, 1, 15, 0, 0)) == datetime(
        2026, 2, 1, 0, 0
    )


def test_cron_month_field():
    spec = {"kind": "cron", "cron": "0 0 1 1 *"}
    assert cron.next_fire_after(spec, datetime(2026, 3, 1, 0, 0)) == datetime(
        2027, 1, 1, 0, 0
    )


def test_cron_dom_and_dow_union_when_both_restricted():
    """dom/dow 都非 * 时取并集：1 号或周一都触发。"""
    spec = {"kind": "cron", "cron": "0 0 1 * 1"}
    # 2026-02-01 是周日，2026-02-02 是周一
    assert cron.next_fire_after(spec, datetime(2026, 1, 31, 12, 0)) == datetime(
        2026, 2, 1, 0, 0
    )
    assert cron.next_fire_after(spec, datetime(2026, 2, 1, 12, 0)) == datetime(
        2026, 2, 2, 0, 0
    )


def test_cron_no_solution_within_a_year():
    spec = {"kind": "cron", "cron": "0 0 30 2 *"}  # 2 月 30 日永远不存在
    assert cron.next_fire_after(spec, datetime(2026, 1, 1, 0, 0)) is None


def test_cron_invalid_expression_raises():
    with pytest.raises(ValueError):
        cron.next_fire_after({"kind": "cron", "cron": "0 9 * *"},
                             datetime(2026, 9, 16, 8, 0))
    with pytest.raises(ValueError):
        cron.next_fire_after({"kind": "cron"}, datetime(2026, 9, 16, 8, 0))


def test_unknown_kind_raises():
    with pytest.raises(ValueError):
        cron.next_fire_after({"kind": "daily"}, datetime(2026, 9, 16, 8, 0))


def test_cron_search_cap_is_one_year():
    """2026-01-01 起算，2028-02-29 超出 366 天上限 → None（不是死循环）。"""
    spec = {"kind": "cron", "cron": "0 0 29 2 *"}
    started = time.time()
    assert cron.next_fire_after(spec, datetime(2026, 1, 1, 0, 0)) is None
    assert time.time() - started < 2.0  # 三级推进，不是逐分钟步进


# ── 时区 ──


def test_cron_honours_timezone_argument():
    """tz_name 生效时，cron 在该时区的墙钟上求值。"""
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo("UTC")
    except Exception:  # pragma: no cover - 缺 tzdata 的环境
        pytest.skip("zoneinfo 无 tz 数据库，本用例跳过")
    spec = {"kind": "cron", "cron": "0 9 * * *"}
    after = datetime(2026, 3, 1, 0, 0)
    offset_hours = datetime.now().astimezone().utcoffset().total_seconds() / 3600
    tz_point = cron.next_fire_after(spec, after, tz_name="UTC")
    naive_point = cron.next_fire_after(spec, after)
    assert tz_point.day == 1
    assert tz_point.hour == int((9 + offset_hours) % 24)
    assert naive_point.hour == 9
    if offset_hours:
        assert tz_point != naive_point


def test_missing_tzdata_falls_back_to_naive(monkeypatch):
    """取不到时区时降级为本地朴素，不抛异常。"""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "zoneinfo":
            raise ImportError("no tzdata")
        return real_import(name, *args, **kwargs)

    cron._resolve_tz.cache_clear()
    monkeypatch.setattr(builtins, "__import__", fake_import)
    try:
        spec = {"kind": "cron", "cron": "0 9 * * *", "timezone": "Mars/Phobos"}
        assert cron.next_fire_after(spec, datetime(2026, 3, 1, 0, 0)) == datetime(
            2026, 3, 1, 9, 0
        )
    finally:
        cron._resolve_tz.cache_clear()


# ── next_n ──


def test_next_n_daily():
    spec = {"kind": "cron", "cron": "0 9 * * *"}
    points = cron.next_n(spec, datetime(2026, 9, 16, 8, 0), 3)
    assert points == [
        datetime(2026, 9, 16, 9, 0),
        datetime(2026, 9, 17, 9, 0),
        datetime(2026, 9, 18, 9, 0),
    ]


def test_next_n_once_has_single_entry():
    spec = {"kind": "once", "at": "2026-09-16T09:00:00"}
    assert cron.next_n(spec, datetime(2026, 9, 16, 8, 0), 5) == [
        datetime(2026, 9, 16, 9, 0)
    ]
    assert cron.next_n(spec, datetime(2026, 9, 16, 10, 0), 5) == []


def test_next_n_clamps_count():
    spec = {"kind": "interval", "interval_sec": 60, "anchor": "2026-09-16T00:00:00"}
    assert len(cron.next_n(spec, datetime(2026, 9, 16, 0, 0), 100)) == cron.MAX_PREVIEW
    assert cron.next_n(spec, datetime(2026, 9, 16, 0, 0), 0) == []
    assert cron.next_n(spec, datetime(2026, 9, 16, 0, 0), -1) == []


# ── parse_datetime ──


def test_parse_datetime_variants():
    assert cron.parse_datetime("2026-09-16T09:00:00") == datetime(2026, 9, 16, 9, 0)
    assert cron.parse_datetime("2026-09-16 09:00:00") == datetime(2026, 9, 16, 9, 0)
    assert cron.parse_datetime(datetime(2026, 9, 16, 9, 0)) == datetime(2026, 9, 16, 9, 0)
    assert cron.parse_datetime("not a time") is None
    assert cron.parse_datetime(None) is None
    assert cron.parse_datetime("") is None
