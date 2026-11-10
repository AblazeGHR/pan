"""Complete templates, atomic reference admission and ephemeral snapshot recovery."""
import asyncio
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import threading
from types import SimpleNamespace

import httpx
import pytest

from packages.actions.store import Store, validate_action
from packages.core import background_jobs as jobs
from packages.jobs.action_templates import Templates, Conflict, CreationPending, config_value
from packages.jobs.action_runtime import Runtime, RUNTIMES
from tests.test_automation_core import async_test, action, until
from tests.test_job_final_http_service import h
from tests.job_retirement_native_fixture import h as native_h  # noqa: F401
from tests.test_retirement_rev16_behavior import h as canonical_h  # noqa: F401


def config(**fields):
    return dict(name='Reference Job',actionId='pause',arguments={},enabled=True,
                schedule=[{'kind':'interval','intervalSec':3600}],**fields)


async def setup(root,pause=None):
    calls=[]
    async def effect(sid,args):calls.append(sid);return {'ok':True}
    store=Store(root/'actions');store.put('actions',action())
    rt=Runtime(root/'jobs',store,pause or effect);await rt.start()
    return rt,calls


def canonical_fixed_template(h, key='fixed', child=None):
    from tests.test_job_final_http_service import create, control, job_def
    from packages.jobs.final_service import object_ref
    from packages.actions.legacy_families import template_successor
    value = {'name':'fixed', 'jobDefinition':child or job_def(h), 'composition':{'creators':[], 'triggers':[]}}
    template = create(h, 'job-templates', value, 'template-' + key)
    published = control(h, 'job-templates', template, 'publish', 'publish-template-' + key)
    assert published.status_code == 200, published.text
    template = published.json()['result']['object']
    raw = action()
    raw['id'] = 'instantiate'
    raw['execution'] = {'kind':'job_template', 'templateId':'jtpl_fixed', 'templateRevision':1}
    canonical = template_successor(raw, object_ref('template', template))
    published_action = h.service.publish_action('instantiate_' + key, canonical)
    if h.service.owner.token is None:
        h.service.owner.claim(0)
    return template, object_ref('action', published_action), raw


async def finished(rt,jid,status='completed'):
    async def ready():
        job=await asyncio.to_thread(rt.get,jid)
        task=rt.tasks.get(jid)
        return (job.get('lastExecution',{}).get('status')==status and not job.get('execution')
                and (task is None or task.done()))
    await until(ready)
    return await asyncio.to_thread(rt.get,jid)


@pytest.mark.parametrize('extra',[
    {'text':'extra'}, {'target':{'sessionId':'other'}}, {'api':'/arbitrary'},
    {'arguments':{'text':'extra'}}, {'arguments':[]}, {'enabled':'true'},
    {'schedule':[]}, {'schedule':[{'kind':'interval','intervalSec':1,'unknown':True}]},
    {'schedule':[{'kind':'once'}]}, {'maxRuns':True}])
def test_job_fields_are_declared_only(h, extra):
    from tests.test_job_final_http_service import job_def
    from tests.test_action_groups import canonical_state
    before = canonical_state(h.service)
    value = job_def(h)
    value['inputSchema'] = {'type': 'object', 'properties': {}, 'additionalProperties': False}
    response = h.client.post('/api/jobs', json={
        'definition': {**value, **extra}, 'expectedRevision': 0,
        'requestKey': 'invalid-template-job'})
    assert response.status_code == 400, response.text
    assert response.json()['error']['code'] == 'invalid_schema'
    assert canonical_state(h.service) == before
    assert h.provider.calls == []


def test_complete_fields_defaults_and_template_revision(h):
    from tests.test_job_final_http_service import create, job_def, USER
    from tests.test_action_groups import canonical_state
    # Shared definition validation remains useful for inspecting old records;
    # assembling a definition does not re-enable a retired registry writer.
    a = action()
    a['input_schema'] = {'type':'object', 'properties':{'count':{'type':'integer'},
        'tag':{'type':'string','default':'fixed'}}, 'required':['count'], 'additionalProperties':False}
    state = Store().read()
    state['actions']['pause'] = validate_action(a)
    with pytest.raises(ValueError):
        config_value(config(), state)
    filled = config_value({**config(), 'enabled':False, 'arguments':{'count':2}}, state)
    assert filled['arguments'] == {'count':2, 'tag':'fixed'} and filled['enabled'] is False
    value = {'name':'complete', 'jobDefinition':job_def(h), 'composition':{'creators':[], 'triggers':[]}}
    template = create(h, 'job-templates', value, 'complete-template')
    assert 'enabled' not in template and template['state'] == 'draft'
    assert template['jobDefinition']['overlap'] == {'mode':'forbid', 'maxActive':1, 'maxPending':0}
    assert template['jobDefinition']['recovery'] == {'automatic':False, 'retryIf':[], 'maxAttempts':3,
        'backoffSeconds':['5','15','60'], 'stableResetSeconds':'300'}
    before = canonical_state(h.service)
    invalid = h.client.put('/api/job-templates/' + template['id'], json={
        'definition':{**value,'enabled':True}, 'expectedRevision':template['revision'], 'requestKey':'invalid-template-enabled'})
    assert invalid.status_code == 400 and invalid.json()['error']['code'] == 'invalid_schema'
    assert canonical_state(h.service) == before
    stale = h.client.put('/api/job-templates/' + template['id'], json={
        'definition':{**value,'name':'stale'}, 'expectedRevision':template['revision'] + 1, 'requestKey':'stale-complete-template'})
    assert stale.status_code == 409 and stale.json()['error']['code'] == 'revision_conflict'
    assert canonical_state(h.service) == before
    saved = h.client.put('/api/job-templates/' + template['id'], json={
        'definition':{**value,'name':'edited'}, 'expectedRevision':template['revision'], 'requestKey':'edit-complete-template'})
    assert saved.status_code == 200, saved.text
    current = h.service.get('template', template['id'], actor=USER)
    assert current['revision'] == 2 and current['name'] == 'edited'
    assert current['jobDefinition'] == template['jobDefinition']
    assert h.provider.calls == []


