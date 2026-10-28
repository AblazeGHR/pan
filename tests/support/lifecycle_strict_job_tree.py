"""Three-generation owned tree for canonical Job cancellation proof."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import psutil

root, depth = Path(sys.argv[1]), int(sys.argv[2])
if depth == 2:
    with (root / "effects.txt").open("a", encoding="utf-8") as stream:
        stream.write("once\n")
row = {"pid": os.getpid(), "birth": psutil.Process().create_time(), "depth": depth}
if os.name == "nt":
    import ctypes
    from ctypes import wintypes as w
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.GetThreadDesktop.argtypes = [w.DWORD]
    user.GetThreadDesktop.restype = w.HANDLE
    user.GetUserObjectInformationW.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD)]
    name, needed = ctypes.create_unicode_buffer(256), w.DWORD()
    assert user.GetUserObjectInformationW(user.GetThreadDesktop(kernel.GetCurrentThreadId()), 2,
                                           name, ctypes.sizeof(name), ctypes.byref(needed))
    row["desktop"] = name.value
target = root / f"member-{depth}.json"
temporary = target.with_suffix(".tmp")
temporary.write_text(json.dumps(row), encoding="utf-8")
temporary.replace(target)
if depth:
    subprocess.Popen([sys.executable, __file__, str(root), str(depth - 1)])
while True:
    time.sleep(.1)
