"""Original real Windows process proofs through the canonical execution port."""
import ctypes
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace

import pytest
from packages.core.terminal.identity import open_process_for_probe, read_creation_filetime, wait_state, ProcessStatus, close_handle_checked

pytestmark = pytest.mark.skipif(os.name != "nt", reason="real Windows desktop lifecycle")
PROBE = Path(__file__).parent / "helpers/desktop_tree_probe.py"


def receipt(case, facts):
    """Optional compact audit output; never writes runtime databases to Git."""
    target = os.environ.get("PAN_TEST_JOB_TRIAL_EVIDENCE")
    if target:
        with Path(target).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"case": case, **facts}) + "\n")


def brief(rows):
    return [{key: r[key] for key in ("pid", "creationFiletime", "desktop", "inputDesktop", "gui", "console") if key in r} for r in rows]


def until(check, timeout=12):
    end = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < end, "owned test deadline"
        time.sleep(.02)


def released(name):
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.OpenDesktopW.argtypes = [w.LPCWSTR, w.DWORD, w.BOOL, w.DWORD]; user.OpenDesktopW.restype = w.HANDLE
    user.CloseDesktop.argtypes = [w.HANDLE]
    def gone():
        handle = user.OpenDesktopW(name, 0, False, 1)
        if handle: user.CloseDesktop(handle); return False
        assert ctypes.get_last_error() == 2
        return True
    until(gone, 5)


# Original case identities are retained, including former backend labels.
# Both labels now use the single canonical native execution port; no legacy
# Job writer or recovery loop is reopened.
from contextlib import contextmanager
from packages.jobs.independent_executor import ExecutionStore, ExecutionHost
from packages.core.platform.execution_host import supervisor_alive, supervisor_exit_evidence
from tests.test_job_final_independent_execution import attempt, key