def test_instantiate_copy_revision_display_only_and_idempotence(h):
    from tests.test_job_final_http_service import create, control, job_def, USER
    from packages.jobs.final_contracts import ContractError
    value = {'name': 'complete', 'jobDefinition': job_def(h), 'composition': {'creators': [], 'triggers': []}}
    template = create(h, 'job-templates', value)
    response = control(h, 'job-templates', template, 'publish', 'publish-copy-template')
    assert response.status_code == 200, response.text
    template = response.json()['result']['object']
    body = {'expectedRevision': template['revision'], 'requestKey': 'stable-copy-key',
            'overrides': {'name': 'instance'}}
    def instantiate(_):
        return h.service.instantiate_template(template['id'], body, actor=USER)
    with ThreadPoolExecutor(max_workers=4) as pool:
        receipts = list(pool.map(instantiate, range(8)))
    assert len({r['operationId'] for r in receipts}) == 1
    ids = {r['result']['objectIds']['job'] for r in receipts}
    assert len(ids) == 1
    jid, = ids
    child = h.service.get('job', jid, actor=USER)
    assert child['name'] == 'instance' and child['steps'] == value['jobDefinition']['steps']
    # Canonical publication is immutable; edits cannot mutate this source or
    # the already instantiated copy. Display overrides never replace config.
    with pytest.raises(ContractError) as immutable:
        h.service.write('template', template['id'], {'definition': {**value, 'name': 'edited'},
            'expectedRevision': template['revision'], 'requestKey': 'edit-published'}, actor=USER)
    assert immutable.value.code == 'reference_conflict'
    assert h.service.get('job', jid, actor=USER) == child
    with pytest.raises(ContractError) as conflict:
        h.service.instantiate_template(template['id'], {**body, 'expectedRevision': template['revision'] + 1}, actor=USER)
    assert conflict.value.code == 'idempotency_conflict'
    with pytest.raises(ContractError) as config_override:
        h.service.instantiate_template(template['id'], {**body, 'requestKey': 'unsafe-override',
            'overrides': {'arguments': {}}}, actor=USER)
    assert config_override.value.code == 'invalid_schema'
    response = control(h, 'job-templates', template, 'retire', 'retire-copy-template')
    assert response.status_code == 200, response.text
    original = instantiate(0)
    assert original['operationId'] == receipts[0]['operationId'] and original['replayed'] is True
    with pytest.raises(ContractError) as retired:
        h.service.instantiate_template(template['id'], {**body, 'requestKey': 'retired-new-copy'}, actor=USER)
    assert retired.value.code == 'reference_conflict'
    assert [j['id'] for j in h.service.list('job', actor=USER)['items']] == [jid]
    assert h.service.get('job', jid, actor=USER) == child


def test_reserved_creation_recovers_after_template_delete_and_write_failure(h, monkeypatch):
    from tests.test_job_final_http_service import create, control, job_def, USER
    from packages.jobs.final_contracts import ContractError
    value = {'name': 'atomic copy', 'jobDefinition': job_def(h), 'composition': {'creators': [], 'triggers': []}}
    template = create(h, 'job-templates', value)
    response = control(h, 'job-templates', template, 'publish', 'publish-atomic-copy')
    assert response.status_code == 200, response.text
    template = response.json()['result']['object']
    body = {'expectedRevision': template['revision'], 'requestKey': 'durable-copy-intent'}
    def snapshot():
        with h.service.transaction(readonly=True) as db:
            names = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            return {name: sorted((tuple(row) for row in db.execute('SELECT * FROM "' + name.replace('"', '""') + '"')), key=repr)
                    for name in names}
    before = snapshot()
    original = h.service._save
    def fail_after_insert(db, kind, identity, document):
        result = original(db, kind, identity, document)
        if kind == 'job':
            raise OSError('injected canonical Job publication failure')
        return result
    monkeypatch.setattr(h.service, '_save', fail_after_insert)
    with pytest.raises(OSError, match='injected canonical Job publication failure'):
        h.service.instantiate_template(template['id'], body, actor=USER)
    monkeypatch.setattr(h.service, '_save', original)
    assert snapshot() == before and h.provider.calls == []
    with pytest.raises(KeyError):
        h.service.operation_for_key('instantiate-template', body['requestKey'], actor=USER)
    # Canonical creation is atomic: no legacy reserved intent survives a
    # rolled-back write, so a retired source cannot manufacture a later copy.
    assert control(h, 'job-templates', template, 'retire', 'retire-failed-source').status_code == 200
    h.service.recover()
    with pytest.raises(ContractError) as retired:
        h.service.instantiate_template(template['id'], body, actor=USER)
    assert retired.value.code == 'reference_conflict'
    assert h.service.list('job', actor=USER)['items'] == []
    # Once the canonical receipt has committed, source retirement cannot lose
    # the original result or turn its key into another creation.
    next_template = create(h, 'job-templates', value, key='next-template')
    response = control(h, 'job-templates', next_template, 'publish', 'publish-next-template')
    assert response.status_code == 200, response.text
    next_template = response.json()['result']['object']
    committed_body = {'expectedRevision': next_template['revision'], 'requestKey': 'committed-copy-intent'}
    committed = h.service.instantiate_template(next_template['id'], committed_body, actor=USER)
    assert control(h, 'job-templates', next_template, 'retire', 'retire-committed-source').status_code == 200
    replay = h.service.instantiate_template(next_template['id'], committed_body, actor=USER)
    assert replay['operationId'] == committed['operationId'] and replay['replayed'] is True
    jid = committed['result']['objectIds']['job']
    assert [j['id'] for j in h.service.list('job', actor=USER)['items']] == [jid]
    assert h.provider.calls == []


def test_template_edit_delete_while_job_write_blocked_uses_admitted_copy(h, monkeypatch):
    from contextlib import contextmanager
    from packages.jobs.final_contracts import ContractError
    from tests.test_job_final_http_service import create, control, job_def, USER
    value = {'name':'complete', 'jobDefinition':job_def(h), 'composition':{'creators':[], 'triggers':[]}}
    template = create(h, 'job-templates', value, 'race-template')
    published = control(h, 'job-templates', template, 'publish', 'publish-race-template')
    assert published.status_code == 200, published.text
    template = published.json()['result']['object']
    with pytest.raises(ContractError) as immutable:
        h.service.write('template', template['id'], {'definition':{**value,'name':'edited'},
            'expectedRevision':template['revision'], 'requestKey':'edit-race-source'}, actor=USER)
    assert immutable.value.code == 'reference_conflict'
    entered, release, retire_waiting = threading.Event(), threading.Event(), threading.Event()
    save, writer = h.service._save, h.service.owner.writer
    retire_thread = []
    def blocked(db, kind, identity, item):
        if kind == 'job':
            entered.set()
            assert release.wait(4), 'template copy commit barrier timed out'
        return save(db, kind, identity, item)
    @contextmanager
    def fenced(*args, **kwargs):
        if retire_thread and threading.get_ident() == retire_thread[0]:
            retire_waiting.set()
        with writer(*args, **kwargs):
            yield
    monkeypatch.setattr(h.service, '_save', blocked)
    monkeypatch.setattr(h.service.owner, 'writer', fenced)
    body = {'expectedRevision':template['revision'], 'requestKey':'interleaved-copy'}
    def retire():
        retire_thread.append(threading.get_ident())
        return h.service.control('template', template['id'], {'operation':'retire',
            'expectedRevision':template['revision'], 'expectedStateRevision':template['stateRevision'],
            'parameters':{}, 'requestKey':'retire-race-source'}, actor=USER)
    with ThreadPoolExecutor(max_workers=2) as pool:
        copy_future = pool.submit(h.service.instantiate_template, template['id'], body, actor=USER)
        retire_future = None
        try:
            assert entered.wait(3)
            retire_future = pool.submit(retire)
            assert retire_waiting.wait(3)
            assert not retire_future.done(), 'retirement bypassed the canonical writer fence'
        finally:
            release.set()
        copied = copy_future.result(timeout=4)
        retired = retire_future.result(timeout=4)
    jid = copied['result']['objectIds']['job']
    job = h.service.get('job', jid, actor=USER)
    assert job['name'] == value['jobDefinition']['name'] and job['steps'] == value['jobDefinition']['steps']
    assert retired['result']['object']['state'] == 'retired'
    replay = h.service.instantiate_template(template['id'], body, actor=USER)
    assert replay['operationId'] == copied['operationId'] and replay['result']['objectIds']['job'] == jid
    assert len(h.service.list('job', actor=USER)['items']) == 1
    assert h.provider.calls == [] and h.service.final.runs(jid) == []


