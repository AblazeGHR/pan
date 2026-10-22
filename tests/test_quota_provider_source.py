"""Provider IDs never depend on native/managed identity mapping. No real IO."""
import asyncio
import copy
import json
import threading
import time

import pytest

from packages.automation.quota_host import ReadSource,HostAdapter,Bindings
from packages.automation.quota_policy import Runtime,normalize,rank,Unavailable
from packages.cc_switch_bridge.provider import Bridge
from tests.test_quota_host import source_fixture,objects,probe
from tests.test_automation_core import async_test


def test_provider_quota_is_known_without_managed_native_binding(tmp_path,monkeypatch):
    source,store,profile,now,trace=source_fixture(tmp_path,monkeypatch)
    monkeypatch.setenv('PAN_TEST_QUOTA_HOST_NOW',str(now))
    value=source.read()
    assert value['policySnapshot']['status']=='known'
    assert value['policySnapshot']['identityDomain']=='cc-switch-provider:codex'
    assert value['policySnapshot']['currentAccountId']=='provider-a'
    assert [r['id'] for r in value['policySnapshot']['accounts']]==['provider-a','provider-b']
    assert value['managed']['defaultAccountId']=='managed-a'
    assert value['native']['accountId']=='native-account'


def injected(tmp_path):
    now=float(int(time.time()));calls=[]
    listing={'currentProviderId':'p-a','providers':[{'id':'p-a','category':'official'},
        {'id':'p-b','category':'official'},{'id':'other','category':'third_party'}]}
    quotas={i:{'app':'codex','providerId':i,'status':'ok','available':True,'queriedAt':now*1000,
               'result':{'type':'subscription','quota':{'success':True,'tiers':[
                   {'name':'five_hour','utilization':20,'resetsAt':now+100},
                   {'name':'seven_day','utilization':40 if i=='p-a' else 10,'resetsAt':now+(500 if i=='p-a' else 200)}]}}} for i in ('p-a','p-b')}
    def runner(args):
        calls.append(args)
        if args==['auth','status','--json']:
            return {'default_account_id':'managed','accounts':[{'id':'managed','access_token':'must-not-leak'}]}
        if args==['--app','codex','provider','list','--json']:return copy.deepcopy(listing)
        assert args[:4]==['--app','codex','provider','quota'] and args[-1]=='--json'
        return copy.deepcopy(quotas[args[4]])
    def unavailable():raise ValueError('Native profile deliberately unavailable')
    bridge=Bridge(tmp_path/'bridge',runner)
    source=ReadSource(tmp_path,bridge=bridge,profile_resolver=unavailable,clock=lambda:now)
    return source,bridge,listing,quotas,calls,now


def test_official_only_sampling_is_read_only_and_ranked_in_provider_domain(tmp_path):
    s,b,l,q,calls,now=injected(tmp_path)
    b._lock=lambda:(_ for _ in ()).throw(AssertionError('Read must not lock'))
    result=s.read();snapshot=normalize(result['policySnapshot'],now)
    assert snapshot['identityDomain']=='cc-switch-provider:codex' and snapshot['currentAccountId']=='p-a'
    assert [r['id'] for r in rank(snapshot)]==['p-b','p-a']
    assert result['native']['status']=='unknown' and result['managed']['status']=='known'
    assert result['profileBindings']=={} and not s.current(result)
    assert result['provider']['providers'][0]['queriedAtMillis']==now*1000
    assert result['provider']['providers'][0]['providerUpdatedAt'] is None
    assert {c[4] for c in calls if c[3:4]==['quota']}=={'p-a','p-b'}
    assert not b.data_dir.exists()  # no .bridge.lock / quota cache creation
    assert 'must-not-leak' not in json.dumps(result)


def test_read_only_refuses_mutations_and_does_not_change_shared_deadline(tmp_path):
    s,b,l,q,calls,now=injected(tmp_path);b._deadline=123
    b.read_only('list',{'app':'codex'})
    assert b._deadline==123
    with pytest.raises(ValueError):b.read_only('switch',{'app':'codex','id':'p-b','expectedCurrentId':'p-a'})
    with pytest.raises(ValueError):b.read_only('quota',{'app':'codex','id':'p-a'},deadline=time.monotonic()-1)
    with pytest.raises(ValueError):b.read_only('list',{'app':'codex','executable':'untrusted'})
    assert not any(c[3:4] in (['quota'],['switch']) for c in calls)


def test_managed_query_failure_does_not_poison_provider_quota(tmp_path):
    s,b,l,q,calls,now=injected(tmp_path);run=b.runner
    def runner(args):
        if args==['auth','status','--json']:
            assert {c[4] for c in calls if c[3:4]==['quota']}=={'p-a','p-b'}
            raise RuntimeError('managed auth unavailable')
        return run(args)
    b.runner=runner;v=s.read()
    assert v['managed']['status']=='unknown' and v['native']['status']=='unknown'
    assert v['policySnapshot']['status']=='known' and v['profileBindings']=={}


@pytest.mark.parametrize('failure',['transport','catalog_removed','category_changed'])
def test_failed_or_changed_query_is_unknown_without_retry(tmp_path,failure):
    s,b,l,q,calls,now=injected(tmp_path);run=b.runner;count=[]
    def runner(args):
        if args[3:5]==['quota','p-b']:
            count.append(1)
            if failure=='transport':raise RuntimeError('credential must-not-leak')
            if failure=='catalog_removed':l['providers'].pop(1)
            else:l['providers'][1]['category']='third_party'
        return run(args)
    b.runner=runner;v=s.read()
    assert v['policySnapshot']['status']=='unknown' and count==[1]
    assert 'must-not-leak' not in json.dumps(v)


@pytest.mark.parametrize('bad',['future','stale','seconds_not_millis','bool_time','nan_time','missing_time',
    'negative','over_100','bool_usage','nan_usage','reset_missing','reset_expired','reset_naive',
    'missing_week','duplicate','unknown_name','too_many_tiers','error','not_available','wrong_id','wrong_app','failed_success'])
def test_invalid_provider_sample_is_explicit_unknown_and_never_ranked(tmp_path,bad):
    s,b,l,q,calls,now=injected(tmp_path);value=q['p-b'];tiers=value['result']['quota']['tiers']
    if bad=='future':value['queriedAt']=(now+1)*1000
    elif bad=='stale':value['queriedAt']=(now-301)*1000
    elif bad=='seconds_not_millis':value['queriedAt']=now
    elif bad=='bool_time':value['queriedAt']=True
    elif bad=='nan_time':value['queriedAt']=float('nan')
    elif bad=='missing_time':value.pop('queriedAt')
    elif bad in ('negative','over_100','bool_usage','nan_usage'):
        tiers[0]['utilization']={'negative':-1,'over_100':101,'bool_usage':True,'nan_usage':float('nan')}[bad]
    elif bad=='reset_missing':tiers[1].pop('resetsAt')
    elif bad=='reset_expired':tiers[1]['resetsAt']=now
    elif bad=='reset_naive':tiers[1]['resetsAt']='2099-01-01T00:00:00'
    elif bad=='missing_week':tiers.pop()
    elif bad=='duplicate':tiers.append(copy.deepcopy(tiers[0]))
    elif bad=='unknown_name':tiers[1]['name']='weekly_scoped_unrelated'
    elif bad=='too_many_tiers':tiers*=17
    elif bad=='error':value['error']='secret provider error must-not-leak'
    elif bad=='not_available':value['available']=False
    elif bad=='wrong_id':value['providerId']='managed'
    elif bad=='wrong_app':value['app']='claude'
    else:value['result']['quota']['success']=False
    v=s.read();snap=normalize(v['policySnapshot'],now)
    assert snap['status']=='unknown' and snap['identityDomain']=='cc-switch-provider:codex'
    assert rank(snap)==[] and 'must-not-leak' not in json.dumps(v)
    assert next(r for r in v['provider']['providers'] if r['id']=='p-b')['status']=='unknown'


def test_disabled_is_not_queried_or_used_as_reset_candidate(tmp_path):
    s,b,l,q,calls,now=injected(tmp_path)
    value=s.read(['p-b']);snap=normalize(value['policySnapshot'],now)
    assert snap['status']=='known' and [r['id'] for r in rank(snap)]==['p-a']
    assert not any(c[3:5]==['quota','p-b'] for c in calls)
    assert value['provider']['providers'][1]=={'id':'p-b','status':'disabled'}
    assert s.read(['p-a'])['policySnapshot']['status']=='unknown'


