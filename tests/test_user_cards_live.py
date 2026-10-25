"""Owned random-port product HTTP + actual MCP stdio, no live profile/provider."""
import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess

import httpx


def test_real_http_stdio_locker_roundtrip_and_restart(tmp_path,monkeypatch):
    from tests import test_real_queue_http_e2e as h
    from tests.support.authorization import authenticate_user_requests
    root=Path(__file__).resolve().parents[1]
    out=root/'audit/reminding-cards'; out.mkdir(parents=True,exist_ok=True)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
    assert port not in {8767,8768}
    monkeypatch.setattr(h,'PORT',port)
    monkeypatch.setattr(h,'SERVER_LAUNCHER',root/'tests/support/job_inbox_http_server.py')
    rt=h._PanRuntime(tmp_path/'data'); authenticate_user_requests(rt)
    rt.data_root.mkdir()
    (rt.data_root/'config.json').write_text(json.dumps({'memory':{'enabled':False},'plugin_manifests':[
        str(root/'manifest.json'),str(root/'packages/mcp/manifest.json')]}),encoding='utf-8')
    evidence={'port':port,'dataRoot':str(rt.data_root),'realProvider':False}
    try:
        rt.start()
        evidence['identity']=rt.request('GET','/__test/job-inbox-identity')
        assert Path(evidence['identity']['sessions']).parent==rt.data_root
        key=(rt.data_root/'automation/user.key').read_text(encoding='utf-8')
        def login():
            return {'Authorization':'Bearer '+rt.request('POST','/api/automation/login',json={'key':key})['token']}
        auth=login()
        owner=rt.request('POST','/api/sessions',json={'name':'card-owner','adapter':'codex','mcpServers':[]})
        other=rt.request('POST','/api/sessions',json={'name':'other','adapter':'codex','mcpServers':[]})
        sid=owner['id']
        rt.request('PATCH',f'/api/sessions/{sid}/queue/paused',json={'paused':True})
        upload=rt.request('POST',f'/api/sessions/{sid}/attachments?filename=evidence.txt',content=b'owned evidence')
        aid=upload.get('attachmentId') or upload['storageFilename']
        def create(key,kind='question',attachments=None):
            return rt.request('POST','/api/reminding-cards',headers=auth,json=dict(ownerSessionId=sid,requestKey=key,
                kind=kind,body='Real user question '+key,attachments=attachments or []))['card']
        question=create('q',attachments=[{'id':aid,'name':'evidence.txt'}])
        task=create('t','task')
        evidence['created']=[question,task]
        async def mcp(sid,answer=False):
            from mcp import ClientSession,StdioServerParameters
            from mcp.client.stdio import stdio_client
            params=StdioServerParameters(command=os.sys.executable,args=['-m','packages.mcp.server'],cwd=str(root),
                env={**os.environ,'PAN_API_URL':rt.base_url,'PAN_AGENT_SESSION_ID':sid})
            async with stdio_client(params) as (read,write):
                async with ClientSession(read,write) as client:
                    await client.initialize()
                    async def call(**args):
                        result=await client.call_tool('reminding_card',args)
                        assert not result.isError,result
                        return json.loads(result.content[0].text)
                    pending=await call(operation='list_pending')
                    if not answer:
                        assert pending['cards']==[]
                        foreign=await call(operation='answer',card_id=question['id'],body='forged',expected_revision=0)
                        assert foreign['error']['code']=='not_found'
                        return
                    assert {c['id'] for c in pending['cards']}=={question['id'],task['id']}
                    assert next(c for c in pending['cards'] if c['id']==question['id'])['attachments'][0]['id']==aid
                    empty=await call(operation='answer',card_id=question['id'],body='',expected_revision=0)
                    assert empty['error']['code']=='invalid'
                    result=await call(operation='answer',card_id=question['id'],body='Real MCP answer',expected_revision=0)
                    assert result['card']['answeredAt'] and result['card']['createdAt']==question['createdAt']
                    duplicate=await call(operation='answer',card_id=question['id'],body='overwrite',expected_revision=1)
                    assert duplicate['error']['code']=='conflict'
                    assert (await call(operation='answer',card_id=task['id'],body='',expected_revision=0))['card']['answer']==''
                    assert (await call(operation='list_pending'))['cards']==[]
        asyncio.run(mcp(other['id']))
        asyncio.run(mcp(sid,True))
        cards=rt.request('GET','/api/reminding-cards',headers=auth)['cards']
        assert len(cards)==2 and all(c['status']=='answered' and not c['read'] for c in cards)
        locker=next(l for l in rt.request('GET','/api/user-lockers',headers=auth)['lockers'] if l['sessionId']==sid)
        assert locker['total']==2 and locker['unread']==2
        with httpx.Client(base_url=rt.base_url,trust_env=False) as client:
            path=f"/api/reminding-cards/{question['id']}/attachments/{aid}"
            assert client.get(path).status_code==401
            assert client.get(path,headers=auth).content==b'owned evidence'
            assert client.get(f"/api/reminding-cards/{task['id']}/attachments/{aid}",headers=auth).status_code==404
        browser=subprocess.run(['node','packages/web/e2e/user-cards.mjs',rt.base_url,str(out),question['id']],
            cwd=root,env={**os.environ,'PAN_TEST_JOB_KEY':key},capture_output=True,text=True,encoding='utf-8',timeout=90)
        (out/'visual.log').write_text(browser.stdout+'\n'+browser.stderr,encoding='utf-8')
        assert browser.returncode==0,browser.stderr
        pending=create('restart')
        before=rt.request('GET',f'/api/sessions/{sid}/queue')['items']
        assert len(before)==4 and all(i['kind']=='report' for i in before)
        assert not rt.fake_records(), 'Paused Session must not spawn a Worker'
        rt.stop(); rt.start(); auth=login()
        after=rt.request('GET',f'/api/sessions/{sid}/queue')['items']
        assert len(after)==len(before), 'Restart must not duplicate immediate notices'
        assert not rt.fake_records()
        rt.request('DELETE','/api/reminding-cards/'+pending['id'],headers=auth)
        rt.request('PUT','/api/reminding-cards/'+question['id']+'/read',headers=auth,json={'read':True})
        locker=next(l for l in rt.request('GET','/api/user-lockers',headers=auth)['lockers'] if l['sessionId']==sid)
        assert locker['total']==2 and locker['unread']==1
        evidence.update(cards=cards,locker=locker,queueBeforeRestart=before,queueAfterRestart=after,providerRecords=rt.fake_records())
    finally:
        rt.stop()
        evidence['portReleased']=h._port_is_free(port)
        (out/'live.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
        if (rt.data_root/'pan.log').exists():
            (out/'live-server.log').write_text((rt.data_root/'pan.log').read_text(encoding='utf-8',errors='replace'),encoding='utf-8')
    assert evidence['portReleased']
