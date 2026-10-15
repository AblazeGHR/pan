"""Deterministic death at owned creation edges; no models or deployed service."""
import ctypes
from ctypes import wintypes as w
import json, os, subprocess, sys, time
from pathlib import Path
import pytest
from packages.core import background_jobs as legacy
from packages.jobs.independent_service import JobStore, ServiceError
from packages.core.terminal.identity import read_creation_filetime, wait_state, ProcessStatus, close_handle_checked
pytestmark=pytest.mark.skipif(os.name!='nt',reason='Windows kernel Job lifecycle')


def until(check, timeout=5):
 end=time.monotonic()+timeout
 while not check():
  if time.monotonic()>=end:return False
  time.sleep(.01)
 return True


def desktop_gone(name):
 u=ctypes.WinDLL('user32',use_last_error=True)
 u.OpenDesktopW.argtypes=[w.LPCWSTR,w.DWORD,w.BOOL,w.DWORD];u.OpenDesktopW.restype=w.HANDLE
 u.CloseDesktop.argtypes=[w.HANDLE]
 handle=u.OpenDesktopW(name,0,False,1)
 if handle:
  assert u.CloseDesktop(handle)
  return False
 assert ctypes.get_last_error()==2
 return True


def retain(pid, filetime=None):
 k=ctypes.WinDLL('kernel32',use_last_error=True)
 k.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];k.OpenProcess.restype=w.HANDLE
 h=k.OpenProcess(0x101001,False,pid)
 assert h
 assert filetime is None or read_creation_filetime(h)==filetime
 return h


def kill(handle):
 k=ctypes.WinDLL('kernel32',use_last_error=True);k.TerminateProcess.argtypes=[w.HANDLE,w.UINT]
 assert k.TerminateProcess(handle,1)


@pytest.mark.parametrize('kind',['legacy','independent'])
@pytest.mark.parametrize('fault',['pre-import','post-create'])
@pytest.mark.parametrize('launch_mode',['base','venv-outer','venv-inner'])
def test_runner_death_at_creation_edge_converges_without_survivors(tmp_path,monkeypatch,kind,fault,launch_mode):
 from packages.jobs import independent_service
 root=tmp_path/'registry';gate=tmp_path/'gate.json';marker=tmp_path/'must-not-run'
 monkeypatch.setenv('PAN_BACKGROUND_JOBS_DIR',str(root))
 monkeypatch.setattr(legacy._sessions,'get',lambda sid:{'id':sid})
 helper=Path(__file__).parent/'helpers/lifecycle_fault_runner.py'
 repo=Path(__file__).resolve().parents[1]
 monkeypatch.setattr(legacy,'PROJECT_ROOT',repo)
 runtime=sys.executable
 if launch_mode!='base':
  import venv
  venv.EnvBuilder(with_pip=False,system_site_packages=True).create(tmp_path/'venv')
  runtime=str(tmp_path/'venv/Scripts/python.exe')
 original_popen=subprocess.Popen; runners=[]
 def spawn(command,**kwargs):
  job_id=command[command.index('--job-id')+1]
  command=[runtime,str(helper.resolve()),'--kind',kind,'--fault',fault,'--root',str(root),'--job-id',job_id,'--evidence',str(gate)]
  proc=original_popen(command,**kwargs);runners.append(proc);return proc
 monkeypatch.setattr(subprocess,'Popen',spawn)
 argv=[sys.executable,'-c','from pathlib import Path;Path('+repr(str(marker))+').touch()']
 store=JobStore(root) if kind=='independent' else None
 job=store.create({'argv':argv,'cwd':str(tmp_path),'targetSessionId':'owned'}) if store else legacy.start('owned',argv,str(tmp_path))
 child=None; runner=runners[0];runner_ft=read_creation_filetime(int(runner._handle))
 try:
  assert until(gate.exists)
  row=json.loads(gate.read_text())
  if fault=='post-create':child=retain(row['pid'],row['creationFiletime'])
  assert read_creation_filetime(int(runner._handle))==runner_ft
  inner=None
  if launch_mode=='venv-inner':
   inner=retain(row['runnerPid'],row['runnerFiletime'])
   try:kill(inner)
   finally:close_handle_checked(inner)
  else:kill(int(runner._handle))
  runner.wait(timeout=5)
  if store:
   restarted=JobStore(root);restarted.recover();fact=restarted.get(job['jobId'])
   assert fact['status']=='UNKNOWN'
   with pytest.raises(ServiceError,match='UNKNOWN'):restarted.retry(job['jobId'])
   from packages.jobs.independent_runner import run
   assert run(root,job['jobId'])==3
  else:
   assert legacy.reconcile_running(root)==1
   fact=legacy.get(job['jobId']);assert fact['status'] not in ('starting','running')
   assert legacy.reconcile_running(root)==0
  dead=child is None or until(lambda:wait_state(child) is ProcessStatus.DEAD,1)
  target=os.environ.get('PAN_TEST_LIFECYCLE_EVIDENCE')
  if target:
   with Path(target).open('a',encoding='utf8') as f:f.write(json.dumps(dict(kind=kind,fault=fault,launchMode=launch_mode,runnerPid=runner.pid,runnerFiletime=str(runner_ft),child=row if child else None,retainedChildDead=dead,registryStatus=fact['status'],markerExecuted=marker.exists()))+'\n')
  assert not marker.exists()
  assert dead,'Owned suspended child survived runner death before caller receives CreateProcess result'
  if child:assert until(lambda:desktop_gone(row['desktop']))
 finally:
  if child:
   if wait_state(child) is not ProcessStatus.DEAD:kill(child)
   assert until(lambda:wait_state(child) is ProcessStatus.DEAD)
   close_handle_checked(child)
  if runner.poll() is None:kill(int(runner._handle));runner.wait(timeout=5)
  runner._handle.Close()


