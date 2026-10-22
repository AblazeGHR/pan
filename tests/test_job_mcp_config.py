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
        {'item':{'id':'jtpl_one','name':'fixed','enabled':False,'config':cfg},'expectedRevision':9})
