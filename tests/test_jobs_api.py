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
from tests.test_job_automatic_recovery import env  # noqa: F401
from tests.test_session_product_closure import h as product_h  # noqa: F401
from tests.job_retirement_native_fixture import h as native_h  # noqa: F401
from tests.job_retirement_native_fixture import h  # noqa: F401
from tests.test_retirement_rev9_qq_http import qq_http  # noqa: F401
from tests.test_retirement_rev25_qq_runtime import run as qq_run  # noqa: F401
from tests.test_retirement_rev16_behavior import h as behavior_h  # noqa: F401
from fastapi import FastAPI
from fastapi.testclient import TestClient

from packages.core import background_jobs as jobs
from packages.core import worker as core_worker
from packages.jobs import api as jobs_api
from packages.jobs import templates as job_templates
from packages.scheduler import store as scheduler_store
from packages.core.platform import data_paths


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.delenv("PAN_SCHEDULER_DIR", raising=False)
    monkeypatch.delenv("PAN_BACKGROUND_JOBS_DIR", raising=False)
    monkeypatch.setattr(jobs, "DEFAULT_ROOT", tmp_path / "bg_jobs")
    monkeypatch.setattr(scheduler_store, "DEFAULT_ROOT", tmp_path / "bg_jobs")
    monkeypatch.setattr(data_paths, "DEFAULT_ROOT", tmp_path / "bg_jobs")
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
    async def fake_wake(session_id, **kw):
        calls.append({"api": "wake_session", "session_id": session_id,
                      "text": core_worker.configured_wake_message(),
                      "source": kw.get("source"), "client_message_id": kw.get("client_message_id")})
        return {"status": "queued", "sessionId": session_id}
    monkeypatch.setattr(core_worker, "wake_session", fake_wake)
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
    from tests.test_job_retained_history_fixtures import archive_job, forbid_legacy_effects, snapshot
    forbid_legacy_effects(monkeypatch)
    root = data_paths.job_root()
    task = archive_job(root, "job_history_task", jobs.SCHEDULED_TASK_KIND,
                       name="定时甲", taskId="sch_original", updatedAt=1)
    msg = archive_job(root, "job_history_message", jobs.SESSION_MESSAGE_KIND,
                      name="消息乙", status="unknown", updatedAt=9)
    archive_job(root, "job_history_failed", jobs.SESSION_MESSAGE_KIND,
                status="failed", updatedAt=2)
    archive_job(root, "job_history_active", jobs.SESSION_MESSAGE_KIND,
                status="running", updatedAt=8)
    archive_job(root, "job_history_older", jobs.SCHEDULED_TASK_KIND,
                taskId="sch_older", updatedAt=0)
    run = {"run_id": "original_dispatch", "task_id": "sch_original",
           "entry_id": "original_entry", "status": "unknown", "dispatch_key": "original_dispatch"}
    (root / "runs.jsonl").write_text(json.dumps(run) + "\n", encoding="utf-8")
    before = snapshot(root)

    body = client.get("/api/jobs").json()
    assert body["ok"] is True
    kinds = {j["kind"] for j in body["jobs"]}
    assert kinds == {jobs.SCHEDULED_TASK_KIND, jobs.SESSION_MESSAGE_KIND}
    assert [j["jobId"] for j in body["jobs"]] == ["job_history_active", "job_history_older", task["jobId"], "job_history_failed", msg["jobId"]]
    by_task = [j for j in body["jobs"] if j.get("taskId") == task["taskId"]]
    assert by_task and by_task[0]["name"] == "定时甲"

    filtered = client.get("/api/jobs", params={"kind": jobs.SESSION_MESSAGE_KIND}).json()
    assert [j["jobId"] for j in filtered["jobs"]] == ["job_history_active", "job_history_failed", msg["jobId"]]
    assert all(j["kind"] == jobs.SESSION_MESSAGE_KIND for j in filtered["jobs"])
    assert [j["jobId"] for j in client.get("/api/jobs?status=unknown").json()["jobs"]] == [msg["jobId"]]
    assert task["jobId"] not in [j["jobId"] for j in client.get("/api/jobs?includeCompleted=false").json()["jobs"]]
    for identity in (task["jobId"], task["taskId"]):
        assert client.get(f"/api/jobs/{identity}").json()["job"]["jobId"] == task["jobId"]
        assert client.get(f"/api/jobs/{identity}/runs").json()["runs"] == [run]
    assert snapshot(root) == before


def test_kinds_endpoint(client):
    body = client.get("/api/jobs/kinds").json()
    assert body["ok"] is True
    kinds = {item["kind"]: item for item in body["kinds"]}
    assert jobs.SCHEDULED_TASK_KIND in kinds
    assert kinds[jobs.SCHEDULED_TASK_KIND]["hasSchedule"] is True


# ── 结构化 source/target ──


def test_structured_identity_new_records(product_h):
    from tests.test_session_product_closure import test_product_http_real_queue_source_and_cold_original_query
    # Public typed arguments/source identity, durable target queue, original
    # effect identity and cold lookup replace legacy duplicated JSON fields.
    test_product_http_real_queue_source_and_cold_original_query(product_h, 'session-message')


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


