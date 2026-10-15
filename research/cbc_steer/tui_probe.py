"""One bounded native CBC TUI task, using an observer hook with no Pan bridge gates."""
import json,os,queue,secrets,subprocess,sys,threading,time,uuid
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from packages.core.terminal.backend import ConPtyBackend
OUT=Path(__file__).parent;base=OUT/('tui-isolated-'+uuid.uuid4().hex[:10]);base.mkdir()
for name in ['cwd','profile','cache','tmp']:(base/name).mkdir()
events=queue.Queue();token=secrets.token_urlsafe(32)
class Observer(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def do_POST(self):
  if not secrets.compare_digest(self.headers.get('Authorization',''),'Bearer '+token):self.send_error(403);return
  event=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
  events.put(event)
  self.send_response(200);self.send_header('Content-Length','2');self.end_headers();self.wfile.write(b'{}')
server=ThreadingHTTPServer(('127.0.0.1',0),Observer);server.daemon_threads=True
threading.Thread(target=server.serve_forever,daemon=True).start()
env={k:os.environ[k] for k in ['SystemRoot','PATH','USERPROFILE','APPDATA','LOCALAPPDATA','COMSPEC','PATHEXT'] if k in os.environ}
env.update(CODEBUDDY_CONFIG_DIR=str(base/'profile'),XDG_CACHE_HOME=str(base/'cache'),TEMP=str(base/'tmp'),TMP=str(base/'tmp'),TERM='xterm-256color',DISABLE_TELEMETRY='1',DISABLE_GALILEO='1',PAN_CBC_HOOK_URL=f'http://127.0.0.1:{server.server_port}/hook',PAN_CBC_HOOK_TOKEN=token)
command=subprocess.list2cmdline([sys.executable.replace('\\','/'),(ROOT/'packages/core/adapters/cbc/native_hook.py').as_posix()])
settings={'trustAll':True,'autoUpdates':False,'alwaysThinkingEnabled':False,'hooks':{name:[{'hooks':[{'type':'command','command':command,'timeout':8}]}] for name in ['SessionStart','UserPromptSubmit','PreToolUse','PostToolUse','Stop','FinalStop']}}
sid=str(uuid.uuid4())
args=['node',r'D:/node_npm/node_global/node_modules/@tencent-ai/codebuddy-code/bin/codebuddy','--model','hy3','--session-id',sid,'--setting-sources','','--strict-mcp-config','--mcp-config','{"mcpServers":{}}','--settings',json.dumps(settings),'--permission-mode','bypassPermissions']
facts={'base':str(base),'sessionId':sid,'model':'hy3','events':[],'writes':[],'steerSent':False,'finalStop':False}
backend=None;stopping=threading.Event();raw=bytearray()
try:
 backend=ConPtyBackend.spawn(args,cwd=str(base/'cwd'),env=env,rows=32,cols=140)
 facts['identity']={'pid':backend.pid,'creationFiletime':backend.identity.created_at_filetime}
 (OUT/'tui-result.json').write_text(json.dumps(facts,ensure_ascii=False,indent=2),encoding='utf-8')
 def drain():
  while not stopping.is_set():
   try:
    data=backend.read(65536,timeout=.2)
    if data:
     if len(raw)<2*1024*1024:raw.extend(data)
     # Answer standard terminal device queries, never infer task facts from ANSI.
     if b'\x1b[6n' in data:backend.write(b'\x1b[1;1R')
   except Exception:return
 threading.Thread(target=drain,daemon=True).start()
 def send(text):
  data=b'\x1b[200~'+text.encode()+b'\x1b[201~'
  count=backend.write(data);assert count==len(data),'partial paste; do not retry'
  time.sleep(.3);assert backend.write(b'\r')==1,'unknown submit; do not retry'
  facts['writes'].append({'bytes':count+1,'pid':backend.pid,'creationFiletime':backend.identity.created_at_filetime})
 deadline=time.monotonic()+58;submitted=False
 while time.monotonic()<deadline:
  try:event=events.get(timeout=min(.5,max(.01,deadline-time.monotonic())))
  except queue.Empty:continue
  facts['events'].append(event);name=event.get('hook_event_name')
  if name=='SessionStart' and not submitted:
   submitted=True;send('Isolated protocol experiment. Use Bash once to run python -c "import time; time.sleep(8)". Then output TUI_BASELINE_19 only. Do not read any files, credentials, environment, history or settings.')
  elif name=='PreToolUse' and event.get('tool_name') in ['Bash','PowerShell'] and not facts['steerSent']:
   facts['steerSent']=True;send('Change the final marker to TUI_STEER_CONSUMED_73. Apply this to the task you are currently doing.')
  elif name=='FinalStop':facts['finalStop']=True;break
 facts['elapsedDeadlineHit']=time.monotonic()>=deadline
finally:
 stopping.set()
 # Persist observations even if a later cleanup phase refuses to release resources.
 (base/'terminal-raw.txt').write_bytes(raw)
 (OUT/'tui-result.json').write_text(json.dumps(facts,ensure_ascii=False,indent=2),encoding='utf-8')
 try:
  if backend:
   backend.terminate(force=True)  # retained root handle must be signaled before close
   owned,remaining=backend.guard.terminate_tree(backend.pid,timeout=5)
   facts['cleanup']={'ownedAtCleanup':owned,'remaining':remaining}
   assert not remaining,'owned tree cleanup unconfirmed'
   facts['close']=backend.close()
 except BaseException as error:
  facts['cleanupErrorType']=type(error).__name__
  raise
 finally:
  server.shutdown();server.server_close()
  (OUT/'tui-result.json').write_text(json.dumps(facts,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'result':str(OUT/'tui-result.json'),'identity':facts.get('identity'),'events':[{'event':x.get('hook_event_name'),'run':x.get('generation_id'),'text':x.get('last_assistant_message')} for x in facts['events']],'steerSent':facts['steerSent'],'cleanup':facts.get('cleanup')},ensure_ascii=False))
