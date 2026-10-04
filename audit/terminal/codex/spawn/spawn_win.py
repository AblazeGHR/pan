"""Atomic ConPTY spawn for Windows -- runnable spike / interface draft.

Why this exists
---------------
The first-version implementation plan (``PAN_TERMINAL_IMPLEMENTATION_PLAN_20261003.md``
§5.3) requires four properties before a terminal may be published as ``running``:
``assigned`` + ``atomic_with_spawn`` + ``identity`` + ``handle_bound_for_cleanup``.
``pywinpty.spawn`` does not expose CreateProcess creation flags, so
"spawn first, AssignProcessToJobObject second" leaves a window in which a child
(or its descendants) can exist outside the guard job. That window is
structurally unclosable with pywinpty, so the plan forbids it as a production
path and asks for a suspended-spawn implementation instead.

This module implements that sequence directly on kernel32 via ctypes:

    CreatePipe x2
      -> CreatePseudoConsole
      -> InitializeProcThreadAttributeList / UpdateProcThreadAttribute
         (PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE)
      -> CreateProcessW(CREATE_SUSPENDED | EXTENDED_STARTUPINFO_PRESENT)
      -> AssignProcessToJobObject(guard, child)     # child has never run yet
      -> ResumeThread(primary thread)

Atomicity claim, stated precisely: between process creation and the guard-job
assignment the child's primary thread has never been scheduled, so it cannot
have executed user code and cannot have created any descendant. Once the guard
job is ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``, every descendant it later
creates is inside that job by default (Microsoft: "by default any child
processes it creates using CreateProcess are also associated with the job"),
so a single kill-on-close handle covers the tree.

Failure is fail-closed: if the assignment fails, the child is *never resumed*,
the module never reports a live session, and it terminates only the process it
created itself (through the retained handle) before raising.

Official sources (read for this spike, cited in the report):
  * Creating a Pseudoconsole session -- sequence, EXTENDED_STARTUPINFO_PRESENT,
    "close these after CreateProcess", drain-before-close warning, 0xc0000142.
    https://learn.microsoft.com/en-us/windows/console/creating-a-pseudoconsole-session
  * CreatePseudoConsole -- HRESULT, mandatory ClosePseudoConsole, PSEUDOCONSOLE_INHERIT_CURSOR.
    https://learn.microsoft.com/en-us/windows/console/createpseudoconsole
  * ClosePseudoConsole -- CTRL_CLOSE_EVENT to clients; "close the output pipe before
    calling ClosePseudoConsole or ... continue reading"; Windows 11 24H2 (26100+)
    returns immediately, earlier versions wait indefinitely.
    https://learn.microsoft.com/en-us/windows/console/closepseudoconsole
  * AssignProcessToJobObject -- hProcess needs PROCESS_SET_QUOTA|PROCESS_TERMINATE;
    "the job specified by hJob must be empty or it must be in the hierarchy of
    nested jobs to which the process already belongs, and it cannot have UI limits
    set"; JOB_OBJECT_SECURITY_ONLY_TOKEN requires a suspended process.
    https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-assignprocesstojobobject
  * Job Objects -- "After a process is associated with a job, the association
    cannot be broken"; kill-on-close fires on the *last* handle; default child
    inheritance; BREAKAWAY_OK / SILENT_BREAKAWAY_OK; and the explicit warning
    that with neither breakaway flag "if a child process attempts to associate
    itself or another child process with a job by calling
    AssignProcessToJobObject, the call will fail".
    https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects
  * ResumeThread -- decrements suspend count; returns previous count, (DWORD)-1 on failure.
    https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-resumethread

Scope / guarantees of this spike
--------------------------------
* Only ever creates, inspects and terminates processes it created itself.
* No shell-out, no ``taskkill``, no WMI/CIM process termination.
* Every blocking wait is bounded by an explicit timeout.
* Retains raw handles (``hProcess`` / ``hThread`` / ``hpc`` / pipe ends) so that
  identity checks and termination act on the *same* kernel object rather than
  re-opening by PID (the TOCTOU lesson recorded in the lifecycle report §5.3).
"""
from __future__ import annotations

import ctypes
import os
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass, field

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# --------------------------------------------------------------------------- constants
CREATE_SUSPENDED = 0x00000004
CREATE_UNICODE_ENVIRONMENT = 0x00000400
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
STARTF_USESTDHANDLES = 0x00000100

PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JobObjectExtendedLimitInformation = 9

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_SET_QUOTA = 0x0100
PROCESS_TERMINATE = 0x0001
SYNCHRONIZE = 0x00100000
THREAD_TERMINATE = 0x0001
THREAD_SUSPEND_RESUME = 0x0002

# GetExitCodeProcess reports STILL_ACTIVE (259) both while a process runs AND
# when a process genuinely exits with code 259. It is therefore an informational
# field only; liveness always comes from WaitForSingleObject(handle, 0).
STILL_ACTIVE = 259
WAIT_OBJECT_0 = 0x00000000
WAIT_ABANDONED = 0x00000080
WAIT_TIMEOUT = 0x00000102
WAIT_FAILED = 0xFFFFFFFF
INFINITE = 0xFFFFFFFF

HPCON = wintypes.HANDLE


# --------------------------------------------------------------------------- structs
class COORD(ctypes.Structure):
    _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.c_void_p),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [("StartupInfo", STARTUPINFOW), ("lpAttributeList", ctypes.c_void_p)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


class LARGE_INTEGER_UNION(ctypes.Union):
    _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG), ("QuadPart", ctypes.c_longlong)]


class LARGE_INTEGER(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("u", LARGE_INTEGER_UNION)]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", LARGE_INTEGER),
        ("PerJobUserTimeLimit", LARGE_INTEGER),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_void_p),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", LARGE_INTEGER),
        ("TotalKernelTime", LARGE_INTEGER),
        ("ThisPeriodTotalUserTime", LARGE_INTEGER),
        ("ThisPeriodTotalKernelTime", LARGE_INTEGER),
        ("TotalPageFaultCount", wintypes.DWORD),
        ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD),
        ("TotalTerminatedProcesses", wintypes.DWORD),
    ]


JobObjectBasicAccountingInformation = 1

# --------------------------------------------------------------------------- prototypes
kernel32.CreatePipe.argtypes = [ctypes.POINTER(wintypes.HANDLE), ctypes.POINTER(wintypes.HANDLE), ctypes.c_void_p, wintypes.DWORD]
kernel32.CreatePipe.restype = wintypes.BOOL

