"""Rev7 consumes immutable product runtime releases; owned synthetic input only."""
import json
import pytest

from packages.jobs import final_contracts as fc
from scripts.job_final_migration import apply as migration
from scripts.job_final_migration.inventory import build_inventory
from scripts.job_final_migration.runtime_fields import RUNTIME_OPT_IN, RUNTIME_PRODUCTS
from tests.test_job_migration_runtime_readiness import _runtime_delivery_source
from tests.test_job_final_migration_apply import context_for
from tests.support.job_migration_readiness_rehearsal import current_actions


def runtime_context(root):
    context = context_for(root)
    inventory = build_inventory(root, runtime_delivery=True)
    # Reuse the original context factory on each eligible shape without changing
    # its global/default behavior; synthetic authority is explicit below.
    from tests.test_job_final_contracts import object_document
    from packages.jobs.final_service import FinalService
    from types import SimpleNamespace
    from contextlib import nullcontext
    class Collector:
        executor = SimpleNamespace(channel_provider=object())
        def __init__(self): self.actions = {}
        def publish_action(self, key, definition):
            self.actions[key] = object_document('action', fc.validate_definition('action', definition), id=key, revision=1)
        def transaction(self, **kwargs): return nullcontext(None)
        def _get(self, db, kind, key): return self.actions[key]
    collector = Collector()
    FinalService._install_product_actions(collector)
    actions = {**context['actions'], **current_actions()}
    for key, product in RUNTIME_PRODUCTS.items():
        obj = collector.actions[product]
        definition = {k:v for k,v in obj.items() if k in fc.schema_artifacts()['action_definition.schema.json']['properties']}
        actions[key] = {'object': obj, 'ref': {'id': product, 'revision': obj['revision'], 'digest': fc.digest(definition)},
                        'completions': {'ordinary': {'contract': obj['defaultCompletion']}}}
    from packages.scheduler.providers import TimeProvider
    provider = TimeProvider()
    for item in inventory['instances']:
        if item['classificationDetail'] != 'simple': continue
        context['records'].setdefault(item['migrationId'], {
            'originalDigest': item['originalDigest'], 'principal': {'type':'user','id':'local'},
            'owner': {'type':'user','id':'local'}, 'oldOwnerStopped':True,'oldOwnerEpoch':1,
            'fenceEvidence':'synthetic source has no running manager',
            'authorizationEvidence':'explicit synthetic test input only', 'importedAt':'2026-10-31T00:00:00+00:00',
            'policies': {'overlap':{'mode':'forbid','maxActive':1,'maxPending':0},
                'recovery':{'automatic':False,'maxAttempts':1,'backoffSeconds':['1'],'stableResetSeconds':'0'},
                'notification':{'on':[],'recipients':[]},'retention':{'policyRef':'synthetic'}},
            'scheduler': {'advanceMode':'grid','dstPolicy':'gap-skip-fold-first','graceSeconds':'60',
                'pausePolicy':'advance','providerRef':{'type':'time','id':'scheduler','releaseDigest':provider.release_digest,
                    'generation':provider.generation},'configSchemaRef':provider.config_schema_ref,
                'requiredCapabilities':list(provider.capabilities)}})
    context.update(actions=actions, runtimeDelivery=RUNTIME_OPT_IN)
    return context


def test_rev7_all_six_legacy_types_runtime_and_frozen_graph(tmp_path):
    from tests.test_job_final_migration import _make_root
    root=_make_root(tmp_path)
    inv=build_inventory(root,runtime_delivery=True)
    assert inv['summary']['simple']==6
    context=runtime_context(root)
    target=tmp_path/'all-six.json'
    migration.migrate(root,target,context,apply=True,expected_digest=migration.plan_digest(inv,context))
    bundle=migration.read_bundle(target)
    assert len(bundle['entries'])==6
    assert sum('runtimeMapping' in e for e in bundle['entries'])==2
    for row in bundle['canonical']:
        fc.validate_object(row['kind'],row['object'])


@pytest.mark.parametrize('kind', ['assign','send_session','legacy-assign','message','broadcast'])
def test_rev7_opt_in_maps_real_runtime_release_input_owner_phase(tmp_path, kind):
    root = tmp_path/'source'
    _runtime_delivery_source(root, kind)
    context = runtime_context(root)
    inventory = build_inventory(root, runtime_delivery=True)
    assert inventory['summary']['simple'] == 1
    target = tmp_path/'bundle.json'
    report = migration.migrate(root,target,context,apply=True,expected_digest=migration.plan_digest(inventory,context))
    bundle = migration.read_bundle(target)
    job = next(r['object'] for r in bundle['canonical'] if r['kind']=='job')
    assert job['deliveryRuntime']['phase']=='before-reservation'
    assert job['deliveryRuntime']['owner']=='delivery-manager'
    step = job['steps'][0]
    assert step['actionRef']['id'] in RUNTIME_PRODUCTS.values()
    assert step['arguments']['text']=={'from':'input','path':'delivery.text'}
    assert report['runtimeActivated'] is False
    assert migration.migrate(root,target,context,apply=True,
        expected_digest=migration.plan_digest(inventory,context))['targetWrites']==0


