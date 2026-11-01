"""Disposable canonical controller; only its retained harness handle is killed."""
import json,sys,time,site
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from packages.jobs.independent_executor import ExecutionStore,ExecutionHost
from packages.core.platform.execution_host import launch_detached
root,destination,runtime,mode=sys.argv[1:5]
root=Path(root);destination=Path(destination)
helper=Path(__file__).with_name('lifecycle_fault_runner.py');tree=helper.with_name('lifecycle_tree_probe.py')
def launch(root,identity):
    argv=[runtime,str(helper),'--kind','canonical','--fault','none','--root',str(root),'--job-id',identity,'--evidence',str(destination/'runner.json')]
    argv += [value for directory in site.getsitepackages() for value in ('--site-packages',directory)]
    result=launch_detached(argv,receipt_root=root)
    if mode=='blocked':
        deadline=time.monotonic()+15
        while not (destination/'tree-0.json').exists():
            assert time.monotonic()<deadline;time.sleep(.01)
        (destination/'spawn.json').write_text(json.dumps({'effectId':identity,'controllerReturnPending':True}),encoding='utf-8')
        time.sleep(30)
    return result
attempt={'attemptId':'attempt_controller','runId':'run_controller','stepId':'step','ownerEpoch':1,'generation':1,'leaseId':'lease_controller','state':'running'}
key={'runId':attempt['runId'],'stepId':'step','targetId':'original','operationOrdinal':0}
request={'jobId':'job_controller','attempt':attempt,'effectKey':key,'frozenPlan':{'operation':'process','arguments':{'argv':[runtime,str(tree),str(destination),'2'],'cwd':str(destination),'hideWindow':True}}}
(destination/'request.json').write_text(json.dumps(request),encoding='utf-8')
doc=ExecutionHost(ExecutionStore(root),launch=launch).submit_execution(request)
(destination/'job.json').write_text(json.dumps(doc),encoding='utf-8')
time.sleep(30)