def test_delete_before_snapshot_atomic_admission_blocks_all_effects(h):
    from tests.test_job_final_http_service import enabled, launch, USER
    from tests.test_action_unification_p4 import plans
    job = enabled(h)
    rid = launch(h, job, 'retired-before-effect')['runId']
    frozen = copy.deepcopy(plans(h, job['id'])[rid]['frozen'])
    action = h.service.get('action', h.action['id'], actor=USER)
    response = h.client.request('DELETE', '/api/jobs/actions/' + action['id'], json={
        'expectedRevision': action['revision'], 'expectedStateRevision': action['stateRevision'],
        'requestKey': 'retire-before-snapshot-effect'})
    assert response.status_code == 200, response.text
    retired = response.json()['result']['object']
    assert retired['state'] == 'retired' and retired['enabled'] is False
    assert plans(h, job['id'])[rid]['frozen'] == frozen
    asyncio.run(h.service.dispatch_once())
    state = h.service.final.get(job['id'])
    assert state['runs'][rid]['state'] == 'failed'
    assert state['attempts'] == {} and state['effects'] == [] and h.provider.calls == []
    blocked = h.client.post('/api/jobs/' + job['id'] + '/runs', json={
        'expectedRevision': job['configRevision'], 'requestKey': 'retired-new-admission',
        'input': {}, 'cause': {'type': 'manual'}})
    assert blocked.status_code == 409 and blocked.json()['error']['code'] == 'reference_conflict'
    assert h.service.final.get(job['id']) == state
    assert h.service.get('action', action['id'], actor=USER) == retired


@pytest.mark.parametrize('mutation', ['edit', 'delete'])
def test_running_snapshot_stable_terminal_cleanup_and_no_auto_restore(h, mutation):
    from tests.test_job_final_http_service import enabled, job_def, launch, USER
    from tests.test_action_unification_p4 import plans
    from packages.jobs.final_service import definition, object_ref
    from packages.jobs.final_runtime.executor import TypedExecutor
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        class Provider:
            async def send_session(self, session_id, text, *, effect_key, source):
                calls.append(text)
                entered.set()
                await release.wait()
                return {'status': 'queued', 'messageId': 'accepted-original'}
        h.service.executor = TypedExecutor(session_provider=Provider())
        job = enabled(h)
        rid = launch(h, job, 'stable-running-copy')['runId']
        frozen = copy.deepcopy(plans(h, job['id'])[rid]['frozen'])
        old_action = h.service.get('action', h.action['id'], actor=USER)
        old_definition = definition('action', old_action)
        dispatch = asyncio.create_task(h.service.dispatch_once())
        try:
            await asyncio.wait_for(entered.wait(), 3)
            if mutation == 'delete':
                retired = h.client.request('DELETE', '/api/jobs/actions/' + old_action['id'], json={
                    'expectedRevision': old_action['revision'], 'expectedStateRevision': old_action['stateRevision'],
                    'requestKey': 'retire-running-source'})
                assert retired.status_code == 200, retired.text
            else:
                edited = h.service.write('action', old_action['id'], {'definition': {**old_definition, 'name': 'changed'},
                    'expectedRevision': old_action['revision'], 'requestKey': 'edit-running-source'}, actor=USER)['result']['object']
                published = h.service.control('action', old_action['id'], {'operation': 'publish',
                    'expectedRevision': edited['revision'], 'expectedStateRevision': edited['stateRevision'],
                    'parameters': {}, 'requestKey': 'publish-running-source'}, actor=USER)['result']['object']
                assert published['revision'] == old_action['revision'] + 1
            assert plans(h, job['id'])[rid]['frozen'] == frozen
            release.set()
            await asyncio.wait_for(dispatch, 5)
        finally:
            release.set()
            if not dispatch.done():
                dispatch.cancel()
                await asyncio.gather(dispatch, return_exceptions=True)
        done = h.service.final.get(job['id'])
        assert done['runs'][rid]['state'] == 'completed' and calls == ['0']
        assert len(done['attempts']) == len(done['effects']) == 1
        assert plans(h, job['id'])[rid]['frozen'] == frozen
        public = h.client.get('/api/jobs/' + job['id']).json()
        assert 'definition' not in public.get('lastExecution', {}) and 'jobExecutions' not in public
        if mutation == 'delete':
            blocked = h.client.post('/api/jobs/' + job['id'] + '/runs', json={
                'expectedRevision': job['configRevision'], 'requestKey': 'no-automatic-restore',
                'input': {}, 'cause': {'type': 'manual'}})
            assert blocked.status_code == 409 and blocked.json()['error']['code'] == 'reference_conflict'
            assert h.service.final.get(job['id']) == done and calls == ['0']
            # Repair requires an explicit different canonical identity/ref;
            # retired source identity is never recreated or auto-enabled.
            ref = object_ref('action', h.service.publish_action('explicit_repaired_message', old_definition))
            cfg = job_def(h)
            cfg['steps'][0]['actionRef'] = ref
            cfg['steps'][0]['arguments']['text']['value'] = 'changed'
            update = h.client.put('/api/jobs/' + job['id'], json={'definition': cfg,
                'expectedRevision': job['configRevision'], 'requestKey': 'user-explicit-repair'})
            assert update.status_code == 200, update.text
            repaired = update.json()['result']['object']
            assert repaired['configRevision'] == 2
            next_rid = launch(h, repaired, 'run-explicit-repair')['runId']
            await h.service.dispatch_once()
            assert h.service.final.get(job['id'])['runs'][next_rid]['state'] == 'completed'
            assert calls == ['0', 'changed']
            assert h.service.get('action', old_action['id'], actor=USER)['state'] == 'retired'
        else:
            current = h.service.get('job', job['id'], actor=USER)
            assert current['steps'][0]['actionRef'] == h.action
            assert h.service.action_ref(old_action['id'], actor=USER)['revision'] == 2
    asyncio.run(scenario())


