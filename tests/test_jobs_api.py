"""packages/jobs API（/api/jobs/*）与 P2 内核扩展的单元测试。

覆盖：
- /api/jobs 列表（全 kind 混合 + 过滤 + active-first 排序）
- 结构化 source/target 出口（新记录双写 / 旧扁平记录读路径折算）
- PATCH：name/description/enabled/target 归属切换（含 scheduled-task 积压重投联动）
- action 模板：assign 默认 / send_session / resume_legal_running / 未知回落
- schedule 模板 CRUD
- broadcast partial → job.partial_failed 事件

隔离：jobs.DEFAULT_ROOT + scheduler store.DEFAULT_ROOT → tmp_path；
worker.assign/send_session 替身；不 spawn 进程、不写真实 data/。
"""

import asyncio
import json
import subprocess
import time
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
                         "on_event": None, "qq_send": None})
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
                      "text": text, "source": source,
                      "client_message_id": kw.get("client_message_id")})
        return {"status": "queued", "sessionId": session_id}

    monkeypatch.setattr(core_worker, "assign", fake_assign)
    monkeypatch.setattr(core_worker, "send_session", fake_send)
    return calls


@pytest.fixture
def fake_session_repository(monkeypatch, tmp_path):
    """Register lightweight Sessions through the same repository used by APIs."""
    from packages.core import session as sess

    sessions = {}
    monkeypatch.setattr(
        sess, "get",
        lambda session_id, *args, **kwargs: sessions.get(session_id),
    )

    def register(*session_ids):
        for session_id in session_ids:
            sessions[session_id] = sess.Session(
                id=session_id, name=session_id, workdir=str(tmp_path))
        return sessions

    return register


@pytest.fixture
def isolated_session_repository(monkeypatch, tmp_path):
    """Use a temporary durable Session directory without starting Workers."""
    from packages.core import session as sess

    old_cache = dict(sess._cache)
    old_loaded = sess._all_loaded
    old_newline_cache = set(sess._newline_terminated_jsonl)
    monkeypatch.setattr(sess, "SESSION_DIR", tmp_path / "sessions")
    sess._cache.clear()
    sess._all_loaded = False
    yield sess
    sess._cache.clear()
    sess._cache.update(old_cache)
    sess._all_loaded = old_loaded
    sess._newline_terminated_jsonl.clear()
    sess._newline_terminated_jsonl.update(old_newline_cache)


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


def test_patch_target_switch_updates_flat_and_struct(
        client, fake_worker, fake_session_repository):
    fake_session_repository("ses_t1", "ses_t2")
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
    rejected = client.patch(
        f"/api/jobs/{job['jobId']}",
        json={"action": {"api": "create_session"}},
    ).json()
    assert rejected["ok"] is False
    assert rejected["error"]["code"] == "invalid_argument"
    jobs._update(job["jobId"], {
        "action": {"api": "create_session", "args": {}},
        "schedule": [{**job["schedule"][0],
                      "nextFireAt": (datetime.now() - timedelta(seconds=5)).isoformat()}]},
        registry_root=scheduler_store.data_root())
    asyncio.run(jobs.run_due_scheduled_tasks())
    assert any(c["api"] == "assign" for c in fake_worker)  # 未知 api 回落


