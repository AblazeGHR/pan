"""The MCP adapter cannot smuggle user enable or execution capabilities."""
from packages.mcp import server


def test_drafts_preserve_complete_config_and_require_explicit_disabled(monkeypatch):
    calls=[]
    monkeypatch.setattr(server,'_api',lambda *args:calls.append(args) or {'ok':True})
    cfg={'name':'draft','actionId':'a','arguments':{},'schedule':[],
         'trigger':{'kind':'immediate'},'enabled':True}
    assert 'error' in server.job_config_drafts('put',config=cfg)
    assert not calls
    cfg['enabled']=False
    assert server.job_config_drafts('put',config=cfg)=={'ok':True}
    assert calls==[('POST','/api/jobs/action-jobs',{'config':cfg})]
    assert 'error' in server.job_config_drafts('instantiate',config=cfg)
    assert 'error' in server.job_config_drafts('confirm-recovery',config=cfg)


def test_template_revision_and_query_forward_without_overrides(monkeypatch):
    calls=[]
    monkeypatch.setattr(server,'_api',lambda *args:calls.append(args) or {})
    cfg={'name':'fixed','actionId':'a','arguments':{},'schedule':[],
         'trigger':{'kind':'event','eventTypes':['owned.e'],'query':{'field':'pinned','op':'eq','value':True}},'enabled':False}
    server.job_config_drafts('put','template',cfg,'jtpl_one',9)
    assert calls[0]==('PUT','/api/jobs/job-templates/jtpl_one',
        {'item':{'id':'jtpl_one','name':'fixed','config':cfg},'expectedRevision':9})


def test_legacy_template_mcp_delete_and_instantiate_keep_old_envelope(monkeypatch):
    calls=[]
    monkeypatch.setattr(server,'_api',lambda *args:calls.append(args) or {'ok':True})
    assert server.job_config_drafts('delete', 'template', item_id='jtpl_one', expected_revision=9) == {'ok':True}
    assert server.job_config_drafts('instantiate', 'template', item_id='jtpl_one', expected_revision=9,
                                    request_key='legacy-key', overrides={'name':'display'}) == {'ok':True}
    assert calls == [
        ('DELETE', '/api/jobs/job-templates/jtpl_one?expectedRevision=9'),
        ('POST', '/api/jobs/job-templates/jtpl_one/instantiate',
         {'expectedRevision': 9, 'idempotencyKey': 'legacy-key', 'overrides': {'name': 'display'}}),
    ]


def test_legacy_scheduler_cancel_forwards_original_task_route(monkeypatch):
    calls=[]
    monkeypatch.setenv('PAN_AGENT_SESSION_ID', 'ses_scheduler')
    monkeypatch.setattr(server, '_check_access', lambda *args, **kwargs: None)
    monkeypatch.setattr(server, '_api', lambda *args: calls.append(args) or {'ok': True,
        'task': {'targetSessionId': 'ses_scheduler'}, 'taskId': 'old-task'})
    assert server.scheduler_cancel('old-task', 'job_owned', 'run_owned', 1, 0, 'cancel-owned',
                                   target_session_id='ses_scheduler')['ok']
    assert calls == [('GET', '/api/scheduler/tasks/old-task'), ('POST', '/api/scheduler/tasks/old-task/cancel', {
        'jobId': 'job_owned', 'runId': 'run_owned', 'expectedRevision': 1,
        'expectedStateRevision': 0, 'requestKey': 'cancel-owned'})]
