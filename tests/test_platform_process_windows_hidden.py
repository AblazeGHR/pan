"""Real Windows ProcessOwner migration proofs; no deployed data or services."""
from contextlib import closing
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='real Windows desktop and atomic Job creation')
PROBE = Path(__file__).parent / 'helpers/process_owner_hidden_probe.py'


def until(check, timeout=10):
    end = time.monotonic() + timeout
    while not check():
        if time.monotonic() >= end:
            return False
        time.sleep(.01)
    return True


def proof(name, **facts):
    directory = Path('evidence/cp-x2-windows-hidden-20261028/proof')
    directory.mkdir(parents=True, exist_ok=True)
    payload = {'sourceSha': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
               'interpreter': sys.executable, 'version': sys.version, **facts}
    (directory / (name + '.json')).write_text(json.dumps(payload, indent=2), encoding='utf-8')


def input_desktop():
    from tests.helpers.desktop_tree_probe import input_desktop as open_input, desktop_name, u
    handle = open_input()
    try:
        return desktop_name(handle)
    finally:
        assert u.CloseDesktop(handle)


def rows(root):
    assert until(lambda: all((root / f'{i}.json').exists() for i in range(3)))
    return [json.loads((root / f'{i}.json').read_text()) for i in range(3)]


def retain(rows):
    from tests.test_job_lifecycle_prevention import retain
    return [retain(row['pid'], row['birth']) for row in rows]


def dead(handles):
    from packages.core.terminal.identity import wait_state, ProcessStatus
    return all(wait_state(h) is ProcessStatus.DEAD for h in handles)


def release_handles(handles):
    from packages.core.terminal.identity import close_handle_checked
    for handle in handles:
        assert close_handle_checked(handle)


def retain_business_root(owned, fact):
    """Prove the reported script lifetime is the root or its real descendant.

    Reuse the production read-only ancestor proof: retained handles, monotonic
    FILETIMEs and the exact owned root birth. Interpreter location does not
    determine whether Windows used a redirector.
    """
    from packages.jobs.window_guard import owned_descendant_handle
    from packages.core.terminal.identity import read_creation_filetime, close_handle_checked
    root_handle = int(owned.popen._handle)
    assert read_creation_filetime(root_handle) == int(owned.identity.birth)
    handle = owned_descendant_handle(owned.pid, root_handle, fact['pid'])
    assert handle, 'business PID must be the exact owned root or a proven descendant'
    try:
        assert read_creation_filetime(handle) == int(fact['birth']), 'business FILETIME mismatch'
        assert owned.guard.is_member(handle), 'business lifetime must belong to the exact owned Job'
        return handle
    except BaseException:
        assert close_handle_checked(handle)
        raise


@pytest.mark.parametrize('runtime', ['base', 'named-venv'])
def test_hidden_tree_identity_stdio_and_desktop_cleanup(tmp_path, runtime):
    from packages.core.platform.process import WindowsProcessOwner
    from tests.test_job_lifecycle_prevention import desktop_gone
    from tests.helpers.process_owner_hidden_probe import desktop
    original = desktop()
    active_input = input_desktop()
    owner = WindowsProcessOwner()
    interpreter = sys._base_executable if runtime == 'base' else sys.executable
    owned = owner.spawn([interpreter, str(PROBE), 'tree', str(tmp_path), '2'])
    handles = []
    try:
        facts = rows(tmp_path)
        handles = retain(facts)
        name = facts[0]['desktop']
        assert name.startswith('PanJob_') and name != original
        assert {row['desktop'] for row in facts} == {name}
        assert not desktop_gone(name)
        from packages.core.terminal.identity import read_creation_filetime
        assert int(owned.identity.birth) == read_creation_filetime(int(owned.popen._handle))
        handles.append(retain_business_root(owned, facts[2]))
        root_is_business = owned.pid == facts[2]['pid']
        if root_is_business:
            assert int(owned.identity.birth) == facts[2]['birth']
        # Even when a redirector exists, retain its original root lifetime too.
        handles += retain([{'pid': owned.pid, 'birth': int(owned.identity.birth)}])
        assert owned.guard.is_member(int(owned.popen._handle))
        assert set(row['pid'] for row in facts) <= set(owned.guard.member_pids())
        assert owner.kill_tree(owned).confirmed
        assert until(lambda: dead(handles))
        owned.close()
        assert until(lambda: desktop_gone(name))
        assert desktop() == original
        assert input_desktop() == active_input
        proof('kill-tree-' + runtime + ('-direct' if root_is_business else '-ancestor'),
              rootIsBusiness=root_is_business, businessBirthVerified=True, ancestryVerified=True,
              identity=owned.identity.as_dict(), members=facts,
              activeInputBefore=active_input, activeInputAfter=input_desktop(),
              allDead=dead(handles), desktopGone=desktop_gone(name))
    finally:
        owned.close()
        release_handles(handles)