def test_resume_legal_running_job_rescans_and_run_now_sends_only_to_current_candidates(
    client, fake_worker, monkeypatch, isolated_session_repository, tmp_path,
):
    _register_unified_hooks()
    created = client.post("/api/jobs", json={
        "kind": "scheduled-task",
        "name": "唤醒合法运行 Session",
        "action": {"api": "resume_legal_running"},
        "schedule": [{"kind": "interval", "intervalSec": 3600}],
    }).json()
    assert created["ok"] is True
    job = created["job"]
    assert job["action"] == {"api": "resume_legal_running"}
    assert job["text"] == "继续"
    assert job["target"] == {"sessionId": None}

    # Create persisted Sessions after the Job to prove candidate IDs are not
    # captured at creation. Force the next scan to reload their legal state
    # from the temporary Session repository on disk.
    sessions = {
        "eligible": isolated_session_repository.create(
            "resume-eligible", workdir=str(tmp_path)),
        "live": isolated_session_repository.create(
            "resume-live", workdir=str(tmp_path)),
        "idle": isolated_session_repository.create(
            "resume-idle", workdir=str(tmp_path)),
        "unknown": isolated_session_repository.create(
            "resume-unknown", workdir=str(tmp_path)),
    }
    sessions["eligible"].last_legal_worker_state = "running"
    sessions["live"].last_legal_worker_state = "running"
    sessions["idle"].last_legal_worker_state = "idle"
    for session in sessions.values():
        isolated_session_repository.save(session)
    monkeypatch.setattr(
        core_worker, "find_alive_worker_by_session",
        lambda session_id: object() if session_id == sessions["live"].id else None,
    )
    isolated_session_repository._cache.clear()
    isolated_session_repository._all_loaded = False

    run = client.post(f"/api/jobs/{job['jobId']}/run-now").json()

    assert run["ok"] is True
    assert run["run"]["status"] == "dispatched"
    assert run["run"]["result"]["matchedCount"] == 1
    assert [call["session_id"] for call in fake_worker] == [sessions["eligible"].id]
    assert fake_worker[0]["text"] == "继续"
    assert fake_worker[0]["source"] == "automation"
    assert fake_worker[0]["client_message_id"].startswith("job-resume:")


def test_resume_legal_running_empty_scan_is_successful_noop(client, fake_worker, monkeypatch):
    from packages.core import session as sess

    _register_unified_hooks()
    monkeypatch.setattr(sess, "list_all", lambda *, load_history=True: [])
    monkeypatch.setattr(core_worker, "find_alive_worker_by_session", lambda _sid: None)
    created = client.post("/api/jobs", json={
        "kind": "scheduled-task",
        "action": {"api": "resume_legal_running"},
        "schedule": [{"kind": "interval", "intervalSec": 3600}],
    }).json()
    assert created["ok"] is True

    run = client.post(f"/api/jobs/{created['job']['jobId']}/run-now").json()
    assert run["ok"] is True
    assert run["run"]["status"] == "completed"
    assert run["run"]["result"]["matchedCount"] == 0
    assert run["run"]["result"]["results"] == []
    assert fake_worker == []


def test_resume_legal_running_scheduled_fire_without_candidates_is_not_backlogged(
    client, fake_worker, monkeypatch,
):
    from packages.core import session as sess

    _register_unified_hooks()
    monkeypatch.setattr(sess, "list_all", lambda *, load_history=True: [])
    monkeypatch.setattr(core_worker, "find_alive_worker_by_session", lambda _sid: None)
    created = client.post("/api/jobs", json={
        "kind": "scheduled-task",
        "action": {"api": "resume_legal_running"},
        "schedule": [{"kind": "interval", "intervalSec": 3600}],
    }).json()
    assert created["ok"] is True
    job = scheduler_store._job_for_task(created["job"]["taskId"])
    due_entry = {
        **job["schedule"][0],
        "nextFireAt": (datetime.now() - timedelta(seconds=5)).isoformat(),
    }
    jobs._update(job["jobId"], {"schedule": [due_entry]},
                 registry_root=scheduler_store.data_root())

    handled = asyncio.run(jobs.run_due_scheduled_tasks())
    saved = jobs.get(job["jobId"], registry_root=scheduler_store.data_root())

    assert handled == 1
    assert saved["lastStatus"] == "completed"
    assert saved["runCount"] == 1
    assert saved["undeliveredFires"] == []
    assert saved["lastDelivery"]["matchedCount"] == 0
    assert fake_worker == []


