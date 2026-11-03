"""Owned PTY exec/signal probe independent of Pan and pytest (Linux/macOS only).

No provider, account, listener, registry, sudo or external process signals. Every
root is a retained direct Popen child in its own session/group. Probe programs
self-exit after 3 seconds so denied signals do not require broader kill authority.
The ready case is a comparison observation, never a replacement for immediate.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import select
import signal
import socket
import subprocess
import sys
import time
import threading


def child():
    import fcntl
    import termios
    ctl=socket.socket(fileno=int(sys.argv[2]))
    ctl.set_inheritable(False)
    fcntl.ioctl(0,termios.TIOCSCTTY,0)
    os.tcsetpgrp(0,os.getpgrp())
    ctl.sendall(b'R')
    if ctl.recv(1)!=b'G':raise SystemExit(125)
    os.execv(sys.executable,[sys.executable,'-u','-c',
        "import os,time;print('TARGET-READY',flush=True);time.sleep(3)"])


def snapshot(process):
    import psutil
    facts={'retained_code':process.poll()}
    try:p=psutil.Process(process.pid)
    except psutil.Error as exc:
        facts['probe_error']=type(exc).__name__
        return facts
    for name,read in [('status',p.status),('pgid',lambda:os.getpgid(process.pid)),
        ('uids',lambda:p.uids()._asdict()),
        ('caller_uids',lambda:psutil.Process().uids()._asdict()),
        ('image_basename',lambda:os.path.basename(p.exe()))]:
        try:facts[name]=read()
        except (psutil.Error,OSError) as exc:facts[name+'_error']=type(exc).__name__
    return facts


def probe(wait_ready,write_input,drain_output):
    master,slave=os.openpty();parent,other=socket.socketpair();parent.settimeout(5)
    process=None;drainer=None;stop=threading.Event()
    facts={'wait_target_ready':wait_ready,'canonical_input':write_input,
        'continuous_drain':drain_output,'drained_bytes':0,'signals':[]}
    try:
        process=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),
            '--child',str(other.fileno())],stdin=slave,stdout=slave,stderr=slave,
            pass_fds=(other.fileno(),),start_new_session=True,close_fds=True)
        other.close();os.close(slave);slave=-1
        assert parent.recv(1)==b'R'
        assert os.getpgid(process.pid)==process.pid
        parent.sendall(b'G');assert parent.recv(128)==b''
        if wait_ready:
            data=b'';deadline=time.monotonic()+5
            while b'TARGET-READY' not in data:
                if time.monotonic()>deadline:raise TimeoutError('target readiness')
                if select.select([master],[],[],.05)[0]:data+=os.read(master,4096)
        if drain_output:
            def drain():
                while not stop.is_set():
                    if not select.select([master],[],[],.05)[0]:continue
                    try:data=os.read(master,65536)
                    except OSError:break
                    if not data:break
                    facts['drained_bytes']+=len(data)
            drainer=threading.Thread(target=drain,daemon=True);drainer.start()
        if write_input:os.write(master,b'x')
        facts['before_signal']=snapshot(process)
        for sig in (signal.SIGTERM,signal.SIGKILL):
            if process.poll() is not None:break
            # Reserved direct-child PID plus unchanged original group; never
            # signal a reaped root or any external process/group.
            try:group=os.getpgid(process.pid)
            except ProcessLookupError:
                facts['signal_target_disappeared']=True
                break  # No signal; retained wait below must prove original exit.
            assert group==process.pid
            sent={'signal':int(sig)}
            try:os.killpg(process.pid,sig);sent['result']='returned'
            except OSError as exc:sent.update(result='error',errno=exc.errno)
            facts['signals'].append(sent)
            try:process.wait(timeout=.25)
            except subprocess.TimeoutExpired:pass
        facts['after_signal']=snapshot(process)
    except (OSError,AssertionError,TimeoutError) as exc:
        facts['probe_exception']=type(exc).__name__
        facts['probe_errno']=getattr(exc,'errno',None)
    finally:
        if process is not None:
            # Only bounded natural self-exit observation if the kernel refused.
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:facts['cleanup']='unknown-retained-root'
            else:facts['cleanup']='retained-root-exited'
        stop.set()
        if drainer is not None:drainer.join(1)
        parent.close();other.close()
        if slave>=0:os.close(slave)
        os.close(master)
        if process is not None and process.returncode is None:
            try:process.wait(timeout=2)
            except subprocess.TimeoutExpired:pass
            facts['after_master_close_code']=process.returncode
            if process.returncode is not None:
                facts['cleanup_before_master_close']=facts['cleanup']
                facts['cleanup']='retained-root-exited-after-master-close'
    return facts


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',required=True,type=Path);a=p.parse_args()
    if sys.platform not in {'darwin','linux'}:raise SystemExit('native POSIX probe only')
    result={'platform':sys.platform,'python':sys.version.split()[0],
        'pan_imported':any(n.startswith('packages.core') for n in sys.modules),
        'pytest_imported':'pytest' in sys.modules,'cases':[]}
    for ready in (False,True):
        for write in (False,True):
            for drain in (False,True):result['cases'].append(probe(ready,write,drain))
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    return int(any((c.get('cleanup') not in {'retained-root-exited','retained-root-exited-after-master-close'} or 'probe_exception' in c) for c in result['cases']))


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--child':child()
    else:raise SystemExit(main())
