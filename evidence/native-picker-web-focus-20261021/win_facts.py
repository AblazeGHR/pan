"""Read-only Win32 window facts and PID-scoped native dialog test controls."""
import ctypes as c
from ctypes import wintypes as w
import time
import subprocess
u=c.WinDLL('user32',use_last_error=True)
k=c.WinDLL('kernel32',use_last_error=True)
u.SetProcessDpiAwarenessContext.argtypes=[c.c_void_p]
u.SetProcessDpiAwarenessContext(c.c_void_p(-4))
u.GetForegroundWindow.restype=w.HWND
u.GetWindow.argtypes=[w.HWND,w.UINT];u.GetWindow.restype=w.HWND
u.GetAncestor.argtypes=[w.HWND,w.UINT];u.GetAncestor.restype=w.HWND
u.GetWindowThreadProcessId.argtypes=[w.HWND,c.POINTER(w.DWORD)];u.GetWindowThreadProcessId.restype=w.DWORD
u.IsWindowVisible.argtypes=[w.HWND]
u.IsWindowEnabled.argtypes=[w.HWND]
u.GetWindowLongW.argtypes=[w.HWND,c.c_int];u.GetWindowLongW.restype=c.c_long
u.GetClassNameW.argtypes=[w.HWND,w.LPWSTR,c.c_int]
u.GetWindowTextW.argtypes=[w.HWND,w.LPWSTR,c.c_int]
u.GetDlgItem.argtypes=[w.HWND,c.c_int];u.GetDlgItem.restype=w.HWND
u.SendMessageW.argtypes=[w.HWND,w.UINT,w.WPARAM,w.LPARAM];u.SendMessageW.restype=w.LPARAM
u.PostMessageW.argtypes=[w.HWND,w.UINT,w.WPARAM,w.LPARAM]
class GUI(c.Structure):
 _fields_=[('size',w.DWORD),('flags',w.DWORD),('active',w.HWND),('focus',w.HWND),('capture',w.HWND),('menu',w.HWND),('move',w.HWND),('caret',w.HWND),('rect',w.RECT)]
u.GetGUIThreadInfo.argtypes=[w.DWORD,c.POINTER(GUI)]
def fact(hwnd):
 pid=w.DWORD();thread=u.GetWindowThreadProcessId(hwnd,c.byref(pid));name=c.create_unicode_buffer(256);title=c.create_unicode_buffer(512)
 u.GetClassNameW(hwnd,name,256);u.GetWindowTextW(hwnd,title,512)
 return {'hwnd':int(hwnd or 0),'pid':pid.value,'thread':thread,'class':name.value,'title':title.value,'visible':bool(u.IsWindowVisible(hwnd)),'enabled':bool(u.IsWindowEnabled(hwnd)),'topmost':bool(u.GetWindowLongW(hwnd,-20)&8),'owner':int(u.GetWindow(hwnd,4) or 0)}
def foreground():
 f=fact(u.GetForegroundWindow());f.pop('title',None);g=GUI();g.size=c.sizeof(g)
 f['guiRead']=bool(u.GetGUIThreadInfo(f['thread'],c.byref(g)))
 f['keyboardFocusRoot']=int(u.GetAncestor(g.focus,2) or 0)
 return f
def windows(pid):
 result=[]
 @c.WINFUNCTYPE(w.BOOL,w.HWND,w.LPARAM)
 def callback(hwnd,_):
  p=w.DWORD();u.GetWindowThreadProcessId(hwnd,c.byref(p))
  if p.value==pid:result.append(fact(hwnd))
  return True
 u.EnumWindows(callback,0)
 return result
def dialog(pid,timeout=12):
 end=time.monotonic()+timeout
 while time.monotonic()<end:
  for f in windows(pid):
   if f['class']=='#32770' and f['title']=='Select server attachments' and f['visible']:return f
  time.sleep(.05)
 raise RuntimeError('No owned native dialog')
