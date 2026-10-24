"""Real TCP and stdio MCP through production APIs with explicit synthetic ports."""
import asyncio
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
import urllib.error
import psutil
import pytest
from packages.jobs.window import command_window_options

ROOT=Path(__file__).resolve().parents[1]


def run_http_mcp(tmp_path,*,browser=False,no_answer=False):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        port=sock.getsockname()[1]
    assert port not in {8767,8768}
    data=tmp_path/'profile'/'data'
    (data/'workdirs'/'owner').mkdir(parents=True)
    (data/'config.json').write_text(json.dumps({'port':port,'memory':{'enabled':False},'scheduler':{'enabled':False},
        'plugin_manifests':[str(ROOT/'manifest.json'),str(ROOT/'packages/mcp/manifest.json')]}),encoding='utf-8')
    env={k:v for k,v in os.environ.items() if not k.upper().startswith('PAN_')}
    if os.environ.get('PAN_TEST_CHROMIUM_EXECUTABLE'):
        env['PAN_TEST_CHROMIUM_EXECUTABLE']=os.environ['PAN_TEST_CHROMIUM_EXECUTABLE']
    # fakeCLI must not consult the developer/runner's native provider history.
    provider_home = tmp_path / 'provider-home'
    provider_home.mkdir()
    env.update(USERPROFILE=str(provider_home), HOME=str(provider_home))
    env.update(PYTHONIOENCODING='utf-8',PAN_TEST_INTERACTION_TOKEN=secrets.token_hex(32),
        PAN_TEST_INBOX_NONCE=secrets.token_hex(16),PAN_E2E_FAKE_CLI=str(ROOT/'tests/support/fake_stream_cli.py'),
        PAN_E2E_FAKE_LOG=str(data/'fake-cli.jsonl'),PAN_TEST_DISABLE_QUEUE_WATCHDOG='1')
    base=f'http://127.0.0.1:{port}'
    def call(method,path,body=None,*,user=False,caller=None):
        headers={'Content-Type':'application/json'}
        if caller:
            headers['X-Pan-Queue-Caller']=caller
        if user:
            headers['Authorization']='Bearer '+env['PAN_TEST_INTERACTION_TOKEN']
        request=urllib.request.Request(base+path,data=json.dumps(body).encode() if body is not None else None,method=method,headers=headers)
        with urllib.request.urlopen(request,timeout=15) as response:
            return json.load(response)
    log=(tmp_path/'server.log').open('w',encoding='utf-8')
    proc=subprocess.Popen([sys.executable,'-m','tests.support.interaction_card_http_server','--port',str(port),
        '--data-root',str(data)],cwd=ROOT,env=env,stdout=log,stderr=log,**command_window_options())
    try:
        deadline=time.monotonic()+15
        while True:
            try:
                ident=call('GET','/__test/inbox-identity')
                break
            except OSError:
                assert time.monotonic()<deadline,(tmp_path/'server.log').read_text(encoding='utf-8')
                time.sleep(.1)
        assert ident['nonce']==env['PAN_TEST_INBOX_NONCE'] and Path(ident['cwd']).resolve()==ROOT
        actual=psutil.Process(ident['pid'])
        assert proc.pid==actual.pid or proc.pid in {p.pid for p in actual.parents()}
        assert 'tests.support.interaction_card_http_server' in ' '.join(actual.cmdline())
        assert any(c.status=='LISTEN' and c.laddr.port==port for c in actual.net_connections(kind='tcp'))
        assert all(Path(p).is_relative_to(data) for p in ident['roots'].values())
        ports=call('GET','/__test/interaction-identity')
        assert Path(ports['cardPath']).is_relative_to(data) and Path(ports['receiptPath']).is_relative_to(data)
        assert ports['auth']==ports['trigger']=='synthetic-test-only'
        owner=call('POST','/api/sessions',{'name':'interaction-owner','adapter':'cbc','workdir':str(data/'workdirs/owner'),'mcpEnabled':False})
        assert 'id' in owner,owner
        assert call('POST','/__test/interaction-trigger',{'ownerSessionId':owner['id']})['ok']
        async def mcp_create():
            from mcp import ClientSession,StdioServerParameters
            from mcp.client.stdio import stdio_client
            params=StdioServerParameters(command=sys.executable,args=['-m','packages.mcp.server'],cwd=str(ROOT),
                env={**env,'PAN_API_URL':base,'PAN_AGENT_SESSION_ID':owner['id']})
            async with stdio_client(params) as (read,write):
                async with ClientSession(read,write) as client:
                    await client.initialize()
                    assert 'interaction_card' in [t.name for t in (await client.list_tools()).tools]
                    result=await client.call_tool('interaction_card',{'operation':'create','recipient_id':'test-user','request_key':'once',
                        'card':{'title':'Backend Card','body':'Question','answerInput':{},'choices':[{'id':'go','label':'Go',
                        'triggerRef':{'id':'synthetic-trigger','revision':1}}],'notification':{'summary':'metadata only'}}})
                    assert not result.isError
                    created=json.loads(result.content[0].text)
                    assert 'card' in created,created
                    card=created['card']
                    rejected=await client.call_tool('interaction_card',{'operation':'respond','card_id':card['id']})
                    assert json.loads(rejected.content[0].text).get('error')
                    return card
        card=asyncio.run(mcp_create())
        with pytest.raises(urllib.error.HTTPError) as unauthorized:
            call('POST',f"/api/user-inbox/{card['id']}/respond",{'requestId':'r','expectedRevision':0,'choiceId':'go'})
        assert unauthorized.value.code==401
        assert call('GET','/api/user-inbox',user=True)['cards'][0]['response'] is None
        payload={'requestId':'response','expectedRevision':0,'answer':'user answer','choiceId':'go'}
        done=call('POST',f"/api/user-inbox/{card['id']}/respond",payload,user=True)['card']
        assert done['status']=='responded' and done['delivery']['state']=='accepted'
        assert call('POST',f"/api/user-inbox/{card['id']}/respond",payload,user=True)['card']==done
        with sqlite3.connect(ports['receiptPath']) as db:
            assert db.execute('SELECT count(*) FROM receipts').fetchone()[0]==1
            event=json.loads(db.execute('SELECT event FROM receipts').fetchone()[0])
        assert event['payload']['response']['answer']=='user answer'
        assert event['payload']['triggerRef']['id']=='synthetic-trigger'
        assert all(not path.read_text(encoding='utf-8').strip() for path in (data/'sessions').glob('*.history.jsonl'))  # Session creation can create an empty file; no Card enters context.
        artifact={'instance':ident,'interaction':ports,'ownerSessionId':owner['id'],'cardId':card['id'],
            'eventId':event['id'],'receiptId':done['delivery']['receiptId'],'mcpProducerOnly':True,'oneResponse':True}
        (tmp_path/'interaction-evidence.json').write_text(json.dumps(artifact,indent=2),encoding='utf-8')
        if no_answer:
            result=subprocess.run(['node',str(ROOT/'packages/web/e2e/card-no-answer.e2e.mjs'),base,
                env['PAN_TEST_INTERACTION_TOKEN'],owner['id'],str(tmp_path/'browser'),sys.executable],
                cwd=ROOT/'packages/web',env={**env, 'PAN_TEST_JOB_KEY': (data/'automation/user.key').read_text(encoding='utf-8').strip()},
                capture_output=True,text=True,encoding='utf-8',timeout=100)
            (tmp_path/'browser-run.log').write_text(result.stdout+'\n'+result.stderr,encoding='utf-8')
            assert result.returncode==0,result.stdout+'\n'+result.stderr
            with sqlite3.connect(ports['cardPath']) as db:
                # Only the pre-existing answered baseline Card sends a notice.
                assert db.execute('SELECT count(*) FROM response_outbox').fetchone()[0]==1
                assert db.execute('SELECT count(*) FROM rejection_outbox').fetchone()[0]==0
            with sqlite3.connect(ports['receiptPath']) as db:
                assert db.execute('SELECT count(*) FROM receipts').fetchone()[0]==1
            cold=call('POST','/api/interaction-card-tools/create',{
                'recipientId':'test-user','requestKey':'cold-restart',
                'card':{'title':'Cold restart','body':'Terminal must survive'}},caller=owner['id'])['card']
            terminal=call('POST','/api/interaction-card-tools/no_answer_needed',{
                'cardId':cold['id'],'expectedRevision':0},caller=owner['id'])['card']
            first_pid=proc.pid
            assert call('POST','/__test/inbox-exit',{})['ok']
            proc.wait(timeout=20)
            assert proc.returncode==0
            proc=subprocess.Popen([sys.executable,'-m','tests.support.interaction_card_http_server','--port',str(port),
                '--data-root',str(data)],cwd=ROOT,env=env,stdout=log,stderr=log,**command_window_options())
            deadline=time.monotonic()+15
            while True:
                try:
                    restarted=call('GET','/__test/inbox-identity')
                    break
                except OSError:
                    assert time.monotonic()<deadline
                    time.sleep(.1)
            assert restarted['nonce']==ident['nonce'] and restarted['pid']!=ident['pid']
            actual=psutil.Process(restarted['pid'])
            assert proc.pid==actual.pid or proc.pid in {p.pid for p in actual.parents()}
            assert call('GET',f"/api/user-inbox/{cold['id']}",user=True)['card']==terminal
            assert call('POST','/api/interaction-card-tools/list_pending',{},caller=owner['id'])['cards']==[]
            assert call('DELETE',f"/api/user-inbox/{cold['id']}",user=True)['deleted']
            (tmp_path/'cold-restart.json').write_text(json.dumps({
                'firstPid':first_pid,'restartedPid':proc.pid,'sameProfile':str(data),'terminal':terminal,
                'terminalPreserved':True,'pendingExcluded':True,'userDeleted':True},indent=2),encoding='utf-8')
        if browser:
            result=subprocess.run(['node',str(ROOT/'packages/web/e2e/locker-panel.e2e.mjs'),base,
                env['PAN_TEST_INTERACTION_TOKEN'],owner['id'],str(tmp_path/'browser')],
                cwd=ROOT/'packages/web',env={**env, 'PAN_TEST_JOB_KEY': (data/'automation/user.key').read_text(encoding='utf-8').strip()},
                capture_output=True,text=True,encoding='utf-8',timeout=90)
            (tmp_path/'browser-run.log').write_text(result.stdout+'\n'+result.stderr,encoding='utf-8')
            assert result.returncode==0,result.stdout+'\n'+result.stderr
            with sqlite3.connect(ports['cardPath']) as db:
                notice=db.execute('SELECT id,state,payload FROM rejection_outbox').fetchall()
                assert len(notice)==1 and notice[0][1:] == ('accepted',None)
            browser_evidence=json.loads((tmp_path/'browser/browser.json').read_text(encoding='utf-8'))
            browser_evidence['durableRejectionNoticeCount']=1
            browser_evidence['durableRejectionNoticeState']='accepted'
            (tmp_path/'browser/browser.json').write_text(json.dumps(browser_evidence,indent=2),encoding='utf-8')

    finally:
        shutdown = {'returncodeBeforeCleanup': proc.poll(), 'forcedTerminate': False}
        if proc.poll() is None:
            try:
                shutdown['exitResponse'] = call('POST','/__test/inbox-exit',{})
                proc.wait(timeout=20)
            except Exception as exc:
                shutdown['gracefulExitError'] = repr(exc)
                shutdown['forcedTerminate'] = True
                proc.terminate()
                proc.wait(timeout=10)
        shutdown['returncodeAfterCleanup'] = proc.poll()
        (tmp_path/'shutdown.json').write_text(json.dumps(shutdown,indent=2),encoding='utf-8')
        log.close()
    assert proc.returncode==0,json.dumps(shutdown)+'\n'+(tmp_path/'server.log').read_text(encoding='utf-8')
    # On Windows psutil can retain a terminated process handle and report
    # ``is_running()`` until the handle is reaped.  Treat only the explicit
    # terminal states as stopped; a live/running process still fails this
    # assertion and remains a cleanup defect.
    try:
        status = actual.status()
    except psutil.NoSuchProcess:
        status = psutil.STATUS_DEAD
    assert not actual.is_running() or status in {psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD, 'terminated'}
    with socket.socket() as probe:
        assert probe.connect_ex(('127.0.0.1',port))!=0
    (tmp_path/'cleanup.json').write_text(json.dumps({'port':port,'serverExited':True,'listenerClosed':True}),encoding='utf-8')


def test_http_mcp_user_response_and_directed_trigger(tmp_path):
    run_http_mcp(tmp_path)


def test_no_answer_http_stdio_production_browser(tmp_path):
    run_http_mcp(tmp_path,no_answer=True)
