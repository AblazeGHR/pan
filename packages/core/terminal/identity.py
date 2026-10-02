"""Windows 进程身份：retained-handle 三态探针 + 单句柄核验终止（P1，TA-B）。

设计要点（与 ``contracts.py`` / P0 接口文档 r2 对齐）：

- **存活判定唯一原语** = 同一进程 handle 上的 ``WaitForSingleObject(h, 0)``：
  ``WAIT_OBJECT_0`` = 已退出（真实 signaled 证据）、``WAIT_TIMEOUT`` = 存活、
  其它 = 未知。``GetExitCodeProcess`` 只作信息字段（259 既是 STILL_ACTIVE
  也是合法退出码，不能用来判活）。
- **三态探针**：``probe_process(pid)`` 返回 ``ProcessProbe``；打不开/查不到一律
  ``UNKNOWN``（fail-closed；``OpenProcess`` 失败绝不冒充 dead）。``DEAD`` 只来自
  真实 signaled 证据（retained handle 或新开 handle 的 signaled 状态）。
- **身份** = pid + 原始 64 位 FILETIME（100ns，自 1601-01-01）。Python 侧一律
  ``int``；任何跨 JS/JSON 的输出必须走 :func:`filetime_json` / :func:`identity_json`
  转成**字符串**（raw64 ≈ 1.3e17 超过 JS 安全整数范围）。
- ``kill_verified``：单句柄原子核验终止——``OpenProcess(SYNCHRONIZE |
  QUERY_LIMITED_INFORMATION | TERMINATE)`` 一次；状态、FILETIME 精确匹配与
  ``TerminateProcess`` 全部作用在**同一个 handle** 上；``unknown`` / 错身份 /
  已退出一律拒绝终止。这是本模块唯一的按 PID 终止路径（无 taskkill/WMI）。
- **导入无副作用**：不在导入期加载 ``kernel32``（延迟到首次调用）；非 Windows
  调用抛 ``BackendUnavailableError``，模块本身可在任何平台导入。

边界（如实声明，不得当作已验证能力）：

1. 探针按 PID 打开全新 handle：PID 复用时若新进程存活，FILETIME 比对会
   mismatch -> 调用方 fail-closed 拒绝；本模块不保证“打不开 = 已退出”。
2. ``kill_verified`` 的 FILETIME 校验防的是“打开后 PID 已被复用”；对
   “打开前 PID 已被复用”的场景由调用方先做 held-identity 比对（同 handle
   原则）后再调用。
3. 未做跨 build / 跨机型的长时间运行验证（与 spawn spike 相同边界）。
"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes
from typing import Any

from .contracts import (
    BackendUnavailableError,
    ProcessIdentity,
    ProcessProbe,
    ProcessStatus,
)

__all__ = [
    "SYNCHRONIZE",
    "PROCESS_QUERY_LIMITED_INFORMATION",
    "PROCESS_TERMINATE",
    "THREAD_TERMINATE",
    "STILL_ACTIVE",
    "WAIT_OBJECT_0",
    "WAIT_TIMEOUT",
    "WAIT_FAILED",
    "ERROR_INVALID_PARAMETER",
    "ERROR_OPERATION_ABORTED",
    "ERROR_BROKEN_PIPE",
    "WindowsApiUnavailable",
    "kernel32",
    "filetime_to_int",
    "filetime_json",
    "identity_json",
    "read_creation_filetime",
    "informational_exit_code",
    "wait_state",
    "probe_handle",
    "probe_process",
    "open_process_for_probe",
    "kill_verified",
    "VerifiedKillResult",
    "cancel_synchronous_io",
    "open_current_thread_handle",
    "close_handle_checked",
    "get_process_handle_count",
    "format_last_error",
    "win32_oserror",
]

SYNCHRONIZE = 0x00100000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_TERMINATE = 0x0001
THREAD_TERMINATE = 0x0001

#: 信息字段专用：既是“仍在运行”也是合法退出码，不能用于判活。
STILL_ACTIVE = 259

WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102
WAIT_FAILED = 0xFFFFFFFF

ERROR_INVALID_PARAMETER = 87
ERROR_OPERATION_ABORTED = 995
ERROR_BROKEN_PIPE = 109
ERROR_NOT_FOUND = 1168


class WindowsApiUnavailable(BackendUnavailableError):
    """非 Windows 平台或 kernel32 加载失败；构造时解释，不阻断整个 Pan。"""


_kernel32: Any | None = None


def kernel32() -> Any:
    """延迟加载并缓存 kernel32 的函数表（导入期零副作用）。

    在非 Windows 平台上调用 -> :class:`WindowsApiUnavailable`。所有函数的
    ``argtypes`` / ``restype`` 在这里一次性绑定（官方 ABI）。
    """
    global _kernel32
    if sys.platform != "win32":
        raise WindowsApiUnavailable(
            f"Windows kernel32 不可用（sys.platform={sys.platform!r}）："
            "ConPtyBackend 仅支持 Windows；本模块在非 Windows 上可导入但不可调用。"
        )
    if _kernel32 is not None:
        return _kernel32
    k = ctypes.WinDLL("kernel32", use_last_error=True)

    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.OpenProcess.restype = wintypes.HANDLE
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.CloseHandle.restype = wintypes.BOOL
    k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k.WaitForSingleObject.restype = wintypes.DWORD
    k.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    k.GetProcessTimes.restype = wintypes.BOOL
    k.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k.GetExitCodeProcess.restype = wintypes.BOOL
    k.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k.TerminateProcess.restype = wintypes.BOOL
    k.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.OpenThread.restype = wintypes.HANDLE
    k.CancelSynchronousIo.argtypes = [wintypes.HANDLE]
    k.CancelSynchronousIo.restype = wintypes.BOOL
    k.GetCurrentThreadId.restype = wintypes.DWORD
    k.GetCurrentProcess.restype = wintypes.HANDLE
    k.GetProcessHandleCount.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.DWORD),
    ]
    k.GetProcessHandleCount.restype = wintypes.BOOL

    _kernel32 = k
    return k


def format_last_error(code: int | None = None) -> str:
    """脱敏的错误描述：只给数字（不调用 FormatMessage，避免额外副作用）。"""
    if code is None:
        code = ctypes.get_last_error()
    return f"winerror={int(code)}"


def win32_oserror(err: int, message: str) -> OSError:
    """构造带 ``winerror`` 的 ``OSError``（两参构造只填 errno，不填 winerror）。"""
    exc = OSError(int(err), message)
    try:
        exc.winerror = int(err)  # type: ignore[attr-defined]
    except AttributeError:  # pragma: no cover - 平台差异兜底
        pass
    return exc


def filetime_to_int(ft: wintypes.FILETIME) -> int:
    """原始 64 位 100ns FILETIME -> Python 精确 int（不做浮点换算）。"""
    return (int(ft.dwHighDateTime) << 32) | int(ft.dwLowDateTime)


def filetime_json(value: int | None) -> str | None:
    """跨 JS/JSON 边界的 FILETIME 一律用字符串（raw64 超出 JS 安全整数范围）。"""
    return None if value is None else str(int(value))


def identity_json(identity: ProcessIdentity | None) -> dict[str, Any] | None:
    """``ProcessIdentity`` 的 JSON 安全形式（FILETIME 为字符串）。"""
    if identity is None:
        return None
    return {
        "pid": identity.pid,
        "created_at_filetime": filetime_json(identity.created_at_filetime),
        "created_at": identity.created_at,
        "image": identity.image,
    }


def read_creation_filetime(hprocess: int) -> int | None:
    """从**已持有的**进程 handle 读取创建 FILETIME（raw64）；失败返回 None。

    ``GetProcessTimes`` 四个 ``[out]`` 参数均非 optional；这里全部提供真实指针。
    """
    k = kernel32()
    ft, exit_ft, kernel_ft, user_ft = (wintypes.FILETIME() for _ in range(4))
    ok = k.GetProcessTimes(
        wintypes.HANDLE(hprocess),
        ctypes.byref(ft),
        ctypes.byref(exit_ft),
        ctypes.byref(kernel_ft),
        ctypes.byref(user_ft),
    )
    return filetime_to_int(ft) if ok else None


def informational_exit_code(hprocess: int) -> int | None:
    """退出码信息字段（259 有歧义；仅用于“已 signaled 之后”的信息读取）。"""
    k = kernel32()
    code = wintypes.DWORD(0)
    if not k.GetExitCodeProcess(wintypes.HANDLE(hprocess), ctypes.byref(code)):
        return None
    return int(code.value)


def wait_state(hprocess: int) -> ProcessStatus:
    """单一存活原语：``WaitForSingleObject(h, 0)`` 的显式三态。

    - ``WAIT_OBJECT_0`` -> ``ProcessStatus.DEAD``（真实 signaled 证据）；
    - ``WAIT_TIMEOUT`` -> ``ProcessStatus.ALIVE``；
    - 其它（WAIT_FAILED 等）-> ``ProcessStatus.UNKNOWN``（拒绝猜测）。
    """
    if not hprocess:
        return ProcessStatus.UNKNOWN
    rc = kernel32().WaitForSingleObject(wintypes.HANDLE(hprocess), 0)
    if rc == WAIT_OBJECT_0:
        return ProcessStatus.DEAD
    if rc == WAIT_TIMEOUT:
        return ProcessStatus.ALIVE
    return ProcessStatus.UNKNOWN


def probe_handle(hprocess: int, pid: int | None = None) -> ProcessProbe:
    """对已持有 handle 做三态探针（DEAD 证据来自 signaled 语义）。

    - ALIVE 时必须能读到 FILETIME（否则 UNKNOWN，fail-closed）；
    - DEAD 时身份（若可读）一并附上供诊断，但 DEAD 结论来自 signaled。
    """
    state = wait_state(hprocess)
    ft = read_creation_filetime(hprocess)
    if state is ProcessStatus.ALIVE:
        if ft is None:
            return ProcessProbe(
                ProcessStatus.UNKNOWN,
                None,
                "handle signaled alive 但读不到 FILETIME：无结论（fail-closed）",
            )
        return ProcessProbe(ProcessStatus.ALIVE, ProcessIdentity(pid=pid, created_at_filetime=ft), "")
    if state is ProcessStatus.DEAD:
        identity = (
            ProcessIdentity(pid=pid, created_at_filetime=ft) if ft is not None else None
        )
        return ProcessProbe(ProcessStatus.DEAD, identity, "retained/opened handle signaled: 已退出")
    return ProcessProbe(ProcessStatus.UNKNOWN, None, "WaitForSingleObject 未知状态：fail-closed")


def open_process_for_probe(pid: int) -> int | None:
    """按 PID 打开只读探针 handle；失败返回 None（由调用方按 UNKNOWN 处理）。"""
    k = kernel32()
    return int(
        k.OpenProcess(
            SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
        )
        or 0
    )


def probe_process(pid: int) -> ProcessProbe:
    """诊断探针（**非清理放行证据**，r3 §13.4）：按 PID 打开新 handle 判定存活。

    - 打开成功且 ``WAIT_TIMEOUT``（存活）且能读 FILETIME -> ``ALIVE`` + identity；
    - 其它（**含打开成功但 signaled**、打不开）-> ``UNKNOWN``。

    为什么 signaled 也返回 UNKNOWN：新开的陌生 handle 的 signaled 结果无法绑定到
    “spawn 时记录的我们的进程”——PID 复用会让“陌生进程已退出”冒充“我们的进程已
    退出”。``DEAD`` 放行证据**必须**来自 spawn 时同一 retained handle
    （:func:`probe_handle`）或 Job 对象层面证据（见 ``guard.JobObjectGuard``）。
    需要给 runtime 接线的场景请用后端自己的绑定探针（如
    ``ConPtyBackend.probe``），而不是本函数。
    """
    k = kernel32()
    h = k.OpenProcess(
        SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
    )
    if not h:
        return ProcessProbe(
            ProcessStatus.UNKNOWN,
            None,
            f"OpenProcess 失败（{format_last_error()}）：查不到/打不开 = unknown，不等于 dead",
        )
    try:
        probe = probe_handle(int(h), pid=int(pid))
    finally:
        # 只读探针句柄：关闭返回值检查；失败不回滚结论，由句柄计数回归测试发现。
        close_handle_checked(int(h))
    if probe.status is ProcessStatus.DEAD:
        return ProcessProbe(
            ProcessStatus.UNKNOWN,
            probe.identity,
            "现查陌生 PID 的 signaled 结果不作为 DEAD 证据（r3 §13.4）："
            "请用 spawn 时同一 retained handle / Job 证据",
        )
    return probe


# ---------------------------------------------------------------------------
# 核验终止（单句柄）
# ---------------------------------------------------------------------------


class VerifiedKillResult:
    """``kill_verified`` 的完整结果（JSON 安全；FILETIME 为字符串）。"""

    __slots__ = (
        "pid",
        "attempted",
        "killed",
        "reason",
        "state",
        "expected_filetime",
        "observed_filetime",
        "last_error",
        "exit_code_informational",
        "signaled_after_terminate",
        "handle_closed",
    )

    def __init__(
        self,
        *,
        pid: int,
        attempted: bool,
        killed: bool,
        reason: str,
        state: str,
        expected_filetime: int | None = None,
        observed_filetime: int | None = None,
        last_error: int | None = None,
        exit_code_informational: int | None = None,
        signaled_after_terminate: bool | None = None,
    ) -> None:
        self.pid = pid
        self.attempted = attempted
        self.killed = killed
        self.reason = reason
        self.state = state
        self.expected_filetime = expected_filetime
        self.observed_filetime = observed_filetime
        self.last_error = last_error
        self.exit_code_informational = exit_code_informational
        self.signaled_after_terminate = signaled_after_terminate
        #: 本次调用的探针句柄是否已成功关闭（CloseHandle 返回检查）。
        self.handle_closed: bool | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "attempted": self.attempted,
            "killed": self.killed,
            "reason": self.reason,
            "state": self.state,
            "expected_filetime": filetime_json(self.expected_filetime),
            "observed_filetime": filetime_json(self.observed_filetime),
            "last_error": self.last_error,
            "exit_code_informational": self.exit_code_informational,
            "signaled_after_terminate": self.signaled_after_terminate,
            "handle_closed": self.handle_closed,
        }

    def __repr__(self) -> str:  # pragma: no cover - 诊断
        return (
            f"VerifiedKillResult(pid={self.pid}, killed={self.killed}, "
            f"reason={self.reason!r}, state={self.state!r})"
        )


def _expected_filetime(expected: ProcessIdentity | int | None) -> int | None:
    if expected is None:
        return None
    if isinstance(expected, ProcessIdentity):
        if expected.created_at_filetime is None:
            return None
        return int(expected.created_at_filetime)
    return int(expected)


def kill_verified(
    pid: int,
    expected: ProcessIdentity | int | None,
    *,
    exit_code: int = 0xDEAD,
    wait_timeout: float = 5.0,
) -> VerifiedKillResult:
    """单句柄原子核验终止：身份不匹配 / 状态未知 / 已退出一律不终止。

    步骤（全部作用在**同一个** ``OpenProcess(SYNCHRONIZE |
    QUERY_LIMITED_INFORMATION | TERMINATE)`` 句柄上）：

    1. 打开失败 -> 拒绝（``open_failed``，状态 unknown）；
    2. ``WaitForSingleObject(h,0)``：``unknown`` -> 拒绝；``dead`` -> 拒绝
       （``not_running``，附信息退出码）；
    3. FILETIME 与期望值**精确相等**才继续；否则拒绝（``identity_mismatch``）；
    4. ``TerminateProcess`` 后在该 handle 上 ``WaitForSingleObject`` 有界等待
       signaled，如实记录。

    ``expected`` 缺可比对 FILETIME（``None`` 或 identity 无 filetime）时拒绝
    （``expected_identity_missing``）——不可验证即不终止。
    """
    k = kernel32()
    expected_ft = _expected_filetime(expected)
    if expected_ft is None:
        return VerifiedKillResult(
            pid=int(pid),
            attempted=False,
            killed=False,
            reason="expected_identity_missing",
            state="unknown",
            last_error=None,
        )
    h = k.OpenProcess(
        SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_TERMINATE,
        False,
        int(pid),
    )
    if not h:
        return VerifiedKillResult(
            pid=int(pid),
            attempted=False,
            killed=False,
            reason="open_failed",
            state="unknown",
            expected_filetime=expected_ft,
            last_error=int(ctypes.get_last_error()),
        )
    result: VerifiedKillResult | None = None
    try:
        result = _kill_verified_with_handle(
            k, int(pid), expected_ft, int(exit_code), float(wait_timeout), int(h)
        )
    finally:
        closed = close_handle_checked(int(h))
    result.handle_closed = bool(closed)
    return result


def _kill_verified_with_handle(
    k: Any,
    pid: int,
    expected_ft: int,
    exit_code: int,
    wait_timeout: float,
    handle: int,
) -> VerifiedKillResult:
    """``kill_verified`` 的句柄内主体（打开/关闭由调用方负责，全程同一 handle）。"""
    state = wait_state(handle)
    observed_ft = read_creation_filetime(handle)
    if state is ProcessStatus.UNKNOWN:
        return VerifiedKillResult(
            pid=pid,
            attempted=False,
            killed=False,
            reason="state_unknown",
            state="unknown",
            expected_filetime=expected_ft,
            observed_filetime=observed_ft,
            exit_code_informational=informational_exit_code(handle),
        )
    if state is ProcessStatus.DEAD:
        return VerifiedKillResult(
            pid=pid,
            attempted=False,
            killed=False,
            reason="not_running",
            state="dead",
            expected_filetime=expected_ft,
            observed_filetime=observed_ft,
            exit_code_informational=informational_exit_code(handle),
        )
    if observed_ft is None or observed_ft != expected_ft:
        return VerifiedKillResult(
            pid=pid,
            attempted=False,
            killed=False,
            reason="identity_mismatch",
            state="alive",
            expected_filetime=expected_ft,
            observed_filetime=observed_ft,
            exit_code_informational=informational_exit_code(handle),
        )
    ctypes.set_last_error(0)
    ok = bool(k.TerminateProcess(wintypes.HANDLE(handle), exit_code))
    last_error = int(ctypes.get_last_error())
    if not ok:
        return VerifiedKillResult(
            pid=pid,
            attempted=True,
            killed=False,
            reason="terminate_failed",
            state="alive",
            expected_filetime=expected_ft,
            observed_filetime=observed_ft,
            last_error=last_error,
        )
    signaled = False
    if wait_timeout > 0:
        rc = k.WaitForSingleObject(wintypes.HANDLE(handle), int(wait_timeout * 1000))
        signaled = rc == WAIT_OBJECT_0
    return VerifiedKillResult(
        pid=pid,
        attempted=True,
        killed=True,
        reason="terminated",
        state="dead" if signaled else "alive",
        expected_filetime=expected_ft,
        observed_filetime=observed_ft,
        last_error=last_error,
        signaled_after_terminate=signaled,
    )


# ---------------------------------------------------------------------------
# 取消原语 / 句柄工具（backend 共用）
# ---------------------------------------------------------------------------


def open_current_thread_handle(access: int = THREAD_TERMINATE) -> int:
    """打开**当前线程**的可取消句柄（``CancelSynchronousIo`` 需要 THREAD_TERMINATE）。

    失败抛 ``OSError``（调用方决定语义；backend 的 read/write 注册要求可取消，
    拿不到句柄即拒绝进入阻塞 I/O）。
    """
    k = kernel32()
    tid = int(k.GetCurrentThreadId())
    h = k.OpenThread(access, False, tid)
    if not h:
        raise win32_oserror(
            int(ctypes.get_last_error()),
            "OpenThread(THREAD_TERMINATE) 失败：无法注册可取消的同步 I/O",
        )
    return int(h)


def cancel_synchronous_io(thread_handle: int) -> tuple[bool, int]:
    """取消指定线程上的进行中同步 I/O。返回 ``(ok, last_error)``。

    线程当前没有挂起 I/O 时返回 ``(False, ERROR_NOT_FOUND)``——调用方按
    “重试直到线程收敛”处理（见 backend 的 stop/join 循环）。
    """
    k = kernel32()
    ctypes.set_last_error(0)
    ok = bool(k.CancelSynchronousIo(wintypes.HANDLE(int(thread_handle))))
    return ok, int(ctypes.get_last_error())


def close_handle_checked(handle: int) -> bool:
    """``CloseHandle`` 并检查返回值（False = 该句柄可能仍被持有）。"""
    return bool(kernel32().CloseHandle(wintypes.HANDLE(int(handle))))


def get_process_handle_count(process_handle: int | None = None) -> int | None:
    """当前进程（或给定进程 handle）的句柄计数，供回归测试使用。"""
    k = kernel32()
    h = wintypes.HANDLE(process_handle) if process_handle else k.GetCurrentProcess()
    count = wintypes.DWORD(0)
    if not k.GetProcessHandleCount(h, ctypes.byref(count)):
        return None
    return int(count.value)
