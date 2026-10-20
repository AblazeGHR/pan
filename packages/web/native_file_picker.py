"""Isolated Windows file dialog. This module never registers or copies files."""
import asyncio
import atexit
import ctypes
import json
import os
from pathlib import Path
import subprocess
import threading


def interactive_desktop() -> bool:
    if os.name != 'nt':
        return False
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.OpenInputDesktop.argtypes = [ctypes.c_uint, ctypes.c_bool, ctypes.c_uint]
    user32.OpenInputDesktop.restype = ctypes.c_void_p
    user32.CloseDesktop.argtypes = [ctypes.c_void_p]
    desktop = user32.OpenInputDesktop(0, False, 0x0100)  # DESKTOP_SWITCHDESKTOP
    if not desktop:
        return False
    user32.CloseDesktop(desktop)
    return True


class NativeFilePicker:
    def __init__(self, timeout: float = 90):
        self.timeout = timeout
        self._gate = threading.Lock()
        self._state = threading.Lock()
        self._process = None
        self._closed = False

    async def pick(self, directory: str = '') -> dict:
        if not self._gate.acquire(blocking=False):
            return {'status': 'busy', 'paths': []}
        # Shield the bounded owner so HTTP cancellation cannot strand the gate
        # or transfer process cleanup to a different request.
        return await asyncio.shield(asyncio.to_thread(self._pick, directory))

    def _pick(self, directory: str) -> dict:
        process = None
        try:
            if not interactive_desktop():
                return {'status': 'unavailable', 'paths': []}
            initial = ''
            if directory:
                candidate = Path(directory)
                if not candidate.is_absolute():
                    return {'status': 'failed', 'paths': []}
                # Filesystem probing (including potentially stalled UNC paths)
                # belongs inside the killable, timed helper.
                initial = os.path.normpath(str(candidate))
            script = Path(__file__).with_name('native_file_dialog.ps1')
            powershell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
            with self._state:
                if self._closed:
                    return {'status': 'unavailable', 'paths': []}
                process = subprocess.Popen(
                    [str(powershell), '-NoProfile', '-NonInteractive', '-STA', '-ExecutionPolicy', 'Bypass', '-File', str(script)],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                self._process = process
            # UTF-8 input is ASCII escaped; PowerShell stdout JSON may contain
            # non-ASCII filenames, so explicitly request UTF-8 in the helper.
            output, _ = process.communicate(json.dumps({'directory': initial}).encode('ascii'), timeout=self.timeout)
            result = json.loads(output.decode('utf-8-sig'))
            if not isinstance(result, dict):
                return {'status': 'failed', 'paths': []}
            paths = result.get('paths')
            if process.returncode or result.get('status') not in {'selected', 'cancelled'}:
                return {'status': 'failed', 'paths': []}
            if not isinstance(paths, list) or len(paths) > 256 or any(not isinstance(p, str) or not Path(p).is_absolute() for p in paths):
                return {'status': 'failed', 'paths': []}
            if result['status'] == 'selected' and not paths:
                return {'status': 'failed', 'paths': []}
            return {'status': result['status'], 'paths': paths if result['status'] == 'selected' else []}
        except subprocess.TimeoutExpired:
            return {'status': 'timeout', 'paths': []}
        except (OSError, ValueError, RuntimeError):
            return {'status': 'failed', 'paths': []}
        finally:
            try:
                if process is not None:
                    if process.poll() is None:
                        process.kill()
                    process.communicate(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                # If OS cleanup cannot confirm exit, retain ownership and the
                # gate. Never spawn another dialog alongside an unknown one.
                pass
            finally:
                if process is None or process.poll() is not None:
                    with self._state:
                        self._process = None
                    self._gate.release()

    def close(self):
        with self._state:
            self._closed = True
            if self._process is not None and self._process.poll() is None:
                try:
                    self._process.kill()
                except OSError:
                    pass  # Still owned; the bounded waiter also attempts cleanup.


native_file_picker = NativeFilePicker()
atexit.register(native_file_picker.close)
