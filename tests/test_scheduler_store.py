"""packages/scheduler/store.py 的单元测试。

隔离：把 ``store.DEFAULT_ROOT`` 指到 pytest 的 tmp_path，绝不污染真实 ``data/``
（先例见 ``tests/conftest.py:16-26``）。
"""

import json
from datetime import datetime, timedelta

import pytest

from packages.scheduler import cron
from packages.scheduler import store


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.delenv("PAN_SCHEDULER_DIR", raising=False)
    monkeypatch.setattr(store, "DEFAULT_ROOT", tmp_path / "scheduler")
    yield


def _dt(offset_sec: int = 0) -> datetime:
    return (datetime.now().replace(microsecond=0) + timedelta(seconds=offset_sec))


def _interval_payload(**overrides) -> dict:
    payload = {
        "name": "每 30 分钟",
        "target_session_id": "ses_test",
        "text": "跑数据",
        "schedule": {
            "kind": "interval",
            "interval_sec": 1800,
            "anchor": _dt(-3600).isoformat(),
        },
    }
    payload.update(overrides)
    return payload


# ── 落盘布局 ──


def test_data_root_redirected(tmp_path):
    assert store.data_root() == tmp_path / "scheduler"
    assert store.tasks_dir() == tmp_path / "scheduler" / "tasks"
    assert store.runs_path() == tmp_path / "scheduler" / "runs.jsonl"


def test_env_var_overrides_root(tmp_path, monkeypatch):
    monkeypatch.setenv("PAN_SCHEDULER_DIR", str(tmp_path / "env_root"))
    assert store.data_root() == tmp_path / "env_root"


def test_create_writes_one_file_per_task(tmp_path):
    task = store.create_task(_interval_payload())
    path = tmp_path / "scheduler" / "tasks" / f"{task['id']}.json"
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["id"] == task["id"]


def test_no_tmp_files_left_behind(tmp_path):
    store.create_task(_interval_payload())
    leftovers = list((tmp_path / "scheduler" / "tasks").glob("*.tmp"))
    assert leftovers == []


# ── create ──


def test_create_generates_id_and_timestamps():
    task = store.create_task(_interval_payload())
    assert task["id"].startswith("sch_")
    assert len(task["id"]) == len("sch_") + 12
    assert task["created_at"] and task["updated_at"]
    assert cron.parse_datetime(task["created_at"]) is not None


def test_create_computes_next_fire_at():
    task = store.create_task(_interval_payload())
    assert task["next_fire_at"]
    point = cron.parse_datetime(task["next_fire_at"])
    anchor = cron.parse_datetime(task["schedule"]["anchor"])
    delta = (point - anchor).total_seconds()
    assert delta % 1800 == 0
    assert point > datetime.now().replace(microsecond=0)


def test_create_defaults():
    task = store.create_task(_interval_payload())
    assert task["enabled"] is True
    assert task["paused"] is False
    assert task["misfire_policy"] == "fire_now"
    assert task["max_runs"] is None
    assert task["run_count"] == 0
    assert task["last_status"] is None


def test_create_interval_defaults_anchor_to_created_at():
    task = store.create_task(
        {
            "target_session_id": "ses_test",
            "text": "x",
            "schedule": {"kind": "interval", "intervalSec": 600},
        }
    )
    assert task["schedule"]["anchor"] == task["created_at"]
    assert task["schedule"]["interval_sec"] == 600
    # 出口字段 camelCase 同步写一份，读写两侧都不会踩空
    assert task["schedule"]["intervalSec"] == 600


def test_create_once_and_cron():
    once = store.create_task(
        {
            "target_session_id": "ses_test",
            "text": "x",
            "schedule": {"kind": "once", "at": _dt(3600).isoformat()},
        }
    )
    # The store stamps its own ``now()``: a second boundary crossed between the
    # two calls is not a scheduling error, so allow a one-second drift.
    assert abs((cron.parse_datetime(once["next_fire_at"]) - _dt(3600)).total_seconds()) <= 1

    cron_task = store.create_task(
        {
            "target_session_id": "ses_test",
            "text": "x",
            "schedule": {"kind": "cron", "cron": "0 9 * * 1-5"},
        }
    )
    point = cron.parse_datetime(cron_task["next_fire_at"])
    assert point.hour == 9 and point.minute == 0
    assert point.weekday() < 5