def test_resume_legal_running_patch_preserves_dynamic_target_and_fixed_text(client):
    created = client.post("/api/jobs", json={
        "kind": "scheduled-task",
        "action": {"api": "resume_legal_running"},
        "schedule": [{"kind": "interval", "intervalSec": 3600}],
    }).json()
    assert created["ok"] is True
    job_id = created["job"]["jobId"]

    edited = client.patch(f"/api/jobs/{job_id}", json={
        "name": "Resume after edit",
        "action": {"api": "resume_legal_running"},
        "target": {"sessionId": None},
        "text": "继续",
    }).json()
    assert edited["ok"] is True
    assert edited["job"]["target"] == {"sessionId": None}
    assert edited["job"]["text"] == "继续"

    wrong_text = client.patch(f"/api/jobs/{job_id}", json={"text": "other"}).json()
    fixed_target = client.patch(f"/api/jobs/{job_id}", json={
        "target": {"sessionId": "ses_fixed"},
    }).json()
    assert wrong_text["ok"] is False
    assert fixed_target["ok"] is False


def test_resume_legal_running_keeps_per_fire_client_message_id_stable(
    fake_worker, monkeypatch,
):
    from types import SimpleNamespace
    from packages.core import session as sess

    monkeypatch.setattr(sess, "list_all", lambda *, load_history=True: [
        SimpleNamespace(id="ses_resume_retry", last_legal_worker_state="running"),
    ])
    monkeypatch.setattr(core_worker, "find_alive_worker_by_session", lambda _sid: None)
    job = {
        "jobId": "job_resume",
        "kind": "scheduled-task",
        "targetSessionId": None,
        "text": "继续",
        "action": {"api": "resume_legal_running"},
    }

    first = asyncio.run(jobs._run_job_action(job, None, "task:entry:fire-1"))
    retried = asyncio.run(jobs._run_job_action(job, None, "task:entry:fire-1"))

    assert first["status"] == retried["status"] == "dispatched"
    assert len(fake_worker) == 2  # the normal sender receives its durable idempotency key
    assert fake_worker[0]["client_message_id"] == fake_worker[1]["client_message_id"]
    assert fake_worker[0]["text"] == fake_worker[1]["text"] == "继续"


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


def test_post_creates_scheduled_shell_action_without_target_or_runner(
        client, monkeypatch, tmp_path):
    popen_calls = []

    def forbidden_popen(*args, **kwargs):
        popen_calls.append((args, kwargs))
        raise AssertionError("scheduled shell API creation must not start a Runner")

    monkeypatch.setattr(subprocess, "Popen", forbidden_popen)
    command = "echo contract-test-must-not-run"
    cwd = str(jobs.PROJECT_ROOT.resolve())
    body = client.post("/api/jobs", json={
        "kind": "scheduled-task",
        "action": {"api": "shell", "args": {"command": command, "cwd": cwd}},
        "target": None,
        "schedule": {"kind": "once", "at": "2099-01-01T09:00:00"},
    }).json()

    assert body["ok"] is True, body
    created = body["job"]
    assert created["kind"] == jobs.SCHEDULED_TASK_KIND
    assert created["action"] == {
        "api": "shell", "args": {"command": command, "cwd": cwd}}
    assert created["target"] == {"sessionId": None}
    assert created["targetSessionId"] is None

    persisted = jobs.get(
        created["jobId"], registry_root=scheduler_store.data_root())
    assert persisted is not None
    assert persisted["kind"] == jobs.SCHEDULED_TASK_KIND
    assert persisted["action"] == created["action"]
    assert persisted["action"]["args"]["command"] == command
    assert persisted["action"]["args"]["cwd"] == cwd
    assert scheduler_store.data_root().is_relative_to(tmp_path)
    assert popen_calls == []


def _create_scheduled_qq_job(client, *, target_type="user", target_id="123456",
                             bot_uin="987654", text="hello QQ"):
    args = {"targetType": target_type, "targetId": target_id}
    if bot_uin is not None:
        args["botUin"] = bot_uin
    return client.post("/api/jobs", json={
        "kind": "scheduled-task",
        "name": "QQ reminder",
        "action": {"api": "send_qq", "args": args},
        "text": text,
        "schedule": [{"kind": "interval", "intervalSec": 3600}],
        "misfirePolicy": "fire_now",
        "maxRuns": 5,
        "enabled": True,
        "paused": False,
    }).json()


