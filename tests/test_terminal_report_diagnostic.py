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
CBC=json.loads((Path(__file__).parent/'fixtures/cbc_quota_error_result.json').read_text(encoding='utf-8'))
CBC_EVENT=CBC['event']
CBC_TEXT=CBC_EVENT['errors'][0]


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
    # CBC/Claude-lineage failed terminals carry the provider text in ``errors``
    # and the structured facts in ``errors_info`` instead of ``error``.
    ({'type':'result','is_error':True,'errors':['native result error'],
      'errors_info':[{'status':429,'code':6004,'category':'quota',
                      'details':'native result error'}]},('native result error',6004,'cli.error')),
    ({'type':'result','is_error':True,'errors_info':[{'details':'details only'}]},
     ('details only',None,'cli.error')),
    ({'type':'result','is_error':True,'errors':['text only']},('text only',None,'cli.error')),
    ({'type':'result','is_error':True,'errors':['first','second'],
      'errors_info':[{'code':6004,'details':'first'}]},('first',6004,'cli.error')),
    ({'type':'result','is_error':True,'errors':['  '],
      'errors_info':[{'code':6004,'details':''}]},(None,6004,'cli.error')),
    ({'type':'result','is_error':True,
      'errors_info':[{'details':'native','opaque':{'password':'DO-NOT-COPY'}}]},
     ('native',None,'cli.error')),
    ({'type':'result','is_error':True,'errors_info':[{'status':429,'code':6004}]},
     (None,6004,'cli.error')),
    ({'type':'result','is_error':True,'errors_info':[{'category':'quota'}]},(None,None,None)),
    ({'type':'result','is_error':True,'errors':None,'errors_info':'not-a-list'},(None,None,None)),
    ({'type':'result','is_error':True,'errors':[],'errors_info':[]},(None,None,None)),
    # An explicit provider error object stays authoritative.
    ({'type':'result','is_error':True,'error':{'message':'explicit','code':7},
      'errors':['native result error'],'errors_info':[{'code':6004}]},('explicit',7,'cli.error')),
    # Only a failed result may use that envelope; a successful result or an
    # assistant entry that happens to carry ``errors`` is not a diagnosis.
    ({'type':'result','is_error':False,'errors':['not an error'],
      'errors_info':[{'status':429,'code':6004}]},(None,None,None)),
    ({'type':'assistant','message':{'role':'assistant','content':[]},
      'errors':['not an error'],'errors_info':[{'status':429,'code':6004}]},(None,None,None)),
    ({'is_error':True,'errors':['no event type'],
      'errors_info':[{'status':429,'code':6004}]},(None,None,None)),
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


def test_cbc_result_errors_reach_terminal_and_report():
    """The provider's own failed-terminal diagnosis is visible without reading prose."""
    child,manager,_=_setup_managed_pair()
    try:
        _run_stream_worker(child,[
            _event_line(_assistant_event('失败前真实回答')),
            _event_line(_assistant_event(CBC_TEXT)),
            _event_line(CBC_EVENT),
        ],seq=11)
        terminal=next(row for row in child.history if row.get('kind')=='terminal')
        assert terminal['terminal']['status']=='error'
        assert terminal['terminal']['error']=={
            'source':'cli.error','code':6004,'message':CBC_TEXT,'truncated':False,
        }
        # The provider's assistant prose is preserved verbatim: nothing is
        # deleted and no second copy is synthesised for the failed terminal.
        assert [row['content'] for row in child.history if row.get('role')=='assistant']==[
            '失败前真实回答',CBC_TEXT]
        # ``result`` stayed empty: the diagnosis comes from ``errors``, not the
        # compatibility result field.
        assert child.last_result['result']==''
        assert child.last_result['historyTerminalId']==terminal['messageId']
        report=manager.queue_pending[0]
        assert report['status']=='error'
        assert report['errorText']==CBC_TEXT
        assert report['errorCode']==6004
        assert report['errorSource']=='cli.error'
        # Refresh reads the durable history row, so it renders the same reason.
        session._cache.pop(child.id,None)
        reloaded=session.get(child.id)
        row=next(item for item in reloaded.history if item.get('kind')=='terminal')
        assert row['terminal']['error']==terminal['terminal']['error']
    finally:_cleanup()