def test_rev7_old_release_does_not_gain_runtime_from_opt_in(tmp_path):
    root=tmp_path/'source'
    _runtime_delivery_source(root,'assign')
    context=runtime_context(root)
    context['actions']['session.assign']=current_actions()['session.assign']
    with pytest.raises(ValueError,match='explicit pinned product runtime release'):
        migration.migrate(root,tmp_path/'absent/bundle.json',context)
    assert not (tmp_path/'absent').exists()


def test_rev7_opt_in_qq_without_runtime_release_is_preserved(tmp_path):
    from tests.test_job_final_migration import _scheduled_job,_write_json
    root=tmp_path/'source'
    value=_scheduled_job()
    value.update(targetSessionId=None,action={'api':'send_qq','args':{'targetType':'group','targetId':'123456','botUin':'654321'}})
    _write_json(root/'background_jobs/jobs/job_qq.json',value)
    inv=build_inventory(root,runtime_delivery=True)
    item,=inv['instances']
    assert item['classification']=='incompatible-preserved' and item['targetIds']==[]
    assert 'runtime_binding_unavailable' in item['reasonCodes']
    assert 'QQ runtime bot/target/text' in item['rejectionReasons'][0]


def test_rev7_opt_in_does_not_mutate_plain_process_or_shell_mapping(tmp_path):
    from tests.test_job_final_migration import _background_job,_scheduled_job,_write_json
    from scripts.job_final_migration.mapping import Mapper
    root=tmp_path/'source'
    _write_json(root/'background_jobs/jobs/job_process.json',_background_job(root))
    shell=_scheduled_job()
    shell['action']={'api':'shell','args':{'command':'D:/synthetic/no-execute.cmd','cwd':'D:/synthetic'}}
    _write_json(root/'background_jobs/jobs/job_shell.json',shell)
    context=context_for(root)
    plain=Mapper(context)
    opted=Mapper({**context,'runtimeDelivery':RUNTIME_OPT_IN})
    sources=migration._inputs(root)
    for item in build_inventory(root)['instances']:
        source=migration._source(item,sources)
        assert plain.map(item,source)==opted.map(item,source)
    assert plain.objects==opted.objects


def test_rev7_runtime_cli_opt_in_matches_reviewed_context_and_stale_digest(tmp_path,capsys):
    from scripts.job_final_migration.cli import main
    root=tmp_path/'source'
    path,source=_runtime_delivery_source(root,'send_session')
    context=runtime_context(root)
    context_path=tmp_path/'context.json'
    context_path.write_text(json.dumps(context),encoding='utf-8')
    target=tmp_path/'bundle.json'
    common=['--root',str(root),'--out','-','--target',str(target),'--context',str(context_path)]
    assert main(common)==2
    assert 'match reviewed context' in capsys.readouterr().err and not target.exists()
    assert main(common+['--runtime-delivery'])==0
    dry=json.loads(capsys.readouterr().out)
    source['text']='changed after reviewed dry-run'
    path.write_text(json.dumps(source),encoding='utf-8')
    assert main(common+['--runtime-delivery','--apply','--expected-digest',dry['planDigest']])==2
    assert 'plan_stale' in capsys.readouterr().err and not target.exists()


@pytest.mark.parametrize('kind',['assign','send_session','message','broadcast'])
def test_rev7_null_target_is_backlog_input_not_missing_function(tmp_path,kind):
    root=tmp_path/'source'
    path,source=_runtime_delivery_source(root,kind)
    source['targetSessionIds' if kind=='broadcast' else 'targetSessionId']=None
    path.write_text(json.dumps(source),encoding='utf-8')
    inv=build_inventory(root,runtime_delivery=True)
    assert inv['summary']['simple']==1
    context=runtime_context(root)
    report=migration.migrate(root,tmp_path/'bundle.json',context)
    assert report['plan']['items'][0]['targetIds']
    assert report['instances'][0]['targetIds']==[]  # dry-run source inventory retains original identity
    assert path.read_text(encoding='utf-8')==json.dumps(source)


