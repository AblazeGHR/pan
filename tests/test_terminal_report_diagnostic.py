"""Native terminal facts must survive the assistant-result compatibility field."""
import json
import asyncio
import copy
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from packages.core import worker, session, session_error_log as errors
from packages.core.adapters import CbcAdapter
from tests.test_report_error_context import (
    _setup_managed_pair, _run_stream_worker, _event_line, _assistant_event, _cleanup,
    _no_real_recovery_spawn,
)

FIXTURE=json.loads((Path(__file__).parent/'fixtures/terminal_capacity_report.json').read_text(encoding='utf-8'))


@pytest.fixture(autouse=True)
def fresh_queue_locks():
    # Each synchronous test owns its asyncio.run loop, including contended
    # Steer receipt settlement; do not reuse a lock bound to the prior loop.
    worker._queue_locks.clear()
    yield
    worker._queue_locks.clear()


@pytest.mark.parametrize('adapter',['cbc','codex'])
def test_native_error_plus_assistant_result_persists_and_renders(monkeypatch,adapter):
    child,manager,_=_setup_managed_pair()
    if adapter=='codex':
        from packages.core.adapters import CodexAdapter
        from tests import test_report_error_context as harness
        monkeypatch.setattr(harness,'CbcAdapter',CodexAdapter)
        # Exercise the real Codex stream adapter, without native accounting IO.
        monkeypatch.setattr(worker,'_schedule_usage_enrichment',lambda sid:None)
    event=FIXTURE['event']
    try:
        _run_stream_worker(child,[_event_line(_assistant_event(event['result'])),_event_line(event)],seq=8)
        report=manager.queue_pending[0]
        assert report['status']=='error'
        assert report['errorText']==event['error']['message']
        assert report['errorCode']=='serverOverloaded'
        assert report['errorSource']=='codex_error_payload'
        assert report['lastMessage']==event['result']
        assert child.last_result['reportDiagnostic']['errorText']==report['errorText']
        assert child.terminal_results[-1]['reportDiagnostic']==child.last_result['reportDiagnostic']
        text=worker._format_report_batch([report])
        assert 'status: error' in text and 'errorCode: serverOverloaded' in text
        assert event['error']['message'] in text
        session._cache.pop(manager.id,None)
        reloaded=session.get(manager.id,load_history=False)
        assert reloaded.queue_pending[0]['errorText']==report['errorText']
    finally:
        _cleanup()


@pytest.mark.parametrize('event,expected',[
    ({'error_text':'native text'},('native text',None,'cli.error_text')),
    ({'error':{'message':'native','code':429}},('native',429,'cli.error')),
    ({'error':{'code':'native_code','opaque':{'password':'DO-NOT-COPY'}}},(None,'native_code','cli.error')),
    ({'result':'Selected model is at capacity. Please try a different model.'},(None,None,None)),
    ({'error':{}},(None,None,None)),
    ({},(None,None,None)),
])
def test_only_native_fields_no_classification_or_opaque_dump(event,expected):
    fields=errors.terminal_report_fields(event)
    assert (fields['errorText'],fields['errorCode'],fields['errorSource'])==expected
    assert 'DO-NOT-COPY' not in json.dumps(fields)


def test_redacts_before_truncating_without_raw_payload_leak():
    secret='sk-'+'A'*40
    event={'error':{'message':'Authorization: Bearer '+secret+'\n'+('x'*4000)+'\napi_key='+secret,
                    'codexErrorInfo':{'secret':'DO-NOT-COPY'}},'authorization':'RAW-SECRET',
           'raw':{'password':'RAW-PASSWORD'}}
    fields=errors.terminal_report_fields(event)
    assert len(fields['errorText'])<=1200 and fields['errorTextTruncated']
    assert 'redacted' in fields['errorText']
    assert secret not in json.dumps(fields)
    assert 'RAW-' not in json.dumps(fields) and fields['errorCode'] is None


@pytest.mark.parametrize('message',[
    'Bearer PRIVATE-VALUE', 'Authorization: Basic PRIVATE-VALUE',
    '{"access_token":"PRIVATE-VALUE"}', 'password=PRIVATE-VALUE',
    'https://name:PRIVATE-VALUE@example.invalid/path',
])
def test_shared_log_and_report_normalization_redacts_credentials(message):
    assert 'PRIVATE-VALUE' not in errors.sanitize(message)
    assert 'PRIVATE-VALUE' not in errors.terminal_report_fields({'error_text':message})['errorText']


def test_next_task_empty_diagnosis_never_reuses_previous_error():
    child,manager,_=_setup_managed_pair()
    try:
        _run_stream_worker(child,[_event_line(FIXTURE['event'])],seq=8)
        _run_stream_worker(child,[_event_line({'type':'result','is_error':True,'result':''})],seq=9)
        first,second=manager.queue_pending
        assert first['errorCode']=='serverOverloaded'
        assert second['status']=='error'
        assert second['errorText'] is None and second['errorCode'] is None
        assert second['lastMessage'] is None
        assert 'no native diagnostics available' in worker._format_report_batch([second])
    finally:_cleanup()