@async_test
async def test_cancel_exception_shutdown_and_recovery_remove_snapshots(tmp_path):
    for mode in ('cancel','error','shutdown','crash'):
        entered=asyncio.Event();calls=[]
        async def pause(*args):
            calls.append(1);entered.set()
            if mode=='error':raise ValueError('owned failure')
            await asyncio.Event().wait()
        rt,_=await setup(tmp_path/mode,pause);job=rt.create(config(),True)
        try:
            if mode=='crash':
                admitted=rt.claim(job['jobId'],'jexec_crash',1)
                rt.templates.snapshot(admitted,admitted['execution'])
                rt.closed=True;RUNTIMES.pop(str(rt.root));rt.ownership.release();rt.owned=False
                rt=Runtime(rt.root,rt.actions,pause);await rt.start()
                expected='uncertain'
            else:
                await rt.launch(job['jobId'],1);await asyncio.wait_for(entered.wait(),2)
                if mode=='cancel':await rt.cancel(job['jobId'],1);expected='cancelled'
                elif mode=='shutdown':await rt.close();expected='uncertain'
                else:expected='failed'
            await finished(rt,job['jobId'],expected)
            assert not rt.actions.read().get('jobExecutions')
            record=jobs.list_run_records(registry_root=rt.root)[0]
            assert record['status']==expected and 'definition' not in record['result']
        finally:await rt.close()


@async_test
async def test_cleanup_failure_is_visible_cleaning_and_retries_cleanup_only(tmp_path,monkeypatch):
    rt,calls=await setup(tmp_path);job=rt.create(config(),True);original=rt.templates.clear_snapshot
    def fail(*args):raise OSError('injected snapshot cleanup failure')
    monkeypatch.setattr(rt.templates,'clear_snapshot',fail)
    try:
        await rt.launch(job['jobId'],1);await rt.tasks[job['jobId']]
        pending=rt.get(job['jobId'])
        assert pending['status']=='cleaning' and pending['executionFinalization']['status']=='completed'
        assert rt.actions.read()['jobExecutions'] and rt.diagnostics[-1]['phase']=='cleanup'
        monkeypatch.setattr(rt.templates,'clear_snapshot',original)
        await rt.tick({'enabled':False});done=await finished(rt,job['jobId'])
        assert calls==['selected'] and not rt.actions.read()['jobExecutions'] and done['runCount']==1
    finally:monkeypatch.setattr(rt.templates,'clear_snapshot',original);await rt.close()


def test_snapshot_io_does_not_hold_job_registry_or_block_event_loop(h, monkeypatch, tmp_path):
    from packages.core import session as sessions
    from packages.jobs.final_service import FinalService, object_ref
    from packages.jobs.final_api import CanonicalUserAuth
    from tests.test_job_final_http_service import enabled, USER
    job = enabled(h)
    entered, release = threading.Event(), threading.Event()
    original = h.service._save
    def blocked(db, kind, identity, value):
        if kind == 'job' and value.get('finalRuntime', {}).get('plans'):
            entered.set()
            assert release.wait(5), 'snapshot commit barrier timed out'
        return original(db, kind, identity, value)
    monkeypatch.setattr(h.service, '_save', blocked)
    other_root = tmp_path / 'independent-profile'
    other = FinalService(other_root / 'canonical', CanonicalUserAuth(other_root / 'automation'))
    action = next(a for a in other.actions(actor=USER)['items'] if a['id'] == 'product_process_cleanup_v1')
    value = {'name': 'unrelated management', 'retention': {'policyRef': 'test'}, 'steps': [{
        'stepId': 'owned', 'actionRef': object_ref('action', action),
        'arguments': {'argv': {'from': 'literal', 'value': ['unused']},
                      'cwd': {'from': 'literal', 'value': str(other.root)}},
        'completion': {'contract': 'process-exit'}}]}
    async def scenario():
        pending = asyncio.create_task(asyncio.to_thread(h.service.create_run, job['id'], {
            'expectedRevision': job['configRevision'], 'requestKey': 'blocked-snapshot',
            'input': {}, 'cause': {'type': 'manual'}}, actor=USER))
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            await asyncio.wait_for(asyncio.sleep(0), .2)
            session = sessions.Session(id='snapshot-independent-session', name='unrelated')
            await asyncio.wait_for(asyncio.to_thread(sessions.save, session), 2)
            assert sessions.get(session.id).name == 'unrelated'
            created = await asyncio.wait_for(asyncio.to_thread(other.write, 'job', None, {
                'definition': value, 'expectedRevision': 0, 'requestKey': 'unrelated-profile-create'}, actor=USER), 2)
            assert created['result']['object']['name'] == 'unrelated management'
            assert not other.final.get(created['result']['object']['id'])['runs']
            assert h.provider.calls == []
        finally:
            release.set()
            await asyncio.wait_for(pending, 5)
            await other.close()
        await h.service.dispatch_once()
        assert len(h.provider.calls) == 1
    asyncio.run(scenario())



@async_test
async def test_snapshot_write_or_post_effect_checkpoint_failure_never_replays(tmp_path,monkeypatch):
    import packages.actions.store as module
    original=module.atomic
    for mode in ('snapshot','checkpoint'):
        rt,calls=await setup(tmp_path/mode);job=rt.create(config(),True)
        def atomic(path,state):
            snapshots=state.get('jobExecutions',{})
            bad=bool(snapshots) if mode=='snapshot' else any(s['checkpoint'].get('targetResults') for s in snapshots.values())
            if bad:raise OSError('injected '+mode+' persistence failure')
            return original(path,state)
        monkeypatch.setattr(module,'atomic',atomic)
        try:
            await rt.launch(job['jobId'],1)
            done=await finished(rt,job['jobId'],'failed' if mode=='snapshot' else 'uncertain')
            assert not rt.actions.read().get('jobExecutions')
            assert calls==([] if mode=='snapshot' else ['selected'])
            if mode=='checkpoint':assert not done['enabled']
        finally:monkeypatch.setattr(module,'atomic',original);await rt.close()


@async_test
async def test_terminal_job_write_failure_after_cleanup_recovers_same_run_once(tmp_path,monkeypatch):
    rt,calls=await setup(tmp_path);job=rt.create(config(),True);original=jobs._write_job
    def write(path,value,**kwargs):
        if value.get('lastExecution') and not value.get('execution'):raise OSError('injected terminal Job write failure')
        return original(path,value,**kwargs)
    monkeypatch.setattr(jobs,'_write_job',write)
    try:
        await rt.launch(job['jobId'],1);await rt.tasks[job['jobId']]
        assert rt.get(job['jobId'])['status']=='cleaning' and not rt.actions.read()['jobExecutions']
        monkeypatch.setattr(jobs,'_write_job',original)
        await rt.tick({'enabled':False});await finished(rt,job['jobId'])
        assert calls==['selected'] and len(jobs.list_run_records(registry_root=rt.root))==1
    finally:monkeypatch.setattr(jobs,'_write_job',original);await rt.close()


