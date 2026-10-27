"""POSIX 进程所有权实现（进程组 + 持久身份）。

与 :mod:`packages.core.platform.process` 共用同一 ``ProcessOwner`` 契约：

- **spawn**：``subprocess.Popen(..., start_new_session=True)``（等价 ``setsid()``），
  子进程成为新 session/新进程组的 leader，``pgid == pid``；前台 Ctrl+C 的
  ``SIGINT`` 不会再打到它。
- **终止**：对**进程组**发信号（``killpg``）：``SIGTERM`` → ``grace`` 宽限 →
  ``SIGKILL``；残留用 ``killpg(pgid, 0)`` 核对（不按 PID 裸杀）。
- **身份**：Linux 使用 /proc starttime + boot_id；Darwin 使用一次原生 libproc
  查询的 uniqueid/idversion/start timeval + boot session UUID。ps 秒级 lstart 仅供诊断。
- **崩溃后回收**：``recoverable_identity`` 产出 JSON 安全记录；``verify`` 重新
  校验；``recover`` 只在“leader 身份成立且存活”时按**记录里校验过的 pgid**
  终止。leader 已退出时**拒绝**按 pgid 回收——此时无法证明该 pgid 仍属于本树，
  盲杀可能命中被复用的进程组（fail-closed）。

硬约束：

- 本模块在 Windows 上**可导入**（顶层不用 ``os.killpg`` / ``signal.SIGKILL``）；
  构造 :class:`PosixProcessOwner` 或调用任何平台方法抛
  :class:`UnsupportedPlatformError`，不是导入期崩溃。
- 无伪造证据：``DEAD`` 只来自“进程不存在 / 僵尸 / 已 wait 回收”，查不到一律
  ``UNKNOWN``。

测试缝（**不是**产品分支）：``_killpg`` / ``_group_exists`` / ``_read_identity``
是三个薄封装，测试可替换它们在非 POSIX 主机上验证控制流（信号升级、身份拒绝）；
生产路径直接调用真实系统调用。
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
import weakref
from pathlib import Path
from typing import Any, Mapping, Sequence

from packages.core.platform.process import (
    PLATFORM_POSIX,
    IdentityCheck,
    IdentityMismatchError,
    OwnedProcess,
    OwnershipUnknownError,
    ProcessIdentity,
    ProcessOwner,
    ProcessStatus,
    RecoveryResult,
    TerminateResult,
    TreeTerminationResult,
    UnsupportedPlatformError,
    WaitResult,
)

__all__ = [
    "PosixOwnedProcess",
    "PosixProcessOwner",
    "parse_proc_stat",
    "read_boot_id",
    "read_linux_identity",
    "read_ps_identity",
    "read_posix_identity",
]

#: SIGTERM 之后的强杀确认预算上界（有界，避免调用方被卡死）。
MAX_KILL_CONFIRM_TIMEOUT = 5.0
#: 轮询间隔（与 guard / panel_builds 的现状一致）。
POLL_INTERVAL = 0.02

_RETAINED = weakref.WeakValueDictionary()
_RETAINED_LOCK = threading.Lock()

_BOOT_ID: str | None = None
_BOOT_ID_READ = False
_BOOT_ID_LOCK = threading.Lock()


# --------------------------------------------------------------------------- platform guards
def _require_posix() -> None:
    if os.name != "posix":
        raise UnsupportedPlatformError(
            f"PosixProcessOwner 仅支持 POSIX（os.name={os.name!r}）"
        )


def _sigterm() -> int:
    return int(signal.SIGTERM)


def _sigkill() -> int:
    """Windows 没有 SIGKILL；非 POSIX 调用前已被 ``_require_posix`` 拦下。"""
    return int(getattr(signal, "SIGKILL", signal.SIGTERM))


def _signal_name(sig: int) -> str:
    try:
        return signal.Signals(int(sig)).name
    except (ValueError, AttributeError):  # pragma: no cover - 非标准信号
        return f"signal-{int(sig)}"


# --------------------------------------------------------------------------- syscall seams
def _killpg(pgid: int, sig: int) -> None:
    """对进程组发信号（薄封装；测试可替换）。"""
    os.killpg(int(pgid), int(sig))


def _kill(pid: int, sig: int) -> None:
    """对单 PID 发信号（薄封装；测试可替换）。"""
    os.kill(int(pid), int(sig))


def _group_exists(pgid: int) -> bool:
    """进程组是否仍有成员（不含僵尸以外的判断）；查询失败 -> 异常。"""
    try:
        _killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:  # pragma: no cover - 罕见内核错误
        raise OwnershipUnknownError(
            f"进程组 {pgid} 存在性查询失败：{exc}"
        ) from exc


def _pid_exists(pid: int) -> bool | None:
    """PID 是否存在；``None`` = 无法判定（fail-closed）。"""
    try:
        _kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # 存在但无权限发信号
    except OSError:  # pragma: no cover - 平台差异
        return None


# --------------------------------------------------------------------------- identity readers
def parse_proc_stat(text: str) -> dict[str, str]:
    """解析 ``/proc/<pid>/stat``（comm 可含空格/括号，用最后一个 ``)`` 切分）。

    返回字段名：``state`` / ``ppid`` / ``pgrp`` / ``session`` / ``starttime``。
    """
    close = text.rfind(")")
    if close < 0:
        raise ValueError("非法 /proc/<pid>/stat：缺少 comm 结束括号")
    rest = text[close + 2 :].split()
    # rest[0] 是字段 3（state）；字段 N 的下标是 N-3。
    if len(rest) < 20:
        raise ValueError(f"非法 /proc/<pid>/stat：字段不足（{len(rest)}）")
    return {
        "state": rest[0],
        "ppid": rest[1],
        "pgrp": rest[2],
        "session": rest[3],
        "starttime": rest[19],
    }


def read_boot_id() -> str | None:
    """Linux 启动 ID（跨重启区分 starttime）；不可用返回 ``None``。"""
    global _BOOT_ID, _BOOT_ID_READ
    with _BOOT_ID_LOCK:
        if _BOOT_ID_READ:
            return _BOOT_ID
        try:
            _BOOT_ID = Path("/proc/sys/kernel/random/boot_id").read_text(
                encoding="utf-8"
            ).strip() or None
        except OSError:
            _BOOT_ID = None
        _BOOT_ID_READ = True
        return _BOOT_ID


def read_linux_identity(
    pid: int, *, proc_root: str | os.PathLike[str] = "/proc"
) -> tuple[ProcessIdentity | None, str | None]:
    """从 ``/proc`` 读身份与进程状态字符；失败 ``(None, None)``。"""
    path = Path(proc_root) / str(int(pid)) / "stat"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None, None
    try:
        fields = parse_proc_stat(text)
        starttime = int(fields["starttime"])
        pgid = int(fields["pgrp"])
    except (ValueError, KeyError):
        return None, None
    identity = ProcessIdentity(
        pid=int(pid),
        kind="starttime",
        birth=str(starttime),
        platform=PLATFORM_POSIX,
        boot_id=read_boot_id(),
        pgid=pgid,
    )
    return identity, fields["state"]


def _run_ps(pid: int) -> tuple[int, str]:
    """执行 ``ps`` 读取身份字段（薄封装；测试可替换）。返回 (returncode, stdout)。"""
    env = {**os.environ, "LC_ALL": "C", "LANG": "C"}
    completed = subprocess.run(
        ["ps", "-o", "lstart=", "-o", "pgid=", "-o", "stat=", "-p", str(int(pid))],
        capture_output=True,
        text=True,
        env=env,
        timeout=3.0,
        check=False,
    )
    return int(completed.returncode), completed.stdout


def read_ps_identity(pid: int) -> tuple[ProcessIdentity | None, str | None]:
    """用 ``ps`` 读身份（macOS / 无 ``/proc`` 的 POSIX）；失败 ``(None, None)``。

    ``LC_ALL=C`` 固定 ``lstart`` 文本，避免 locale 差异破坏身份字符串。
    """
    try:
        returncode, stdout = _run_ps(pid)
    except (OSError, subprocess.SubprocessError):
        return None, None
    if returncode != 0:
        return None, None
    line = stdout.strip()
    if not line:
        return None, None
    parts = line.rsplit(None, 2)
    if len(parts) != 3:
        return None, None
    lstart, pgid_text, stat = parts
    birth = " ".join(lstart.split())
    if not birth:
        return None, None
    try:
        pgid = int(pgid_text)
    except ValueError:
        return None, None
    identity = ProcessIdentity(
        pid=int(pid),
        kind="lstart",
        birth=birth,
        platform=PLATFORM_POSIX,
        pgid=pgid,
    )
    return identity, stat[:1] or None


def read_posix_identity(pid: int) -> tuple[ProcessIdentity | None, str | None]:
    """Linux reads /proc; Darwin reads native libproc. No weak fallback."""
    _require_posix()
    if sys.platform.startswith("linux"):
        identity, state = read_linux_identity(pid)
        if identity is not None:
            return identity, state
    if sys.platform == "darwin":
        from packages.core.platform.process_darwin import read_darwin_identity
        return read_darwin_identity(pid)
    return None, None


def _strong_identity(identity: ProcessIdentity | None) -> bool:
    return (identity is not None and identity.kind in {"starttime", "proc_uniqueid"}
            and bool(identity.boot_id))


def _same_darwin_process(expected: ProcessIdentity, observed: ProcessIdentity) -> bool:
    if (expected.kind != "proc_uniqueid" or observed.kind != "proc_uniqueid"
            or expected.pid != observed.pid or expected.boot_id != observed.boot_id):
        return False
    try:
        first = [int(value) for value in expected.birth.split(":")]
        second = [int(value) for value in observed.birth.split(":")]
        return len(first) == len(second) == 4 and first[0] == second[0]
    except ValueError:
        return False


def _tree_members(pid: int, pgid: int) -> tuple[ProcessIdentity, ...]:
    """Snapshot descendants while the leader still authorizes the group.

    A process group is not a containment boundary. An observed escaped member
    refuses cleanup; neither PID signals nor a success receipt are substitutes.
    """
    import psutil

    try:
        processes = [psutil.Process(pid)]
        processes.extend(processes[0].children(recursive=True))
        records = []
        for process in processes:
            identity, state = read_posix_identity(process.pid)
            if identity is None and _pid_exists(process.pid) is False:
                continue  # Proven disappearance during a read-only snapshot.
            if (not _strong_identity(identity)
                    or identity.pgid != pgid):
                raise OwnershipUnknownError("Tree member unreadable or outside original group")
            records.append(identity)
        return tuple(records)
    except (psutil.Error, OSError) as exc:
        raise OwnershipUnknownError("Tree membership query failed") from exc


# --------------------------------------------------------------------------- handle
class PosixOwnedProcess(OwnedProcess):
    """POSIX 自有进程：``Popen`` + spawn 时绑定的身份/进程组。

    ``popen`` 由本句柄持有（``wait`` 用它回收僵尸）；调用方不要另外 ``wait``。
    stdin/stdout/stderr 的读写责任仍在调用方（本层只做所有权）。
    """

    platform = PLATFORM_POSIX

    def __init__(
        self,
        owner: "PosixProcessOwner",
        popen: Any,
        identity: ProcessIdentity,
        pgid: int,
    ) -> None:
        super().__init__(owner, int(popen.pid), identity)
        self.popen = popen
        self.pgid = int(pgid)
        self.members = {identity.pid: identity}
        self.membership_unknown = False
        with _RETAINED_LOCK:
            _RETAINED[(identity.pid, identity.kind, identity.birth, identity.boot_id)] = self

    def close(self) -> None:
        """释放句柄（POSIX 无内核句柄可关；幂等，仅标记关闭）。"""
        with self._close_lock:
            self._closed = True


# --------------------------------------------------------------------------- owner
class PosixProcessOwner(ProcessOwner):
    """POSIX 实现：独立 session + 进程组整树终止 + 持久身份回收。

    构造无副作用（可在任何平台导入/实例化）；**平台方法**调用时若
    ``os.name != "posix"`` 抛 :class:`UnsupportedPlatformError`。
    """

    platform = PLATFORM_POSIX

    def __init__(self) -> None:
        pass

    # ------------------------------------------------------------- spawn
    def spawn(
        self,
        argv: Sequence[str],
        *,
        cwd: str | os.PathLike[str] | None = None,
        env: Mapping[str, str] | None = None,
        stdin: Any = subprocess.DEVNULL,
        stdout: Any = None,
        stderr: Any = None,
        hide_window: bool = True,  # POSIX 无窗口语义；保留参数以便同签名
    ) -> PosixOwnedProcess:
        _require_posix()
        if sys.platform.startswith("linux"):
            if not read_boot_id():
                raise OwnershipUnknownError("Linux boot identity unavailable: no effect admitted")
        elif sys.platform == "darwin":
            # Probe before admitting an effect; never fall back to weak ps.
            mine, _ = read_posix_identity(os.getpid())
            if not _strong_identity(mine):
                raise OwnershipUnknownError("Darwin strong native identity unavailable: no effect admitted")
        else:
            raise UnsupportedPlatformError("No strong POSIX process identity backend")
        args = [str(a) for a in argv]
        if not args:
            raise ValueError("argv 不能为空：拒绝无命令的 spawn")
        popen = subprocess.Popen(
            args,
            cwd=cwd,
            env=None if env is None else {str(k): str(v) for k, v in env.items()},
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
            close_fds=True,
        )
        identity, _state = read_posix_identity(popen.pid)
        if not _strong_identity(identity):
            self._abort_spawn(popen)
            raise OwnershipUnknownError(
                "spawn 后读不到子进程身份（/proc 与 ps 均失败）："
                "无身份不可纳管，已终止本次自建进程组"
            )
        if identity.pgid is not None and int(identity.pgid) != int(popen.pid):
            self._abort_spawn(popen)
            raise OwnershipUnknownError(
                f"start_new_session 未产生独立进程组（pid={popen.pid}, "
                f"pgid={identity.pgid}）：拒绝交出所有权"
            )
        identity = ProcessIdentity(
            pid=identity.pid,
            kind=identity.kind,
            birth=identity.birth,
            platform=identity.platform,
            boot_id=identity.boot_id,
            pgid=int(popen.pid),
            image=args[0],
        )
        return PosixOwnedProcess(self, popen, identity, int(popen.pid))

    @staticmethod
    def _abort_spawn(popen: Any) -> None:
        # Before the first wait/poll the direct child's PID is reserved even if
        # it has become a zombie. This narrow spawn-abort authority must never
        # be reused for a reaped leader or an externally persisted record.
        try:
            _killpg(int(popen.pid), _sigkill())
        except ProcessLookupError:
            pass
        except OSError:
            raise OwnershipUnknownError("Spawn abort group signal failed; cleanup unconfirmed")
        try:
            popen.wait(timeout=5.0)
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise OwnershipUnknownError(
                f"Spawn abort root exit unconfirmed (pid={popen.pid})"
            ) from exc

    # ------------------------------------------------------------- queries
    def identity(self, target: PosixOwnedProcess | int) -> ProcessIdentity | None:
        if isinstance(target, OwnedProcess):
            self._require_handle(target)
            return target.identity
        _require_posix()
        identity, _state = read_posix_identity(int(target))
        return identity

    def check(self, identity: ProcessIdentity) -> IdentityCheck:
        if identity.platform != PLATFORM_POSIX:
            return IdentityCheck(
                status=ProcessStatus.UNKNOWN,
                ours=None,
                expected=identity,
                detail=f"身份记录属于 {identity.platform!r}，不能在 POSIX 复核",
            )
        if not _strong_identity(identity):
            return IdentityCheck(
                status=ProcessStatus.UNKNOWN,
                ours=None,
                expected=identity,
                detail="Strong birth identity unavailable; lstart/missing boot_id cannot authorize ownership",
            )
        observed, state = read_posix_identity(identity.pid)
        if observed is None:
            exists = _pid_exists(identity.pid)
            if exists is False:
                return IdentityCheck(
                    status=ProcessStatus.DEAD,
                    ours=None,
                    expected=identity,
                    detail="PID 不存在：进程已退出",
                )
            return IdentityCheck(
                status=ProcessStatus.UNKNOWN,
                ours=None,
                expected=identity,
                detail="PID 存在但读不到身份：无结论（fail-closed）",
            )
        if not _strong_identity(observed):
            return IdentityCheck(
                status=ProcessStatus.UNKNOWN, ours=None, expected=identity,
                observed=observed, detail="Observed birth identity is weak: fail-closed",
            )
        if (_same_darwin_process(identity, observed)
                and not observed.matches(identity)):
            # pidversion can change on exec without process exit. This is not
            # PID reuse and must never become supervisor-exit evidence.
            return IdentityCheck(status=ProcessStatus.UNKNOWN, ours=None,
                expected=identity, observed=observed,
                detail="Darwin execution generation changed within original process; no signal/exit authority")
        if state == "Z":
            return IdentityCheck(
                status=ProcessStatus.DEAD,
                ours=observed.matches(identity),
                expected=identity,
                observed=observed,
                detail="僵尸进程：已退出，等待父进程回收",
            )
        if observed.matches(identity):
            return IdentityCheck(
                status=ProcessStatus.ALIVE,
                ours=True,
                expected=identity,
                observed=observed,
                detail=f"出生标记精确匹配（{identity.kind}）",
            )
        return IdentityCheck(
            status=ProcessStatus.ALIVE,
            ours=False,
            expected=identity,
            observed=observed,
            detail="出生标记不匹配：PID 已被复用，拒绝终止",
        )

    # ------------------------------------------------------------- lifecycle
    def _signal_authority(self, identity: ProcessIdentity, pgid: int) -> None:
        """Recheck birth AND current group before each destructive signal.

        A retained Popen proves leader exit, not ownership of a numeric pgid.
        In particular it cannot authorize escalation after leader exit.
        """
        check = self.check(identity)
        if check.status is ProcessStatus.ALIVE and check.ours is False:
            raise IdentityMismatchError("Birth identity mismatch: refusing group signal")
        if not check.verified_alive:
            raise OwnershipUnknownError("Leader unavailable: group ownership unknown")
        if (pgid != identity.pid or identity.pgid != pgid
                or check.observed is None or check.observed.pgid != pgid):
            raise OwnershipUnknownError("Process group changed or missing: refusing group signal")

    def _observe_members(self, owned: PosixOwnedProcess, *, strict=False):
        if owned.popen.returncode is None:
            try:
                members = _tree_members(owned.pid, owned.pgid)
                owned.members.update((member.pid, member) for member in members)
            except OwnershipUnknownError:
                if self.check(owned.identity).status is not ProcessStatus.DEAD:
                    owned.membership_unknown = True
                if strict:
                    raise
        return tuple(owned.members.values())

    def _exited_tree(self, owned: PosixOwnedProcess) -> TreeTerminationResult | None:
        """Read-only clearance never grants signal authority to a dead leader."""
        self._reap(owned)
        if owned.popen.returncode is None:
            return None
        remaining = self._member_remaining(tuple(owned.members.values()),
                                           (owned.pgid,) if _group_exists(owned.pgid) else ())
        if owned.membership_unknown:
            remaining = tuple(sorted(set(remaining) | {owned.pgid}))
        return TreeTerminationResult(owned=tuple(sorted(owned.members)), remaining=remaining,
            confirmed=not remaining, detail="Retained root exit + read-only group/member absence"
            if not remaining else "Root exited; tree resources remain unknown (no signal authorized)")

    def wait(
        self, handle: PosixOwnedProcess, *, timeout: float | None = None
    ) -> WaitResult:
        _require_posix()
        owned = self._require_handle(handle)
        started = time.monotonic()
        deadline = None if timeout is None else started + max(0.0, float(timeout))
        while True:
            self._observe_members(owned)
            try:
                budget = POLL_INTERVAL if deadline is None else max(0.0, min(POLL_INTERVAL, deadline - time.monotonic()))
                code = owned.popen.wait(timeout=budget)
                break
            except subprocess.TimeoutExpired:
                if deadline is not None and time.monotonic() >= deadline:
                    return WaitResult(exited=False, exit_code=None, timed_out=True,
                                      seconds=round(time.monotonic() - started, 4),
                                      detail="等待超时：进程仍在运行（未终止）")
        return WaitResult(
            exited=True,
            exit_code=None if code is None else int(code),
            timed_out=False,
            seconds=round(time.monotonic() - started, 4),
        )

    def terminate(
        self, handle: PosixOwnedProcess, *, grace: float = 5.0
    ) -> TerminateResult:
        """SIGTERM（进程组）→ ``grace`` 宽限 → SIGKILL；返回前核对组是否为空。"""
        _require_posix()
        owned = self._require_handle(handle)
        grace = max(0.0, float(grace))
        started = time.monotonic()
        pgid = owned.pgid

        settled = self._exited_tree(owned)
        if settled is not None:
            return TerminateResult(requested=False, exited=True, escalated=False,
                                   signal_sent=None, exit_code=owned.popen.returncode,
                                   remaining=settled.remaining, detail=settled.detail)
        self._signal_authority(owned.identity, pgid)
        members = self._observe_members(owned, strict=True)
        requested = True
        escalated = False
        signal_sent: str | None = None
        self._signal_authority(owned.identity, pgid)
        signal_sent = _signal_name(_sigterm())
        try:
            _killpg(pgid, _sigterm())
        except ProcessLookupError:
            pass
        deadline = started + grace
        while time.monotonic() < deadline:
            self._reap(owned)
            if owned.popen.returncode is not None and not _group_exists(pgid):
                break
            time.sleep(POLL_INTERVAL)
        if _group_exists(pgid):
            self._signal_authority(owned.identity, pgid)
            escalated = True
            signal_sent = _signal_name(_sigkill())
            try:
                _killpg(pgid, _sigkill())
            except ProcessLookupError:
                pass
            kill_deadline = time.monotonic() + min(
                MAX_KILL_CONFIRM_TIMEOUT, max(0.5, grace)
            )
            while time.monotonic() < kill_deadline:
                self._reap(owned)
                if not _group_exists(pgid):
                    break
                time.sleep(POLL_INTERVAL)

        self._reap(owned)
        remaining = self._member_remaining(members, self._remaining(owned, pgid))
        if owned.membership_unknown:
            remaining = tuple(sorted(set(remaining) | {pgid}))
        code = owned.popen.returncode
        return TerminateResult(
            requested=requested,
            exited=code is not None,
            escalated=escalated,
            signal_sent=signal_sent,
            exit_code=None if code is None else int(code),
            remaining=remaining,
            detail=(
                f"grace={grace}s；escalated={escalated}；"
                + ("进程组已清空" if not remaining else "仍有残留（fail-closed）")
            ),
        )

    def kill_tree(
        self, handle: PosixOwnedProcess, *, timeout: float = 5.0
    ) -> TreeTerminationResult:
        """SIGKILL 整个进程组并核对清空（不等宽限）。"""
        _require_posix()
        owned = self._require_handle(handle)
        pgid = owned.pgid
        settled = self._exited_tree(owned)
        if settled is not None:
            return settled
        self._signal_authority(owned.identity, pgid)
        members = self._observe_members(owned, strict=True)
        try:
            self._signal_authority(owned.identity, pgid)
            _killpg(pgid, _sigkill())
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + max(0.0, float(timeout))
        while time.monotonic() < deadline:
            self._reap(owned)
            if not _group_exists(pgid):
                break
            time.sleep(POLL_INTERVAL)
        self._reap(owned)
        remaining = self._remaining(owned, pgid)
        # A zombie is still a resource, and a live escaped snapshot member is
        # not erased by killpg(pgid, 0) returning ESRCH.
        remaining = self._member_remaining(members, remaining)
        if owned.membership_unknown:
            remaining = tuple(sorted(set(remaining) | {pgid}))
        return TreeTerminationResult(
            owned=tuple(sorted(member.pid for member in members)),
            remaining=remaining,
            confirmed=not remaining,
            detail=(
                "killpg(SIGKILL) + killpg(pgid, 0) 核对"
                if not remaining
                else "仍有残留（fail-closed）"
            ),
        )

    @staticmethod
    def _member_remaining(
        members: Sequence[ProcessIdentity], remaining: Sequence[int] = ()
    ) -> tuple[int, ...]:
        unresolved = set(remaining)
        for member in members:
            observed, state = read_posix_identity(member.pid)
            if observed is None:
                if _pid_exists(member.pid) is not False:
                    unresolved.add(member.pid)
            elif (not _strong_identity(observed)
                  or observed.matches(member) or _same_darwin_process(member, observed)):
                unresolved.add(member.pid)
        return tuple(sorted(unresolved))

    @staticmethod
    def _remaining(owned: PosixOwnedProcess, pgid: int) -> tuple[int, ...]:
        """未确认结束的目标：根进程（若仍活）与进程组（若仍有成员）。"""
        still: list[int] = []
        if owned.popen.returncode is None:
            still.append(int(owned.pid))
        if _group_exists(pgid) and int(pgid) not in still:
            still.append(int(pgid))
        return tuple(still)

    @staticmethod
    def _reap(owned: PosixOwnedProcess) -> None:
        """非阻塞回收根进程（僵尸会让 ``killpg(pgid, 0)`` 误报“组仍存在”）。"""
        try:
            owned.popen.poll()
        except OSError:  # pragma: no cover - Popen 已损坏
            pass

    # ------------------------------------------------------------- recovery
    def recover(
        self,
        record: Mapping[str, Any],
        *,
        terminate: bool = True,
        grace: float = 5.0,
        timeout: float = 5.0,
    ) -> RecoveryResult:
        _require_posix()
        try:
            expected = self._identity_from_record(record)
        except ValueError as exc:
            return RecoveryResult(
                action="unknown",
                verified=False,
                terminated=False,
                detail=f"身份记录不可用：{exc}",
            )
        if _strong_identity(expected) and expected.pgid == expected.pid:
            with _RETAINED_LOCK:
                retained = _RETAINED.get((expected.pid, expected.kind, expected.birth, expected.boot_id))
            if retained is not None and not retained.closed and retained.pgid == expected.pgid and terminate:
                check = self.check(expected)
                try:
                    result = retained.owner.terminate(retained, grace=grace)
                    clear = result.exited and not result.remaining
                    return RecoveryResult(action=("terminated" if result.requested else "already_dead") if clear else "unknown",
                        verified=True, terminated=clear and result.requested,
                        remaining=result.remaining, check=check, exit_code=result.exit_code,
                        detail="Original retained Popen + group/member evidence; no fresh PID wait")
                except (OSError, OwnershipUnknownError, IdentityMismatchError) as exc:
                    return RecoveryResult(action="unknown", verified=False, terminated=False,
                        remaining=(expected.pgid,), check=check, detail=str(exc))
        check = self.check(expected)
        if check.status is ProcessStatus.DEAD:
            # No destructive action. Only proven PID absence and group absence
            # may report already_dead; a zombie or live subtree stays unknown.
            absent = False
            if expected.pgid == expected.pid and _pid_exists(expected.pid) is False:
                try:
                    absent = not _group_exists(expected.pgid)
                except (OSError, OwnershipUnknownError):
                    pass
            return RecoveryResult(action="already_dead" if absent else "unknown",
                verified=check.ours is True, terminated=False, check=check,
                remaining=() if absent else (expected.pgid or expected.pid,),
                detail="Read-only PID/group absence" if absent else
                "Leader exited; tree resources unknown, no stale pgid signal authorized")
        if check.status is ProcessStatus.ALIVE and check.ours is False:
            return RecoveryResult(
                action="refused_identity",
                verified=False,
                terminated=False,
                check=check,
                detail="出生标记不匹配：拒绝终止",
            )
        if check.status is not ProcessStatus.ALIVE:
            return RecoveryResult(
                action="unknown",
                verified=False,
                terminated=False,
                check=check,
                remaining=(expected.pgid or expected.pid,),
                detail="身份/存活不可确认：fail-closed，不终止",
            )

        observed = check.observed
        if expected.pgid is None:
            return RecoveryResult(
                action="unknown",
                verified=True,
                terminated=False,
                check=check,
                detail="记录缺少 pgid：无法确定终止目标，拒绝",
            )
        if int(expected.pgid) != int(expected.pid):
            return RecoveryResult(
                action="unknown",
                verified=True,
                terminated=False,
                check=check,
                detail=(
                    f"记录 pgid={expected.pgid} 与 pid={expected.pid} 不一致："
                    "只有 setsid 新建的进程组才可安全回收，拒绝"
                ),
            )
        if observed is not None and observed.pgid is not None:
            if int(observed.pgid) != int(expected.pgid):
                return RecoveryResult(
                    action="refused_pgid_changed",
                    verified=True,
                    terminated=False,
                    check=check,
                    detail=(
                        f"进程已更换进程组（记录={expected.pgid}, "
                        f"实际={observed.pgid}）：拒绝终止，避免误杀他组"
                    ),
                )
        if not terminate:
            return RecoveryResult(
                action="none",
                verified=True,
                terminated=False,
                check=check,
                detail="身份成立且仍存活；调用方选择不终止",
            )

        pgid = int(expected.pgid)
        grace = max(0.0, float(grace))
        signal_sent: str | None = None
        try:
            members = _tree_members(expected.pid, pgid)
            if grace > 0:
                self._signal_authority(expected, pgid)
                signal_sent = _signal_name(_sigterm())
                try:
                    _killpg(pgid, _sigterm())
                except ProcessLookupError:
                    pass
                term_deadline = time.monotonic() + grace
                while time.monotonic() < term_deadline:
                    if not _group_exists(pgid):
                        break
                    time.sleep(POLL_INTERVAL)
            if _group_exists(pgid):
                self._signal_authority(expected, pgid)
                signal_sent = _signal_name(_sigkill())
                try:
                    _killpg(pgid, _sigkill())
                except ProcessLookupError:
                    pass
                deadline = time.monotonic() + max(0.0, float(timeout))
                while time.monotonic() < deadline:
                    if not _group_exists(pgid):
                        break
                    time.sleep(POLL_INTERVAL)
        except (OSError, OwnershipUnknownError, IdentityMismatchError) as exc:
            return RecoveryResult(
                action="unknown",
                verified=True,
                terminated=False,
                remaining=(pgid,),
                check=check,
                detail=f"回收失败（{type(exc).__name__}）：{exc}",
            )
        remaining = self._member_remaining(members, (pgid,) if _group_exists(pgid) else ())
        return RecoveryResult(
            action="terminated" if not remaining else "unknown",
            verified=True,
            terminated=not remaining,
            remaining=remaining,
            check=check,
            detail=(
                f"按持久身份校验后 {signal_sent or 'SIGKILL'} 回收"
                + ("；进程组已清空" if not remaining else "；仍有残留（fail-closed）")
            ),
        )