def wait_value(read, predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = read()
        if predicate(value): return value
        time.sleep(.02)
    raise AssertionError('owned canonical execution deadline: ' + repr(value))


@pytest.fixture
def isolated_registry(tmp_path):
    return tmp_path / 'executions'


@contextmanager
def native_case(root, argv, cwd, *, label='owned'):
    store = ExecutionStore(root)
    host = ExecutionHost(store)
    request = {'jobId':'job_' + label, 'attempt':attempt(), 'effectKey':key(),
        'frozenPlan':{'operation':'process','arguments':{'argv':list(argv),
            'cwd':str(cwd),'hideWindow':True}}}
    original = host.submit_execution(request)
    read = lambda: store.get(original['effectId'])
    try:
        yield SimpleNamespace(store=store, host=host, request=request, read=read,
            identity=original['effectId'])
    finally:
        document = read()
        supervisor = document.get('supervisorIdentity')
        if supervisor and supervisor_alive(supervisor):
            if document['result'] is None:
                host.cleanup_execution(document['effectId'], document['attempt'])
            wait_value(lambda:supervisor_exit_evidence(supervisor),lambda p:p['confirmed'])


def terminal(case, exit_code):
    document = wait_value(case.read, lambda d:d['result'] is not None)
    result = document['result']
    assert result['receipt']['exitCode'] == exit_code
    assert result['outcome'] == ('succeeded' if exit_code == 0 else 'rejected')
    assert result['cleanupProof']['confirmed'] is True
    assert result['cleanupProof']['ownedTree']['confirmed'] is True
    assert result['cleanupProof']['ownedTree']['remaining'] == []
    wait_value(lambda:supervisor_exit_evidence(document['supervisorIdentity']),lambda p:p['confirmed'])
    return document


def no_replay(case, original):
    launches = []
    reopened = ExecutionHost(ExecutionStore(case.store.root), launch=lambda *args:launches.append(args))
    assert reopened.get_execution(case.identity) == original
    assert reopened.submit_execution(case.request) == original
    assert reopened.submit_execution({**case.request,'retry':True}) == original
    assert launches == [] and case.read() == original


def assert_desktop_tree(rows):
    assert len({row['desktop'] for row in rows}) == 1
    for row in rows:
        assert row['desktop'] != row['inputDesktop'] and row['inputOwnedWindows'] == []
        assert row['gui']['creationFiletime'] == row['creationFiletime']
        if row.get('console'): assert row['console']['desktop'] == row['desktop']


def retain(row):
    handle = open_process_for_probe(row['pid'])
    assert handle and read_creation_filetime(handle) == row['creationFiletime']
    return handle


def test_legacy_scheduled_shell_uses_real_runner(isolated_registry, tmp_path):
    # The old shell payload only encoded this argv. Execute the same program
    # through the typed process Action, without restoring a shell dispatcher.
    with native_case(isolated_registry,[sys.executable,str(PROBE.resolve()),'2','new',str(tmp_path)],tmp_path) as case:
        final = terminal(case,0)
        rows = [json.loads((tmp_path/f'{depth}.json').read_text(encoding='utf-8')) for depth in range(3)]
        assert_desktop_tree(rows)
        released(rows[0]['desktop'])
        no_replay(case, final)


@pytest.mark.parametrize('kind',['independent','legacy'])
@pytest.mark.parametrize('mode',['new','alloc'])
def test_production_three_generation_console_and_gui(isolated_registry,tmp_path,kind,mode):
    with native_case(isolated_registry,[sys.executable,str(PROBE.resolve()),'2',mode,str(tmp_path)],tmp_path,label=kind) as case:
        final = terminal(case,0)
        rows = [json.loads((tmp_path/f'{depth}.json').read_text(encoding='utf-8')) for depth in range(3)]
        assert_desktop_tree(rows)
        page = case.host.read_execution_log(case.identity,final['attempt'])
        assert page['text'].count('probe') == 3
        released(rows[0]['desktop'])
        no_replay(case,final)
        receipt(kind+'-'+mode,{'effectId':case.identity,'result':final['result'],'rows':brief(rows),'desktopReleased':True})


@pytest.mark.parametrize('kind',['independent','legacy'])
def test_production_stdout_exit_and_bidirectional_file_ipc(isolated_registry,tmp_path,kind):
    cwd = tmp_path / 'owned 中文 cwd with spaces'
    cwd.mkdir()
    program = """import os,sys,json,time
from pathlib import Path
print('owned stdout',flush=True)
print('owned stderr',file=sys.stderr,flush=True)
Path('request.tmp').write_text('child-request')
os.replace('request.tmp','request.txt')
end=time.monotonic()+8
while not Path('reply.txt').exists():
 assert time.monotonic()<end
 time.sleep(.01)
assert Path('reply.txt').read_text()=='parent-reply'
Path('cwd.json').write_text(json.dumps(os.getcwd()))
sys.exit(7)
"""
    with native_case(isolated_registry,[sys.executable,'-c',program],cwd,label=kind) as case:
        wait_value(lambda:(cwd/'request.txt').exists(),bool)
        assert (cwd/'request.txt').read_text() == 'child-request'
        (cwd/'reply.tmp').write_text('parent-reply')
        os.replace(cwd/'reply.tmp',cwd/'reply.txt')
        final = terminal(case,7)
        assert json.loads((cwd/'cwd.json').read_text()) == str(cwd)
        page = case.host.read_execution_log(case.identity,final['attempt'])
        assert 'owned stdout' in page['text'] and 'owned stderr' in page['text']
        no_replay(case,final)


@pytest.mark.parametrize('kind',['independent','legacy'])
@pytest.mark.parametrize('policy',['shutdown','deadline'])
def test_production_shutdown_recovery_then_cancel(isolated_registry,tmp_path,kind,policy):
    with native_case(isolated_registry,[sys.executable,str(PROBE.resolve()),'1','hold',str(tmp_path)],tmp_path,label=kind) as case:
        wait_value(lambda:(tmp_path/'child.json').exists(),bool)
        rows = [json.loads((tmp_path/f'{name}.json').read_text(encoding='utf-8')) for name in ('hold','child')]
        handles = [retain(row) for row in rows]
        try:
            original = case.read()
            assert original['result'] is None and supervisor_alive(original['supervisorIdentity'])
            if policy == 'deadline': time.sleep(.1)  # Original APIs had no native timeout option.
            no_replay(case,original)  # Cold lookup/admission cannot kill or recreate live work.
            requested = case.host.cleanup_execution(case.identity,original['attempt'])
            assert requested['cleanupRequest']['status'] == 'requested'
            final = wait_value(case.read,lambda d:d.get('cleanupRequest',{}).get('status')=='confirmed')
            assert final['cleanupRequest']['proof']['confirmed'] is True
            assert final['attempt'] == original['attempt'] and final['effectKey'] == original['effectKey']
            wait_value(lambda:[wait_state(h) for h in handles],lambda states:all(s is ProcessStatus.DEAD for s in states))
            wait_value(lambda:supervisor_exit_evidence(original['supervisorIdentity']),lambda p:p['confirmed'])
            released(rows[0]['desktop'])
        finally:
            for handle in handles: close_handle_checked(handle)


def crash_original_supervisor(root,tmp_path,label):
    with native_case(root,[sys.executable,str(PROBE.resolve()),'1','hold',str(tmp_path)],tmp_path,label=label) as case:
        wait_value(lambda:(tmp_path/'child.json').exists(),bool)
        rows = [json.loads((tmp_path/f'{name}.json').read_text(encoding='utf-8')) for name in ('hold','child')]
        handles = [retain(row) for row in rows]
        original = case.read()
        supervisor = original['supervisorIdentity']
        kernel = ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];kernel.OpenProcess.restype=w.HANDLE
        kernel.TerminateProcess.argtypes=[w.HANDLE,w.UINT];kernel.TerminateProcess.restype=w.BOOL
        runner = kernel.OpenProcess(0x101001,False,supervisor['pid'])
        try:
            assert runner and supervisor['kind']=='filetime'
            assert str(read_creation_filetime(runner)) == supervisor['birth']
            assert kernel.TerminateProcess(runner,1)
            wait_value(lambda:wait_state(runner),lambda status:status is ProcessStatus.DEAD)
            wait_value(lambda:[wait_state(h) for h in handles],lambda states:all(s is ProcessStatus.DEAD for s in states))
            assert supervisor_exit_evidence(supervisor)['confirmed'] is True
            # No native terminal receipt exists: retain the original document,
            # never invent failed/success, reclaim it or replay the command.
            after = case.read()
            assert after['result'] is None and after['attempt'] == original['attempt']
            assert after['effectKey'] == original['effectKey']
            no_replay(case,after)
            released(rows[0]['desktop'])
        finally:
            if runner: close_handle_checked(runner)
            for handle in handles: close_handle_checked(handle)


def test_legacy_runner_crash_recovery_and_cleanup(isolated_registry,tmp_path):
    crash_original_supervisor(isolated_registry,tmp_path,'legacy')


def test_independent_runner_crash_recovery_and_cleanup(isolated_registry,tmp_path):
    crash_original_supervisor(isolated_registry,tmp_path,'independent')
