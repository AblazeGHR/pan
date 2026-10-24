"""Deterministic deletion barriers; only owned synthetic process identities."""
import concurrent.futures
import threading
import time
from types import SimpleNamespace

import psutil
import pytest

from packages.core import background_jobs as jobs
from packages.core.terminal.contracts import ProcessStatus
from packages.jobs import process_cleanup as cleanup


class Process:
    def __init__(self, pid):
        self.pid = pid
        self.dead = False
        self.child = []
        self.error = None
    def create_time(self):
        return float(self.pid)
    def children(self, recursive=True):
        if self.error:
            raise self.error
        return self.child
    def kill(self):
        self.dead = True
    def is_running(self):
        return not self.dead
    def status(self):
        return 'running'


@pytest.fixture
def tree(monkeypatch):
    monkeypatch.setattr(cleanup, 'os', SimpleNamespace(name='nt'))
    processes = {pid: Process(pid) for pid in (10, 11, 12)}
    processes[11].child = [processes[10], processes[12]]
    processes[10].child = [processes[12]]
    held = {}
    class Held:
        def __init__(self, pid):
            self.pid, self.handle, self.filetime = pid, pid, pid*100
        def close(self):
            return True
        def probe(self):
            return SimpleNamespace(status=ProcessStatus.DEAD if processes[self.pid].dead else ProcessStatus.ALIVE)
    def capture(pid, expected=None, filetime=None):
        proc = processes[pid]
        assert proc.create_time() == expected
        held[pid] = Held(pid)
        return proc, held[pid]
    class Guard:
        last_terminate_error = None
        def is_member(self, h):
            return h in (10, 12)
        def close(self):
            return True
        def terminate_tree(self, pid, timeout):
            processes[10].dead = processes[12].dead = True
            return [10,12], []
    guard = Guard()
    monkeypatch.setattr(cleanup, '_capture_process', capture)
    monkeypatch.setattr(cleanup, '_open_task_guard', lambda *args: guard)
    monkeypatch.setattr(psutil, 'Process', lambda pid: processes[pid])
    from packages.core.terminal import identity
    def kill(pid, ft, **kw):
        processes[pid].dead = True
        return SimpleNamespace(killed=True, signaled_after_terminate=True)
    monkeypatch.setattr(identity, 'kill_verified', kill)
    record = dict(jobId='job_delete_safety', kind='background-process', status='running',
                  pid=10, processCreatedAt=10., runnerPid=11, runnerProcessCreatedAt=11.)
    return processes, guard, record


@pytest.mark.parametrize('fault', ['children_oserror','children_denied','birth','kill','missing_pid','late_child'])
def test_unobservable_tree_never_confirmed(monkeypatch, tree, fault):
    processes, guard, record = tree
    if fault == 'children_oserror':
        processes[11].error = OSError('enumeration unavailable')
    elif fault == 'children_denied':
        processes[11].error = psutil.AccessDenied(11)
    elif fault == 'birth':
        processes[12].create_time = lambda: (_ for _ in ()).throw(OSError('birth unreadable'))
    elif fault == 'kill':
        guard.last_terminate_error = 5
        guard.terminate_tree = lambda *a, **k: ([10,12], [12])
        processes[12].kill = lambda: (_ for _ in ()).throw(OSError('kill denied'))
    elif fault == 'missing_pid':
        record.pop('pid')
    else:
        guard.terminate_tree = lambda *a, **k: ([10,12,13], [13])
    try:
        receipt = jobs._terminate_job_processes([record], timeout=0)
    except Exception:
        return
    assert receipt.get('confirmed') is not True


def test_all_roots_captured_before_guard_effect_and_late_children_caught(monkeypatch, tree):
    processes, guard, record = tree
    def terminate(pid, timeout):
        # Both roots and every observed descendant are still alive at capture.
        assert not any(proc.dead for proc in processes.values())
        processes[10].dead = processes[12].dead = True
        return [10,12,13], []  # new member inherited Job ownership; Job now empty
    guard.terminate_tree = terminate
    proofs=[]
    record['_cleanupPersist']=proofs.append
    assert jobs._terminate_job_processes([record])['confirmed']
    assert len(proofs[0]['cleanupMembers']) == 3
    assert proofs[1] == {'cleanupTreeEnded': True}