def test_fixed_template_action_capability_idempotence_and_group_gate(h):
    from packages.actions.groups import Groups
    from packages.actions.executor import execute_dynamic
    from packages.jobs.final_contracts import ContractError
    from packages.jobs.final_service import definition, object_ref
    from tests.test_job_final_http_service import USER
    from tests.test_action_groups import canonical_state
    template, ref, raw = canonical_fixed_template(h)
    with pytest.raises(PermissionError, match='retired'):
        Store().put('actions', raw, authorized=True)
    with pytest.raises(PermissionError, match='retired'):
        Store().build('g')
    async def forbidden(*args, **kwargs):
        raise AssertionError('retired executor must not call an effect')
    with pytest.raises(PermissionError, match='retired'):
        asyncio.run(execute_dynamic(raw, {}, {}, forbidden, forbidden))
    groups = Groups(h.service)
    value = definition('action', h.service.get('action', ref['id'], actor=USER))
    draft = h.service.write('action', None, {'definition':value, 'expectedRevision':0,
        'requestKey':'draft-template-action'}, actor=USER)['result']['object']
    before = canonical_state(h.service)
    with pytest.raises(ContractError) as blocked:
        groups.put({'id':'draft_template_group', 'name':'Draft', 'tools':[{
            'alias':'create', 'actionRef':object_ref('action', draft)}]}, 0, 'draft-template-group', actor=USER)
    assert blocked.value.code == 'reference_conflict' and canonical_state(h.service) == before
    created = groups.put({'id':'fixed_template_group', 'name':'Fixed', 'tools':[{
        'alias':'create', 'actionRef':ref}]}, 0, 'fixed-template-group', actor=USER)
    published = groups.publish('fixed_template_group', created['result']['group']['revision'],
        'publish-fixed-template-group', actor=USER)['result']['release']
    body = {'actionRef':ref, 'arguments':{}, 'requestKey':'one-logical-invocation'}
    before = canonical_state(h.service)
    with pytest.raises(ContractError) as denied:
        asyncio.run(h.service.invoke_action(ref['id'], body, actor={'type':'agent','id':'ungranted','revision':1}))
    assert denied.value.code == 'authorization_denied' and canonical_state(h.service) == before
    one = asyncio.run(groups.invoke(published['version'], 'create', {}, body['requestKey'], actor=USER))
    two = asyncio.run(groups.invoke(published['version'], 'create', {}, body['requestKey'], actor=USER))
    assert two['replayed'] and one['operationId'] == two['operationId']
    jid = one['result']['output']['jobId']
    assert one['result']['output']['jobCompleted'] is False
    assert [j['id'] for j in h.service.list('job', actor=USER)['items']] == [jid]
    assert h.service.get('job', jid, actor=USER)['state'] == 'disabled'
    assert h.service.final.runs(jid) == [] and h.provider.calls == []
    before = canonical_state(h.service)
    with pytest.raises(ContractError) as invalid:
        asyncio.run(groups.invoke(published['version'], 'create', {'text':'override'}, 'invalid-template-args', actor=USER))
    assert invalid.value.code == 'invalid_schema' and canonical_state(h.service) == before
    with pytest.raises(ValueError):
        validate_action({**raw, 'input_schema':{'type':'object', 'properties':{'x':{'type':'string'}}, 'additionalProperties':False}})


def test_running_template_instantiation_uses_private_context_after_template_delete(h, monkeypatch):
    from tests.test_job_final_http_service import enabled, job_def, launch, control, USER
    from tests.test_action_unification_p4 import plans
    from tests.test_action_groups import canonical_state
    from packages.actions import invocation
    from packages.jobs.final_contracts import ContractError
    template, ref, _ = canonical_fixed_template(h, key='private')
    cfg = job_def(h)
    cfg['steps'] = [{'stepId':'create', 'actionRef':ref, 'arguments':{}, 'completion':{'contract':'job-created'}}]
    parent = enabled(h, cfg)
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        original = invocation.template_job_execute
        original_receipts = []
        async def blocked(*args, **kwargs):
            receipt = await original(*args, **kwargs)
            original_receipts.append(copy.deepcopy(receipt))
            entered.set()
            await release.wait()
            return receipt
        monkeypatch.setattr(invocation, 'template_job_execute', blocked)
        rid = launch(h, parent, 'private-template-run')['runId']
        frozen = copy.deepcopy(plans(h, parent['id'])[rid]['frozen'])
        task = asyncio.create_task(h.service.dispatch_once())
        try:
            await asyncio.wait_for(entered.wait(), 3)
            # The original composition is already committed; retiring the
            # source cannot invalidate its delayed original receipt.
            jid = original_receipts[0]['receipt']['output']['jobId']
            child = h.service.get('job', jid, actor=USER)
            retired = control(h, 'job-templates', template, 'retire', 'retire-private-template')
            assert retired.status_code == 200, retired.text
            release.set()
            await asyncio.wait_for(task, 5)
        finally:
            release.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        state = h.service.final.get(parent['id'])
        assert state['runs'][rid]['state'] == 'completed'
        assert len(state['attempts']) == len(state['effects']) == len(original_receipts) == 1
        assert state['effects'][0]['receipt'] == original_receipts[0]
        assert h.service.get('job', jid, actor=USER) == child and child['state'] == 'disabled'
        assert plans(h, parent['id'])[rid]['frozen'] == frozen
        before = canonical_state(h.service)
        with pytest.raises(ContractError) as denied:
            await h.service.invoke_action(ref['id'], {'actionRef':ref, 'arguments':{},
                'requestKey':'new-after-template-retired'}, actor=USER)
        assert denied.value.code == 'reference_conflict' and canonical_state(h.service) == before
        await h.service.dispatch_once()
        assert len(h.service.list('job', actor=USER)['items']) == 2
        assert not Store().read().get('jobExecutions') and h.provider.calls == []
    asyncio.run(scenario())


