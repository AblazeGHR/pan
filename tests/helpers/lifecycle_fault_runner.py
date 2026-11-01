"""Owned fault launcher; production modules execute after deterministic gates."""
import argparse, json, os
from pathlib import Path
import sys, time, ctypes
from ctypes import wintypes as w
k=ctypes.WinDLL('kernel32',use_last_error=True);k.GetCurrentProcess.restype=w.HANDLE
k.GetProcessTimes.argtypes=[w.HANDLE,ctypes.POINTER(w.FILETIME),ctypes.POINTER(w.FILETIME),ctypes.POINTER(w.FILETIME),ctypes.POINTER(w.FILETIME)]
created,exited,kernel,user=(w.FILETIME() for _ in range(4))
assert k.GetProcessTimes(k.GetCurrentProcess(),ctypes.byref(created),ctypes.byref(exited),ctypes.byref(kernel),ctypes.byref(user))
identity={'runnerPid':os.getpid(),'runnerParent':os.getppid(),'runnerFiletime':(created.dwHighDateTime<<32)|created.dwLowDateTime}
if sys.prefix != sys.base_prefix:
 k.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD];k.OpenProcess.restype=w.HANDLE
 k.CloseHandle.argtypes=[w.HANDLE]
 parent=k.OpenProcess(0x1000,False,os.getppid());assert parent
 try:
  pc,pe,pk,pu=(w.FILETIME() for _ in range(4))
  assert k.GetProcessTimes(parent,ctypes.byref(pc),ctypes.byref(pe),ctypes.byref(pk),ctypes.byref(pu))
  identity['runnerParentFiletime']=(pc.dwHighDateTime<<32)|pc.dwLowDateTime
 finally:assert k.CloseHandle(parent)
p=argparse.ArgumentParser();p.add_argument('--kind');p.add_argument('--fault');p.add_argument('--evidence');p.add_argument('--root');p.add_argument('--job-id');p.add_argument('--site-packages',action='append',default=[]);p.add_argument('--bootstrap-receipt');p.add_argument('--bootstrap-nonce');a=p.parse_args()
import site
for directory in a.site_packages:site.addsitedir(directory)
def publish(row):
 target=Path(a.evidence);temporary=target.with_suffix('.tmp')
 temporary.write_text(json.dumps(row));os.replace(temporary,target)
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
if a.fault=='none':publish(identity)
if a.fault=='pre-import':
 publish(identity)
 while True:time.sleep(.05)
from packages.jobs.restricted_token import HiddenJobToken
from packages.core.terminal.identity import read_creation_filetime
if a.fault=='post-create':
 original=HiddenJobToken.create_process
 def create(self,*args):
  result=original(self,*args)
  publish({**identity,'pid':result[2],'creationFiletime':read_creation_filetime(result[0]),'desktop':getattr(args[3],'StartupInfo',args[3]).lpDesktop})
  while True:time.sleep(.05)
 HiddenJobToken.create_process=create
if a.fault=='wait-error':
 def fail_wait(*args,**kwargs):
  root=Path(a.evidence).parent
  deadline=time.monotonic()+8
  while not (root/'tree-0.json').exists():
   assert time.monotonic()<deadline
   time.sleep(.01)
  publish(identity)
  while not (root/'fault-release').exists():
   assert time.monotonic()<deadline
   time.sleep(.01)
  raise RuntimeError('injected owned wait failure')
 if a.kind=='canonical':
  from packages.core.platform.process import WindowsProcessOwner
  WindowsProcessOwner.wait=fail_wait
 elif a.kind=='legacy':
  from packages.jobs.desktop import DesktopCommand
  DesktopCommand.wait=fail_wait
 else:
  from packages.core.terminal import identity as identity_module
  identity_module.wait_state=fail_wait
if a.kind=='canonical':
 from packages.jobs.independent_execution_runner import run
 from packages.core.platform.execution_host import read_bootstrap_receipt
 receipt=read_bootstrap_receipt(a.bootstrap_receipt,a.bootstrap_nonce) if a.bootstrap_receipt else None
 raise SystemExit(run(a.root,a.job_id,bootstrap_receipt=receipt))
elif a.kind=='legacy':
 from packages.core.background_runner import main
 sys.argv=[sys.argv[0],'--job-id',a.job_id];raise SystemExit(main())
else:
 from packages.jobs.independent_runner import run
 raise SystemExit(run(a.root,a.job_id))