def test_freeze_before_await_concurrent_sessions_and_late_generation(monkeypatch):
    async def run():
        entered=asyncio.Event();release=asyncio.Event()
        async def delayed_log(*args):
            entered.set();await release.wait()
        monkeypatch.setattr(worker,'_record_terminal_error_sample',delayed_log)
        def make(sid,seq):
            s=session.Session(id=sid,name=sid);session._cache[sid]=s
            w=worker.Worker(worker_id='w-'+sid,session_id=sid,adapter=CbcAdapter(),status='running',pending_signal=asyncio.Queue())
            w._current_seq=seq;w._execution_seq=2
            w._current_task_last_assistant_seq=seq;w._current_task_last_assistant_text='original '+sid
            worker.workers[w.worker_id]=w
            return s,w
        s,w=make('first',8);other,ow=make('other',9)
        owner=worker._offline.owner(w)
        task=asyncio.create_task(worker._persist_terminal_state(w,s,'error','synthetic',expected_owner=owner,
            error_context={'cli_raw':copy.deepcopy(FIXTURE['event'])}))
        await entered.wait()
        w._current_task_last_assistant_text='LATE NEW ASSISTANT';w._current_task_last_assistant_seq=99
        second=asyncio.create_task(worker._persist_terminal_state(ow,other,'error','other',
            error_context={'cli_raw':{'error_text':'other native'}}))
        release.set();first,second=await asyncio.gather(task,second)
        assert first['reportDiagnostic']['lastMessage']=='original first'
        assert first['reportDiagnostic']['errorCode']=='serverOverloaded'
        assert second['reportDiagnostic']['errorText']=='other native'
        assert second['reportDiagnostic']['lastMessage']=='original other'
        # A late terminal after restart cannot attach error metadata to the new
        # execution. Existing owner/generation gate rejects it before mutation.
        w.generation+=1;w._terminal_handled=False
        before=copy.deepcopy(s.last_result)
        assert await worker._persist_terminal_state(w,s,'error','late',expected_owner=owner,
            error_context={'cli_raw':{'error_text':'LATE OLD ERROR'}}) is None
        assert s.last_result==before
    try:asyncio.run(run())
    finally:_cleanup()


def test_diagnostic_fields_do_not_change_delivery_identity():
    base={'type':'report','sessionId':'child','workerId':'w','status':'error','result':'synthetic','taskId':'t'}
    enriched={**base,**errors.terminal_report_fields(FIXTURE['event'])}
    assert worker._delivery_key(enriched)==worker._delivery_key(base)
    assert worker._delivery_key_matches(worker._delivery_key(base),enriched)


@pytest.mark.parametrize('status',['done','cancelled'])
def test_non_error_completion_does_not_publish_native_diagnostic(status):
    child,manager,_=_setup_managed_pair()
    try:
        event={**FIXTURE['event'],'is_error':False,'cancelled':status=='cancelled'}
        _run_stream_worker(child,[_event_line(event)],seq=8)
        report=manager.queue_pending[0]
        assert report['status']==status
        assert 'errorText' not in report and 'reportDiagnostic' not in child.last_result
    finally:_cleanup()


@pytest.mark.parametrize('mode',['batch','steer','force'])
def test_real_report_rendering_entrypoints(monkeypatch,mode):
    from tests.test_force_steer import live
    async def run():
        s,w,stdin=live()
        monkeypatch.setattr(worker,'auto_force_settings',lambda:{'report':True})
        monkeypatch.setattr(worker,'_wake_worker',AsyncMock())
        report={'id':'q-report','queueItemId':'q-report','revision':1,'type':'report','kind':'report',
            'source':'report','status':'error','result':'synthetic assistant','sessionId':'child',
            'taskId':'task','workerId':'child-worker',**errors.terminal_report_fields(FIXTURE['event'])}
        s.queue_pending.append(report)
        if mode=='batch':
            text=worker._queue_history_row([report])['content']
        else:
            def accept(payload):
                control=json.loads(payload)
                worker._route_steer_receipt(w,{'type':'pan.steer_receipt','requestId':control['requestId'],
                    'ok':True,'outcome':'accepted'})
            stdin.write.side_effect=accept
            if mode=='steer':
                assert await worker.steer_queue_item(s.id,report['id'],1) is None
            else:
                result=await worker._auto_force_item(s.id,report['id'],'report')
                assert result['status']=='accepted',result
            text=json.loads(stdin.write.call_args.args[0])['text']
        assert 'status: error' in text and 'errorCode: serverOverloaded' in text
        assert FIXTURE['event']['error']['message'] in text
    try:asyncio.run(run())
    finally:_cleanup()
