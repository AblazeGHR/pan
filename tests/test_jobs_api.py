"""packages/jobs API（/api/jobs/*）与 P2 内核扩展的单元测试。

覆盖：
- /api/jobs 列表（全 kind 混合 + 过滤 + active-first 排序）
- 结构化 source/target 出口（新记录双写 / 旧扁平记录读路径折算）
- PATCH：name/description/enabled/target 归属切换（含 scheduled-task 积压重投联动）
- action 模板：assign 默认 / send_session / 未知回落
- schedule 模板 CRUD
- broadcast partial → job.partial_failed 事件

隔离：jobs.DEFAULT_ROOT + scheduler store.DEFAULT_ROOT → tmp_path；
worker.assign/send_session 替身；不 spawn 进程、不写真实 data/。
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from packages.core import background_jobs as jobs
from packages.core import worker as core_worker
from packages.jobs import api as jobs_api
from packages.jobs import templates as job_templates
from packages.scheduler import store as scheduler_store


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.delenv("PAN_SCHEDULER_DIR", raising=False)
    monkeypatch.delenv("PAN_BACKGROUND_JOBS_DIR", raising=False)
    monkeypatch.setattr(jobs, "DEFAULT_ROOT", tmp_path / "bg_jobs")
    monkeypatch.setattr(scheduler_store, "DEFAULT_ROOT", tmp_path / "bg_jobs")
    monkeypatch.setattr(jobs, "_scheduled_task_hooks",
                        {"root_resolver": None, "config_resolver": None,
                         "on_event": None})
    yield
    scheduler_store.release_leader()


@pytest.fixture
def fake_worker(monkeypatch):
    calls: list[dict] = []

    async def fake_assign(session_id, text, source=None, task_id=None, **kw):
        calls.append({"api": "assign", "session_id": session_id, "text": text,
                      "source": source, "task_id": task_id})
        return {"status": "queued", "workerId": "w1"}

    async def fake_send(session_id, text, source=None, **kw):
        calls.append({"api": "send_session", "session_id": session_id,
                      "text": text, "source": source})
        return {"status": "queued", "sessionId": session_id}

    monkeypatch.setattr(core_worker, "assign", fake_assign)
    monkeypatch.setattr(core_worker, "send_session", fake_send)
    return calls


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(jobs_api.router)
    return TestClient(app)


def _make_scheduled_task(**overrides):
    payload = {
        "target_session_id": "ses_t1",
        "text": "做事",
        "schedule": {"kind": "interval", "interval_sec": 1800,
                     "anchor": (datetime.now() - timedelta(hours=1)).isoformat()},
    }
    payload.update(overrides)
    return scheduler_store.create_task(payload)


def _register_unified_hooks():
    """让统一循环扫到本测试的 scheduler 数据根（root_resolver 注入）。"""
    jobs.register_scheduled_tasks(
        root_resolver=lambda: str(scheduler_store.data_root()),
        config_resolver=lambda: {"enabled": True},
        on_event=None,
    )


def _make_message_job(monkeypatch, tmp_path, name=None):
    from packages.core import session as sess

    target = sess.Session(id="ses_m1", name="m1", workdir=str(tmp_path))
    sess._cache["ses_m1"] = target
    monkeypatch.setattr(
        "packages.core.background_jobs._sessions.get",
        lambda sid: target if sid == "ses_m1" else None)
    return jobs.start_message("ses_m1", "hello",
                              {"type": "interval", "intervalSeconds": 600},
                              name=name)


# ── 列表 / kind 混合 ──


def test_list_jobs_mixed_kinds_and_filters(client, monkeypatch, tmp_path):
    task = _make_scheduled_task(name="定时甲")
    msg = _make_message_job(monkeypatch, tmp_path, name="消息乙")

    body = client.get("/api/jobs").json()
    assert body["ok"] is True
    kinds = {j["kind"] for j in body["jobs"]}
    assert kinds == {jobs.SCHEDULED_TASK_KIND, jobs.SESSION_MESSAGE_KIND}
    by_task = [j for j in body["jobs"] if j.get("taskId") == task["id"]]
    assert by_task and by_task[0]["name"] == "定时甲"

    filtered = client.get("/api/jobs", params={"kind": jobs.SESSION_MESSAGE_KIND}).json()
    assert [j["jobId"] for j in filtered["jobs"]] == [msg["jobId"]]
    assert all(j["kind"] == jobs.SESSION_MESSAGE_KIND for j in filtered["jobs"])


def test_kinds_endpoint(client):
    body = client.get("/api/jobs/kinds").json()
    assert body["ok"] is True
    kinds = {item["kind"]: item for item in body["kinds"]}
    assert jobs.SCHEDULED_TASK_KIND in kinds
    assert kinds[jobs.SCHEDULED_TASK_KIND]["hasSchedule"] is True


# ── 结构化 source/target ──


def test_structured_identity_new_records(client, monkeypatch, tmp_path):
    msg = _make_message_job(monkeypatch, tmp_path)
    body = client.get(f"/api/jobs/{msg['jobId']}").json()
    source = body["job"]["source"]
    target = body["job"]["target"]
    assert source["type"] == "agent"
    assert target == {"sessionId": "ses_m1"}


def test_structured_identity_folds_flat_legacy(client):
    """旧扁平字段（无 sourceStruct）读路径折算成结构化形状，不重写文件。"""
    task = _make_scheduled_task()
    job = scheduler_store._job_for_task(task["id"])
    path = jobs._job_path(job["jobId"])
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "sourceStruct" in raw  # 兼容层新记录已双写；删掉模拟旧记录
    raw.pop("sourceStruct")
    raw.pop("targetStruct")
    raw["source"] = "automation"
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    body = client.get(f"/api/jobs/{job['jobId']}").json()
    assert body["job"]["source"] == {"type": "system"}  # automation → system
    assert body["job"]["target"] == {"sessionId": "ses_t1"}


# ── PATCH：通用字段 + 归属切换 ──


def test_patch_rename_and_description(client):
    task = _make_scheduled_task()
    job = scheduler_store._job_for_task(task["id"])
    body = client.patch(f"/api/jobs/{job['jobId']}",
                        json={"name": "新名字", "description": "新说明"}).json()
    assert body["ok"] is True
    assert body["job"]["name"] == "新名字"
    assert body["job"]["description"] == "新说明"
    # scheduler 兼容层读的是同一记录
    assert scheduler_store.get_task(task["id"])["name"] == "新名字"


def test_patch_blank_name_rejected(client):
    task = _make_scheduled_task()
    job = scheduler_store._job_for_task(task["id"])
    body = client.patch(f"/api/jobs/{job['jobId']}", json={"name": "  "}).json()
    assert body["ok"] is False
    assert body["error"]["code"] == "invalid_argument"


def test_patch_target_switch_updates_flat_and_struct(client, fake_worker):
    task = _make_scheduled_task()
    job = scheduler_store._job_for_task(task["id"])
    body = client.patch(f"/api/jobs/{job['jobId']}",
                        json={"target": {"sessionId": "ses_t2"}}).json()
    assert body["ok"] is True
    assert body["job"]["target"] == {"sessionId": "ses_t2"}
    assert body["job"]["targetSessionId"] == "ses_t2"  # 扁平双写
    # 兼容层视角同步（同一注册表）
    assert scheduler_store.get_task(task["id"])["target_session_id"] == "ses_t2"


def test_patch_enable_disable_stops_entries(client, fake_worker):
    task = _make_scheduled_task()
    job = scheduler_store._job_for_task(task["id"])
    body = client.patch(f"/api/jobs/{job['jobId']}", json={"enabled": False}).json()
    assert body["ok"] is True
    assert body["job"]["status"] == "completed"
    assert body["job"]["nextFireAt"] is None
    assert all(e["enabled"] is False for e in body["job"]["schedule"])


# ── action 模板 ──


def test_action_template_default_assign(client, fake_worker):
    _register_unified_hooks()
    _make_scheduled_task()
    job = scheduler_store._job_for_task(
        scheduler_store.list_tasks()[0]["id"])
    # 无 action 字段 → 回落 assign
    jobs._update(job["jobId"], {
        "schedule": [{**job["schedule"][0],
                      "nextFireAt": (datetime.now() - timedelta(seconds=5)).isoformat()}]},
        registry_root=scheduler_store.data_root())
    asyncio.run(jobs.run_due_scheduled_tasks())
    assert any(c["api"] == "assign" for c in fake_worker)


def test_action_template_send_session(client, fake_worker):
    _register_unified_hooks()
    _make_scheduled_task()
    job = scheduler_store._job_for_task(
        scheduler_store.list_tasks()[0]["id"])
    jobs._update(job["jobId"], {
        "action": {"api": "send_session", "args": {}},
        "schedule": [{**job["schedule"][0],
                      "nextFireAt": (datetime.now() - timedelta(seconds=5)).isoformat()}]},
        registry_root=scheduler_store.data_root())
    asyncio.run(jobs.run_due_scheduled_tasks())
    assert any(c["api"] == "send_session" for c in fake_worker)
    assert not any(c["api"] == "assign" for c in fake_worker)


def test_action_template_unknown_falls_back_to_assign(client, fake_worker):
    _register_unified_hooks()
    _make_scheduled_task()
    job = scheduler_store._job_for_task(
        scheduler_store.list_tasks()[0]["id"])
    jobs._update(job["jobId"], {
        "action": {"api": "create_session", "args": {}},
        "schedule": [{**job["schedule"][0],
                      "nextFireAt": (datetime.now() - timedelta(seconds=5)).isoformat()}]},
        registry_root=scheduler_store.data_root())
    asyncio.run(jobs.run_due_scheduled_tasks())
    assert any(c["api"] == "assign" for c in fake_worker)  # 未知 api 回落


# ── schedule 模板 ──


def test_templates_list_has_builtins(client):
    body = client.get("/api/jobs/templates").json()
    ids = {t["id"] for t in body["templates"]}
    assert "tpl_weekday_morning" in ids
    assert all(t.get("name") for t in body["templates"])


def test_templates_create_and_delete(client):
    body = client.post("/api/jobs/templates",
                       json={"name": "半夜跑", "kind": "cron",
                             "cron": "0 3 * * *"}).json()
    assert body["ok"] is True
    tpl_id = body["template"]["id"]
    listed = client.get("/api/jobs/templates").json()["templates"]
    assert tpl_id in {t["id"] for t in listed}

    # 内置不可删
    assert client.delete("/api/jobs/templates/tpl_hourly").json()["ok"] is False
    # 自定义可删
    assert client.delete(f"/api/jobs/templates/{tpl_id}").json()["ok"] is True
    assert tpl_id not in {t["id"] for t in
                          client.get("/api/jobs/templates").json()["templates"]}


def test_templates_reject_invalid(client):
    assert client.post("/api/jobs/templates",
                       json={"name": "x", "kind": "cron"}).json()["ok"] is False
    assert client.post("/api/jobs/templates",
                       json={"name": "", "kind": "cron",
                             "cron": "0 9 * * *"}).json()["ok"] is False


# ── runs / next / run-now ──


def test_runs_and_next_and_run_now(client, fake_worker):
    task = _make_scheduled_task()
    job = scheduler_store._job_for_task(task["id"])

    next_body = client.get("/api/jobs/next", params={"count": 3}).json()
    assert next_body["ok"] is True
    assert next_body["next"] and "fireAt" in next_body["next"][0]

    run_body = client.post(f"/api/jobs/{job['jobId']}/run-now").json()
    assert run_body["ok"] is True
    assert run_body["run"]["status"] == "dispatched"

    runs = client.get(f"/api/jobs/{job['jobId']}/runs").json()
    assert runs["ok"] is True
    assert len(runs["runs"]) == 1
    assert runs["runs"][0]["task_id"] == task["id"]


def test_delete_job_routes_to_compat_for_scheduled_task(client, fake_worker):
    task = _make_scheduled_task()
    job = scheduler_store._job_for_task(task["id"])
    body = client.delete(f"/api/jobs/{job['jobId']}").json()
    assert body["ok"] is True
    assert scheduler_store.get_task(task["id"]) is None


# ── partial_failed 事件 ──


def test_partial_failed_event_emitted(client, monkeypatch, tmp_path):
    events: list[dict] = []
    monkeypatch.setattr(jobs, "_scheduled_task_hooks",
                        {"root_resolver": None, "config_resolver": None,
                         "on_event": events.append})
    from packages.core import session as sess

    for sid in ("ses_pa", "ses_pb"):
        sess._cache[sid] = sess.Session(id=sid, name=sid, workdir=str(tmp_path))

    async def flaky_send(session_id, text, source=None, **kw):
        if session_id == "ses_pb":
            return {"status": "error", "result": "worker dead"}
        return {"status": "queued", "sessionId": session_id}

    monkeypatch.setattr("packages.core.background_jobs._sessions.get",
                        lambda sid: sess._cache.get(sid))
    monkeypatch.setattr(core_worker, "send_session", flaky_send)

    # message-job 语义：once 的 at 须未来、delaySeconds 须 > 0 → 1 秒后到期
    job = jobs.start_broadcast(["ses_pa", "ses_pb"], "群发",
                               {"type": "once", "delaySeconds": 1})

    async def _wait_and_run():
        await asyncio.sleep(1.2)
        return await jobs.run_due_message_jobs()

    delivered = asyncio.run(_wait_and_run())
    assert delivered == 1

    saved = jobs.get(job["jobId"])
    assert saved["lastDelivery"]["status"] == "partial"
    partial_events = [e for e in events if e.get("type") == "job.partial_failed"]
    assert partial_events, "partial 必须发出即时 toast 事件"
    assert partial_events[0]["jobId"] == job["jobId"]
    assert any("ses_pb" in err for err in partial_events[0]["errors"])


# ── POST /api/jobs（创建，多 entry）──


def test_post_creates_scheduled_task_multi_entry(client, monkeypatch):
    monkeypatch.setattr(jobs_api, "_session_exists", lambda sid: True)
    body = client.post("/api/jobs", json={
        "kind": "scheduled-task",
        "name": "多 entry",
        "target": {"sessionId": "ses_new"},
        "text": "干活",
        "schedule": [
            {"kind": "interval", "intervalSec": 3600},
            {"kind": "cron", "cron": "0 9 * * 1-5"},
            {"kind": "once", "at": "2099-01-01T09:00:00", "enabled": False},
        ],
    }).json()
    assert body["ok"] is True, body
    job = body["job"]
    entries = job["schedule"]
    assert [e["kind"] for e in entries] == ["interval", "cron", "once"]
    assert entries[2]["enabled"] is False
    enabled_points = [e["nextFireAt"] for e in entries if e["enabled"]]
    assert job["nextFireAt"] == min(enabled_points)
    assert job["target"] == {"sessionId": "ses_new"}
    assert job["targetSessionId"] == "ses_new"
    # 旧契约视角兼容（同一注册表）
    assert scheduler_store.get_task(job["taskId"])["target_session_id"] == "ses_new"


def test_post_rejects_other_kinds_and_empty_schedule(client, monkeypatch):
    monkeypatch.setattr(jobs_api, "_session_exists", lambda sid: True)
    body = client.post("/api/jobs", json={
        "kind": "session-message", "target": {"sessionId": "s"},
        "text": "x", "schedule": {"kind": "interval", "intervalSec": 60}}).json()
    assert body["ok"] is False
    assert body["error"]["code"] == "invalid_argument"
    body = client.post("/api/jobs", json={
        "kind": "scheduled-task", "target": {"sessionId": "s"},
        "text": "x", "schedule": []}).json()
    assert body["ok"] is False
    assert body["error"]["code"] == "invalid_schedule"


def test_post_requires_existing_session(client):
    body = client.post("/api/jobs", json={
        "kind": "scheduled-task", "target": {"sessionId": "ses_ghost"},
        "text": "x", "schedule": {"kind": "interval", "intervalSec": 60}}).json()
    assert body["ok"] is False
    assert body["error"]["code"] == "session_not_found"


# ── PATCH schedule（entry 列表整体替换）──


def test_patch_schedule_replaces_entries(client, fake_worker):
    task = _make_scheduled_task()
    job = scheduler_store._job_for_task(task["id"])
    body = client.patch(f"/api/jobs/{job['jobId']}", json={
        "schedule": [
            {"kind": "cron", "cron": "30 8 * * *"},
            {"kind": "interval", "intervalSec": 7200},
        ]}).json()
    assert body["ok"] is True, body
    entries = body["job"]["schedule"]
    assert [e["kind"] for e in entries] == ["cron", "interval"]
    assert body["job"]["nextFireAt"] == min(
        e["nextFireAt"] for e in entries if e["enabled"])
    refreshed = scheduler_store._job_for_task(task["id"])
    assert len(refreshed["schedule"]) == 2


def test_patch_schedule_rejects_bad_spec(client, fake_worker):
    task = _make_scheduled_task()
    job = scheduler_store._job_for_task(task["id"])
    body = client.patch(f"/api/jobs/{job['jobId']}", json={
        "schedule": [{"kind": "cron", "cron": "not a cron"}]}).json()
    assert body["ok"] is False
    assert body["error"]["code"] == "invalid_schedule"


# ── PATCH target 清空 → 短路积压 ─--


def test_patch_target_clear_enters_undeliverable(client, fake_worker):
    _register_unified_hooks()
    task = _make_scheduled_task()
    job = scheduler_store._job_for_task(task["id"])
    body = client.patch(f"/api/jobs/{job['jobId']}",
                        json={"target": None}).json()
    assert body["ok"] is True, body
    assert body["job"]["target"] == {"sessionId": None}
    # 到期触发 → 空 target 短路：不派发、直接积压
    jobs._update(job["jobId"], {
        "schedule": [{**job["schedule"][0],
                      "nextFireAt": (datetime.now() - timedelta(seconds=5)).isoformat()}]},
        registry_root=scheduler_store.data_root())
    asyncio.run(jobs.run_due_scheduled_tasks())
    assert fake_worker == [], "空 target 不得派发"
    refreshed = scheduler_store._job_for_task(task["id"])
    assert refreshed["lastStatus"] == "undeliverable"
    assert len(refreshed["undeliveredFires"]) == 1


# ── PATCH text（编辑派发正文）──


def test_patch_text_updates_dispatch_body(client, fake_worker):
    task = _make_scheduled_task()
    job = scheduler_store._job_for_task(task["id"])
    body = client.patch(f"/api/jobs/{job['jobId']}", json={"text": "新正文"}).json()
    assert body["ok"] is True, body
    assert body["job"]["text"] == "新正文"
    assert scheduler_store.get_task(task["id"])["text"] == "新正文"
    body = client.patch(f"/api/jobs/{job['jobId']}", json={"text": "   "}).json()
    assert body["ok"] is False
    assert body["error"]["code"] == "invalid_argument"


# ── P4：runs 记录携带 entry_id ──


def test_run_records_carry_entry_id(client, fake_worker):
    _register_unified_hooks()
    task = _make_scheduled_task()
    job = scheduler_store._job_for_task(task["id"])
    jobs._update(job["jobId"], {
        "schedule": [{**job["schedule"][0],
                      "nextFireAt": (datetime.now() - timedelta(seconds=5)).isoformat()}]},
        registry_root=scheduler_store.data_root())
    asyncio.run(jobs.run_due_scheduled_tasks())
    runs = jobs.list_run_records(task_id=task["id"],
                                 registry_root=scheduler_store.data_root())
    assert runs, "fire 后应有 run 记录"
    assert runs[0].get("entry_id") == job["schedule"][0]["id"]
