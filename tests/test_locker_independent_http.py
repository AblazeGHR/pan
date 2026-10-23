"""Owned random-port HTTP/MCP + actual fakeCLI notification handoff."""
import asyncio
import json
import os
import subprocess
import hashlib
from pathlib import Path

import httpx
import psutil

from tests import test_real_queue_http_e2e as h
from tests.test_automation_core import action, trigger

OUT = Path('audit/locker-binding')


def test_owned_notification_consumption_and_restart(tmp_path, monkeypatch):
    assert h.PORT not in {8767, 8768}
    monkeypatch.setattr(h, 'SERVER_LAUNCHER', Path(__file__).parent/'support/job_inbox_http_server.py')
    root = Path(__file__).resolve().parents[1]
    rt = h._PanRuntime(tmp_path/'data')
    rt.data_root.mkdir()
    (rt.data_root/'config.json').write_text(json.dumps({'scheduler': {'enabled': True},
        'memory': {'enabled': False}, 'plugin_manifests': [str(root/'manifest.json'),
        str(root/'packages/mcp/manifest.json')]}), encoding='utf-8')
    evidence = {'port': h.PORT, 'dataRoot': str(rt.data_root), 'realProvider': False}
    evidence['sourceSha']=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,encoding='utf-8').strip()
    evidence['productionIndexSha256']=hashlib.sha256((root/'packages/web/dist/index.html').read_bytes()).hexdigest()
    def stop():
        owned = []
        if rt.process and rt.process.poll() is None:
            parent = psutil.Process(rt.process.pid)
            owned = [parent, *parent.children(recursive=True)]
            for p in owned:
                try:
                    rt._owned_processes.append((p.pid, p.create_time(), ' '.join(p.cmdline())))
                except psutil.Error:
                    pass
        rt.stop()
        _, live = psutil.wait_procs(owned, timeout=5)
        assert not live, [(p.pid, p.status()) for p in live]
    try:
        rt.start()
        identity = rt.request('GET', '/__test/job-inbox-identity')
        assert Path(identity['root']) == rt.data_root
        assert Path(identity['sessions']).parent == rt.data_root
        assert Path(identity['config']).parent == rt.data_root
        assert Path(identity['inbox']).is_relative_to(rt.data_root)
        assert Path(identity['gatewayManifest']).is_relative_to(rt.data_root)
        assert Path(identity['fakeCLI']) == h.FAKE_CLI
        evidence['identity'] = identity
        owner = rt.request('POST', '/api/sessions', json={'name': 'review-source', 'adapter': 'cbc', 'mcpServers': [],
            'panAccess': {'restrictToManaged': True, 'canClaimUnmanaged': False}})
        target = rt.request('POST', '/api/sessions', json={'name': 'review-target', 'adapter': 'cbc', 'mcpServers': []})
        rt.request('PATCH', f"/api/sessions/{owner['id']}/queue/paused", json={'paused': True})
        assert 'error' not in h._spawn(rt, owner['id'])
        key = (rt.data_root/'automation/user.key').read_text(encoding='utf-8')
        auth = {'Authorization': 'Bearer '+rt.request('POST', '/api/automation/login', json={'key': key})['token']}
        rt.request('POST', '/api/actions/actions', json={'item': action([target['id']])})
        for tid in ('selected', 'unselected'):
            rt.request('POST', '/api/automation/triggers', headers=auth, json={'item': {
                **trigger(tid), 'eventTypes': ['pan.interaction.responded']}, 'expectedRevision': 0})
        async def produce():
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
            params = StdioServerParameters(command=os.sys.executable, args=['-m', 'packages.mcp.server'], cwd=str(root),
                env={**os.environ, 'PAN_API_URL': rt.base_url, 'PAN_AGENT_SESSION_ID': owner['id']})
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as client:
                    await client.initialize()
                    denied = await client.call_tool('agent_notify', {'target_session_id': target['id'], 'text': 'forbidden'})
                    denial = json.loads(denied.content[0].text)
                    assert denial.get('error', {}).get('code') == 'permission_denied', denial
                    evidence['directAgentNotifyDenied'] = True
                    forbidden = await client.call_tool('interaction_card', {'operation':'create','request_key':'forbidden',
                        'recipient_id':'user','card':{'title':'Denied','body':'body','choices':[{'id':'go','label':'Go',
                        'triggerRef':{'id':'selected'}}]}})
                    assert 'forbidden' in forbidden.content[0].text, forbidden
                    evidence['outOfScopeChoiceDenied'] = True
                    rt.request('POST','/api/claim',json={'managerId':owner['id'],'sessionId':target['id']})
                    cards = []
                    for req, recipient in [('submitted', 'user'), ('rejected', 'user'), ('foreign', 'unknown-recipient')]:
                        result = await client.call_tool('interaction_card', {'operation': 'create', 'request_key': req,
                            'recipient_id': recipient, 'card': {'title': 'Pause target', 'body': 'FULL-SECRET',
                            'summaryBody': 'S'*250, 'answerInput': {}, 'choices': [{'id': 'pause', 'label': 'Keep target running',
                            'triggerRef': {'id': 'selected', 'revision': 1}}]}})
                        assert not result.isError, result
                        cards.append(json.loads(result.content[0].text)['card'])
                    return cards
        submitted, rejected, foreign = asyncio.run(produce())
        with httpx.Client(base_url=rt.base_url, trust_env=False) as client:
            assert client.get('/api/user-lockers').status_code == 401
            assert client.get('/api/user-lockers', headers={'Authorization': 'Bearer wrong'}).status_code == 401
            for suffix, method, payload in [('', 'GET', None), ('/read', 'PUT', {'read': True}),
                    ('/submit', 'POST', {'requestId': 'x', 'expectedRevision': 0}), ('', 'DELETE', None)]:
                assert client.request(method, '/api/user-inbox/'+foreign['id']+suffix, headers=auth, json=payload).status_code == 404
        browser=subprocess.run(['node','packages/web/e2e/locker-binding.mjs',rt.base_url,str(OUT.resolve()),submitted['id'],target['id']],
            env={**os.environ,'PAN_TEST_JOB_KEY':key},capture_output=True,text=True,encoding='utf-8',timeout=90)
        (OUT/'browser.log').write_text(browser.stdout+'\n'+browser.stderr,encoding='utf-8')
        assert browser.returncode==0,browser.stderr
        approved=rt.request('GET','/api/user-inbox/'+submitted['id'],headers=auth)['card']
        payload = {'requestId': approved['response']['requestId'], 'expectedRevision': 0, 'choiceId': 'pause', 'answer': 'ANSWER-SECRET'}
        for _ in range(2):
            result = rt.request('POST', '/api/user-inbox/'+submitted['id']+'/submit', headers=auth, json=payload)
            assert result['card']['delivery']['state'] == 'accepted'
        rt.request('POST', '/api/user-inbox/'+rejected['id']+'/reject', headers=auth,
            json={'requestId': 'reject', 'expectedRevision': 0, 'answer': 'Owned rejection probe'})
        rt.request('DELETE', '/api/user-inbox/'+rejected['id'], headers=auth)
        def done():
            runs = rt.request('GET', '/api/automation')['runs']
            return runs if len(runs) == 1 and next(iter(runs.values()))['status'] == 'succeeded' else None
        runs = h._wait_for(done, label='one directed Pause Run')
        assert next(iter(runs.values()))['triggerId'] == 'selected'
        assert rt.request('GET', f"/api/sessions/{target['id']}/queue")['queuePaused']
        queued = rt.request('GET', f"/api/sessions/{owner['id']}/queue")['items']
        assert len(queued) == 2
        persisted = json.loads((rt.data_root/'sessions'/f"{owner['id']}.json").read_text(encoding='utf-8'))['queue_pending']
        assert len(persisted) == 2
        assert all(len(n['envelope']['summaryBody']) == 200 for n in persisted)
        assert 'FULL-SECRET' not in json.dumps(queued) and 'ANSWER-SECRET' in json.dumps(queued)
        assert not h._received(rt), 'Paused: persistent acceptance is not CLI consumption'
        evidence['durableQueueIds'] = [n['id'] for n in queued]
        rt.request('PATCH', f"/api/sessions/{owner['id']}/queue/paused", json={'paused': False})
        def delivered():
            records = h._received(rt)
            text = '\n'.join(n['text'] for n in records)
            return records if text.count('Interaction submitted: ') == 1 and text.count('Interaction rejected: ') == 1 and not rt.request('GET', f"/api/sessions/{owner['id']}/queue")['items'] else None
        received = h._wait_for(delivered, label='source fakeCLI actually reads both notices')
        assert sum('Interaction submitted: ' in n['text'] for n in received) == 1
        assert sum('Interaction rejected: ' in n['text'] for n in received) == 1
        assert not any('FULL-SECRET' in n['text'] for n in received)
        response_notices=[json.loads(line.split(': ',1)[1]) for n in received for line in n['text'].splitlines()
                          if line.startswith(('Interaction submitted: ','Interaction rejected: '))]
        submitted_notice=next(n for n in response_notices if n['responseKind']=='submit')
        rejected_notice=next(n for n in response_notices if n['responseKind']=='reject')
        assert submitted_notice['cardId']==submitted['id'] and submitted_notice['answer']=='ANSWER-SECRET'
        assert submitted_notice['choiceId']=='pause' and len(submitted_notice['summaryBody'])==200
        assert rejected_notice['cardId']==rejected['id'] and len(rejected_notice['summaryBody'])==200
        evidence['actualResponseNotices']=response_notices
        history = rt.request('GET', f"/api/sessions/{owner['id']}/history?limit=100")['history']
        notice_history = [n for n in history if n.get('role') == 'user' and 'Interaction ' in n.get('content', '')]
        assert sum(n.get('content', '').count('Interaction submitted: ') for n in notice_history) == 1
        assert sum(n.get('content', '').count('Interaction rejected: ') for n in notice_history) == 1
        assert {qid for n in notice_history for qid in n.get('queueItemIds', [])} == set(evidence['durableQueueIds'])
        delivered_state = json.loads((rt.data_root/'sessions'/f"{owner['id']}.json").read_text(encoding='utf-8'))
        assert set(evidence['durableQueueIds']) <= set(delivered_state['queue_delivery_ledger'])
        evidence.update(actualFakeCLIReceived=received, history=notice_history, runIds=list(runs),
            deliveryLedger=delivered_state['queue_delivery_ledger'])
        stop()
        rt.start()
        assert httpx.get(rt.base_url+'/api/user-lockers', headers=auth, trust_env=False).status_code == 401
        auth = {'Authorization': 'Bearer '+rt.request('POST', '/api/automation/login', json={'key': key})['token']}
        assert rt.request('GET', '/api/user-inbox/'+submitted['id'], headers=auth)['card']['delivery']['state'] == 'accepted'
        assert len(rt.request('GET', '/api/automation')['runs']) == 1
        assert not rt.request('GET', f"/api/sessions/{owner['id']}/queue")['items']
        assert h._received(rt) == received
        evidence['restartNoReplay'] = True
    finally:
        stop()
        evidence['portReleased'] = h._port_is_free(h.PORT)
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT/'http-evidence.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
        assert evidence['portReleased']