kernel32.CreatePseudoConsole.argtypes = [COORD, wintypes.HANDLE, wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(HPCON)]
kernel32.CreatePseudoConsole.restype = ctypes.c_long  # HRESULT

kernel32.ResizePseudoConsole.argtypes = [HPCON, COORD]
kernel32.ResizePseudoConsole.restype = ctypes.c_long  # HRESULT

kernel32.ClosePseudoConsole.argtypes = [HPCON]
kernel32.ClosePseudoConsole.restype = None

kernel32.InitializeProcThreadAttributeList.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_size_t)]
kernel32.InitializeProcThreadAttributeList.restype = wintypes.BOOL

kernel32.UpdateProcThreadAttribute.argtypes = [
    ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p,
    ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p,
]
kernel32.UpdateProcThreadAttribute.restype = wintypes.BOOL

kernel32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
kernel32.DeleteProcThreadAttributeList.restype = None

kernel32.CreateProcessW.argtypes = [
    wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, wintypes.BOOL,
    wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(PROCESS_INFORMATION),
]
kernel32.CreateProcessW.restype = wintypes.BOOL

kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
kernel32.ResumeThread.restype = wintypes.DWORD

kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
kernel32.CreateJobObjectW.restype = wintypes.HANDLE

kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
kernel32.SetInformationJobObject.restype = wintypes.BOOL

kernel32.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
kernel32.QueryInformationJobObject.restype = wintypes.BOOL

kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
kernel32.AssignProcessToJobObject.restype = wintypes.BOOL

kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
kernel32.TerminateJobObject.restype = wintypes.BOOL

kernel32.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
kernel32.IsProcessInJob.restype = wintypes.BOOL

kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME)]
kernel32.GetProcessTimes.restype = wintypes.BOOL

kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
kernel32.GetExitCodeProcess.restype = wintypes.BOOL

kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE

kernel32.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenThread.restype = wintypes.HANDLE

kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
kernel32.TerminateProcess.restype = wintypes.BOOL

kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.WaitForSingleObject.restype = wintypes.DWORD

kernel32.CancelSynchronousIo.argtypes = [wintypes.HANDLE]
kernel32.CancelSynchronousIo.restype = wintypes.BOOL

kernel32.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
kernel32.ReadFile.restype = wintypes.BOOL

kernel32.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
kernel32.WriteFile.restype = wintypes.BOOL

kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL

kernel32.GetCurrentThreadId.restype = wintypes.DWORD
kernel32.GetCurrentProcess.restype = wintypes.HANDLE


# --------------------------------------------------------------------------- helpers
def filetime_to_int(ft: wintypes.FILETIME) -> int:
    """Raw 100-nanosecond timestamp since 1601-01-01, as an exact integer."""
    return (ft.dwHighDateTime << 32) | ft.dwLowDateTime


def read_creation_filetime(hprocess: wintypes.HANDLE) -> int | None:
    """Read the raw 64-bit creation FILETIME from an already-open process handle.

    ``GetProcessTimes`` is documented with four ``[out]`` FILETIME parameters and
    none of them is marked optional
    (<https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-getprocesstimes>),
    so all four pointers are always supplied here; passing NULL would violate the
    API's pointer contract. The value returned is the raw 100-nanosecond count
    since 1601-01-01 as an exact Python int (64-bit), used for identity
    comparison rather than any tolerance-based "same second" heuristic.
    """
    ft, exit_ft, kernel_ft, user_ft = (wintypes.FILETIME() for _ in range(4))
    ok = kernel32.GetProcessTimes(
        hprocess, ctypes.byref(ft), ctypes.byref(exit_ft),
        ctypes.byref(kernel_ft), ctypes.byref(user_ft),
    )
    return filetime_to_int(ft) if ok else None


def wait_state(hprocess: wintypes.HANDLE) -> str:
    """The single liveness primitive: ``WaitForSingleObject(handle, 0)``.

    Returns exactly one of:
      ``"dead"``    -- WAIT_OBJECT_0: the process object is signaled (exited).
      ``"alive"``   -- WAIT_TIMEOUT: still running.
      ``"unknown"`` -- anything else (WAIT_FAILED, WAIT_ABANDONED, unexpected).

    "unknown" is never treated as either answer: callers must refuse rather than
    guess. ``GetExitCodeProcess`` is deliberately not used here, because a
    process that exits with code 259 is indistinguishable from a running one by
    that API alone, and a detached/never-signaled handle must not be reported as
    alive on the strength of an exit-code read.
    """
    rc = kernel32.WaitForSingleObject(hprocess, 0)
    if rc == WAIT_OBJECT_0:
        return "dead"
    if rc == WAIT_TIMEOUT:
        return "alive"
    return "unknown"


def read_exit_code_informational(hprocess: wintypes.HANDLE) -> int | None:
    """Exit code as *information only* (see the STILL_ACTIVE caveat above)."""
    code = wintypes.DWORD(0)
    if not kernel32.GetExitCodeProcess(hprocess, ctypes.byref(code)):
        return None
    return int(code.value)


def is_process_in_job(hprocess: wintypes.HANDLE, hjob: wintypes.HANDLE) -> bool:
    """``IsProcessInJob`` with an explicit or NULL job handle.

    Official parameter semantics
    (<https://learn.microsoft.com/en-us/windows/win32/api/jobapi/nf-jobapi-isprocessinjob>):
    ``JobHandle`` is "A handle to the job. **If this parameter is NULL, the
    function tests if the process is running under any job.**"

    So ``hjob=None`` asks "is this process in *any* job at all" -- it is not a
    question about the caller's job. Pass the explicit guard handle when the
    question is "is this process in *our* job", which is the only form suitable
    for a gate.
    """
    in_job = wintypes.BOOL()
    if not kernel32.IsProcessInJob(hprocess, hjob, ctypes.byref(in_job)):
        raise ctypes.WinError(ctypes.get_last_error())
    return bool(in_job.value)


def is_caller_in_any_job() -> bool:
    """Whether *this* process is running under any job (ambient-job question)."""
    return is_process_in_job(kernel32.GetCurrentProcess(), None)


def hresult_failed(hr: int) -> bool:
    return ctypes.c_long(hr).value < 0


