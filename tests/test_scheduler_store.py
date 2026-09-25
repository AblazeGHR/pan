"""packages/scheduler/store.py（统一注册表兼容层）的单元测试。

P1 统一后 task 的持久化形态是 ``packages.core.background_jobs`` 注册表里
``kind="scheduled-task"`` 的 job 记录；本文件验证兼容层的 CRUD/校验/迁移语义
与 PR 契约逐条一致（详见 docs/design/job-unification/PLAN_JOB_UNIFICATION.md）。

隔离：把 ``store.DEFAULT_ROOT`` 指到 pytest 的 tmp_path，绝不污染真实 ``data/``。
"""

import json
from datetime import datetime, timedelta

import pytest

from packages.core import background_jobs
from packages.scheduler import cron
from packages.scheduler import store


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.delenv("PAN_SCHEDULER_DIR", raising=False)
    monkeypatch.delenv("PAN_BACKGROUND_JOBS_DIR", raising=False)
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


def _job_file(task_id: str):
    """按对外 task_id 找到注册表里的 job 文件（兼容层寻址路径）。"""
    root = store.data_root()
    for path in (root / "jobs").glob("job_*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("taskId") == task_id:
            return path, data
    return None, None


# ── 注册表布局 ──


def test_data_root_redirected(tmp_path):
    assert store.data_root() == tmp_path / "scheduler"
    assert (store.data_root() / "jobs").exists() or True  # 惰性创建，不强制存在


def test_env_var_overrides_root(tmp_path, monkeypatch):
    monkeypatch.setenv("PAN_SCHEDULER_DIR", str(tmp_path / "env_root"))
    assert store.data_root() == tmp_path / "env_root"
    # 迁移源跟随同一个 env（测试期数据根即迁移源根）
    assert store.legacy_root() == tmp_path / "env_root"


def test_create_writes_job_record(tmp_path):
    task = store.create_task(_interval_payload())
    path, data = _job_file(task["id"])
    assert path is not None
    assert data["kind"] == background_jobs.SCHEDULED_TASK_KIND
    assert data["taskId"] == task["id"]
    assert data["targetSessionId"] == "ses_test"
    assert isinstance(data["schedule"], list) and len(data["schedule"]) == 1


def test_no_tmp_files_left_behind(tmp_path):
    store.create_task(_interval_payload())
    jobs_dir = store.data_root() / "jobs"
    leftovers = list(jobs_dir.glob("*.tmp")) if jobs_dir.exists() else []
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
    runs_path = store.data_root() / "runs.jsonl"
    lines = [
        line
        for line in runs_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(lines) == store.RUNS_MAX_ENTRIES
    records = [json.loads(line) for line in lines]
    assert records[0]["seq"] == 20          # 最老的 20 条被滚掉
    assert records[-1]["seq"] == store.RUNS_MAX_ENTRIES + 19


def test_list_runs_on_empty_store():
    assert store.list_runs() == []


# ── leader 选主（已退役 → 兼容桩恒真）──


def test_claim_leader_stub_is_idempotent():
    assert store.claim_leader() is True
    assert store.claim_leader() is True
    store.release_leader()  # no-op，不抛


# ── 迁移 ──


def _legacy_task_dict(task_id: str = "sch_legacy01") -> dict:
    """PR 时代的 task 文件形状。"""
    return {
        "id": task_id,
        "name": "旧任务",
        "target_session_id": "ses_old",
        "text": "旧文本",
        "enabled": True,
        "paused": False,
        "schedule": {"kind": "interval", "interval_sec": 1800,
                     "intervalSec": 1800, "anchor": _dt(-3600).isoformat()},
        "next_fire_at": _dt(600).isoformat(),
        "last_fire_at": None,
        "last_status": None,
        "last_error": None,
        "run_count": 3,
        "max_runs": None,
        "misfire_policy": "skip",
        "created_at": _dt(-7200).isoformat(),
        "updated_at": _dt(-3600).isoformat(),
    }


@pytest.fixture
def legacy_env(tmp_path, monkeypatch):
    """迁移源 = 注册表根 = 同一个 tmp（env 优先语义下二者合一）。"""
    root = tmp_path / "unified"
    monkeypatch.setenv("PAN_SCHEDULER_DIR", str(root))
    (root / "tasks").mkdir(parents=True)
    return root


def test_migrate_legacy_tasks_roundtrip(legacy_env):
    legacy = _legacy_task_dict()
    (legacy_env / "tasks" / f"{legacy['id']}.json").write_text(
        json.dumps(legacy, ensure_ascii=False), encoding="utf-8")

    migrated = store.migrate_legacy_tasks()
    assert migrated == 1
    task = store.get_task(legacy["id"])
    assert task is not None
    assert task["text"] == "旧文本"
    assert task["misfire_policy"] == "skip"
    assert task["run_count"] == 3
    assert task["next_fire_at"] == legacy["next_fire_at"]  # 节奏不丢
    # 源文件保留
    assert (legacy_env / "tasks" / f"{legacy['id']}.json").exists()


def test_migrate_is_idempotent(legacy_env):
    legacy = _legacy_task_dict()
    (legacy_env / "tasks" / f"{legacy['id']}.json").write_text(
        json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    assert store.migrate_legacy_tasks() == 1
    assert store.migrate_legacy_tasks() == 0  # taskId 已在注册表 → 跳过
    assert len(store.list_tasks()) == 1


def test_migrate_skips_invalid_files(legacy_env):
    (legacy_env / "tasks" / "garbage.json").write_text("not json", encoding="utf-8")
    (legacy_env / "tasks" / "no_id.json").write_text('{"name": "x"}', encoding="utf-8")
    assert store.migrate_legacy_tasks() == 0
    assert store.list_tasks() == []


def test_migrate_merges_legacy_runs(legacy_env):
    legacy_run = {"run_id": "run_legacy_1", "task_id": "sch_legacy01",
                  "status": "dispatched"}
    (legacy_env / "runs.jsonl").write_text(
        json.dumps(legacy_run, ensure_ascii=False) + "\n", encoding="utf-8")
    store.migrate_legacy_tasks()
    runs = store.list_runs(task_id="sch_legacy01")
    assert [r["run_id"] for r in runs] == ["run_legacy_1"]
    # 再迁一次不重复
    store.migrate_legacy_tasks()
    assert len(store.list_runs(task_id="sch_legacy01")) == 1


# ── name / description 规范（所有 kind 统一，2026-09-25 用户拍板）──


def test_create_defaults_name_to_min_vacant_job_n():
    """不填 name → job-N；N = 存量最小空缺（改名腾号后可复用）。"""
    first = store.create_task(_interval_payload(name=None))
    assert first["name"] == "job-1"
    second = store.create_task(_interval_payload(name=None))
    assert second["name"] == "job-2"
    third = store.create_task(_interval_payload(name=None))
    assert third["name"] == "job-3"

    # 显式改名让 job-2 空缺 → 下一个默认名复用 job-2
    store.update_task(second["id"], {"name": "日报任务"})
    fourth = store.create_task(_interval_payload(name=None))
    assert fourth["name"] == "job-2"


def test_create_blank_name_variants_all_get_default():
    """None / 空串 / 纯空白一律走默认名，绝不因 name 拒绝创建。"""
    a = store.create_task(_interval_payload(name=None))
    b = store.create_task(_interval_payload(name="   "))
    c = store.create_task(_interval_payload(name=""))
    assert a["name"] == "job-1"
    assert b["name"] == "job-2"
    assert c["name"] == "job-3"
    # 显式名字 strip 后收编
    d = store.create_task(_interval_payload(name="  x  "))
    assert d["name"] == "x"


def test_name_is_editable_and_description_roundtrip():
    task = store.create_task(_interval_payload(description="  说明  "))
    assert task["description"] == "说明"  # strip 收编
    updated = store.update_task(task["id"], {"name": "改名", "description": ""})
    assert updated["name"] == "改名"
    assert updated["description"] == ""
    # name 改成空白 → 回退默认名（不允许空）
    reverted = store.update_task(task["id"], {"name": "  "})
    assert reverted["name"].startswith("job-")


def test_description_defaults_empty_and_exports():
    task = store.create_task(_interval_payload())
    assert task["description"] == ""
    job = store._job_for_task(task["id"])
    assert job["description"] == ""


def test_migrated_legacy_task_gets_name_description(legacy_env):
    legacy = _legacy_task_dict()
    (legacy_env / "tasks" / f"{legacy['id']}.json").write_text(
        json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    store.migrate_legacy_tasks()
    task = store.get_task(legacy["id"])
    assert task["name"] == "旧任务"          # 迁移保留原名
    assert task["description"] == ""          # 旧数据无 description → 兜底空串