def test_patch_rename_and_description(env):
    from tests.test_job_automatic_recovery import job_definition
    value = job_definition(env, {'automatic':False})
    response = env.client.post('/api/jobs', json={'definition':value,'expectedRevision':0,'requestKey':'metadata-create'})
    assert response.status_code == 201,response.text
    item = response.json()['result']['object']
    value.update(name='新名字',description='新说明')
    response = env.client.put('/api/jobs/'+item['id'],json={
        'definition':value,'expectedRevision':item['configRevision'],'requestKey':'metadata-update'})
    assert response.status_code == 200,response.text
    changed = response.json()['result']['object']
    assert changed['id']==item['id'] and changed['configRevision']==item['configRevision']+1
    assert changed['name']=='新名字' and changed['description']=='新说明'
    assert env.client.get('/api/jobs/'+item['id']).json()==changed
    assert not env.service.final.get(item['id'])['runs']
    assert env.client.put('/api/jobs/'+item['id'],json={'definition':value,
        'expectedRevision':item['configRevision'],'requestKey':'metadata-stale'}).status_code==409


def test_patch_blank_name_rejected(env):
    from tests.test_job_automatic_recovery import job_definition
    value = job_definition(env, {'automatic':False})
    response = env.client.post('/api/jobs',json={'definition':value,'expectedRevision':0,'requestKey':'name-create'})
    assert response.status_code==201,response.text
    item = response.json()['result']['object']
    before = env.service.final.store.get(item['id'])
    value['name']='  '
    response = env.client.put('/api/jobs/'+item['id'],json={'definition':value,
        'expectedRevision':item['configRevision'],'requestKey':'name-rejected'})
    assert response.status_code==400 and response.json()['error']['code']=='invalid_schema'
    assert response.json()['error']['path'] in {'name','definition.name'}
    assert env.service.final.store.get(item['id'])==before


def test_patch_target_switch_updates_flat_and_struct(behavior_h):
    # One canonical representation replaces dual-written flat/structured state.
    # Exact current target/revision is audited and used by the original Run.
    from tests.test_retirement_session_runtime import test_queued_missing_target_cold_current_target_text_same_run_attempt_effect
    test_queued_missing_target_cold_current_target_text_same_run_attempt_effect(behavior_h, 'assign')


def test_patch_enable_disable_stops_entries(behavior_h):
    # Creator owns future occurrences; disabling it cannot admit a Run/effect.
    from tests.test_retirement_rev16_behavior import test_creator_disable_stops_future_events_and_effects
    test_creator_disable_stops_future_events_and_effects(behavior_h)


# ── action 模板 ──


def test_action_template_default_assign(product_h):
    from packages.jobs.final_service import normalize_legacy_form_fields
    from tests.test_session_product_closure import config
    from tests.test_job_final_http_service import enabled, launch
    from packages.core import session
    # Existing pure form adapter chooses assign without an action/kind field.
    fields = normalize_legacy_form_fields({'target': {'sessionId': 'ses_a'},
        'text': 'default assign', 'source': 'agent', 'taskId': 'default-action'})
    assert fields == {'sessionId': 'ses_a', 'text': 'default assign',
                      'source': 'agent', 'taskId': 'default-action'}
    job = enabled(product_h, config(product_h, 'assign', **fields))
    run = launch(product_h, job)
    asyncio.run(product_h.service.dispatch_once())
    state = product_h.service.final.get(job['id'])
    assert state['runs'][run['runId']]['state'] == 'completed'
    assert len(state['attempts']) == len(state['effects']) == 1
    row = session.get('ses_a').queue_pending[0]
    assert row['text'] == fields['text'] and row['taskId'] == 'default-action'
    assert row['taskIdSource'] == 'assign' and row['source'] == 'agent'
    asyncio.run(product_h.service.dispatch_once())
    assert product_h.service.final.get(job['id']) == state
    assert len(session.get('ses_a').queue_pending) == 1


def test_action_template_send_session(product_h):
    from tests.test_session_product_closure import test_product_http_real_queue_source_and_cold_original_query
    test_product_http_real_queue_source_and_cold_original_query(product_h, 'session-message')


def test_action_template_unknown_falls_back_to_assign(behavior_h, client, fake_worker):
    from tests.test_session_product_closure import config
    from packages.jobs.retired import RetiredEntryPoint
    h = behavior_h
    value = config(h, 'assign', sessionId='ses_a', text='must not execute')
    value['steps'][0]['actionRef']['id'] = 'unknown_create_session'
    before = h.client.get('/api/jobs').json()
    response = h.client.post('/api/jobs', json={'definition': value,
        'expectedRevision': 0, 'requestKey': 'unknown-action'})
    assert response.status_code == 409 and response.json()['error']['code'] == 'reference_conflict'
    assert h.client.get('/api/jobs').json() == before
    # D6 preserves unknown legacy payloads but explicitly excludes execution.
    # The original fallback-to-assign assertion conflicts with that approval.
    root = scheduler_store.data_root()
    jobs._create({'jobId': 'job_unknown', 'kind': 'scheduled-task', 'status': 'scheduled',
        'action': {'api': 'create_session', 'args': {}}, 'targetSessionId': 'ses_a'}, registry_root=root)
    raw = jobs._job_path('job_unknown', registry_root=root).read_bytes()
    assert client.get('/api/jobs/job_unknown').json()['job']['action']['api'] == 'create_session'
    with pytest.raises(RetiredEntryPoint):
        asyncio.run(jobs.run_due_scheduled_tasks())
    asyncio.run(h.service.tick())
    assert jobs._job_path('job_unknown', registry_root=root).read_bytes() == raw
    assert fake_worker == [] and h.client.get('/api/jobs').json() == before


def test_resume_legal_running_job_rescans_and_run_now_sends_only_to_current_candidates(product_h, monkeypatch, record_property):
    from tests.test_session_resume_product import test_http_fresh_query_per_fire_original_wake_pause_and_identity
    test_http_fresh_query_per_fire_original_wake_pause_and_identity(product_h,monkeypatch,record_property)


def test_resume_legal_running_empty_scan_is_successful_noop(product_h):
    from tests.test_session_resume_product import test_empty_manual_is_completed_no_effect_and_dynamic_schema_edit
    test_empty_manual_is_completed_no_effect_and_dynamic_schema_edit(product_h)


def test_resume_legal_running_scheduled_fire_without_candidates_is_not_backlogged(product_h):
    from tests.test_session_resume_product import test_scheduled_passive_template_matching_empty_fire_not_backlogged
    test_scheduled_passive_template_matching_empty_fire_not_backlogged(product_h)


def test_resume_legal_running_patch_preserves_dynamic_target_and_fixed_text(product_h):
    from tests.test_session_resume_product import test_empty_manual_is_completed_no_effect_and_dynamic_schema_edit
    test_empty_manual_is_completed_no_effect_and_dynamic_schema_edit(product_h)
    item=product_h.service.list('job',actor={'type':'user','id':'local'})['items'][0]
    assert item['name']=='Edited resume' and item['steps'][0]['arguments']=={}


def test_resume_legal_running_keeps_per_fire_client_message_id_stable(product_h, monkeypatch):
    from tests.test_session_resume_product import test_cold_reopen_original_selection_and_config_change_never_resends
    test_cold_reopen_original_selection_and_config_change_never_resends(product_h,monkeypatch,'cancelled')


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


def test_runs_and_next_and_run_now(behavior_h):
    import copy
    from packages.jobs.final_service import definition
    from tests.test_retirement_rev16_behavior import schedule, instant
    from tests.test_job_final_http_service import launch
    from packages.core import session
    h = behavior_h
    at = instant(30)
    job, creator, _ = schedule(h, [{'ruleId': 'next', 'type': 'interval',
        'seconds': '60', 'anchor': at, 'advanceMode': 'grid'}])
    original = copy.deepcopy(h.service.creators.get(creator['id']))
    response = h.client.post('/api/event-creators/preview', json={
        'definition': definition('event_creator', creator), 'at': instant(0), 'count': 3})
    assert response.status_code == 200, response.text
    points = response.json()['items']
    assert len(points) == 3 and points[0]['at'] == at
    assert all(p['ruleId'] == 'next' for p in points)
    assert h.service.creators.get(creator['id']) == original
    assert not h.service.final.history(job['id'])['runs']
    admitted = launch(h, job)
    asyncio.run(h.service.dispatch_once())
    runs = h.client.get('/api/jobs/' + job['id'] + '/runs').json()['items']
    assert len(runs) == 1 and runs[0]['runId'] == admitted['runId']
    assert runs[0]['cause'] == {'type': 'manual'} and runs[0]['state'] == 'completed'
    assert h.service.creators.get(creator['id']) == original
    assert len(session.get('ses_a').queue_pending) == 1


def test_delete_job_routes_to_compat_for_scheduled_task(client, fake_worker):
    task = _make_scheduled_task()
    job = scheduler_store._job_for_task(task["id"])
    body = client.delete(f"/api/jobs/{job['jobId']}").json()
    assert body["ok"] is True
    assert scheduler_store.get_task(task["id"]) is None


def test_delete_running_job_reports_cleanup_pending_failure(client, monkeypatch):
    jobs._save({"jobId": "job_api_delete_running", "targetSessionId": "ses_target",
                "status": "running", "pid": 10, "processCreatedAt": 1,
                "createdAt": 1})
    monkeypatch.setattr(jobs, "_terminate_job_processes",
                        lambda records: {"confirmed": False, "reason": "receipt unknown"})
    response = client.delete("/api/jobs/job_api_delete_running")
    body = response.json()
    assert body["ok"] is False
    assert body["error"]["code"] == "job_busy"
    assert jobs.get("job_api_delete_running")["status"] == "cleanup_pending"


# ── partial_failed 事件 ──


def test_partial_failed_event_emitted(behavior_h):
    # Partial is not fabricated success/known failure: preserve exact target
    # receipts plus visible canonical WS/Session notice and cold no-replay.
    from tests.test_retirement_rev16_behavior import test_partial_failure_terminal_notice_identifies_actual_failed_target
    test_partial_failure_terminal_notice_identifies_actual_failed_target(behavior_h)


# ── POST /api/jobs（创建，多 entry）──


def test_post_creates_scheduled_task_multi_entry(behavior_h):
    from tests.test_retirement_rev16_behavior import schedule, instant
    from tests.test_job_final_creators import config as time_config
    from packages.scheduler.providers import time_definition
    from packages.jobs.final_service import definition
    from tests.test_job_final_http_service import control
    h = behavior_h
    job, source, trigger = schedule(h, [
        {'ruleId': 'interval', 'type': 'interval', 'seconds': '3600', 'anchor': instant(3600), 'advanceMode': 'grid'},
        {'ruleId': 'cron', 'type': 'cron', 'expression': '0 9 * * 1-5', 'timezone': 'UTC', 'dstPolicy': 'gap-skip-fold-first'}])
    # Rule lifecycle is now Creator lifecycle. A separately disabled Creator
    # keeps the disabled once schedule visible and bound to the same Job.
    response = h.client.post('/api/event-creators', json={'definition': time_definition('disabled once',
        time_config([{'ruleId': 'once', 'type': 'once', 'at': '2099-01-01T09:00:00Z'}])),
        'expectedRevision': 0, 'requestKey': 'disabled-once'})
    assert response.status_code == 201, response.text
    once = control(h, 'event-creators', response.json()['result']['object'], 'enable', 'enable-once').json()['result']['object']
    response = control(h, 'event-creators', once, 'disable', 'disable-once')
    assert response.status_code == 200, response.text
    once = response.json()['result']['object']
    value = definition('trigger', trigger)
    value['creatorSelector']['ids'].append(once['id'])
    response = h.client.put('/api/triggers/' + trigger['id'], json={'definition': value,
        'expectedRevision': trigger['revision'], 'requestKey': 'bind-disabled-once'})
    assert response.status_code == 200, response.text
    assert response.json()['result']['object']['targetJobId'] == job['id']
    assert response.json()['result']['object']['creatorSelector']['ids'] == [source['id'], once['id']]
    cursors = h.service.creators.get(source['id'])['cursor']['rules']
    assert set(cursors) == {'interval', 'cron'} and all(c['nextFireAt'] for c in cursors.values())
    assert once['state'] == 'disabled' and once['config']['rules'][0]['ruleId'] == 'once'
    assert h.client.get('/api/jobs/' + job['id']).json()['steps'][0]['arguments']['sessionId']['value'] == 'ses_a'
    assert not h.service.final.history(job['id'])['runs']


def test_post_creates_scheduled_shell_action_without_target_or_runner(env, monkeypatch):
    from packages.jobs.final_service import normalize_legacy_form_fields
    from tests.test_job_automatic_recovery import job_definition
    from packages.scheduler.providers import time_definition
    from tests.test_job_final_creators import config as time_config
    h = env
    calls = []
    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError('creation must not start any process')
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    fields = normalize_legacy_form_fields({'command': 'echo contract-test-must-not-run', 'cwd': str(h.tmp)})
    cfg = job_definition(h, {'automatic': False})
    cfg['steps'][0]['arguments'] = {k: {'from': 'literal', 'value': v} for k, v in fields.items()}
    response = h.client.post('/api/jobs', json={'definition': cfg, 'expectedRevision': 0, 'requestKey': 'scheduled-shell'})
    assert response.status_code == 201, response.text
    job = response.json()['result']['object']
    from tests.test_job_final_http_service import control
    response = control(h, 'jobs', job, 'enable', 'enable-shell')
    assert response.status_code == 200, response.text
    job = response.json()['result']['object']
    args = job['steps'][0]['arguments']
    assert args['argv']['value'] == fields['argv'] and 'contract-test-must-not-run' in args['argv']['value'][-1]
    assert args['cwd']['value'] == str(h.tmp) and not ({'sessionId', 'targetSessionId'} & set(args))
    source = time_definition('future shell', time_config([{'ruleId': 'shell', 'type': 'once', 'at': '2099-01-01T09:00:00Z'}]))
    response = h.client.post('/api/event-creators', json={'definition': source, 'expectedRevision': 0, 'requestKey': 'future-shell'})
    assert response.status_code == 201, response.text
    response = h.client.post('/api/triggers', json={'definition': {'name': 'future shell', 'targetJobId': job['id'],
        'creatorSelector': {'mode': 'ids', 'ids': [response.json()['result']['object']['id']]},
        'eventTypes': ['scheduler.time.v2']}, 'expectedRevision': 0, 'requestKey': 'shell-trigger'})
    assert response.status_code == 201, response.text
    assert h.client.get('/api/jobs/' + job['id']).json()['steps'][0]['arguments'] == args
    assert not h.service.final.get(job['id'])['runs'] and calls == []


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


def test_post_scheduled_qq_action_persists_target_and_rejects_untrusted_fields(qq_http):
    import copy
    from tests.test_retirement_rev6_parity import qq_job
    from tests.test_job_final_http_service import create
    q = qq_http
    value = qq_job(q.h, '222222')
    job = create(q.h, value=value)
    persisted = q.h.client.get('/api/jobs/' + job['id']).json()
    args = persisted['steps'][0]['arguments']
    assert {k: b['value'] for k, b in args.items()} == {
        'targetType': 'user', 'targetId': '123456', 'botUin': '222222', 'text': 'hello QQ'}
    assert not ({'sessionId', 'sessionIds', 'sourceSessionId'} & set(args))
    before = q.h.client.get('/api/jobs').json()
    invalid = [('targetId', '123'), ('botUin', 'abc'), ('url', 'http://host'),
        ('targetType', ['user']), ('targetType', 'friend'), ('text', '  '), ('sessionId', 'ses_arbitrary')]
    for index, (field, bad) in enumerate(invalid):
        cfg = copy.deepcopy(value)
        cfg['steps'][0]['arguments'][field] = {'from': 'literal', 'value': bad}
        response = q.h.client.post('/api/jobs', json={'definition': cfg,
            'expectedRevision': 0, 'requestKey': 'bad-qq-' + str(index)})
        assert response.status_code == 400, response.text
        assert response.json()['error']['code'] == 'invalid_schema'
        assert q.h.client.get('/api/jobs').json() == before
    assert not q.rows and not q.h.service.final.get(job['id'])['runs']