def test_reference_graph_cycle_rejected_and_template_restore_does_not_enable(h):
    from packages.jobs.action_templates import check_action_graph
    from packages.jobs.final_contracts import ContractError
    from packages.jobs.final_service import definition, object_ref
    from tests.test_job_final_http_service import USER, create, control, job_def, launch
    from tests.test_action_groups import canonical_state
    # Keep the shared read/definition graph validator's original cycle fence.
    state = Store().read()
    loop = {**action(), 'execution':{'kind':'job_template', 'templateId':'jtpl_cycle', 'templateRevision':1}}
    state['actions']['pause'] = validate_action(loop)
    state.setdefault('jobTemplates', {})['jtpl_cycle'] = {'id':'jtpl_cycle', 'revision':1, 'config':config()}
    with pytest.raises(ValueError, match='cycle'):
        check_action_graph(state, loop)
    template, _, _ = canonical_fixed_template(h, key='graph')
    original = h.service.get('action', h.action['id'], actor=USER)
    response = h.client.request('DELETE', '/api/jobs/actions/' + original['id'], json={
        'expectedRevision':original['revision'], 'expectedStateRevision':original['stateRevision'],
        'requestKey':'retire-template-reference'})
    assert response.status_code == 200, response.text
    retired = response.json()['result']['object']
    before = canonical_state(h.service)
    with pytest.raises(ContractError) as unavailable:
        h.service.instantiate_template(template['id'], {'expectedRevision':template['revision'],
            'requestKey':'unavailable-graph-copy'}, actor=USER)
    assert unavailable.value.code == 'reference_conflict' and canonical_state(h.service) == before
    with pytest.raises(ContractError) as restore:
        h.service.write('action', original['id'], {'definition':definition('action', original),
            'expectedRevision':retired['revision'], 'requestKey':'restore-graph-reference'}, actor=USER)
    assert restore.value.code == 'reference_conflict' and canonical_state(h.service) == before
    replacement = h.service.publish_action('explicit_graph_replacement', definition('action', original))
    value = job_def(h)
    value['steps'][0]['actionRef'] = object_ref('action', replacement)
    successor = create(h, 'job-templates', {'name':'repaired graph', 'jobDefinition':value,
        'composition':{'creators':[], 'triggers':[]}}, 'repair-graph-template')
    published = control(h, 'job-templates', successor, 'publish', 'publish-graph-repair')
    assert published.status_code == 200, published.text
    successor = published.json()['result']['object']
    copy_receipt = h.service.instantiate_template(successor['id'], {'expectedRevision':successor['revision'],
        'requestKey':'explicit-repaired-graph-copy'}, actor=USER)
    jid = copy_receipt['result']['objectIds']['job']
    child = h.service.get('job', jid, actor=USER)
    assert child['state'] == 'disabled' and h.service.final.runs(jid) == []
    assert h.provider.calls == []
    activated = control(h, 'jobs', child, 'enable', 'enable-repaired-copy')
    assert activated.status_code == 200, activated.text
    rid = launch(h, activated.json()['result']['object'], 'run-repaired-graph')['runId']
    asyncio.run(h.service.dispatch_once())
    done = h.service.final.get(jid)
    assert done['runs'][rid]['state'] == 'completed'
    assert len(done['attempts']) == len(done['effects']) == len(h.provider.calls) == 1
    assert h.service.get('action', original['id'], actor=USER) == retired


def test_api_permissions_unknown_fields_legacy_aliases_and_repair(h):
    from fastapi.testclient import TestClient
    from tests.test_action_unification_p6_template_http_compat import _legacy_item
    from tests.test_job_final_http_service import USER, control, launch
    from tests.test_action_groups import canonical_state
    from packages.jobs.final_service import definition
    before = canonical_state(h.service)
    with TestClient(h.app) as anonymous:
        for method, path, body in [
            ('GET','/api/jobs/job-templates',None),
            ('POST','/api/jobs/job-templates',{'item':{},'expectedRevision':0}),
            ('POST','/api/job-templates',{'definition':{},'expectedRevision':0,'requestKey':'anonymous-template'})]:
            denied = anonymous.request(method, path, json=body)
            assert denied.status_code == 401, denied.text
            assert canonical_state(h.service) == before
    for path in ('/api/legacy/jobs/action-jobs', '/api/legacy/jobs/job_missing/run-now'):
        retired = h.client.post(path, json={})
        assert retired.status_code == 410 and retired.json()['detail']['code'] == 'retired'
        assert canonical_state(h.service) == before
    item = _legacy_item(h.action['id'])
    invalid = h.client.post('/api/jobs/job-templates', json={'item':{**item,'unknown':True}, 'expectedRevision':0})
    assert invalid.status_code == 400 and invalid.json()['error']['code'] == 'invalid_schema'
    assert canonical_state(h.service) == before
    created = h.client.post('/api/jobs/job-templates', json={'item':item, 'expectedRevision':0})
    assert created.status_code == 200, created.text
    original = h.service.get('template', created.json()['canonical']['id'], actor=USER)
    published = control(h, 'job-templates', original, 'publish', 'publish-http-template')
    assert published.status_code == 200, published.text
    original = published.json()['result']['object']
    changed = {**item, 'name':'edited alias'}
    updated = h.client.put('/api/jobs/job-templates/' + item['id'], json={'item':changed, 'expectedRevision':1})
    assert updated.status_code == 200, updated.text
    assert updated.json()['item']['revision'] == 2
    assert updated.json()['canonical']['id'] != original['id']
    assert h.service.get('template', original['id'], actor=USER) == original
    admitted = h.client.post('/api/jobs/job-templates/' + item['id'] + '/instantiate', json={
        'expectedRevision':2, 'idempotencyKey':'http-template-copy'})
    assert admitted.status_code == 200, admitted.text
    jid = admitted.json()['job']['jobId']
    job = h.service.get('job', jid, actor=USER)
    assert job['state'] == 'disabled' and h.service.final.runs(jid) == []
    replay = h.client.post('/api/jobs/job-templates/' + item['id'] + '/instantiate', json={
        'expectedRevision':2, 'idempotencyKey':'http-template-copy'})
    assert replay.status_code == 200 and replay.json()['job']['jobId'] == jid
    enabled = control(h, 'jobs', job, 'enable', 'enable-http-template-copy')
    assert enabled.status_code == 200, enabled.text
    job = enabled.json()['result']['object']
    rid = launch(h, job, 'http-template-run')['runId']
    asyncio.run(h.service.dispatch_once())
    original_run = h.service.get_run(rid, actor=USER)
    assert original_run['state'] == 'completed' and len(h.provider.calls) == 1
    source = h.service.get('action', h.action['id'], actor=USER)
    retired = h.client.request('DELETE', '/api/jobs/actions/' + source['id'], json={
        'expectedRevision':source['revision'], 'expectedStateRevision':source['stateRevision'], 'requestKey':'retire-http-template-action'})
    assert retired.status_code == 200, retired.text
    before = canonical_state(h.service)
    blocked = h.client.post('/api/jobs/job-templates/' + item['id'] + '/instantiate', json={
        'expectedRevision':2, 'idempotencyKey':'blocked-http-template-copy'})
    assert blocked.status_code == 409 and blocked.json()['error']['code'] == 'reference_conflict'
    assert canonical_state(h.service) == before
    replacement = h.service.publish_action('http_template_replacement', definition('action', source))
    repaired = {**changed, 'config':{**item['config'],'actionId':replacement['id']}}
    saved = h.client.put('/api/jobs/job-templates/' + item['id'], json={'item':repaired, 'expectedRevision':2})
    assert saved.status_code == 200, saved.text
    assert saved.json()['item']['revision'] == 3
    copied = h.client.post('/api/jobs/job-templates/' + item['id'] + '/instantiate', json={
        'expectedRevision':3, 'idempotencyKey':'repaired-http-template-copy'})
    assert copied.status_code == 200, copied.text
    second = h.service.get('job', copied.json()['job']['jobId'], actor=USER)
    assert second['state'] == 'disabled' and h.service.final.runs(second['id']) == []
    assert h.service.get_run(rid, actor=USER) == original_run
    assert h.service.get('action', source['id'], actor=USER)['state'] == 'retired'
    assert len(h.service.list('job', actor=USER)['items']) == 2


