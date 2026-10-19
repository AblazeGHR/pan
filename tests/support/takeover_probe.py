"""Deterministic provider boundary; actual HTTP/lifecycle/Windows Job are real."""
import json
import os
from pathlib import Path
import sys


def install(server, root):
    from packages.core.adapters.cbc import sessions
    from packages.core.takeover_job import TakeoverJob
    terminate = TakeoverJob.terminate_and_verify
    def stop(job):
        if (root / 'gate-stop').exists():
            import time
            (root / 'stop-entered').touch()
            deadline = time.monotonic() + 5
            while not (root / 'stop-release').exists():
                if time.monotonic() > deadline:
                    raise TimeoutError('test stop gate not released')
                time.sleep(.005)
        if (root / 'fail-stop').exists():
            raise TimeoutError('probe: Job retirement timed out')
        return terminate(job)
    TakeoverJob.terminate_and_verify = stop
    class Requests:
        def __init__(self, app):
            self.app = app
        async def __call__(self, scope, receive, send):
            if scope['type'] == 'http' and scope.get('method') == 'POST' and scope['path'].endswith(('/worker/kill', '/worker/restart')):
                with (root / 'control-requests.jsonl').open('a') as handle:
                    handle.write(json.dumps({'path': scope['path']}) + '\n')
            await self.app(scope, receive, send)
    server.app.add_middleware(Requests)
    def transcript(sid, cwd=None):
        path = root / (sid + '.resume.json')
        return json.loads(path.read_text()) if path.exists() else []
    sessions.session_exists = lambda sid, cwd=None: bool(transcript(sid, cwd))
    sessions.parse_history = transcript
    def open_terminal(cmd, cwd, *, takeover_job=None):
        assert takeover_job is not None
        return takeover_job.launch([sys.executable, str(Path(__file__).resolve()), str(root)], cwd=cwd)
    server._open_terminal = open_terminal


if __name__ == '__main__':
    import ctypes
    from ctypes import wintypes
    import subprocess
    import time
    root = Path(sys.argv[1])
    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.GetCurrentProcess.restype = wintypes.HANDLE
    api.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    values = [wintypes.FILETIME() for _ in range(4)]
    assert api.GetProcessTimes(api.GetCurrentProcess(), *(ctypes.byref(x) for x in values))
    created = (values[0].dwHighDateTime << 32) | values[0].dwLowDateTime
    with (root / 'takeover-processes.jsonl').open('a') as handle:
        handle.write(json.dumps({'pid': os.getpid(), 'filetime': created, 'child': child.pid}) + '\n')
    if not (root / 'close-terminal').exists():
        time.sleep(120)