@pytest.mark.parametrize('kind',['assign','send_session','broadcast'])
@pytest.mark.parametrize('outcome',['accepted','unknown'])
def test_rev7_actual_import_event_backlog_retarget_cold_no_resend(tmp_path,kind,outcome):
    import asyncio
    import copy
    import hashlib
    from packages.core import session,worker
    from packages.jobs.final_api import CanonicalUserAuth
    from packages.jobs.final_service import FinalService,definition
    from tests.support.job_migration_runtime_rehearsal import hashes
    profile=tmp_path/'owned-profile'
    path,source=_runtime_delivery_source(profile,kind)
    # Keep the exact schedule grid/cursor offset explicit for the real importer.
    if kind=='broadcast':
        source['targetSessionIds']=None
    else:
        source['targetSessionId']=None
        source['schedule'][0]['at']=source['schedule'][0]['nextFireAt']='2030-01-01T00:00:00+00:00'
        source['nextFireAt']='2030-01-01T00:00:00+00:00'
    path.write_text(json.dumps(source),encoding='utf-8')
    original=path.read_bytes()
    for sid in ('rev7_target','rev7_later','sess_creator'):
        session.save(session.Session(id=sid,name='owned rev7',workdir=str(tmp_path),queue_paused=True))
    before_workers=set(worker.workers)
    service=FinalService(profile/'job-final',CanonicalUserAuth(profile/'auth'))
    context=runtime_context(profile)
    context['profileId']=service.profile_id
    target=tmp_path/'import-bundle.json'
    inv=build_inventory(profile,runtime_delivery=True)
    result=migration.migrate(profile,target,context,apply=True,mark_read_only=True,
        expected_digest=migration.plan_digest(inv,context))
    bundle=migration.read_bundle(target)
    ids={r['kind']:r['object']['id'] for r in bundle['canonical']}
    spec={'path':str(target),'expected_hash':hashlib.sha256(target.read_bytes()).hexdigest(),
          'expected_plan_digest':bundle['planDigest'],'active_ids':list(ids.values())}
    actor={'type':'user','id':'local'}
    async def run():
        service.creators.clock=lambda: __import__('packages.scheduler.providers',fromlist=['instant']).instant('2030-01-01T00:00:00+00:00')
        await service.start(activation=spec)
        try:
            await service.tick()
            await service.tick()
            events=service.events.page()
            assert len(events)==1
            event=copy.deepcopy(service.events.get(events[0]['eventId']))
            assert service.final.history(ids['job'])['runs']==[]
            assert not session.get('rev7_target').queue_pending
        finally:
            await service.close()
        cold=FinalService(service.root,service.auth)
        cold.creators.clock=service.creators.clock
        calls=[]
        if outcome=='unknown':
            from packages.actions.typed_executor import WorkerSessionProvider,WorkerAssignProvider
            class UnknownSend(WorkerSessionProvider):
                async def send_session(self,sid,text,**kwargs):
                    calls.append((sid,text))
                    return {'status':'unknown'}
            class UnknownAssign(WorkerAssignProvider):
                async def assign(self,*args,**kwargs):
                    calls.append((args,kwargs))
                    return {'status':'unknown'}
            cold.executor.session_provider=UnknownSend()
            cold.executor.assign_provider=UnknownAssign()
        try:
            await cold.start(activation=spec)
            current=cold.get('job',ids['job'],actor=actor)
            cfg=definition('job',current)
            field='sessionIds' if kind=='broadcast' else 'sessionId'
            cfg['arguments']['delivery']={field:['rev7_target'] if kind=='broadcast' else 'rev7_target',
                                         'text':'  updated runtime body\n原文  '}
            changed=cold.write('job',ids['job'],{'definition':cfg,'expectedRevision':current['configRevision'],
                'requestKey':'rev7-retarget'},actor=actor)
            assert changed['result']['runtimeAudit']['after']['inputs']['delivery.text']==cfg['arguments']['delivery']['text']
            await cold.tick()
            state=cold.final.get(ids['job'])
            assert len(state['runs'])==len(state['attempts'])==len(state['effects'])==1
            rid=next(iter(state['runs']))
            assert state['runs'][rid]['cause']['eventId']==event['event']['eventId']
            frozen=copy.deepcopy(cold.final.store.get(ids['job'])['finalRuntime']['plans'][rid]['frozen'])
            receipt=state['effects'][0]['receipt']['deliveryRuntime']
            assert receipt['owner']=='delivery-manager' and receipt['acceptance'][0]['outcome']==('succeeded' if outcome=='accepted' else 'unknown'), state['effects']
            child=receipt['children'][0]
            assert child['status']==('accepted' if outcome=='accepted' else 'unknown')
            assert child['parentFence']['attemptId']==state['effects'][0]['attemptId']
            assert len(session.get('rev7_target').queue_pending)==int(outcome=='accepted')
            if outcome=='accepted':
                assert session.get('rev7_target').queue_pending[0]['text']==cfg['arguments']['delivery']['text']
            latest=changed['result']['object']
            cfg['arguments']['delivery'][field]=['rev7_later'] if kind=='broadcast' else 'rev7_later'
            cfg['arguments']['delivery']['text']='must not resend'
            cold.write('job',ids['job'],{'definition':cfg,'expectedRevision':latest['configRevision'],
                'requestKey':'after-accepted'},actor=actor)
            await cold.tick()
            assert not session.get('rev7_later').queue_pending
            assert cold.final.store.get(ids['job'])['finalRuntime']['plans'][rid]['frozen']==frozen
        finally:
            await cold.close()
        reopened=FinalService(service.root,service.auth)
        try:
            await reopened.start(activation=spec)
            await reopened.tick()
            assert len(reopened.final.get(ids['job'])['runs'])==1
            assert len(session.get('rev7_target').queue_pending)==int(outcome=='accepted') and not session.get('rev7_later').queue_pending
            if outcome=='unknown':
                assert len(calls)==1 and reopened.final.get(ids['job'])['runs'][rid]['state']=='uncertain'
            assert reopened.events.get(event['event']['eventId'])==event
        finally:
            await reopened.close()
    asyncio.run(run())
    assert set(worker.workers)==before_workers and path.read_bytes()==original
