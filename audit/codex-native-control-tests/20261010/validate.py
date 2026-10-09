import json, os, pathlib, subprocess, sys, time, hashlib, xml.etree.ElementTree as ET
ROOT=pathlib.Path(__file__).resolve().parents[3]
BASE=ROOT/'audit/codex-native-control-tests/20261010'
OUT=BASE/'evidence-r3'
OUT.mkdir(parents=True, exist_ok=True)
EXTRA=BASE/'python-extra'
ENV={k:v for k,v in os.environ.items() if not k.upper().startswith('PAN_')}
ENV['PYTHONPATH']=str(ROOT)+os.pathsep+str(EXTRA)
ENV['PYTHONIOENCODING']='utf-8'
ENV['PYTHONUTF8']='1'
SHA=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
tracked=subprocess.check_output(['git','ls-files','packages','tests'],cwd=ROOT,text=True).splitlines()
def hashes():
 return {p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in tracked if (ROOT/p).is_file() and pathlib.Path(p).suffix in {'.py','.ts','.tsx','.json','.yaml','.yml'}}
initial=hashes()
summary={'sha':SHA,'python':sys.version,'executable':sys.executable,'stages':[],'source_hashes_before':initial,'python312':'Interpreter present but unavailable dependencies: pytest import fails with ModuleNotFoundError: pluggy. No shared environment modified.', 'python_extra_hashes': {str(p.relative_to(EXTRA)):hashlib.sha256(p.read_bytes()).hexdigest() for p in EXTRA.rglob('*.py')}}
(OUT/'validation-start.json').write_text(json.dumps(summary,indent=2),encoding='utf8')
def run(label,args,cwd=ROOT,junit=False):
 start=time.time(); xml=OUT/(label+'.xml')
 if junit: args=[sys.executable,'-m','pytest',*args,'-q','-ra','--junitxml='+str(xml)]
 with (OUT/(label+'.txt')).open('w',encoding='utf8') as f:
  f.write('SHA='+SHA+'\nargv='+json.dumps(args)+'\ncwd='+str(cwd)+'\n');f.flush()
  p=subprocess.run(args,cwd=cwd,env=ENV,stdout=f,stderr=subprocess.STDOUT)
 result={'label':label,'argv':args,'exit':p.returncode,'seconds':time.time()-start}
 if junit and xml.exists():
  suites=ET.parse(xml);cases=list(suites.iter('testcase'))
  result.update(tests=len(cases),failures=sum(t.find('failure') is not None for t in cases),errors=sum(t.find('error') is not None for t in cases),skips=[{'nodeid':t.attrib['classname']+'::'+t.attrib['name'],'reason':t.find('skipped').attrib.get('message','')} for t in cases if t.find('skipped') is not None])
  result['passed']=len(cases)-result['failures']-result['errors']-len(result['skips'])
 summary['stages'].append(result)
 print(label,'exit',p.returncode,flush=True)
run('additional-41',(BASE/'additional-failed-nodeids.txt').read_text().splitlines(),junit=True)
run('final-18',(BASE/'failed-nodeids.txt').read_text().splitlines(),junit=True)
modules=sorted({n.split('::')[0] for n in (BASE/'failed-nodeids.txt').read_text().splitlines()})
modules+=['tests/test_terminal_tracked_call.py','tests/test_terminal_runner_ipc.py','tests/test_terminal_runtime.py','tests/test_terminal_windows_backend.py','tests/test_steer_history_consistency.py','tests/test_queue_restart_hardening.py','tests/test_report_subscription.py','tests/test_independent_job_bridge.py','tests/test_worker_watchdog.py']
run('related',modules,junit=True)
for i in range(1,21):
 run(f'watchdog-repeat-{i:02}', ['tests/test_worker_watchdog.py::test_active_running_worker_not_killed','tests/test_worker_watchdog.py::test_running_watchdog_original_task_timeout_boundary'],junit=True)
run('python-full',['tests/','packages/qq'],junit=True)
run('http-rejection-1000',[sys.executable,str(BASE/'http_repeat.py')])
run('frontend-tsc',['pnpm.cmd','exec','tsc','-b'],cwd=ROOT/'packages/web')
run('frontend-lint',['pnpm.cmd','lint'],cwd=ROOT/'packages/web')
run('frontend-build',['pnpm.cmd','build'],cwd=ROOT/'packages/web')
summary['python_extra_hashes_after']={str(p.relative_to(EXTRA)):hashlib.sha256(p.read_bytes()).hexdigest() for p in EXTRA.rglob('*.py')}
summary['source_hashes_after']=hashes()
summary['source_hashes_stable']=initial==summary['source_hashes_after'] and summary['python_extra_hashes']==summary['python_extra_hashes_after']
summary['head_after']=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
summary['passed']=all(s['exit']==0 for s in summary['stages']) and summary['source_hashes_stable'] and summary['head_after']==SHA
(OUT/'validation-summary.json').write_text(json.dumps(summary,indent=2),encoding='utf8')
print(json.dumps({'sha':SHA,'passed':summary['passed'],'stages':len(summary['stages'])}),flush=True)
sys.exit(0 if summary['passed'] else 1)