def hresult_hex(hr: int) -> str:
    return f"0x{ctypes.c_ulong(hr).value & 0xFFFFFFFF:08X}"


# --------------------------------------------------------------------------- guard job
class GuardJob:
    """``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` job owned by the caller.

    Closing the last handle of this job terminates every associated process
    (Microsoft: "closing the last job object handle terminates all associated
    processes and then destroys the job object itself"). That is the intended
    cleanup mechanism: the owner process dying is enough, no cleanup code and
    no taskkill required.
    """

    def __init__(self, name: str | None = None) -> None:
        self._handle = kernel32.CreateJobObjectW(None, name)
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = kernel32.SetInformationJobObject(
            self._handle, JobObjectExtendedLimitInformation,
            ctypes.byref(info), ctypes.sizeof(info),
        )
        if not ok:
            err = ctypes.get_last_error()
            kernel32.CloseHandle(self._handle)
            self._handle = None
            raise ctypes.WinError(err)

    @property
    def raw_handle(self) -> wintypes.HANDLE:
        return self._handle

    def assign(self, hprocess: wintypes.HANDLE) -> tuple[bool, int]:
        """Assign a (suspended) process. Returns (ok, GetLastError)."""
        ctypes.set_last_error(0)
        ok = bool(kernel32.AssignProcessToJobObject(self._handle, hprocess))
        return ok, ctypes.get_last_error()

    def assign_via_invalid_handle(self, hprocess: wintypes.HANDLE) -> tuple[bool, int]:
        """Injection hook: call AssignProcessToJobObject with a bogus job handle.

        Used only by the failure-path scenario to exercise the fail-closed
        branch deterministically, without depending on ambient-job behaviour.
        """
        ctypes.set_last_error(0)
        ok = bool(kernel32.AssignProcessToJobObject(wintypes.HANDLE(0xDEAD), hprocess))
        return ok, ctypes.get_last_error()

    def active_processes(self) -> int | None:
        """Member count of *this* guard job, or ``None`` when we hold no handle.

        Measured trap: ``QueryInformationJobObject`` accepts NULL for its job
        handle and, per its documentation, then reports on "the job object" of
        the **calling process** rather than failing. A closed owner therefore did
        not produce an error but a perfectly valid count belonging to the
        *ambient* job (measured: 20) -- which would have been silently mistaken
        for our guard's membership. The explicit ``None`` return prevents that;
        it is a guard against querying the wrong job, not against stale data.
        """
        if not self._handle:
            return None
        info = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
        returned = wintypes.DWORD(0)
        ok = kernel32.QueryInformationJobObject(
            self._handle, JobObjectBasicAccountingInformation,
            ctypes.byref(info), ctypes.sizeof(info), ctypes.byref(returned),
        )
        return int(info.ActiveProcesses) if ok else None

    def terminate(self, exit_code: int = 1) -> bool:
        return bool(kernel32.TerminateJobObject(self._handle, exit_code))

    def close(self) -> None:
        """Close our handle. If we are the last holder, the tree dies."""
        if self._handle:
            kernel32.CloseHandle(self._handle)
            self._handle = None


# --------------------------------------------------------------------------- errors
class SpawnDenied(RuntimeError):
    """Fail-closed startup rejection: the child was never resumed."""

    def __init__(self, stage: str, detail: dict) -> None:
        super().__init__(f"atomic spawn denied at {stage}: {detail}")
        self.stage = stage
        self.detail = detail


# --------------------------------------------------------------------------- session
@dataclass
class SpawnRecord:
    """Identity + retained handles for one atomic spawn."""
    pid: int
    creation_filetime: int
    creation_filetime_iso: str
    command_line: str
    cwd: str
    cols: int
    rows: int
    assign_ok: bool
    assign_last_error: int
    assign_last_error_name: str
    resumed: bool
    resume_previous_suspend_count: int | None
    child_in_guard_job: bool
    child_in_ambient_job: bool
    ambient_job_before_spawn: bool
    guard_active_after_assign: int | None
    timings: dict = field(default_factory=dict)