@async_test
async def test_reference_replacement_failed_publication_protects_old_and_new(tmp_path,monkeypatch):
    rt,_=await setup(tmp_path)
    entered=threading.Event();release=threading.Event();original=jobs._write_job
    try:
        other={**action(),'id':'other'};rt.actions.put('actions',other)
        job=rt.create(config(),True);jid=job['jobId']
        def blocked(path,value):
            if value.get('jobId')==jid and value.get('configRevision')==2:
                entered.set();assert release.wait(3);raise OSError('replacement commit failed')
            return original(path,value)
        monkeypatch.setattr(jobs,'_write_job',blocked)
        update=asyncio.create_task(asyncio.to_thread(rt.update,jid,{**config(),'actionId':'other'},1,True))
        assert await asyncio.to_thread(entered.wait,2)
        from packages.jobs.action_templates import references
        assert references(rt.actions.read(),'pause') and references(rt.actions.read(),'other')
        with pytest.raises(PermissionError):rt.actions.delete('actions','pause')
        # Deleting the old definition must not wait for the Job file writer.
        await asyncio.wait_for(asyncio.to_thread(rt.actions.delete,'actions','pause',authorized=True,confirm_references=True),1)
        release.set()
        with pytest.raises(OSError,match='replacement'):await update
        assert rt.get(jid)['config']['actionId']=='pause'
        assert rt.view(rt.get(jid))['integrity']['status']=='incomplete'
        monkeypatch.setattr(jobs,'_write_job',original)
        await rt.close()
        fresh=Runtime(rt.root,rt.actions,rt.pause);await fresh.start()
        try:
            assert references(fresh.actions.read(),'pause')
            assert not references(fresh.actions.read(),'other')
        finally:await fresh.close()
    finally:release.set();await rt.close()


@async_test
async def test_cancel_while_snapshot_commit_inflight_settles_before_cleanup(tmp_path,monkeypatch):
    rt,calls=await setup(tmp_path);entered=threading.Event();release=threading.Event()
    original=rt.templates.snapshot
    def blocked(*args):
        entered.set();assert release.wait(3)
        return original(*args)
    monkeypatch.setattr(rt.templates,'snapshot',blocked)
    try:
        job=rt.create(config(),True);await rt.launch(job['jobId'],1)
        assert await asyncio.to_thread(entered.wait,2)
        cancel=asyncio.create_task(rt.cancel(job['jobId'],1))
        await asyncio.sleep(0);assert not cancel.done()
        release.set();await cancel
        assert not calls and not rt.actions.read().get('jobExecutions')
        assert rt.get(job['jobId'])['lastExecution']['status']=='cancelled'
    finally:release.set();await rt.close()


def test_close_during_claim_never_starts_effect(h, monkeypatch):
    from tests.test_job_final_http_service import enabled, launch, USER
    from packages.jobs.final_contracts import ContractError
    from tests.test_action_groups import canonical_state
    job = enabled(h)
    rid = launch(h, job, 'close-during-candidate')['runId']
    entered, release = threading.Event(), threading.Event()
    original = h.service._dispatch_candidates
    def blocked():
        candidates = original()
        assert (job['id'], rid) in candidates
        entered.set()
        assert release.wait(5), 'candidate claim barrier timed out'
        return candidates
    monkeypatch.setattr(h.service, '_dispatch_candidates', blocked)
    before = canonical_state(h.service)
    async def scenario():
        pending = asyncio.create_task(h.service.dispatch_once())
        try:
            assert await asyncio.to_thread(entered.wait, 3)
            await h.service.close()
        finally:
            release.set()
        with pytest.raises(ContractError) as stopped:
            await asyncio.wait_for(pending, 5)
        assert stopped.value.code == 'provider_unavailable'
    asyncio.run(scenario())
    state = h.service.final.get(job['id'])
    # Selection is a detached candidate snapshot, not an Attempt admission.
    # Closing before the writer fence commits keeps the original queued Run.
    assert state['runs'][rid]['state'] == 'queued'
    assert state['attempts'] == {} and state['effects'] == [] and h.provider.calls == []
    assert canonical_state(h.service) == before
    with pytest.raises(ContractError) as closed:
        h.service.create_run(job['id'], {'expectedRevision': job['configRevision'],
            'requestKey': 'closed-new-run', 'input': {}, 'cause': {'type': 'manual'}}, actor=USER)
    assert closed.value.code == 'provider_unavailable'
    assert canonical_state(h.service) == before


def test_duplicate_profile_owner_cannot_recover_or_close_active_execution(h):
    from tests.test_job_final_http_service import enabled, launch
    from packages.jobs.final_service import FinalService
    from packages.jobs.final_contracts import ContractError
    from packages.jobs.runtime_owner import OwnershipConflict
    from packages.jobs.final_runtime.executor import TypedExecutor
    h.service.owner.claim(0)
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        class Provider:
            async def send_session(self, session_id, text, *, effect_key, source):
                calls.append(dict(effect_key))
                entered.set()
                await release.wait()
                return {'status': 'queued', 'messageId': 'owner-original'}
        h.service.executor = TypedExecutor(session_provider=Provider())
        job = enabled(h)
        rid = launch(h, job, 'owner-original-run')['runId']
        token = h.service.owner.token
        duplicate = FinalService(h.root, h.automation.auth, executor=h.service.executor)
        dispatch = asyncio.create_task(h.service.dispatch_once())
        try:
            await asyncio.wait_for(entered.wait(), 3)
            before = copy.deepcopy(h.service.final.get(job['id']))
            with pytest.raises(OwnershipConflict) as busy:
                duplicate.owner.claim(token[0])
            assert busy.value.reason == 'owner_busy'
            with pytest.raises(ContractError) as fenced:
                duplicate.recover()
            assert fenced.value.code == 'revision_conflict'
            await duplicate.close()
            assert h.service.owner.token == token and h.service.owner.lock.file is not None
            assert h.service.final.get(job['id']) == before
            assert before['runs'][rid]['state'] == 'running' and len(calls) == 1
            release.set()
            await asyncio.wait_for(dispatch, 5)
            done = h.service.final.get(job['id'])
            assert done['runs'][rid]['state'] == 'completed'
            assert len(done['attempts']) == len(done['effects']) == len(calls) == 1
        finally:
            release.set()
            if not dispatch.done():
                dispatch.cancel()
                await asyncio.gather(dispatch, return_exceptions=True)
            await duplicate.close()
    asyncio.run(run())


