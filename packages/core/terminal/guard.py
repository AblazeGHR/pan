"""整树所有权守卫：Job Object（KILL_ON_JOB_CLOSE），P1（TA-B）。

契约（``contracts.py::TreeGuard`` / P0 接口文档 r2）：

- 顺序：**先** ``owned_pids`` 快照所有权，**再** ``terminate_tree`` 终止，
  **最后** ``remaining`` 核对残留；
- 枚举**整个 Job**（``QueryInformationJobObject`` / ``JobObjectBasicProcessIdList``），
  **不靠 psutil、不按根 PID 扫描**：根进程已死仍能枚举出仍在 Job 内的孙进程；
- 终止只有 ``TerminateJobObject``（内核级整树）；**没有按 PID 裸杀路径**
  （PID 列表仅用于诊断与残留核对）；
- 查询失败 = 所有权/残留未知 -> 抛异常，调用方必须 fail-closed；
- ``describe()["os_level_guard"]`` 如实标注 ``True``（本实现是内核级 Job 句柄，
  不是测试观察方案）。

r3 纪律（核心窄修 b5017d1d）：**guard 的每个操作自身有界、快速返回**——
``owned_pids``/``remaining`` 是单次 ``QueryInformationJobObject``（成员列表
扩容循环有界），``terminate_tree`` 只做单次 ``TerminateJobObject`` + 受
``timeout`` 约束的轮询（上限见 :data:`MAX_TERMINATE_TREE_TIMEOUT`），不存在
阻塞等待；runtime 的 ``_BoundedCall`` 只是外层兜底，不代替本模块的 deadline。

成员不得 breakaway：Job 只设置 ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE``，
**不设置** ``BREAKAWAY_OK`` / ``SILENT_BREAKAWAY_OK``（创建后立刻回读
``LimitFlags`` 自证；若发现 breakaway 位被置位则关闭并 fail-closed）。

边界（如实声明）：

- 句柄关闭后查询一律失败（``GuardQueryError``）——不会“查询到 ambient Job”
  （KILL_ON_JOB_CLOSE Job 关闭即整树终止，随后句柄不再可用）；
- 嵌套 Job：本机 ambient Job 允许嵌套 assign（spawn spike s6 实测）；
  上层 Job 若带 KILL_ON_JOB_CLOSE，其持有者结束会连带终止本树——这是
  detach 产品门的一部分，**不在本阶段声称已解决**；
- 未做跨 build 长时间稳定性验证。
"""

from __future__ import annotations

import ctypes
import struct
import sys
import threading
import time
from ctypes import wintypes
from typing import Any, Iterable

from .identity import (
    WindowsApiUnavailable,
    close_handle_checked,
    kernel32,
)

__all__ = ["GuardQueryError", "JobObjectGuard", "MAX_TERMINATE_TREE_TIMEOUT"]

# --------------------------------------------------------------------------- constants
JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x00000800
JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK = 0x00001000
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000

JobObjectBasicAccountingInformation = 1
JobObjectBasicProcessIdList = 3
JobObjectExtendedLimitInformation = 9

ERROR_MORE_DATA = 234
ERROR_ACCESS_DENIED = 5

#: ``terminate_tree`` 接受的最大终止预算（r3：guard 每个操作必须自身有界、
#: 快速返回；更大预算一律钳制，超时按“残留未知”fail-closed）。
MAX_TERMINATE_TREE_TIMEOUT = 30.0


class GuardQueryError(RuntimeError):
    """Job 查询失败：所有权/残留未知，调用方必须 fail-closed（不声称已死/已清）。"""


# --------------------------------------------------------------------------- structs
class _LARGE_INTEGER_UNION(ctypes.Union):
    _fields_ = [
        ("LowPart", wintypes.DWORD),
        ("HighPart", wintypes.LONG),
        ("QuadPart", ctypes.c_longlong),
    ]


