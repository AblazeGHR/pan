"""Owned product HTTP + stdio MCP + deterministic CLI report handoff."""
import asyncio
import json
import os
from pathlib import Path
import subprocess

import psutil

from tests import test_real_queue_http_e2e as base
from tests.test_session_error_log_e2e import FAKE_CLI

OUT=Path('audit/terminal-report/http')


def test_native_terminal_report_survives_http_mcp_handoff_and_cold_reload(tmp_path,monkeypatch):
    assert base.PORT not in {8767,8768}, 'Use run_tests_isolated.py'
    monkeypatch.setattr(base,'FAKE_CLI',FAKE_CLI)
    rt=base._PanRuntime(tmp_path/'data');rt.data_root.mkdir()
    (rt.data_root/'config.json').write_text(json.dumps({'scheduler':{'enabled':False},
        'memory':{'enabled':False},'worker':{'idle_sec':300}}),encoding='utf-8')
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
        asyncio.run(mcp_assign(manager['id'],child['id']))
        def report_ready():
            rows=stored(manager['id']).get('queue_pending',[])
            return rows[0] if rows else None
        report=base._wait_for(report_ready,label='durable native error report')
        native='Selected model is at capacity. Please try a different model.'
        assert report['status']=='error' and report['errorText']==native
        assert report['errorCode']=='serverOverloaded' and report['errorSource']=='codex_error_payload'
        assert report['lastMessage']==report['result']=='Synthetic assistant work summary; not an error diagnosis.'
        assert stored(child['id'])['last_result']['reportDiagnostic']['errorText']==native
        # Cold host reopens the persisted report without needing the error JSONL.
        capture();rt.stop()
        log=rt.data_root/'logs/session-errors.jsonl'
        assert log.exists()
        log.replace(log.with_suffix('.saved'))  # Owned log absent at cold read.
        rt.start();capture()
        reloaded=rt.request('GET',f"/api/sessions/{manager['id']}/queue")['items'][0]
        assert reloaded['meta']['status']=='error'
        assert stored(manager['id'])['queue_pending'][0]['errorText']==native
        assert rt.request('GET',f"/api/sessions/{child['id']}")['lastResult']['reportDiagnostic']['errorText']==native
        rt.request('PATCH',f"/api/sessions/{manager['id']}/queue/paused",json={'paused':False})
        def delivered():
            rows=[json.loads(line) for line in rt.log_path.read_text(encoding='utf-8').splitlines()]
            return next((r['text'] for r in rows if r.get('kind')=='received' and native in r.get('text','')),None)
        text=base._wait_for(delivered,label='real fakeCLI consumes manager report')
        assert 'status: error' in text and 'errorCode: serverOverloaded' in text
        assert 'lastMessageRole: assistant' in text
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
