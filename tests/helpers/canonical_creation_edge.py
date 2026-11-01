"""Test-only gates around original native creation; no execution/recovery engine."""
import json,site,sys,venv
from pathlib import Path
from packages.jobs.independent_executor import ExecutionStore,ExecutionHost,ExecutionConflict
from packages.core.platform.execution_host import launch_detached,supervisor_alive
from packages.core.terminal.identity import open_process_for_probe,read_creation_filetime,wait_state,ProcessStatus,close_handle_checked
from tests.test_job_final_independent_execution import attempt,key
from tests.test_job_hidden_production import wait_value


def check_creation_edge(tmp_path,fault,launch_mode):
    from tests.test_job_lifecycle_prevention import retain,kill,desktop_gone
    root=tmp_path/'canonical-executions';gate=tmp_path/'canonical-gate.json';marker=tmp_path/'must-not-run'
    runtime=sys._base_executable
    if launch_mode!='base':
        venv.EnvBuilder(with_pip=False,system_site_packages=True).create(tmp_path/'canonical-venv')
        runtime=str(tmp_path/'canonical-venv/Scripts/python.exe')
    helper=Path(__file__).with_name('lifecycle_fault_runner.py')
    launches=[]
    def launch(root,identity):
        launches.append(identity)
        argv=[runtime,str(helper),'--kind','canonical','--fault',fault,'--root',str(root),'--job-id',identity,'--evidence',str(gate)]
        argv += [value for directory in site.getsitepackages() for value in ('--site-packages',directory)]
        return launch_detached(argv,receipt_root=root)
    store=ExecutionStore(root);host=ExecutionHost(store,launch=launch)
    request={'jobId':'job_creation_edge','attempt':attempt(),'effectKey':key(),
        'frozenPlan':{'operation':'process','arguments':{'argv':[sys.executable,'-c','from pathlib import Path;Path('+repr(str(marker))+').touch()'],'cwd':str(tmp_path),'hideWindow':True}}}
    original=host.submit_execution(request);inner=outer=child=None
    try:
        wait_value(gate.exists,bool)
        row=json.loads(gate.read_text())
        inner=retain(row['runnerPid'],row['runnerFiletime'])
        outer=inner if launch_mode=='base' else retain(row['runnerParent'],row['runnerParentFiletime'])
        if fault=='post-create':child=retain(row['pid'],row['creationFiletime'])
        kill(inner if launch_mode=='venv-inner' else outer)
        wait_value(lambda:wait_state(inner),lambda s:s is ProcessStatus.DEAD,timeout=5)
        if child:wait_value(lambda:wait_state(child),lambda s:s is ProcessStatus.DEAD,timeout=5)
        assert not marker.exists()
        cold=ExecutionStore(root);cold.recover(supervisor_alive)
        fact=cold.get(original['effectId'])
        assert fact['result']['outcome']=='unknown' and fact['result']['cleanupProof']['confirmed'] is False
        assert fact['attempt']==original['attempt'] and fact['effectKey']==original['effectKey']
        reloaded=ExecutionHost(cold,launch=lambda *args:(_ for _ in ()).throw(AssertionError('unknown must not relaunch')))
        assert reloaded.submit_execution(request)==fact
        assert reloaded.submit_execution({**request,'retry':True})==fact
        import pytest
        with pytest.raises(ExecutionConflict):
            reloaded.submit_execution({**request,'retry':True,'attempt':attempt(attemptId='other',generation=2)})
        assert cold.get(original['effectId'])==fact and launches==[original['effectId']]
        if child:wait_value(lambda:desktop_gone(row['desktop'].split('\\')[-1]),bool)
    finally:
        for handle in {h for h in (inner,outer,child) if h is not None}:
            if wait_state(handle) is not ProcessStatus.DEAD:kill(handle)
            wait_value(lambda:wait_state(handle),lambda s:s is ProcessStatus.DEAD)
            close_handle_checked(handle)