@pytest.mark.parametrize('kind', ['legacy', 'independent'])
@pytest.mark.parametrize('failure', ['root-exit', 'wait-error'])
def test_owned_multigeneration_tree_cleanup_on_exit_or_exception(tmp_path, monkeypatch, kind, failure):
    root = tmp_path / 'registry'
    monkeypatch.setenv('PAN_BACKGROUND_JOBS_DIR', str(root))
    monkeypatch.setattr(legacy._sessions, 'get', lambda sid: {'id': sid})
    monkeypatch.setattr(legacy, 'PROJECT_ROOT', Path(__file__).resolve().parents[1])
    helper = Path(__file__).parent / 'helpers/lifecycle_fault_runner.py'
    tree = Path(__file__).parent / 'helpers/lifecycle_tree_probe.py'
    gate = tmp_path / 'gate.json'
    original_popen = subprocess.Popen
    runners = []
    def spawn(command, **kwargs):
        if failure == 'wait-error':
            job_id = command[command.index('--job-id') + 1]
            command = [sys.executable, str(helper.resolve()), '--kind', kind, '--fault', failure,
                       '--root', str(root), '--job-id', job_id, '--evidence', str(gate)]
        proc = original_popen(command, **kwargs)
        runners.append(proc)
        return proc
    monkeypatch.setattr(subprocess, 'Popen', spawn)
    argv = [sys.executable, str(tree.resolve()), str(tmp_path), '2']
    store = JobStore(root) if kind == 'independent' else None
    job = (store.create({'argv': argv, 'cwd': str(tmp_path), 'targetSessionId': 'owned'})
           if store else legacy.start('owned', argv, str(tmp_path)))
    runner = runners[0]
    runner_ft = read_creation_filetime(int(runner._handle))
    handles = []
    get = lambda: store.get(job['jobId']) if store else legacy.get(job['jobId'])
    try:
        assert until(lambda: (tmp_path / 'tree-0.json').exists())
        rows = [json.loads((tmp_path / f'tree-{i}.json').read_text()) for i in (2, 1, 0)]
        handles = [retain(row['pid'], row['creationFiletime']) for row in rows]
        assert all(row['desktop'].startswith('PanJob_') for row in rows)
        if failure == 'root-exit':
            (tmp_path / 'root-release').touch()
        else:
            assert until(gate.exists)
            (tmp_path / 'fault-release').touch()
        runner.wait(timeout=8)
        assert read_creation_filetime(int(runner._handle)) == runner_ft
        assert until(lambda: all(wait_state(h) is ProcessStatus.DEAD for h in handles))
        assert until(lambda: desktop_gone(rows[0]['desktop']))
        fact = get()
        expected = ('completed' if failure == 'root-exit' else 'failed') if not store else 'UNKNOWN'
        assert fact['status'] == expected, fact
        if store:
            store.recover()
            with pytest.raises(ServiceError, match='UNKNOWN'): store.retry(job['jobId'])
        else:
            assert legacy.reconcile_running(root) == 0
        text = Path(fact['logPath']).read_text(encoding='utf8')
        if failure == 'wait-error':
            diagnostic = text + str(fact.get('error', ''))
            assert 'injected owned wait failure' in diagnostic
        else:
            assert 'owned root stdout' in text and 'owned root stderr' in text
        target = os.environ.get('PAN_TEST_LIFECYCLE_EVIDENCE')
        if target:
            with Path(target).open('a', encoding='utf8') as f:
                f.write(json.dumps(dict(kind=kind, fault=failure, registryStatus=fact['status'],
                                       rows=rows, retainedAllDead=True, runnerPid=runner.pid,
                                       runnerFiletime=str(runner_ft))) + '\n')
    finally:
        for h in handles:
            if wait_state(h) is not ProcessStatus.DEAD: kill(h)
        for h in handles:
            assert until(lambda: wait_state(h) is ProcessStatus.DEAD)
            close_handle_checked(h)
        if runner.poll() is None: kill(int(runner._handle)); runner.wait(timeout=5)
        runner._handle.Close()

