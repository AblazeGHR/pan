"""Owned three-generation file-IPC tree, no model/network/UI."""
import ctypes
from ctypes import wintypes as w
import json, os
from pathlib import Path
import subprocess, sys, time
root=Path(sys.argv[1]);depth=int(sys.argv[2])
k=ctypes.WinDLL('kernel32',use_last_error=True);k.GetCurrentProcess.restype=w.HANDLE
k.GetProcessTimes.argtypes=[w.HANDLE,ctypes.POINTER(w.FILETIME),ctypes.POINTER(w.FILETIME),ctypes.POINTER(w.FILETIME),ctypes.POINTER(w.FILETIME)]
c,e,t,u=(w.FILETIME() for _ in range(4));assert k.GetProcessTimes(k.GetCurrentProcess(),ctypes.byref(c),ctypes.byref(e),ctypes.byref(t),ctypes.byref(u))
user=ctypes.WinDLL('user32',use_last_error=True);user.GetThreadDesktop.argtypes=[w.DWORD];user.GetThreadDesktop.restype=w.HANDLE
user.GetUserObjectInformationW.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD,ctypes.POINTER(w.DWORD)]
name=ctypes.create_unicode_buffer(256);n=w.DWORD();assert user.GetUserObjectInformationW(user.GetThreadDesktop(k.GetCurrentThreadId()),2,name,ctypes.sizeof(name),ctypes.byref(n))
row=dict(pid=os.getpid(),creationFiletime=(c.dwHighDateTime<<32)|c.dwLowDateTime,desktop=name.value)
if '--runner-ancestry' in sys.argv:
 import psutil
 from packages.core.terminal.identity import probe_process, ProcessStatus
 ancestors=[]
 parent=psutil.Process().parent()
 while parent:
  probe=probe_process(parent.pid)
  assert probe.status is ProcessStatus.ALIVE and probe.identity
  command=parent.cmdline()
  ancestors.append(dict(pid=parent.pid,creationFiletime=probe.identity.created_at_filetime,
                        executable=parent.exe(),command=command))
  if 'packages.core.background_runner' in command:break
  assert len(ancestors)<4, 'Owned runner not found in task ancestry'
  parent=parent.parent()
 row['runnerAncestors']=ancestors
temporary=root/f'tree-{depth}.tmp'
temporary.write_text(json.dumps(row));os.replace(temporary,root/f'tree-{depth}.json')
if depth:subprocess.Popen([sys.executable,__file__,str(root),str(depth-1)],creationflags=subprocess.CREATE_NO_WINDOW)
if depth==2:
 deadline=time.monotonic()+30
 while not (root/'root-release').exists():
  assert time.monotonic()<deadline
  time.sleep(.01)
 print('owned root stdout',flush=True)
 print('owned root stderr',file=sys.stderr,flush=True)
else:time.sleep(30)