def check_multigeneration(tmp_path,failure):
    from tests.test_job_lifecycle_prevention import retain,kill,desktop_gone
    root=tmp_path/'canonical-executions';gate=tmp_path/'gate.json'
    helper=Path(__file__).with_name('lifecycle_fault_runner.py');tree=helper.with_name('lifecycle_tree_probe.py')
    if failure=='wait-error':
        def launch(root,identity):
            argv=[sys.executable,str(helper),'--kind','canonical','--fault',failure,'--root',str(root),'--job-id',identity,'--evidence',str(gate)]
            return launch_detached(argv,receipt_root=root)
        host=ExecutionHost(ExecutionStore(root),launch=launch)
    else:host=ExecutionHost(ExecutionStore(root))
    request={'jobId':'job_multigeneration','attempt':attempt(),'effectKey':key(),'frozenPlan':{
        'operation':'process','arguments':{'argv':[sys.executable,str(tree),str(tmp_path),'2'],'cwd':str(tmp_path),'hideWindow':True}}}
    original=host.submit_execution(request);handles=[]
    try:
        wait_value(lambda:(tmp_path/'tree-0.json').exists(),bool)
        rows=[json.loads((tmp_path/('tree-'+str(i)+'.json')).read_text()) for i in [2,1,0]]
        handles=[retain(row['pid'],row['creationFiletime']) for row in rows]
        assert len({row['desktop'] for row in rows})==1 and rows[0]['desktop'].startswith('PanJob_')
        if failure=='wait-error':
            wait_value(gate.exists,bool)
            observed=json.loads(gate.read_text())
            doc=host.get_execution(original['effectId'])
            assert observed['runnerPid']==doc['supervisorIdentity']['pid']
            assert str(observed['runnerFiletime'])==doc['supervisorIdentity']['birth']
            (tmp_path/'fault-release').touch()
        else:(tmp_path/'root-release').touch()
        final=wait_value(lambda:host.get_execution(original['effectId']),lambda d:d['result'] is not None)
        wait_value(lambda:[wait_state(h) for h in handles],lambda states:all(s is ProcessStatus.DEAD for s in states))
        wait_value(lambda:desktop_gone(rows[0]['desktop']),bool)
        if failure=='wait-error':
            assert final['result']['outcome']=='unknown'
            assert final['result']['cleanupProof']['confirmed'] is False
            assert 'injected owned wait failure' in str(final['result'])
        else:
            assert final['result']['outcome']=='succeeded' and final['result']['receipt']['exitCode']==0
            assert final['result']['cleanupProof']['confirmed'] is True
            page=host.read_execution_log(final['effectId'],final['attempt'])
            assert 'owned root stdout' in page['text'] and 'owned root stderr' in page['text']
        assert final['result']['cleanupProof']['ownedTree']['confirmed'] is True
        assert final['result']['cleanupProof']['ownedTree']['remaining']==[]
        from packages.core.platform.execution_host import supervisor_exit_evidence
        wait_value(lambda:supervisor_exit_evidence(final['supervisorIdentity']),lambda proof:proof['confirmed'])
        launches=[];cold=ExecutionHost(ExecutionStore(root),launch=lambda *args:launches.append(args))
        assert cold.get_execution(final['effectId'])==final
        assert cold.submit_execution(request)==final and cold.submit_execution({**request,'retry':True})==final
        assert launches==[]
    finally:
        for h in handles:
            if wait_state(h) is not ProcessStatus.DEAD:kill(h)
            wait_value(lambda:wait_state(h),lambda s:s is ProcessStatus.DEAD)
            close_handle_checked(h)