class _LARGE_INTEGER(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("u", _LARGE_INTEGER_UNION)]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", _LARGE_INTEGER),
        ("PerJobUserTimeLimit", _LARGE_INTEGER),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_void_p),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", _LARGE_INTEGER),
        ("TotalKernelTime", _LARGE_INTEGER),
        ("ThisPeriodTotalUserTime", _LARGE_INTEGER),
        ("ThisPeriodTotalKernelTime", _LARGE_INTEGER),
        ("TotalPageFaultCount", wintypes.DWORD),
        ("TotalProcesses", wintypes.DWORD),
        ("ActiveProcesses", wintypes.DWORD),
        ("TotalTerminatedProcesses", wintypes.DWORD),
    ]


_bound = False


def _api() -> Any:
    global _bound
    k = kernel32()
    if not _bound:
        k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k.CreateJobObjectW.restype = wintypes.HANDLE
        k.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        k.SetInformationJobObject.restype = wintypes.BOOL
        k.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        k.QueryInformationJobObject.restype = wintypes.BOOL
        k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k.AssignProcessToJobObject.restype = wintypes.BOOL
        k.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k.TerminateJobObject.restype = wintypes.BOOL
        k.IsProcessInJob.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.BOOL),
        ]
        k.IsProcessInJob.restype = wintypes.BOOL
        _bound = True
    return k


