"""Disposable controller host; killed only through retained harness handle."""
import json, os
from pathlib import Path
import sys, time
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
kind,root,tree,destination=sys.argv[1:5]
os.environ['PAN_BACKGROUND_JOBS_DIR']=root
from packages.core import background_jobs as jobs, config
from packages.jobs.independent_service import JobStore
jobs._sessions.get=lambda sid:{'id':sid}
config.load_config=lambda:{}
runtime=sys.argv[6] if len(sys.argv)>6 else sys.executable
config.resolve_pan_python_argv=lambda:[runtime]
if len(sys.argv)>5 and sys.argv[5]=='post-spawn':
 import subprocess
 from packages.core.terminal.identity import read_creation_filetime
 original=subprocess.Popen
 def spawn(command,**kwargs):
  result=original(command,**kwargs)
  job_id=command[command.index('--job-id')+1]
  (Path(destination)/'spawn.json').write_text(json.dumps(dict(jobId=job_id,pid=result.pid,creationFiletime=read_creation_filetime(int(result._handle)))))
  time.sleep(30)
  return result
 subprocess.Popen=spawn
argv=[runtime,tree,destination,'2']
if len(sys.argv)>5 and sys.argv[5]=='post-spawn':argv.append('--runner-ancestry')
store=JobStore(root) if kind=='independent' else None
job=store.create({'argv':argv,'cwd':destination,'targetSessionId':'owned'}) if store else jobs.start('owned',argv,destination)
(Path(destination)/'job.json').write_text(json.dumps(job))
time.sleep(30)