class ConPtySession:
    """Owns a ConPTY, its pipes, the child process handles and the guard job binding."""

    def __init__(self) -> None:
        self.record: SpawnRecord | None = None
        self._hpc = None
        self._h_process = None
        self._h_thread = None
        self._input_read = None
        self._input_write = None
        self._output_read = None
        self._output_write = None
        self._closed = False
        self._attr_buf = None
        self.resume_detail: dict = {}
        self._reader_thread: threading.Thread | None = None
        self._reader_stop = threading.Event()
        self._reader_thread_handle = None
        self._reader_error: str | None = None
        self.output_chunks: list[bytes] = []
        self.reader_cancelled = threading.Event()
        self.reader_returned = threading.Event()

    # ---------------------------------------------------------------- spawn
    @classmethod
    def spawn(
        cls,
        command_line: str,
        cwd: str,
        cols: int,
        rows: int,
        guard: GuardJob,
        *,
        inject_assign_failure: bool = False,
        inject_membership_query_failure: bool = False,
        inject_membership_false: bool = False,
        inject_resume_count: int | None = None,
        resume: bool = True,
        creation_flags_extra: int = 0,
        use_std_handles_null: bool = True,
        inherit_handles: bool = False,
    ) -> "ConPtySession":
        """Create the child suspended, bind it to ``guard``, then resume.

        Every gate below is fail-closed: if the guard-job assignment cannot be
        *proved*, or the resume cannot be *proved* to have happened exactly once,
        the child is never resumed, this session is never returned, and the
        attempt raises :class:`SpawnDenied` after cleaning up only what this call
        created.

        The three ``inject_*`` hooks exist solely so the driver can exercise the
        refusal branches deterministically. They are wrapper-level simulations of
        failures -- they are **not** reproductions of real OS errors, and the
        evidence they produce must not be read as such.
        """
        self = cls()
        t0 = time.perf_counter()
        timings: dict[str, float] = {}
        ambient = is_caller_in_any_job()
        stage = "create_pipes"

        try:
            # 1) communication channels (synchronous pipes, per official sample)
            r_in, w_in = wintypes.HANDLE(), wintypes.HANDLE()
            r_out, w_out = wintypes.HANDLE(), wintypes.HANDLE()
            if not kernel32.CreatePipe(ctypes.byref(r_in), ctypes.byref(w_in), None, 0):
                raise ctypes.WinError(ctypes.get_last_error())
            if not kernel32.CreatePipe(ctypes.byref(r_out), ctypes.byref(w_out), None, 0):
                kernel32.CloseHandle(r_in); kernel32.CloseHandle(w_in)
                raise ctypes.WinError(ctypes.get_last_error())
            self._input_read, self._input_write = r_in, w_in
            self._output_read, self._output_write = r_out, w_out
            timings["create_pipes_ms"] = round((time.perf_counter() - t0) * 1000, 2)

            # 2) pseudoconsole
            stage = "create_pseudo_console"
            t1 = time.perf_counter()
            hpc = HPCON()
            hr = kernel32.CreatePseudoConsole(COORD(cols, rows), self._input_read, self._output_write, 0, ctypes.byref(hpc))
            if hresult_failed(hr):
                raise ctypes.WinError(ctypes.get_last_error())
            self._hpc = hpc
            timings["create_pseudo_console_ms"] = round((time.perf_counter() - t1) * 1000, 2)

            # 3) attribute list carrying PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE
            stage = "attribute_list"
            size = ctypes.c_size_t(0)
            kernel32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
            if not size.value:
                raise ctypes.WinError(ctypes.get_last_error())
            attr_buf = ctypes.create_string_buffer(size.value)
            if not kernel32.InitializeProcThreadAttributeList(attr_buf, 1, 0, ctypes.byref(size)):
                raise ctypes.WinError(ctypes.get_last_error())
            self._attr_buf = attr_buf  # keep alive for the duration of the spawn
            if not kernel32.UpdateProcThreadAttribute(
                attr_buf, 0, ctypes.c_void_p(PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE),
                ctypes.c_void_p(self._hpc.value), ctypes.sizeof(HPCON), None, None,
            ):
                raise ctypes.WinError(ctypes.get_last_error())

            si = STARTUPINFOEXW()
            si.StartupInfo.cb = ctypes.sizeof(STARTUPINFOEXW)
            si.lpAttributeList = ctypes.cast(attr_buf, ctypes.c_void_p)
            # SPIKE FINDING (measured on this machine; see s11 for the committed
            # matrix): creating the pseudoconsole is not enough. If
            # STARTF_USESTDHANDLES is left unset, CreateProcess copies the
            # *parent's* standard handles into the child, and those copies shadow
            # the pseudoconsole's console handles: the child's console is the pty
            # (writes to CONOUT$ do reach the pty) but its stdout/stderr still
            # point at whatever the parent had. Setting STARTF_USESTDHANDLES with
            # NULL handles makes the console subsystem hand the child the pty's
            # own handles instead. In the s11 matrix (this machine, this build)
            # the flag alone decided the outcome in all four combinations; that
            # is not a claim about every Windows build or every parent topology.
            if use_std_handles_null:
                si.StartupInfo.dwFlags = STARTF_USESTDHANDLES
                si.StartupInfo.hStdInput = None
                si.StartupInfo.hStdOutput = None
                si.StartupInfo.hStdError = None
            pi = PROCESS_INFORMATION()

            # 4) suspended creation -- the child has not executed a single instruction
            stage = "create_process_suspended"
            t2 = time.perf_counter()
            cmd_buf = ctypes.create_unicode_buffer(command_line)
            flags = CREATE_SUSPENDED | EXTENDED_STARTUPINFO_PRESENT | CREATE_UNICODE_ENVIRONMENT | creation_flags_extra
            if not kernel32.CreateProcessW(
                None, cmd_buf, None, None, inherit_handles, flags, None,
                cwd or None, ctypes.byref(si), ctypes.byref(pi),
            ):
                raise ctypes.WinError(ctypes.get_last_error())
            self._h_process, self._h_thread = pi.hProcess, pi.hThread
            timings["create_process_ms"] = round((time.perf_counter() - t2) * 1000, 2)

            # Official step: after CreateProcess, drop the two handles that were
            # handed to CreatePseudoConsole. Keeping our own copies alive would
            # hold a reference on the underlying device object, so the output
            # pipe would never report a broken channel / EOF once the
            # pseudoconsole closes its side. Only the "client" ends we actually
            # use are retained: input_write (send keys) and output_read (drain).
            if self._input_read:
                kernel32.CloseHandle(self._input_read)
                self._input_read = None
            if self._output_write:
                kernel32.CloseHandle(self._output_write)
                self._output_write = None

            # 5) identity -- captured from the retained handle, never re-opened by PID
            creation = read_creation_filetime(pi.hProcess)
            if creation is None:
                raise ctypes.WinError(ctypes.get_last_error())

            # 6) guard-job assignment while still suspended (the atomicity step)
            stage = "assign_guard_job"
            t3 = time.perf_counter()
            if inject_assign_failure:
                assign_ok, assign_err = guard.assign_via_invalid_handle(pi.hProcess)
            else:
                assign_ok, assign_err = guard.assign(pi.hProcess)
            timings["assign_ms"] = round((time.perf_counter() - t3) * 1000, 2)

            if not assign_ok:
                raise self._deny(stage, {
                    "pid": int(pi.dwProcessId),
                    "creation_filetime": creation,
                    "assign_last_error": assign_err,
                    "assign_last_error_name": _winerr_name(assign_err),
                    "resumed": False,
                    "note": "child was never resumed; terminating only this self-created process",
                }, hprocess=pi.hProcess)

            # Membership must be PROVED. A failed query and a negative answer are
            # both treated as "not assigned": the plan's gate requires
            # `assigned`, and an unproven guard binding must never reach a
            # `running` state. Previously a query failure silently fell through
            # to resume, which is exactly the hole this closes.
            child_in_guard = False
            membership_error: str | None = None
            if inject_membership_query_failure:
                membership_error = "injected: membership query failure (wrapper-level simulation)"
            else:
                try:
                    child_in_guard = is_process_in_job(pi.hProcess, guard.raw_handle)
                except OSError as exc:
                    membership_error = f"membership query raised: {exc!r}"
                if membership_error is None and inject_membership_false:
                    # force the *reported* answer to false, whatever the real one is
                    child_in_guard = False
                if membership_error is None and not child_in_guard:
                    membership_error = ("injected: membership reported false" if inject_membership_false
                                        else "guard job reports the child is not a member")

            if membership_error is not None:
                raise self._deny("verify_guard_membership", {
                    "pid": int(pi.dwProcessId),
                    "creation_filetime": creation,
                    "child_in_guard_job": child_in_guard,
                    "membership_error": membership_error,
                    "resumed": False,
                    "injected": bool(inject_membership_query_failure or inject_membership_false),
                    "note": "membership unproven -> fail-closed, child never resumed",
                }, hprocess=pi.hProcess)

            # 7) resume -- and verify it actually happened exactly once.
            prev = None
            resumed = False
            if resume:
                stage = "resume_thread"
                if inject_resume_count is not None:
                    r = inject_resume_count
                else:
                    r = kernel32.ResumeThread(pi.hThread)
                if r == WAIT_FAILED:
                    raise self._deny(stage, {
                        "last_error": ctypes.get_last_error(),
                        "resume_return": "WAIT_FAILED",
                        "resumed": False,
                    }, hprocess=pi.hProcess)
                if r != 1:
                    # A CREATE_SUSPENDED thread must report a previous suspend
                    # count of exactly 1. 0 means it was never suspended (so the
                    # "never ran before the guard assignment" claim is void) and
                    # >1 means it is still suspended after this call (so the
                    # child is not running). Neither may be recorded as resumed.
                    raise self._deny(stage, {
                        "resume_return": int(r),
                        "expected_previous_suspend_count": 1,
                        "injected": inject_resume_count is not None,
                        "resumed": False,
                        "note": "unexpected previous suspend count -> fail-closed",
                    }, hprocess=pi.hProcess)
                prev = int(r)
                resumed = True

            self.record = SpawnRecord(
                pid=int(pi.dwProcessId),
                creation_filetime=creation,
                creation_filetime_iso=_filetime_iso(creation),
                command_line=command_line,
                cwd=cwd,
                cols=cols,
                rows=rows,
                assign_ok=True,
                assign_last_error=assign_err,
                assign_last_error_name=_winerr_name(assign_err),
                resumed=resumed,
                resume_previous_suspend_count=prev,
                child_in_guard_job=child_in_guard,
                child_in_ambient_job=is_process_in_job(pi.hProcess, None),
                ambient_job_before_spawn=ambient,
                guard_active_after_assign=guard.active_processes(),
                timings=timings,
            )
            return self

        except SpawnDenied:
            raise
        except Exception as exc:
            # any unexpected failure must also fail closed, and must release the
            # pseudoconsole + attribute list rather than leaking them
            detail = {"exception": repr(exc), "resumed": False}
            raise self._deny(stage, detail, hprocess=self._h_process) from exc

    # ---------------------------------------------------------------- ops
    def resume(self) -> tuple[bool, int | None]:
        """Resume a session that was deliberately left suspended.

        Uses exactly the same gate as ``spawn(resume=True)``: the call counts as
        a resume only when ``ResumeThread`` reports a previous suspend count of
        **exactly 1**. Anything else (``WAIT_FAILED``, ``0``, ``>1``) is refused
        and ``record.resumed`` is left untouched, so the factual record never
        claims a resume that did not happen.

        A session that has already been resumed is refused as ``already_resumed``
        rather than being re-issued against a live thread -- a second
        ``ResumeThread`` on an unsuspended thread returns 0, which previously was
        recorded as ``resumed=True`` even though nothing was resumed.

        Detailed outcome is available on ``resume_detail``.
        """
        if self.record is not None and self.record.resumed:
            self.resume_detail = {
                "ok": False,
                "reason": "already_resumed",
                "currently_resumed": True,
                "previous_suspend_count": None,
            }
            return False, None
        if not self._h_thread:
            self.resume_detail = {"ok": False, "reason": "no_thread_handle",
                                  "currently_resumed": False}
            return False, None

        r = kernel32.ResumeThread(self._h_thread)
        if r == WAIT_FAILED:
            self.resume_detail = {
                "ok": False,
                "reason": "resume_call_failed",
                "last_error": ctypes.get_last_error(),
                "currently_resumed": False,
            }
            return False, None
        if r != 1:
            self.resume_detail = {
                "ok": False,
                "reason": "unexpected_previous_suspend_count",
                "expected_previous_suspend_count": 1,
                "previous_suspend_count": int(r),
                "currently_resumed": False,
            }
            return False, None

        if self.record:
            self.record.resumed = True
            self.record.resume_previous_suspend_count = 1
        self.resume_detail = {
            "ok": True,
            "reason": "resumed",
            "previous_suspend_count": 1,
            "currently_resumed": True,
        }
        return True, 1

    def resize(self, cols: int, rows: int) -> tuple[bool, str]:
        hr = kernel32.ResizePseudoConsole(self._hpc, COORD(cols, rows))
        return (not hresult_failed(hr)), hresult_hex(hr)

    def write_input(self, data: bytes) -> int:
        written = wintypes.DWORD(0)
        buf = ctypes.create_string_buffer(data, len(data))
        if not kernel32.WriteFile(self._input_write, buf, len(data), ctypes.byref(written), None):
            raise ctypes.WinError(ctypes.get_last_error())
        return int(written.value)

    def start_reader(self) -> None:
        """Drain the output pipe on its own thread.

        Microsoft: the channels should be serviced on separate threads and
        remain drained; a single-threaded synchronous host risks deadlock.
        """
        def _loop() -> None:
            # A real handle to *this* thread is required by CancelSynchronousIo.
            self._reader_thread_handle = kernel32.OpenThread(THREAD_TERMINATE, False, kernel32.GetCurrentThreadId())
            buf = ctypes.create_string_buffer(4096)
            read = wintypes.DWORD(0)
            while not self._reader_stop.is_set():
                ctypes.set_last_error(0)
                ok = kernel32.ReadFile(self._output_read, buf, 4096, ctypes.byref(read), None)
                if not ok:
                    err = ctypes.get_last_error()
                    self._reader_error = f"ReadFile failed: {err} ({_winerr_name(err)})"
                    break
                if read.value == 0:
                    self._reader_error = "EOF"
                    break
                self.output_chunks.append(buf.raw[: read.value])
            self.reader_returned.set()

        self.reader_returned.clear()
        self._reader_thread = threading.Thread(target=_loop, name="conpty-reader", daemon=True)
        self._reader_thread.start()

    def _stop_reader(self, timeout: float = 5.0) -> dict:
        """Stop the drain reader and **confirm the thread has actually exited**.

        Confirmation is by ``Thread.join`` / ``is_alive``, never by the
        completion Event: the reader sets that Event as its last action before
        returning, so there is a window in which the Event is already set while
        the thread is still running (and may still touch handles). Ownership
        decisions are therefore based on the thread having exited, not on the
        Event.

        The stop flag is raised *before* any cancellation, so a
        ``CancelSynchronousIo`` that lands in the gap between two ``ReadFile``
        calls -- where there is no pending I/O to cancel -- still leaves the
        reader exiting at its next loop check instead of blocking again. Because
        that gap is real, cancellation is retried until the thread is gone or the
        deadline passes.
        """
        t0 = time.perf_counter()
        thread = self._reader_thread
        if thread is None:
            return {"reader_present": False, "stopped": True, "converge_seconds": 0.0}

        self._reader_stop.set()
        deadline = t0 + timeout

        # the reader publishes its own thread handle as its first action
        while self._reader_thread_handle is None and thread.is_alive() and time.perf_counter() < deadline:
            time.sleep(0.02)

        attempts = 0
        last_ok: bool | None = None
        last_err: int | None = None
        while thread.is_alive() and time.perf_counter() < deadline:
            if self._reader_thread_handle:
                ctypes.set_last_error(0)
                ok = bool(kernel32.CancelSynchronousIo(self._reader_thread_handle))
                last_ok = ok
                last_err = ctypes.get_last_error()
                attempts += 1
                if ok:
                    self.reader_cancelled.set()
            thread.join(timeout=0.05)

        thread.join(timeout=max(0.0, deadline - time.perf_counter()))
        stopped = not thread.is_alive()
        return {
            "reader_present": True,
            "stop_flag_set_first": True,
            "thread_handle_obtained": bool(self._reader_thread_handle),
            "cancel_attempts": attempts,
            "cancel_ok": last_ok,
            "cancel_last_error": last_err,
            "stopped": stopped,
            "converge_seconds": round(time.perf_counter() - t0, 3),
            "reader_error": self._reader_error,
        }

    def cancel_read(self, timeout: float = 5.0) -> dict:
        """Backwards-compatible view of :meth:`_stop_reader`."""
        result = self._stop_reader(timeout)
        result["reader_converged"] = result["stopped"]
        return result

    def cancel_read_by_closing_output(self, timeout: float = 5.0) -> dict:
        """Alternative convergence path: close the read end under the reader."""
        t0 = time.perf_counter()
        if self._output_read:
            kernel32.CloseHandle(self._output_read)
            self._output_read = None
        converged = self.reader_returned.wait(timeout=timeout)
        return {
            "reader_converged": bool(converged),
            "converge_seconds": round(time.perf_counter() - t0, 3),
            "reader_error": self._reader_error,
        }

    def output_bytes(self) -> bytes:
        return b"".join(self.output_chunks)

    def output_text(self) -> str:
        return self.output_bytes().decode("utf-8", "replace")

    def wait_output_contains(self, needle: str, timeout: float) -> bool:
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            if needle in self.output_text():
                return True
            time.sleep(0.05)
        return needle in self.output_text()

    # ---------------------------------------------------------------- identity
    def identity(self) -> tuple[int, int] | None:
        return (self.record.pid, self.record.creation_filetime) if self.record else None

    def liveness(self) -> str:
        """``"dead"`` / ``"alive"`` / ``"unknown"`` for the retained handle."""
        if not self._h_process:
            return "unknown"
        return wait_state(self._h_process)

    def is_alive(self) -> bool:
        """True only for a positive ``wait_state == "alive"``.

        Uses the retained handle, so the answer concerns the exact kernel object
        this session created -- never a PID that may since have been reused.
        """
        return self.liveness() == "alive"

    def wait_exit(self, timeout: float) -> int | None:
        rc = kernel32.WaitForSingleObject(self._h_process, int(timeout * 1000))
        if rc == WAIT_OBJECT_0:
            return read_exit_code_informational(self._h_process)
        return None

    def exit_code(self) -> int | None:
        """Informational exit code; may read 259 for a genuinely exited process."""
        return read_exit_code_informational(self._h_process)

    # ---------------------------------------------------------------- teardown
    def _terminate_self_created(self, hprocess) -> None:
        """Terminate exactly the process this module created, via its retained handle."""
        if hprocess:
            try:
                kernel32.TerminateProcess(hprocess, 0xDEAD)
            except Exception:
                pass

    def _destroy_pty(self) -> dict:
        """Close the pseudoconsole handle if we still own one.

        Previously the denial paths never reached this, so an assignment failure
        leaked the HPCON. Every failure path now calls it.
        """
        if not self._hpc:
            return {"pty_closed": False, "already_gone": True}
        try:
            kernel32.ClosePseudoConsole(self._hpc)
            self._hpc = None
            return {"pty_closed": True}
        except Exception as exc:
            return {"pty_closed": False, "error": repr(exc)}

    def _delete_attr_list(self) -> dict:
        """Release the PROC_THREAD_ATTRIBUTE list allocation.

        ``DeleteProcThreadAttributeList`` is documented as the correct release
        for memory initialised by ``InitializeProcThreadAttributeList``; it was
        missing before, so each spawn leaked that heap block.
        """
        if self._attr_buf is None:
            return {"attr_list_deleted": False, "already_gone": True}
        try:
            kernel32.DeleteProcThreadAttributeList(self._attr_buf)
            self._attr_buf = None
            return {"attr_list_deleted": True}
        except Exception as exc:
            return {"attr_list_deleted": False, "error": repr(exc)}

    def _close_pipe_handles(self) -> list[str]:
        closed = []
        for name in ("_input_read", "_input_write", "_output_read", "_output_write"):
            h = getattr(self, name, None)
            if h:
                kernel32.CloseHandle(h)
                setattr(self, name, None)
                closed.append(name)
        return closed

    def _close_process_handles(self) -> list[str]:
        closed = []
        for name in ("_h_process", "_h_thread"):
            h = getattr(self, name, None)
            if h:
                kernel32.CloseHandle(h)
                setattr(self, name, None)
                closed.append(name)
        return closed

    def _release_handles(self) -> None:
        """Legacy name kept for callers: closes pipes + process handles only."""
        self._close_pipe_handles()
        self._close_process_handles()

    def _close_output_read(self) -> dict:
        """Close the ConPTY output read end.

        Microsoft's ``ClosePseudoConsole`` guidance is to close the output pipe
        before closing the pseudoconsole (or keep draining), so that a full pipe
        cannot block the call. This ordering is used on *every* release path --
        normal close and failure paths alike -- not only in ``close()``.
        """
        if not self._output_read:
            return {"output_closed": False, "already_gone": True}
        try:
            kernel32.CloseHandle(self._output_read)
            self._output_read = None
            return {"output_closed": True}
        except Exception as exc:
            return {"output_closed": False, "error": repr(exc)}

    def _full_local_cleanup(self, reader_timeout: float = 2.0) -> dict:
        """Release everything this session owns locally, in the official order.

        Order: stop the drain reader -> close the output pipe -> close the
        pseudoconsole -> delete the attribute list -> remaining pipe and process
        handles. Every step's result is checked and reported, so a caller can see
        exactly which resources were released and which were not, rather than
        being told "nothing was released".

        Used by the spawn-denial paths, where the session is discarded; the
        staged, retryable variant used by ``close()`` shares the same helpers.
        """
        result: dict = {
            "order": ["reader_stop", "output_close", "pty", "attr_list", "pipes", "process_handles"],
        }
        errors: list[str] = []

        reader = self._stop_reader(reader_timeout)
        result["reader_stop"] = reader
        if not reader.get("stopped", False):
            errors.append("reader_not_converged")

        output = self._close_output_read()
        result["output_close"] = output
        if not (output.get("output_closed") or output.get("already_gone")):
            errors.append("output_close_failed")

        pty = self._destroy_pty()
        result["pty"] = pty
        if not (pty.get("pty_closed") or pty.get("already_gone")):
            errors.append("pty_close_failed")

        attr = self._delete_attr_list()
        result["attr_list"] = attr
        if not (attr.get("attr_list_deleted") or attr.get("already_gone")):
            errors.append("attr_list_delete_failed")

        result["pipes_closed"] = self._close_pipe_handles()
        result["process_handles_closed"] = self._close_process_handles()
        result["errors"] = errors
        result["released"] = [
            name for name, ok in (
                ("reader", reader.get("stopped") or not reader.get("reader_present")),
                ("output_read", output.get("output_closed") or output.get("already_gone")),
                ("pseudo_console", pty.get("pty_closed") or pty.get("already_gone")),
                ("attr_list", attr.get("attr_list_deleted") or attr.get("already_gone")),
                ("pipes", bool(result["pipes_closed"])),
                ("process_handles", bool(result["process_handles_closed"])),
            ) if ok
        ]
        return result

    def _deny(self, stage: str, detail: dict, *, hprocess=None) -> SpawnDenied:
        """Fail-closed exit: verify-kill only our own child, then release all own
        resources (including the pseudoconsole and the attribute list)."""
        if hprocess is not None:
            self._terminate_self_created(hprocess)
        detail = dict(detail)
        detail["cleanup"] = self._full_local_cleanup()
        return SpawnDenied(stage, detail)

    def close(self, *, close_pty: bool = True, drain_timeout: float = 5.0) -> dict:
        """Bounded, ordered and *honest* teardown.

        ``closed=True`` is reported only when every step actually succeeded. If
        the drain reader cannot be brought to a stop within the bound, this
        returns ``closed=False, retryable=True`` and **keeps ownership**: the
        handle set is left intact so a later ``close()`` can retry, rather than
        releasing a reader thread that would still touch freed handles. The
        caller (and the driver's cleanup sweep) must treat a non-closed result as
        "still owned", not as "cleaned up".

        Ordering note: the output pipe is closed *before* ``ClosePseudoConsole``,
        which is the ordering Microsoft recommends so that a full output pipe
        cannot block the call. On this machine ClosePseudoConsole returns
        immediately (Windows 11 build 26200 >= the documented 26100 threshold);
        the blocking behaviour documented for older builds is **not tested here**
        and remains an unverified boundary.
        """
        if self._closed:
            return {"already_closed": True, "closed": True, "retryable": False}

        t0 = time.perf_counter()
        errors: list[str] = []

        # 1) stop the drain reader and CONFIRM the thread exited (join-based)
        reader = self._stop_reader(drain_timeout)
        if not reader.get("stopped", False):
            errors.append("reader_not_converged")

        if errors:
            # Ownership retained in full: the reader thread handle and the output
            # handle are deliberately NOT closed and NOT cleared, so a retry can
            # issue CancelSynchronousIo again against the same objects. Report
            # exactly which handles are still held.
            return {
                "closed": False,
                "retryable": True,
                "errors": errors,
                "reader_stop": reader,
                "released": [],
                "retained": self._retained_resources(),
                "seconds": round(time.perf_counter() - t0, 3),
                "note": "owner retained (not marked closed); retry re-issues cancellation on the same handles",
            }

        # 2) output pipe before the pseudoconsole (official ordering guidance)
        output = self._close_output_read()
        if not (output.get("output_closed") or output.get("already_gone")):
            errors.append("output_close_failed")

        # 3) pseudoconsole, then the attribute list
        pty = self._destroy_pty() if close_pty else {"pty_closed": False, "skipped": True}
        if close_pty and not (pty.get("pty_closed") or pty.get("already_gone")):
            errors.append("pty_close_failed")
        attr = self._delete_attr_list()
        if not (attr.get("attr_list_deleted") or attr.get("already_gone")):
            errors.append("attr_list_delete_failed")

        if errors:
            # A resource that failed to release stays owned and retryable; the
            # remaining handles are NOT torn down around it, and `_closed` stays
            # False so the failure cannot be mistaken for a completed teardown.
            return {
                "closed": False,
                "retryable": True,
                "errors": errors,
                "reader_stop": reader,
                "output_close": output,
                "pty": pty,
                "attr_list": attr,
                "released": [r["resource"] for r in self._retained_resources(only_released=True)],
                "retained": self._retained_resources(),
                "seconds": round(time.perf_counter() - t0, 3),
                "note": "release failed for at least one resource; remaining handles kept for retry",
            }

        pipes = self._close_pipe_handles()
        procs = self._close_process_handles()

        self._closed = True
        return {
            "closed": True,
            "retryable": False,
            "reader_stop": reader,
            "output_close": output,
            "pty": pty,
            "attr_list": attr,
            "pipes_closed": pipes,
            "process_handles_closed": procs,
            "released": ["reader", "output_read", "pseudo_console", "attr_list", "pipes", "process_handles"],
            "retained": [],
            "seconds": round(time.perf_counter() - t0, 3),
        }

    def _retained_resources(self, only_released: bool = False) -> list[dict]:
        """Inventory of what this session still holds (or, optionally, released)."""
        held = [
            ("reader_thread", self._reader_thread is not None and self._reader_thread.is_alive()),
            ("reader_thread_handle", bool(self._reader_thread_handle)),
            ("input_write", bool(self._input_write)),
            ("output_read", bool(self._output_read)),
            ("input_read", bool(self._input_read)),
            ("output_write", bool(self._output_write)),
            ("pseudo_console", bool(self._hpc)),
            ("attr_list", self._attr_buf is not None),
            ("process_handle", bool(self._h_process)),
            ("thread_handle", bool(self._h_thread)),
        ]
        return [{"resource": name, "released": not is_held} for name, is_held in held
                if is_held != only_released]