def test_pending_recheck_does_not_repeat_effects_after_confirmed_exit(monkeypatch, tree):
    processes, guard, record = tree
    record.update(cleanupTreeEnded=True, cleanupMembers=[{'pid':p,'birth':float(p)} for p in processes])
    for proc in processes.values():
        proc.dead=True
    monkeypatch.setattr(cleanup, '_capture_process', lambda *a: pytest.fail('must not recapture/replay'))
    assert jobs._terminate_job_processes([record])['confirmed']


def test_delete_failure_recheck_and_runner_writeback(monkeypatch, tree):
    _, _, record = tree
    jobs._save(record)
    monkeypatch.setattr(jobs, '_terminate_job_processes', lambda *a: {'confirmed':False,'reason':'unknown'})
    with pytest.raises(ValueError, match='unknown'):
        jobs.delete_job(record['jobId'])
    jobs.runner_update(record['jobId'], status='completed', exitCode=0)
    assert jobs.get(record['jobId'])['status'] == 'cleanup_pending'
    assert jobs.reconcile_running() == 0
    monkeypatch.setattr(jobs, '_terminate_job_processes', lambda *a: {'confirmed':True})
    assert jobs.delete_job(record['jobId'])
    with pytest.raises(ValueError,match='not found'):
        jobs.runner_update(record['jobId'], status='completed')
    assert not jobs.delete_job(record['jobId'])


def test_concurrent_delete_serializes_and_unrelated_job_not_blocked(monkeypatch, tree):
    _, _, record=tree
    jobs._save(record)
    entered, release=threading.Event(), threading.Event()
    calls=[]
    def cleanup_effect(*a):
        calls.append(1); entered.set()
        assert release.wait(3)
        return {'confirmed':True}
    monkeypatch.setattr(jobs,'_terminate_job_processes',cleanup_effect)
    with concurrent.futures.ThreadPoolExecutor(3) as pool:
        first=pool.submit(jobs.delete_job,record['jobId'])
        assert entered.wait(3)
        second=pool.submit(jobs.delete_job,record['jobId'])
        unrelated=pool.submit(jobs._save,{'jobId':'job_unrelated','status':'completed'})
        assert unrelated.result(timeout=1)
        release.set()
        assert first.result(timeout=3)
        assert second.result(timeout=3) is False
    assert calls == [1]


def test_spawn_denied_after_delete_intent(monkeypatch, tree):
    _, _, record=tree
    record['status']='cleanup_pending'
    jobs._save(record)
    with pytest.raises(ValueError,match='no longer admitted'):
        jobs.runner_spawn(record['jobId'],lambda: pytest.fail('must not spawn'))
    with pytest.raises(ValueError,match='no longer admitted'):
        jobs._spawn_background_runner(record)


@pytest.mark.parametrize('parent_status,child_status', [('running','running'),('scheduled','running'),('scheduled','cleanup_pending')])
def test_scheduled_parent_keeps_active_child_and_no_resurrection(monkeypatch, tree,parent_status,child_status):
    _, _, child=tree
    child['scheduledParentJobId']='job_parent'
    child['status']=child_status
    parent={'jobId':'job_parent','kind':'scheduled-task','status':parent_status,'action':{'api':'shell'}}
    jobs._save(parent);jobs._save(child)
    with pytest.raises(ValueError,match='active dispatch'):
        jobs.delete_job('job_parent')
    assert jobs.get(child['jobId'])


def test_legacy_unguarded_tree_is_not_proof():
    record={}
    # Actual implementation guard acquisition refuses absent ownership before
    # trying any native handle duplication, even if process snapshot looked sane.
    with pytest.raises(ValueError,match='no provable'):
        cleanup._open_task_guard(record,None,None)


def test_explicit_recheck_finishes_runner_only_after_durable_tree_proof(monkeypatch, tree):
    processes, guard, record=tree
    processes[10].dead=processes[12].dead=True
    record.update(cleanupTreeEnded=True, cleanupMembers=[
        {'pid':p,'birth':float(p),'filetime':str(p*100)} for p in processes])
    monkeypatch.setattr(guard,'terminate_tree',lambda *a,**kw: pytest.fail('child effects must not replay'))
    assert jobs._terminate_job_processes([record])['confirmed']
    assert processes[11].dead