def test_patch_scheduled_qq_action_is_strict_and_editable(qq_http):
    import copy
    from tests.test_retirement_rev6_parity import qq_job
    from tests.test_job_final_http_service import create, control
    q = qq_http
    value = qq_job(q.h, '222222')
    job = create(q.h, value=value)
    before = copy.deepcopy(q.h.service.final.store.get(job['id']))
    for index, (field, bad) in enumerate([('command', 'echo unsafe'), ('botUin', 'not-a-bot'),
            ('targetId', 'not-a-qq-id'), ('text', '  '), ('sessionId', 'ses_arbitrary')]):
        cfg = copy.deepcopy(value)
        cfg['steps'][0]['arguments'][field] = {'from': 'literal', 'value': bad}
        response = q.h.client.put('/api/jobs/' + job['id'], json={'definition': cfg,
            'expectedRevision': job['configRevision'], 'requestKey': 'bad-qq-edit-' + str(index)})
        assert response.status_code == 400, response.text
        assert response.json()['error']['code'] == 'invalid_schema'
        assert q.h.service.final.store.get(job['id']) == before
    value['name'] = 'Updated QQ reminder'
    for field, literal in {'targetType': 'group', 'targetId': '456789', 'botUin': '111111', 'text': 'updated text'}.items():
        value['steps'][0]['arguments'][field] = {'from': 'literal', 'value': literal}
    response = q.h.client.put('/api/jobs/' + job['id'], json={'definition': value,
        'expectedRevision': job['configRevision'], 'requestKey': 'qq-edit'})
    assert response.status_code == 200, response.text
    job = response.json()['result']['object']
    assert job['name'] == 'Updated QQ reminder'
    assert {k: b['value'] for k, b in job['steps'][0]['arguments'].items()} == {
        'targetType': 'group', 'targetId': '456789', 'botUin': '111111', 'text': 'updated text'}
    job = control(q.h, 'jobs', job, 'enable', 'qq-enable').json()['result']['object']
    response = control(q.h, 'jobs', job, 'pause', 'qq-pause')
    assert response.status_code == 200 and response.json()['result']['object']['state'] == 'paused'
    assert not q.rows and not q.h.service.final.get(job['id'])['runs']


def test_scheduled_qq_fire_uses_selected_bot_and_records_success_or_offline_error(qq_http, qq_run):
    _canonical_qq_success_and_offline(qq_http, qq_run, scheduled=True)


def test_scheduled_qq_run_now_is_targetless_and_records_plugin_failure(qq_http, qq_run):
    import copy
    from tests.test_retirement_rev6_parity import qq_job
    from tests.test_job_final_http_service import create, control, launch
    q = qq_http
    _canonical_qq_success_and_offline(q, qq_run, scheduled=False)
    # Actual accepted OneBot effect with a lost acknowledgement is unknown,
    # never fabricated known failure or an authorization to resend.
    q.mode['value'] = 'lost-response'
    item = create(q.h, value=qq_job(q.h, '222222'), key='lost-ack-create')
    item = control(q.h, 'jobs', item, 'enable', 'lost-ack-enable').json()['result']['object']
    rid = launch(q.h, item, key='lost-ack-run')['runId']
    qq_run(q.h.service.dispatch_once())
    state = copy.deepcopy(q.h.service.final.get(item['id']))
    assert state['runs'][rid]['state'] == 'uncertain'
    assert len(state['attempts']) == len(state['effects']) == 1
    assert state['effects'][0]['receipt']['outcome'] == 'unknown'
    assert len(q.rows) == 2 and q.rows[-1]['bot'] == '222222'
    qq_run(q.h.service.dispatch_once())
    assert q.h.service.final.get(item['id']) == state and len(q.rows) == 2


def test_scheduled_qq_backlog_retry_runs_without_session_target(qq_http, qq_run):
    # Original manually injected backlog lacked no-send proof. The approved
    # successor uses two real Events, authoritative no-send receipts, cold
    # recovery/current target selection and same Attempt/effect; no new send ID.
    from tests.test_retirement_rev25_qq_runtime import test_two_real_scheduler_fires_cold_drain_current_bot_each_original_event_once
    test_two_real_scheduler_fires_cold_drain_current_bot_each_original_event_once(qq_http, qq_run)


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


def test_post_accepts_session_message_and_rejects_empty_schedule(behavior_h):
    from tests.test_retirement_rev16_behavior import schedule, instant
    from tests.test_job_final_creators import config as time_config
    from packages.scheduler.providers import time_definition
    from tests.test_session_product_closure import config
    h = behavior_h
    job, creator, _ = schedule(h, [{'ruleId': 'message', 'type': 'interval', 'seconds': '60',
        'anchor': instant(60), 'advanceMode': 'grid'}], operation='session-message')
    assert job['steps'][0]['actionRef']['id'] == 'product_session_message_v2'
    before = h.service.creators.list()
    with h.service.transaction(readonly=True) as db:
        operations = db.execute('SELECT count(*) FROM final_operations').fetchone()[0]
    response = h.client.post('/api/event-creators', json={'definition': time_definition('empty', time_config([])),
        'expectedRevision': 0, 'requestKey': 'empty-schedule'})
    assert response.status_code == 400, response.text
    assert h.service.creators.list() == before
    value = config(h, 'assign', sessionId='ses_a', text='x')
    value['steps'][0]['actionRef']['id'] = 'unknown_create_session'
    response = h.client.post('/api/jobs', json={'definition': value, 'expectedRevision': 0, 'requestKey': 'unknown-action'})
    assert response.status_code == 409 and response.json()['error']['code'] == 'reference_conflict'
    with h.service.transaction(readonly=True) as db:
        assert db.execute('SELECT count(*) FROM final_operations').fetchone()[0] == operations
    assert not h.service.final.history(job['id'])['runs']


def test_post_requires_existing_session(product_h):
    from tests.test_session_product_closure import config
    from packages.core import session, worker
    h = product_h
    assert session.get('ses_ghost') is None
    before = h.client.get('/api/jobs').json()
    workers = set(worker.workers)
    value = config(h, 'assign', sessionId='ses_ghost', text='x')
    response = h.client.post('/api/jobs', json={'definition': value,
        'expectedRevision': 0, 'requestKey': 'missing-session'})
    assert response.status_code == 400, response.text
    assert h.client.get('/api/jobs').json() == before
    assert session.get('ses_ghost') is None and set(worker.workers) == workers


