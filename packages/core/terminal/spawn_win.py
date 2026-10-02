"""ConPTY 原子 spawn：挂起创建 -> assign -> 证明成员 -> exactly-one resume（P1，TA-B）。

生产路径（与实施计划 §5.3、P0 接口文档 r2 对齐）：

    CreatePipe ×2
      -> CreatePseudoConsole(COORD(cols, rows), input_read, output_write)
      -> InitializeProcThreadAttributeList + UpdateProcThreadAttribute(
             PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE)
      -> CreateProcessW(CREATE_SUSPENDED | EXTENDED_STARTUPINFO_PRESENT)
      -> [官方步骤] 释放交给伪控制台的两个本进程句柄（input_read / output_write）
      -> 由**保留的** hProcess 读原始 64 位创建 FILETIME（精确身份）
      -> AssignProcessToJobObject(guard, child)      # 子进程从未执行过
      -> IsProcessInJob(child, guard) == True 证明成员；active_processes ≥ 1
      -> ResumeThread(primary thread) 门禁：**先前的挂起计数必须恰为 1**

原子性声明（精确表述）：从进程创建到 guard 入组之间，子进程主线程从未被调度，
不可能执行用户代码、不可能派生后代；assign 时子进程仍处挂起。Job 为
KILL_ON_JOB_CLOSE 且无 breakaway 位，其后代默认继承入组，单一句柄覆盖整树。

失败一律 fail-closed：不 resume、不返回 session、不发布 running；只终止
**本次自建**的子进程（经保留句柄），随后按官方顺序分阶段释放自有资源；
任何释放失败 -> 保留仍持有的资源并在 :class:`SpawnDenied.cleanup` 中如实列出，
可用 :meth:`SpawnDenied.retry_cleanup` 重试（不伪报 released）。

四证据门禁（真实原语产生，bool 不是伪填）：

- ``assigned``：``IsProcessInJob(child, guard)`` 返回 True 且 Job active ≥ 1；
- ``atomic_with_spawn``：``CREATE_SUSPENDED`` 创建、assign/证明在 resume 之前；
- ``identity``：由保留 hProcess 读取的 pid + raw64 FILETIME；
- ``handle_bound_for_cleanup``：清理所依赖的同一 hProcess 全程保留。

边界（如实声明）：

- ``ClosePseudoConsole`` 在 <26100 的旧 build 上可能等待客户端 drain；本模块
  已按官方建议**先关输出管道**再关伪控制台，并有界等待关闭 worker（超时则保留
  HPCON 可重试）；旧 build 行为**未真机验证**；
- 嵌套 Job / detach 宿主可行性不在本模块内证明（见实施计划 §5.1/§7）；
- 注入钩子（``assign_impl`` / ``membership_impl`` / ``resume_impl``）仅供测试
  构造**真实失败分支**（仍调用真实内核 API 的错误入参），不得用于伪造证据。
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from .contracts import (
    ProcessIdentity,
    ProcessOwnershipEvidence,
    ProcessStatus,
)
from .guard import GuardQueryError, JobObjectGuard
from .identity import (
    WindowsApiUnavailable,
    close_handle_checked,
    filetime_json,
    format_last_error,
    kernel32,
    read_creation_filetime,
    wait_state,
    win32_oserror,
)

__all__ = [
    "SpawnDenied",
    "SpawnEvidence",
    "ConPtySpawn",
    "spawn_conpty_suspended",
    "build_command_line",
    "build_environment_block",
]

# --------------------------------------------------------------------------- constants
CREATE_SUSPENDED = 0x00000004
CREATE_UNICODE_ENVIRONMENT = 0x00000400
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
STARTF_USESTDHANDLES = 0x00000100
PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016

WAIT_FAILED = 0xFFFFFFFF

HPCON = wintypes.HANDLE
MAX_DIMENSION = 32767


class _COORD(ctypes.Structure):
    _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]


class _STARTUPINFOW(ctypes.Structure):
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


class _STARTUPINFOEXW(ctypes.Structure):
    _fields_ = [("StartupInfo", _STARTUPINFOW), ("lpAttributeList", ctypes.c_void_p)]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


_bound = False


def _api() -> Any:
    global _bound
    k = kernel32()
    if not _bound:
        k.CreatePipe.argtypes = [
            ctypes.POINTER(wintypes.HANDLE),
            ctypes.POINTER(wintypes.HANDLE),
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        k.CreatePipe.restype = wintypes.BOOL
        k.CreatePseudoConsole.argtypes = [
            _COORD,
            wintypes.HANDLE,
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.POINTER(HPCON),
        ]
        k.CreatePseudoConsole.restype = ctypes.c_long  # HRESULT
        k.ResizePseudoConsole.argtypes = [HPCON, _COORD]
        k.ResizePseudoConsole.restype = ctypes.c_long  # HRESULT
        k.ClosePseudoConsole.argtypes = [HPCON]
        k.ClosePseudoConsole.restype = None
        k.InitializeProcThreadAttributeList.argtypes = [
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        k.InitializeProcThreadAttributeList.restype = wintypes.BOOL
        k.UpdateProcThreadAttribute.argtypes = [
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        k.UpdateProcThreadAttribute.restype = wintypes.BOOL
        k.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
        k.DeleteProcThreadAttributeList.restype = None
        k.CreateProcessW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPWSTR,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.BOOL,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.LPCWSTR,
            ctypes.c_void_p,
            ctypes.POINTER(_PROCESS_INFORMATION),
        ]
        k.CreateProcessW.restype = wintypes.BOOL
        k.ResumeThread.argtypes = [wintypes.HANDLE]
        k.ResumeThread.restype = wintypes.DWORD
        k.ReadFile.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        ]
        k.ReadFile.restype = wintypes.BOOL
        k.WriteFile.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        ]
        k.WriteFile.restype = wintypes.BOOL
        _bound = True
    return k


def _hresult_failed(hr: int) -> bool:
    return ctypes.c_long(int(hr)).value < 0


def _hresult_hex(hr: int) -> str:
    return f"0x{ctypes.c_ulong(int(hr)).value & 0xFFFFFFFF:08X}"


# --------------------------------------------------------------------------- builders
def build_command_line(argv: Sequence[str]) -> str:
    """argv -> Windows 命令行字符串（``subprocess.list2cmdline`` 的引号规则）。"""
    args = [str(a) for a in argv]
    if not args:
        raise ValueError("argv 不能为空：拒绝无命令的 spawn")
    return subprocess.list2cmdline(args)


def build_environment_block(env: Mapping[str, str] | None) -> str | None:
    """构造 ``CREATE_UNICODE_ENVIRONMENT`` 环境块；``None`` = 继承父环境。

    格式：``K=V\\0K=V\\0\\0``（不区分大小写排序，键值均为字符串）。
    """
    if env is None:
        return None
    entries = sorted(((str(k), str(v)) for k, v in env.items()), key=lambda kv: kv[0].lower())
    for key, _ in entries:
        if "=" in key or "\x00" in key:
            raise ValueError(f"非法环境变量名（含 '=' 或 NUL）：{key!r}")
    return "".join(f"{k}={v}\x00" for k, v in entries) + "\x00"


# --------------------------------------------------------------------------- evidence
@dataclass(frozen=True)
class SpawnEvidence:
    """原子 spawn 证据（四要素 + 上下文事实；JSON 安全，FILETIME 一律字符串）。

    与 :class:`ProcessOwnershipEvidence` 的关系：``ownership_evidence()`` 把
    本证据装配成门禁入参；四要素的 bool 只有真实原语成立时才为真：

    - ``assigned``：``IsProcessInJob(child, guard)==True`` 且 Job active ≥ 1；
    - ``atomic_with_spawn``：``CREATE_SUSPENDED`` + assign/证明在 resume 之前；
    - ``identity``：保留 hProcess 读出的 pid + raw64 FILETIME；
    - ``handle_bound_for_cleanup``：清理用同一 hProcess（保留至释放）。
    """

    pid: int
    identity: ProcessIdentity
    assigned: bool
    atomic_with_spawn: bool
    handle_bound_for_cleanup: bool
    guard_kind: str
    guard_detail: dict[str, Any]
    membership_proof: str
    suspended_created: bool
    resume_previous_suspend_count: int | None
    resumed: bool
    timings: dict[str, float] = field(default_factory=dict)
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "identity": {
                "pid": self.identity.pid,
                "created_at_filetime": filetime_json(self.identity.created_at_filetime),
            },
            "assigned": self.assigned,
            "atomic_with_spawn": self.atomic_with_spawn,
            "handle_bound_for_cleanup": self.handle_bound_for_cleanup,
            "guard_kind": self.guard_kind,
            "guard_detail": dict(self.guard_detail),
            "membership_proof": self.membership_proof,
            "suspended_created": self.suspended_created,
            "resume_previous_suspend_count": self.resume_previous_suspend_count,
            "resumed": self.resumed,
            "timings": dict(self.timings),
            "detail": self.detail,
        }

    def ownership_evidence(self) -> ProcessOwnershipEvidence:
        return ProcessOwnershipEvidence(
            assigned=self.assigned,
            atomic_with_spawn=self.atomic_with_spawn,
            identity=self.identity,
            handle_bound_for_cleanup=self.handle_bound_for_cleanup,
            guard=self.guard_kind,
            detail=self.detail,
        )


# --------------------------------------------------------------------------- errors
class SpawnDenied(RuntimeError):
    """Fail-closed 启动拒绝：子进程从未 resume（或异常 resume 已被清理门禁捕获）。

    ``cleanup`` 是失败路径的清理报告（``released`` / ``retained`` / ``errors``）；
    ``retained`` 非空表示仍有自有资源被保留——调用方可用
    :meth:`retry_cleanup` 重试，而不是假设已清理干净。
    """

    def __init__(
        self,
        stage: str,
        detail: dict[str, Any],
        *,
        cleanup: dict[str, Any] | None = None,
        attempt: "ConPtySpawn | None" = None,
    ) -> None:
        super().__init__(f"atomic spawn denied at stage={stage}: {detail}")
        self.stage = stage
        self.detail = dict(detail)
        self.cleanup = cleanup
        self.attempt = attempt

    @property
    def retryable(self) -> bool:
        if self.cleanup is None:
            return False
        return bool(self.cleanup.get("retained"))

    def retry_cleanup(self) -> dict[str, Any]:
        """对同一 attempt 重试清理（不重叠、幂等；已释放的资源不再重复释放）。"""
        if self.attempt is None:
            return {"closed": True, "retryable": False, "note": "no attempt object"}
        report = self.attempt.cleanup()
        self.cleanup = report
        return report

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "detail": _json_safe(self.detail),
            "cleanup": _json_safe(self.cleanup),
            "retryable": self.retryable,
        }


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, int) and not isinstance(value, bool) and abs(value) > 2**53:
        return str(value)  # raw64（如 FILETIME）跨 JS 边界一律字符串
    return value


# --------------------------------------------------------------------------- session
class ConPtySpawn:
    """一次原子 spawn 的对象与资源属主（低层原语；并发/取消归 backend）。

    资源归属（全部显式，逐一释放并检查返回值）：

    - ``input_write``：本进程写入 ConPTY 输入的唯一句柄；
    - ``output_read``：本进程读取 ConPTY 输出的唯一句柄；
    - ``input_read`` / ``output_write``：交给 ``CreatePseudoConsole`` 后按官方
      步骤在 ``CreateProcessW`` 成功后立即释放（失败则保留可重试）；
    - ``hpc``：伪控制台（HPCON）；
    - ``attr_buf``：PROC_THREAD_ATTRIBUTE 列表内存；
    - ``h_process``：**保留**的进程句柄（身份 + 终止 + 清理绑定，最后释放）；
    - ``h_thread``：主线程句柄（resume 后释放；失败保留可重试）。
    """

    def __init__(self, guard: JobObjectGuard | None = None) -> None:
        self.guard = guard
        self.pid: int = 0
        self.identity: ProcessIdentity | None = None
        self.evidence: SpawnEvidence | None = None
        self.resumed = False
        self.resume_detail: dict[str, Any] = {}
        self.terminated = False
        self.terminate_error: str | None = None
        self.death_confirmed = False
        self.input_read = 0
        self.input_write = 0
        self.output_read = 0
        self.output_write = 0
        self.hpc = 0
        self.h_process = 0
        self.h_thread = 0
        self.release_notes: list[str] = []
        self.timings: dict[str, float] = {}
        self._attr_buf: Any | None = None
        self._pty_close_thread: threading.Thread | None = None
        self._pty_closed_event = threading.Event()
        self._pty_close_error: str | None = None
        self._release_lock = threading.Lock()
        self._closed = False
        # 测试专用释放失败注入（包装层模拟；见 inject_release_failure）。
        self._injected_failures: list[str] = []

    # ------------------------------------------------------------- lifecycle
    @property
    def closed(self) -> bool:
        return self._closed

    def resume(self) -> tuple[bool, int | None]:
        """公共 resume（与 spawn 内同一门禁）：先前挂起计数恰为 1 才算恢复。

        重复调用以 ``already_resumed`` 拒绝且不改写记录；``0`` / ``>1`` /
        ``WAIT_FAILED`` 一律拒绝（fail-closed）。
        """
        if self.resumed:
            self.resume_detail = {
                "ok": False,
                "reason": "already_resumed",
                "currently_resumed": True,
                "previous_suspend_count": None,
            }
            return False, None
        if not self.h_thread:
            self.resume_detail = {
                "ok": False,
                "reason": "no_thread_handle",
                "currently_resumed": False,
                "previous_suspend_count": None,
            }
            return False, None
        prev = int(_api().ResumeThread(wintypes.HANDLE(self.h_thread)))
        if prev == WAIT_FAILED:
            self.resume_detail = {
                "ok": False,
                "reason": "resume_call_failed",
                "last_error": int(ctypes.get_last_error()),
                "currently_resumed": False,
                "previous_suspend_count": None,
            }
            return False, None
        if prev != 1:
            self.resume_detail = {
                "ok": False,
                "reason": "unexpected_previous_suspend_count",
                "expected_previous_suspend_count": 1,
                "previous_suspend_count": prev,
                "currently_resumed": False,
            }
            return False, None
        self.resumed = True
        self.resume_detail = {
            "ok": True,
            "reason": "resumed",
            "previous_suspend_count": 1,
            "currently_resumed": True,
        }
        return True, 1

    # ------------------------------------------------------------- raw ops
    def read_raw(self, size: int) -> bytes:
        """一次同步 ``ReadFile``（阻塞直到数据/断链）；返回原始字节。

        失败抛 ``OSError``（含 ``winerror``）；**不做**任何 EOF 语义映射
        （EOF 判定归 backend，见其 read 契约）。
        """
        if size <= 0:
            raise ValueError("read size 必须 > 0")
        if not self.output_read:
            raise OSError("[WinError 6] output_read 句柄已释放")
        buf = ctypes.create_string_buffer(int(size))
        read = wintypes.DWORD(0)
        ctypes.set_last_error(0)
        ok = _api().ReadFile(
            wintypes.HANDLE(self.output_read),
            buf,
            int(size),
            ctypes.byref(read),
            None,
        )
        if not ok:
            err = int(ctypes.get_last_error())
            raise win32_oserror(err, f"ReadFile failed ({format_last_error(err)})")
        return buf.raw[: read.value]

    def write_raw(self, data: bytes) -> int:
        """一次同步 ``WriteFile``；返回本次实际写入字节数（可能 partial）。"""
        if not self.input_write:
            raise OSError("[WinError 6] input_write 句柄已释放")
        if not data:
            return 0
        buf = ctypes.create_string_buffer(bytes(data), len(data))
        written = wintypes.DWORD(0)
        ctypes.set_last_error(0)
        ok = _api().WriteFile(
            wintypes.HANDLE(self.input_write),
            buf,
            len(data),
            ctypes.byref(written),
            None,
        )
        if not ok:
            err = int(ctypes.get_last_error())
            raise win32_oserror(err, f"WriteFile failed ({format_last_error(err)})")
        return int(written.value)

    def resize_raw(self, rows: int, cols: int) -> None:
        """``ResizePseudoConsole`` 到实际子尺寸；HRESULT 失败抛 ``OSError``。"""
        if not self.hpc:
            raise OSError("[WinError 6] 伪控制台句柄已释放")
        rows, cols = int(rows), int(cols)
        if not (1 <= rows <= MAX_DIMENSION and 1 <= cols <= MAX_DIMENSION):
            raise ValueError(f"尺寸越界：rows={rows} cols={cols}")
        hr = _api().ResizePseudoConsole(HPCON(self.hpc), _COORD(cols, rows))
        if _hresult_failed(hr):
            raise OSError(
                f"ResizePseudoConsole 失败（hr={_hresult_hex(hr)}；rows={rows} cols={cols}）"
            )

    def terminate_raw(self, exit_code: int = 0xDEAD) -> bool:
        """对**保留的** hProcess 调 ``TerminateProcess``（只杀本次自建根进程）。

        返回 True = 内核接受终止请求；False = 调用失败（错误记入
        ``terminate_error``）。不做整树（整树 = guard.terminate_tree）。
        """
        if not self.h_process:
            self.terminate_error = "no_process_handle"
            return False
        ctypes.set_last_error(0)
        ok = bool(_api().TerminateProcess(wintypes.HANDLE(self.h_process), int(exit_code)))
        if ok:
            self.terminated = True
        else:
            self.terminate_error = format_last_error()
        return ok

    def wait_dead(self, timeout: float) -> bool:
        """在有界预算内等待保留句柄 signaled；``True`` = 已确认退出。"""
        deadline = time.monotonic() + max(0.0, float(timeout))
        while True:
            if wait_state(self.h_process) is ProcessStatus.DEAD:
                self.death_confirmed = True
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.02)

    # ------------------------------------------------------------- cleanup
    def close_pseudo_console(self, timeout: float = 2.0) -> bool:
        """显式关闭伪控制台（**不**释放其他资源；``close()`` 内部循此路径）。

        用途：关闭 HPCON 会让输出管道断链（真实 EOF）；本机实测子进程自然退出
        不会产生 EOF，只有伪控制台关闭才断链。测试用它验证 EOF 语义；宿主一般
        不需要直接调用（``close()`` 会按官方顺序处理）。
        """
        ok, _note = self._release_pseudo_console(max(0.0, float(timeout)))
        return ok

    def inject_release_failure(self, resource: str) -> None:
        """**测试专用**：让指定资源的下一次释放走失败分支（保留句柄、可重试）。

        这是包装层模拟，**不是**真实 OS 失败；用于验证“部分失败 -> 保留
        owned 资源 -> 重试成功”的清理语义（真实失败分支由 CloseHandle 返回值
        检查直接覆盖）。
        """
        self._injected_failures.append(str(resource))

    def cleanup(self) -> dict[str, Any]:
        """失败路径清理：终止自建子进程 -> guard 整树清扫 -> 分阶段释放。

        不覆盖任何“未确认”的状态：进程未确认死亡时**不**释放 hProcess
        （保留清理绑定），资源释放失败时保留其余资源并在报告中列出。
        """
        out: dict[str, Any] = {}
        if self.h_process:
            if wait_state(self.h_process) is ProcessStatus.DEAD:
                out["terminate"] = "already-dead"
            else:
                ok = self.terminate_raw(0xDEAD)
                dead = self.wait_dead(2.0) if ok else False
                out["terminate"] = {
                    "requested": ok,
                    "confirmed_dead": dead,
                    "error": self.terminate_error,
                }
        else:
            out["terminate"] = "no-process"
        if self.guard is not None:
            try:
                owned, remaining = self.guard.terminate_tree(self.pid or None, timeout=1.0)
                out["guard_terminate"] = {"owned": owned, "remaining": remaining}
            except (GuardQueryError, OSError) as exc:
                out["guard_terminate_error"] = f"{type(exc).__name__}: {exc}"
        out["release"] = self.staged_release(require_dead=True)
        return out

    def staged_release(
        self, *, require_dead: bool = True, pty_close_timeout: float = 2.0
    ) -> dict[str, Any]:
        """分阶段释放自有资源（官方顺序）；任何失败都保留其余并如实报告。

        顺序：output_read -> ClosePseudoConsole -> 属性表 -> input_write ->
        其余管道句柄 -> 线程句柄 -> **最后** h_process。

        ``require_dead=True`` 时进程未确认退出 -> 拒绝释放（保留 hProcess 与
        其余资源，可重试）；重复调用幂等；失败后重试只做未释放的步骤。
        """
        started = time.perf_counter()
        report: dict[str, Any] = {
            "closed": False,
            "retryable": True,
            "order": [
                "output_read",
                "pseudo_console",
                "attr_list",
                "input_write",
                "leftover_pipes",
                "thread_handle",
                "process_handle",
            ],
            "released": [],
            "retained": [],
            "errors": [],
            "seconds": 0.0,
            "notes": list(self.release_notes),
        }
        with self._release_lock:
            if self._closed:
                report.update(closed=True, retryable=False, retained=[], notes=list(self.release_notes))
                report["seconds"] = round(time.perf_counter() - started, 4)
                return report
            if require_dead and self.h_process:
                if wait_state(self.h_process) is not ProcessStatus.DEAD:
                    report["errors"].append("process_not_confirmed_dead")
                    report["retained"] = self.retained_resources()
                    report["seconds"] = round(time.perf_counter() - started, 4)
                    return report
                self.death_confirmed = True

            # 1) 输出管道（官方建议：先关输出再关伪控制台）
            if not self._release_close_slot("output_read", report):
                report["retained"] = self.retained_resources()
                report["seconds"] = round(time.perf_counter() - started, 4)
                return report
            # 2) 伪控制台（有界 worker：旧 build 阻塞不拖死调用方）
            ok, note = self._release_pseudo_console(pty_close_timeout)
            if not ok:
                report["errors"].append(note or "pseudo_console_close_failed")
                report["retained"] = self.retained_resources()
                report["seconds"] = round(time.perf_counter() - started, 4)
                return report
            report["released"].append("pseudo_console")
            # 3) 属性表
            if not self._release_attr_list(report):
                report["retained"] = self.retained_resources()
                report["seconds"] = round(time.perf_counter() - started, 4)
                return report
            # 4) 输入写端（调用方必须已保证 writer 收敛）
            if not self._release_close_slot("input_write", report):
                report["retained"] = self.retained_resources()
                report["seconds"] = round(time.perf_counter() - started, 4)
                return report
            # 5) 其余管道句柄（正常 spawn 已释放；失败路径可能仍有）
            for name in ("input_read", "output_write"):
                if not self._release_close_slot(name, report):
                    report["retained"] = self.retained_resources()
                    report["seconds"] = round(time.perf_counter() - started, 4)
                    return report
            report["released"].append("leftover_pipes")
            # 6) 线程句柄
            if not self._release_close_slot("h_thread", report):
                report["retained"] = self.retained_resources()
                report["seconds"] = round(time.perf_counter() - started, 4)
                return report
            # 7) 进程句柄（身份/终止绑定的最后释放）
            if not self._release_close_slot("h_process", report):
                report["retained"] = self.retained_resources()
                report["seconds"] = round(time.perf_counter() - started, 4)
                return report
            self._closed = True
            report.update(closed=True, retryable=False, retained=[], notes=list(self.release_notes))
            report["seconds"] = round(time.perf_counter() - started, 4)
            return report

    def _release_close_slot(self, name: str, report: dict[str, Any]) -> bool:
        handle = getattr(self, name)
        if not handle:
            setattr(self, name, 0)
            return True
        if name in self._injected_failures:
            self._injected_failures.remove(name)
            note = f"injected-release-failure({name})：包装层模拟，句柄保留可重试"
            self.release_notes.append(note)
            report["errors"].append(note)
            return False
        if close_handle_checked(int(handle)):
            setattr(self, name, 0)
            report["released"].append(name)
            return True
        note = f"CloseHandle({name}) 失败（{format_last_error()}）：句柄保留可重试"
        self.release_notes.append(note)
        report["errors"].append(note)
        return False

    def _release_pseudo_console(self, timeout: float) -> tuple[bool, str | None]:
        if not self.hpc:
            return True, None
        if "pseudo_console" in self._injected_failures:
            self._injected_failures.remove("pseudo_console")
            note = "injected-release-failure(pseudo_console)：包装层模拟，HPCON 保留可重试"
            self.release_notes.append(note)
            return False, note
        if self._pty_close_thread is None:
            def _work() -> None:
                try:
                    _api().ClosePseudoConsole(HPCON(self.hpc))
                except Exception as exc:  # noqa: BLE001 - void API 的唯一失败面
                    self._pty_close_error = f"{type(exc).__name__}: {exc}"
                finally:
                    self._pty_closed_event.set()

            thread = threading.Thread(
                target=_work, name="conpty-close-hpc", daemon=True
            )
            self._pty_close_thread = thread
            thread.start()
        if not self._pty_closed_event.wait(max(0.0, float(timeout))):
            # 旧 build 的“等待客户端 drain”行为（未真机验证）：有界策略=
            # 超时即保留 HPCON，交由调用方重试；不谎报已关闭。
            note = "pseudo_console_close_timeout（旧 build 阻塞边界，HPCON 保留可重试）"
            self.release_notes.append(note)
            return False, note
        if self._pty_close_error:
            note = f"ClosePseudoConsole 异常：{self._pty_close_error}（HPCON 保留可重试）"
            self.release_notes.append(note)
            return False, note
        self.hpc = 0
        return True, None

    def _release_attr_list(self, report: dict[str, Any]) -> bool:
        if self._attr_buf is None:
            return True
        if "attr_list" in self._injected_failures:
            self._injected_failures.remove("attr_list")
            note = "injected-release-failure(attr_list)：包装层模拟，保留可重试"
            self.release_notes.append(note)
            report["errors"].append(note)
            return False
        try:
            _api().DeleteProcThreadAttributeList(self._attr_buf)
        except Exception as exc:  # noqa: BLE001
            note = f"DeleteProcThreadAttributeList 异常：{type(exc).__name__}: {exc}"
            self.release_notes.append(note)
            report["errors"].append(note)
            return False
        self._attr_buf = None
        report["released"].append("attr_list")
        return True

    def retained_resources(self) -> list[str]:
        names = []
        for name in (
            "input_read",
            "input_write",
            "output_read",
            "output_write",
            "hpc",
            "h_process",
            "h_thread",
        ):
            if getattr(self, name):
                names.append(name)
        if self._attr_buf is not None:
            names.append("attr_list")
        if self._pty_close_thread is not None and not self._pty_closed_event.is_set():
            names.append("pty_close_worker")
        return names


# --------------------------------------------------------------------------- spawn
def spawn_conpty_suspended(
    argv: Sequence[str],
    cwd: str | None = None,
    env: Mapping[str, str] | None = None,
    rows: int = 24,
    cols: int = 80,
    guard: JobObjectGuard | None = None,
    *,
    resume: bool = True,
    assign_impl: Callable[[JobObjectGuard, int], None] | None = None,
    membership_impl: Callable[[JobObjectGuard, int], bool] | None = None,
    resume_impl: Callable[[int], int] | None = None,
) -> ConPtySpawn:
    """挂起创建 -> 入 guard -> 证明成员 -> exactly-one resume；失败 fail-closed。

    ``assign_impl`` / ``membership_impl`` / ``resume_impl`` 是**测试专用**注入；
    注入实现仍应调用真实内核 API（用错误入参构造真实失败），不得用于伪造证据。
    全部通过才返回 :class:`ConPtySpawn`（含 :class:`SpawnEvidence`）。

    ``resume=False``（诊断/门禁用）返回仍处挂起的会话；之后必须经
    :meth:`ConPtySpawn.resume`（同一门禁）恢复。
    """
    if sys.platform != "win32":
        raise WindowsApiUnavailable(
            f"spawn_conpty_suspended 仅支持 Windows（sys.platform={sys.platform!r}）"
        )
    if not (1 <= int(rows) <= MAX_DIMENSION and 1 <= int(cols) <= MAX_DIMENSION):
        raise ValueError(f"尺寸越界：rows={rows} cols={cols}")
    command_line = build_command_line(argv)
    env_block = build_environment_block(env)
    guarded = guard if guard is not None else JobObjectGuard()
    owns_guard = guard is None
    attempt = ConPtySpawn(guarded)
    k = _api()
    stage = "create_pipes"
    t_start = time.perf_counter()
    timings: dict[str, float] = {}

    def _deny(detail: dict[str, Any]) -> SpawnDenied:
        detail = dict(detail)
        detail.setdefault("argv0", str(argv[0]) if argv else None)
        detail["stage_timings"] = dict(timings)
        cleanup = attempt.cleanup()
        if owns_guard and guarded.handle_open:
            # 自建 guard：清扫后关闭句柄（kill-on-close 兜底），失败如实记录。
            detail["guard_closed"] = guarded.close()
        return SpawnDenied(stage, detail, cleanup=cleanup, attempt=attempt)

    try:
        # 1) 两条同步通信管道（后续 I/O 由 backend 在自己的线程上服务）
        r_in, w_in = wintypes.HANDLE(), wintypes.HANDLE()
        r_out, w_out = wintypes.HANDLE(), wintypes.HANDLE()
        if not k.CreatePipe(ctypes.byref(r_in), ctypes.byref(w_in), None, 0):
            raise OSError(
                f"CreatePipe(input) 失败（{format_last_error()}）"
            )
        if not k.CreatePipe(ctypes.byref(r_out), ctypes.byref(w_out), None, 0):
            close_handle_checked(int(r_in.value))
            close_handle_checked(int(w_in.value))
            raise OSError(
                f"CreatePipe(output) 失败（{format_last_error()}）"
            )
        attempt.input_read = int(r_in.value)
        attempt.input_write = int(w_in.value)
        attempt.output_read = int(r_out.value)
        attempt.output_write = int(w_out.value)
        timings["create_pipes_ms"] = round((time.perf_counter() - t_start) * 1000, 3)

        # 2) 伪控制台
        stage = "create_pseudo_console"
        t0 = time.perf_counter()
        hpc = HPCON()
        hr = k.CreatePseudoConsole(
            _COORD(int(cols), int(rows)),
            wintypes.HANDLE(attempt.input_read),
            wintypes.HANDLE(attempt.output_write),
            0,
            ctypes.byref(hpc),
        )
        if _hresult_failed(hr):
            raise OSError(
                f"CreatePseudoConsole 失败（hr={_hresult_hex(hr)}）"
            )
        attempt.hpc = int(hpc.value)
        timings["create_pseudo_console_ms"] = round(
            (time.perf_counter() - t0) * 1000, 3
        )

        # 3) 属性表：PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE
        stage = "attribute_list"
        size = ctypes.c_size_t(0)
        k.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
        if not size.value:
            raise OSError(
                f"InitializeProcThreadAttributeList(size) 失败（{format_last_error()}）"
            )
        attr_buf = ctypes.create_string_buffer(size.value)
        if not k.InitializeProcThreadAttributeList(
            attr_buf, 1, 0, ctypes.byref(size)
        ):
            raise OSError(
                f"InitializeProcThreadAttributeList 失败（{format_last_error()}）"
            )
        attempt._attr_buf = attr_buf
        if not k.UpdateProcThreadAttribute(
            attr_buf,
            0,
            ctypes.c_void_p(PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE),
            ctypes.c_void_p(attempt.hpc),
            ctypes.sizeof(HPCON),
            None,
            None,
        ):
            raise OSError(
                f"UpdateProcThreadAttribute(PSEUDOCONSOLE) 失败（{format_last_error()}）"
            )

        si = _STARTUPINFOEXW()
        si.StartupInfo.cb = ctypes.sizeof(_STARTUPINFOEXW)
        si.lpAttributeList = ctypes.cast(attr_buf, ctypes.c_void_p)
        # 本机 s11 矩阵（spawn 报告）：只设 PSEUDOCONSOLE 属性时子进程 stdout
        # 仍指向父进程句柄；STARTF_USESTDHANDLES + NULL 使子进程拿到 pty 自身
        # 的控制台句柄（范围限本机/build，见报告 §3 s11）。
        si.StartupInfo.dwFlags = STARTF_USESTDHANDLES
        si.StartupInfo.hStdInput = None
        si.StartupInfo.hStdOutput = None
        si.StartupInfo.hStdError = None
        pi = _PROCESS_INFORMATION()

        # 4) 挂起创建：子进程从未执行
        stage = "create_process_suspended"
        t0 = time.perf_counter()
        cmd_buf = ctypes.create_unicode_buffer(command_line)
        env_ptr: Any = None
        env_buf = None
        if env_block is not None:
            env_buf = ctypes.create_unicode_buffer(env_block)
            env_ptr = ctypes.cast(env_buf, ctypes.c_void_p)
        flags = (
            CREATE_SUSPENDED
            | EXTENDED_STARTUPINFO_PRESENT
            | CREATE_UNICODE_ENVIRONMENT
        )
        if not k.CreateProcessW(
            None,
            cmd_buf,
            None,
            None,
            False,
            flags,
            env_ptr,
            cwd or None,
            ctypes.byref(si),
            ctypes.byref(pi),
        ):
            raise OSError(f"CreateProcessW 失败（{format_last_error()}）")
        attempt.h_process = int(pi.hProcess)
        attempt.h_thread = int(pi.hThread)
        attempt.pid = int(pi.dwProcessId)
        timings["create_process_ms"] = round((time.perf_counter() - t0) * 1000, 3)

        # 5) 官方步骤：CreateProcess 成功后释放交给伪控制台的本进程句柄，
        #    否则底层设备对象引用计数偏高，断链检测（EOF）可能不出现。
        #    失败不否认 spawn 事实，但句柄保留在 slot 中可重试释放。
        for name in ("input_read", "output_write"):
            handle = getattr(attempt, name)
            if handle:
                if close_handle_checked(handle):
                    setattr(attempt, name, 0)
                else:
                    attempt.release_notes.append(
                        f"post-CreateProcess 释放 {name} 失败（{format_last_error()}）：保留待重试"
                    )

        # 6) 身份（保留 handle 读取；绝不按 PID 重开）
        stage = "read_identity"
        creation_ft = read_creation_filetime(attempt.h_process)
        if creation_ft is None:
            raise OSError(
                f"GetProcessTimes 读取创建时间失败（{format_last_error()}）："
                "无精确身份即拒绝"
            )
        attempt.identity = ProcessIdentity(
            pid=attempt.pid, created_at_filetime=creation_ft
        )

        # 7) assign（子进程仍处挂起 = 原子性步骤）
        stage = "assign_guard_job"
        t0 = time.perf_counter()
        if assign_impl is not None:
            assign_impl(guarded, attempt.h_process)
        else:
            guarded.assign(attempt.h_process)
        timings["assign_ms"] = round((time.perf_counter() - t0) * 1000, 3)

        # 8) 证明成员（查询失败/报 False 都视为未证明 -> fail-closed）
        stage = "verify_guard_membership"
        membership_ok = False
        membership_note = ""
        try:
            if membership_impl is not None:
                membership_ok = bool(membership_impl(guarded, attempt.h_process))
            else:
                membership_ok = bool(guarded.is_member(attempt.h_process))
            if not membership_ok:
                membership_note = "IsProcessInJob 返回 False：成员关系未证明"
        except OSError as exc:
            membership_note = f"成员关系查询失败：{exc}"
        active = guarded.active_processes()
        if membership_ok and active is None:
            membership_ok = False
            membership_note = "Job active_processes 查询失败：assigned 无法证明"
        elif membership_ok and active < 1:
            membership_ok = False
            membership_note = f"Job active_processes={active}：成员未确认"
        if not membership_ok:
            raise _MembershipDenial(membership_note)

        # 9) resume 门禁：先前挂起计数必须恰为 1
        prev: int | None = None
        if resume:
            stage = "resume_thread"
            if resume_impl is not None:
                raw = int(resume_impl(attempt.h_thread))
            else:
                raw = int(k.ResumeThread(wintypes.HANDLE(attempt.h_thread)))
            if raw == WAIT_FAILED:
                raise _ResumeDenial("resume_call_failed", {
                    "last_error": int(ctypes.get_last_error()),
                })
            if raw != 1:
                raise _ResumeDenial("unexpected_previous_suspend_count", {
                    "resume_return": raw,
                    "expected_previous_suspend_count": 1,
                    "note": "0=未挂起(原子性声明失效) / >1=仍挂起；均拒绝并清理",
                })
            prev = raw
            attempt.resumed = True

        # 10) 证据（真实原语 -> 四要素）
        membership_proof = (
            f"IsProcessInJob(child, guard)==True; active_processes={active}; "
            f"resumed={'yes(prev=1)' if attempt.resumed else 'no'}"
        )
        evidence = SpawnEvidence(
            pid=attempt.pid,
            identity=attempt.identity,
            assigned=True,
            atomic_with_spawn=True,
            handle_bound_for_cleanup=True,
            guard_kind=str(guarded.describe().get("kind", "job-object")),
            guard_detail=guarded.describe(),
            membership_proof=membership_proof,
            suspended_created=True,
            resume_previous_suspend_count=prev,
            resumed=attempt.resumed,
            timings=dict(timings),
            detail=(
                "CREATE_SUSPENDED -> assign(guard) -> IsProcessInJob 证明 -> "
                "ResumeThread(prev==1)；身份由保留句柄读取（raw64 FILETIME）"
            ),
        )
        attempt.evidence = evidence
        attempt.timings = dict(timings)
        return attempt

    except _MembershipDenial as denied:
        raise _deny({
            "membership_error": denied.detail,
            "resumed": False,
            "note": "成员关系未证明：fail-closed，子进程从未 resume",
        }) from denied
    except _ResumeDenial as denied:
        raise _deny({
            "resume_error": denied.reason,
            **denied.detail,
            "note": "resume 门禁拒绝：按 fail-closed 清理本次自建资源",
        }) from denied
    except SpawnDenied:
        raise
    except Exception as exc:  # noqa: BLE001 - 任何未预期失败都必须 fail-closed
        raise _deny({
            "exception": f"{type(exc).__name__}: {exc}",
            "resumed": False,
        }) from exc


class _MembershipDenial(Exception):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class _ResumeDenial(Exception):
    def __init__(self, reason: str, detail: dict[str, Any]) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail
