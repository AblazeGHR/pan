"""Creation convenience retains strict canonical documents and operation identity."""
import copy
from concurrent.futures import ThreadPoolExecutor
import pytest
from tests.test_job_final_http_service import h, job_def, create
from packages.jobs.final_service import FinalService


def post(h,key,**fields):
    value=job_def(h);value.pop('name');value.update(fields)
    return h.client.post('/api/jobs',json={'definition':value,'expectedRevision':0,'requestKey':key})


def test_omitted_name_smallest_vacancy_explicit_trim_and_default_description(h):
    create(h,value={**job_def(h),'name':'job-1'},key='one')
    create(h,value={**job_def(h),'name':'job-3'},key='three')
    r=post(h,'auto');assert r.status_code==201,r.text
    item=r.json()['result']['object'];assert item['name']=='job-2' and item['description']==''
    explicit=post(h,'explicit',name='  周报  ');assert explicit.status_code==201,explicit.text
    assert explicit.json()['result']['object']['name']=='周报'
    assert post(h,'next').json()['result']['object']['name']=='job-4'


def test_same_key_replay_and_cold_response_loss_never_allocates_another_name(h):
    first=post(h,'original');assert first.status_code==201,first.text
    repeat=post(h,'original');assert repeat.status_code==200 and repeat.json()['replayed']
    assert repeat.json()['operationId']==first.json()['operationId']
    assert repeat.json()['result']==first.json()['result']
    import asyncio
    asyncio.run(h.service.close())
    cold=FinalService(h.root,h.automation.auth)
    try:
        value=job_def(h);value.pop('name')
        receipt=cold.write('job',None,{'definition':value,'expectedRevision':0,'requestKey':'original'},actor={'type':'user','id':'local'})
        assert receipt['replayed'] and receipt['operationId']==first.json()['operationId']
        assert len(cold.list('job',actor={'type':'user','id':'local'})['items'])==1
    finally:asyncio.run(cold.close())


def test_concurrent_different_keys_share_canonical_scope_without_name_collision(h):
    def write(i):
        value=job_def(h);value.pop('name')
        return h.service.write('job',None,{'definition':value,'expectedRevision':0,'requestKey':'concurrent-'+str(i)},actor={'type':'user','id':'local'})
    with ThreadPoolExecutor(max_workers=4) as pool:receipts=list(pool.map(write,range(4)))
    assert {r['result']['object']['name'] for r in receipts}=={'job-1','job-2','job-3','job-4'}
    assert len({r['operationId'] for r in receipts})==4


def test_invalid_creation_rolls_back_name_and_receipt_and_updates_stay_strict(h):
    value=copy.deepcopy(job_def(h));value.pop('name');value['steps'][0]['actionRef']['revision']=999
    r=h.client.post('/api/jobs',json={'definition':value,'expectedRevision':0,'requestKey':'broken'})
    assert r.status_code in {400,409},r.text
    assert not h.service.list('job',actor={'type':'user','id':'local'})['items']
    with h.service.transaction(readonly=True) as db:
        assert db.execute('SELECT count(*) FROM final_operations').fetchone()[0]==0
    r=post(h,'first');assert r.status_code==201,r.text
    item=r.json()['result']['object'];assert item['name']=='job-1'
    value=job_def(h);value.pop('name')
    before=copy.deepcopy(h.service.get('job',item['id'],actor={'type':'user','id':'local'}))
    r=h.client.put('/api/jobs/'+item['id'],json={'definition':value,'expectedRevision':item['configRevision'],'requestKey':'update'})
    assert r.status_code==400
    assert h.service.get('job',item['id'],actor={'type':'user','id':'local'})==before


def test_default_scope_uses_only_current_canonical_profile(h, tmp_path, monkeypatch):
    from packages.core import background_jobs
    legacy=tmp_path/'legacy'/'jobs';legacy.mkdir(parents=True)
    (legacy/'old.json').write_text('{"name":"job-1"}',encoding='utf-8')
    monkeypatch.setattr(background_jobs,'DEFAULT_ROOT',legacy.parent)
    original=(legacy/'old.json').read_bytes()
    r=post(h,'canonical-only');assert r.status_code==201,r.text
    assert r.json()['result']['object']['name']=='job-1'
    assert (legacy/'old.json').read_bytes()==original


def test_other_canonical_profile_does_not_reserve_this_profiles_name(h, tmp_path):
    import asyncio
    from packages.jobs.final_service import definition
    other=FinalService(tmp_path/'other-canonical',h.automation.auth)
    try:
        action=next(a for a in h.service.actions(actor={'type':'user','id':'local'})['items'] if a['id']==h.action['id'])
        other.publish_action(action['id'],definition('action',action))
        value=job_def(h)
        receipt=other.write('job',None,{'definition':{**value,'name':'job-1'},'expectedRevision':0,'requestKey':'other'},actor={'type':'user','id':'local'})
        assert receipt['result']['object']['name']=='job-1'
        r=post(h,'local');assert r.status_code==201,r.text
        assert r.json()['result']['object']['name']=='job-1'
    finally:asyncio.run(other.close())
