"""Private-profile installed CLI probe; one maximum hy3 task, bounded, owned Job tree.
No credentials/history are opened or copied. HOME/global settings are never changed.
"""
import ctypes,json,os,queue,subprocess,sys,threading,time,uuid
from pathlib import Path
from ctypes import wintypes
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from packages.core.takeover_job import TakeoverJob
OUT=Path(__file__).parent
base=OUT/('isolated-'+uuid.uuid4().hex[:10]);base.mkdir()
for name in ['cwd','profile','cache','tmp']:(base/name).mkdir()
env={k:os.environ[k] for k in ['SystemRoot','PATH','USERPROFILE','APPDATA','LOCALAPPDATA','COMSPEC','PATHEXT'] if k in os.environ}
env.update(CODEBUDDY_CONFIG_DIR=str(base/'profile'),XDG_CACHE_HOME=str(base/'cache'),TEMP=str(base/'tmp'),TMP=str(base/'tmp'),DISABLE_TELEMETRY='1',DISABLE_GALILEO='1')
cli=['node',r'D:/node_npm/node_global/node_modules/@tencent-ai/codebuddy-code/bin/codebuddy']
facts={'base':str(base),'model':'hy3','identity':{},'frames':[],'notes':[]}
def run(args,inputs=None,seconds=15):
 job=TakeoverJob()
 p=subprocess.Popen(cli+args,cwd=base/'cwd',env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=subprocess.CREATE_NO_WINDOW|4)
 try:
  assert job.api.AssignProcessToJobObject(job.handle,int(p._handle))
  created=wintypes.FILETIME();exited=wintypes.FILETIME();kernel=wintypes.FILETIME();user=wintypes.FILETIME()
  api=ctypes.WinDLL('kernel32',use_last_error=True);api.GetProcessTimes.argtypes=[wintypes.HANDLE]+[ctypes.POINTER(wintypes.FILETIME)]*4
  assert api.GetProcessTimes(int(p._handle),*[ctypes.byref(x) for x in [created,exited,kernel,user]])
  identity={'pid':p.pid,'creationFiletime':(created.dwHighDateTime<<32)|created.dwLowDateTime}
  nt=ctypes.WinDLL('ntdll');nt.NtResumeProcess.argtypes=[wintypes.HANDLE];assert nt.NtResumeProcess(int(p._handle))==0
  q=queue.Queue();raw=[];errors=[]
  def read(pipe,label):
   for line in iter(pipe.readline,b''):
    txt=line.decode('utf-8','replace'); (raw if label=='out' else errors).append(txt)
    if label=='out':
     try:q.put(json.loads(txt))
     except ValueError:pass
  ts=[threading.Thread(target=read,args=(p.stdout,'out'),daemon=True),threading.Thread(target=read,args=(p.stderr,'err'),daemon=True)]
  for t in ts:t.start()
  def send(frame):p.stdin.write((json.dumps(frame)+'\n').encode());p.stdin.flush()
  if inputs:
   for frame in inputs:send(frame)
  deadline=time.monotonic()+seconds;frames=[];sent=False
  while p.poll() is None and time.monotonic()<deadline:
   try:event=q.get(timeout=min(.2,max(.01,deadline-time.monotonic())))
   except queue.Empty:continue
   frames.append(event)
   # A tool sleep creates a safe window only if the one permitted task actually runs.
   if inputs and event.get('type')=='assistant' and not sent:
    content=(event.get('message') or {}).get('content',[])
    if any(x.get('type')=='tool_use' for x in content if isinstance(x,dict)):
     send({'type':'control_request','request_id':'isolated-steer-1','request':{'subtype':'steer','content_blocks':[{'type':'text','text':'Change the final marker to STEER_CONSUMED_73 and do not start another task.'}]}});sent=True
   if inputs and event.get('type')=='result':break
  while not q.empty():frames.append(q.get_nowait())
  text=''.join(raw+errors)
  # Keep only our synthetic CLI output, never print arbitrary auth details.
  classes=[word for word in ['login','Login','auth','Auth','API key','trust','Trust','STEER_CONSUMED_73'] if word in text]
  (base/('cli-'+str(p.pid)+'.txt')).write_text(text,encoding='utf-8')
  return {'identity':identity,'exitCode':p.poll(),'frames':frames,'diagnosticClasses':classes,'steerSent':sent,'outputChars':len(text),'outputPath':str(base/('cli-'+str(p.pid)+'.txt'))}
 finally:
  job.stop();p.wait(timeout=5)
  for t in locals().get('ts',[]):t.join(1)
  facts['notes'].append({'pid':p.pid,'ownedJobEmpty':True})
help_result=run(['--help'],seconds=12)
facts['help']={k:v for k,v in help_result.items() if k!='frames'}
sid=str(uuid.uuid4());facts['cliSessionId']=sid
args=['-p','--input-format','stream-json','--output-format','stream-json','--model','hy3','--session-id',sid,'--setting-sources','','--strict-mcp-config','--mcp-config','{"mcpServers":{}}','--settings','{"trustAll":true,"autoUpdates":false,"alwaysThinkingEnabled":false}','--permission-mode','bypassPermissions']
facts['probe']=run(args,[{'type':'user','message':{'role':'user','content':[{'type':'text','text':'Isolated protocol experiment. Use Bash once to run python -c "import time; time.sleep(6)". Then output BASELINE_19 only. Do not read any files, credentials, environment, history or settings.'}]}},{'type':'control_request','request_id':'isolated-idle','request':{'subtype':'steer','content_blocks':[{'type':'text','text':'Protocol boundary marker; no independent task.'}],'expected_request_id':'definitely-stale'}}],seconds=42)
(OUT/'cli-result.json').write_text(json.dumps(facts,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'result':str(OUT/'cli-result.json'),'help':facts['help'],'probe':{k:v for k,v in facts['probe'].items() if k!='frames'},'frameTypes':[x.get('type') for x in facts['probe']['frames']],'cleanup':facts['notes']},ensure_ascii=False))
