import asyncio

import pytest

import packages.mcp.server as mcp_server
import packages.web.server as web_server
from tests.test_job_automatic_recovery import env  # noqa: F401
from tests.test_session_product_closure import h  # noqa: F401


def test_http_start_reports_missing_session(h):
    # The destination is a typed Session Action target, not a process owner.
    from tests.test_session_product_closure import test_product_missing_target_rejects_without_spawn
    test_product_missing_target_rejects_without_spawn(h)


def test_http_start_invalid_argv_and_cwd(env):
    from tests.test_job_automatic_recovery import job_definition
    from tests.test_job_final_executors import test_r5_invalid_process_plan_is_refused_before_spawn
    value = job_definition(env, {'automatic': False})
    value['steps'][0]['arguments']['argv']['value'] = []
    response = env.client.post('/api/jobs', json={'definition':value,
        'expectedRevision':0, 'requestKey':'invalid-argv'})
    assert response.status_code == 400 and response.json()['error']['code'] == 'invalid_schema'
    assert env.service.list('job', actor={'type':'user','id':'local'})['items'] == []
    test_r5_invalid_process_plan_is_refused_before_spawn(env.tmp, {'cwd':'.'})


def test_http_start_forwards_creator_and_target_separately(h):
    from tests.test_session_product_closure import test_product_http_real_queue_source_and_cold_original_query
    test_product_http_real_queue_source_and_cold_original_query(h, 'assign')
    row = h.owned['ses_a'].queue_pending[0]
    assert row['sourceSessionId'] == 'ses_source' and not h.owned['ses_source'].queue_pending
    job = h.service.list('job', actor={'type':'user','id':'local'})['items'][0]
    state = h.service.final.get(job['id'])
    assert next(iter(state['admissions'].values()))['authority'] == {'type':'user','id':'local'}


def test_mcp_start_defaults_to_current_session_and_checks_access(monkeypatch):
    calls = []
    monkeypatch.setattr(mcp_server, "_caller_identity", lambda: {"id": "ses_self"})
    monkeypatch.setattr(mcp_server, "_check_access", lambda target, claim=False: None)
    monkeypatch.setattr(mcp_server, "_api", lambda method, path, body=None, timeout=30.0:
                        calls.append((method, path, body)) or {"jobId": "job_1"})
    result = mcp_server.agent_background_start(["python", "train.py"], "C:\\Pan", name="Validate background task")
    assert result["jobId"] == "job_1"
    assert calls[0][2]["targetSessionId"] == "ses_self"
    assert calls[0][2]["creatorSessionId"] == "ses_self"


def test_mcp_start_records_creator_when_target_is_managed(monkeypatch):
    calls = []
    monkeypatch.setattr(mcp_server, "_caller_identity", lambda: {"id": "ses_a"})
    monkeypatch.setattr(mcp_server, "_check_access", lambda target, claim=False: None)
    monkeypatch.setattr(mcp_server, "_api", lambda method, path, body=None, timeout=30.0:
                        calls.append((method, path, body)) or {"jobId": "job_t046"})

    result = mcp_server.agent_background_start(
        ["python", "job.py"], "C:\\Pan", target_session_id="ses_b", name="Validate managed task")
    assert result["jobId"] == "job_t046"
    assert calls[0][2]["creatorSessionId"] == "ses_a"
    assert calls[0][2]["targetSessionId"] == "ses_b"


def test_mcp_start_denies_unmanaged_target(monkeypatch):
    monkeypatch.setattr(mcp_server, "_caller_identity", lambda: {"id": "ses_self"})
    monkeypatch.setattr(mcp_server, "_check_access", lambda target, claim=False:
                        {"ok": False, "error": {"code": "permission_denied"}})
    called = []
    monkeypatch.setattr(mcp_server, "_api", lambda *args, **kwargs: called.append(args))
    result = mcp_server.agent_background_start(["python"], "C:\\Pan", "ses_other", name="Validate access")
    assert result["error"]["code"] == "permission_denied"
    assert called == []


def test_mcp_get_list_cancel_retry_use_managed_target(monkeypatch):
    calls = []
    monkeypatch.setattr(mcp_server, "_caller_identity", lambda: {"id": "ses_self"})
    monkeypatch.setattr(mcp_server, "_check_access", lambda target, claim=False: None)

    def api(method, path, body=None, timeout=30.0):
        calls.append((method, path, body))
        if method == "GET" and path.endswith("job_1"):
            return {"jobId": "job_1", "targetSessionId": "ses_managed"}
        if method == "GET":
            return {"jobs": []}
        return {"ok": True}

    monkeypatch.setattr(mcp_server, "_api", api)
    assert mcp_server.agent_background_get("job_1")["jobId"] == "job_1"
    assert mcp_server.agent_background_list()["jobs"] == []
    assert mcp_server.agent_background_cancel("job_1")["ok"]
    assert mcp_server.agent_background_retry("job_1")["ok"]
    assert any(path.endswith("/cancel") for _, path, _ in calls)
    assert any(path.endswith("/retry") for _, path, _ in calls)