def test_uncertain_runner_kill_keeps_durable_tree_proof(monkeypatch, tree):
    processes, guard, record=tree
    jobs._save(record)
    from packages.core.terminal import identity
    monkeypatch.setattr(identity,'kill_verified',lambda *a,**kw: SimpleNamespace(killed=False,signaled_after_terminate=False))
    with pytest.raises(ValueError, match='runner termination'):
        jobs.delete_job(record['jobId'])
    saved=jobs.get(record['jobId'])
    assert saved['status']=='cleanup_pending' and saved['cleanupTreeEnded']
    assert len(saved['cleanupMembers'])==3
    # Simulate runner completion while service was absent, then reread only the
    # durable JSON (no original handles or process-local cleanup registry).
    processes[11].dead=True
    assert jobs.delete_job(record['jobId'])


def test_scheduled_terminal_child_writeback_does_not_recreate_deleted_parent(monkeypatch, tree):
    _, _, child=tree
    child.update(status='completed',scheduledParentJobId='job_parent',dispatchKey='once',exitCode=0)
    parent={'jobId':'job_parent','kind':'scheduled-task','status':'scheduled','action':{'api':'shell'}}
    jobs._save(parent);jobs._save(child)
    assert jobs.delete_job('job_parent')
    jobs.runner_update(child['jobId'],status='completed',exitCode=0)
    jobs._reconcile_scheduled_processes(jobs._root())
    assert jobs.get('job_parent') is None


def test_zero_tolerance_and_raw_filetime_precedence(monkeypatch):
    from packages.core.terminal.win_pipe import ProcessIdentityHandle
    proc=Process(10)
    monkeypatch.setattr(psutil,'Process',lambda pid:proc)
    held=SimpleNamespace(filetime=123,probe=lambda:SimpleNamespace(status=ProcessStatus.ALIVE),close=lambda:True)
    monkeypatch.setattr(ProcessIdentityHandle,'open',lambda pid:held)
    with pytest.raises(ValueError,match='zero tolerance'):
        cleanup._capture_process(10,10.00001)
    assert cleanup._capture_process(10,10.00001,'123')[1] is held
    with pytest.raises(ValueError,match='FILETIME'):
        cleanup._capture_process(10,10.,'124')


@pytest.mark.parametrize('state',[ProcessStatus.UNKNOWN,ProcessStatus.DEAD])
def test_capture_unknown_or_exited_handle_cannot_prove_tree(monkeypatch,state):
    from packages.core.terminal.win_pipe import ProcessIdentityHandle
    monkeypatch.setattr(psutil,'Process',lambda pid:Process(pid))
    held=SimpleNamespace(filetime=123,probe=lambda:SimpleNamespace(status=state),close=lambda:True)
    monkeypatch.setattr(ProcessIdentityHandle,'open',lambda pid:held)
    with pytest.raises(ValueError,match='not observably alive'):
        cleanup._capture_process(10,10.,'123')


def test_expired_root_without_tree_proof_retains_record(monkeypatch,tree):
    _,_,record=tree
    jobs._save(record)
    def gone(*args):
        raise psutil.NoSuchProcess(11)
    monkeypatch.setattr(cleanup,'_capture_process',gone)
    with pytest.raises(ValueError,match='cannot safely delete'):
        jobs.delete_job(record['jobId'])
    assert jobs.get(record['jobId'])['status']=='cleanup_pending'


def test_spawn_wait_does_not_block_unrelated_job(monkeypatch,tree):
    _,_,record=tree
    jobs._save(record)
    entered,release=threading.Event(),threading.Event()
    def factory():
        entered.set();assert release.wait(3)
        raise ValueError('test spawn rejected')
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        spawning=pool.submit(jobs.runner_spawn,record['jobId'],factory)
        assert entered.wait(3)
        assert pool.submit(jobs._save,{'jobId':'job_other','status':'completed'}).result(timeout=1)
        release.set()
        with pytest.raises(ValueError,match='test spawn rejected'):
            spawning.result(timeout=3)


@pytest.mark.parametrize('phase',['before','during_probe','during_kill'])
def test_cancel_cannot_turn_unconfirmed_delete_into_terminal(monkeypatch,tree,phase):
    processes,_,record=tree
    if phase=='before':record['status']='cleanup_pending'
    jobs._save(record)
    def mark():jobs._update(record['jobId'],{'status':'cleanup_pending'})
    def probe(data):
        if phase=='during_probe':mark()
        return processes[10]
    def kill(proc):
        if phase=='during_kill':mark()
    monkeypatch.setattr(jobs,'_owns_process',probe)
    monkeypatch.setattr(jobs,'_kill_tree',kill)
    with pytest.raises(ValueError,match='cleanup pending'):
        jobs.cancel(record['jobId'])
    assert jobs.get(record['jobId'])['status']=='cleanup_pending'
