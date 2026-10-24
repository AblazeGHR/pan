"""Real production GUI scroll geometry on isolated profile/random port."""
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import secrets
import socket
import subprocess
from tests import test_real_queue_http_e2e as h
from tests.test_locker_product_live import ROOT

OUT = ROOT/'audit/locker-order-manual-read-mcp/scroll'


def test_locker_order_scroll_live(tmp_path, monkeypatch):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    assert port not in {8767, 8768}
    monkeypatch.setattr(h, 'PORT', port)
    monkeypatch.setattr(h, 'SERVER_LAUNCHER', ROOT/'tests/support/locker_product_http_server.py')
    nonce = secrets.token_hex(24)
    monkeypatch.setenv('PAN_TEST_LOCKER_NONCE', nonce)
    monkeypatch.setenv('PAN_TEST_DISABLE_QUEUE_WATCHDOG', '1')
    rt = h._PanRuntime(tmp_path/'profile/data')
    rt.data_root.mkdir(parents=True)
    (rt.data_root/'config.json').write_text(json.dumps({'memory': {'enabled': False}, 'plugin_manifests': [str(ROOT/'manifest.json'), str(ROOT/'packages/mcp/manifest.json')]}), encoding='utf-8')
    OUT.mkdir(parents=True, exist_ok=True)
    proof = {'port': port, 'profile': str(rt.data_root), 'apiInterception': False}
    try:
        rt.start()
        proof['identity'] = rt.request('GET', '/__test/job-inbox-identity')
        assert str(rt.data_root) == str(Path(proof['identity']['sessions']).parent)
        key = (rt.data_root/'automation/user.key').read_text(encoding='utf-8')
        source = rt.request('POST', '/api/sessions', json={'name': 'Scroll-source', 'adapter': 'cbc', 'mcpServers': [], 'workdir': str(tmp_path)})
        assert source.get('id'), source
        rt.request('PATCH', f"/api/sessions/{source['id']}/queue/paused", json={'paused': True})
        ids=[]
        for index in range(110):
            message = {'messageId': f'scroll-{index:03}', 'role': 'assistant', 'content': f'## Scroll row {index:03}\n\nReading ordinary content.\n\n'+('Paragraph with stable height.\n\n'*3), 'ts': datetime.now(timezone.utc).isoformat()}
            row = rt.request('POST', '/__test/locker/accept', headers={'x-locker-fixture':nonce},
                json={'sourceSessionId':source['id'],'recipientId':'user','message':message})['message']
            ids.append(row['id'])
            if index in {20,60,90}:
                card=rt.request('POST','/api/interaction-cards',json={'ownerSessionId':source['id'],'recipientId':'user','requestKey':f'scroll-card-{index}',
                    'card':{'title':f'Mixed Card {index}','body':'A real Card between ordinary messages.','summaryBody':'Mixed Card'}})['card']
                ids.append(card['id'])
        # Use production createdAt/ID comparator rather than synthetic insertion assumptions.
        auth={'Authorization':'Bearer '+rt.request('POST','/api/automation/login',json={'key':key})['token']}
        items=rt.request('GET','/api/user-locker-messages?limit=500',headers=auth)['messages']+rt.request('GET','/api/user-inbox',headers=auth)['cards']
        ids=[item['id'] for item in sorted(items,key=lambda item:(item['createdAt'],item['id']))]
        fixture={'ids':ids,'locker':row['lockerId'],'source':source['id'],'nonce':nonce}
        fixture_path=OUT/'fixture.json';fixture_path.write_text(json.dumps(fixture),encoding='utf-8')
        result=subprocess.run(['node',str(ROOT/'packages/web/e2e/locker-order-scroll.mjs'),rt.base_url,str(OUT),str(fixture_path)],
            cwd=ROOT,env={**os.environ,'PAN_TEST_JOB_KEY':key},capture_output=True,text=True,encoding='utf-8',timeout=90)
        (OUT/'browser.log').write_text(result.stdout+'\n'+result.stderr,encoding='utf-8')
        proof['browserExitCode']=result.returncode
        assert result.returncode==0,result.stdout+'\n'+result.stderr
        assert not rt.request('GET', f"/api/sessions/{source['id']}/history")['history']
        assert not rt.request('GET', f"/api/sessions/{source['id']}/queue")['items']
    finally:
        rt.stop()
        proof['portReleased']=h._port_is_free(port)
        (OUT/'evidence.json').write_text(json.dumps(proof,indent=2),encoding='utf-8')
        assert proof['portReleased']