def test_post_scheduled_qq_action_persists_target_and_rejects_untrusted_fields(client):
    created = _create_scheduled_qq_job(client)
    assert created["ok"] is True, created
    job = created["job"]
    assert job["action"] == {"api": "send_qq", "args": {
        "targetType": "user", "targetId": "123456", "botUin": "987654"}}
    assert job["target"] == {"sessionId": None}
    assert job["targetSessionId"] is None
    assert job["text"] == "hello QQ"

    invalid_bodies = [
        {"action": {"api": "send_qq", "args": {
            "targetType": "user", "targetId": "123"}}, "text": "x"},
        {"action": {"api": "send_qq", "args": {
            "targetType": "group", "targetId": "123456", "botUin": "abc"}},
         "text": "x"},
        {"action": {"api": "send_qq", "args": {
            "targetType": "user", "targetId": "123456", "url": "http://host"}},
         "text": "x"},
        {"action": {"api": "send_qq", "args": {
            "targetType": ["user"], "targetId": "123456"}}, "text": "x"},
        {"action": {"api": "send_qq", "args": {
            "targetType": "friend", "targetId": "123456"}}, "text": "x"},
        {"action": {"api": "send_qq", "args": {
            "targetType": "user", "targetId": "123456"}}, "text": "  "},
        {"action": {"api": "send_qq", "args": {
            "targetType": "user", "targetId": "123456"}},
         "target": {"sessionId": "ses_arbitrary"}, "text": "x"},
    ]
    for fields in invalid_bodies:
        body = client.post("/api/jobs", json={
            "kind": "scheduled-task",
            "schedule": [{"kind": "interval", "intervalSec": 3600}],
            **fields,
        }).json()
        assert body["ok"] is False, fields
        assert body["error"]["code"] == "invalid_argument"


def test_patch_scheduled_qq_action_is_strict_and_editable(client):
    created = _create_scheduled_qq_job(client)
    assert created["ok"] is True, created
    job_id = created["job"]["jobId"]

    malformed = client.patch(f"/api/jobs/{job_id}", json={
        "action": {"api": "send_qq", "args": {
            "targetType": "group", "targetId": "123456", "command": "echo unsafe"}},
    }).json()
    malformed_bot = client.patch(f"/api/jobs/{job_id}", json={
        "action": {"api": "send_qq", "args": {
            "targetType": "user", "targetId": "123456", "botUin": "not-a-bot"}},
    }).json()
    malformed_recipient = client.patch(f"/api/jobs/{job_id}", json={
        "action": {"api": "send_qq", "args": {
            "targetType": "group", "targetId": "not-a-qq-id"}},
    }).json()
    empty_text = client.patch(f"/api/jobs/{job_id}", json={"text": "  "}).json()
    session_target = client.patch(f"/api/jobs/{job_id}", json={
        "target": {"sessionId": "ses_arbitrary"},
    }).json()
    assert malformed["ok"] is False
    assert malformed_bot["ok"] is False
    assert malformed_recipient["ok"] is False
    assert empty_text["ok"] is False
    assert session_target["ok"] is False

    edited = client.patch(f"/api/jobs/{job_id}", json={
        "name": "Updated QQ reminder",
        "action": {"api": "send_qq", "args": {
            "targetType": "group", "targetId": "456789", "botUin": "234567"}},
        "text": "updated text",
        "paused": True,
    }).json()
    assert edited["ok"] is True, edited
    assert edited["job"]["action"] == {"api": "send_qq", "args": {
        "targetType": "group", "targetId": "456789", "botUin": "234567"}}
    assert edited["job"]["target"] == {"sessionId": None}
    assert edited["job"]["text"] == "updated text"
    assert edited["job"]["paused"] is True


