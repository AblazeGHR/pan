import asyncio
import copy
import threading

import pytest

from packages.jobs.action_runtime import AuthorizationRevoked
from packages.jobs.action_templates import Conflict
from tests.test_automation_core import async_test, action, trigger, event, until
from tests.test_authorization_mcp import harness, close
from tests.test_authorization_permissions import full_permissions
from tests.test_job_action_templates import config, finished


@async_test
async def test_revocation_admission_and_effect_barrier_user_operations_continue(tmp_path,monkeypatch):
    h=await harness(tmp_path,monkeypatch)
    entered=threading.Event();release=threading.Event()
    try:
        auth=h.svc.auth
        auth.change_permissions(full_permissions(),0)
        delegated=h.rt.create(config(),True,{'type':'agent','revision':1})
        user=h.rt.create(config(),True,{'type':'user'})
        auth.change_permissions(full_permissions(False),1)
        before=copy.deepcopy(h.rt.get(delegated['jobId']))
        with pytest.raises(AuthorizationRevoked):await h.rt.launch(delegated['jobId'],1)
        assert h.rt.get(delegated['jobId'])==before
        await h.rt.launch(user['jobId'],1);await finished(h.rt,user['jobId'])
        assert h.calls==['selected']
        # Explicit user execution of a delegated object uses the user's authority.
        await h.rt.launch(delegated['jobId'],1,authority={'type':'user'})
        await finished(h.rt,delegated['jobId'])
        assert h.calls==['selected','selected']
        auth.change_permissions(full_permissions(),2)
        job=h.rt.create(config(),True,{'type':'agent','revision':3})
        original=h.rt.templates.snapshot
        def blocked(*args):
            entered.set();assert release.wait(5)
            return original(*args)
        monkeypatch.setattr(h.rt.templates,'snapshot',blocked)
        await h.rt.launch(job['jobId'],1)
        assert await asyncio.to_thread(entered.wait,3)
        persisted=h.rt.get(job['jobId'])['execution']
        assert persisted['authorization']=={'type':'agent','revision':3}
        assert persisted['authorizationRevision']==3
        auth.change_permissions(full_permissions(False),3)
        release.set()
        await finished(h.rt,job['jobId'],'cancelled')
        assert h.calls==['selected','selected']
        assert not h.rt.actions.read().get('jobExecutions')
    finally:release.set();await close(h)


@async_test
async def test_started_attempt_not_killed_and_new_attempt_or_recovery_denied(tmp_path,monkeypatch):
    h=await harness(tmp_path,monkeypatch)
    entered=asyncio.Event();release=asyncio.Event()
    async def effect(sid,args):
        h.calls.append(sid);entered.set();await release.wait();return {'ok':True}
    h.rt.pause=effect
    try:
        h.svc.auth.change_permissions(full_permissions(),0)
        j=h.rt.create(config(),True,{'type':'agent','revision':1})
        await h.rt.launch(j['jobId'],1)
        await asyncio.wait_for(entered.wait(),3)
        h.svc.auth.change_permissions(full_permissions(False),1)
        assert h.rt.live_for(j['jobId'])
        assert not h.rt.tasks[j['jobId']].cancelled()
        release.set();await finished(h.rt,j['jobId'])
        assert h.calls==['selected']
        with pytest.raises(AuthorizationRevoked):await h.rt.launch(j['jobId'],1)
        assert len(h.rt.get(j['jobId'])['attempts'])==1
    finally:release.set();await close(h)


