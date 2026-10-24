from tests.support.authorization import authenticate_user_requests
"""Owned product HTTP + stdio MCP + deterministic CLI report handoff."""
import asyncio
import json
import os
from pathlib import Path
import subprocess

import psutil

from tests import test_real_queue_http_e2e as base
from tests.test_session_error_log_e2e import FAKE_CLI

OUT=Path('audit/eta-report-integration/http')


def test_trusted_locker_and_error_report_share_product_runtime(tmp_path,monkeypatch):
    assert base.PORT not in {8767,8768}, 'Use run_tests_isolated.py'
    monkeypatch.setattr(base,'FAKE_CLI',FAKE_CLI)
    monkeypatch.setattr(base,'SERVER_LAUNCHER',Path(__file__).parent/'support/job_inbox_http_server.py')
    rt=base._PanRuntime(tmp_path/'data');authenticate_user_requests(rt);rt.data_root.mkdir()
    (rt.data_root/'config.json').write_text(json.dumps({'scheduler':{'enabled':True},
        'memory':{'enabled':False},'worker':{'idle_sec':300},
        'plugin_manifests':[str(base.ROOT/'manifest.json'),str(base.ROOT/'packages/mcp/manifest.json')]}),encoding='utf-8')
    OUT.mkdir(parents=True,exist_ok=True)
    evidence={'sourceSha':subprocess.check_output(['git','rev-parse','HEAD'],encoding='utf-8').strip(),
        'port':base.PORT,'fakeCLI':str(FAKE_CLI),'dataRoot':str(rt.data_root)}
    owned={}
    def capture():
        if rt.process and rt.process.poll() is None:
            p=psutil.Process(rt.process.pid)
            for child in [p,*p.children(recursive=True)]:
                try:owned[child.pid]=child.create_time()
                except psutil.Error:pass
    def stored(sid):
        return json.loads((rt.data_root/'sessions'/f'{sid}.json').read_text(encoding='utf-8'))
    async def mcp_assign(manager,child):
        from mcp import ClientSession,StdioServerParameters
        from mcp.client.stdio import stdio_client
        params=StdioServerParameters(command=os.sys.executable,args=['-m','packages.mcp.server'],
            cwd=str(base.ROOT),env={**os.environ,'PAN_API_URL':rt.base_url,'PAN_AGENT_SESSION_ID':manager})
        async with stdio_client(params) as (read,write):
            async with ClientSession(read,write) as client:
                await client.initialize()
                for tool,args in [('report_subscribe',{'session_id':child}),
                    ('agent_assign',{'session_id':child,'text':'err-capacity-report','task_id':'fixture-task'})]:
                    result=await client.call_tool(tool,args)
                    assert not result.isError
                    data=json.loads(result.content[0].text)
                    assert not data.get('error'),data
    try:
        rt.start();capture()
        manager=base._create(rt,'manager');child=base._create(rt,'child')
        rt.request('PATCH',f"/api/sessions/{manager['id']}/queue/paused",json={'paused':True})
        identity=rt.request('GET','/__test/job-inbox-identity')
        assert Path(identity['root'])==rt.data_root
        evidence['identity']=identity
        from tests.test_automation_core import action,trigger
        target=base._create(rt,'approved-pause-target')
        key=(rt.data_root/'automation/user.key').read_text(encoding='utf-8')
        auth={'Authorization':'Bearer '+rt.request('POST','/api/automation/login',json={'key':key})['token']}
        rt.request('POST','/api/actions/actions',json={'item':action([target['id']])})
        rt.request('POST','/api/automation/triggers',headers=auth,json={'item':{
            **trigger('selected'),'eventTypes':['pan.interaction.responded']},'expectedRevision':0})
        card=rt.request('POST','/api/interaction-cards',json={'ownerSessionId':manager['id'],
            'recipientId':'user','requestKey':'combined','card':{'title':'Combined approved Pause',
            'body':'FULL-SECRET','summaryBody':'combined summary','answerInput':{},
            'choices':[{'id':'pause','label':'Keep target running','triggerRef':{'id':'selected','revision':1}}]}})['card']
        browser=subprocess.run(['node','packages/web/e2e/locker-binding.mjs',rt.base_url,str(OUT.resolve()),card['id'],target['id']],
            cwd=base.ROOT,env={**os.environ,'PAN_TEST_JOB_KEY':key},capture_output=True,text=True,encoding='utf-8',timeout=90)
        (OUT/'browser.log').write_text(browser.stdout+'\n'+browser.stderr,encoding='utf-8')
        assert browser.returncode==0,browser.stderr
        def pause_done():
            runs=rt.request('GET','/api/automation')['runs']
            return next(iter(runs.values())) if len(runs)==1 and next(iter(runs.values()))['status']=='succeeded' else None
        run=base._wait_for(pause_done,label='trusted directed Pause completes alongside reporting')
        assert rt.request('GET',f"/api/sessions/{target['id']}/queue")['queuePaused']
        evidence.update(trustedRunId=run['id'],cardId=card['id'],trustedPause=True)
        asyncio.run(mcp_assign(manager['id'],child['id']))
        def report_ready():
            rows=stored(manager['id']).get('queue_pending',[])
            return next((row for row in rows if row.get('source')=='report'),None)
        report=base._wait_for(report_ready,label='durable native error report')
        native='Selected model is at capacity. Please try a different model.'
        assert report['status']=='error' and report['errorText']==native
        assert report['errorCode']=='serverOverloaded' and report['errorSource']=='codex_error_payload'
        assert report['lastMessage']==report['result']=='Synthetic assistant work summary; not an error diagnosis.'
        assert stored(child['id'])['last_result']['reportDiagnostic']['errorText']==native
        # Same terminal also retains ETA RuntimeIssue; report facts do not
        # replace observation/classification or reclassify a CBC event as Codex.
        rows=[json.loads(line) for line in (rt.data_root/'logs/session-errors.jsonl').read_text(encoding='utf-8').splitlines()]
        observation=next(row for row in rows if row.get('sessionId')==child['id'] and row.get('kind')=='terminal_error')
        assert observation['runtimeIssue']['issue']['classification']=='unknown'
        assert observation['errorText']==native
        assert observation['runtimeIssue']['issue']['correlation']['taskSeq']==report.get('taskSeq',observation['taskSeq'])
        evidence['runtimeIssueAlongsideReport']=observation['runtimeIssue']['issue']['issueId']
        queued=stored(manager['id'])['queue_pending']
        assert len(queued)==2
        evidence['originalQueueIds']=[row['id'] for row in queued]
        assert not rt.log_path.exists() or not any('errorCode: serverOverloaded' in row.get('text','') for row in
            [json.loads(line) for line in rt.log_path.read_text(encoding='utf-8').splitlines()])
        # Cold host reopens the persisted report without needing the error JSONL.
        capture();rt.stop()
        log=rt.data_root/'logs/session-errors.jsonl'
        assert log.exists()
        log.replace(log.with_suffix('.saved'))  # Owned log absent at cold read.
        rt.start();capture()
        reloaded=next(row for row in rt.request('GET',f"/api/sessions/{manager['id']}/queue")['items'] if row.get('meta',{}).get('status')=='error')
        assert reloaded['meta']['status']=='error'
        assert next(row for row in stored(manager['id'])['queue_pending'] if row.get('source')=='report')['errorText']==native
        assert rt.request('GET',f"/api/sessions/{child['id']}")['lastResult']['reportDiagnostic']['errorText']==native
        rt.request('PATCH',f"/api/sessions/{manager['id']}/queue/paused",json={'paused':False})
        def delivered():
            rows=[json.loads(line) for line in rt.log_path.read_text(encoding='utf-8').splitlines()]
            return next((r['text'] for r in rows if r.get('kind')=='received' and native in r.get('text','')),None)
        text=base._wait_for(delivered,label='real fakeCLI consumes manager report')
        assert 'status: error' in text and 'errorCode: serverOverloaded' in text
        assert 'lastMessageRole: assistant' in text
        assert len(rt.request('GET','/api/automation')['runs'])==1
        def all_notices():
            state=stored(manager['id'])
            return state if not state['queue_pending'] and set(evidence['originalQueueIds'])<=set(state['queue_delivery_ledger']) else None
        state=base._wait_for(all_notices,label='both original report and Locker Queue IDs handed off')
        history=rt.request('GET',f"/api/sessions/{manager['id']}/history?limit=100")['history']
        assert set(evidence['originalQueueIds'])<=set(qid for row in history for qid in row.get('queueItemIds',[]))
        received=[json.loads(line) for line in rt.log_path.read_text(encoding='utf-8').splitlines()]
        notice_text='\n'.join(row.get('text','') for row in received)
        assert notice_text.count('Interaction submitted: ')==1
        assert 'FULL-SECRET' not in notice_text
        evidence['originalIdsConsumed']=True
        evidence.update(status='error',nativeDiagnostic=True,coldReload=True,mcpSubscribeAssign=True,
            managerConsumed=True,queueItemId=report['id'])
        (OUT/'delivered.txt').write_text(text,encoding='utf-8')
    finally:
        capture();rt.stop()
        remaining=[]
        for pid,born in owned.items():
            try:
                p=psutil.Process(pid)
                if abs(p.create_time()-born)<.01 and p.is_running():remaining.append(p)
            except psutil.Error:pass
        for p in remaining:
            try:p.terminate()
            except psutil.Error:pass
        _,remaining=psutil.wait_procs(remaining,timeout=3)
        for p in remaining:
            try:p.kill()
            except psutil.Error:pass
        _,remaining=psutil.wait_procs(remaining,timeout=3)
        evidence.update(ownedPidBirths=owned,ownedProcessesRemaining=[p.pid for p in remaining],
            portReleased=base._port_is_free(base.PORT))
        (OUT/'evidence.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
        assert not remaining and evidence['portReleased']