def test_scheduled_qq_fire_uses_selected_bot_and_records_success_or_offline_error(
        client, monkeypatch):
    _register_unified_hooks()
    calls = []

    async def fake_qq_send(*, target_type, target_id, text, bot_uin=None):
        calls.append({"target_type": target_type, "target_id": target_id,
                      "text": text, "bot_uin": bot_uin})
        if bot_uin == "765432":
            return {"ok": False, "error": {
                "code": "bot_offline", "message": "selected QQ bot is offline"}}
        return {"ok": True, "message_id": "msg-1"}

    jobs.register_scheduled_tasks(qq_send=fake_qq_send)
    success = _create_scheduled_qq_job(client, target_type="user", target_id="123456")
    offline = _create_scheduled_qq_job(
        client, target_type="group", target_id="654321", bot_uin="765432")
    assert success["ok"] and offline["ok"]

    for index, created in enumerate((success, offline)):
        saved = scheduler_store._job_for_task(created["job"]["taskId"])
        due = {**saved["schedule"][0],
               "nextFireAt": (datetime.now() - timedelta(seconds=5)).isoformat()}
        patch = {"schedule": [due]}
        if index == 0:
            # Recover a stale in-flight claim and retry its due targetless action.
            patch.update(status="running", runStartedAt=time.time() - 60)
        jobs._update(saved["jobId"], patch,
                     registry_root=scheduler_store.data_root())

    assert asyncio.run(jobs.run_due_scheduled_tasks()) == 2
    success_job = jobs.get(success["job"]["jobId"], registry_root=scheduler_store.data_root())
    offline_job = jobs.get(offline["job"]["jobId"], registry_root=scheduler_store.data_root())
    assert success_job["targetSessionId"] is None
    assert success_job["lastStatus"] == "completed"
    assert success_job["lastDelivery"]["messageId"] == "msg-1"
    assert offline_job["lastStatus"] == "error"
    assert "offline" in offline_job["lastError"]
    assert offline_job["undeliveredFires"] == []
    success_run = jobs.list_run_records(
        task_id=success["job"]["taskId"], registry_root=scheduler_store.data_root())[0]
    offline_run = jobs.list_run_records(
        task_id=offline["job"]["taskId"], registry_root=scheduler_store.data_root())[0]
    assert success_run["status"] == "completed"
    assert offline_run["status"] == "error"
    assert {call["target_id"]: call for call in calls} == {
        "123456": {"target_type": "private", "target_id": "123456",
                   "text": "hello QQ", "bot_uin": "987654"},
        "654321": {"target_type": "group", "target_id": "654321",
                   "text": "hello QQ", "bot_uin": "765432"},
    }


def test_scheduled_qq_run_now_is_targetless_and_records_plugin_failure(client):
    calls = []

    async def fake_qq_send(*, target_type, target_id, text, bot_uin=None):
        calls.append((target_type, target_id, text, bot_uin))
        if bot_uin == "765432":
            return {"ok": False, "error": {
                "code": "connection_error", "message": "QQ plugin is unavailable"}}
        return {"ok": True, "message_id": "manual-1"}

    jobs.register_scheduled_tasks(qq_send=fake_qq_send)
    success = _create_scheduled_qq_job(client)
    offline = _create_scheduled_qq_job(client, bot_uin="765432")
    assert success["ok"] and offline["ok"]

    success_run = client.post(
        f"/api/jobs/{success['job']['jobId']}/run-now").json()
    offline_run = client.post(
        f"/api/jobs/{offline['job']['jobId']}/run-now").json()
    assert success_run["ok"] is True
    assert success_run["run"]["status"] == "completed"
    assert success_run["run"]["session_id"] is None
    assert success_run["run"]["result"]["messageId"] == "manual-1"
    assert offline_run["ok"] is True
    assert offline_run["run"]["status"] == "error"
    assert "unavailable" in offline_run["run"]["error"]
    assert len(client.get(
        f"/api/jobs/{offline['job']['jobId']}/runs").json()["runs"]) == 1
    assert calls == [
        ("private", "123456", "hello QQ", "987654"),
        ("private", "123456", "hello QQ", "765432"),
    ]


