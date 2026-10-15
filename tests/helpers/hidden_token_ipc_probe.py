"""Owned temporary IPC server for hidden-token compatibility validation."""
import json, mmap, sys
from pathlib import Path
import win32api, win32event, win32file, win32pipe
root = Path(sys.argv[1]); name = sys.argv[2]
event = win32event.CreateEvent(None, True, False, name + "event")
mutex = win32event.CreateMutex(None, False, name + "mutex")
section = mmap.mmap(-1, 4096, tagname=name + "section")
pipe = win32pipe.CreateNamedPipe(r"\\.\pipe" + chr(92) + name, 3, 0, 1, 4096, 4096, 2000, None)
(root / "owned.txt").write_text("child-file")
(root / "ready.json").write_text(json.dumps({"ready": True}))
try:
    try: win32pipe.ConnectNamedPipe(pipe, None)
    except Exception as error:
        if getattr(error, "winerror", None) != 535: raise
    _, data = win32file.ReadFile(pipe, 64)
    assert data == b"parent-pipe", data
    assert win32event.WaitForSingleObject(event, 8000) == 0
    assert section[:14] == b"parent-section"
    assert (root / "owned.txt").read_text() == "parent-file"
    print("owned IPC stdout", flush=True)
    print("owned IPC stderr", file=sys.stderr, flush=True)
finally:
    section.close()
    for handle in (pipe, mutex, event): handle.Close()