@pytest.mark.parametrize('bad',['duplicate','missing_current','not_official_current','capacity','bad_id','invalid_row'])
def test_catalog_rejects_unknown_and_capacity_before_quotas(tmp_path,bad):
    s,b,l,q,calls,now=injected(tmp_path)
    if bad=='duplicate':l['providers'].append(l['providers'][0])
    elif bad=='missing_current':l['currentProviderId']='missing'
    elif bad=='not_official_current':l['currentProviderId']='other'
    elif bad=='capacity':l['providers']=[{'id':'p'+str(i),'category':'official'} for i in range(33)];l['currentProviderId']='p0'
    elif bad=='bad_id':l['providers'][0]['id']='-bad'
    else:l['providers'].append('bad')
    assert s.read()['policySnapshot']['status']=='unknown'
    if bad!='not_official_current':assert not any(c[3:4]==['quota'] for c in calls)


@async_test
async def test_provider_changes_during_quota_and_unrelated_session_not_blocked(tmp_path):
    s,b,l,q,calls,now=injected(tmp_path);entered=threading.Event();release=threading.Event();run=b.runner
    def blocked(args):
        if args[3:5]==['quota','p-a']:
            entered.set();assert release.wait(5)
        return run(args)
    b.runner=blocked
    task=asyncio.create_task(asyncio.to_thread(s.read))
    assert await asyncio.to_thread(entered.wait,5)
    from packages.core.worker import queue_lock
    async with queue_lock('owned-unrelated-provider-fixture'):l['currentProviderId']='p-b'
    release.set();value=await task
    assert value['policySnapshot']['status']=='unknown' and value['provider']['stable'] is False


@async_test
async def test_global_current_never_backfills_existing_worker_and_sampling_persists(tmp_path):
    source,b,l,q,calls,now=injected(tmp_path);module,w,session=objects()
    rt=Runtime(tmp_path/'policy',tmp_path/'jobs',clock=lambda:now)
    rt.store.set_host({'enabled':True,'sessionRoles':{'s':'ta'}},0)
    a=HostAdapter(rt.store,source,Bindings(module,probe,clock=lambda:now),clock=lambda:now)
    rt.adapter=a;a.runtime=rt
    assert (await rt.query())['currentAccountId']=='p-a'
    inv=await a.inventory();assert inv['workers'][0]['accountId'] is None
    raw=await Bindings(module,probe,clock=lambda:now).inventory({'s':'ta'},{'profile-a':'p-a'},'p-a')
    assert raw['workers'][0]['accountId'] is None  # even a supplied old map cannot backfill
    rt.store.configure('automatic','ma',rt.store.read()['revision'])
    issue={'classification':'codex_quota_exhausted','issueId':'own','confidence':'structured',
           'correlation':{'runtimeEpoch':'owner','workerId':'w','sessionId':'s','generation':1,'taskSeq':1}}
    assert (await rt.consume(issue))['status']=='unknown'
    assert not rt.store.read()['commands']
    recovered=Runtime(tmp_path/'policy',tmp_path/'jobs')
    assert recovered.store.read()['quota']['identityDomain']=='cc-switch-provider:codex'
    assert recovered.store.read()['hostObservation']['provider']['currentProviderId']=='p-a'


@async_test
async def test_disabled_changed_during_sample_rejects_old_publication(tmp_path):
    source,b,l,q,calls,now=injected(tmp_path);module,w,session=objects()
    rt=Runtime(tmp_path/'policy',tmp_path/'jobs',clock=lambda:now)
    rt.store.set_host({'enabled':True,'sessionRoles':{}},0)
    a=HostAdapter(rt.store,source,Bindings(module,probe),clock=lambda:now);a.runtime=rt
    entered=threading.Event();release=threading.Event();run=b.runner
    def blocked(args):
        if args[3:5]==['quota','p-a']:entered.set();assert release.wait(5)
        return run(args)
    b.runner=blocked;task=asyncio.create_task(a.sample())
    assert await asyncio.to_thread(entered.wait,5)
    await asyncio.to_thread(rt.store.set_disabled,['p-b'],rt.store.read()['revision'])
    release.set()
    with pytest.raises(Unavailable):await task
    assert 'hostObservation' not in rt.store.read()
