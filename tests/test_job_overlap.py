import asyncio

import pytest
from packages.actions.store import Store
from packages.jobs.action_runtime import Runtime, Conflict
from tests.test_automation_core import action


def test_parallel_claim_bound_independent_jobs_and_terminal_cleanup(tmp_path):
    async def run():
        release=asyncio.Event(); two=asyncio.Event(); calls=[]
        async def pause(sid,_):
            calls.append(sid)
            if len(calls)>=2:two.set()
            await release.wait()
            return {'ok':True}
        store=Store(tmp_path/'actions');store.put('actions',action())
        rt=Runtime(tmp_path/'jobs',store,pause);await rt.start()
        cfg={'name':'parallel','actionId':'pause','arguments':{},'enabled':True,'schedule':[{'kind':'interval','intervalSec':60}],
             'overlap':{'mode':'parallel','parallelLimit':2}}
        job=rt.create(cfg,True)
        try:
            await asyncio.gather(rt.launch(job['jobId'],1),rt.launch(job['jobId'],1))
            await asyncio.wait_for(two.wait(),2)
            assert len(rt.active(rt.get(job['jobId'])))==2
            with pytest.raises(Conflict,match='overlap capacity'):
                await rt.launch(job['jobId'],1)
            other=rt.create({**cfg,'name':'independent'},True)
            await rt.launch(other['jobId'],1)
            assert len(rt.active(rt.get(other['jobId'])))==1
            release.set()
            await asyncio.gather(*list(rt.tasks.values()))
            done=rt.get(job['jobId'])
            assert len(done['attempts'])==2
            assert len({a['runId'] for a in done['attempts']})==2
            assert all(a['status']=='completed' for a in done['attempts'])
            assert not rt.active(done)
            assert not rt.actions.read().get('jobExecutions')
        finally:release.set();await rt.close()
        with pytest.raises(Conflict,match='closed'):await rt.launch(job['jobId'],1)
    asyncio.run(run())


def test_queue_is_persistent_bounded_and_disable_cancels_waiting(tmp_path):
    async def run():
        release=asyncio.Event();entered=asyncio.Event()
        async def pause(*_):entered.set();await release.wait();return {'ok':True}
        store=Store(tmp_path/'actions');store.put('actions',action())
        rt=Runtime(tmp_path/'jobs',store,pause);await rt.start()
        cfg={'name':'queued','actionId':'pause','arguments':{},'enabled':True,'schedule':[{'kind':'interval','intervalSec':60}],
             'overlap':{'mode':'queue','parallelLimit':3}}
        job=rt.create(cfg,True)
        try:
            await rt.launch(job['jobId'],1);await asyncio.wait_for(entered.wait(),2)
            await rt.launch(job['jobId'],1)
            current=rt.get(job['jobId']);assert len(current['pendingRuns'])==1
            disabled=rt.update(job['jobId'],{**cfg,'enabled':False},1,True)
            assert not disabled['pendingRuns']
            assert len(rt.active(rt.get(job['jobId'])))==1
            release.set();await asyncio.gather(*list(rt.tasks.values()))
            await rt.tick({'enabled':True})
            assert len(rt.get(job['jobId'])['attempts'])==1
        finally:release.set();await rt.close()
    asyncio.run(run())


def test_reduce_parallel_limit_rechecks_claim_after_reference_validation(tmp_path,monkeypatch):
    store=Store(tmp_path/'actions');store.put('actions',action())
    rt=Runtime(tmp_path/'jobs',store,None)
    cfg={'name':'race','actionId':'pause','arguments':{},'enabled':True,
         'schedule':[{'kind':'interval','intervalSec':60}], 'overlap':{'mode':'parallel','parallelLimit':3}}
    job=rt.create(cfg,True)
    rt.claim(job['jobId'],'first',1);rt.claim(job['jobId'],'second',1)
    original=rt.templates.transaction
    def interleave(change):
        result=original(change)
        rt.claim(job['jobId'],'third',1)
        return result
    monkeypatch.setattr(rt.templates,'transaction',interleave)
    with pytest.raises(Conflict,match='Active execution count'):
        rt.update(job['jobId'],{**cfg,'overlap':{'mode':'parallel','parallelLimit':2}},1,True)
    current=rt.get(job['jobId'])
    assert current['configRevision']==1 and len(rt.active(current))==3
    assert current['config']['overlap']['parallelLimit']==3
