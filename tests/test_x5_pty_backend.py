"""One PtyBackend/PtyRuntime contract; native POSIX tests require actual target OS."""
import os
import sys
import time
from dataclasses import replace
import threading
import subprocess

import pytest

from packages.core.terminal.contracts import (ProcessIdentity, ProcessStatus, OwnershipMode,
    OwnershipPolicy, RuntimeState, DetachUnsupportedError)
from packages.core.terminal.ownership import build_runtime
from packages.core.terminal.backend_posix import PosixPtyBackend
from packages.core.platform.process import UnsupportedPlatformError, OwnershipUnknownError

NATIVE = pytest.mark.skipif(sys.platform not in {"linux", "darwin"}, reason="real POSIX PTY requires Linux/macOS")


def until(read, predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = read()
        if predicate(value):
            return value
        time.sleep(.02)
    raise AssertionError("owned PTY observation deadline")


def spawn(code, **kw):
    return PosixPtyBackend.spawn([sys.executable, "-u", "-c", code], **kw)


def collect(backend, marker):
    data = bytearray()
    def read():
        try:
            data.extend(backend.read(65536))
        except EOFError:
            pass
        return bytes(data)
    return until(read, lambda b: marker in b)


def runtime(backend):
    r = build_runtime("owned-x5-pty", backend,
        ownership=OwnershipPolicy(mode=OwnershipMode.SERVICE, lifecycle_owner="service",
            tree_guard_kind="posix-process-group", tree_guard=backend.guard),
        identity=backend.identity, identity_probe=backend.probe)
    r.start(rows=24, cols=80, gate=backend.gate)
    return r


def dispose(backend):
    backend.terminate(True)
    backend.close()


def test_windows_explicitly_unavailable():
    if sys.platform in {"linux", "darwin"}:
        pytest.skip("unsupported-platform negative applies on Windows")
    with pytest.raises(UnsupportedPlatformError):
        spawn("pass")


@NATIVE
def test_real_tty_utf8_resize_interrupt_and_raw_tui():
    code = r'''import os,sys,signal,tty,termios
assert all(os.isatty(fd) for fd in (0,1,2))
with open('/dev/tty') as t: assert os.tcgetpgrp(t.fileno()) == os.getpgrp()
signal.signal(signal.SIGWINCH, lambda *_: print('SIZE:%d:%d' % (os.get_terminal_size().lines,os.get_terminal_size().columns),flush=True))
signal.signal(signal.SIGINT, lambda *_: print('INTERRUPTED',flush=True))
print('TTY-READY',flush=True)
for line in sys.stdin:
 if line.strip() == 'raw':
  old=termios.tcgetattr(0);tty.setraw(0)
  try:
   os.write(1,b'\x1b[?1049hTUI-READY'); key=os.read(0,3);os.write(1,b'KEY:'+key+b'\x1b[?1049l')
  finally:termios.tcsetattr(0,termios.TCSANOW,old)
 else:print('ECHO:'+line.strip(),flush=True)
'''
    b = spawn(code)
    try:
        collect(b,b'TTY-READY')
        assert b.write('你好 café\n'.encode()) == len('你好 café\n'.encode())
        assert 'ECHO:你好 café'.encode() in collect(b,'ECHO:你好 café'.encode())
        b.resize(37,103)
        collect(b,b'SIZE:37:103')
        b.write(b'\x03'); collect(b,b'INTERRUPTED')
        b.write(b'raw\n');collect(b,b'TUI-READY')
        b.write(b'\x1b[A');collect(b,b'KEY:\x1b[A')
        assert b.alive() and b.exit_code() is None
        assert b.probe(b.pid+999999).status is ProcessStatus.UNKNOWN
    finally:dispose(b)


@NATIVE
def test_runtime_eof_tail_cleanup_and_detach_is_not_connection_attach():
    b = spawn("import time; print('TAIL-你好',flush=True); time.sleep(.15)")
    r = runtime(b)
    until(lambda:r.exit,lambda x:x.channel_eof)
    assert r.exit.output_complete
    assert "TAIL-你好".encode() in b"".join(chunk.data for chunk in r.log.read_from(0).chunks)
    report=r.close(interrupt=False)
    assert report.ok and not report.owner_retained and r.state is RuntimeState.EXITED
    assert b.exit_code() == 0
    b.close()
    b = spawn("import time; time.sleep(60)")
    r=runtime(b)
    try:
        with pytest.raises(DetachUnsupportedError):r.detach()
        assert r.state is RuntimeState.RUNNING and b.alive()
    finally:
        assert r.close(interrupt=False).ok


@NATIVE
def test_unknown_identity_refuses_runtime_close_and_can_retry(monkeypatch):
    b=spawn("import time; time.sleep(60)")
    r=runtime(b)
    probe=r._identity_probe
    r._identity_probe=lambda _:None
    report=r.close(interrupt=False)
    assert report.owner_retained and not report.ok and b.alive()
    r._identity_probe=probe
    assert r.close(interrupt=False).ok


@NATIVE
def test_spawn_failure_does_not_publish_or_leak():
    with pytest.raises(FileNotFoundError):
        PosixPtyBackend.spawn(['/x5-owned-nonexistent-command'])


@NATIVE
def test_close_live_process_refuses_fd_release():
    b=spawn("import time; time.sleep(60)")
    try:
        with pytest.raises(OwnershipUnknownError):b.close()
        assert b.alive() and b.write(b'x') == 1
    finally:
        try:dispose(b)
        except BaseException as exc:
            if sys.platform == 'darwin':
                # Diagnostics only: no enumeration result grants kill/close rights.
                import ctypes
                lib=ctypes.CDLL('/usr/lib/libproc.dylib',use_errno=True)
                lib.proc_listpgrppids.argtypes=[ctypes.c_int,ctypes.c_void_p,ctypes.c_int]
                lib.proc_listpgrppids.restype=ctypes.c_int
                buf=(ctypes.c_int*4096)()
                ctypes.set_errno(0)
                count=lib.proc_listpgrppids(b.owned.pgid,buf,ctypes.sizeof(buf))
                exc.add_note(str({'diagnostic_group_members':list(buf[:max(0,count)]),
                    'native_list_count':count,'native_list_errno':ctypes.get_errno()}))
                import psutil
                from packages.core.platform import process_darwin as darwin
                native,_=darwin._libraries();info=darwin.BsdInfoWithUniqueId()
                copied=native.proc_pidinfo(b.pid,darwin.PROC_PIDT_BSDINFOWITHUNIQID,1,
                    ctypes.byref(info),ctypes.sizeof(info))
                exc.add_note(str({'native_copy_bytes':copied,'native_status':info.bsd.status,
                    'native_flags':info.bsd.flags,'uid':info.bsd.uid,'ruid':info.bsd.ruid,
                    'svuid':info.bsd.svuid,'caller_uid':os.getuid(),'caller_euid':os.geteuid()}))
                root=psutil.Process(b.pid)
                for name,read in [('status',root.status),('uids',root.uids),
                    ('image',lambda:os.path.basename(root.exe())),
                    ('children',lambda:[p.pid for p in root.children(recursive=True)])]:
                    try:value=read()
                    except psutil.Error as diagnostic_error:value=type(diagnostic_error).__name__
                    exc.add_note(str({name:value}))
                # A bounded retained wait is diagnostic only; keep original failure.
                try:b.owned.popen.wait(timeout=1.)
                except subprocess.TimeoutExpired:pass
                exc.add_note(str({'after_observation_root_code':b.exit_code()}))
            raise


def test_native_identity_never_matches_weak_time_or_other_birth():
    native=ProcessIdentity(pid=123,native_identity=("posix","starttime","100","boot"))
    assert native.matches(replace(native))
    assert not native.matches(replace(native,native_identity=("posix","starttime","101","boot")))
    assert not native.matches(ProcessIdentity(pid=123,created_at=100.))
    assert not native.matches(replace(native,pid=124))
    assert not replace(native,native_identity=()).matches(replace(native,native_identity=()))
    assert native.as_dict()["native_identity"][2] == "100"


@NATIVE
def test_observed_group_children_close_reaps_before_release():
    code = r'''import os,signal,subprocess,sys,time
child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])
def stop(*_):
 child.wait(timeout=2)
 raise SystemExit(0)
signal.signal(signal.SIGTERM,stop)
print('CHILD-READY',flush=True)
while True:time.sleep(.05)
'''
    b=spawn(code)
    collect(b,b'CHILD-READY')
    r=runtime(b)
    report=r.close(interrupt=False)
    assert report.ok,report.as_dict()
    assert len(report.tree_owned_pids)==2 and not report.tree_remaining_pids


@NATIVE
def test_permission_unknown_preserves_owner_no_signal(monkeypatch):
    from packages.core.platform.process import IdentityCheck,ProcessStatus as OSStatus
    b=spawn("import time; time.sleep(60)")
    original=b.owner.check
    monkeypatch.setattr(b.owner,'check',lambda identity:IdentityCheck(
        status=OSStatus.UNKNOWN,ours=None,expected=identity,detail='permission denied'))
    try:
        with pytest.raises(OwnershipUnknownError):b.terminate(True)
        with pytest.raises(OwnershipUnknownError):b.alive()
        assert b.owned.popen.poll() is None
    finally:
        monkeypatch.setattr(b.owner,'check',original)
        dispose(b)


@NATIVE
def test_reader_and_writer_close_are_bounded_and_keep_raw_bytes():
    b=spawn("import time; print('READY',flush=True); time.sleep(60)")
    collect(b,b'READY')
    received=[];done=threading.Event()
    def reader():
        try:
            while not done.is_set():received.append(b.read(4096))
        except (EOFError,RuntimeError):pass
    thread=threading.Thread(target=reader,daemon=True);thread.start()
    try:
        assert b.write(b'parallel-input')==14
        b.resize(40,120)
        dispose(b)
    finally:
        done.set();thread.join(2)
    assert not thread.is_alive()


@NATIVE
def test_strong_identity_spawn_failure_cleans_before_return(monkeypatch):
    from packages.core.platform import process_posix as native
    original=native.read_posix_identity
    seen=[]
    def read(pid):
        if pid!=os.getpid():
            seen.append(pid)
            return None,None
        return original(pid)
    monkeypatch.setattr(native,'read_posix_identity',read)
    with pytest.raises(OwnershipUnknownError):spawn("import time;time.sleep(60)")
    assert seen
    import psutil
    assert not psutil.pid_exists(seen[0])


@NATIVE
def test_leader_exit_live_member_is_unknown_no_stale_group_signal(tmp_path,monkeypatch):
    from packages.core.platform import process_posix as native
    release=tmp_path/'child-release';exit_root=tmp_path/'root-release';ready=tmp_path/'child-ready'
    child=("import pathlib,signal,time; signal.signal(signal.SIGHUP,signal.SIG_IGN); "
        + "pathlib.Path("+repr(str(ready))+").touch(); "
        + "exec(\"while not pathlib.Path("+repr(str(release))+ ").exists():time.sleep(.02)\")")
    code=("import pathlib,subprocess,sys,time; child=subprocess.Popen([sys.executable,'-c',"+repr(child)+"]); "
        +"exec(\"while not pathlib.Path("+repr(str(ready))+").exists():time.sleep(.02)\"); print('MEMBER-READY',flush=True); "
        +"exec(\"while not pathlib.Path("+repr(str(exit_root))+").exists():time.sleep(.02)\")")
    b=spawn(code)
    collect(b,b'MEMBER-READY')
    members=b.guard.owned_pids(b.pid)
    assert len(members)==2
    exit_root.touch()
    until(b.exit_code,lambda x:x is not None)
    original=native._killpg;signals=[]
    def record(pgid,sig):
        if sig:signals.append((pgid,sig))
        return original(pgid,sig)
    monkeypatch.setattr(native,'_killpg',record)
    try:
        with pytest.raises(OwnershipUnknownError):b.terminate(True)
        with pytest.raises(OwnershipUnknownError):b.close()
        assert b.guard.remaining(members) and not signals
    finally:
        release.touch()
        until(lambda:b.guard.remaining(members),lambda x:not x)
        b.close()


def test_same_pty_interface_echo_resize_and_close_on_native_backend():
    if sys.platform == "win32":
        from packages.core.terminal.backend import ConPtyBackend as Backend
    else:
        Backend=PosixPtyBackend
    b=Backend.spawn([sys.executable,'-u','-c',
        "import os,sys,time; assert os.isatty(0); print('SHARED-READY',flush=True); "
        "line=sys.stdin.readline(); print('SHARED:'+line.strip(),flush=True); time.sleep(60)"],rows=24,cols=80)
    try:
        b.gate.verify()
        collect(b,b'SHARED-READY')
        b.resize(30,90)
        assert b.write(b'hello-shared\r')==13
        collect(b,b'SHARED:hello-shared')
        assert b.alive() and b.exit_code() is None
    finally:
        r=runtime(b)
        report=r.close(interrupt=False)
        assert report.ok,report.as_dict()


@NATIVE
def test_startup_handshake_timeout_cleans_retained_child(monkeypatch):
    from packages.core.platform.process_posix import PosixProcessOwner
    original=PosixProcessOwner.spawn;children=[]
    def stalled(self,argv,**kw):
        handle=original(self,[sys.executable,'-c','import time;time.sleep(60)'],**kw)
        children.append(handle)
        return handle
    monkeypatch.setattr(PosixProcessOwner,'spawn',stalled)
    with pytest.raises(TimeoutError):spawn('pass',startup_timeout=.1)
    assert len(children)==1 and children[0].popen.poll() is not None


@NATIVE
def test_backpressure_returns_partial_within_write_budget():
    b=spawn("import tty,time;tty.setraw(0);print('RAW-READY',flush=True);time.sleep(60)")
    collect(b,b'RAW-READY')
    try:
        started=time.monotonic()
        written=b.write(b'x'*(8*1024*1024))
        assert 0 < written < 8*1024*1024
        assert time.monotonic()-started < 2.5
    finally:dispose(b)


@pytest.mark.skipif(not sys.platform.startswith('linux'),reason='Linux EIO/HUP distinction')
def test_eio_without_slave_hangup_is_error_not_eof(monkeypatch):
    import errno
    import packages.core.terminal.backend_posix as backend
    b=spawn("import time;print('READABLE',flush=True);time.sleep(60)")
    original=backend.os.read
    def failed(fd,size):raise OSError(errno.EIO,'injected error, slave still open')
    # Wait for readiness without consuming it, then inject the actual read failure.
    import select
    assert select.select([b._master],[],[],5)[0]
    monkeypatch.setattr(backend.os,'read',failed)
    try:
        with pytest.raises(OSError):b.read(100)
    finally:
        monkeypatch.setattr(backend.os,'read',original)
        dispose(b)


@NATIVE
@pytest.mark.parametrize('root_exits',[False,True])
def test_signal_error_only_readonly_retained_clearance_can_succeed(monkeypatch,root_exits):
    code='import time;time.sleep(.15)' if root_exits else 'import time;time.sleep(60)'
    b=spawn(code)
    original=b.owner.terminate
    def failure(handle,*,grace):
        if root_exits:
            assert b.owner.wait(handle,timeout=3).exited
        raise PermissionError('injected signal race/permission boundary')
    monkeypatch.setattr(b.owner,'terminate',failure)
    try:
        if root_exits:
            b.terminate(True)
            b.close()
        else:
            with pytest.raises(PermissionError):b.terminate(True)
            assert b.alive()
            with pytest.raises(OwnershipUnknownError):b.close()
    finally:
        monkeypatch.setattr(b.owner,'terminate',original)
        dispose(b)
