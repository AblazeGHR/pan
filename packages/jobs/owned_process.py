"""Assign an owned child to its kernel Job during CreateProcess (Windows 10+)."""
import ctypes
from contextlib import contextmanager

EXTENDED_STARTUPINFO_PRESENT = 0x00080000
PROC_THREAD_ATTRIBUTE_JOB_LIST = 0x0002000D


@contextmanager
def job_startup_info(startup, guard):
    """Borrow the non-inheritable Job handle; never fall back to later assign.

    guard must outlive CreateProcess. All ctypes buffers remain alive through
    DeleteProcThreadAttributeList. Failure prevents user command execution.
    """
    from packages.core.platform import supports
    from packages.core.platform.process import UnsupportedPlatformError
    if not supports("process.hiddenDesktop"):
        raise UnsupportedPlatformError("process.hiddenDesktop unsupported on this platform")
    from ctypes import wintypes as w
    class StartupInfoEx(ctypes.Structure):
        _fields_ = [('StartupInfo', type(startup)), ('lpAttributeList', ctypes.c_void_p)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.InitializeProcThreadAttributeList.argtypes = [ctypes.c_void_p, w.DWORD, w.DWORD, ctypes.POINTER(ctypes.c_size_t)]
    kernel.InitializeProcThreadAttributeList.restype = w.BOOL
    kernel.UpdateProcThreadAttribute.argtypes = [ctypes.c_void_p, w.DWORD, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p]
    kernel.UpdateProcThreadAttribute.restype = w.BOOL
    kernel.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
    kernel.DeleteProcThreadAttributeList.restype = None
    size = ctypes.c_size_t()
    kernel.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
    if not size.value:
        raise ctypes.WinError(ctypes.get_last_error())
    buffer = ctypes.create_string_buffer(size.value)
    if not kernel.InitializeProcThreadAttributeList(buffer, 1, 0, ctypes.byref(size)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        handle = guard.raw_handle
        if not handle:
            raise RuntimeError('owned creation Job handle is closed')
        jobs = (w.HANDLE * 1)(handle)
        if not kernel.UpdateProcThreadAttribute(buffer, 0, PROC_THREAD_ATTRIBUTE_JOB_LIST,
                                                jobs, ctypes.sizeof(jobs), None, None):
            raise ctypes.WinError(ctypes.get_last_error())
        extended = StartupInfoEx()
        extended.StartupInfo = startup
        extended.StartupInfo.cb = ctypes.sizeof(extended)
        extended.lpAttributeList = ctypes.addressof(buffer)
        yield extended
    finally:
        kernel.DeleteProcThreadAttributeList(buffer)