def test_http_retry_rejects_running_job(env, monkeypatch):
    from tests.test_job_automatic_recovery import seed
    row = seed(env, monkeypatch)
    before = env.service.final.store.get(row.jid)
    run = env.service.final.get(row.jid)['runs'][row.rid]
    assert run['state'] == 'running'
    response = env.client.post('/api/job-runs/' + row.rid + '/operations', json={
        'operation':'retry', 'requestKey':'running-retry', 'expectedStateRevision':run['stateRevision']})
    assert response.status_code == 409 and response.json()['error']['code'] == 'not_retryable'
    assert env.service.final.store.get(row.jid) == before and not row.original.calls


def test_http_cancel_reports_unsafe_pid(monkeypatch):
    def unsafe(job_id):
        raise ValueError("cannot safely cancel: task PID identity is unavailable or reused")
    monkeypatch.setattr(web_server.background_jobs, "cancel", unsafe)
    result = asyncio.run(web_server.api_background_job_cancel("job_reused"))
    assert result["error"]["code"] == "cancel_unsafe"


def test_http_message_job_creation_preserves_source_and_text(h):
    from tests.support.canonical_session_schedule import schedule
    job, state = schedule(h, 'session-message', {'sessionId':'ses_a','text':'message text',
        'source':'agent','sourceSessionId':'ses_source'})
    assert next(iter(state['runs'].values()))['state'] == 'completed'
    row = h.owned['ses_a'].queue_pending[0]
    assert row['text'] == 'message text' and row['source'] == 'agent'
    assert row['sourceSessionId'] == 'ses_source' and not h.owned['ses_source'].queue_pending


def test_http_selected_session_broadcast_reuses_send_session(monkeypatch):
    class Target:
        id = "session"
        readonly_session = False
        managed_by = None

    monkeypatch.setattr(web_server.sess, "get", lambda sid: Target())
    calls = []

    async def send(session_id, text, **kwargs):
        calls.append((session_id, text, kwargs))
        return {"status": "queued", "workerId": None, "sessionId": session_id}

    monkeypatch.setattr(web_server.worker, "send_session", send)
    result = asyncio.run(web_server.api_sessions_broadcast({
        "sessionIds": ["ses_a", "ses_a", "ses_b"], "text": "hello",
        "source": "agent", "sourceSessionId": "ses_caller"}))
    assert result["ok"] is True
    assert [item[0] for item in calls] == ["ses_a", "ses_b"]
    assert all(item[2] == {"source": "agent", "force": False,
                           "client_message_id": None,
                           "source_session_id": "ses_caller"} for item in calls)


def test_mcp_message_job_shortcuts_preserve_caller_prefix(monkeypatch):
    calls = []
    monkeypatch.setattr(mcp_server, "_caller_identity",
                        lambda: {"id": "ses_caller"})
    monkeypatch.setattr(mcp_server, "_check_access", lambda target, claim=False: None)
    monkeypatch.setattr(mcp_server, "_agent_message_prefix",
                        lambda text: "PREFIX\n" + text)
    monkeypatch.setattr(mcp_server, "_api", lambda method, path, body=None,
                        timeout=30.0: calls.append((method, path, body)) or {
                            "jobId": "job_message", "kind": "session-message"})
    result = mcp_server.agent_message_job_create(
        "do it", {"type": "once", "delaySeconds": 5},
        target_session_id="ses_target", description="desc")
    assert result["jobId"] == "job_message"
    assert calls[0] == ("POST", "/api/session-message-jobs", {
        "targetSessionId": "ses_target", "text": "PREFIX\ndo it",
        "schedule": {"type": "once", "delaySeconds": 5},
        "description": "desc", "source": "agent",
        "sourceSessionId": "ses_caller", "creatorSessionId": "ses_caller"})


def test_http_scheduled_broadcast_checks_each_target_and_uses_broadcast_model(h):
    from tests.support.canonical_session_schedule import schedule
    job, state = schedule(h, 'session-broadcast', {'sessionIds':['ses_a','ses_b','ses_a'],
        'text':'hello','source':'agent','sourceSessionId':'ses_source'})
    assert next(iter(state['runs'].values()))['state'] == 'completed'
    children = state['effects'][0]['receipt']['receipt']['targets']
    assert [row['effectKey']['targetId'] for row in children] == ['ses_a','ses_b']
    assert all(row['outcome'] == 'succeeded' for row in children)
    for sid in ('ses_a','ses_b'):
        rows = h.owned[sid].queue_pending
        assert len(rows) == 1 and rows[0]['text'] == 'hello'
        assert rows[0]['source'] == 'agent' and rows[0]['sourceSessionId'] == 'ses_source'
    assert not h.owned['ses_source'].queue_pending


