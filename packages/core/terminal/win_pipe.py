"""Windows 命名管道传输与安全对象原语（P1，Windows-only）。

职责：

1. **命名管道 server/client**（ctypes）：``CreateNamedPipeW`` +
   ``FILE_FLAG_FIRST_PIPE_INSTANCE``（占名检测）+ ``PIPE_REJECT_REMOTE_CLIENTS``
   + **owner-only DACL**（当前用户 SID 唯一 ACE）；``PipeServer``/``PipeClient``/
   ``PipeConnection``。
2. **对端身份核验原语**（本模块内实现，也可注入验证器）：
   ``GetNamedPipeServerProcessId`` / ``GetNamedPipeClientProcessId`` 取得对端
   真实 PID，再在**同一个进程句柄**上做 ``GetProcessTimes``（raw 100ns FILETIME）
   与 ``WaitForSingleObject(h, 0)``（``WAIT_TIMEOUT``=存活 / ``WAIT_OBJECT_0``=已退出），
   与 ``contracts.ProcessIdentity`` 精确比对（**不用**退出码 259 判活，PID 单值
   不作为授权依据）。
3. **Windows 安全对象原语**（供 ``secret_store`` 复用）：owner-only 目录/文件
   **从创建时生效**（``CreateDirectoryW``/``CreateFileW`` 带 SECURITY_ATTRIBUTES，
   而不是“先创建再收紧”）、DACL 读取（安全测试证据）、reparse point 检测、
   ``MoveFileExW`` 原子替换。
4. **有界 I/O 与可收敛取消**：全部读写走 overlapped I/O + 事件等待，
   取消用 ``CancelIoEx``，``close()`` 等待在飞操作归零后才关句柄；未收敛则
   **返回 ``CloseReport(converged=False)`` 并保留资源**（可重试），不伪造成功、
   不留后台 daemon 线程冒充关闭完成。

边界（如实声明，勿当作已解决）：

- **同用户边界**：owner-only DACL + DPAPI 只隔离**其它 Windows 用户**；同一
  用户下的其它进程仍可读同名秘密文件、也可连接本用户的管道。首版信任边界 =
  同一 Pan 用户，不承诺“防同用户恶意软件”（见 ``PAN_TERMINAL_IPC_INTERFACES_20261003.md`` §7）。
- **未做的负验证**：``PIPE_REJECT_REMOTE_CLIENTS`` 与“其它 SID 连接被拒”需要
  第二台主机 / 第二个安全上下文才能实测；本 TA 未创建其它账户、未改任何账户
  权限，因此这两条只做了**结构性证据**（创建参数 + DACL 枚举无其它 SID ACE），
  不作为“已实测拒绝”声明。
- 非 Windows 平台：本模块可导入，但任何入口抛
  ``contracts.BackendUnavailableError``；POSIX 上 PTY/IPC 均未实现。
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from . import ipc as _ipc
from .contracts import (
    BackendUnavailableError,
    ProcessIdentity,
    ProcessProbe,
    ProcessStatus,
)

_IS_WINDOWS = os.name == "nt"

#: 命名管道名前缀（每终端一个名）。
PIPE_PREFIX = "\\\\.\\pipe\\pan-terminal-"
#: 命名管道默认实例数上界（有界并发）。
DEFAULT_MAX_INSTANCES = 4
#: 单进程同时保持的活动连接上界（有界并发连接）。
DEFAULT_MAX_ACTIVE_CONNECTIONS = 4
#: 管道缓冲区大小。
DEFAULT_PIPE_BUFFER_BYTES = 64 * 1024
#: ``connect``/``accept`` 的默认预算。
DEFAULT_CONNECT_TIMEOUT = 5.0
#: ``close`` 等待在飞 I/O 收敛的默认预算。
DEFAULT_CLOSE_TIMEOUT = 2.0

# ── Win32 常量（跨平台声明，便于文档/测试引用） ──────────────────────────────
PIPE_ACCESS_DUPLEX = 0x00000003
PIPE_TYPE_BYTE = 0x00000000
PIPE_READMODE_BYTE = 0x00000000
PIPE_WAIT = 0x00000000
PIPE_REJECT_REMOTE_CLIENTS = 0x00000008
PIPE_UNLIMITED_INSTANCES = 255
FILE_FLAG_FIRST_PIPE_INSTANCE = 0x00080000
FILE_FLAG_OVERLAPPED = 0x40000000
FILE_FLAG_WRITE_THROUGH = 0x80000000
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
CREATE_ALWAYS = 2
FILE_ATTRIBUTE_NORMAL = 0x80
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
INVALID_HANDLE_VALUE = -1
WAIT_OBJECT_0 = 0x00000000
WAIT_ABANDONED = 0x00000080
WAIT_TIMEOUT = 0x00000102
WAIT_FAILED = 0xFFFFFFFF
ERROR_FILE_NOT_FOUND = 2
ERROR_ACCESS_DENIED = 5
ERROR_SEM_TIMEOUT = 121
ERROR_PIPE_BUSY = 231
#: 客户端在 ``ConnectNamedPipe`` 之前已连上（视为已连接，不是错误）。
ERROR_PIPE_CONNECTED = 535
ERROR_NO_DATA = 232
ERROR_PIPE_NOT_CONNECTED = 233
ERROR_IO_PENDING = 997
ERROR_IO_INCOMPLETE = 996
ERROR_OPERATION_ABORTED = 995
ERROR_BROKEN_PIPE = 109
SECURITY_DESCRIPTOR_REVISION = 1
ACL_REVISION = 2
SE_FILE_OBJECT = 1
OWNER_SECURITY_INFORMATION = 0x00000001
DACL_SECURITY_INFORMATION = 0x00000004
OBJECT_INHERIT_ACE = 0x01
CONTAINER_INHERIT_ACE = 0x02
ACCESS_ALLOWED_ACE_TYPE = 0x00
ACCESS_DENIED_ACE_TYPE = 0x01
FILE_ALL_ACCESS = 0x001F01FF
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001
#: ``WaitForSingleObject`` 需要 SYNCHRONIZE；少了它 wait 会 WAIT_FAILED（不能当存活判据）。
SYNCHRONIZE = 0x00100000


# --------------------------------------------------------------------------
# 异常
# --------------------------------------------------------------------------


class PipeError(RuntimeError):
    """命名管道层错误基类。"""


class PipeSecurityError(PipeError):
    """安全对象（SD/DACL/reparse）操作失败。"""


class PipeBusyError(PipeError):
    """管道名已被占用（``FIRST_PIPE_INSTANCE`` 拒绝）或实例耗尽。"""


class PipeTimeout(PipeError, _ipc.TransportTimeout):
    """管道操作超时（不是协议错误）。"""


class PipeClosed(PipeError, _ipc.TransportClosedError):
    """管道已关闭/对端关闭（EOF）。"""


class PipeCancelled(PipeError):
    """管道操作被显式取消（``CancelIoEx``）。"""


class PipeIOError(PipeError):
    """Win32 调用失败。"""


@dataclass(frozen=True)
class CloseReport:
    """``close()``/``accept`` 取消的结果：**收敛**才允许声称已关闭。"""

    converged: bool
    closed: bool
    in_flight: int = 0
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "converged": self.converged,
            "closed": self.closed,
            "in_flight": self.in_flight,
            "detail": self.detail,
        }


def _require_windows() -> None:
    if not _IS_WINDOWS:
        raise BackendUnavailableError(
            "Pan Terminal 命名管道/DPAPI 传输仅支持 Windows（POSIX 只有接口，未实现）"
        )


#: ``CreateFileW`` / ``CreateNamedPipeW`` 失败时返回的伪句柄。
#: 注意：``ctypes`` 的 ``c_void_p`` 返回的是**无符号**值（``2**64-1``），
#: 而 ``INVALID_HANDLE_VALUE`` 是 ``-1``；直接写 ``int(handle) == -1`` 永远为假，
#: 会把创建失败当成成功（本 TA 在占名用例中先复现、再修复的缺陷）。
INVALID_HANDLE_UNSIGNED = (1 << 64) - 1
_INVALID_HANDLE_VALUES = frozenset({0, -1, INVALID_HANDLE_VALUE, INVALID_HANDLE_UNSIGNED})


def is_invalid_handle(handle: Any) -> bool:
    """句柄是否为“无效/空”（含 ctypes 的无符号 ``INVALID_HANDLE_VALUE``）。"""
    if handle is None:
        return True
    try:
        value = int(handle)
    except (TypeError, ValueError):  # pragma: no cover - 非整数值
        return True
    return value in _INVALID_HANDLE_VALUES


def pipe_name_for(terminal_id: str) -> str:
    """终端 id → 管道名 ``\\\\.\\pipe\\pan-terminal-<id>``。"""
    if not isinstance(terminal_id, str) or not terminal_id:
        raise ValueError("terminal_id must be a non-empty string")
    if any(ch in terminal_id for ch in "\\/:*?\"<>|"):  # 只允许安全字符
        raise ValueError("terminal_id contains path/pipe separators")
    return f"{PIPE_PREFIX}{terminal_id}"


def _resolve_pipe_name(term_or_name: str) -> str:
    return term_or_name if term_or_name.startswith("\\\\.\\pipe\\") else pipe_name_for(term_or_name)


if _IS_WINDOWS:  # pragma: no cover - 平台分支
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _adv32 = ctypes.WinDLL("advapi32", use_last_error=True)
    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)

    class _CRYPTPROTECT_DATA_BLOB(ctypes.Structure):
        """``DATA_BLOB``（CryptProtectData/CryptUnprotectData 出入参）。"""

        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(wintypes.BYTE)),
        ]

    class _SECURITY_ATTRIBUTES(ctypes.Structure):
        _fields_ = [
            ("nLength", wintypes.DWORD),
            ("lpSecurityDescriptor", ctypes.c_void_p),
            ("bInheritHandle", wintypes.BOOL),
        ]

    class _SECURITY_DESCRIPTOR(ctypes.Structure):
        _fields_ = [
            ("Revision", ctypes.c_ubyte),
            ("Sbz1", ctypes.c_ubyte),
            ("Control", wintypes.WORD),
            ("Owner", ctypes.c_void_p),
            ("Group", ctypes.c_void_p),
            ("Sacl", ctypes.c_void_p),
            ("Dacl", ctypes.c_void_p),
        ]

    class _ACL_SIZE_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("AceCount", wintypes.DWORD),
            ("AclBytesInUse", wintypes.DWORD),
            ("AclBytesFree", wintypes.DWORD),
        ]

    class _ACE_HEADER(ctypes.Structure):
        _fields_ = [
            ("AceType", ctypes.c_ubyte),
            ("AceFlags", ctypes.c_ubyte),
            ("AceSize", wintypes.WORD),
        ]

    class _ACCESS_ACE(ctypes.Structure):
        _fields_ = [
            ("Header", _ACE_HEADER),
            ("Mask", wintypes.DWORD),
            ("SidStart", wintypes.DWORD),
        ]

    class _OVERLAPPED(ctypes.Structure):
        _fields_ = [
            ("Internal", ctypes.c_size_t),
            ("InternalHigh", ctypes.c_size_t),
            ("Offset", wintypes.DWORD),
            ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        ]

    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _k32.GetCurrentProcess.restype = wintypes.HANDLE
    _k32.GetCurrentProcess.argtypes = ()
    _k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _k32.CloseHandle.restype = wintypes.BOOL
    _k32.GetProcessTimes.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    )
    _k32.GetProcessTimes.restype = wintypes.BOOL
    _k32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    _k32.WaitForSingleObject.restype = wintypes.DWORD
    _k32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
    _k32.TerminateProcess.restype = wintypes.BOOL
    _k32.CreateNamedPipeW.restype = wintypes.HANDLE
    _k32.CreateNamedPipeW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(_SECURITY_ATTRIBUTES),
    )
    _k32.ConnectNamedPipe.argtypes = (wintypes.HANDLE, ctypes.POINTER(_OVERLAPPED))
    _k32.ConnectNamedPipe.restype = wintypes.BOOL
    _k32.DisconnectNamedPipe.argtypes = (wintypes.HANDLE,)
    _k32.DisconnectNamedPipe.restype = wintypes.BOOL
    _k32.WaitNamedPipeW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD)
    _k32.WaitNamedPipeW.restype = wintypes.BOOL
    _k32.CreateFileW.restype = wintypes.HANDLE
    _k32.CreateFileW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(_SECURITY_ATTRIBUTES),
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    _k32.ReadFile.argtypes = (
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(_OVERLAPPED),
    )
    _k32.ReadFile.restype = wintypes.BOOL
    _k32.WriteFile.argtypes = (
        wintypes.HANDLE,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(_OVERLAPPED),
    )
    _k32.WriteFile.restype = wintypes.BOOL
    _k32.GetOverlappedResult.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(_OVERLAPPED),
        ctypes.POINTER(wintypes.DWORD),
        wintypes.BOOL,
    )
    _k32.GetOverlappedResult.restype = wintypes.BOOL
    _k32.CancelIoEx.argtypes = (wintypes.HANDLE, ctypes.POINTER(_OVERLAPPED))
    _k32.CancelIoEx.restype = wintypes.BOOL
    _k32.CreateEventW.restype = wintypes.HANDLE
    _k32.CreateEventW.argtypes = (
        ctypes.POINTER(_SECURITY_ATTRIBUTES),
        wintypes.BOOL,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    )
    _k32.SetEvent.argtypes = (wintypes.HANDLE,)
    _k32.SetEvent.restype = wintypes.BOOL
    _k32.ResetEvent.argtypes = (wintypes.HANDLE,)
    _k32.ResetEvent.restype = wintypes.BOOL
    _k32.FlushFileBuffers.argtypes = (wintypes.HANDLE,)
    _k32.FlushFileBuffers.restype = wintypes.BOOL
    _k32.GetNamedPipeServerProcessId.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG))
    _k32.GetNamedPipeServerProcessId.restype = wintypes.BOOL
    _k32.GetNamedPipeClientProcessId.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG))
    _k32.GetNamedPipeClientProcessId.restype = wintypes.BOOL
    _k32.GetNamedPipeInfo.argtypes = (
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(wintypes.DWORD),
    )
    _k32.GetNamedPipeInfo.restype = wintypes.BOOL
    _k32.CreateDirectoryW.restype = wintypes.BOOL
    _k32.CreateDirectoryW.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(_SECURITY_ATTRIBUTES))
    _k32.GetFileAttributesW.argtypes = (wintypes.LPCWSTR,)
    _k32.GetFileAttributesW.restype = wintypes.DWORD
    _k32.MoveFileExW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD)
    _k32.MoveFileExW.restype = wintypes.BOOL
    _k32.DeleteFileW.argtypes = (wintypes.LPCWSTR,)
    _k32.DeleteFileW.restype = wintypes.BOOL
    _k32.LocalFree.argtypes = (wintypes.HLOCAL,)
    _k32.LocalFree.restype = wintypes.HLOCAL

    _adv32.OpenProcessToken.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    )
    _adv32.OpenProcessToken.restype = wintypes.BOOL
    _adv32.GetTokenInformation.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    )
    _adv32.GetTokenInformation.restype = wintypes.BOOL
    _adv32.SetFileSecurityW.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(_SECURITY_DESCRIPTOR),
    )
    _adv32.SetFileSecurityW.restype = wintypes.BOOL
    _adv32.InitializeSecurityDescriptor.argtypes = (
        ctypes.POINTER(_SECURITY_DESCRIPTOR),
        wintypes.DWORD,
    )
    _adv32.InitializeSecurityDescriptor.restype = wintypes.BOOL
    _adv32.InitializeAcl.argtypes = (ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD)
    _adv32.InitializeAcl.restype = wintypes.BOOL
    _adv32.AddAccessAllowedAceEx.argtypes = (
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
    )
    _adv32.AddAccessAllowedAceEx.restype = wintypes.BOOL
    _adv32.SetSecurityDescriptorDacl.argtypes = (
        ctypes.POINTER(_SECURITY_DESCRIPTOR),
        wintypes.BOOL,
        ctypes.c_void_p,
        wintypes.BOOL,
    )
    _adv32.SetSecurityDescriptorDacl.restype = wintypes.BOOL
    _adv32.SetSecurityDescriptorOwner.argtypes = (
        ctypes.POINTER(_SECURITY_DESCRIPTOR),
        ctypes.c_void_p,
        wintypes.BOOL,
    )
    _adv32.SetSecurityDescriptorOwner.restype = wintypes.BOOL
    _adv32.ConvertStringSidToSidW.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p))
    _adv32.ConvertStringSidToSidW.restype = wintypes.BOOL
    _adv32.ConvertSidToStringSidW.argtypes = (ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR))
    _adv32.ConvertSidToStringSidW.restype = wintypes.BOOL
    _adv32.GetNamedSecurityInfoW.argtypes = (
        wintypes.LPCWSTR,
        ctypes.c_int,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    )
    _adv32.GetNamedSecurityInfoW.restype = wintypes.DWORD
    _adv32.GetAclInformation.argtypes = (ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.c_int)
    _adv32.GetAclInformation.restype = wintypes.BOOL
    _adv32.GetAce.argtypes = (ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p))
    _adv32.GetAce.restype = wintypes.BOOL

    # DPAPI（secret_store 复用）：**用户作用域** = 不带 CRYPTPROTECT_LOCAL_MACHINE。
    _crypt32.CryptProtectData.argtypes = (
        ctypes.POINTER(_CRYPTPROTECT_DATA_BLOB),
        wintypes.LPCWSTR,
        ctypes.POINTER(_CRYPTPROTECT_DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_CRYPTPROTECT_DATA_BLOB),
    )
    _crypt32.CryptProtectData.restype = wintypes.BOOL
    _crypt32.CryptUnprotectData.argtypes = (
        ctypes.POINTER(_CRYPTPROTECT_DATA_BLOB),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_CRYPTPROTECT_DATA_BLOB),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_CRYPTPROTECT_DATA_BLOB),
    )
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL

    def _last_error() -> int:
        return int(ctypes.get_last_error())

    def _error_text(code: int | None = None) -> str:
        return str(ctypes.WinError(_last_error() if code is None else code).strerror or "unknown")

    def _close_handle(handle: int | None) -> None:
        if handle and not is_invalid_handle(handle):
            _k32.CloseHandle(wintypes.HANDLE(handle))

    def _filetime_to_int(ft: "wintypes.FILETIME") -> int:
        return (int(ft.dwHighDateTime) << 32) | int(ft.dwLowDateTime)

    def _fill_identity(handle: int, pid: int | None) -> ProcessIdentity | None:
        created = wintypes.FILETIME()
        empty = wintypes.FILETIME()
        if not _k32.GetProcessTimes(
            wintypes.HANDLE(handle),
            ctypes.byref(created),
            ctypes.byref(empty),
            ctypes.byref(empty),
            ctypes.byref(empty),
        ):
            return None
        raw = _filetime_to_int(created)
        return ProcessIdentity(
            pid=pid,
            created_at_filetime=raw,
            created_at=raw / 10_000_000.0 - 11644473600.0,
        )

    def _wait_signaled(handle: int, timeout_ms: int = 0) -> ProcessStatus:
        """``WaitForSingleObject`` 三态（**不用**退出码 259 判活）。"""
        result = int(_k32.WaitForSingleObject(wintypes.HANDLE(handle), int(timeout_ms)))
        if result == WAIT_TIMEOUT:
            return ProcessStatus.ALIVE
        if result in (WAIT_OBJECT_0, WAIT_ABANDONED):
            return ProcessStatus.DEAD
        return ProcessStatus.UNKNOWN
else:  # pragma: no cover - 非 Windows 只有 stub
    ctypes = None  # type: ignore[assignment]
    wintypes = None  # type: ignore[assignment]

    def _last_error() -> int:
        return 0

    def _error_text(code: int | None = None) -> str:
        return "windows-only"

    def _close_handle(handle: int | None) -> None:
        return None

    def _filetime_to_int(ft: Any) -> int:
        return 0

    def _fill_identity(handle: int, pid: int | None) -> ProcessIdentity | None:
        return None

    def _wait_signaled(handle: int, timeout_ms: int = 0) -> ProcessStatus:
        return ProcessStatus.UNKNOWN


# --------------------------------------------------------------------------
# 进程身份（同 handle：PID + raw FILETIME + Wait）
# --------------------------------------------------------------------------


class ProcessIdentityHandle:
    """**保留句柄**的进程身份：FILETIME 与存活判定都在这一个句柄上做。

    ``OpenProcess`` 成功不等于存活，PID 复用也不能用“再查一次 PID”防住；
    唯一可靠的判据是**同一个句柄**上的 ``WaitForSingleObject(h, 0)``：
    ``WAIT_TIMEOUT``=仍在运行、``WAIT_OBJECT_0``=已退出。句柄还使 PID 在保留
    期间不可被系统复用，因此“核验 → 使用”之间没有 TOCTOU 窗口。
    """

    def __init__(self, pid: int, handle: int, identity: ProcessIdentity) -> None:
        self.pid = int(pid)
        self.handle = int(handle)
        self.identity = identity
        self._closed = False

    @classmethod
    def open(cls, pid: int) -> "ProcessIdentityHandle | None":
        """打开进程并读取 raw FILETIME；打不开/读不到返回 ``None``（**不是**“已退出”）。"""
        _require_windows()
        handle = _k32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, int(pid)
        )
        if not handle:
            return None
        identity = _fill_identity(handle, int(pid))
        if identity is None:
            _close_handle(handle)
            return None
        return cls(int(pid), int(handle), identity)

    @property
    def filetime(self) -> int | None:
        return self.identity.created_at_filetime

    def is_alive(self) -> bool:
        """同 handle ``WaitForSingleObject(h, 0)``：只有 ``WAIT_TIMEOUT`` 才算存活。"""
        if self._closed:
            raise PipeError("identity handle already closed")
        return _wait_signaled(self.handle, 0) is ProcessStatus.ALIVE

    def probe(self) -> ProcessProbe:
        status = ProcessStatus.UNKNOWN if self._closed else _wait_signaled(self.handle, 0)
        if status is ProcessStatus.ALIVE:
            return ProcessProbe(status=ProcessStatus.ALIVE, identity=self.identity, detail="same-handle wait")
        if status is ProcessStatus.DEAD:
            return ProcessProbe(status=ProcessStatus.DEAD, identity=self.identity, detail="same-handle signaled")
        return ProcessProbe(status=ProcessStatus.UNKNOWN, identity=self.identity, detail="wait failed")

    def matches(self, expected: ProcessIdentity | None) -> bool:
        return expected is not None and expected.matches(self.identity)

    def close(self) -> None:
        if not self._closed:
            _close_handle(self.handle)
            self._closed = True

    def __enter__(self) -> "ProcessIdentityHandle":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def probe_process(pid: int) -> ProcessProbe:
    """显式三态探针：同一 handle 上读 raw FILETIME + ``Wait``。

    - 打不开/读不到 → ``UNKNOWN``（**不得**当“已退出”，核心对 UNKNOWN fail-closed）；
    - ``Wait`` 显示 signaled → ``DEAD``（真实证据，不是“查不到”）；
    - ``WAIT_TIMEOUT`` → ``ALIVE`` + ``ProcessIdentity``（含 raw FILETIME）。
    """
    _require_windows()
    handle = ProcessIdentityHandle.open(int(pid))
    if handle is None:
        return ProcessProbe(status=ProcessStatus.UNKNOWN, detail="OpenProcess/GetProcessTimes failed")
    try:
        return handle.probe()
    finally:
        handle.close()


def default_identity_probe(pid: int) -> ProcessProbe:
    """``contracts.IdentityProbe`` 兼容的默认探针（client/server 可注入本函数）。"""
    return probe_process(pid)


def current_process_identity() -> ProcessIdentity:
    """当前进程身份（``GetCurrentProcess`` 伪句柄 + ``GetProcessTimes``）。"""
    _require_windows()
    identity = _fill_identity(int(_k32.GetCurrentProcess()), os.getpid())
    if identity is None:
        raise PipeError("GetProcessTimes failed for current process")
    return identity


def terminate_verified_process(pid: int, expected_filetime: int | None) -> dict[str, Any]:
    """**同 handle 核验后**才终止进程；返回决策轨迹（用于测试/清理证据）。

    没有 raw FILETIME（``None``）一律拒绝——不对“不可核验”的 PID 下手。
    """
    _require_windows()
    result: dict[str, Any] = {
        "pid": int(pid),
        "requestedCreateTimeFiletime": expected_filetime,
        "opened": False,
        "createTimeFiletime": None,
        "filetimeMatch": False,
        "running": False,
        "terminated": False,
    }
    if expected_filetime is None:
        result["detail"] = "refused: no raw create-time FILETIME to verify against"
        return result
    handle = _k32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_TERMINATE | SYNCHRONIZE, False, int(pid)
    )
    if not handle:
        result["detail"] = f"OpenProcess failed: {_error_text()}"
        return result
    result["opened"] = True
    try:
        identity = _fill_identity(handle, int(pid))
        if identity is None:
            result["detail"] = "GetProcessTimes failed"
            return result
        result["createTimeFiletime"] = identity.created_at_filetime
        result["filetimeMatch"] = int(identity.created_at_filetime or -1) == int(expected_filetime)
        if not result["filetimeMatch"]:
            result["detail"] = "refused: create-time FILETIME mismatch (same-handle check)"
            return result
        status = _wait_signaled(handle, 0)
        result["running"] = status is ProcessStatus.ALIVE
        if not result["running"]:
            result["detail"] = f"refused: not running on same handle (status={status.value})"
            return result
        result["terminated"] = bool(_k32.TerminateProcess(wintypes.HANDLE(handle), 1))
        result["detail"] = "terminated" if result["terminated"] else f"TerminateProcess failed: {_error_text()}"
        return result
    finally:
        _close_handle(handle)


# --------------------------------------------------------------------------
# 安全对象原语（owner-only DACL / reparse / 原子替换）
# --------------------------------------------------------------------------


def current_user_sid() -> str:
    """当前进程令牌的 user SID 字符串。"""
    _require_windows()
    token = wintypes.HANDLE()
    TOKEN_QUERY = 0x0008
    TokenUser = 1
    if not _adv32.OpenProcessToken(
        wintypes.HANDLE(int(_k32.GetCurrentProcess())), TOKEN_QUERY, ctypes.byref(token)
    ):
        raise PipeSecurityError(f"OpenProcessToken failed: {_error_text()}")
    try:
        size = wintypes.DWORD(0)
        _adv32.GetTokenInformation(token, TokenUser, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(max(16, int(size.value)))
        if not _adv32.GetTokenInformation(
            token, TokenUser, buffer, wintypes.DWORD(len(buffer)), ctypes.byref(size)
        ):
            raise PipeSecurityError(f"GetTokenInformation(TokenUser) failed: {_error_text()}")
        sid = ctypes.c_void_p.from_buffer(buffer).value
        return _sid_to_string(ctypes.c_void_p(sid))
    finally:
        _close_handle(int(token.value or 0))


def _sid_to_string(sid: Any) -> str:
    ptr = wintypes.LPWSTR()
    if not _adv32.ConvertSidToStringSidW(ctypes.c_void_p(sid) if isinstance(sid, int) else sid, ctypes.byref(ptr)):
        raise PipeSecurityError(f"ConvertSidToStringSidW failed: {_error_text()}")
    try:
        return str(ptr.value)
    finally:
        _k32.LocalFree(ctypes.cast(ptr, wintypes.HLOCAL))


class OwnerOnlySecurity:
    """owner-only DACL 的 ``SECURITY_ATTRIBUTES`` 持有者。

    DACL 只有**一个** ACE：当前用户 SID + ``FILE_ALL_ACCESS``（目录版本带
    ``OBJECT_INHERIT_ACE|CONTAINER_INHERIT_ACE`` 以便子对象继承）。没有
    Everyone/Users/ANONYMOUS 等任何其它 ACE，因此“其它 Windows 用户”被拒绝；
    **同用户进程不受限**（威胁模型边界见模块 docstring）。
    """

    def __init__(self, *, inherit: bool = False) -> None:
        _require_windows()
        self.sid_string = current_user_sid()
        self.inherit = bool(inherit)
        sid_ptr = ctypes.c_void_p()
        if not _adv32.ConvertStringSidToSidW(self.sid_string, ctypes.byref(sid_ptr)):
            raise PipeSecurityError(f"ConvertStringSidToSidW failed: {_error_text()}")
        self._sid = sid_ptr
        acl_size = 4096
        self._acl = ctypes.create_string_buffer(acl_size)
        if not _adv32.InitializeAcl(ctypes.cast(self._acl, ctypes.c_void_p), acl_size, ACL_REVISION):
            self.close()
            raise PipeSecurityError(f"InitializeAcl failed: {_error_text()}")
        flags = (OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE) if inherit else 0
        if not _adv32.AddAccessAllowedAceEx(
            ctypes.cast(self._acl, ctypes.c_void_p),
            ACL_REVISION,
            flags,
            FILE_ALL_ACCESS,
            ctypes.cast(sid_ptr, ctypes.c_void_p),
        ):
            self.close()
            raise PipeSecurityError(f"AddAccessAllowedAceEx failed: {_error_text()}")
        self._sd = _SECURITY_DESCRIPTOR()
        if not _adv32.InitializeSecurityDescriptor(ctypes.byref(self._sd), SECURITY_DESCRIPTOR_REVISION):
            self.close()
            raise PipeSecurityError(f"InitializeSecurityDescriptor failed: {_error_text()}")
        if not _adv32.SetSecurityDescriptorOwner(ctypes.byref(self._sd), sid_ptr, False):
            self.close()
            raise PipeSecurityError(f"SetSecurityDescriptorOwner failed: {_error_text()}")
        if not _adv32.SetSecurityDescriptorDacl(
            ctypes.byref(self._sd), True, ctypes.cast(self._acl, ctypes.c_void_p), False
        ):
            self.close()
            raise PipeSecurityError(f"SetSecurityDescriptorDacl failed: {_error_text()}")
        self._sa = _SECURITY_ATTRIBUTES(
            nLength=ctypes.sizeof(_SECURITY_ATTRIBUTES),
            lpSecurityDescriptor=ctypes.cast(ctypes.byref(self._sd), ctypes.c_void_p),
            bInheritHandle=False,
        )

    @property
    def security_attributes(self) -> Any:
        return ctypes.byref(self._sa)

    def describe(self) -> dict[str, Any]:
        return {"owner_sid": self.sid_string, "dacl_aces": 1, "inherit": self.inherit}

    def close(self) -> None:
        if getattr(self, "_sd", None) is not None:
            self._sd = None
            self._sa = None
        if self._acl is not None:
            self._acl = None
        if getattr(self, "_sid", None) is not None:
            _k32.LocalFree(ctypes.cast(self._sid, wintypes.HLOCAL))
            self._sid = None

    def __enter__(self) -> "OwnerOnlySecurity":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def path_exists(path: str) -> bool:
    _require_windows()
    return int(_k32.GetFileAttributesW(str(path))) != 0xFFFFFFFF


def is_reparse_point(path: str) -> bool:
    """路径本身是否是 reparse point（symlink/junction/其它）——一律拒绝信任。"""
    _require_windows()
    attributes = int(_k32.GetFileAttributesW(str(path)))
    if attributes == 0xFFFFFFFF:
        return False
    return bool(attributes & FILE_ATTRIBUTE_REPARSE_POINT)


def create_owner_only_directory(path: str, *, create_parents: bool = True) -> bool:
    """以 owner-only DACL **创建**目录（已存在则收紧 ACL）。

    返回 ``True`` 表示本次创建；``False`` 表示已存在（ACL 会被重新应用为
    owner-only，避免“事先存在的宽松目录”被当作安全目录使用）。
    """
    _require_windows()
    target = os.path.abspath(str(path))
    missing: list[str] = []
    if create_parents:
        walk = target
        while True:
            parent = os.path.dirname(walk)
            if parent == walk:
                break
            if path_exists(parent):
                break
            missing.append(parent)
            walk = parent
    security = OwnerOnlySecurity(inherit=True)
    try:
        for directory in reversed(missing):
            if not _k32.CreateDirectoryW(str(directory), security.security_attributes):
                error = _last_error()
                if error != 183:  # ERROR_ALREADY_EXISTS（并发创建）
                    raise PipeSecurityError(f"CreateDirectoryW failed for {directory}: {_error_text(error)}")
        created = bool(_k32.CreateDirectoryW(target, security.security_attributes))
        if not created:
            error = _last_error()
            if error != 183:
                raise PipeSecurityError(f"CreateDirectoryW failed for {target}: {_error_text(error)}")
            apply_owner_only_acl(target, inherit=True)
            return False
        return True
    finally:
        security.close()


def apply_owner_only_acl(path: str, *, inherit: bool = False) -> None:
    """把已存在对象的 DACL 收紧为 owner-only（用于“已存在的目录”兜底）。"""
    _require_windows()
    security = OwnerOnlySecurity(inherit=inherit)
    try:
        if not _adv32.SetFileSecurityW(
            str(path),
            DACL_SECURITY_INFORMATION | OWNER_SECURITY_INFORMATION,
            ctypes.byref(_to_security_descriptor(security)),
        ):
            raise PipeSecurityError(f"SetFileSecurityW failed for {path}: {_error_text()}")
    finally:
        security.close()


def _to_security_descriptor(security: OwnerOnlySecurity) -> Any:
    """从持有者取出 ``SECURITY_DESCRIPTOR``（供 ``SetFileSecurityW`` 使用）。"""
    return security._sd  # noqa: SLF001 - 同模块内的安全原语


def write_file_owner_only(path: str, data: bytes, *, flush: bool = True) -> None:
    """以 owner-only DACL **从创建时**写文件（``CREATE_ALWAYS``）。"""
    _require_windows()
    security = OwnerOnlySecurity()
    try:
        handle = _k32.CreateFileW(
            str(path),
            GENERIC_WRITE,
            0,
            security.security_attributes,
            CREATE_ALWAYS,
            FILE_ATTRIBUTE_NORMAL | FILE_FLAG_WRITE_THROUGH,
            None,
        )
        if is_invalid_handle(handle):
            raise PipeSecurityError(f"CreateFileW failed for {path}: {_error_text()}")
        try:
            total = 0
            payload = bytes(data)
            while total < len(payload):
                written = wintypes.DWORD(0)
                chunk = payload[total:]
                if not _k32.WriteFile(
                    wintypes.HANDLE(handle),
                    ctypes.c_char_p(chunk),
                    len(chunk),
                    ctypes.byref(written),
                    None,
                ):
                    raise PipeSecurityError(f"WriteFile failed for {path}: {_error_text()}")
                if written.value == 0:
                    raise PipeSecurityError(f"WriteFile wrote 0 bytes for {path}")
                total += int(written.value)
            if flush and not _k32.FlushFileBuffers(wintypes.HANDLE(handle)):
                raise PipeSecurityError(f"FlushFileBuffers failed for {path}: {_error_text()}")
        finally:
            _close_handle(int(handle))
    finally:
        security.close()


def replace_file_atomic(source: str, destination: str, *, retries: int = 20) -> None:
    """``MoveFileExW(MOVEFILE_REPLACE_EXISTING|MOVEFILE_WRITE_THROUGH)`` 原子替换。

    Windows 文件扫描器/杀毒会短暂占用目标：有界重试；**绝不**回退为截断写。
    """
    _require_windows()
    MOVEFILE_REPLACE_EXISTING = 0x1
    MOVEFILE_WRITE_THROUGH = 0x8
    flags = MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH
    last_error = 0
    for attempt in range(int(retries)):
        if _k32.MoveFileExW(str(source), str(destination), flags):
            return
        last_error = _last_error()
        time.sleep(0.01 * (attempt + 1))
    raise PipeSecurityError(
        f"MoveFileExW failed ({_error_text(last_error)}): {os.path.basename(str(destination))}"
    )


def delete_file(path: str) -> bool:
    """删除文件；文件本来就不存在返回 ``False``（不抛，便于幂等调用方区分）。"""
    _require_windows()
    if _k32.DeleteFileW(str(path)):
        return True
    return False


def dacl_entries(path: str) -> list[dict[str, Any]]:
    """读取 DACL 的 ACE 列表（安全测试证据：确认没有其它 SID 的授权）。"""
    _require_windows()
    owner = ctypes.c_void_p()
    group = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    sacl = ctypes.c_void_p()
    sd = ctypes.c_void_p()
    rc = int(
        _adv32.GetNamedSecurityInfoW(
            str(path),
            SE_FILE_OBJECT,
            OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
            ctypes.byref(owner),
            ctypes.byref(group),
            ctypes.byref(dacl),
            ctypes.byref(sacl),
            ctypes.byref(sd),
        )
    )
    if rc != 0:
        raise PipeSecurityError(f"GetNamedSecurityInfoW failed for {path}: {_error_text(rc)}")
    try:
        if not dacl:
            return [{"sid": None, "ace_type": "null-dacl", "ace_flags": 0, "mask": None}]
        info = _ACL_SIZE_INFORMATION()
        if not _adv32.GetAclInformation(
            dacl, ctypes.byref(info), ctypes.sizeof(info), 2  # AclSizeInformation
        ):
            raise PipeSecurityError(f"GetAclInformation failed for {path}: {_error_text()}")
        entries: list[dict[str, Any]] = []
        for index in range(int(info.AceCount)):
            ace_ptr = ctypes.c_void_p()
            if not _adv32.GetAce(dacl, index, ctypes.byref(ace_ptr)):
                raise PipeSecurityError(f"GetAce failed for {path}: {_error_text()}")
            header = ctypes.cast(ace_ptr, ctypes.POINTER(_ACE_HEADER)).contents
            entry: dict[str, Any] = {
                "ace_type": int(header.AceType),
                "ace_flags": int(header.AceFlags),
                "ace_size": int(header.AceSize),
                "sid": None,
                "mask": None,
            }
            if int(header.AceType) in (ACCESS_ALLOWED_ACE_TYPE, ACCESS_DENIED_ACE_TYPE):
                access = ctypes.cast(ace_ptr, ctypes.POINTER(_ACCESS_ACE)).contents
                entry["mask"] = int(access.Mask)
                entry["sid"] = _sid_to_string(int(ace_ptr.value) + 8)  # SidStart offset
            entries.append(entry)
        return entries
    finally:
        _k32.LocalFree(ctypes.cast(sd, wintypes.HLOCAL))


def owner_sid(path: str) -> str | None:
    """对象 owner 的 SID 字符串。"""
    _require_windows()
    owner = ctypes.c_void_p()
    group = ctypes.c_void_p()
    dacl = ctypes.c_void_p()
    sacl = ctypes.c_void_p()
    sd = ctypes.c_void_p()
    rc = int(
        _adv32.GetNamedSecurityInfoW(
            str(path),
            SE_FILE_OBJECT,
            OWNER_SECURITY_INFORMATION,
            ctypes.byref(owner),
            ctypes.byref(group),
            ctypes.byref(dacl),
            ctypes.byref(sacl),
            ctypes.byref(sd),
        )
    )
    if rc != 0:
        return None
    try:
        if not owner:
            return None
        return _sid_to_string(owner)
    finally:
        _k32.LocalFree(ctypes.cast(sd, wintypes.HLOCAL))


# --------------------------------------------------------------------------
# 管道 I/O
# --------------------------------------------------------------------------


class _PeerClosed(Exception):
    """内部信号：``ReadFile`` 立即返回“对端已关”（EOF），交由调用方判定断帧。"""


class _OverlappedOp:
    """一次 overlapped 操作的状态（超时后可续用，不丢数据）。

    **内存安全**：内核在操作完成前会继续写 ``OVERLAPPED``/缓冲区。因此在
    ``CancelIoEx`` 之后必须**等事件置位**（操作真正落地）才能释放，否则内核会写
    已释放内存（本 TA 在压力运行中复现过 access violation）。``release()`` 幂等。
    """

    __slots__ = ("ov", "event", "buffer", "requested", "transferred", "issued", "kind", "error")

    def __init__(self, kind: str, size: int) -> None:
        self.kind = kind
        self.event = int(_k32.CreateEventW(None, True, False, None))
        if not self.event:
            raise PipeIOError(f"CreateEventW failed: {_error_text()}")
        self.ov = _OVERLAPPED()
        self.ov.hEvent = wintypes.HANDLE(self.event)
        self.buffer = ctypes.create_string_buffer(max(1, int(size)))
        self.requested = int(size)
        self.transferred = 0
        self.issued = False
        self.error: int | None = None

    def wait_complete(self, timeout_ms: int) -> bool:
        """等待操作落地（``CancelIoEx`` 后应尽快置位）；返回是否已落地。"""
        if not self.event:
            return True
        wait = int(_k32.WaitForSingleObject(wintypes.HANDLE(self.event), int(timeout_ms)))
        return wait in (WAIT_OBJECT_0, WAIT_ABANDONED)

    def release(self) -> None:
        """释放事件句柄（幂等）；**仅在操作落地后**调用。"""
        if self.event:
            _close_handle(self.event)
            self.event = 0


class PipeConnection:
    """一条命名管道连接的**有界**帧/字节 I/O 面。

    - 收发都带预算（``timeout``）；超时返回 ``None``/抛 ``PipeTimeout``，
      未完成的 I/O 保持可续用（读），或把连接标记为不可继续使用（写半帧）；
    - 取消走 ``CancelIoEx``，``close()`` 只有等到在飞操作归零才关句柄，
      否则返回 ``CloseReport(converged=False)`` 并可再次调用重试；
    - 供 ``ipc.IpcSession`` 使用的鸭子类型面：``send_frame``/``recv_frame``/
      ``peer_server_pid``/``peer_client_pid``/``close``。
    """

    def __init__(
        self,
        handle: int,
        *,
        role: str,
        max_frame_bytes: int = _ipc.DEFAULT_MAX_FRAME_BYTES,
        on_close: Callable[["PipeConnection"], None] | None = None,
        detail: str = "",
        diagnostics: _ipc.IpcDiagnostics | None = None,
    ) -> None:
        self.handle = int(handle)
        self.role = role
        self.max_frame_bytes = int(max_frame_bytes)
        self.diagnostics = (
            diagnostics if diagnostics is not None else _ipc.IpcDiagnostics(label=detail or role)
        )
        self._decoder = _ipc.FrameDecoder(max_frame_bytes=self.max_frame_bytes)
        self._deferred: list[dict[str, Any]] = []
        self._read_op: _OverlappedOp | None = None
        #: “已取消但未确认落地”的操作：保留引用以防内核写已释放内存（close 等待/重试）
        self._orphan_ops: list[_OverlappedOp] = []
        self._read_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._state = threading.Lock()
        self._cv = threading.Condition(self._state)
        self._inflight = 0
        self._closing = False
        self._closed = False
        self._broken = False
        self._on_close = on_close
        self.detail = detail

    # -- 状态 ----------------------------------------------------------
    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def broken(self) -> bool:
        """写半帧/协议违例后必须关闭（不能继续当同一条流用）。"""
        return self._broken

    @property
    def in_flight(self) -> int:
        with self._state:
            return self._inflight

    def _enter_op(self) -> None:
        with self._state:
            if self._closing or self._closed:
                raise PipeClosed("connection is closing/closed")
            if self._broken:
                raise PipeClosed("connection is marked broken (partial write/protocol violation)")
            self._inflight += 1

    def _leave_op(self) -> None:
        with self._cv:
            self._inflight -= 1
            self._cv.notify_all()

    def _check_open(self) -> None:
        with self._state:
            if self._closing or self._closed:
                raise PipeClosed("connection is closing/closed")

    # -- 对端身份 ------------------------------------------------------
    def peer_server_pid(self) -> int | None:
        if not _IS_WINDOWS or not self.handle:
            return None
        pid = wintypes.ULONG(0)
        if _k32.GetNamedPipeServerProcessId(wintypes.HANDLE(self.handle), ctypes.byref(pid)):
            return int(pid.value)
        return None

    def peer_client_pid(self) -> int | None:
        if not _IS_WINDOWS or not self.handle:
            return None
        pid = wintypes.ULONG(0)
        if _k32.GetNamedPipeClientProcessId(wintypes.HANDLE(self.handle), ctypes.byref(pid)):
            return int(pid.value)
        return None

    # -- 读 ------------------------------------------------------------
    def _issue_read(self, max_bytes: int) -> _OverlappedOp:
        op = _OverlappedOp("read", max_bytes)
        transferred = wintypes.DWORD(0)
        ok = _k32.ReadFile(
            wintypes.HANDLE(self.handle),
            op.buffer,
            op.requested,
            ctypes.byref(transferred),
            ctypes.byref(op.ov),
        )
        if ok:
            op.transferred = int(transferred.value)
            return op
        error = _last_error()
        if error == ERROR_IO_PENDING:
            op.issued = True
            return op
        op.release()
        if error in (ERROR_BROKEN_PIPE, ERROR_PIPE_NOT_CONNECTED, ERROR_NO_DATA):
            raise _PeerClosed(f"peer closed the pipe (error={error})")
        raise PipeIOError(f"ReadFile failed: {_error_text(error)}")

    def recv_bytes(self, max_bytes: int, *, timeout: float | None = None) -> bytes:
        """读一段原始字节（测试/协议调试用；正常路径走 ``recv_frame``）。"""
        data, status = self._read_some(max_bytes, timeout=timeout)
        if status == "timeout":
            raise PipeTimeout("read timed out")
        return data

    def _read_some(
        self, max_bytes: int, *, timeout: float | None
    ) -> tuple[bytes, str]:
        """返回 ``(data, status)``；``status`` ∈ ``{"data", "timeout", "eof"}``。

        超时**不丢弃**已发出的读（``self._read_op`` 保留，下次调用续等）。
        """
        budget = None if timeout is None else max(0.0, float(timeout))
        deadline = None if budget is None else time.monotonic() + budget
        with self._read_lock:
            self._check_open()
            self._enter_op()
            try:
                if self._read_op is None:
                    try:
                        self._read_op = self._issue_read(max_bytes)
                    except _PeerClosed:
                        return b"", "eof"
                op = self._read_op
                while True:
                    remaining_ms = 0xFFFFFFFF if deadline is None else max(
                        0, int((deadline - time.monotonic()) * 1000)
                    )
                    wait = int(_k32.WaitForSingleObject(wintypes.HANDLE(op.event), remaining_ms))
                    if wait == WAIT_TIMEOUT:
                        # 先做了一次 0ms 轮询（remaining_ms 已为 0）仍未完成：超时但保留操作。
                        return b"", "timeout"
                    if wait not in (WAIT_OBJECT_0, WAIT_ABANDONED):
                        op.error = _last_error()
                        self._read_op = None
                        op.release()
                        raise PipeIOError(f"WaitForSingleObject(read) failed: {_error_text(op.error)}")
                    transferred = wintypes.DWORD(0)
                    if not _k32.GetOverlappedResult(
                        wintypes.HANDLE(self.handle), ctypes.byref(op.ov), ctypes.byref(transferred), False
                    ):
                        error = _last_error()
                        if error == ERROR_IO_INCOMPLETE:
                            continue
                        self._read_op = None
                        op.release()
                        if error == ERROR_OPERATION_ABORTED:
                            raise PipeCancelled("read cancelled (CancelIoEx)")
                        if error in (ERROR_BROKEN_PIPE, ERROR_PIPE_NOT_CONNECTED, ERROR_NO_DATA):
                            return b"", "eof"
                        raise PipeIOError(f"GetOverlappedResult(read) failed: {_error_text(error)}")
                    data = op.buffer.raw[: int(transferred.value)]
                    self._read_op = None
                    op.release()
                    if not data:
                        return b"", "eof"
                    return bytes(data), "data"
            finally:
                self._leave_op()

    # -- 写 ------------------------------------------------------------
    def _write_all(self, payload: bytes, *, timeout: float | None) -> int:
        data = bytes(payload)
        if not data:
            return 0
        deadline = None if timeout is None else time.monotonic() + max(0.0, float(timeout))
        written_total = 0
        with self._write_lock:
            self._check_open()
            self._enter_op()
            try:
                while written_total < len(data):
                    self._check_open()
                    op = _OverlappedOp("write", len(data) - written_total)
                    op.buffer = ctypes.create_string_buffer(data[written_total:], len(data) - written_total)
                    transferred = wintypes.DWORD(0)
                    ok = _k32.WriteFile(
                        wintypes.HANDLE(self.handle),
                        op.buffer,
                        len(data) - written_total,
                        ctypes.byref(transferred),
                        ctypes.byref(op.ov),
                    )
                    if not ok:
                        error = _last_error()
                        if error != ERROR_IO_PENDING:
                            op.release()
                            if error in (ERROR_BROKEN_PIPE, ERROR_PIPE_NOT_CONNECTED, ERROR_NO_DATA):
                                raise PipeClosed(f"peer closed the pipe during write (error={error})")
                            self._broken = True
                            raise PipeIOError(f"WriteFile failed: {_error_text(error)}")
                    while True:
                        remaining_ms = 0xFFFFFFFF if deadline is None else max(
                            0, int((deadline - time.monotonic()) * 1000)
                        )
                        wait = int(_k32.WaitForSingleObject(wintypes.HANDLE(op.event), remaining_ms))
                        if wait == WAIT_TIMEOUT:
                            if deadline is not None and time.monotonic() >= deadline:
                                # 半帧已写出：不能续写，必须关连接（不静默修复）。
                                _k32.CancelIoEx(wintypes.HANDLE(self.handle), ctypes.byref(op.ov))
                                if op.wait_complete(2000):
                                    op.release()
                                else:
                                    # 取消未落地：**保留引用**（内核可能仍写该 OVERLAPPED），
                                    # 交由 close() 等待/重试；绝不提前释放。
                                    self._orphan_ops.append(op)
                                    self.diagnostics.record_error("write-cancel-not-converged")
                                self._broken = True
                                raise PipeTimeout(
                                    "write timed out mid-frame (cancelled); connection is marked broken"
                                )
                            continue
                        if wait not in (WAIT_OBJECT_0, WAIT_ABANDONED):
                            op.release()
                            self._broken = True
                            raise PipeIOError(f"WaitForSingleObject(write) failed: {_error_text()}")
                        transferred = wintypes.DWORD(0)
                        if not _k32.GetOverlappedResult(
                            wintypes.HANDLE(self.handle),
                            ctypes.byref(op.ov),
                            ctypes.byref(transferred),
                            False,
                        ):
                            e = _last_error()
                            if e == ERROR_IO_INCOMPLETE:
                                continue
                            op.release()
                            self._broken = True
                            if e == ERROR_OPERATION_ABORTED:
                                raise PipeCancelled("write cancelled (CancelIoEx)")
                            if e in (ERROR_BROKEN_PIPE, ERROR_PIPE_NOT_CONNECTED, ERROR_NO_DATA):
                                raise PipeClosed("peer closed the pipe during write")
                            raise PipeIOError(f"GetOverlappedResult(write) failed: {_error_text(e)}")
                        written_total += int(transferred.value)
                        op.release()
                        break
                return written_total
            finally:
                self._leave_op()

    # -- 帧 ------------------------------------------------------------
    def send_raw(self, data: bytes, *, timeout: float | None = None) -> int:
        self._check_open()
        return self._write_all(data, timeout=timeout)

    def send_frame(self, message: Mapping[str, Any], *, timeout: float | None = None) -> None:
        self._check_open()
        frame = _ipc.encode_frame(message)
        self._write_all(frame, timeout=timeout)

    def recv_frame(self, *, timeout: float | None = None) -> dict[str, Any] | None:
        """读一帧；超时返回 ``None``（部分帧留在解码器里）。

        对端 EOF 且**帧未完成** → ``ipc.FrameTruncatedError``（断帧，拒绝）；
        干净 EOF → ``PipeClosed``。
        """
        self._check_open()
        if self._decoder.failed:
            raise PipeClosed("decoder failed earlier; connection must be closed")
        if self._deferred:
            return self._deferred.pop(0)
        budget = None if timeout is None else max(0.0, float(timeout))
        deadline = None if budget is None else time.monotonic() + budget
        while True:
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            if remaining is not None and remaining <= 0:
                return None
            try:
                data, status = self._read_some(64 * 1024, timeout=remaining)
            except PipeCancelled:
                raise
            if status == "timeout":
                return None
            if status == "eof":
                partial = self._decoder.take_partial()
                if partial:
                    raise _ipc.FrameTruncatedError(
                        f"connection closed with {len(partial)} bytes of an unfinished frame"
                    )
                raise PipeClosed("peer closed the pipe (clean EOF)")
            frames = self._decoder.feed(data)
            if frames:
                first, rest = frames[0], frames[1:]
                self._deferred.extend(rest)
                return first

    # -- 关闭 ----------------------------------------------------------
    def close(self, *, timeout: float | None = None) -> CloseReport:
        """取消在飞 I/O 并等其收敛；未收敛则**保留资源**（可重试）。

        收敛判定不只数“在飞调用”，还包括**超时后被保留的读操作**与“取消但未落地”
        的孤儿操作——内核在操作落地前仍会写那些 ``OVERLAPPED``/缓冲区，提前释放会
        造成访问违例（本 TA 在压力运行中复现过）。
        """
        budget = DEFAULT_CLOSE_TIMEOUT if timeout is None else max(0.0, float(timeout))
        with self._state:
            if self._closed:
                return CloseReport(converged=True, closed=True, detail="already closed")
            if not self._closing:
                self._closing = True
                if _IS_WINDOWS and self.handle:
                    # 无在飞 I/O 时 CancelIoEx 返回 False + ERROR_NOT_FOUND：忽略。
                    _k32.CancelIoEx(wintypes.HANDLE(self.handle), None)
            deadline = time.monotonic() + budget
            while self._inflight > 0 and time.monotonic() < deadline:
                self._cv.wait(max(0.01, deadline - time.monotonic()))
            if self._inflight > 0:
                return CloseReport(
                    converged=False,
                    closed=False,
                    in_flight=self._inflight,
                    detail="in-flight I/O did not converge after cancel; resources retained for retry",
                )
            retained_read = self._read_op
            orphans = list(self._orphan_ops)
        # 保留的读操作：取消后等其落地再释放（否则内核可能写已释放内存）
        if retained_read is not None:
            remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
            if not retained_read.wait_complete(remaining_ms):
                return CloseReport(
                    converged=False,
                    closed=False,
                    detail="retained read op did not complete after cancel; resources retained for retry",
                )
            if self._read_op is retained_read:
                self._read_op = None
            retained_read.release()
        for orphan in orphans:
            remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
            if not orphan.wait_complete(remaining_ms):
                return CloseReport(
                    converged=False,
                    closed=False,
                    detail="orphan op did not complete after cancel; resources retained for retry",
                )
            orphan.release()
            with self._state:
                if orphan in self._orphan_ops:
                    self._orphan_ops.remove(orphan)
        with self._state:
            if _IS_WINDOWS and self.handle:
                if self.role == "server":
                    _k32.DisconnectNamedPipe(wintypes.HANDLE(self.handle))
                _close_handle(self.handle)
            self.handle = 0
            self._closed = True
            self._closing = True
        if self._on_close is not None:
            try:
                self._on_close(self)
            except Exception:  # noqa: BLE001 - 回调失败不影响关闭结论
                pass
        return CloseReport(converged=True, closed=True, detail="closed")

    def __enter__(self) -> "PipeConnection":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


# --------------------------------------------------------------------------
# 服务器 / 客户端
# --------------------------------------------------------------------------


class PipeServer:
    """命名管道服务器（每终端一个名字；owner-only DACL + 占名检测）。

    - ``create()`` 用 ``FILE_FLAG_FIRST_PIPE_INSTANCE`` 创建**首个**实例：
      名字已被别的进程占用时创建失败（``PipeBusyError``），绝不静默共用同名管道；
      后续实例（连接过程中重建）不再带该标志——这是 Win32 的硬性要求；
    - ``accept(timeout)`` 有界：超时返回 ``None`` 且**保留**待连接实例（可重试）；
      ``cancel_accept()`` 取消在飞连接，取消后的实例被关闭并在下次 ``accept``
      重新创建（重新做占名检测）；
    - 并发连接有界（``max_active_connections``）；超限直接返回 ``None`` 并计入
      诊断（不排队、不无界增长）。
    """

    def __init__(
        self,
        terminal_id_or_name: str,
        *,
        max_instances: int = DEFAULT_MAX_INSTANCES,
        max_active_connections: int = DEFAULT_MAX_ACTIVE_CONNECTIONS,
        max_frame_bytes: int = _ipc.DEFAULT_MAX_FRAME_BYTES,
        inbound_buffer: int = DEFAULT_PIPE_BUFFER_BYTES,
        outbound_buffer: int = DEFAULT_PIPE_BUFFER_BYTES,
        diagnostics: _ipc.IpcDiagnostics | None = None,
    ) -> None:
        _require_windows()
        if not 1 <= int(max_instances) <= 254:
            raise ValueError("max_instances must be within [1, 254]")
        self.name = _resolve_pipe_name(str(terminal_id_or_name))
        self.terminal_id = self.name[len(PIPE_PREFIX):] if self.name.startswith(PIPE_PREFIX) else self.name
        self.max_instances = int(max_instances)
        self.max_active_connections = int(max_active_connections)
        self.max_frame_bytes = int(max_frame_bytes)
        self.inbound_buffer = int(inbound_buffer)
        self.outbound_buffer = int(outbound_buffer)
        self.diagnostics = diagnostics if diagnostics is not None else _ipc.IpcDiagnostics(label=self.terminal_id)
        # RLock：finish/_drop/create 会在持锁路径内互相调用（同线程重入）。
        self._lock = threading.RLock()
        self._pending: dict[str, Any] | None = None
        self._connections: list[PipeConnection] = []
        self._own_instances = 0
        self._active = 0
        self._closing = False
        self._closed = False
        self.accept_timeouts = 0
        self.accept_cancels = 0

    # -- 生命周期 ------------------------------------------------------
    def create(self) -> None:
        """创建**首个**实例并占用管道名（``FIRST_PIPE_INSTANCE``）。"""
        with self._lock:
            if self._closed or self._closing:
                raise PipeClosed("server is closed")
            if self._pending is not None or self._own_instances > 0:
                return
            self._pending = self._create_instance_locked()

    def _create_instance_locked(self) -> dict[str, Any]:
        # 只要本进程当前**不持有**任何同名实例，就带上 FIRST_PIPE_INSTANCE：
        # 名字被别人占住时创建会失败（PipeBusyError），绝不静默共用同名管道；
        # 一旦自己已有实例，Win32 不允许再带该标志（会 ERROR_ACCESS_DENIED）。
        first = self._own_instances == 0
        open_mode = PIPE_ACCESS_DUPLEX | FILE_FLAG_OVERLAPPED
        if first:
            open_mode |= FILE_FLAG_FIRST_PIPE_INSTANCE
        # 注意：PIPE_REJECT_REMOTE_CLIENTS 是 **dwPipeMode** 标志；放进 dwOpenMode
        # 会被 Win32 判为 ERROR_INVALID_PARAMETER(87)（本 TA 在测试用 raw 客户端
        # 复现过该错误，见 tests/test_terminal_runner_ipc.py 的创建参数 spy 用例）。
        pipe_mode = (
            PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS
        )
        security = OwnerOnlySecurity()
        try:
            handle = _k32.CreateNamedPipeW(
                self.name,
                open_mode,
                pipe_mode,
                self.max_instances,
                self.outbound_buffer,
                self.inbound_buffer,
                0,
                security.security_attributes,
            )
            if is_invalid_handle(handle):
                error = _last_error()
                if error == ERROR_ACCESS_DENIED and first:
                    raise PipeBusyError(
                        "pipe name is already in use by another server instance "
                        "(FILE_FLAG_FIRST_PIPE_INSTANCE rejected); refusing to share the name"
                    )
                raise PipeIOError(f"CreateNamedPipeW failed: {_error_text(error)}")
        finally:
            security.close()
        self._own_instances += 1
        event = int(_k32.CreateEventW(None, True, False, None))
        if not event:
            _close_handle(int(handle))
            self._own_instances -= 1
            raise PipeIOError(f"CreateEventW failed: {_error_text()}")
        overlapped = _OVERLAPPED()
        overlapped.hEvent = wintypes.HANDLE(event)
        return {
            "handle": int(handle),
            "event": event,
            "ov": overlapped,
            "issued": False,
            "cancelled": False,
        }

    def _drop_pending_locked(self, *, wait_event_ms: int = 0) -> bool:
        """关闭待连接实例。

        ``wait_event_ms>0`` 时先等取消的 I/O 落地：**只有确认落地才关闭句柄并释放
        ``OVERLAPPED``**；否则返回 ``False`` 并保留实例（可重试），因为内核在操作
        落地前仍会写那块内存。
        """
        pending = self._pending
        if pending is None:
            return True
        if wait_event_ms and pending["issued"]:
            wait = int(
                _k32.WaitForSingleObject(wintypes.HANDLE(pending["event"]), int(wait_event_ms))
            )
            if wait not in (WAIT_OBJECT_0, WAIT_ABANDONED):
                return False
        _close_handle(pending["handle"])
        _close_handle(pending["event"])
        self._own_instances -= 1
        self._pending = None
        return True

    def accept(self, *, timeout: float | None = None) -> PipeConnection | None:
        """有界等待一个客户端连接；超时 ``None``、取消 ``PipeCancelled``。"""
        budget = DEFAULT_CONNECT_TIMEOUT if timeout is None else max(0.0, float(timeout))
        with self._lock:
            if self._closed or self._closing:
                raise PipeClosed("server is closed")
            if self._active >= self.max_active_connections:
                self.diagnostics.bump("connections_rejected_capacity")
                return None
            if self._pending is None:
                self._pending = self._create_instance_locked()
            pending = self._pending
        deadline = time.monotonic() + budget
        while True:
            with self._lock:
                if pending["cancelled"] or self._pending is not pending:
                    self.accept_cancels += 1
                    raise PipeCancelled("accept was cancelled")
                if not pending["issued"]:
                    _k32.ResetEvent(wintypes.HANDLE(pending["event"]))
                    ok = bool(
                        _k32.ConnectNamedPipe(
                            wintypes.HANDLE(pending["handle"]), ctypes.byref(pending["ov"])
                        )
                    )
                    if ok or _last_error() == ERROR_PIPE_CONNECTED:
                        return self._finish_accept(pending)
                    error = _last_error()
                    if error == ERROR_NO_DATA:
                        # 客户端在 ConnectNamedPipe 之前连上又断开（瞬时状态，不是致命错误）：
                        # 重建实例并在同一预算内重试，**不**把 accept 循环打死。
                        self._drop_pending_locked()
                        self.diagnostics.record_error("accept: client left before connect; retrying")
                        if time.monotonic() >= deadline:
                            self.accept_timeouts += 1
                            return None
                        pending = self._pending = self._create_instance_locked()
                        continue
                    if error != ERROR_IO_PENDING:
                        self._drop_pending_locked()
                        raise PipeIOError(f"ConnectNamedPipe failed: {_error_text(error)}")
                    pending["issued"] = True
            if time.monotonic() >= deadline:
                self.accept_timeouts += 1
                return None
            remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
            wait = int(_k32.WaitForSingleObject(wintypes.HANDLE(pending["event"]), remaining_ms))
            if wait == WAIT_TIMEOUT:
                self.accept_timeouts += 1
                return None
            if wait not in (WAIT_OBJECT_0, WAIT_ABANDONED):
                with self._lock:
                    if self._pending is pending:
                        self._drop_pending_locked()
                raise PipeIOError(f"WaitForSingleObject(connect) failed: {_error_text()}")
            # 完成后处理**必须**在锁内：取消方在锁内 set(cancelled)→CancelIoEx→
            # 等事件→关句柄，因此这里要么看到 cancelled=真（不碰已关闭的句柄），
            # 要么在句柄仍有效时完成检查。两条路径都不关一个仍有在飞 I/O 的句柄。
            with self._lock:
                if pending["cancelled"] or self._pending is not pending:
                    self.accept_cancels += 1
                    raise PipeCancelled("accept was cancelled")
                transferred = wintypes.DWORD(0)
                if _k32.GetOverlappedResult(
                    wintypes.HANDLE(pending["handle"]),
                    ctypes.byref(pending["ov"]),
                    ctypes.byref(transferred),
                    False,
                ):
                    return self._finish_accept(pending)
                error = _last_error()
                if error == ERROR_IO_INCOMPLETE:
                    continue
                if error == ERROR_OPERATION_ABORTED:
                    self._drop_pending_locked()
                    self.accept_cancels += 1
                    raise PipeCancelled("accept was cancelled (CancelIoEx)")
                if error == ERROR_PIPE_CONNECTED:
                    return self._finish_accept(pending)
                self._drop_pending_locked()
                raise PipeIOError(f"GetOverlappedResult(connect) failed: {_error_text(error)}")

    def _finish_accept(self, pending: dict[str, Any]) -> PipeConnection:
        connection = PipeConnection(
            pending["handle"],
            role="server",
            max_frame_bytes=self.max_frame_bytes,
            on_close=self._forget_connection,
            detail=f"server instance of {self.name}",
            diagnostics=self.diagnostics,
        )
        with self._lock:
            if self._pending is pending:
                self._pending = None
            self._own_instances -= 1  # 实例句柄移交给连接
            self._connections.append(connection)
            self._active += 1
        _close_handle(pending["event"])
        self.diagnostics.bump("connections_accepted")
        return connection

    def _forget_connection(self, connection: PipeConnection) -> None:
        with self._lock:
            if connection in self._connections:
                self._connections.remove(connection)
                self._active -= 1

    def cancel_accept(self) -> bool:
        """取消在飞 ``ConnectNamedPipe``（幂等；无在飞连接返回 False）。

        取消后**立即重建**待连接实例（重新做占名检测）：否则管道名会短暂无人
        持有，客户端在此期间连不上（本 TA 在测试里复现过这个窗口）。
        """
        with self._lock:
            pending = self._pending
            if pending is None or not pending["issued"]:
                return False
            pending["cancelled"] = True
        cancelled = bool(_k32.CancelIoEx(wintypes.HANDLE(pending["handle"]), ctypes.byref(pending["ov"])))
        # 等取消的 I/O 落地后再关句柄（避免关掉仍被内核引用的 OVERLAPPED）；
        # 未落地则保留实例并记录，交由后续 accept/close 重试，绝不提前释放内存。
        with self._lock:
            if self._pending is pending:
                if not self._drop_pending_locked(wait_event_ms=2000):
                    self.diagnostics.record_error("cancel_accept: pending connect did not converge")
                    return cancelled
            if not self._closing and not self._closed:
                try:
                    self._pending = self._create_instance_locked()
                except PipeError as exc:
                    self.diagnostics.record_error(
                        f"cancel_accept: pipe instance could not be re-armed: {type(exc).__name__}"
                    )
                    self._pending = None
        return cancelled

    def close(self, *, timeout: float | None = None) -> CloseReport:
        """关闭服务器：先关连接，再取消待连接实例；未收敛则保留资源可重试。"""
        budget = DEFAULT_CLOSE_TIMEOUT if timeout is None else max(0.0, float(timeout))
        deadline = time.monotonic() + budget
        with self._lock:
            self._closing = True
            connections = list(self._connections)
        converged = True
        details: list[str] = []
        for connection in connections:
            report = connection.close(timeout=max(0.05, deadline - time.monotonic()))
            if not report.converged:
                converged = False
                details.append("connection-close-not-converged")
        with self._lock:
            pending = self._pending
        if pending is not None:
            if pending["issued"] and not pending["cancelled"]:
                pending["cancelled"] = True
                _k32.CancelIoEx(wintypes.HANDLE(pending["handle"]), ctypes.byref(pending["ov"]))
            remaining = max(0, int((deadline - time.monotonic()) * 1000))
            wait = int(_k32.WaitForSingleObject(wintypes.HANDLE(pending["event"]), remaining))
            if wait in (WAIT_OBJECT_0, WAIT_ABANDONED):
                with self._lock:
                    if self._pending is pending:
                        self._drop_pending_locked()
            else:
                converged = False
                details.append("connect-cancel-not-converged")
        with self._lock:
            if converged:
                self._closed = True
        return CloseReport(
            converged=converged,
            closed=converged,
            in_flight=sum(connection.in_flight for connection in connections),
            detail="; ".join(details) if details else "closed",
        )

    @property
    def active_connections(self) -> int:
        return self._active

    @property
    def own_instances(self) -> int:
        return self._own_instances

    @property
    def name_owned(self) -> bool:
        """本进程当前是否持有该管道名的实例（占名状态）。"""
        return self._own_instances > 0

    @property
    def closed(self) -> bool:
        return self._closed


class PipeClient:
    """命名管道客户端：有界重试连接；连接后核对**本地**管道（远端管道拒绝）。"""

    def __init__(
        self,
        terminal_id_or_name: str,
        *,
        max_frame_bytes: int = _ipc.DEFAULT_MAX_FRAME_BYTES,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        poll_interval: float = 0.02,
    ) -> None:
        _require_windows()
        self.name = _resolve_pipe_name(str(terminal_id_or_name))
        self.max_frame_bytes = int(max_frame_bytes)
        self.connect_timeout = float(connect_timeout)
        self.poll_interval = float(poll_interval)

    def connect(self, *, timeout: float | None = None) -> PipeConnection:
        budget = self.connect_timeout if timeout is None else max(0.0, float(timeout))
        deadline = time.monotonic() + budget
        last_error = 0
        while True:
            if time.monotonic() >= deadline:
                raise PipeTimeout(
                    f"pipe {self.name} not available within {budget}s (last error {last_error})"
                )
            remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
            if not _k32.WaitNamedPipeW(self.name, remaining_ms):
                last_error = _last_error()
                if last_error in (ERROR_FILE_NOT_FOUND, ERROR_SEM_TIMEOUT):
                    continue
                raise PipeIOError(f"WaitNamedPipeW failed: {_error_text(last_error)}")
            handle = _k32.CreateFileW(
                self.name,
                GENERIC_READ | GENERIC_WRITE,
                0,
                None,
                OPEN_EXISTING,
                FILE_FLAG_OVERLAPPED,
                None,
            )
            if is_invalid_handle(handle):
                last_error = _last_error()
                if last_error in (ERROR_PIPE_BUSY, ERROR_FILE_NOT_FOUND):
                    time.sleep(self.poll_interval)
                    continue
                raise PipeIOError(f"CreateFileW failed for {self.name}: {_error_text(last_error)}")
            connection = PipeConnection(
                int(handle), role="client", max_frame_bytes=self.max_frame_bytes, detail=f"client of {self.name}"
            )
            if connection.peer_server_pid() is None:
                # 拿不到服务器 PID = 不是本机管道（或已断开）：fail-closed 拒绝。
                connection.close(timeout=1.0)
                raise PipeIOError(
                    "connected pipe has no local server process id (remote pipe?); refusing"
                )
            return connection


__all__ = [
    "PIPE_PREFIX",
    "DEFAULT_MAX_INSTANCES",
    "DEFAULT_MAX_ACTIVE_CONNECTIONS",
    "DEFAULT_PIPE_BUFFER_BYTES",
    "DEFAULT_CONNECT_TIMEOUT",
    "DEFAULT_CLOSE_TIMEOUT",
    "PIPE_REJECT_REMOTE_CLIENTS",
    "FILE_FLAG_FIRST_PIPE_INSTANCE",
    "PipeError",
    "PipeSecurityError",
    "PipeBusyError",
    "PipeTimeout",
    "PipeClosed",
    "PipeCancelled",
    "PipeIOError",
    "CloseReport",
    "pipe_name_for",
    "is_invalid_handle",
    "INVALID_HANDLE_UNSIGNED",
    "ProcessIdentityHandle",
    "probe_process",
    "default_identity_probe",
    "current_process_identity",
    "terminate_verified_process",
    "current_user_sid",
    "OwnerOnlySecurity",
    "path_exists",
    "is_reparse_point",
    "create_owner_only_directory",
    "apply_owner_only_acl",
    "write_file_owner_only",
    "replace_file_atomic",
    "delete_file",
    "dacl_entries",
    "owner_sid",
    "PipeConnection",
    "PipeServer",
    "PipeClient",
]