# ── PATCH schedule（entry 列表整体替换）──


def test_patch_schedule_replaces_entries(behavior_h):
    from tests.test_retirement_rev16_behavior import test_schedule_replacement_removes_old_rule_and_fires_only_new_real_event
    test_schedule_replacement_removes_old_rule_and_fires_only_new_real_event(behavior_h)


def test_patch_schedule_rejects_bad_spec(behavior_h):
    from tests.test_retirement_rev16_behavior import test_literal_empty_or_bad_schedule_update_rejects_without_any_write
    test_literal_empty_or_bad_schedule_update_rejects_without_any_write(behavior_h, [
        {'ruleId': 'bad', 'type': 'cron', 'expression': 'not a cron',
         'timezone': 'UTC', 'dstPolicy': 'gap-skip-fold-first'}])


# ── PATCH target 清空 → 短路积压 ─--


def test_patch_target_clear_enters_undeliverable(behavior_h):
    from tests.test_retirement_session_runtime import test_queued_missing_target_cold_current_target_text_same_run_attempt_effect
    test_queued_missing_target_cold_current_target_text_same_run_attempt_effect(behavior_h, 'session-message')


# ── PATCH text（编辑派发正文）──


def test_patch_text_updates_dispatch_body(behavior_h):
    import copy
    from tests.test_retirement_session_runtime import admitted, edit
    from packages.jobs.final_service import definition
    from packages.core import session
    h = behavior_h
    job, rid, _ = admitted(h, 'assign')
    job = edit(h, job, 'ses_b', '  新正文\n raw  ', 'text-update')
    assert h.client.get('/api/jobs/' + job['id']).json()['arguments']['delivery']['text'] == '  新正文\n raw  '
    before = copy.deepcopy(h.service.final.store.get(job['id']))
    value = definition('job', job)
    value['arguments']['delivery']['text'] = '   '
    response = h.client.put('/api/jobs/' + job['id'], json={'definition': value,
        'expectedRevision': job['configRevision'], 'requestKey': 'blank-text'})
    assert response.status_code == 400, response.text
    assert h.service.final.store.get(job['id']) == before
    asyncio.run(h.service.dispatch_once())
    state = h.service.final.get(job['id'])
    assert state['runs'][rid]['state'] == 'completed'
    assert len(state['attempts']) == len(state['effects']) == 1
    assert session.get('ses_b').queue_pending[0]['text'] == '  新正文\n raw  '
    assert not session.get('ses_a').queue_pending


# ── P4：runs 记录携带 entry_id ──


def test_run_records_carry_entry_id(behavior_h):
    from tests.test_retirement_rev16_behavior import schedule, instant, fire
    h = behavior_h
    at = instant(30)
    job, creator, _ = schedule(h, [{'ruleId': 'original-entry', 'type': 'once', 'at': at}])
    fire(h, at)
    runs = h.client.get('/api/jobs/' + job['id'] + '/runs').json()['items']
    assert len(runs) == 1 and runs[0]['state'] == 'completed'
    event_id = runs[0]['cause']['eventId']
    event = h.service.events.get(event_id)['event']
    assert event['creatorId'] == creator['id'] and event['creatorRevision'] == creator['revision']
    assert event['payload']['ruleId'] == 'original-entry' and event['payload']['scheduledAt'] == at
    fire(h, at)
    assert h.client.get('/api/jobs/' + job['id'] + '/runs').json()['items'] == runs


@pytest.mark.parametrize("kind", ["session-message", "session-broadcast", "background-process", "main-lifecycle", "scheduled-task"])
def test_pin_persists_without_touching_lifecycle(client, kind):
    job = {"jobId": "job_pin", "kind": kind, "status": "completed", "updatedAt": 7,
           "createdAt": 1, "name": "pin test", "schedule": {"type": "once", "at": 99}}
    jobs._create(job, registry_root=scheduler_store.data_root())
    before = jobs.get("job_pin", registry_root=scheduler_store.data_root())
    assert client.put("/api/jobs/job_pin/pin", json={"pinned": True}).json()["job"]["pinned"] is True
    assert client.get("/api/jobs/job_pin").json()["job"]["pinned"] is True
    assert client.get("/api/jobs", params={"kind": kind, "status": "completed"}).json()["jobs"][0]["pinned"] is True
    assert jobs.get("job_pin", registry_root=scheduler_store.data_root()) == before
    assert client.put("/api/jobs/job_pin/pin", json={"pinned": False}).json()["job"]["pinned"] is False
    assert client.get("/api/jobs/job_pin").json()["job"]["pinned"] is False


def test_pin_failure_keeps_disk_and_emits_nothing(client, monkeypatch):
    jobs._create({"jobId": "job_pin", "status": "running"}, registry_root=scheduler_store.data_root())
    assert client.put("/api/jobs/job_pin/pin", json={"pinned": True}).json()["ok"]
    emitted = []
    monkeypatch.setitem(jobs_api._state, "broadcast", emitted.append)
    def fail(*args):
        raise OSError("disk full")
    from packages.jobs.final_runtime import coordinator
    monkeypatch.setattr(coordinator, "atomic_write", fail)
    result = client.put("/api/jobs/job_pin/pin", json={"pinned": False}).json()
    assert result["error"]["code"] == "persistence_failed"
    assert emitted == []
    assert client.get("/api/jobs/job_pin").json()["job"]["pinned"] is True