class JobObjectGuard:
    """生产树守卫：自持 ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` Job 句柄。

    - 句柄生命周期由持有者（runner/backend）负责；``close()`` 关闭最后一个
      句柄时内核终止整树（这是硬杀宿主进程时的兜底清理）；
    - 所有方法在句柄已关闭后查询 -> :class:`GuardQueryError`（fail-closed）。
    """

    def __init__(self, name: str | None = None, *, kill_on_close: bool = True) -> None:
        if sys.platform != "win32":
            raise WindowsApiUnavailable(
                f"JobObjectGuard 仅支持 Windows（sys.platform={sys.platform!r}）"
            )
        k = _api()
        self._name = name
        self._handle = int(k.CreateJobObjectW(None, name) or 0)
        if not self._handle:
            raise OSError(
                f"CreateJobObjectW 失败（winerror={ctypes.get_last_error()}）"
            )
        self._kill_on_close = bool(kill_on_close)
        self._lock = threading.Lock()
        self._last_terminate_error: int | None = None
        limit_flags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE if kill_on_close else 0
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = limit_flags
        ok = k.SetInformationJobObject(
            wintypes.HANDLE(self._handle),
            JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            err = int(ctypes.get_last_error())
            close_handle_checked(self._handle)
            self._handle = 0
            raise OSError(f"SetInformationJobObject 失败（winerror={err}）")
        # 自证：KILL_ON_JOB_CLOSE 已置位，breakaway（两个位）均未置位。
        flags = self.limit_flags()
        if flags is None:
            err = int(ctypes.get_last_error())
            close_handle_checked(self._handle)
            self._handle = 0
            raise GuardQueryError(
                f"回读 Job LimitFlags 失败（winerror={err}）：无法自证守卫语义"
            )
        if kill_on_close and not (flags & JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE):
            close_handle_checked(self._handle)
            self._handle = 0
            raise RuntimeError(
                "Job LimitFlags 未包含 KILL_ON_JOB_CLOSE：守卫语义未生效，fail-closed"
            )
        if flags & (JOB_OBJECT_LIMIT_BREAKAWAY_OK | JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK):
            close_handle_checked(self._handle)
            self._handle = 0
            raise RuntimeError(
                "Job 允许 breakaway（BREAKAWAY_OK/SILENT_BREAKAWAY_OK 被置位）："
                "整树所有权不可靠，fail-closed"
            )
        self._limit_flags = flags

    # ------------------------------------------------------------- properties
    @property
    def raw_handle(self) -> int:
        """原始 Job 句柄（int）。句柄关闭后为 0。"""
        return self._handle

    @property
    def handle_open(self) -> bool:
        return bool(self._handle)

    @property
    def last_terminate_error(self) -> int | None:
        return self._last_terminate_error

    def describe(self) -> dict[str, Any]:
        return {
            "kind": "job-object",
            "os_level_guard": True,
            "kill_on_close": self._kill_on_close,
            "breakaway_allowed": False,
            "limit_flags": self._limit_flags,
            "handle_open": self.handle_open,
            "name": self._name,
            "note": (
                "内核级 Job（KILL_ON_JOB_CLOSE + 无 breakaway 位）；"
                "枚举走 QueryInformationJobObject，不按 PID 扫描、不裸 PID 杀"
            ),
        }

    # ------------------------------------------------------------- queries
    def limit_flags(self) -> int | None:
        k = _api()
        if not self._handle:
            return None
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        returned = wintypes.DWORD(0)
        ok = k.QueryInformationJobObject(
            wintypes.HANDLE(self._handle),
            JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
            ctypes.byref(returned),
        )
        return int(info.BasicLimitInformation.LimitFlags) if ok else None

    def active_processes(self) -> int | None:
        """Job 当前活跃成员数；无句柄或查询失败 -> ``None``（不编造数字）。"""
        k = _api()
        if not self._handle:
            return None
        info = _JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
        returned = wintypes.DWORD(0)
        ok = k.QueryInformationJobObject(
            wintypes.HANDLE(self._handle),
            JobObjectBasicAccountingInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
            ctypes.byref(returned),
        )
        return int(info.ActiveProcesses) if ok else None

    def member_pids(self) -> list[int]:
        """当前 Job 内**活跃**成员 PID 列表（整 Job 枚举）。

        失败/无句柄 -> :class:`GuardQueryError`（调用方 fail-closed）。
        PID 列表仅用于诊断与残留核对，**绝不**用于按 PID 终止。
        """
        k = _api()
        if not self._handle:
            raise GuardQueryError("Job 句柄已关闭：所有权未知（fail-closed）")
        ptr_size = ctypes.sizeof(ctypes.c_size_t)
        # JOBOBJECT_BASIC_PROCESS_ID_LIST = 2×DWORD + ULONG_PTR[]
        capacity = 64
        for _ in range(8):  # 有界扩容（64 -> 8192）
            size = 8 + ptr_size * capacity
            buf = ctypes.create_string_buffer(size)
            returned = wintypes.DWORD(0)
            ok = k.QueryInformationJobObject(
                wintypes.HANDLE(self._handle),
                JobObjectBasicProcessIdList,
                buf,
                size,
                ctypes.byref(returned),
            )
            if not ok:
                err = int(ctypes.get_last_error())
                if err == ERROR_MORE_DATA:
                    # 头部结构在 ERROR_MORE_DATA 下仍由内核填写（NumberOfAssignedProcesses）
                    assigned = struct.unpack_from("<I", buf.raw, 0)[0]
                    new_capacity = max(assigned, capacity * 2)
                    if new_capacity > 8192:
                        raise GuardQueryError(
                            f"Job 成员列表超过有界容量（assigned={assigned}）"
                        )
                    capacity = new_capacity
                    continue
                if err == 0:
                    # 极少数情况下缓冲区不足但不置 more-data：用稳定扩容重试
                    capacity = min(capacity * 2, 8192)
                    continue
                raise GuardQueryError(
                    f"QueryInformationJobObject(JobObjectBasicProcessIdList) "
                    f"失败（winerror={err}）：残留未知，fail-closed"
                )
            raw = buf.raw
            _assigned, count = struct.unpack_from("<II", raw, 0)
            pids = [
                int.from_bytes(
                    raw[8 + i * ptr_size : 8 + (i + 1) * ptr_size], "little"
                )
                for i in range(count)
            ]
            return pids
        raise GuardQueryError("Job 成员列表扩容重试耗尽：所有权未知（fail-closed）")

    # ------------------------------------------------------------- TreeGuard
    def owned_pids(self, root_pid: int | None) -> list[int]:
        """整 Job 成员（含根，若仍在 Job 内）。``root_pid`` 仅作诊断参照。"""
        return self.member_pids()

    def remaining(self, pids: Iterable[int]) -> list[int]:
        """残留核对：仍**活跃**于本 Job 的请求集子集；查询失败 -> 抛异常。"""
        members = set(self.member_pids())
        return sorted({int(p) for p in pids} & members)

    def terminate_tree(
        self, root_pid: int | None, *, timeout: float
    ) -> tuple[list[int], list[int]]:
        """先枚举 -> ``TerminateJobObject`` -> 查询 ``active==0`` 证明整树。

        - ``root_pid`` 仅用于诊断；终止只经 Job 句柄（根已死也能清孙进程）；
        - 返回 ``(owned_after, remaining_after)``；``remaining_after`` 为空 = 
          已证明整树无活跃成员；非空 = 未确认（调用方 fail-closed）；
        - 查询失败抛 :class:`GuardQueryError`；
        - **自身有界（r3）**：函数内只做单次内核调用 + 受 ``timeout`` 约束的
          轮询（钳制上限 :data:`MAX_TERMINATE_TREE_TIMEOUT`），无阻塞等待；
          到期即返回（active 非 0 => ``remaining_after`` 非空，fail-closed）。
        """
        budget = min(max(0.0, float(timeout)), MAX_TERMINATE_TREE_TIMEOUT)
        with self._lock:
            owned_after = self.member_pids()
            k = _api()
            if not self._handle:
                raise GuardQueryError("Job 句柄已关闭：无法终止整树（fail-closed）")
            ctypes.set_last_error(0)
            ok = bool(k.TerminateJobObject(wintypes.HANDLE(self._handle), 1))
            self._last_terminate_error = None if ok else int(ctypes.get_last_error())
            deadline = time.monotonic() + budget
            active: int | None = None
            while True:
                active = self.active_processes()
                if active == 0:
                    break
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.02)
            if active == 0:
                # 双证据：active==0 且 PID 列表为空（不一致即 fail-closed）。
                leftover = self.member_pids()
                if leftover:
                    raise GuardQueryError(
                        f"active=0 但 Job PID 列表非空 {leftover}：查询不一致，fail-closed"
                    )
                return owned_after, []
            if active is None:
                raise GuardQueryError(
                    "TerminateJobObject 后无法证明 active==0（查询失败）：整树未确认，fail-closed"
                )
            # 仍有活跃成员：给出最新诊断列表（仅诊断；调用方 fail-closed）。
            latest = self.member_pids()
            if not latest and active > 0:
                raise GuardQueryError(
                    f"active={active} 但 PID 列表为空：查询不一致，fail-closed"
                )
            return sorted(set(owned_after) | set(latest)), latest

    def close(self) -> bool:
        """关闭 Job 句柄（最后一个句柄 = 内核终止整树）。

        返回 ``True`` = 已关闭（含重复 close 幂等）；``False`` = ``CloseHandle``
        失败，**句柄保留**（可重试），不谎报已释放。
        """
        with self._lock:
            if not self._handle:
                return True
            if close_handle_checked(self._handle):
                self._handle = 0
                return True
            return False

    # ------------------------------------------------------------- spawn 辅助
    def assign(self, hprocess: int) -> None:
        """把进程 assign 进本 Job；失败抛 ``OSError``（调用方 fail-closed）。"""
        k = _api()
        if not self._handle:
            raise GuardQueryError("Job 句柄已关闭：无法 assign（fail-closed）")
        ctypes.set_last_error(0)
        ok = bool(
            k.AssignProcessToJobObject(
                wintypes.HANDLE(self._handle), wintypes.HANDLE(int(hprocess))
            )
        )
        if not ok:
            err = int(ctypes.get_last_error())
            raise OSError(
                f"AssignProcessToJobObject 失败（winerror={err}）：拒绝进入 running"
            )

    def is_member(self, hprocess: int) -> bool:
        """``IsProcessInJob(child, 本 Job 句柄)``；查询失败抛 ``OSError``。

        注意：``JobHandle`` 传 NULL 的官方语义是“是否在任意 Job 下”，不能用
        于门禁；这里始终传**本 guard 的句柄**（prove membership 的唯一形式）。
        """
        k = _api()
        if not self._handle:
            raise GuardQueryError("Job 句柄已关闭：无法证明成员关系（fail-closed）")
        in_job = wintypes.BOOL()
        ctypes.set_last_error(0)
        ok = k.IsProcessInJob(
            wintypes.HANDLE(int(hprocess)),
            wintypes.HANDLE(self._handle),
            ctypes.byref(in_job),
        )
        if not ok:
            raise OSError(
                f"IsProcessInJob 查询失败（winerror={ctypes.get_last_error()}）"
            )
        return bool(in_job.value)