# --------------------------------------------------------------------------- misc
def _filetime_iso(raw: int) -> str:
    """Convert a raw 100ns-since-1601 value to local ISO text (display only)."""
    try:
        unix = raw / 10_000_000 - 11_644_473_600
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(unix))
    except Exception:
        return "<unconvertible>"


_WINERR_NAMES = {
    0: "ERROR_SUCCESS",
    5: "ERROR_ACCESS_DENIED",
    6: "ERROR_INVALID_HANDLE",
    87: "ERROR_INVALID_PARAMETER",
    170: "ERROR_BUSY",
    1816: "ERROR_NOT_ENOUGH_QUOTA",
}


def _winerr_name(code: int) -> str:
    return _WINERR_NAMES.get(code, f"<code {code}>")


def retrieve_identity_by_pid(pid: int) -> dict:
    """Read-only identity probe used by the driver for comparisons.

    Opens with SYNCHRONIZE (needed to wait on the process object) plus query
    rights, then reports the shared ``wait_state`` verdict, the raw 64-bit
    creation FILETIME and the informational exit code. Never terminates anything.
    """
    h = kernel32.OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return {"pid": pid, "open_ok": False, "last_error": ctypes.get_last_error(),
                "state": "unknown"}
    try:
        state = wait_state(h)
        return {
            "pid": pid,
            "open_ok": True,
            "state": state,
            "still_active": state == "alive",
            "creation_filetime": read_creation_filetime(h),
            "exit_code_informational": read_exit_code_informational(h),
        }
    finally:
        kernel32.CloseHandle(h)