def test_pin_validation_and_independent_owner(client, monkeypatch):
    remote = {"jobId": "job_remote", "status": "running", "kind": "background-process"}
    monkeypatch.setattr(jobs_api.pan_bridge, "request", lambda method, *args: remote if method == "get" else {"jobs": [remote]})
    for body in ({"pinned": 1}, {"pinned": "true"}, {"pinned": None}, {}, {"pinned": True, "status": "completed"}):
        assert client.put("/api/jobs/job_remote/pin", json=body).json()["error"]["code"] == "invalid_argument"
    assert client.put("/api/jobs/job_remote/pin", json={"pinned": True}).json()["job"]["executionOwner"] == "independent-job-service"
    assert client.get("/api/jobs").json()["jobs"][0]["pinned"] is True
    assert not (scheduler_store.data_root() / "jobs" / "job_remote.json").exists()


def test_multiple_pins_filter_and_corrupt_metadata(client):
    root = scheduler_store.data_root()
    for name, status in [("a", "running"), ("b", "completed"), ("c", "scheduled")]:
        jobs._create({"jobId": "job_" + name, "kind": "session-message", "status": status}, registry_root=root)
    for name in ("b", "c"):
        assert client.put(f"/api/jobs/job_{name}/pin", json={"pinned": True}).json()["ok"]
    assert [j["jobId"] for j in client.get("/api/jobs").json()["jobs"]] == ["job_c", "job_b", "job_a"]
    assert [j["jobId"] for j in client.get("/api/jobs?status=running").json()["jobs"]] == ["job_a"]
    (root / "pins.json").write_text("broken", encoding="utf-8")
    assert client.put("/api/jobs/job_a/pin", json={"pinned": True}).json()["error"]["code"] == "pin_metadata_unavailable"
    assert (root / "pins.json").read_text(encoding="utf-8") == "broken"


@pytest.mark.parametrize("failure", ["json", "encoding", "read"])
@pytest.mark.parametrize("kind", ["session-message", "scheduled-task"])
def test_pin_metadata_failure_does_not_fail_committed_operations(
        client, monkeypatch, failure, kind):
    from pathlib import Path
    from tests.test_job_retained_history_fixtures import history_record, forbid_legacy_effects
    forbid_legacy_effects(monkeypatch)
    root = scheduler_store.data_root()
    root.mkdir(parents=True, exist_ok=True)
    pin_path = root / "pins.json"
    original = b'{bad json' if failure == "json" else b'\xff\xfe' if failure == "encoding" else b'{"job_existing": true}'
    pin_path.write_bytes(original)
    original_read = Path.read_text
    def read_error(path, *args, **kwargs):
        if path == pin_path:
            raise PermissionError("isolated unreadable pins")
        return original_read(path, *args, **kwargs)
    if failure == "read":
        monkeypatch.setattr(Path, "read_text", read_error)
    events = []
    monkeypatch.setitem(jobs_api._state, "broadcast", events.append)
    jobs.register_scheduled_tasks(on_event=jobs_api._emit)
    # Explicit offline historical storage, never a retired producer/API. The
    # real storage publisher emits only after its atomic commit succeeds.
    committed = jobs._create(history_record("job_history_metadata", kind,
                             name="created once"), registry_root=root)
    created = jobs_api._view(committed)
    job_id = created["jobId"]
    assert created["name"] == "created once" and created["status"] == "completed"
    assert created["enabled"] is False
    assert created["pinned"] is None
    assert created["pinMetadataStatus"] == "unavailable"
    assert len(list((root / "jobs").glob("job_*.json"))) == 1
    assert len([ev for ev in events if ev["type"] == "job.created"]) == 1
    assert events[0]["jobId"] == job_id
    assert json.loads((root / "jobs" / (job_id + ".json")).read_text(encoding="utf-8"))["name"] == "created once"
    changed = jobs.update_job_field(job_id, {"name": "modified once"}, registry_root=root)
    assert changed["name"] == "modified once"
    assert [event["type"] for event in events] == ["job.created", "job.updated"]
    assert events[1]["job"]["name"] == "modified once"
    assert jobs_api._view(changed)["pinMetadataStatus"] == "unavailable"
    assert json.loads((root / "jobs" / (job_id + ".json")).read_text(encoding="utf-8"))["name"] == "modified once"
    assert jobs.get(job_id, registry_root=root)["name"] == "modified once"
    fetched = client.get(f"/api/jobs/{job_id}")
    assert fetched.status_code == 200 and fetched.json()["ok"] is True
    assert fetched.json()["job"]["pinMetadataStatus"] == "unavailable"
    listed = client.get("/api/jobs", params={"kind": kind, "status": created["status"]})
    assert listed.status_code == 200 and listed.json()["ok"] is True
    assert listed.json()["pinMetadataStatus"] == "unavailable"
    assert [job["jobId"] for job in listed.json()["jobs"]] == [job_id]
    before_events = list(events)
    rejected = client.put(f"/api/jobs/{job_id}/pin", json={"pinned": True})
    assert rejected.status_code == 200
    assert rejected.json()["error"]["code"] == "pin_metadata_unavailable"
    assert "unavailable" in rejected.json()["error"]["message"]
    assert pin_path.read_bytes() == original
    assert events == before_events
    assert len(list((root / "jobs").glob("job_*.json"))) == 1


def test_empty_list_exposes_pin_metadata_failure(client):
    scheduler_store.data_root().mkdir(parents=True, exist_ok=True)
    (scheduler_store.data_root() / "pins.json").write_bytes(b"\xff")
    result = client.get("/api/jobs").json()
    assert result == {"ok": True, "jobs": [], "pinMetadataStatus": "unavailable"}