def test_normal_exit_env_cwd_pipes_merge_and_private_desktop(tmp_path):
    from packages.core.platform.process import WindowsProcessOwner
    from tests.test_job_lifecycle_prevention import desktop_gone
    owner = WindowsProcessOwner()
    code = 'import os,sys; print(os.getcwd()); print(os.environ["CPX2_VALUE"]); print(sys.stdin.readline().strip()); print("stderr",file=sys.stderr,flush=True)'
    owned = owner.spawn([sys.executable, '-u', '-c', code], cwd=tmp_path,
        env={**os.environ, 'CPX2_VALUE': 'owned-\u4e2d\u6587', 'PYTHONIOENCODING': 'utf-8'}, stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    name = owned.popen.desktop.name
    try:
        output, error = owned.popen.communicate(b'input-value\n', timeout=10)
        text = output.decode(errors='replace')
        assert str(tmp_path).lower() in text.lower()
        assert 'owned-\u4e2d\u6587' in text and 'input-value' in text and 'stderr' in text
        assert error is None and owned.popen.returncode == 0
        assert owner.wait(owned, timeout=1).exit_code == 0
    finally:
        owned.close()
    assert until(lambda: desktop_gone(name))
    proof('normal-exit', identity=owned.identity.as_dict(), desktop=name,
          desktopGone=desktop_gone(name), exitCode=owned.popen.returncode,
          mergedStdio=True, unicodeEnv=True, cwd=True)


def test_visible_intent_does_not_allocate_hidden_desktop(tmp_path):
    from packages.core.platform.process import WindowsProcessOwner
    from tests.helpers.process_owner_hidden_probe import desktop
    owner = WindowsProcessOwner()
    with closing(owner.spawn([sys._base_executable, str(PROBE), 'tree', str(tmp_path), '0'], hide_window=False)) as owned:
        assert until(lambda: (tmp_path / '0.json').exists())
        fact = json.loads((tmp_path / '0.json').read_text())
        assert fact['desktop'] == desktop()
        assert owned.popen.desktop is None
        (tmp_path / 'release').touch()
        assert owner.wait(owned, timeout=10).exit_code == 0


def test_atomic_membership_precedes_resume_and_no_late_assign(tmp_path, monkeypatch):
    from packages.core.platform.process import WindowsProcessOwner
    from packages.core.platform.restricted_token_win import HiddenJobToken
    from packages.core.terminal.guard import JobObjectGuard
    from packages.core.terminal.identity import read_creation_filetime
    marker = tmp_path / 'executed'
    original = HiddenJobToken.create_process
    proofs = []
    def create(token, *args):
        process, thread, pid = original(token, *args)
        assert not marker.exists()
        # This callback runs inside native creation, before ownership return or resume.
        assert guards[0].is_member(process)
        proofs.append((pid, read_creation_filetime(process)))
        return process, thread, pid
    guards = []
    original_init = JobObjectGuard.__init__
    def init(guard, *a, **kw):
        original_init(guard, *a, **kw)
        guards.append(guard)
    monkeypatch.setattr(JobObjectGuard, '__init__', init)
    monkeypatch.setattr(HiddenJobToken, 'create_process', create)
    def forbidden(*args):
        pytest.fail('post-create assignment must never authorize an owned command')
    monkeypatch.setattr(JobObjectGuard, 'assign', forbidden)
    owner = WindowsProcessOwner()
    with closing(owner.spawn([sys.executable, '-c', 'from pathlib import Path; Path('+repr(str(marker))+').touch()'])) as owned:
        assert proofs == [(owned.pid, int(owned.identity.birth))]
        assert owner.wait(owned, timeout=10).exit_code == 0
        assert marker.exists()
        proof('atomic-membership', pid=owned.pid, birth=owned.identity.birth,
              capturedBeforeResume=proofs, lateAssignForbidden=True)


@pytest.mark.parametrize('stage', ['before-create', 'after-create', 'after-return'])
def test_creation_failure_closes_real_resources_without_execution(tmp_path, monkeypatch, stage):
    from packages.core.platform.process import WindowsProcessOwner
    from packages.core.platform.desktop_win import HiddenDesktop
    from packages.core.platform.restricted_token_win import HiddenJobToken
    from packages.core.terminal.guard import JobObjectGuard
    from tests.test_job_lifecycle_prevention import desktop_gone, retain
    from packages.core.terminal import identity
    desktops, handles, guards = [], [], []
    original_desktop, original_create = HiddenDesktop.__init__, HiddenJobToken.create_process
    original_guard = JobObjectGuard.__init__
    def desktop(obj):
        original_desktop(obj)
        desktops.append(obj)
    def guard(obj, *a, **kw):
        original_guard(obj, *a, **kw)
        guards.append(obj)
    marker = tmp_path / 'must-not-execute'
    def create(token, *args):
        if stage == 'before-create':
            raise OSError('injected before native create')
        process, thread, pid = original_create(token, *args)
        handles.append(retain(pid, identity.read_creation_filetime(process)))
        return process, thread, pid
    monkeypatch.setattr(HiddenDesktop, '__init__', desktop)
    monkeypatch.setattr(JobObjectGuard, '__init__', guard)
    monkeypatch.setattr(HiddenJobToken, 'create_process', create)
    if stage == 'after-create':
        monkeypatch.setattr(JobObjectGuard, 'is_member', lambda *a: False)
    if stage == 'after-return':
        monkeypatch.setattr(identity, 'read_creation_filetime', lambda *a: None)
    try:
        with pytest.raises((OSError, RuntimeError)):
            WindowsProcessOwner().spawn([sys.executable, '-c', 'from pathlib import Path; Path('+repr(str(marker))+').touch()'])
        assert desktops and all(d.handle == 0 for d in desktops)
        assert all(not g.handle_open for g in guards)
        assert until(lambda: dead(handles))
        assert all(desktop_gone(d.name) for d in desktops)
        if stage != 'after-return':
            assert not marker.exists()
    finally:
        release_handles(handles)


def test_runner_hard_death_releases_whole_tree_and_desktop(tmp_path, monkeypatch):
    from tests.test_job_lifecycle_prevention import desktop_gone
    import site
    monkeypatch.setenv('PYTHONPATH', os.pathsep.join([str(Path.cwd()), *site.getsitepackages()]))
    runner = subprocess.Popen([sys._base_executable, str(PROBE), 'runner', str(tmp_path)], creationflags=0x08000000)
    handles = []
    try:
        facts = rows(tmp_path)
        assert until(lambda: (tmp_path / 'runner.json').exists())
        handles = retain(facts)
        name = facts[0]['desktop']
        assert name.startswith('PanJob_')
        assert {row['desktop'] for row in facts} == {name}
        runner.kill()
        runner.wait(10)
        assert until(lambda: dead(handles))
        assert until(lambda: desktop_gone(name))
        proof('runner-hard-death', members=facts, allDead=dead(handles),
              desktopGone=desktop_gone(name), runnerExit=runner.returncode)
    finally:
        if runner.poll() is None:
            runner.kill()
            runner.wait(10)
        release_handles(handles)


def test_runner_death_inside_create_before_return_runs_no_child_instruction(tmp_path, monkeypatch):
    from tests.test_job_lifecycle_prevention import desktop_gone
    import site
    monkeypatch.setenv('PYTHONPATH', os.pathsep.join([str(Path.cwd()), *site.getsitepackages()]))
    runner = subprocess.Popen([sys._base_executable, str(PROBE), 'creation-runner', str(tmp_path)], creationflags=0x08000000)
    handles = []
    try:
        assert until(lambda: (tmp_path / 'suspended.json').exists())
        fact = json.loads((tmp_path / 'suspended.json').read_text())
        handles = retain([fact])
        assert fact['desktop'].startswith('PanJob_')
        assert not desktop_gone(fact['desktop'])
        assert not (tmp_path / 'must-not-execute').exists()
        runner.kill()
        runner.wait(10)
        assert until(lambda: dead(handles))
        assert until(lambda: desktop_gone(fact['desktop']))
        assert not (tmp_path / 'must-not-execute').exists()
        proof('creation-edge-hard-death', suspended=fact, allDead=dead(handles),
              desktopGone=desktop_gone(fact['desktop']), noChildInstruction=True)
    finally:
        if runner.poll() is None:
            runner.kill()
            runner.wait(10)
        release_handles(handles)


def test_close_failure_retains_desktop_and_allows_retry(monkeypatch):
    from packages.core.platform.process import WindowsProcessOwner, OwnershipUnknownError
    from tests.test_job_lifecycle_prevention import desktop_gone
    owner = WindowsProcessOwner()
    owned = owner.spawn([sys._base_executable, '-c', 'import time; time.sleep(30)'])
    name = owned.popen.desktop.name
    original = owned.guard.close
    monkeypatch.setattr(owned.guard, 'close', lambda: False)
    try:
        with pytest.raises(OwnershipUnknownError):
            owned.close()
        assert not owned.closed
        assert not desktop_gone(name)
    finally:
        monkeypatch.setattr(owned.guard, 'close', original)
        owned.close()
    assert owned.closed
    assert until(lambda: desktop_gone(name))


def test_hidden_owner_preserves_existing_token_restriction():
    from packages.core.platform.process import WindowsProcessOwner
    from tests.test_hidden_job_token import _ordinary_access_checks
    import win32api
    import win32security
    import win32con
    owner = WindowsProcessOwner()
    with closing(owner.spawn([sys._base_executable, '-c', 'import time; time.sleep(30)'])) as owned:
        descriptor = win32security.GetSecurityInfo(int(owned.popen._handle),
            win32security.SE_KERNEL_OBJECT, win32security.DACL_SECURITY_INFORMATION)
        token = win32security.OpenProcessToken(int(owned.popen._handle), win32con.TOKEN_QUERY)
        try:
            sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        finally:
            token.Close()
        dacl = descriptor.GetSecurityDescriptorDacl()
        assert any(ace[0][0] == win32security.ACCESS_DENIED_ACE_TYPE and
                   ace[1] & 0x200 and ace[2] == sid
                   for ace in (dacl.GetAce(index) for index in range(dacl.GetAceCount())))
        with _ordinary_access_checks():
            query = win32api.OpenProcess(0x400, False, owned.pid)
            query.Close()
            with pytest.raises(Exception) as failure:
                win32api.OpenProcess(0x200, False, owned.pid)
            assert failure.value.winerror == 5


def test_process_handle_close_failure_remains_retryable(monkeypatch):
    from packages.core.platform.process import WindowsProcessOwner
    from packages.core.terminal import identity
    from packages.jobs.cleanup import CleanupError
    owned = WindowsProcessOwner().spawn([sys._base_executable, '-c', 'import time; time.sleep(30)'])
    handle = owned.popen._handle
    original = identity.close_handle_checked
    calls = []
    def close(value):
        if int(value) == int(handle):
            calls.append(int(value))
            return False
        return original(value)
    monkeypatch.setattr(identity, 'close_handle_checked', close)
    try:
        with pytest.raises(CleanupError, match='process handle close unconfirmed'):
            owned.close()
        assert not owned.closed and not handle.closed
        assert owned.guard is None
        assert calls == [int(handle)]
    finally:
        monkeypatch.setattr(identity, 'close_handle_checked', original)
        owned.close()
    assert owned.closed and handle.closed


def test_named_runtime_direct_business_root(tmp_path, monkeypatch):
    """CI's interpreter may be installed directly, with no venv redirector."""
    monkeypatch.setattr(sys, 'executable', sys._base_executable)
    test_hidden_tree_identity_stdio_and_desktop_cleanup(tmp_path, 'named-venv')


@pytest.mark.parametrize('mismatch', ['birth', 'root-birth', 'unrelated-root'])
def test_business_lifetime_proof_rejects_mismatch(tmp_path, mismatch):
    from packages.core.platform.process import WindowsProcessOwner
    owner = WindowsProcessOwner()
    owned = owner.spawn([sys._base_executable, str(PROBE), 'tree', str(tmp_path), '2'])
    unrelated = None
    try:
        facts = rows(tmp_path)
        fact = dict(facts[2])
        if mismatch == 'birth':
            fact['birth'] += 1
            with pytest.raises(AssertionError, match='FILETIME mismatch'):
                retain_business_root(owned, fact)
        elif mismatch == 'root-birth':
            from dataclasses import replace
            original_identity = owned.identity
            try:
                owned.identity = replace(original_identity, birth=str(int(original_identity.birth) + 1))
                with pytest.raises(AssertionError):
                    retain_business_root(owned, fact)
            finally:
                owned.identity = original_identity
        else:
            unrelated = owner.spawn([sys._base_executable, '-c', 'import time; time.sleep(30)'])
            with pytest.raises(AssertionError, match='proven descendant'):
                retain_business_root(unrelated, fact)
    finally:
        owned.close()
        if unrelated is not None:
            unrelated.close()