def pid_exists(pid: int) -> bool:
    return bool(retrieve_identity_by_pid(pid).get("open_ok", False))


def kill_verified(pid: int, expected_filetime: int, exit_code: int = 0xDEAD) -> dict:
    """Terminate a process only after proving its identity, on one handle.

    Follows the single-handle atomic pattern the lifecycle report §5.3 settled
    on: ``OpenProcess`` once with SYNCHRONIZE|QUERY_LIMITED_INFORMATION|TERMINATE,
    then the liveness verdict, the FILETIME check and ``TerminateProcess`` all act
    on that same handle. The handle pins one kernel process object, so a PID
    reused between check and kill cannot redirect the kill at a new process.

    Refuses -- and reports why -- when:
      * the handle cannot be opened;
      * ``wait_state`` is ``"unknown"`` (never guessed);
      * ``wait_state`` is ``"dead"`` (nothing to kill);
      * the creation FILETIME differs at all from the expected value.

    This is the only termination-by-PID path in the spike; there is no
    ``taskkill`` and no WMI/CIM termination anywhere.
    """
    h = kernel32.OpenProcess(
        SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_TERMINATE, False, pid
    )
    if not h:
        return {"pid": pid, "killed": False, "reason": "open_failed",
                "last_error": ctypes.get_last_error(), "state": "unknown"}
    try:
        state = wait_state(h)
        creation = read_creation_filetime(h)
        detail = {
            "pid": pid,
            "state": state,
            "expected_filetime": expected_filetime,
            "observed_filetime": creation,
        }
        if state == "unknown":
            detail.update(killed=False, reason="state_unknown")
            return detail
        if state == "dead":
            detail.update(killed=False, reason="not_running",
                          exit_code_informational=read_exit_code_informational(h))
            return detail
        if creation is None or creation != expected_filetime:
            detail.update(killed=False, reason="identity_mismatch")
            return detail
        ctypes.set_last_error(0)
        ok_kill = bool(kernel32.TerminateProcess(h, exit_code))
        detail.update(killed=ok_kill, reason="terminated" if ok_kill else "terminate_failed",
                      last_error=ctypes.get_last_error())
        return detail
    finally:
        kernel32.CloseHandle(h)