@async_test
async def test_action_disable_blocks_new_job_attempt_recovery_without_killing_started(tmp_path,monkeypatch):
    h=await harness(tmp_path,monkeypatch)
    try:
        job=h.rt.create(config(),True)
        a=h.rt.actions.read()['actions']['pause']
        h.rt.actions.set_enabled('pause',False,h.rt.actions.read()['revision'])
        with pytest.raises(Conflict,match='Action disabled'):h.rt.create(config(),True)
        with pytest.raises(Conflict,match='Action disabled'):await h.rt.launch(job['jobId'],1)
        assert not h.rt.get(job['jobId']).get('attempts')
        h.rt.actions.set_enabled('pause',True,h.rt.actions.read()['revision'])
        await h.rt.launch(job['jobId'],1);await finished(h.rt,job['jobId'])
        assert h.calls==['selected']
    finally:await close(h)


@async_test
async def test_trigger_lifecycle_grant_does_not_authorize_effect_and_user_trigger_survives_revocation(tmp_path,monkeypatch):
    h=await harness(tmp_path,monkeypatch)
    try:
        h.svc.engine.execute.keywords['pause']=lambda *args:None  # replaced below by deterministic async effect
        async def pause(sid,args):h.calls.append(sid);return {'ok':True}
        h.svc.engine.execute.keywords['pause']=pause
        await h.svc.engine.start()
        permissions=full_permissions(False)
        permissions['trigger']={k:True for k in permissions['trigger']}
        h.svc.auth.change_permissions(permissions,0)
        h.svc.ledger.put(trigger('agent'),0,True,authority={'type':'agent','revision':1})
        await h.svc.engine.match(event('denied'))
        runs=h.svc.ledger.read()['runs']
        assert len(runs)==1 and next(iter(runs.values()))['status']=='failed'
        assert not next(iter(runs.values()))['attempts'] and not h.calls
        h.svc.ledger.put(trigger('agent'),1,True,authority={'type':'user'})
        await h.svc.engine.match(event('user'))
        async def done():return any(r['status']=='succeeded' for r in h.svc.ledger.read()['runs'].values())
        await until(done)
        assert h.calls==['selected']
    finally:await close(h)


@async_test
async def test_revoked_retry_does_not_reserve_recovery_and_user_confirmation_can_proceed(tmp_path,monkeypatch):
    h=await harness(tmp_path,monkeypatch)
    async def fail(sid,args):return {'ok':False}
    h.rt.pause=fail
    try:
        h.svc.auth.change_permissions(full_permissions(),0)
        j=h.rt.create(config(),True,{'type':'agent','revision':1})
        await h.rt.launch(j['jobId'],1);await finished(h.rt,j['jobId'],'failed')
        h.svc.auth.change_permissions(full_permissions(False),1)
        before=copy.deepcopy(h.rt.get(j['jobId']))
        with pytest.raises(AuthorizationRevoked):await h.rt.confirm_recovery(j['jobId'],1,True)
        assert h.rt.get(j['jobId'])==before
        await h.rt.confirm_recovery(j['jobId'],1,True,{'type':'user'})
        await finished(h.rt,j['jobId'],'failed')
        assert len(h.rt.get(j['jobId'])['attempts'])==2
        assert h.rt.get(j['jobId'])['runAuthorization']=={'type':'user'}
    finally:await close(h)


@async_test
async def test_trigger_effect_revision_barrier(tmp_path,monkeypatch):
    h=await harness(tmp_path,monkeypatch)
    prepared=asyncio.Event();release=asyncio.Event()
    async def executor(a,args,ev,progress):
        prepared.set();await release.wait();await progress.before_effect()
        h.calls.append('effect');return {'ok':True}
    h.svc.engine.execute=executor
    try:
        h.svc.auth.change_permissions(full_permissions(),0)
        h.svc.ledger.put(trigger(),0,True,authority={'type':'agent','revision':1})
        await h.svc.engine.start()
        await h.svc.engine.match(event())
        await asyncio.wait_for(prepared.wait(),3)
        h.svc.auth.change_permissions(full_permissions(False),1)
        release.set()
        async def done():return all(r['status']=='failed' for r in h.svc.ledger.read()['runs'].values())
        await until(done)
        row=next(iter(h.svc.ledger.read()['runs'].values()))
        assert row['authorizationRevision']==1 and len(row['attempts'])==1
        assert not h.calls and 'nextAttemptAt' not in row
    finally:release.set();await close(h)