def close_dialog(hwnd):
 assert fact(hwnd)['class']=='#32770'
 assert u.PostMessageW(hwnd,0x10,0,0)
def select_file(hwnd,path):
 assert fact(hwnd)['class']=='#32770'
 edit=u.GetDlgItem(hwnd,1148)
 if not edit:
  # Modern common dialog embeds its filename edit under a nested container.
  @c.WINFUNCTYPE(w.BOOL,w.HWND,w.LPARAM)
  def cb(h,_):
   nonlocal edit
   if u.GetDlgCtrlID(h)==1148:edit=h;return False
   return True
  u.EnumChildWindows(hwnd,cb,0)
 assert edit,'filename edit missing'
 text=c.create_unicode_buffer(path)
 u.SendMessageW(edit,0xC,0,c.cast(text,c.c_void_p).value)
 button=u.GetDlgItem(hwnd,1)
 assert button,'open button missing'
 assert u.PostMessageW(button,0xF5,0,0)

# Test-only desktop mouse operations: scoped to a verified owned HWND, no keys.
u.SetWindowPos.argtypes=[w.HWND,w.HWND,c.c_int,c.c_int,c.c_int,c.c_int,w.UINT]
u.GetWindowRect.argtypes=[w.HWND,c.POINTER(w.RECT)]
u.WindowFromPoint.argtypes=[w.POINT];u.WindowFromPoint.restype=w.HWND
u.SetCursorPos.argtypes=[c.c_int,c.c_int]
u.mouse_event.argtypes=[w.DWORD,w.DWORD,w.DWORD,w.DWORD,c.c_size_t]
class LAST(c.Structure):
 _fields_=[('size',w.UINT),('time',w.DWORD)]
def last_input():
 info=LAST();info.size=c.sizeof(info);assert u.GetLastInputInfo(c.byref(info))
 return info.time
def rect(hwnd):
 r=w.RECT();assert u.GetWindowRect(hwnd,c.byref(r));return {'left':r.left,'top':r.top,'right':r.right,'bottom':r.bottom}
def mouse_click(hwnd,x,y):
 assert u.GetAncestor(u.WindowFromPoint(w.POINT(x,y)),2)==hwnd, ('target covered',hwnd,x,y)
 before=last_input();assert u.SetCursorPos(x,y)
 u.mouse_event(2,0,0,0,0);u.mouse_event(4,0,0,0,0)
 return {'x':x,'y':y,'lastInputBefore':before,'lastInputAfter':last_input()}
def show_owned_browser(hwnd):
 assert fact(hwnd)['class']=='Chrome_WidgetWin_1'
 # Ordinary z-order only to expose our own test window to the test mouse.
 assert u.SetWindowPos(hwnd,None,0,0,0,0,0x53)
 # Standard accessibility focus prepares only the test browser, not the picker.
 script='Add-Type -AssemblyName UIAutomationClient; $w=[Windows.Automation.AutomationElement]::FromHandle([IntPtr]'+str(hwnd)+'); $p=New-Object Windows.Automation.PropertyCondition([Windows.Automation.AutomationElement]::IsKeyboardFocusableProperty,$true); $e=$w.FindFirst([Windows.Automation.TreeScope]::Descendants,$p); if(-not $e){throw "No focusable browser element"}; $e.SetFocus()'
 result=subprocess.run(['C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe','-NoProfile','-Command',script],capture_output=True,timeout=10,creationflags=subprocess.CREATE_NO_WINDOW)
 assert result.returncode==0,result.stderr
 time.sleep(.2)
 r=rect(hwnd)
 return mouse_click(hwnd,r['left']+150,r['top']+20)

k.ProcessIdToSessionId.argtypes=[w.DWORD,c.POINTER(w.DWORD)]
def process_session(pid):
 value=w.DWORD();assert k.ProcessIdToSessionId(pid,c.byref(value));return value.value
