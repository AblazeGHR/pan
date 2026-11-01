"""Production React and authenticated canonical HTTP on an owned no-effect app."""
import json,socket,threading,time
from pathlib import Path
import uvicorn
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from playwright.sync_api import sync_playwright
from tests.test_job_final_http_service import h,job_def


def test_production_blank_name_uses_server_default_and_lost_response_queries_original(h):
    dist=Path(__file__).resolve().parents[1]/'packages/web/dist'
    assert (dist/'index.html').is_file()
    from types import SimpleNamespace
    from packages.automation.api import install as install_auth
    install_auth(SimpleNamespace(app=h.app))
    @h.app.get('/api/main/startup-recovery')
    def empty_owned_recovery():
        # This fixture has no service/Worker startup or recovery candidates.
        return {'state':'no_candidates','candidateSnapshot':[]}
    from packages.jobs.maintenance import install
    install(h.app)
    h.app.mount('/react/assets',StaticFiles(directory=dist/'assets'))
    @h.app.get('/react/{path:path}')
    def bundle(path):return FileResponse(dist/'index.html')
    listener=socket.socket();listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
    assert port not in {8767,8768}
    server=uvicorn.Server(uvicorn.Config(h.app,lifespan='off',log_level='error'))
    thread=threading.Thread(target=server.run,kwargs={'sockets':[listener]},daemon=True);thread.start()
    try:
        deadline=time.monotonic()+5
        while not server.started and thread.is_alive() and time.monotonic()<deadline:time.sleep(.01)
        assert server.started
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            try:
                page=browser.new_page();page.goto(f'http://127.0.0.1:{port}/react/automation/canonical')
                page.get_by_label('Local user key').fill(h.automation.auth.key())
                page.get_by_role('button',name='Log in',exact=True).click()
                page.get_by_role('button',name='创建 Jobs',exact=True).click()
                name=page.get_by_label('名称',exact=True)
                assert name.input_value()=='' and not name.evaluate('(e)=>e.required')
                page.get_by_text('Advanced definition JSON',exact=True).click()
                value=job_def(h);value.pop('name')
                page.locator('textarea[spellcheck=false]').fill(json.dumps(value))
                writes=[]
                def lose_reply(route):
                    writes.append(route.request.post_data_json)
                    response=route.fetch();assert response.status==201
                    route.abort()
                page.route('**/api/jobs',lose_reply)
                page.get_by_role('button',name='保存定义',exact=True).click()
                page.get_by_role('button',name='查询原操作',exact=False).wait_for()
                page.get_by_role('button',name='查询原操作',exact=False).click()
                page.get_by_role('heading',name='job-1',exact=True).wait_for()
                assert len(writes)==1 and 'name' not in writes[0]['definition']
                items=h.service.list('job',actor={'type':'user','id':'local'})['items']
                assert len(items)==1 and items[0]['name']=='job-1' and items[0]['description']==''
                assert not h.service.final.get(items[0]['id'])['runs'] and h.provider.calls==[]
            finally:browser.close()
    finally:
        server.should_exit=True;thread.join(8);listener.close();assert not thread.is_alive()