def job_members(job: GuardJob) -> int | None:
    """Convenience wrapper: current member count of a guard job."""
    return job.active_processes()


def spawn_plain_probe(command_line: str, cwd: str, flags: int = 0) -> dict:
    """Tiny non-ConPTY spawn used only to characterise the ambient job.

    No pseudoconsole, no guard job: this exists to answer "does
    CREATE_BREAKAWAY_FROM_JOB actually let a child leave the ambient job on
    this machine?". It creates one process, observes membership, and closes.
    """
    k = kernel32
    si = STARTUPINFOW()
    si.cb = ctypes.sizeof(STARTUPINFOW)
    pi = PROCESS_INFORMATION()
    cmd_buf = ctypes.create_unicode_buffer(command_line)
    ok = k.CreateProcessW(
        None, cmd_buf, None, None, False, flags,
        None, cwd or None, ctypes.byref(si), ctypes.byref(pi),
    )
    if not ok:
        err = ctypes.get_last_error()
        return {"created": False, "last_error": err, "last_error_name": _winerr_name(err)}
    try:
        creation = read_creation_filetime(pi.hProcess)
        in_job = is_process_in_job(pi.hProcess, None)
        return {
            "created": True,
            "pid": int(pi.dwProcessId),
            "creation_filetime": creation,
            "in_any_job": in_job,
            "flags": hex(flags),
        }
    finally:
        k.TerminateProcess(pi.hProcess, 0)
        k.WaitForSingleObject(pi.hProcess, 5000)
        k.CloseHandle(pi.hThread)
        k.CloseHandle(pi.hProcess)


def current_process_job_report() -> dict:
    """Report whether *this* process is inside an ambient job chain."""
    return {
        "pid": os.getpid(),
        "in_any_job": is_caller_in_any_job(),
        "note": "membership only; job flags are not readable without a named-job handle",
    }
