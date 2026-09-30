"""Windows takeover ownership independent of the terminal's lifetime."""
import ctypes
from ctypes import wintypes
import subprocess
import time


class TakeoverJob:
    def __init__(self):
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        for name, args in {
            "AssignProcessToJobObject": [wintypes.HANDLE, wintypes.HANDLE],
            "TerminateJobObject": [wintypes.HANDLE, wintypes.UINT],
            "QueryInformationJobObject": [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p],
            "CloseHandle": [wintypes.HANDLE],
            "SetInformationJobObject": [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD],
        }.items():
            getattr(self.api, name).argtypes = args
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        # Extended limit information on Windows (64-bit): limit flags at 16.
        # Kill owned descendants if Pan exits and closes its last job handle.
        class BasicLimits(ctypes.Structure):
            _fields_ = [("per_process", ctypes.c_longlong), ("per_job", ctypes.c_longlong),
                        ("flags", wintypes.DWORD), ("min_ws", ctypes.c_size_t),
                        ("max_ws", ctypes.c_size_t), ("active", wintypes.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                        ("scheduling", wintypes.DWORD)]
        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("basic", BasicLimits), ("io", ctypes.c_ulonglong * 6),
                        ("memory", ctypes.c_size_t * 4)]
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.api.CloseHandle(self.handle)
            self.handle = None
            raise error

    def launch(self, args, **kwargs):
        # No child can escape between launch and assignment: the shell starts
        # suspended, and descendants inherit membership even after it exits.
        proc = subprocess.Popen(args, creationflags=subprocess.CREATE_NEW_CONSOLE | 4, **kwargs)
        try:
            if not self.api.AssignProcessToJobObject(self.handle, int(proc._handle)):
                raise ctypes.WinError(ctypes.get_last_error())
            ntdll = ctypes.WinDLL("ntdll")
            ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
            ntdll.NtResumeProcess.restype = ctypes.c_long
            if ntdll.NtResumeProcess(int(proc._handle)) != 0:
                raise OSError("Unable to resume takeover terminal")
            return proc.pid
        except BaseException:
            proc.kill()
            proc.wait(timeout=5)
            raise

    def stop(self):
        if not self.api.TerminateJobObject(self.handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())
        deadline = time.monotonic() + 5
        while True:
            # JOBOBJECT_BASIC_ACCOUNTING_INFORMATION: four LARGE_INTEGERs,
            # then PageFaults, TotalProcesses, ActiveProcesses, Terminated.
            info = ctypes.create_string_buffer(48)
            if not self.api.QueryInformationJobObject(self.handle, 1, info, 48, None):
                raise ctypes.WinError(ctypes.get_last_error())
            if int.from_bytes(info.raw[40:44], "little") == 0:
                self.api.CloseHandle(self.handle)
                self.handle = None
                return
            if time.monotonic() >= deadline:
                raise TimeoutError("Takeover writer processes have not exited")
            time.sleep(0.05)
