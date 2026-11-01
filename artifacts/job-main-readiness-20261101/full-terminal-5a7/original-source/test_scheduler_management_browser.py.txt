"""Actual authenticated HTTP and production Chromium source/reference editing."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx
import pytest

from packages.jobs.window import command_window_options


def test_authenticated_native_source_shared_links_conflict_and_retire_in_production_browser(tmp_path):
    from playwright.sync_api import sync_playwright, expect
    with socket.socket() as owned:
        owned.bind(("127.0.0.1",0))
        port=owned.getsockname()[1]
    assert port not in (8767,8768)
    root=tmp_path/"profile"
    repo=Path(__file__).resolve().parents[1]
    out=repo/"evidence/r6i-forms"
    out.mkdir(exist_ok=True)
    log=(out/"advanced-browser-server.log").open("wb")
    child=subprocess.Popen([sys.executable,"-m","tests.support.scheduler_management_server","--root",str(root),"--port",str(port)],cwd=repo,stdout=log,stderr=subprocess.STDOUT,**command_window_options())
    base=f"http://127.0.0.1:{port}"
    result={"port":port,"rootOwned":True,"productionBundle":True}
    try:
        with httpx.Client(base_url=base,timeout=5) as client:
            for _ in range(100):
                if child.poll() is not None:
                    pytest.fail("Owned fixture exited; inspect browser-server.log")
                try:
                    if client.get('/test/ready').status_code==200:break
                except httpx.TransportError:pass
                time.sleep(.1)
            key=(root/'automation/user.key').read_text(encoding='utf-8').strip()
            token=client.post('/api/automation/login',json={'key':key}).json()['token']
            client.headers['Authorization']='Bearer '+token
            assert client.get('/api/event-creators/providers').status_code==200
            action=next(a for a in client.get('/api/jobs/actions').json()['items'] if a['id']=='product_session_message')
            from packages.jobs.final_service import object_ref
            job_def={'name':'Browser Job','steps':[{'stepId':'message','actionRef':object_ref('action',action),'arguments':{'sessionId':{'from':'literal','value':'owned-session'},'text':{'from':'literal','value':'owned-message'}},'completion':{'contract':'durable-queue-accepted'}}],'retention':{'policyRef':'test'}}
            job=client.post('/api/jobs',json={'definition':job_def,'expectedRevision':0,'requestKey':'browser-job'}).json()['result']['object']
            client.post(f"/api/jobs/{job['id']}/operations",json={'operation':'enable','parameters':{},'expectedRevision':1,'expectedStateRevision':0,'requestKey':'enable-job'}).raise_for_status()
            with sync_playwright() as playwright:
                launch = {}
                if os.name == "nt":
                    installed = sorted((Path(os.environ["LOCALAPPDATA"]) / "ms-playwright").glob("chromium_headless_shell-*/chrome-headless-shell-win64/chrome-headless-shell.exe"))
                    if installed:
                        launch["executable_path"] = str(installed[-1])
                browser=playwright.chromium.launch(headless=True, **launch)
                result["chromiumVersion"] = browser.version
                try:
                    page=browser.new_page(viewport={'width':1440,'height':1000})
                    errors=[]; requests=[]
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    page.on('request',lambda request:requests.append((request.method,request.url.split(base)[-1])))
                    page.goto(base+'/react/automation?tab=events')
                    page.get_by_label('Local user key',exact=True).fill(key)
                    page.get_by_role('button',name='Log in',exact=True).click()
                    page.get_by_role('button',name='New Scheduler',exact=True).click()
                    page.get_by_label('Scheduler name',exact=True).fill('Browser timer')
                    config={'rules':[{'ruleId':'fraction','type':'interval','seconds':'3600.125','anchor':'2026-11-01T00:00:00.125Z','advanceMode':'grid'},{'ruleId':'dst','type':'weekly','weekday':0,'time':'01:30:00','timezone':'America/New_York','dstPolicy':'gap-skip-fold-second'}],'misfirePolicy':'skip','graceSeconds':'1.25','pausePolicy':'advance','driveLimit':{'maxEvents':17,'initialProducedCount':0}}
                    page.get_by_text('Advanced Scheduler JSON',exact=True).click()
                    page.get_by_label('Scheduler config JSON').fill(json.dumps(config))
                    page.get_by_role('button',name='Save Scheduler',exact=True).click()
                    expect(page.get_by_role('article',name='Scheduler Browser timer',exact=True)).to_be_visible()
                    source=next(s for s in client.get('/api/event-creators?limit=100').json()['items'] if s['name']=='Browser timer')
                    assert source['config']==config
                    # Two real canonical subscriptions share this exact source.
                    for index in range(2):
                        response=client.post('/api/triggers',json={'definition':{'name':f'Browser subscription {index}','targetJobId':job['id'],'eventTypes':['scheduler.time.v2'],'creatorSelector':{'mode':'ids','ids':[source['id']]}},'expectedRevision':0,'expectedCreatorRevisions':{source['id']:source['revision']},'requestKey':f'sub-{index}'})
                        response.raise_for_status()
                    page.get_by_role('button',name='Refresh',exact=True).click()
                    expect(page.get_by_role('heading',name='2 referencing subscribers',exact=True)).to_be_visible()
                    page.get_by_role('button',name='Edit Browser timer',exact=True).click()
                    expect(page.get_by_role('button',name='Save Scheduler',exact=True)).to_be_disabled()
                    expect(page.get_by_role('button',name='Delete Scheduler',exact=True)).to_be_disabled()
                    page.get_by_label('Scheduler name',exact=True).fill('Browser local edit')
                    page.get_by_label('I reviewed the affected subscribers.').check()
                    # Concurrent source update must reject the displayed CAS.
                    from packages.jobs.final_service import definition
                    client.put('/api/event-creators/'+source['id'],json={'definition':{**definition('event_creator',source),'name':'Concurrent server edit'},'expectedRevision':source['revision'],'requestKey':'concurrent-edit'}).raise_for_status()
                    page.get_by_role('button',name='Save Scheduler',exact=True).click()
                    expect(page.get_by_role('button',name='Keep draft against latest source revision')).to_be_visible()
                    expect(page.get_by_label('Scheduler name',exact=True)).to_have_value('Browser local edit')
                    page.get_by_role('button',name='Keep draft against latest source revision').click()
                    expect(page.get_by_role('button',name='Save Scheduler',exact=True)).to_be_disabled()
                    page.get_by_label('I reviewed the affected subscribers.').check()
                    page.get_by_role('button',name='Save Scheduler',exact=True).click()
                    expect(page.get_by_role('article',name='Scheduler Browser local edit',exact=True)).to_be_visible()
                    current=client.get('/api/event-creators/'+source['id']).json()
                    assert current['config']==config and current['revision']==3
                    # Lifecycle is a separate authenticated CAS operation. The
                    # fixture clock precedes both rules, so no effect is due.
                    for enabled in (True, False):
                        page.get_by_role('button',name='Edit Browser local edit',exact=True).click()
                        page.get_by_label('Scheduler enabled',exact=True).set_checked(enabled)
                        page.get_by_label('I reviewed the affected subscribers.').check()
                        page.get_by_role('button',name='Save Scheduler',exact=True).click()
                        expect(page.get_by_role('region',name='Scheduler editor')).to_have_count(0)
                        current=client.get('/api/event-creators/'+source['id']).json()
                        assert current['state']==('enabled' if enabled else 'disabled')
                        assert current['config']==config
                        assert client.get('/api/jobs/'+job['id']).json()['state']=='enabled'
                    # Reference picker writes real Creator IDs and reviewed revisions.
                    page.get_by_role('tab',name='Trigger',exact=True).click()
                    page.get_by_role('button',name='创建 Triggers',exact=True).click()
                    page.get_by_label('名称',exact=True).fill('Browser picker subscription')
                    page.get_by_label('Target canonical Job').select_option(job['id'])
                    page.get_by_label('Scheduler sources').select_option([source['id']])
                    page.get_by_role('button',name='保存定义',exact=True).click()
                    expect(page.get_by_role('article',name='Triggers Browser picker subscription')).to_be_visible()
                    refs=client.get('/api/event-creators/'+source['id']+'/references').json()
                    assert len(refs['references'])==3
                    # Detaching subscriptions is explicit; source and history survive.
                    for trigger in client.get('/api/triggers?limit=100').json()['items']:
                        client.request('DELETE','/api/triggers/'+trigger['id'],json={'expectedRevision':trigger['revision'],'expectedStateRevision':trigger['stateRevision'],'requestKey':'retire-'+trigger['id']}).raise_for_status()
                    page.get_by_role('tab',name='Event Register',exact=True).click()
                    page.get_by_role('button',name='Refresh',exact=True).click()
                    expect(page.get_by_role('heading',name='0 referencing subscribers',exact=True)).to_be_visible()
                    page.get_by_role('button',name='Edit Browser local edit',exact=True).click()
                    page.on('dialog',lambda dialog:dialog.accept())
                    page.get_by_role('button',name='Delete Scheduler',exact=True).click()
                    expect(page.get_by_role('article',name='Scheduler Browser local edit',exact=True)).to_have_count(0)
                    assert client.get('/api/event-creators/'+source['id']).json()['state']=='retired'
                    assert not any('scheduler/event-sources' in path for method,path in requests)
                    assert errors==[],errors
                    for width in (390,1440):
                        page.set_viewport_size({'width':width,'height':1000})
                        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                    result.update({'sourceId':source['id'],'configRoundTrip':True,'sharedReferences':3,'sourceConflictRebased':True,'sourceLifecycleCAS':True,'nativePicker':True,'retiredHistoryReadable':True,'legacySourceRequests':0,'pageErrors':errors,'providerCalls':client.get('/test/ready').json()['providerCalls']})
                    assert result['providerCalls']==0
                finally:
                    browser.close()
    finally:
        child.terminate()
        try:child.wait(timeout=15)
        except subprocess.TimeoutExpired:child.kill();child.wait(timeout=10)
        log.close()
        for _ in range(50):
            with socket.socket() as check:
                check.settimeout(.2)
                released=check.connect_ex(('127.0.0.1',port))!=0
            if released:break
            time.sleep(.1)
        assert released
        result['portReleased']=True
        (out/'advanced-browser-result.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