def test_cbc_terminal_diagnosis_is_native_never_last_assistant_prose():
    child,manager,_=_setup_managed_pair()
    try:
        _run_stream_worker(child,[
            _event_line(_assistant_event('失败前真实回答，正文里提到 429 但这不是诊断')),
            _event_line(CBC_EVENT),
        ],seq=11)
        terminal=next(row for row in child.history if row.get('kind')=='terminal')
        assert terminal['terminal']['error']['message']==CBC_TEXT
        assert terminal['terminal']['error']['message']!='失败前真实回答，正文里提到 429 但这不是诊断'
        assert [row['content'] for row in child.history if row.get('role')=='assistant']==[
            '失败前真实回答，正文里提到 429 但这不是诊断']
        assert manager.queue_pending[0]['errorText']==CBC_TEXT
    finally:_cleanup()


def test_cbc_failed_terminal_without_native_error_stays_undiagnosed():
    """No native reason means no prose guess and no invented code."""
    child,manager,_=_setup_managed_pair()
    try:
        _run_stream_worker(child,[
            _event_line(_assistant_event('429 您的使用量已超出频率限制，将在 2026-11-13 10:43:39 UTC+8 重置。')),
            _event_line({'type':'result','subtype':'error_during_execution','is_error':True}),
        ],seq=11)
        terminal=next(row for row in child.history if row.get('kind')=='terminal')
        assert terminal['terminal']['status']=='error'
        assert terminal['terminal']['error']=={
            'source':'worker.stream.result','code':None,'message':'','truncated':False,
        }
        report=manager.queue_pending[0]
        assert report['status']=='error'
        assert report['errorText'] is None and report['errorCode'] is None
        assert 'no native diagnostics available' in worker._format_report_batch([report])
    finally:_cleanup()


def test_cbc_native_result_error_feeds_terminal_and_runtime_issue(monkeypatch):
    """One native raw input drives both the visible reason and the issue type."""
    calls=[]
    async def capture(**kwargs):
        calls.append(kwargs)
    monkeypatch.setattr(errors,'record_session_error_async',capture)
    child,manager,_=_setup_managed_pair()
    try:
        _run_stream_worker(child,[
            _event_line(_assistant_event(CBC_TEXT)),
            _event_line(CBC_EVENT),
        ],seq=11)
        samples=[item for item in calls if item.get('cli_origin')=='worker.stream.result']
        assert len(samples)==1,calls
        sample=samples[0]
        assert sample['error_text']==CBC_TEXT
        assert sample['error_text_source']=='cli.error'
        issue=sample['runtime_issue']['issue']
        assert issue['classification']=='cbc_rate_limited'
        assert issue['confidence']=='structured'
        assert issue['adapter']=='cbc'
        assert issue['observation']['nativeError']=={
            'status':429,'code':6004,'category':'quota'}
        terminal=next(row for row in child.history if row.get('kind')=='terminal')
        assert terminal['terminal']['error']['message']==CBC_TEXT
        assert terminal['terminal']['error']['code']==6004
        assert terminal['terminal']['error']['source']=='cli.error'
        assert manager.queue_pending[0]['status']=='error'
    finally:_cleanup()


def test_done_result_mentioning_429_is_not_a_failure():
    child,manager,_=_setup_managed_pair()
    try:
        _run_stream_worker(child,[
            _event_line(_assistant_event('上一轮触发过 429，已经恢复')),
            _event_line({'type':'result','subtype':'success','is_error':False,'result':'已完成'}),
        ],seq=8)
        report=manager.queue_pending[0]
        assert report['status']=='done' and 'errorText' not in report
        assert 'reportDiagnostic' not in child.last_result
        assert all(row.get('terminal',{}).get('status')=='done'
                   for row in child.history if row.get('kind')=='terminal')
    finally:_cleanup()