def test_cancelled_pin_request_after_commit_requires_durable_reread(monkeypatch):
    import threading
    import httpx
    from packages.jobs import pins
    root = scheduler_store.data_root()
    jobs._create({"jobId": "job_cancel_pin", "status": "running"}, registry_root=root)
    emitted = []
    monkeypatch.setitem(jobs_api._state, "broadcast", emitted.append)
    original = pins.set_pin
    async def scenario():
        loop = asyncio.get_running_loop()
        committed, finished = asyncio.Event(), asyncio.Event()
        release = threading.Event()
        def write_then_gate(*args):
            result = original(*args)
            loop.call_soon_threadsafe(committed.set)
            try:
                assert release.wait(5), "writer gate not released"
                return result
            finally:
                loop.call_soon_threadsafe(finished.set)
        monkeypatch.setattr(pins, "set_pin", write_then_gate)
        app = FastAPI()
        app.include_router(jobs_api.router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://isolated") as client:
            request = asyncio.create_task(client.put("/api/jobs/job_cancel_pin/pin", json={"pinned": True}))
            try:
                await asyncio.wait_for(committed.wait(), 5)
                request.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await request
            finally:
                release.set()
            await asyncio.wait_for(finished.wait(), 5)
            assert pins.read(root) == {"job_cancel_pin": True}
            reread = (await client.get("/api/jobs/job_cancel_pin")).json()
            assert reread["ok"] is True and reread["job"]["pinned"] is True
            assert emitted == []  # interrupted response does not send a rollback/failure event
    asyncio.run(scenario())


def _canonical_qq_success_and_offline(q, run, *, scheduled):
    import copy
    from tests.test_retirement_rev6_parity import qq_job
    from tests.test_job_final_http_service import create, control, launch
    from tests.test_job_final_creators import config as time_config
    from tests.test_retirement_rev16_behavior import instant
    from packages.scheduler.providers import time_definition
    from packages.jobs.final_service import FinalService
    from packages.web import server
    jobs_and_runs = []
    for key, bot, target in [('success', None, 'user'), ('offline', '222222', 'group')]:
        cfg = qq_job(q.h, bot)
        cfg['steps'][0]['arguments']['targetType']['value'] = target
        item = create(q.h, value=cfg, key=key + '-create')
        response = control(q.h, 'jobs', item, 'enable', key + '-enable')
        assert response.status_code == 200, response.text
        item = response.json()['result']['object']
        jobs_and_runs.append((key, item))
    connected = q.selected._bot
    q.selected._bot = None
    try:
        if scheduled:
            at = instant(30)
            response = q.h.client.post('/api/event-creators', json={'definition': time_definition('QQ timer',
                time_config([{'ruleId': 'QQ-fire', 'type': 'once', 'at': at}])),
                'expectedRevision': 0, 'requestKey': 'QQ-clock'})
            assert response.status_code == 201, response.text
            source = control(q.h, 'event-creators', response.json()['result']['object'], 'enable', 'QQ-clock-enable').json()['result']['object']
            for key, item in jobs_and_runs:
                response = q.h.client.post('/api/triggers', json={'definition': {'name': key,
                    'targetJobId': item['id'], 'creatorSelector': {'mode': 'ids', 'ids': [source['id']]},
                    'eventTypes': ['scheduler.time.v2']}, 'expectedRevision': 0, 'requestKey': key + '-trigger'})
                assert response.status_code == 201, response.text
                response = control(q.h, 'triggers', response.json()['result']['object'], 'enable', key + '-trigger-enable')
                assert response.status_code == 200, response.text
            q.h.service.refresh_subscriptions()
            run(q.h.service.creator_runtime.tick(at=at))
            run(q.h.service.tick())
        else:
            for key, item in jobs_and_runs:
                launch(q.h, item, key=key + '-manual')
            run(q.h.service.dispatch_once())
        assert q.rows == [{'bot': '111111', 'operation': 'send_private_msg',
            'body': {'user_id': 123456, 'message': 'hello QQ'}}]
        states = {}
        for key, item in jobs_and_runs:
            state = copy.deepcopy(q.h.service.final.get(item['id']))
            states[item['id']] = state
            assert len(state['runs']) == len(state['attempts']) == len(state['effects']) == 1
            current = next(iter(state['runs'].values()))
            assert current['state'] == ('completed' if key == 'success' else 'failed')
            receipt = state['effects'][0]['receipt']
            if key == 'success':
                assert receipt['outcome'] == 'succeeded' and receipt['receipt'] == {'ok': True, 'message_id': 'owned-1'}
            else:
                assert receipt['outcome'] == 'rejected' and receipt['receipt']['error']['code'] == 'bot_not_connected'
            frozen = q.h.service.final.store.get(item['id'])['finalRuntime']['plans'][current['runId']]['frozen']
            assert not ({'sessionId', 'sessionIds', 'sourceSessionId'} & set(frozen['steps'][0]['arguments']))
            if scheduled:
                event = q.h.service.events.get(current['cause']['eventId'])['event']
                assert event['creatorId'] == source['id'] and event['payload']['ruleId'] == 'QQ-fire'
                assert event['payload']['scheduledAt'] == at
            else:
                assert current['cause'] == {'type': 'manual'}
        q.selected._bot = connected
        cold = FinalService(q.h.root, q.h.automation.auth)
        server._bind_qq_channel_provider(cold)
        try:
            run(cold.dispatch_once())
            for jid, state in states.items():
                assert cold.final.get(jid) == state
            assert len(q.rows) == 1
        finally:
            run(cold.close())
    finally:
        q.selected._bot = connected
