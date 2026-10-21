"""Own report configuration and non-consuming next over canonical queue facts."""
import asyncio
import copy
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from packages.core import session as sess, worker, self_queue
from packages.core.adapters import CodexAdapter
from packages.mcp import server as mcp
from packages.web import server


@pytest.fixture(autouse=True)
def no_delivery(monkeypatch):
    worker.workers.clear()
    worker._queue_locks.clear()
    for name in ('_spawn_process','create_worker','restart_or_start_worker','_wake_worker','_save_receipt','_append_new_queue_item'):
        monkeypatch.setattr(worker,name,AsyncMock(side_effect=AssertionError('read attempted delivery/persistence')))
    monkeypatch.setattr(self_queue,'consume',AsyncMock(side_effect=AssertionError('read attempted consume')))
    yield
    worker.workers.clear()


def setup():
    s = sess.create('own-report-reader',adapter='codex')
    s.report_subscriptions = {'ses-child-b','ses-child-a'}
    report = {'id':'q_real', 'queueItemId':'q_real', 'type':'report','kind':'report',
              'source':'report','sourceSessionId':'ses-child-a','sessionId':'ses-child-a',
              'status':'done','result':'full body '*100, 'taskId':'T1','workerId':'w-child',
              'deliveryState':'queued', 'revision':1}
    s.queue_pending = [report]
    sess.save_full(s)
    return s,report


def test_status_and_repeated_next_preserve_durable_queue_and_history():
    async def run():
        s,item = setup()
        disk = sess._path(s.id).read_bytes()
        before = copy.deepcopy((s.queue_pending,s.queue_delivery_ledger,s.history,s.queue_revision,s.report_subscriptions))
        status = await self_queue.report_status(s.id)
        assert status == {'ok':True,'sessionId':s.id,'reportSubscriptions':['ses-child-a','ses-child-b'],'agentReportsPaused':False}
        one = await self_queue.report_next(s.id)
        assert one == await self_queue.report_next(s.id)
        assert one['report']['queueItemId'] == item['id']
        assert one['report']['result'] == item['result']
        one['report']['result']='mutated response'
        assert item['result'] != 'mutated response'
        assert (s.queue_pending,s.queue_delivery_ledger,s.history,s.queue_revision,s.report_subscriptions) == before
        assert sess._path(s.id).read_bytes() == disk
        sess._cache.clear()
        assert (await self_queue.report_next(s.id))['report']['queueItemId'] == 'q_real'
        assert (await self_queue.report_status(s.id))['reportSubscriptions'] == status['reportSubscriptions']
    asyncio.run(run())


@pytest.mark.parametrize('gate',['manual','illegal','stop-report','pause','readonly','edit','native-next',
                                'manual-owner','uncertain','steer','reserved','sent','held','backoff'])
def test_next_never_reveals_blocked_rows(gate):
    async def run():
        s,item = setup()
        if gate == 'manual': item['queueLockManual']=True
        elif gate == 'illegal': item.update(queueLockSource='illegal',queueLockIllegalBatch='batch')
        elif gate == 'stop-report': item['queueLockAutoReport']=True
        elif gate == 'pause': s.queue_paused=True
        elif gate == 'readonly': s.readonly_session=True
        elif gate == 'edit': worker.acquire_queue_edit_lock(s,item['id'],'editor')
        elif gate == 'native-next': s.adapter_config['native_control_next']=True
        elif gate == 'manual-owner':
            w=worker.Worker(worker_id='w',session_id=s.id,adapter=CodexAdapter(),status='running',
                            process=SimpleNamespace(returncode=None),native_control_mode='manual')
            worker.workers[w.worker_id]=w
        elif gate == 'uncertain': item['selfConsumeUncertain']=True
        elif gate == 'steer': item['queueSteerPending']=True
        elif gate == 'reserved': item['deliveryState']='reserved'
        elif gate == 'sent': item['deliveryState']='sent_to_cli'
        elif gate == 'held': item['forceSendHeld']=True
        elif gate == 'backoff': item['nextAttemptAt']=time.time()+60
        before=copy.deepcopy(s.queue_pending)
        assert await self_queue.report_next(s.id) == {'ok':True,'report':None}
        assert s.queue_pending == before
    asyncio.run(run())


def test_next_selects_visible_child_report_only_in_current_queue_order():
    async def run():
        s,item=setup()
        hidden={**item,'id':'q_hidden','queueItemId':'q_hidden','queueLockManual':True,'result':'HIDDEN'}
        s.queue_pending=[{'id':'q_task','type':'task','text':'task','source':'user'},
                         {'id':'q_notice','type':'notice','result':'notice'},
                         {'id':'q_qq','type':'qq','text':'channel'}, hidden,item]
        assert (await self_queue.report_next(s.id))['report']['queueItemId']=='q_real'
        s.queue_pending.remove(item)  # simulates existing lifecycle, not new read behavior
        assert await self_queue.report_next(s.id)=={'ok':True,'report':None}
        s.queue_delivery_ledger['q_real']={'deliveryState':'sent_to_cli'}
        assert await self_queue.report_next(s.id)=={'ok':True,'report':None}
    asyncio.run(run())


def test_legacy_existing_identity_alias_is_not_written_and_no_id_is_skipped():
    async def run():
        s,item=setup()
        item.pop('queueItemId')
        before=copy.deepcopy(item)
        assert (await self_queue.report_next(s.id))['report']['queueItemId']=='q_real'
        assert item == before
        item.pop('id')
        before=copy.deepcopy(item)
        assert await self_queue.report_next(s.id)=={'ok':True,'report':None}
        assert item == before
    asyncio.run(run())