@async_test
async def test_agent_resume_of_user_job_is_not_an_execute_grant(tmp_path,monkeypatch):
    from packages.plugins.ipc import request
    h=await harness(tmp_path,monkeypatch)
    try:
        j=h.rt.create(config(),True,{'type':'user'})
        h.rt.set_paused(j['jobId'],True,1,{'type':'user'})
        permissions=full_permissions(False);permissions['job']['lifecycle']=True
        h.svc.auth.change_permissions(permissions,0)
        response=await asyncio.to_thread(request,{'session_id':h.session.id,'operation':'authorization_objects',
            'method':'POST','path':'/api/jobs/action-jobs/'+j['jobId']+'/resume',
            'body':{'expectedRevision':2}},h.bridge.address)
        assert response['job']['authorization']['type']=='agent'
        with pytest.raises(AuthorizationRevoked):await h.rt.launch(j['jobId'],3)
        assert not h.calls and not h.rt.get(j['jobId']).get('attempts')
    finally:await close(h)


@async_test
async def test_automatic_recovery_revocation_keeps_budget_and_never_starts_another_attempt(tmp_path,monkeypatch):
    h=await harness(tmp_path,monkeypatch)
    effects=[]
    async def no_signal(*args):effects.append(1);return {'ok':True,'processTreeEnded':True}
    monkeypatch.setattr('packages.jobs.system_actions.execute',no_signal)
    try:
        h.svc.auth.change_permissions(full_permissions(),0)
        cfg={**config(),'actionId':'system_background_process',
             'arguments':{'sessionId':'owned','argv':['synthetic'],'cwd':str(tmp_path)},
             'completion':{'kind':'monitoring','signalType':'owned.done'},
             'recovery':{'enabled':True,'monitoring':True,'maxAttempts':3,'backoffSec':[0,0,0],'stableResetSec':300}}
        j=h.rt.create(cfg,True,{'type':'agent','revision':1})
        await h.rt.launch(j['jobId'],1)
        await finished(h.rt,j['jobId'],'restarting')
        before=h.rt.get(j['jobId'])
        assert len(before['attempts'])==1 and before['status']=='restarting'
        h.svc.auth.change_permissions(full_permissions(False),1)
        await h.rt.tick({'enabled':True})
        after=h.rt.get(j['jobId'])
        assert len(after['attempts'])==1 and effects==[1]
        assert after['recoveryFailures']==before['recoveryFailures']
        assert after['nextAttemptAt']==before['nextAttemptAt']
    finally:await close(h)


@async_test
async def test_sensitive_command_parameters_are_not_copied_to_attempt_errors(tmp_path,monkeypatch):
    import sys
    h=await harness(tmp_path,monkeypatch)
    try:
        secret='SENSITIVE_PARAMETER_SENTINEL'
        script=tmp_path/'failure.py'
        script.write_text('import sys\nsys.stderr.write(sys.argv[1])\nsys.exit(7)\n',encoding='utf-8')
        a=action();a.update(id='private_command',effect='write_local',
            input_schema={'type':'object','properties':{'value':{'type':'string'}},'required':['value'],'additionalProperties':False},
            execution={'kind':'command','executable':sys.executable,'cwd':str(tmp_path),'args':[str(script),'{value}']})
        h.rt.actions.put('actions',a)
        j=h.rt.create({**config(),'actionId':a['id'],'arguments':{'value':secret}},True)
        await h.rt.launch(j['jobId'],1);await finished(h.rt,j['jobId'],'failed')
        row=h.rt.get(j['jobId'])
        assert row['lastExecution']['error']=='Command exited 7'
        assert secret not in str(row['lastExecution']) and secret not in str(row['attempts'])
        assert secret not in str(h.rt.diagnostics)
    finally:await close(h)