def check_controller_loss(tmp_path,runtime_profile='base',blocked=False):
    import subprocess
    from tests.test_job_lifecycle_prevention import retain,kill,desktop_gone
    from packages.core.terminal.identity import probe_process
    runtime=sys._base_executable
    if runtime_profile=='venv':
        venv.EnvBuilder(with_pip=False,system_site_packages=True).create(tmp_path/'canonical-venv')
        runtime=str(tmp_path/'canonical-venv/Scripts/python.exe')
    root=tmp_path/'canonical-executions';helper=Path(__file__).with_name('canonical_controller_host.py')
    from packages.jobs.window import command_window_options
    controller=subprocess.Popen([sys.executable,str(helper),str(root),str(tmp_path),runtime,'blocked' if blocked else 'published'],
        stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,**command_window_options())
    birth=read_creation_filetime(int(controller._handle));handles=[];supervisor=None
    try:
        wait_value(lambda:(tmp_path/'tree-0.json').exists(),bool)
        wait_value(lambda:(tmp_path/('spawn.json' if blocked else 'job.json')).exists(),bool)
        if blocked:assert not (tmp_path/'job.json').exists()
        request=json.loads((tmp_path/'request.json').read_text())
        observed=json.loads((tmp_path/'runner.json').read_text())
        store=ExecutionStore(root)
        from packages.jobs.independent_executor import effect_id
        doc=store.get(effect_id(request['effectKey']))
        assert doc['supervisorIdentity']['pid']==observed['runnerPid']
        assert doc['supervisorIdentity']['birth']==str(observed['runnerFiletime'])
        supervisor=retain(observed['runnerPid'],observed['runnerFiletime'])
        handles.append(supervisor)
        if runtime_profile=='venv':
            wrapper=retain(observed['runnerParent'],observed['runnerParentFiletime']);handles.append(wrapper)
            assert observed['runnerPid']!=observed['runnerParent']
            import psutil
            assert Path(psutil.Process(observed['runnerParent']).exe()).resolve()==Path(runtime).resolve()
        rows=[json.loads((tmp_path/('tree-'+str(i)+'.json')).read_text()) for i in [2,1,0]]
        handles.extend(retain(row['pid'],row['creationFiletime']) for row in rows)
        assert all(row['desktop'].startswith('PanJob_') for row in rows)
        assert read_creation_filetime(int(controller._handle))==birth
        kill(int(controller._handle));controller.wait(timeout=5)
        assert read_creation_filetime(int(controller._handle))==birth
        assert all(wait_state(h) is ProcessStatus.ALIVE for h in handles)
        cold=ExecutionStore(root);cold.recover(supervisor_alive)
        assert cold.get(doc['effectId'])==doc and doc['result'] is None
        host=ExecutionHost(cold,launch=lambda *args:(_ for _ in ()).throw(AssertionError('original must not relaunch')))
        assert host.submit_execution(request)==doc
        result=host.cleanup_execution(doc['effectId'],doc['attempt'])
        assert result['cleanupRequest']['status']=='requested'
        final=wait_value(lambda:cold.get(doc['effectId']),lambda d:d.get('cleanupRequest',{}).get('status')=='confirmed' and d['result'] is not None)
        assert final['result']['effectKey']==doc['effectKey']
        assert final['cleanupRequest']['proof']['confirmed'] is True
        assert final['cleanupRequest']['proof']['ownedTree']['remaining']==[]
        wait_value(lambda:[wait_state(h) for h in handles],lambda states:all(s is ProcessStatus.DEAD for s in states))
        wait_value(lambda:desktop_gone(rows[0]['desktop']),bool)
        assert host.get_execution(doc['effectId'])==final
        assert host.submit_execution(request)==final
    finally:
        if controller.poll() is None:kill(int(controller._handle));controller.wait(timeout=5)
        controller._handle.Close()
        if controller.stderr:controller.stderr.close()
        for handle in set(handles):
            if wait_state(handle) is not ProcessStatus.DEAD:kill(handle)
            wait_value(lambda:wait_state(handle),lambda s:s is ProcessStatus.DEAD)
            close_handle_checked(handle)