def test_mcp_scheduled_broadcast_preserves_prefix_and_denies_any_target(monkeypatch):
    calls = []
    monkeypatch.setattr(mcp_server, "_caller_identity", lambda: {"id": "ses_caller"})
    monkeypatch.setattr(mcp_server, "_agent_message_prefix",
                        lambda text: "PREFIX\n" + text)

    def check(target, claim=False):
        if target == "ses_denied":
            return {"ok": False, "error": {"code": "permission_denied"}}
        return None

    monkeypatch.setattr(mcp_server, "_check_access", check)
    monkeypatch.setattr(mcp_server, "_api", lambda method, path, body=None,
                        timeout=30.0: calls.append((method, path, body)) or {
                            "jobId": "job_broadcast", "kind": "session-broadcast"})
    result = mcp_server.agent_message_job_create(
        "do it", {"type": "weekly", "weekday": 1, "time": "09:00",
                  "timezone": "UTC"},
        target_session_ids=["ses_a", "ses_b"], description="broadcast")
    assert result["kind"] == "session-broadcast"
    assert calls[0] == ("POST", "/api/session-message-jobs", {
        "text": "PREFIX\ndo it",
        "schedule": {"type": "weekly", "weekday": 1, "time": "09:00",
                      "timezone": "UTC"},
        "source": "agent", "targetSessionIds": ["ses_a", "ses_b"],
        "description": "broadcast", "sourceSessionId": "ses_caller",
        "creatorSessionId": "ses_caller"})
    calls.clear()
    denied = mcp_server.agent_message_job_create(
        "do it", {"type": "once", "delaySeconds": 5},
        target_session_ids=["ses_a", "ses_denied"])
    assert denied["error"]["code"] == "permission_denied"
    assert calls == []


@pytest.mark.parametrize("name", ["", "   ", "x" * 201, None])
def test_mcp_background_name_invalid(monkeypatch, name):
    monkeypatch.setattr(mcp_server, "_api", lambda *a, **k: pytest.fail("must not create"))
    assert mcp_server.agent_background_start(["python"], ".", name=name)["error"]["code"] == "invalid_job"


def test_mcp_background_name_required():
    import inspect
    assert inspect.signature(mcp_server.agent_background_start).parameters["name"].default is inspect.Parameter.empty


def test_mcp_background_name_and_optional_label(monkeypatch):
    monkeypatch.setattr(mcp_server, "_caller_identity", lambda: {"id": "ses_self"})
    monkeypatch.setattr(mcp_server, "_check_access", lambda *a, **k: None)
    monkeypatch.setattr(mcp_server, "_api", lambda method, path, body: body)
    result = mcp_server.agent_background_start(["python"], ".", name="  Validate cancellation  ")
    assert result["name"] == "Validate cancellation"
    assert result["label"] is None


@pytest.mark.parametrize("payload", [{}, {"name": ""}, {"name": "   "}, {"name": "x" * 201}])
def test_http_process_name_required_before_spawn(env, payload):
    from tests.test_job_automatic_recovery import job_definition
    value = job_definition(env, {'automatic': False})
    value.pop('name')
    value.update(payload)
    response = env.client.post('/api/jobs', json={'definition':value,
        'expectedRevision':0, 'requestKey':'invalid-name'})
    if not payload:
        assert response.status_code == 201, response.text
        job = response.json()['result']['object']
        assert job['name'] == 'job-1' and job['description'] == ''
        state = env.service.final.get(job['id'])
        assert not state['runs'] and not state['attempts'] and not state['effects']
        return
    assert response.status_code == 400 and response.json()['error']['code'] == 'invalid_schema'
    assert 'name' in str(response.json()['error'])
    assert env.service.list('job', actor={'type':'user','id':'local'})['items'] == []


def test_public_background_tool_schema_requires_name_only():
    """R5: the old start tool is gone from the catalog.

    The previous version asserted the ``agent_background_start`` input schema.
    That tool is no longer discoverable, so the successor assertion is the
    retirement itself plus the retained read/cancel tools that keep live
    legacy Jobs inspectable and cancellable.
    """
    tools = asyncio.run(mcp_server.mcp.list_tools())
    names = {tool.name for tool in tools}
    assert "agent_background_start" not in names
    assert "agent_background_retry" not in names
    assert {"agent_background_get", "agent_background_list",
            "agent_background_cancel"} <= names


@pytest.mark.parametrize('kind', ['unknown-field', 'long-name'])
def test_http_name_path_never_echoes_input_fields_or_values(env, kind):
    from tests.test_job_automatic_recovery import job_definition
    value = job_definition(env, {'automatic': False})
    sentinel = 'PRIVATE_INPUT_SENTINEL'
    if kind == 'unknown-field': value[sentinel] = sentinel
    else: value['name'] = sentinel * 20
    response = env.client.post('/api/jobs', json={'definition':value,
        'expectedRevision':0, 'requestKey':'safe-error'})
    assert response.status_code == 400 and response.json()['error']['code'] == 'invalid_schema'
    assert sentinel not in response.text
    if kind == 'long-name': assert response.json()['error']['path'] in {'name','definition.name'}
    assert env.service.list('job', actor={'type':'user','id':'local'})['items'] == []