@pytest.mark.parametrize('kind', ['legacy', 'independent'])
def test_job_attribute_failure_prevents_creation_and_deletes_list(tmp_path, monkeypatch, kind):
    from packages.jobs import owned_process, independent_runner
    from packages.jobs.desktop import DesktopCommand
    from packages.jobs.restricted_token import HiddenJobToken
    native = ctypes.WinDLL
    deleted = []
    created = []
    class RejectUpdate:
        def __call__(self, *args):
            ctypes.set_last_error(50)
            return False
    def library(name, *args, **kwargs):
        dll = native(name, *args, **kwargs)
        if name == 'kernel32':
            remove = dll.DeleteProcThreadAttributeList
            remove.argtypes = [ctypes.c_void_p]
            def delete(pointer):
                remove(pointer)
                deleted.append(True)
            dll.UpdateProcThreadAttribute = RejectUpdate()
            dll.DeleteProcThreadAttributeList = delete
        return dll
    monkeypatch.setattr(owned_process.ctypes, 'WinDLL', library)
    def must_not_create(*args):
        created.append(True)
        raise AssertionError('CreateProcess called despite unavailable Job creation attribute')
    monkeypatch.setattr(HiddenJobToken, 'create_process', must_not_create)
    if kind == 'independent':
        result = independent_runner._run_windows_task(tmp_path, 'owned', 'claim',
            {'argv': [sys.executable], 'cwd': str(tmp_path)}, dict(os.environ), tmp_path / 'log')
        assert result[:2] == ('failed', -50)
    else:
        with (tmp_path / 'log').open('ab') as log, pytest.raises(OSError) as caught:
            DesktopCommand([sys.executable], str(tmp_path), log)
        assert caught.value.winerror == 50
    assert deleted == [True]
    assert not created