@pytest.mark.parametrize(
    "payload",
    [
        {"target_session_id": "", "text": "x",
         "schedule": {"kind": "interval", "interval_sec": 60}},
        {"target_session_id": "s", "text": "   ",
         "schedule": {"kind": "interval", "interval_sec": 60}},
        {"target_session_id": "s", "text": "x", "schedule": {"kind": "weekly"}},
        {"target_session_id": "s", "text": "x", "schedule": {"kind": "once"}},
        {"target_session_id": "s", "text": "x",
         "schedule": {"kind": "cron", "cron": "0 9 * *"}},
        {"target_session_id": "s", "text": "x",
         "schedule": {"kind": "interval", "interval_sec": 0}},
    ],
)
def test_create_rejects_invalid(payload):
    with pytest.raises(ValueError):
        store.create_task(payload)


def test_create_rejects_bad_misfire_policy():
    with pytest.raises(ValueError):
        store.create_task(_interval_payload(misfire_policy="whatever"))


# ── get / list / update / delete ──


def test_get_roundtrip():
    task = store.create_task(_interval_payload())
    assert store.get_task(task["id"])["id"] == task["id"]


def test_get_missing_or_traversal_returns_none():
    assert store.get_task("sch_missing") is None
    assert store.get_task("../../etc/passwd") is None
    assert store.get_task("") is None


def test_list_filters_disabled():
    enabled = store.create_task(_interval_payload())
    disabled = store.create_task(_interval_payload(enabled=False))
    assert {t["id"] for t in store.list_tasks()} == {enabled["id"], disabled["id"]}
    assert [t["id"] for t in store.list_tasks(include_disabled=False)] == [enabled["id"]]
    assert disabled["next_fire_at"] is None


def test_update_recomputes_next_fire_on_schedule_change():
    task = store.create_task(_interval_payload())
    updated = store.update_task(
        task["id"], {"schedule": {"kind": "cron", "cron": "0 9 * * *"}}
    )
    assert updated["schedule"]["cron"] == "0 9 * * *"
    point = cron.parse_datetime(updated["next_fire_at"])
    assert (point.hour, point.minute) == (9, 0)


def test_update_keeps_explicit_next_fire_at():
    task = store.create_task(_interval_payload())
    marker = "2026-01-01T00:00:00"
    updated = store.update_task(task["id"], {"next_fire_at": marker})
    assert updated["next_fire_at"] == marker


def test_update_disable_clears_next_fire_at():
    task = store.create_task(_interval_payload())
    assert store.update_task(task["id"], {"enabled": False})["next_fire_at"] is None
    assert store.update_task(task["id"], {"enabled": True})["next_fire_at"]


def test_update_rejects_bad_policy_and_returns_none_for_missing():
    task = store.create_task(_interval_payload())
    with pytest.raises(ValueError):
        store.update_task(task["id"], {"misfire_policy": "nope"})
    assert store.update_task("sch_missing", {"name": "x"}) is None


def test_delete():
    task = store.create_task(_interval_payload())
    assert store.delete_task(task["id"]) is True
    assert store.get_task(task["id"]) is None
    assert store.delete_task(task["id"]) is False


# ── runs.jsonl ──


def test_append_and_list_runs():
    store.append_run({"task_id": "sch_a", "status": "dispatched"})
    store.append_run({"task_id": "sch_b", "status": "error"})
    store.append_run({"task_id": "sch_a", "status": "skipped"})
    assert [r["status"] for r in store.list_runs()] == ["skipped", "error", "dispatched"]
    assert [r["status"] for r in store.list_runs(task_id="sch_a")] == [
        "skipped",
        "dispatched",
    ]
    assert len(store.list_runs(task_id="sch_a", limit=1)) == 1


def test_runs_roll_over_at_500(tmp_path):
    for i in range(store.RUNS_MAX_ENTRIES + 20):
        store.append_run({"task_id": "sch_a", "status": "dispatched", "seq": i})
    lines = [
        line
        for line in store.runs_path().read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(lines) == store.RUNS_MAX_ENTRIES
    records = [json.loads(line) for line in lines]
    assert records[0]["seq"] == 20          # 最老的 20 条被滚掉
    assert records[-1]["seq"] == store.RUNS_MAX_ENTRIES + 19


def test_list_runs_on_empty_store():
    assert store.list_runs() == []


# ── leader 选主 ──


def test_claim_leader_first_wins():
    assert store.claim_leader() is True
    assert store.claim_leader() is True  # 幂等：同进程内复用
    store.release_leader()


def test_claim_leader_survives_missing_lock_backend(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "packages.core.background_jobs":
            raise ImportError("no lock backend")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert store.claim_leader() is True  # 退化成单实例假设
    store.release_leader()