def test_missing_caller_and_subscription_pause_is_configuration_not_relock():
    async def run():
        assert await self_queue.report_next('ses-missing')=={'ok':False,'error':'caller_unavailable'}
        s,item=setup(); s.agent_reports_paused=True
        assert (await self_queue.report_status(s.id))['agentReportsPaused'] is True
        # Actual row lock owns visibility; the future-report flag is not a new lock.
        assert (await self_queue.report_next(s.id))['report']['queueItemId']==item['id']
    asyncio.run(run())


def test_http_own_identity_gate_rejects_body_session_override():
    async def run():
        s,_=setup()
        other,_=setup()
        transport=httpx.ASGITransport(app=server.app,client=('127.0.0.1',12345))
        async with httpx.AsyncClient(transport=transport,base_url='http://local') as client:
            headers={'X-Pan-Queue-Caller':s.id}
            for op in ('report-status','report-next'):
                assert (await client.post('/api/self-queue/'+op,headers=headers,json={})).json()['ok']
                for denied in ({},{**headers,'Origin':'https://browser'}, {**headers,'Sec-Fetch-Site':'same-origin'},
                               {'X-Pan-Queue-Caller':'ses-missing'}):
                    assert (await client.post('/api/self-queue/'+op,headers=denied,json={})).json()['error']=='caller_unavailable'
                assert (await client.post('/api/self-queue/'+op,headers=headers,json={'sessionId':other.id})).json()['error']=='invalid_request'
                assert (await client.post('/api/self-queue/'+op,headers=headers,json={'itemId':'q_real'})).json()['error']=='invalid_request'
    asyncio.run(run())


@pytest.mark.parametrize('tool',[mcp.report_status,mcp.report_next])
def test_mcp_remote_or_unbound_callers_never_borrow_identity(monkeypatch,tool):
    monkeypatch.setattr(mcp,'_terminal_transport','sse')
    monkeypatch.setenv('PAN_AGENT_SESSION_ID','ses-own')
    monkeypatch.setattr(mcp,'_pan_api_url','http://127.0.0.1:51999')
    assert tool()=={'ok':False,'error':'caller_unavailable'}
    monkeypatch.setattr(mcp,'_terminal_transport','stdio')
    monkeypatch.delenv('PAN_AGENT_SESSION_ID')
    assert tool()=={'ok':False,'error':'caller_unavailable'}


def test_native_running_other_row_edit_lease_hides_report():
    async def run():
        s,item=setup()
        other={'id':'q_other','queueItemId':'q_other','type':'task','text':'other','source':'user'}
        s.queue_pending.append(other)
        w=worker.Worker(worker_id='w',session_id=s.id,adapter=CodexAdapter(),status='running',
                        process=SimpleNamespace(returncode=None))
        worker.workers[w.worker_id]=w
        worker.acquire_queue_edit_lock(s,other['id'],'editor')
        assert await self_queue.report_next(s.id)=={'ok':True,'report':None}
    asyncio.run(run())


def test_real_http_mcp_reports_remain_read_only(monkeypatch,record_property):
    import os
    import socket
    import threading
    import uvicorn
    from fastapi import FastAPI
    from mcp.shared.memory import create_connected_server_and_client_session

    s,item=setup()
    disk=sess._path(s.id).read_bytes()
    monkeypatch.setenv('PAN_AGENT_SESSION_ID',s.id)
    monkeypatch.setattr(mcp,'_terminal_transport','stdio')
    app=FastAPI()
    app.add_api_route('/api/self-queue/{operation}',server.api_self_queue,methods=['POST'])
    sock=socket.socket(); sock.bind(('127.0.0.1',0))
    port=sock.getsockname()[1]
    assert port not in (8767,8768)
    record_property('owned_http_port',port); record_property('owned_http_pid',os.getpid())
    monkeypatch.setattr(mcp,'_pan_api_url',f'http://127.0.0.1:{port}')
    http=uvicorn.Server(uvicorn.Config(app,lifespan='off',log_level='error',ws='none'))
    thread=threading.Thread(target=http.run,kwargs={'sockets':[sock]},daemon=True)
    thread.start()
    try:
        deadline=time.monotonic()+5
        while not http.started and thread.is_alive() and time.monotonic()<deadline: thread.join(.01)
        assert http.started
        async def exercise():
            async with create_connected_server_and_client_session(mcp.mcp) as protocol:
                tools={t.name:t for t in (await protocol.list_tools()).tools}
                assert 'report_status' in tools and 'report_next' in tools and 'report_ack' not in tools
                for name in ('report_status','report_next'):
                    assert tools[name].inputSchema.get('properties',{})=={}
                async def call(name):
                    result=await protocol.call_tool(name,{})
                    assert not result.isError
                    return json.loads(next(c.text for c in result.content if c.type=='text'))
                status=await call('report_status')
                assert status['sessionId']==s.id
                first=await call('report_next')
                assert first==await call('report_next')
                assert first['report']['queueItemId']==item['queueItemId']
                assert sess._path(s.id).read_bytes()==disk
                item['queueLockManual']=True
                assert await call('report_next')=={'ok':True,'report':None}
        asyncio.run(exercise())
    finally:
        http.should_exit=True; thread.join(5); sock.close()
        assert not thread.is_alive()
    with socket.socket() as probe: assert probe.connect_ex(('127.0.0.1',port)) != 0
