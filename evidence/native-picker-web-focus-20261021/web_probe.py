"""Real production browser button -> real HTTP -> unchanged product helper.
Owned temporary profile; no provider, existing service or global input calls.
"""
import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import psutil
import httpx
import uvicorn
from playwright.async_api import async_playwright
import win_facts as win
ROOT=Path(__file__).resolve().parents[2]
OUT=Path(__file__).parent/sys.argv[1];OUT.mkdir(exist_ok=True)
RUNTIME=Path(tempfile.mkdtemp(prefix='pan-picker-web-'))
for key in list(os.environ):
 if key.upper().startswith('PAN_'):del os.environ[key]
with socket.socket() as s:s.bind(('127.0.0.1',0));PORT=s.getsockname()[1]
assert PORT not in (8767,8768)
os.environ.update(PAN_E2E_RUNTIME=str(RUNTIME),PAN_PORT=str(PORT))
sys.path.insert(0,str(ROOT))
spec=importlib.util.spec_from_file_location('fixture',ROOT/'packages/web/e2e/server.py')
fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
from packages.web import server as product
from packages.web.native_file_picker import native_file_picker
fixture._install_test_isolation(product)
from packages.core.character import CharacterManager
product._character_manager=CharacterManager(str(fixture.DATA_DIR))
product._character_manager.load_manifest([])
from packages.core import session_drafts
session_drafts.DRAFT_DIR=RUNTIME/'drafts'
work=RUNTIME/'work';work.mkdir()
file=work/'native-附件.txt';file.write_text('owned fixture unchanged',encoding='utf-8')
session=product.sess.create('Native Picker Focus',adapter='codex',workdir=str(work),history=[])
session.mcp_servers=[];product.sess.save(session)
report={'checkout':str(ROOT),'head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'helperSha256':hashlib.sha256((ROOT/'packages/web/native_file_dialog.ps1').read_bytes()).hexdigest(),'pid':os.getpid(),'port':PORT,'runtime':str(RUNTIME),'sessionId':session.id,'lifespan':'off: full production app routes/static/WS, no service startup loops','events':[],'cases':[]}
def event(kind,**data):
 row={'kind':kind,'time':time.time(),'foreground':win.foreground(),'lastInput':win.last_input(),'serverDesktopSession':win.process_session(os.getpid()),**data};report['events'].append(row);return row
def save():
 (OUT/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
original_popen=subprocess.Popen
owned=[]
def observe_popen(argv,*args,**kwargs):
 p=original_popen(argv,*args,**kwargs)
 if isinstance(argv,list) and str(argv[-1]).endswith('native_file_dialog.ps1'):
  owned.append(p);event('helper-start',helperPid=p.pid,helperDesktopSession=win.process_session(p.pid),parentPid=os.getpid(),serverParentPid=psutil.Process().ppid(),argv=argv)
  communicate=p.communicate
  def observed(*args,**kwargs):
   r=communicate(*args,**kwargs)
   if r[1]:p.focus_diagnostic=r[1].decode('utf-8',errors='replace')
   return r
  p.communicate=observed
 return p
subprocess.Popen=observe_popen
@product.app.middleware('http')
async def trace(request,call_next):
 if request.url.path.endswith('/attachments/native-picker'):event('http-native-start',path=request.url.path)
 response=await call_next(request)
 if request.url.path.endswith('/attachments/native-picker'):event('http-native-end',status=response.status_code)
 return response
host=uvicorn.Server(uvicorn.Config(product.app,host='127.0.0.1',port=PORT,lifespan='off',log_level='warning',access_log=False))
thread=threading.Thread(target=host.run);thread.start()
base=f'http://127.0.0.1:{PORT}'
async def run():
 context=None
 async with async_playwright() as p:
  try:
   context=await p.chromium.launch_persistent_context(str(RUNTIME/'browser-profile'),channel='msedge',headless=False,no_viewport=True,args=['--no-first-run','--window-size=1280,900'])
   page=context.pages[0]
   await page.add_init_script("""window.__pickerClicks=[];document.addEventListener('click',e=>{if(e.target.closest('button')?.textContent.includes('Open server file picker'))window.__pickerClicks.push({trusted:e.isTrusted,time:Date.now(),visibility:document.visibilityState,focus:document.hasFocus()});},true);""")
   responses=[]
   page.on('response',lambda r:responses.append(r) if '/attachments/' in r.url else None)
   await page.goto(base+'/react/',wait_until='networkidle')
   await page.locator('[data-session-card-id]').filter(has_text='Native Picker Focus').first.click()
   await page.get_by_role('button',name='添加附件',exact=True).click()
   await page.get_by_role('button',name='服务端附件',exact=True).click()
   await page.get_by_role('button',name='Open server file picker',exact=True).wait_for()
   await page.screenshot(path=str(OUT/'web-before.png'))
   event('browser-ready',browserProcesses=[{'pid':x.pid,'ppid':x.ppid(),'name':x.name()} for x in psutil.Process().children(recursive=True) if 'msedge' in x.name().lower()])
   browser_pids={x.pid for x in psutil.Process().children(recursive=True) if 'msedge' in x.name().lower()}
   browser_windows=[f for pid in browser_pids for f in win.windows(pid) if f['class']=='Chrome_WidgetWin_1' and f['visible']]
   assert len(browser_windows)==1,browser_windows
   browser_hwnd=browser_windows[0]['hwnd']
   for action in ('cancel','select'):
    button=page.get_by_role('button',name='Open server file picker',exact=True)
    if sys.argv[1]!='before':
     event('test-browser-activation-mouse',action=action,mouse=win.show_owned_browser(browser_hwnd))
     await asyncio.sleep(.15)
     assert win.foreground()['hwnd']==browser_hwnd,win.foreground()
    event('before-browser-click',action=action,lastInput=win.last_input(),browserHwnd=browser_hwnd,serverParentPid=psutil.Process().ppid())
    async with page.expect_response(lambda r:r.url.endswith('/attachments/native-picker')) as waiting:
     if sys.argv[1]=='before':
      await button.click()
     else:
      box=await button.bounding_box()
      metrics=await page.evaluate('({outerWidth,outerHeight,innerWidth,innerHeight,devicePixelRatio})')
      r=win.rect(browser_hwnd);scale=(r['right']-r['left'])/metrics['outerWidth']
      border=(metrics['outerWidth']-metrics['innerWidth'])/2
      x=round(r['left']+(border+box['x']+box['width']/2)*scale)
      y=round(r['top']+(metrics['outerHeight']-metrics['innerHeight']-border+box['y']+box['height']/2)*scale)
      event('test-native-mouse-button-click',action=action,metrics=metrics,box=box,rect=r,mouse=win.mouse_click(browser_hwnd,x,y))
     event('after-browser-click',action=action)
     for _ in range(100):
      process=native_file_picker._process
      if process is not None:break
      await asyncio.sleep(.05)
     assert process is not None,'helper not started'
     d=await asyncio.to_thread(win.dialog,process.pid)
     await asyncio.sleep(.4)
     earlyForeground=win.foreground()
     await asyncio.sleep(1.2)
     case={'action':action,'foregroundAt400ms':earlyForeground,'measurementLastInput':win.last_input(),'dialog':win.fact(d['hwnd']),'foreground':win.foreground(),'windows':win.windows(process.pid)}
     case['focused']=case['foreground']['hwnd']==d['hwnd'] and case['foreground']['keyboardFocusRoot']==d['hwnd']
     assert not case['dialog']['topmost']
     assert all(not x['topmost'] for x in case['windows'] if x['hwnd'] in (d['hwnd'],d['owner']))
     case['responsiveHTTP']=(await asyncio.to_thread(httpx.get,base+'/api/sessions?summary=1',timeout=3)).status_code
     report['cases'].append(case);save()
     if sys.argv[1]=='mouse-final' and case['focused']:
      r=win.rect(browser_hwnd)
      # Normal browser activation is test preparation only, never picker code.
      case['ordinarySwitchClick']=win.show_owned_browser(browser_hwnd)
      await asyncio.sleep(.2)
      case['afterOrdinaryBrowserSwitch']=win.foreground()
      assert case['afterOrdinaryBrowserSwitch']['hwnd']==browser_hwnd,case
      assert not win.fact(d['hwnd'])['topmost']
      case['switchAwayPassed']=True
     if action=='cancel':win.close_dialog(d['hwnd'])
     else:win.select_file(d['hwnd'],str(file))
    response=await waiting.value
    case['httpStatus']=response.status;case['response']=await response.json();case['focusDiagnostic']=getattr(process,'focus_diagnostic','');case['helperExited']=process.poll() is not None
    assert case['response']['status']==('cancelled' if action=='cancel' else 'selected'),case
    if action=='select':
     await page.get_by_text(str(file),exact=True).wait_for()
     assert not any('from-server-file' in r.url for r in responses)
     await page.get_by_role('button',name='Confirm selected attachments',exact=True).click()
     await page.get_by_test_id('server-attachments').wait_for()
     case['confirmed']=True
     assert file.read_text(encoding='utf-8')=='owned fixture unchanged'
     assert not any(x.name==file.name for x in product.ATTACHMENTS_DIR.rglob('*'))
    save()
   report['focusAcceptancePassed']=len(report['cases'])==2 and all(c['focused'] for c in report['cases'])
   report['browserClicks']=await page.evaluate('window.__pickerClicks')
   assert len(report['browserClicks'])==2 and all(c['trusted'] for c in report['browserClicks'])
   await page.screenshot(path=str(OUT/'web-after.png'))
  finally:
   if context:await context.close()
try:
 for _ in range(100):
  if host.started:break
  time.sleep(.05)
 asyncio.run(run());report['harnessPassed']=True
except BaseException:
 report['error']=traceback.format_exc();raise
finally:
 native_file_picker.close();host.should_exit=True;thread.join(5)
 if thread.is_alive():
  host.force_exit=True
  thread.join(5)
 for case in report['cases']:
  for process in owned:
   if process.pid==case['dialog']['pid']:
    case['focusDiagnostic']=getattr(process,'focus_diagnostic','')
 report['cleanup']={'hostStopped':not thread.is_alive(),'helpersExited':all(p.poll() is not None for p in owned)}
 with socket.socket() as s:report['cleanup']['portClosed']=s.connect_ex(('127.0.0.1',PORT))!=0
 save();print(json.dumps({'output':str(OUT),'cleanup':report['cleanup'],'error':report.get('error')},ensure_ascii=True))