def test_notification_retry_is_independent_of_effect_and_snapshot(h):
    from tests.test_job_final_http_service import enabled, launch
    from tests.test_action_unification_p4 import plans
    job = enabled(h)
    rid = launch(h, job, 'notification-independent')['runId']
    asyncio.run(h.service.dispatch_once())
    done = h.service.final.get(job['id'])
    assert done['runs'][rid]['state'] == 'completed' and len(h.provider.calls) == 1
    effects = copy.deepcopy(done['effects'])
    frozen = copy.deepcopy(plans(h, job['id'])[rid]['frozen'])
    original_ids = set(done['outbox'])
    assert original_ids and all(row['status'] == 'pending' for row in done['outbox'].values())
    # No transport is available: no sending reservation or effect retry.
    asyncio.run(h.service.deliver_outbox())
    assert h.service.final.get(job['id']) == done
    seen = []
    async def sender(row):
        seen.append(copy.deepcopy(row))
        return {'outcome': 'succeeded', 'receiptId': row['outboxId']}
    h.service.outbox_sender = sender
    asyncio.run(h.service.deliver_outbox())
    current = h.service.final.get(job['id'])
    assert {row['outboxId'] for row in seen} == original_ids
    assert all(row['status'] == 'acked' for row in current['outbox'].values())
    assert current['effects'] == effects and plans(h, job['id'])[rid]['frozen'] == frozen
    assert len(current['attempts']) == len(h.provider.calls) == 1
    asyncio.run(h.service.deliver_outbox())
    asyncio.run(h.service.dispatch_once())
    assert len(seen) == len(original_ids) and len(h.provider.calls) == 1
    assert h.service.final.get(job['id']) == current


def test_shared_scheduler_entry_and_misfire_do_not_replay_effects(canonical_h):
    from tests.test_retirement_rev16_behavior import schedule, fire, instant
    from packages.jobs.final_service import definition
    from packages.core import session
    h = canonical_h
    missed, due = instant(30), instant(35)
    job, creator, _ = schedule(h, [{'ruleId':'original', 'type':'interval', 'seconds':'60',
        'anchor':missed, 'advanceMode':'grid'}])
    cfg = definition('event_creator', creator)
    cfg['config']['misfirePolicy'] = 'skip'
    cfg['config']['graceSeconds'] = '0'
    changed = h.client.put('/api/event-creators/' + creator['id'], json={
        'definition':cfg, 'expectedRevision':creator['revision'], 'requestKey':'skip-misfire'})
    assert changed.status_code == 200, changed.text
    creator = changed.json()['result']['object']
    fire(h, due)
    skipped = h.service.creators.get(creator['id'])
    assert skipped['drives'][-1]['outcome'] == 'skipped'
    assert skipped['producedCount'] == 0
    state = h.service.final.get(job['id'])
    assert not state['runs'] and not state['attempts'] and not state['effects']
    assert not session.get('ses_a').queue_pending
    # Replace the exhausted rule through the same public CAS API. The new
    # occurrence has its own Event; repeating its tick must not resend it.
    next_at = instant(60)
    cfg = definition('event_creator', creator)
    cfg['config']['rules'] = [{'ruleId':'replacement', 'type':'once', 'at':next_at}]
    cfg['config']['misfirePolicy'] = 'fire_now'
    changed = h.client.put('/api/event-creators/' + creator['id'], json={
        'definition':cfg, 'expectedRevision':creator['revision'], 'requestKey':'fire-now'})
    assert changed.status_code == 200, changed.text
    fire(h, next_at)
    history = h.service.final.history(job['id'])
    assert len(history['runs']) == len(h.service.final.get(job['id'])['attempts']) == len(history['effects']) == 1
    event = h.service.events.get(history['runs'][0]['cause']['eventId'])['event']
    assert event['payload']['ruleId'] == 'replacement'
    assert event['payload']['scheduledAt'] == next_at
    assert len(session.get('ses_a').queue_pending) == 1
    fire(h, next_at)
    assert h.service.final.history(job['id']) == history
    assert len(session.get('ses_a').queue_pending) == 1


@pytest.mark.parametrize('gate', ['disabled', 'high_risk'])
def test_disabled_and_high_risk_action_gates_block_job_effects(h, gate):
    from tests.test_job_final_http_service import enabled, job_def, launch, USER
    from packages.jobs.final_service import definition, object_ref
    if gate == 'high_risk':
        action = h.service.get('action', h.action['id'], actor=USER)
        value = definition('action', action)
        value['effectClass'] = 'high_risk'
        ref = object_ref('action', h.service.publish_action('unapproved_high_risk', value))
        cfg = job_def(h)
        cfg['steps'][0]['actionRef'] = ref
    else:
        cfg = job_def(h)
    job = enabled(h, cfg)
    rid = launch(h, job, 'gated-run')['runId']
    if gate == 'disabled':
        action = h.service.get('action', h.action['id'], actor=USER)
        response = h.client.post('/api/jobs/actions/' + action['id'] + '/operations', json={
            'operation': 'disable', 'expectedRevision': action['revision'],
            'expectedStateRevision': action['stateRevision'], 'requestKey': 'disable-before-effect', 'parameters': {}})
        assert response.status_code == 200, response.text
        before = copy.deepcopy(h.service.final.get(job['id']))
        blocked = h.client.post('/api/jobs/' + job['id'] + '/runs', json={
            'expectedRevision': job['configRevision'], 'requestKey': 'disabled-new-admission',
            'input': {}, 'cause': {'type': 'manual'}})
        assert blocked.status_code == 409, blocked.text
        assert blocked.json()['error']['code'] == 'reference_conflict'
        assert h.service.final.get(job['id']) == before
    asyncio.run(h.service.dispatch_once())
    state = h.service.final.get(job['id'])
    assert state['runs'][rid]['state'] == 'failed'
    assert state['attempts'] == {} and state['effects'] == [] and h.provider.calls == []
    assert state['steps'][rid]['s0']['attemptCount'] == 0
    asyncio.run(h.service.dispatch_once())
    assert h.service.final.get(job['id']) == state and h.provider.calls == []