def test_scheduled_qq_backlog_retry_runs_without_session_target(client):
    calls = []

    async def fake_qq_send(*, target_type, target_id, text, bot_uin=None):
        calls.append((target_type, target_id, text, bot_uin))
        return {"ok": True, "message_id": "replay-1"}

    jobs.register_scheduled_tasks(qq_send=fake_qq_send)
    created = _create_scheduled_qq_job(client)
    assert created["ok"] is True
    saved = jobs.get(created["job"]["jobId"], registry_root=scheduler_store.data_root())
    note = {
        "entryId": saved["schedule"][0]["id"],
        "fireAt": (datetime.now() - timedelta(seconds=5)).isoformat(),
        "dispatchKey": f"{saved['taskId']}:retry-fixture",
        "text": saved["text"],
        "error": "recovered pending QQ send",
    }
    saved = jobs._update(saved["jobId"], {
        "undeliveredFires": [note],
        "targetSessionId": None,
    }, registry_root=scheduler_store.data_root())

    assert asyncio.run(jobs._redeliver_undelivered(
        saved, scheduler_store.data_root())) == 1
    refreshed = jobs.get(saved["jobId"], registry_root=scheduler_store.data_root())
    run = jobs.list_run_records(
        task_id=saved["taskId"], registry_root=scheduler_store.data_root())[0]
    assert calls == [("private", "123456", "hello QQ", "987654")]
    assert refreshed["undeliveredFires"] == []
    assert refreshed["lastStatus"] == "completed"
    assert refreshed["lastDelivery"]["messageId"] == "replay-1"
    assert run["status"] == "completed"


def test_scheduled_qq_registered_callback_posts_only_to_plugin_send_route(monkeypatch):
    from packages.web import server as web_server

    requests = []

    async def fake_post(path, body):
        requests.append((path, body))
        if body.get("bot_uin") == "765432":
            return {"ok": False, "error": {
                "code": "bot_offline", "message": "selected bot is offline"}}
        return {"ok": True, "message_id": "plugin-message-1"}

    monkeypatch.setattr(web_server, "_qq_plugin_post", fake_post)
    sent = asyncio.run(web_server._send_scheduled_qq(
        target_type="private", target_id="123456", text="fixed text",
        bot_uin="987654"))
    offline = asyncio.run(web_server._send_scheduled_qq(
        target_type="group", target_id="654321", text="fixed text",
        bot_uin="765432"))
    default = asyncio.run(web_server._send_scheduled_qq(
        target_type="private", target_id="123456", text="default channel",
        bot_uin=None))

    assert sent == {"ok": True, "message_id": "plugin-message-1"}
    assert offline["ok"] is False
    assert default["ok"] is True
    assert requests == [
        ("/api/qq/send", {"target_type": "private", "target_id": "123456",
                           "text": "fixed text", "bot_uin": "987654"}),
        ("/api/qq/send", {"target_type": "group", "target_id": "654321",
                           "text": "fixed text", "bot_uin": "765432"}),
        ("/api/qq/send", {"target_type": "private", "target_id": "123456",
                           "text": "default channel"}),
    ]


def test_post_accepts_session_message_and_rejects_empty_schedule(
        client, fake_session_repository):
    fake_session_repository("s")
    body = client.post("/api/jobs", json={
        "kind": "session-message", "target": {"sessionId": "s"},
        "text": "x", "schedule": {"type": "interval", "intervalSeconds": 60}}).json()
    assert body["ok"] is True, body
    assert body["job"]["kind"] == jobs.SESSION_MESSAGE_KIND
    assert body["job"]["schedule"] == {"type": "interval", "intervalSeconds": 60}

    body = client.post("/api/jobs", json={
        "kind": "scheduled-task", "target": {"sessionId": "s"},
        "text": "x", "schedule": []}).json()
    assert body["ok"] is False
    assert body["error"]["code"] == "invalid_schedule"

    unknown_action = client.post("/api/jobs", json={
        "kind": "scheduled-task", "action": {"api": "create_session"},
        "target": {"sessionId": "s"}, "text": "x",
        "schedule": {"kind": "interval", "intervalSec": 60},
    }).json()
    assert unknown_action["ok"] is False
    assert unknown_action["error"]["code"] == "invalid_argument"


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