@pytest.mark.parametrize('kind', ['legacy', 'independent'])
def test_controller_process_crash_keeps_owned_runner_then_restart_cancels(tmp_path, monkeypatch, kind):
    root = tmp_path / 'registry'
    monkeypatch.setenv('PAN_BACKGROUND_JOBS_DIR', str(root))
    helper = Path(__file__).parent / 'helpers/lifecycle_controller_host.py'
    tree = Path(__file__).parent / 'helpers/lifecycle_tree_probe.py'
    host = subprocess.Popen([sys.executable, str(helper.resolve()), kind, str(root),
                             str(tree.resolve()), str(tmp_path)], creationflags=subprocess.CREATE_NO_WINDOW,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    host_ft = read_creation_filetime(int(host._handle))
    handles = []
    runner = None
    try:
        assert until(lambda: (tmp_path / 'tree-0.json').exists())
        rows = [json.loads((tmp_path / f'tree-{i}.json').read_text()) for i in (2, 1, 0)]
        handles = [retain(row['pid'], row['creationFiletime']) for row in rows]
        job = json.loads((tmp_path / 'job.json').read_text())
        assert read_creation_filetime(int(host._handle)) == host_ft
        kill(int(host._handle)); host.wait(timeout=5)
        if kind == 'independent':
            store = JobStore(root); store.recover(); fact = store.get(job['jobId'])
            runner = retain(fact['runnerPid'], int(fact['runnerFiletime']))
            assert fact['status'] == 'running'
            assert wait_state(runner) is ProcessStatus.ALIVE
            assert all(wait_state(h) is ProcessStatus.ALIVE for h in handles)
            store.request_cancel(job['jobId'])
            get = lambda: store.get(job['jobId'])
        else:
            assert legacy.reconcile_running(root) == 0
            fact = legacy.get(job['jobId'])
            runner = retain(fact['runnerPid'])
            expected = fact['runnerProcessCreatedAt']
            assert abs(read_creation_filetime(runner)/10_000_000-11644473600-expected) < .01
            assert fact['status'] == 'running'
            assert wait_state(runner) is ProcessStatus.ALIVE
            assert all(wait_state(h) is ProcessStatus.ALIVE for h in handles)
            legacy.cancel(job['jobId'])
            get = lambda: legacy.get(job['jobId'])
        assert until(lambda: get()['status'] == 'cancelled')
        assert until(lambda: all(wait_state(h) is ProcessStatus.DEAD for h in handles))
        assert until(lambda: wait_state(runner) is ProcessStatus.DEAD)
        assert until(lambda: desktop_gone(rows[0]['desktop']))
        target = os.environ.get('PAN_TEST_LIFECYCLE_EVIDENCE')
        if target:
            with Path(target).open('a', encoding='utf8') as f:
                f.write(json.dumps(dict(kind=kind, fault='controller-crash-restart', hostPid=host.pid,
                                       hostFiletime=str(host_ft), registryStatus=get()['status'], rows=rows,
                                       retainedAllDead=True, runnerDead=True)) + '\n')
    finally:
        if host.poll() is None: kill(int(host._handle)); host.wait(timeout=5)
        host._handle.Close()
        if runner:
            if wait_state(runner) is not ProcessStatus.DEAD: kill(runner)
        for handle in handles:
            if wait_state(handle) is not ProcessStatus.DEAD: kill(handle)
            assert until(lambda: wait_state(handle) is ProcessStatus.DEAD)
            close_handle_checked(handle)
        if runner: close_handle_checked(runner)


def test_legacy_runner_registers_itself_if_controller_dies_before_pid_publication(tmp_path, monkeypatch):
    root = tmp_path / 'registry'
    monkeypatch.setenv('PAN_BACKGROUND_JOBS_DIR', str(root))
    helper = Path(__file__).parent / 'helpers/lifecycle_controller_host.py'
    tree = Path(__file__).parent / 'helpers/lifecycle_tree_probe.py'
    host = subprocess.Popen([sys.executable, str(helper.resolve()), 'legacy', str(root),
                             str(tree.resolve()), str(tmp_path), 'post-spawn'],
                            creationflags=subprocess.CREATE_NO_WINDOW,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    host_ft = read_creation_filetime(int(host._handle))
    runner = None
    handles = []
    try:
        assert until(lambda: (tmp_path / 'tree-0.json').exists())
        spawn = json.loads((tmp_path / 'spawn.json').read_text())
        runner = retain(spawn['pid'], spawn['creationFiletime'])
        rows = [json.loads((tmp_path / f'tree-{i}.json').read_text()) for i in (2, 1, 0)]
        handles = [retain(row['pid'], row['creationFiletime']) for row in rows]
        assert read_creation_filetime(int(host._handle)) == host_ft
        kill(int(host._handle)); host.wait(timeout=5)
        assert legacy.reconcile_running(root) == 0, 'Live owned runner lost because parent never published its PID'
        fact = legacy.get(spawn['jobId'])
        assert fact['status'] == 'running'
        assert fact['runnerPid'] == spawn['pid']
        legacy.cancel(spawn['jobId'])
        assert until(lambda: all(wait_state(h) is ProcessStatus.DEAD for h in handles))
        assert until(lambda: wait_state(runner) is ProcessStatus.DEAD)
        assert legacy.get(spawn['jobId'])['status'] == 'cancelled'
    finally:
        if host.poll() is None: kill(int(host._handle)); host.wait(timeout=5)
        host._handle.Close()
        if runner and wait_state(runner) is not ProcessStatus.DEAD: kill(runner)
        for handle in handles:
            if wait_state(handle) is not ProcessStatus.DEAD: kill(handle)
            assert until(lambda: wait_state(handle) is ProcessStatus.DEAD)
            close_handle_checked(handle)
        if runner: close_handle_checked(runner)
