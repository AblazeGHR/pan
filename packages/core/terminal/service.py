"""Pan TerminalService：终端服务控制层（P2 第一批）。

本层是 **Pan 服务侧的终端编排者**：持有 registry 记录、派生独立 runner 进程
（生产 launcher）、维持独立心跳、代理 attachment 控制 lease，并把显式
close / detach / reconcile / shutdown 的生命周期闭环做实。**不含** HTTP/WS/MCP
入口，也不含前端与 lifespan 接线（下一批）。

设计依据（只读，见 ``docs/design/``）：

- ``PAN_TERMINAL_IMPLEMENTATION_PLAN_20261003.md``（§5.5 reconcile、§13 预算）；
- ``PAN_TERMINAL_COMPOSITION_BOUNDARIES_20261003.md``（I1 生命周期与 reason 词表、
  I2 快照/恢复边界、I3 确认字段、I4 服务与 attachment 分层）；
- ``PAN_TERMINAL_RUNNER_INTERFACES_20261003.md`` / ``_IPC_INTERFACES_20261003.md``
  （argv 无 token、bootstrap 自证、HMAC、lease 心跳、8 个命令）；
- ``PAN_TERMINAL_LAUNCHER_INTERFACES_20261003.md``（引擎归属与收尾、退出码）；
- ``PAN_TERMINAL_CORE_INTERFACES_20261003.md`` §4.5/§6/§7（registry 数据层、
  attachment lease 协议、``RuntimeState`` 合法枚举）。

必须保持的性质（逐条对应实施验收）：

1. **导入无副作用**：模块导入不建数据目录、不起线程/进程；Windows 专用依赖
   （``runner_client``/``secret_store``/``win_pipe``）全部**函数内延迟导入**。
2. **先持久化后派生**：``create`` 先写 registry 记录，再派生进程；四门（自身校验 /
   DPAPI 秘密 / HMAC 双向认证 / 原子 spawn 证据）确认前**不发布 RUNNING**。
3. **不认 ``Popen.pid``**：runner 身份权威 = hello 自证 + 同句柄 pid/raw FILETIME/Wait
   内核核验；本进程持有的 spawn 句柄只用于**自己派生**的那个 launcher 进程。
   两者身份分别记录、分别清理，**不按命令行/进程名广杀**。
4. **独立心跳**：心跳走**自己的连接**与稳定 ``client_id``（间隔 1s / 死期 2s），
   慢 snapshot / input / close 不拖住心跳；未完成 worker 可追踪、跨重试复用、不叠加。
5. **控制权分层**：attachment（浏览器）lease 与 runner IPC 所有者心跳是两层；
   ``input``/``resize`` 经 ``AttachmentRegistry`` **同临界区**校验+调用，撤销后
   **零新写**；observer 不可 input/resize；断开 attachment 只撤销，不停止 runtime。
6. **不做过度声明**：read 有 gap 就返回 gap + fresh-view 提示（不补零、不跳 cursor、
   不自动 reset、不杀 PTY）；snapshot 保留协议 A 的 partial/degraded/note 与 F5
   bool-or-null 字段（``diagnostics.reasons`` 为空**不等于** full）；resize 的
   PTY / 引擎确认**分列**，不宣称三方一致。
7. **未证明就不改事实**：close 先记 CLOSING（映射既有 ``RuntimeState.EXITING``），
   只有“runner 收尾确认 + launcher 引擎收尾 + 经身份核验的进程退出”三者齐备才记
   ``exited`` 并删除秘密；否则保留 owner/record/secret 可重试，不标 exited/lost，
   **不用 caller-responsibility 绕过**。
8. **reconcile 不冒充恢复**：未知/错身份**零终止**并保留诊断；PID 查不到**不是**
   retained DEAD 证据（“已死 / 无法归因 / 清理未确认”三者分列）；不创建同 id 替代
   runner、不复活旧 lease、不因客户端断连删秘密。
9. **输出面脱敏**：公共返回值不含 token/pipe/秘密内容；错误只带静态 reason 与
   **异常类型名**，诊断有界。

**本批未验收门**：Ctrl-C、真实 durable detach（ambient Job 下显式拒绝）、浏览器、
Web 鉴权（Origin/CSRF/MCP caller gate）、跨用户/主机、POSIX、长稳。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .attachments import AttachmentRegistry
from .contracts import (
    LEASE_ROLE_CONTROL,
    LEASE_ROLE_OBSERVER,
    LeaseToken,
    NotControlLeaseError,
    RuntimeState,
    TerminalExistsError,
    TerminalRecord,
    TerminalScope,
    UnknownTerminalError,
)
from .registry import TerminalRegistry

__all__ = [
    "DEFAULT_CAPACITY",
    "DEFAULT_HEARTBEAT_INTERVAL_SECONDS",
    "DEFAULT_LEASE_GRACE_SECONDS",
    "DEFAULT_STOP_CONFIRM_SECONDS",
    "DEFAULT_SHUTDOWN_BUDGET_SECONDS",
    "DEFAULT_STARTUP_TIMEOUT_SECONDS",
    "DEFAULT_SHUTDOWN_REASON",
    "MAX_DIAGNOSTIC_EVENTS",
    "CALLBACK_REASON",
    "EXIT_REASON_EXPLICIT_CLOSE",
    "EXIT_REASON_SERVICE_SHUTDOWN",
    "ServiceContext",
    "TerminalServiceError",
    "CapacityExceeded",
    "StartupFailed",
    "CleanupUnconfirmed",
    "DetachRefused",
    "TerminalNotAttached",
    "ShutdownBudgetExhausted",
    "ServiceClosingDown",
    "MAX_STOP_RESENDS",
    "TerminalService",
]


# ══════════════════════════════════════════════════════════════════════════
# 常量（预算与词表；与计划 §13 / 组合边界 I1 对齐）
# ══════════════════════════════════════════════════════════════════════════

#: 并发容量准入上限（同时存活且未终结的终端数）。
DEFAULT_CAPACITY = 8
#: 所有者心跳间隔（组合边界 I4：1s）。
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 1.0
#: lease 死期（核心契约统一口径 2s）。
DEFAULT_LEASE_GRACE_SECONDS = 2.0
#: 显式 close 等 runner 整树收尾的预算。
DEFAULT_STOP_CONFIRM_SECONDS = 5.0
#: shutdown 总预算（**含锁等待**，见计划 §13）。
DEFAULT_SHUTDOWN_BUDGET_SECONDS = 20.0
#: 启动等待 hello 自证的预算。
DEFAULT_STARTUP_TIMEOUT_SECONDS = 30.0
#: 有界诊断事件条数（超出丢弃最旧；防无界增长）。
MAX_DIAGNOSTIC_EVENTS = 64

#: 关闭/停止原因（组合边界 I1 冻结词表：close / explicit-close / service-shutdown /
#: lease-expired / detach；**不新增词**）。
EXIT_REASON_EXPLICIT_CLOSE = "explicit-close"
EXIT_REASON_SERVICE_SHUTDOWN = "service-shutdown"
#: 回调原因（automation/等待场景的显式关闭）。
CALLBACK_REASON = "close"

#: 本服务代发的客户端角色（不新建协议角色；仅用于诊断与稳定 id 派生）。
_OWNER_ROLE = LEASE_ROLE_CONTROL

#: 记录里视为"不再占容量"的终态。
_TERMINAL_STATUSES = frozenset({RuntimeState.EXITED, RuntimeState.LOST})

#: 单次close 序列内允许的 **stop 重发**上限（F2：允许幂等重发，但有界防风暴）。
MAX_STOP_RESENDS = 2

#: launcher 状态文件目录名（与 launcher/runner 平行）。
_LAUNCHER_STATUS_DIRNAME = "launcher-status"
_RUNNER_STATUS_DIRNAME = "runner-status"
_SERVICE_LOG_DIRNAME = "service-logs"

_REPO_ROOT = Path(__file__).resolve().parents[3]


# ══════════════════════════════════════════════════════════════════════════
# 错误类型（只带静态 reason + 异常类型名；不含 token/pipe/文本细节）
# ══════════════════════════════════════════════════════════════════════════


class TerminalServiceError(RuntimeError):
    """服务层错误基类。``reason`` 是**静态可枚举**分类，不是自由文本。"""

    reason = "service-error"

    def __init__(self, reason: str | None = None, *, error_type: str | None = None) -> None:
        self.reason = str(reason or type(self).reason)
        self.error_type = error_type
        detail = f": {error_type}" if error_type else ""
        super().__init__(f"{self.reason}{detail}")


class CapacityExceeded(TerminalServiceError):
    """容量准入拒绝（默认 8）；**零状态变化**（无记录、无进程）。"""

    reason = "capacity-exceeded"


class StartupFailed(TerminalServiceError):
    """启动未完成（四门未确认 / 派生失败 / bootstrap 未核验）。

    记录与 owner **保留**（可重试），秘密不提前删除。
    """

    reason = "startup-failed"


class CleanupUnconfirmed(TerminalServiceError):
    """清理未获证明；保留 owner/record/secret 可重试。"""

    reason = "cleanup-unconfirmed"


class DetachRefused(TerminalServiceError):
    """真实宿主不支持 durable detach（ambient Job）；**零状态变化**。"""

    reason = "detach-refused"


class TerminalNotAttached(TerminalServiceError):
    """本服务实例未持有该终端的连接（可 reconcile 后重连）。"""

    reason = "not-attached"


class ServiceClosingDown(TerminalServiceError):
    """``shutdown`` 已开始：拒绝新 ``create``（F9，避免产生无人收敛的新终端）。"""

    reason = "service-closing-down"


class ShutdownBudgetExhausted(TerminalServiceError):
    """shutdown 总预算耗尽；已收敛的部分如实报告，未收敛部分保留。"""

    reason = "shutdown-budget-exhausted"


# ══════════════════════════════════════════════════════════════════════════
# 调用上下文（trusted local/controller + attachment 角色；**不是**鉴权）
# ══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ServiceContext:
    """服务调用上下文。

    ``role`` 标明调用者在 attachment 协议里的角色（``control`` / ``observer``）；
    ``created_by`` 记录**来源**（如 ``"web"`` / ``"mcp"`` / ``"service"``），
    **不从目标 session_id 推断**。

    ``trusted_local=True`` 只表示"调用方是同用户本地受信控制器"这一**接线前提**：
    本层**不**实施 Origin/CSRF/MCP caller gate，也不因此声称存在 Web 鉴权
    （真实鉴权是下一批的接线责任）。
    """

    created_by: str = "service"
    role: str = LEASE_ROLE_CONTROL
    trusted_local: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "created_by": str(self.created_by)[:32],
            "role": str(self.role),
            "trusted_local": bool(self.trusted_local),
            "authorization": "not-enforced-in-this-layer",
        }


# ══════════════════════════════════════════════════════════════════════════
# 可追踪的有界调用（worker 跨重试复用、不叠加）
# ══════════════════════════════════════════════════════════════════════════


class _BoundedCall:
    """有界阻塞调用的追踪器（同 ``runner._TrackedCall`` 纪律）。

    - 首次 ``run`` 启动 daemon worker；超时返回 ``finished=False``，worker **保留**；
    - 后续 ``run`` **等待同一 worker**（不重复发起同一阻塞调用、不叠加）；
    - worker 结束后结果被缓存，迟到调用可取回（``late_success`` 消费）。
    - 只记异常**类型名**（异常文本可能带秘密）。
    """

    def __init__(self, fn: Callable[[], Any], *, name: str) -> None:
        self._fn = fn
        self._name = str(name)
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._done = threading.Event()
        self._result: Any = None
        self._error_type: str | None = None
        self._started = False

    @property
    def started(self) -> bool:
        return self._started

    @property
    def finished(self) -> bool:
        return self._done.is_set()

    @property
    def in_flight(self) -> bool:
        worker = self._worker
        return worker is not None and worker.is_alive()

    @property
    def succeeded(self) -> bool:
        return self._done.is_set() and self._error_type is None

    @property
    def result(self) -> Any:
        return self._result

    @property
    def error_type(self) -> str | None:
        return self._error_type

    def run(self, budget: float) -> tuple[bool, Any, str | None]:
        """等待至多 ``budget`` 秒；返回 ``(finished, result, error_type)``。"""
        with self._lock:
            if not self._done.is_set() and not self.in_flight:
                self._started = True
                self._worker = threading.Thread(
                    target=self._execute, daemon=True, name=f"pan-terminal-{self._name}"
                )
                self._worker.start()
        if self._done.wait(timeout=max(0.0, float(budget))):
            return True, self._result, self._error_type
        return False, None, None

    def reset(self) -> bool:
        """丢弃已缓存的结果，为下一轮**有界重发**做准备（F2）。

        **仅在 worker 已结束**时允许——在途调用绝不复用/重置（那会造成叠加）。
        返回是否成功重置。
        """
        with self._lock:
            if self.in_flight:
                return False
            self._worker = None
            self._done = threading.Event()
            self._result = None
            self._error_type = None
            self._started = False
            return True

    def _execute(self) -> None:
        try:
            self._result = self._fn()
        except Exception as exc:  # noqa: BLE001 - 只记类型名（文本可能含敏感内容）
            self._error_type = type(exc).__name__
        finally:
            self._done.set()


# ══════════════════════════════════════════════════════════════════════════
# 独立心跳（自己的连接 + 稳定 client_id）
# ══════════════════════════════════════════════════════════════════════════


class _Heartbeat:
    """单个终端的所有者心跳线程。

    - **独立连接**：不复用业务连接，因此慢 snapshot / input / close 不会拖住心跳；
    - **稳定 client_id**：跨重连不变（同 id 续约不增 generation，见组合边界 I4）；
    - 单线程串行、每轮有界（``heartbeat`` 自身带 timeout_ms）；失败只计数与记
      类型名，连续失败超过死期即如实标记 ``lost``（不掩盖、不静默重试风暴）。
    """

    def __init__(
        self,
        *,
        client_factory: Callable[..., Any],
        terminal_id: str,
        data_root: Path,
        client_id: str,
        interval: float = DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
        grace: float = DEFAULT_LEASE_GRACE_SECONDS,
        request_timeout_ms: int = 2000,
    ) -> None:
        self.terminal_id = str(terminal_id)
        self._client_factory = client_factory
        self._data_root = data_root
        self.client_id = str(client_id)
        self.interval = max(0.05, float(interval))
        self.grace = max(self.interval, float(grace))
        self._request_timeout_ms = int(request_timeout_ms)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._client: Any | None = None
        self._consecutive_failures = 0
        self.beats = 0
        self.failures = 0
        self.last_error_type: str | None = None
        self.lost = False

    # -- 生命周期 ------------------------------------------------------
    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._loop, daemon=True, name=f"pan-terminal-hb-{self.terminal_id[-8:]}"
            )
            self._thread.start()

    def stop(self, timeout: float = 2.0) -> bool:
        self._stop.set()
        thread = self._thread
        joined = thread is None or thread.join(timeout=max(0.0, float(timeout)))
        if joined:
            self._release()
        return joined

    # -- 状态 ----------------------------------------------------------
    def describe(self) -> dict[str, Any]:
        return {
            "client_id": self.client_id,
            "interval_seconds": self.interval,
            "grace_seconds": self.grace,
            "beats": int(self.beats),
            "failures": int(self.failures),
            "consecutive_failures": int(self._consecutive_failures),
            "last_error_type": self.last_error_type,
            "lost": bool(self.lost),
            "running": bool(self._thread is not None and self._thread.is_alive()),
        }

    # -- 内部 ----------------------------------------------------------
    def _loop(self) -> None:
        while not self._stop.is_set():
            self._beat_once()
            self._stop.wait(self.interval)

    def _beat_once(self) -> None:
        client = self._ensure_client()
        if client is None:
            self._register_failure("attach-failed")
            return
        try:
            result = client.heartbeat(timeout_ms=self._request_timeout_ms)
        except Exception as exc:  # noqa: BLE001 - 只记类型名
            self._register_failure(type(exc).__name__)
            self._release()
            return
        status = str((result or {}).get("status") or "")
        if status == "ok":
            self.beats += 1
            self._consecutive_failures = 0
            self.lost = False
            return
        # closing / 其他非 ok 状态：如实记录（不当作成功，也不当作进程已死）。
        self._register_failure(f"status:{status[:24]}" if status else "status:unknown")

    def _ensure_client(self) -> Any | None:
        with self._lock:
            if self._client is not None:
                return self._client
        try:
            client = self._client_factory(
                self.terminal_id,
                data_root=self._data_root,
                client_id=self.client_id,
                connect_timeout=self.grace,
                request_timeout_ms=self._request_timeout_ms,
            )
            attach = getattr(client, "attach", None)
            if callable(attach):
                attach()
        except Exception:  # noqa: BLE001 - 只记类型名，失败在调用方计数
            return None
        with self._lock:
            self._client = client
        return client

    def _register_failure(self, error_type: str) -> None:
        self.failures += 1
        self._consecutive_failures += 1
        self.last_error_type = str(error_type)[:64]
        if self._consecutive_failures * self.interval >= self.grace:
            self.lost = True

    def _release(self) -> None:
        with self._lock:
            client, self._client = self._client, None
        if client is None:
            return
        try:
            client.release_connection()
        except Exception:  # noqa: BLE001 - 释放失败不改变"已释放"事实
            pass


# ══════════════════════════════════════════════════════════════════════════
# attachment 通道代理（TerminalChannel：write / resize）
# ══════════════════════════════════════════════════════════════════════════


class _ChannelProxy:
    """把 ``AttachmentRegistry`` 的 write/resize 转发到 runner IPC 连接。

    ``AttachmentRegistry`` 在**每终端锁内**完成"校验 lease → 调用本对象"，
    因此撤销后不会再产生新写（零新写保证来自该临界区，不来自本对象）。
    """

    def __init__(self, service: "TerminalService", terminal_id: str) -> None:
        self._service = service
        self.terminal_id = str(terminal_id)
        self._lock = threading.Lock()
        self._last_resize: dict[str, Any] = {"rows": None, "cols": None, "payload": {}}
        self._last_input: dict[str, Any] = {}
        self.writes = 0
        self.resizes = 0

    def write(self, data: bytes) -> int:
        result = self._service._ipc_input(self.terminal_id, data)
        with self._lock:
            self._last_input = dict(result)
        self.writes += 1
        return int(result.get("size") or 0)

    def last_input(self) -> dict[str, Any]:
        """取回**刚才那次** input 的接受结果（单控制写者下与调用严格对应）。"""
        with self._lock:
            return dict(self._last_input)

    def resize(self, rows: int, cols: int) -> bool:
        payload = self._service._ipc_resize(self.terminal_id, rows, cols)
        with self._lock:
            self._last_resize = {
                "rows": int(rows),
                "cols": int(cols),
                "payload": dict(payload),
            }
        self.resizes += 1
        return str(payload.get("status") or "") == "ok"

    def last_resize(self, rows: int, cols: int) -> dict[str, Any]:
        """取回**刚才那次** resize 的分列确认（单控制写者下与调用严格对应）。"""
        with self._lock:
            if self._last_resize["rows"] == int(rows) and self._last_resize["cols"] == int(cols):
                return dict(self._last_resize["payload"])
        return {}

    def describe(self) -> dict[str, Any]:
        with self._lock:
            last = dict(self._last_resize)
        return {
            "writes": int(self.writes),
            "resizes": int(self.resizes),
            "last_resize_rows": last.get("rows"),
            "last_resize_cols": last.get("cols"),
        }


# ══════════════════════════════════════════════════════════════════════════
# 每终端运行期状态
# ══════════════════════════════════════════════════════════════════════════


@dataclass
class _TerminalState:
    """服务进程自持的运行期对象（**不**进 registry；registry 只是数据层）。"""

    terminal_id: str
    record: TerminalRecord
    #: 业务连接（读/输入/尺寸/快照/停止）。
    client: Any | None = None
    #: 自有 spawn 句柄派生出的 launcher 进程（只代表**我们派生的那个进程**）。
    process: Any | None = None
    #: spawn 时的 Popen pid（**非** runner 身份权威；仅诊断与交叉核验）。
    spawn_pid: int | None = None
    #: hello 自证 + 内核核验后的 runner 身份（权威）。
    runner_pid: int | None = None
    runner_filetime: int | None = None
    heartbeat: _Heartbeat | None = None
    channel: _ChannelProxy | None = None
    close_call: _BoundedCall | None = None
    close_reason: str | None = None
    #: 本终端已重发 stop 的次数（F2 有界防风暴）。
    stop_resends: int = 0
    #: r3：只串行化"判定+重发"这一步的**独立**锁（不包裹阻塞 stop 调用）。
    close_op_lock: threading.Lock = field(default_factory=threading.Lock)
    lock: threading.RLock = field(default_factory=threading.RLock)
    attached: bool = False


# ══════════════════════════════════════════════════════════════════════════
# 服务
# ══════════════════════════════════════════════════════════════════════════


class TerminalService:
    """终端服务控制层（同步面）。

    **线程纪律（公开 sync 面的既定选择）**：本层公开**同步** API，调用方
    （未来的 REST/WS/MCP 适配器）**不得**在事件循环线程里直接调用本层阻塞原语，
    必须 ``await asyncio.to_thread(...)`` 或丢到工作线程；本层自己只用两类线程：
    每终端一个**心跳线程**与每终端最多一个**有界 close worker**（跨重试复用、
    不叠加）。阻塞预算见 ``docs/design/PAN_TERMINAL_SERVICE_INTERFACES_20261003.md``。

    **可注入原语**（仅为确定性测试；默认生产路径真实）：
    ``registry`` / ``secret_store`` / ``spawn_launcher`` / ``client_factory`` /
    ``identity_probe``。注入面只影响"谁来执行原语"，不改变本层的判定纪律。
    """

    def __init__(
        self,
        root: str | os.PathLike[str] | None = None,
        *,
        registry: TerminalRegistry | None = None,
        secret_store: Any | None = None,
        spawn_launcher: Callable[..., Any] | None = None,
        client_factory: Callable[..., Any] | None = None,
        identity_probe: Callable[[int], Any] | None = None,
        capacity: int = DEFAULT_CAPACITY,
        heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
        lease_grace: float = DEFAULT_LEASE_GRACE_SECONDS,
        stop_confirm: float = DEFAULT_STOP_CONFIRM_SECONDS,
        shutdown_budget: float = DEFAULT_SHUTDOWN_BUDGET_SECONDS,
        startup_timeout: float = DEFAULT_STARTUP_TIMEOUT_SECONDS,
        shell_argv: Sequence[str] | None = None,
        python_argv: Sequence[str] | None = None,
        repo_root: str | os.PathLike[str] | None = None,
        log_stderr: bool = True,
    ) -> None:
        self.root = Path(root) if root is not None else self._default_root()
        self.capacity = max(1, int(capacity))
        self.heartbeat_interval = max(0.05, float(heartbeat_interval))
        self.lease_grace = max(self.heartbeat_interval, float(lease_grace))
        self.stop_confirm = max(0.1, float(stop_confirm))
        self.shutdown_budget = max(0.1, float(shutdown_budget))
        self.startup_timeout = max(1.0, float(startup_timeout))
        self.shell_argv = tuple(str(item) for item in shell_argv) if shell_argv else None
        self.python_argv = tuple(str(item) for item in python_argv) if python_argv else None
        self.repo_root = Path(repo_root) if repo_root is not None else _REPO_ROOT
        self.log_stderr = bool(log_stderr)

        self._registry = registry if registry is not None else TerminalRegistry(self.root)
        self._secret_store = secret_store
        self._spawn_launcher_impl = spawn_launcher
        self._client_factory_impl = client_factory
        self._identity_probe = identity_probe

        self._states: dict[str, _TerminalState] = {}
        self._global_lock = threading.RLock()
        self._admission_lock = threading.Lock()
        self._shutdown_done = False
        #: F9：shutdown 一开始即置位，与并发 ``create`` 在 ``_admission_lock`` 内线性化。
        self._closing_down = False
        #: r3：无锁的关门信号——取锁超时被跳过时也保证 create 被拒（不丢关门请求）。
        self._closing_down_event = threading.Event()
        #: F3：本实例已尝试过"遗留记录重发 stop"的终端（避免反复打扰 runner）。
        self._persisted_stop_attempts: set[str] = set()
        self._events: list[tuple[str, str]] = []

    # ------------------------------------------------------------ 基础设施
    @staticmethod
    def _default_root() -> Path:
        value = os.environ.get("PAN_TERMINALS_DIR")
        return Path(value).expanduser() if value else TerminalRegistry().root

    @property
    def registry(self) -> TerminalRegistry:
        return self._registry

    def _store(self) -> Any:
        """延迟构造 ``SecretStore``（构造需 Windows；不在导入/构造期触发）。"""
        if self._secret_store is None:
            from . import secret_store as secret_store_module

            self._secret_store = secret_store_module.SecretStore(self.root)
        return self._secret_store

    def _note(self, event: str, detail: str = "") -> None:
        """有界脱敏事件（静态串/类型名；上限 ``MAX_DIAGNOSTIC_EVENTS``）。"""
        with self._global_lock:
            self._events.append((str(event)[:48], str(detail)[:64]))
            if len(self._events) > MAX_DIAGNOSTIC_EVENTS:
                del self._events[: len(self._events) - MAX_DIAGNOSTIC_EVENTS]

    def _note_error(self, event: str, exc: BaseException) -> None:
        self._note(event, type(exc).__name__)

    def _client_factory(self) -> Callable[..., Any]:
        if self._client_factory_impl is not None:
            return self._client_factory_impl
        from . import runner_client

        return runner_client.RunnerClient

    def _probe(self, pid: int) -> Any:
        """身份探针（默认 ``win_pipe.probe_process``；延迟导入）。"""
        if self._identity_probe is not None:
            return self._identity_probe(int(pid))
        from . import win_pipe

        return win_pipe.probe_process(int(pid))

    def _owner_client_id(self, terminal_id: str) -> str:
        """稳定所有者 client_id（按终端确定 → 跨重连不变）。"""
        return f"pan-owner-{terminal_id}"

    # ------------------------------------------------------------ 公共查询
    def list(self) -> list[dict[str, Any]]:
        """列出全部记录（公共视图：**不含 pipe/token**）。"""
        views = [self._view(record) for record in self._registry.list()]
        views.sort(key=lambda item: (item.get("created_at") or 0.0, item.get("terminal_id") or ""))
        return views

    def get(self, terminal_id: str) -> dict[str, Any]:
        """取单条记录的公共视图。"""
        return self._view(self._registry.get(terminal_id))

    def describe(self) -> dict[str, Any]:
        """服务层诊断（静态字段 + 计数；无秘密）。"""
        with self._global_lock:
            states = dict(self._states)
            events = tuple(self._events)
        return {
            "root_exists": self.root.exists(),
            "capacity": self.capacity,
            "attached": sorted(tid for tid, st in states.items() if st.attached),
            "heartbeats": {tid: st.heartbeat.describe() for tid, st in states.items() if st.heartbeat},
            "shutdown_done": bool(self._shutdown_done),
            "events": [{"event": name, "detail": detail} for name, detail in events],
        }

    def _view(self, record: TerminalRecord) -> dict[str, Any]:
        """公共视图：白名单字段；**不含 pipe / token / 秘密内容**。

        ``process_created_at_filetime`` 以十进制**字符串**给出（raw64 ≈ 1.3e17
        超过 JS 安全整数；与 ``identity.filetime_json`` 口径一致）。
        """
        with self._global_lock:
            state = self._states.get(record.terminal_id)
        view: dict[str, Any] = {
            "terminal_id": record.terminal_id,
            "status": record.status.value,
            "owner": record.owner,
            "rows": int(record.rows),
            "cols": int(record.cols),
            "pid": record.pid,
            "process_created_at_filetime": (
                str(record.process_created_at_filetime)
                if record.process_created_at_filetime is not None
                else None
            ),
            "detached": bool(record.detached),
            "detached_at": record.detached_at,
            "scope": record.scope.as_dict(),
            "exit": {"code": record.exit_code, "reason": record.exit_reason},
            "lease_grace_seconds": record.lease_grace_seconds,
            "created_by": record.created_by,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "attached": bool(state.attached) if state is not None else False,
            "authorization": "scope-is-metadata-not-permission",
        }
        if state is not None and state.heartbeat is not None:
            view["heartbeat"] = {
                "client_id": state.heartbeat.client_id,
                "beats": int(state.heartbeat.beats),
                "lost": bool(state.heartbeat.lost),
            }
        return view

    # ------------------------------------------------------------ create
    def create(
        self,
        *,
        rows: int = 24,
        cols: int = 80,
        cwd: str | os.PathLike[str] | None = None,
        workspace_id: str | None = None,
        session_id: str | None = None,
        context: ServiceContext | None = None,
        terminal_id: str | None = None,
        shell_argv: Sequence[str] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """创建终端：先持久化记录 → 派生生产 launcher → 四门确认 → 发布 RUNNING。

        - 容量准入（默认 8）在**记录写入之前**，超限零状态变化；
        - ``terminal_id`` 缺省由 registry 生成；重复 id 不覆盖既有记录/秘密
          （改用新 id 重试，有界）；
        - 记录先落盘（``starting``），四门确认后才改 ``running``；中途失败
          **保留记录与 owner** 供重试/reconcile，**不删秘密**；
        - 默认 shell = ``cmd.exe /q /d``（launcher/runner 默认）；``cwd`` 与
          ``shell_argv`` 可选覆盖（真实 cwd 接线）。
        """
        ctx = context or ServiceContext()
        rows = int(rows)
        cols = int(cols)
        if not (1 <= rows <= 500) or not (1 <= cols <= 1000):
            raise StartupFailed("invalid-size", error_type="ValueError")
        budget = self.startup_timeout if timeout is None else max(1.0, float(timeout))
        effective_shell = tuple(str(item) for item in shell_argv) if shell_argv else self.shell_argv

        record = self._admit(
            rows=rows,
            cols=cols,
            workspace_id=workspace_id,
            session_id=session_id,
            created_by=ctx.created_by,
            terminal_id=terminal_id,
        )
        terminal_id = record.terminal_id
        self._note("create-record-persisted", terminal_id)

        state = _TerminalState(terminal_id=terminal_id, record=record)
        with self._global_lock:
            self._states[terminal_id] = state

        try:
            self._start_terminal(state, cwd=cwd, shell_argv=effective_shell, budget=budget)
        except StartupFailed:
            self._note("create-startup-failed", terminal_id)
            raise
        except Exception as exc:  # noqa: BLE001 - 归一为静态 reason + 类型名
            self._note_error("create-startup-error", exc)
            self._mark_unproven(state, "startup-internal", type(exc).__name__)
            raise StartupFailed("startup-internal", error_type=type(exc).__name__) from None
        return self._view(self._registry.get(terminal_id))

    # -- create 分步 ---------------------------------------------------
    def _admit(
        self,
        *,
        rows: int,
        cols: int,
        workspace_id: str | None,
        session_id: str | None,
        created_by: str,
        terminal_id: str | None = None,
    ) -> TerminalRecord:
        """        容量准入 + 记录持久化（**同一临界区**，并发不得超限）。

        计数与写入必须原子：若只在计数后释放锁，并发 create 会各自看到"未超限"
        而同时落盘（实测 6/3）。这里在 ``_admission_lock`` 内完成"计数 → 分配
        id → 写记录"，因此**超限时零记录、零进程**。

        F9：``shutdown`` 在**同一把锁**内置位 ``_closing_down``，因此"关准入"与
        并发 ``create`` **线性化**——不会产生既没被 shutdown 收敛、又被放过的新终端。
        """
        with self._admission_lock:
            # r3：Event 与标志是"或"关系——即便取锁超时被跳过，create 仍必被拒。
            if self._closing_down or self._closing_down_event.is_set():
                # 已进入/完成 shutdown：拒绝新终端（不产生无人收敛的记录）。
                raise ServiceClosingDown("service-closing-down")
            live = sum(
                1 for record in self._safe_records() if record.status not in _TERMINAL_STATUSES
            )
            if live >= self.capacity:
                raise CapacityExceeded("capacity-exceeded")
            candidate = terminal_id or self._registry.new_terminal_id()
            record = TerminalRecord(
                terminal_id=candidate,
                owner="service",
                status=RuntimeState.STARTING,
                rows=rows,
                cols=cols,
                # scope **仅元数据**，不是权限；created_by 来自调用上下文。
                scope=TerminalScope(workspace_id=workspace_id, session_id=session_id),
                lease_grace_seconds=self.lease_grace,
                created_by=str(created_by)[:32],
            )
            try:
                return self._registry.create(record)
            except TerminalExistsError:
                if terminal_id is not None:
                    # 调用方显式指定了已存在的 id：不覆盖既有记录/秘密，改用新 id。
                    replacement = TerminalRecord(
                        terminal_id=self._registry.new_terminal_id(),
                        owner="service",
                        status=RuntimeState.STARTING,
                        rows=rows,
                        cols=cols,
                        scope=TerminalScope(workspace_id=workspace_id, session_id=session_id),
                        lease_grace_seconds=self.lease_grace,
                        created_by=str(created_by)[:32],
                    )
                    return self._registry.create(replacement)
                raise

    def _safe_records(self) -> list[TerminalRecord]:
        try:
            return self._registry.list()
        except Exception as exc:  # noqa: BLE001 - 损坏记录不得被静默当作"没终端"
            self._note_error("registry-list-failed", exc)
            raise

    def _start_terminal(
        self,
        state: _TerminalState,
        *,
        cwd: str | os.PathLike[str] | None,
        shell_argv: Sequence[str] | None,
        budget: float,
    ) -> None:
        terminal_id = state.terminal_id
        store = self._store()
        store.ensure_secrets_dir()
        secret_file = store.secret_path(terminal_id)

        # ① 派生生产 launcher（argv 只有 id/secret 路径/尺寸/cwd/shell；**无 token**）。
        try:
            process = self._spawn(state, secret_file=secret_file, cwd=cwd, shell_argv=shell_argv)
        except Exception as exc:  # noqa: BLE001
            self._mark_unproven(state, "launcher-spawn-failed", type(exc).__name__)
            raise StartupFailed("launcher-spawn-failed", error_type=type(exc).__name__) from None
        state.process = process
        state.spawn_pid = int(getattr(process, "pid", 0) or 0) or None

        # ② hello 自证 + 同句柄 pid/raw FILETIME/Wait 内核核验 → 才写 DPAPI 秘密。
        try:
            bootstrap = store.wait_for_bootstrap_identity(terminal_id, timeout=budget)
        except Exception as exc:  # noqa: BLE001
            self._mark_unproven(state, "bootstrap-unverified", type(exc).__name__)
            raise StartupFailed("bootstrap-unverified", error_type=type(exc).__name__) from None
        state.runner_pid = int(bootstrap.pid)
        state.runner_filetime = int(bootstrap.filetime)

        from . import runner_client

        try:
            runner_client.complete_bootstrap(store, terminal_id, timeout=budget)
        except Exception as exc:  # noqa: BLE001
            self._mark_unproven(state, "secret-write-failed", type(exc).__name__)
            raise StartupFailed("secret-write-failed", error_type=type(exc).__name__) from None

        # ③ 端点身份核验 + HMAC 握手成功后才允许业务请求。
        client = self._client_factory()(
            terminal_id,
            data_root=self.root,
            connect_timeout=self.lease_grace,
            request_timeout_ms=max(2000, int(self.stop_confirm * 1000)),
            close_timeout_ms=int(self.stop_confirm * 1000) + 10_000,
            client_id=self._owner_client_id(terminal_id),
        )
        try:
            client.attach()
            described = client.describe()
        except Exception as exc:  # noqa: BLE001
            state.client = client
            self._mark_unproven(state, "attach-failed", type(exc).__name__)
            raise StartupFailed("attach-failed", error_type=type(exc).__name__) from None
        state.client = client
        state.attached = True

        # ④ 四门确认（runner 自报 running）后才发布 RUNNING。
        if str(described.get("runner_state") or "") != "running":
            self._mark_unproven(state, "gates-not-confirmed", "runner-state-not-running")
            raise StartupFailed("gates-not-confirmed", error_type="runner-state-not-running")

        updated = self._registry.update(
            terminal_id,
            lambda record: self._apply_running(record, state),
        )
        state.record = updated

        # ⑤ 独立心跳（自己的连接 + 稳定 client_id）。
        heartbeat = _Heartbeat(
            client_factory=self._client_factory(),
            terminal_id=terminal_id,
            data_root=self.root,
            client_id=self._owner_client_id(terminal_id),
            interval=self.heartbeat_interval,
            grace=self.lease_grace,
        )
        state.heartbeat = heartbeat
        state.channel = _ChannelProxy(self, terminal_id)
        heartbeat.start()
        self._note("terminal-running", terminal_id)

    def _apply_running(self, record: TerminalRecord, state: _TerminalState) -> TerminalRecord:
        record.status = RuntimeState.RUNNING
        record.owner = "service"
        # runner 身份 = hello 自证 + 内核核验（**不是** Popen.pid）。
        record.pid = state.runner_pid
        record.process_created_at_filetime = state.runner_filetime
        record.lease_grace_seconds = self.lease_grace
        return record

    def _mark_unproven(self, state: _TerminalState, reason: str, error_type: str | None) -> None:
        """启动/关闭未获证明：**保留**记录与 owner，可重试；不删秘密。

        F6：**不**无界取 ``state.lock``（预算耗尽/等锁时也不许再挂死）——
        这里只做"记录写 + 内存态同步"，不需要临界区。
        """
        terminal_id = state.terminal_id
        self._note("unproven-retained", f"{terminal_id}:{reason}")
        try:
            self._registry.update(
                terminal_id,
                lambda record: self._apply_unproven(record, reason),
            )
        except Exception as exc:  # noqa: BLE001 - 记录写失败也不丢 owner 引用
            self._note_error("unproven-record-write-failed", exc)
        # F8：内存态与磁盘同步（否则 lease 门读陈旧的 running）。
        self._sync_state_record(state)

    @staticmethod
    def _apply_unproven(record: TerminalRecord, reason: str) -> TerminalRecord:
        """未证明时**不标 exited/lost**：停在 EXITING（或 CLEANUP_FAILED）可重试。"""
        if record.status not in (RuntimeState.EXITED, RuntimeState.LOST):
            record.status = RuntimeState.CLEANUP_FAILED
            record.exit_reason = reason
        return record

    # -- 派生（launcher） ----------------------------------------------
    def _spawn(
        self,
        state: _TerminalState,
        *,
        secret_file: Path,
        cwd: str | os.PathLike[str] | None,
        shell_argv: Sequence[str] | None,
    ) -> Any:
        if self._spawn_launcher_impl is not None:
            return self._spawn_launcher_impl(
                state.terminal_id,
                secret_file=secret_file,
                cwd=cwd,
                shell_argv=shell_argv,
                root=self.root,
            )
        return self._spawn_production_launcher(
            state.terminal_id,
            secret_file=secret_file,
            rows=state.record.rows,
            cols=state.record.cols,
            cwd=cwd,
            shell_argv=shell_argv,
        )

    def _launcher_argv(
        self,
        terminal_id: str,
        secret_file: Path,
        *,
        rows: int,
        cols: int,
        cwd: str | os.PathLike[str] | None = None,
        shell_argv: Sequence[str] | None = None,
    ) -> list[str]:
        """派生 argv：只有 id / secret 路径 / 尺寸 / 可选 cwd+shell；**无 token**。"""
        python = list(self.python_argv) if self.python_argv else self._resolve_python()
        argv = [
            *python,
            "-m",
            "packages.core.terminal.launcher",
            "--terminal-id",
            str(terminal_id),
            "--secret-file",
            str(secret_file),
            "--rows",
            str(int(rows)),
            "--cols",
            str(int(cols)),
        ]
        # 可选接线：缺省不传 = launcher/runner 保持既有默认（cmd.exe /q /d、无 cwd）。
        if cwd is not None:
            argv += ["--cwd", str(cwd)]
        if shell_argv:
            argv += ["--shell-argv", json.dumps([str(item) for item in shell_argv])]
        return argv

    @staticmethod
    def _resolve_python() -> list[str]:
        try:
            from ..config import resolve_pan_python_argv

            resolved = resolve_pan_python_argv()
            if resolved:
                return [str(item) for item in resolved]
        except Exception:  # noqa: BLE001 - 回落到当前解释器（不静默改语义）
            pass
        return [sys.executable]

    def _spawn_production_launcher(
        self,
        terminal_id: str,
        *,
        secret_file: Path,
        rows: int,
        cols: int,
        cwd: str | os.PathLike[str] | None,
        shell_argv: Sequence[str] | None,
    ) -> Any:
        """派生独立 launcher；请求脱离允许 breakaway 的外层 Job。

        DETACHED_PROCESS 只解除控制台关联，不证明 Job 独立。外层拒绝 breakaway
        时退回受限布局，runner 的真实 ambient-Job 探测仍决定 detach 是否可用。
        我们不在这里判定 runner 身份；身份权威来自 hello 自证 + 内核核验。
        """
        argv = self._launcher_argv(
            terminal_id, secret_file, rows=rows, cols=cols, cwd=cwd, shell_argv=shell_argv
        )
        env = {k: v for k, v in os.environ.items() if not k.startswith("PAN_TERMINAL_")}
        env["PYTHONIOENCODING"] = "utf-8"
        # 目标 cwd = 终端真实工作目录；解析 ``packages.core.terminal.launcher``
        # 需要仓库根在 sys.path（PYTHONPATH），二者互不冲突。
        workdir = str(self.repo_root)
        env["PYTHONPATH"] = (
            workdir + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        )
        kwargs: dict[str, Any] = {
            "cwd": workdir,
            "env": env,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        stderr_stream = None
        if self.log_stderr:
            log_path = self._stderr_log_path(terminal_id)
            stderr_stream = log_path.open("a", encoding="utf-8", errors="replace")
            kwargs["stderr"] = stderr_stream
        if os.name == "nt":
            kwargs["creationflags"] = (
                getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
                | getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)
            )
        try:
            try:
                return subprocess.Popen(argv, **kwargs)
            except OSError as exc:
                # CreateProcess 的拒绝没有创建子进程；只对这一已知策略拒绝回落。
                # 其他 spawn 错误必须原样传播，不能以重试掩盖路径/权限故障。
                if os.name != "nt" or getattr(exc, "winerror", None) != 5:
                    raise
                self._note("launcher-breakaway-denied", "ambient-job-restricted")
                kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
                return subprocess.Popen(argv, **kwargs)
        finally:
            # Popen 已复制/继承其所需句柄；父服务不长期持有每次创建的日志文件。
            if stderr_stream is not None:
                try:
                    stderr_stream.close()
                except OSError as exc:
                    # 子进程可能已成功创建；日志关闭故障不得丢掉其 owner 返回值。
                    self._note("launcher-parent-log-close-failed", type(exc).__name__)

    def _stderr_log_path(self, terminal_id: str) -> Path:
        directory = self.root / _SERVICE_LOG_DIRNAME
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{terminal_id}.stderr.log"

    # ------------------------------------------------------------ attachment
    def attach(
        self,
        terminal_id: str,
        client_id: str,
        *,
        role: str = LEASE_ROLE_OBSERVER,
        rows: int = 0,
        cols: int = 0,
    ) -> LeaseToken:
        """签发浏览器 attachment lease（**连接级**，与 IPC 所有者心跳分层）。"""
        state = self._require_state(terminal_id)
        if not state.attached or state.client is None:
            record = self._registry.get(terminal_id)
            if not record.detached or self._reconcile_live(state, record) != "alive":
                raise TerminalNotAttached("detached-reconnect-unconfirmed")
        if state.channel is None:
            raise TerminalNotAttached("channel-not-ready")
        token = self._attachments().attach(
            terminal_id, str(client_id), role=role, rows=int(rows), cols=int(cols)
        )
        self._note("attachment-issued", f"{terminal_id}:{role}")
        return token

    def release_attachment(self, token: LeaseToken) -> None:
        """断开 attachment：**只撤销 lease**，永不触碰 runtime 寿命。"""
        self._attachments().detach(token)
        self._note("attachment-released", token.terminal_id)

    def control_holder(self, terminal_id: str) -> dict[str, Any] | None:
        holder = self._attachments().control_holder(terminal_id)
        if holder is None:
            return None
        return {
            "client_id": holder.client_id,
            "role": holder.role,
            "generation": int(holder.generation),
        }

    def _attachments(self) -> AttachmentRegistry:
        registry = getattr(self, "_attachment_registry", None)
        if registry is None:
            registry = AttachmentRegistry(lookup=self._lease_lookup)
            self._attachment_registry = registry
        return registry

    def _lease_lookup(self, terminal_id: str) -> _ChannelProxy | None:
        """lease 目标：只有本实例已连上、且**当前记录**可写的终端才可写。

        F8：状态取**磁盘registry**（权威）而不是内存副本——否则 close 之后内存仍
        是陈旧的 ``running``，准入会**晚于** RPC 判据。内存副本被判定为陈旧时
        直接 fail-closed（不查盘也不放行）。
        """
        with self._global_lock:
            state = self._states.get(terminal_id)
        if state is None or state.channel is None or not state.attached:
            return None
        status = self._current_status(terminal_id, state)
        if status != RuntimeState.RUNNING.value:
            return None
        return state.channel

    def _current_status(self, terminal_id: str, state: _TerminalState) -> str:
        """当前权威状态：优先读磁盘；读失败时用内存副本（并保持 fail-closed）。"""
        try:
            return self._registry.get(terminal_id).status.value
        except Exception as exc:  # noqa: BLE001
            self._note_error("status-read-failed", exc)
            return state.record.status.value

    # ------------------------------------------------------------ 读/快照
    def read(
        self,
        terminal_id: str,
        cursor: int = 0,
        *,
        max_bytes: int | None = None,
    ) -> dict[str, Any]:
        """按游标读取输出。

        - **gap 不补零、不跳 cursor**：有 gap 就原样返回并附 fresh-view 提示；
        - 不自动 reset、不杀 PTY；``data`` 是"从 ``seq`` 开始"的事实。
        """
        state = self._require_state(terminal_id)
        client = self._require_client(state)
        result = client.read(int(cursor), max_bytes=max_bytes)
        gap = result.get("gap")
        first_retained = int(result.get("first_retained_seq") or 0)
        next_cursor = int(result.get("next_cursor") or cursor)
        return {
            "terminal_id": terminal_id,
            "data": result.get("data") or b"",
            "seq": int(result.get("seq") or cursor),
            "size": int(result.get("size") or 0),
            "next_cursor": next_cursor,
            "total_bytes": int(result.get("total_bytes") or 0),
            "first_retained_seq": first_retained,
            "truncated": bool(result.get("truncated")),
            "gap": list(gap) if isinstance(gap, (list, tuple)) and len(gap) == 2 else None,
            # 缺口/驱逐的显式提示：客户端必须走快照或明确降级 fresh-view。
            "fresh_view_required": bool(gap) or (next_cursor < first_retained),
            "cursor_advanced": next_cursor != int(cursor),
            "status": result.get("status"),
            "zero_fill": False,
        }

    def snapshot(self, terminal_id: str, *, timeout_ms: int = 5000) -> dict[str, Any]:
        """协议 A 快照：serialized 屏幕 + **applied cursor** + 降级/确认字段。

        诚实边界（组合边界 I2/I3）：``partial``/``degraded``/``note`` 原样透出；
        F5 字段 ``cursors_valid`` / ``reset_unconfirmed`` **保持 bool 或 null**；
        ``diagnostics.reasons`` 为空**不**升级为 full；applied cursor 被驱逐时给
        显式 fresh-view 提示，**不**自动 ``reset_baseline``、**不**杀 PTY。
        """
        state = self._require_state(terminal_id)
        client = self._require_client(state)
        result = client.snapshot(timeout_ms=int(timeout_ms))
        out: dict[str, Any] = {
            "terminal_id": terminal_id,
            "status": result.get("status"),
            "serialized_screen": result.get("serialized_screen"),
            "cursor": result.get("cursor"),
            "rows": int(result.get("rows") or 0),
            "cols": int(result.get("cols") or 0),
            "fidelity": result.get("fidelity"),
            "recovery": result.get("recovery"),
            "feed_lag": result.get("feed_lag"),
            "note": result.get("note"),
            "engine": result.get("engine"),
            "cursors_valid": self._bool_or_none(result.get("cursors_valid")),
            "reset_unconfirmed": self._bool_or_none(result.get("reset_unconfirmed")),
            "diagnostics": result.get("diagnostics"),
            "auto_reset_applied": False,
        }
        applied = result.get("cursor")
        out["applied_evicted"] = self._applied_evicted(client, applied)
        out["continuation_hint"] = self._continuation_hint(result, out["applied_evicted"])
        return out

    def _applied_evicted(self, client: Any, applied: Any) -> bool | None:
        """applied cursor 是否已被输出窗口驱逐（**不自动 reset、不杀 PTY**）。

        snapshot 响应本身不带 ``first_retained_seq``，因此用一次轻量 ``describe``
        取保留窗口起点；取不到就返回 ``None``（unknown 不造值、不声称可续流）。
        """
        if not isinstance(applied, int) or applied < 0:
            return None
        try:
            first_retained = int(client.describe().get("first_retained_seq") or 0)
        except Exception as exc:  # noqa: BLE001 - 诊断失败不升级为"已驱逐"
            self._note_error("first-retained-unavailable", exc)
            return None
        return applied < first_retained

    @staticmethod
    def _bool_or_none(value: Any) -> bool | None:
        """F5 确认字段：只接受真 bool；缺失/异常一律 ``None``（unknown 不造值）。"""
        return value if isinstance(value, bool) else None

    @staticmethod
    def _continuation_hint(result: Mapping[str, Any], applied_evicted: bool | None) -> str:
        """续流建议（**不**升级保真度）。"""
        if str(result.get("status") or "") == "starting":
            return "retry-after-start"
        if applied_evicted is True:
            return "fresh-view-required"
        recovery = str(result.get("recovery") or "")
        if recovery in ("none", "degraded"):
            return "fresh-view-required"
        if result.get("cursors_valid") is not True or result.get("reset_unconfirmed") is True:
            return "fresh-view-required"
        if str(result.get("fidelity") or "") != "full":
            return "partial-view"
        return "continue-stream"

    # ------------------------------------------------------------ 写/尺寸
    def input(
        self,
        terminal_id: str,
        token: LeaseToken,
        data: bytes,
        *,
        seq: int | None = None,
    ) -> dict[str, Any]:
        """经 attachment **控制 lease** 写入（同临界区校验+调用；撤销后零新写）。"""
        state = self._require_state(terminal_id)
        if token.terminal_id != terminal_id:
            raise NotControlLeaseError("token-terminal-mismatch")
        if token.role != LEASE_ROLE_CONTROL:
            raise NotControlLeaseError("observer-cannot-input")
        payload = bytes(data)
        registry = self._attachments()
        # send() 内部：lease 校验与写入在同一（每终端）临界区 —— 撤销后不再产生新写。
        written = registry.send(token, payload)
        result = state.channel.last_input() if state.channel is not None else {}
        out = {
            "terminal_id": terminal_id,
            "size": int(written),
            "status": result.get("status"),
            "accepted": result.get("ok"),
            "lease_generation": int(token.generation),
            "role": token.role,
        }
        if seq is not None:
            out["seq"] = int(seq)
        if state.channel is not None:
            out["channel"] = state.channel.describe()
        return out

    def resize(
        self,
        terminal_id: str,
        token: LeaseToken,
        rows: int,
        cols: int,
    ) -> dict[str, Any]:
        """经控制 lease 改尺寸：PTY 与引擎确认**分列**，不宣称三方一致。"""
        state = self._require_state(terminal_id)
        if token.terminal_id != terminal_id:
            raise NotControlLeaseError("token-terminal-mismatch")
        if token.role != LEASE_ROLE_CONTROL:
            raise NotControlLeaseError("observer-cannot-resize")
        registry = self._attachments()
        accepted = registry.resize(token, int(rows), int(cols))
        payload: dict[str, Any] = {}
        if state.channel is not None:
            payload = state.channel.last_resize(rows, cols)
        described = payload.get("describe") or {}
        emulator = described.get("emulator_resize")
        return {
            "terminal_id": terminal_id,
            "requested": {"rows": int(rows), "cols": int(cols)},
            "accepted": bool(accepted),
            "status": payload.get("status"),
            # 分列事实：PTY 侧（runner 接受）与引擎侧（仿真器确认）**各自**报告。
            "pty": {
                "accepted": str(payload.get("status") or "") == "ok",
                "rows": int(payload.get("rows") or rows),
                "cols": int(payload.get("cols") or cols),
            },
            "engine": {
                "confirmed": bool(emulator.get("ok")) if isinstance(emulator, Mapping) else None,
                "detail": emulator if isinstance(emulator, Mapping) else None,
            },
            "three_way_agreement": None,
            "lease_generation": int(token.generation),
        }

    # -- IPC 直通（供 _ChannelProxy 与 close 使用） ----------------------
    def _ipc_input(self, terminal_id: str, data: bytes) -> dict[str, Any]:
        state = self._require_state(terminal_id)
        client = self._require_client(state)
        return dict(client.input(bytes(data)))

    def _ipc_resize(self, terminal_id: str, rows: int, cols: int) -> dict[str, Any]:
        state = self._require_state(terminal_id)
        client = self._require_client(state)
        return dict(client.resize(int(rows), int(cols)))

    # ------------------------------------------------------------ close
    def close(
        self,
        terminal_id: str,
        *,
        reason: str = EXIT_REASON_EXPLICIT_CLOSE,
    ) -> dict[str, Any]:
        """显式关闭：先记 CLOSING，再停 runner，等**真实收尾确认**。

        三项证明齐备才记 ``exited`` 并删除秘密：
        ① runner 侧收尾确认（stop 响应 ``exited``）；② launcher 引擎收尾
        （launcher-status ``engine.cleanup.converged`` 为真 bool）；③ **经身份核验的
        进程退出**（我们**自己派生**的 launcher 句柄）。

        任一项缺失 → 停在 ``EXITING``/``CLEANUP_FAILED`` + **静态分类** reason
        （``F10``：区分缺哪一项证明），**保留** owner/record/secret 可重试；
        **不**用 ``caller_responsible`` 绕过。``budget`` 覆盖取锁与全部等待。
        """
        state = self._require_state(terminal_id)
        report = self._close_state(state, reason=reason, budget=self.stop_confirm)
        if report["status"] == "exited":
            return self._view(self._registry.get(terminal_id))
        # F10：reason 用 close-outcome 的**内部静态分类**，而非调用方传入值，
        # 让调用方能据reason 判断"该重试"还是"该升级处置"。
        raise CleanupUnconfirmed(
            str(report.get("missing") or report.get("reason") or "cleanup-unconfirmed"),
            error_type=report.get("error_type"),
        )

    def _close_state(self, state: _TerminalState, *, reason: str, budget: float) -> dict[str, Any]:
        """关闭序列（可重入）。

        **可追踪 worker 只覆盖阻塞的 stop 调用**：

        - 在途 → 复用同一 worker（单飞，不叠加）；
        - 已完成但**未确认**（``closing`` / 异常）且证据仍未齐备 → 下一轮**有界幂等**
          重发（runner 侧对重复 stop 幂等：已 ``exited`` 返回 ``exited``）；
        - **已确认成功** → 不机械重复发 stop。

        每轮**重新评估**外部证据（launcher 引擎收尾、经身份核验的进程退出）。
        ``budget`` 是**总预算**（含取锁等待），用单调 deadline 贯穿。
        """
        terminal_id = state.terminal_id
        deadline = time.monotonic() + max(0.0, float(budget))

        # ---- 取 state 锁也纳入预算（F6：无界等锁 = 预算形同虚设）----
        if not state.lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
            self._mark_unproven(state, "close-lock-wait-timeout", None)
            return {
                "terminal_id": terminal_id,
                "status": "cleanup-failed",
                "reason": reason,
                "missing": "close-lock-wait-timeout",
                "proven": False,
            }
        try:
            if state.record.status is RuntimeState.EXITED:
                return {"terminal_id": terminal_id, "status": "exited", "reason": "already-exited"}
            # ① 先记录 CLOSING（映射既有合法枚举 EXITING；不引入新枚举值）。
            try:
                self._registry.update(
                    terminal_id, lambda record: self._apply_closing(record, reason)
                )
            except Exception as exc:  # noqa: BLE001 - 记录写失败仍继续尝试收尾
                self._note_error("closing-record-write-failed", exc)
            # F8：内存态与磁盘**同步**（否则 lease 门读陈旧的 running）。
            self._sync_state_record(state)
            state.close_reason = str(reason)
            if state.close_call is None:
                state.close_call = _BoundedCall(
                    lambda: self._request_stop(state, reason=reason),
                    name=f"close-{terminal_id[-8:]}",
                )
            call = state.close_call
        finally:
            state.lock.release()

        # 停心跳也占用剩余预算（F6）。
        self._stop_heartbeat(state, deadline=deadline)

        finished, result, error_type = call.run(max(0.0, deadline - time.monotonic()))
        if not finished:
            self._mark_unproven(state, "close-in-flight", None)
            return {
                "terminal_id": terminal_id,
                "status": "closing",
                "reason": reason,
                "missing": "close-in-flight",
                "proven": False,
            }
        evidence = dict(result or {})
        stop_confirmed = bool(evidence.get("runner_confirmed"))

        # ---- r3：同终端的重发决定必须**串行** ----
        # 两个 caller 各自 close 时，若都判定"该重发"就会各自 ``reset()`` 相互覆盖，
        # 造成结果错配/叠加。用一把**独立的 close-operation 锁**只串行化"判定+重发"，
        # 锁等待及锁内 call.run 的有界等待共用本次 deadline。
        # 真正的 stop 始终由 _BoundedCall 的 worker 执行，不在调用者线程同步调用。
        if not stop_confirmed and self._close_op_lock(state).acquire(
            timeout=max(0.0, deadline - time.monotonic())
        ):
            try:
                # The first result predates acquiring this lock. Another caller may
                # have completed a retry meanwhile; consume its current result before
                # deciding to reset. Never overwrite a success using stale evidence.
                current_finished, current_result, current_error = call.run(0.0)
                if not current_finished:
                    self._mark_unproven(state, "close-in-flight", None)
                    return {
                        "terminal_id": terminal_id, "status": "closing",
                        "reason": reason, "missing": "close-in-flight", "proven": False,
                    }
                evidence = dict(current_result or {})
                error_type = current_error
                stop_confirmed = bool(evidence.get("runner_confirmed"))
                if not stop_confirmed and self._should_resend_stop(state, call, evidence):
                    self._note("close-stop-resend", terminal_id)
                    # r3：``reset()`` 拒绝时**保留/重读原调用**，不读未定义变量、
                    # 不据此重发、更不假收敛。
                    if call.reset():
                        state.stop_resends += 1
                        finished2, result2, error_type2 = call.run(
                            max(0.0, deadline - time.monotonic())
                        )
                        if not finished2:
                            self._mark_unproven(state, "close-in-flight", None)
                            return {
                                "terminal_id": terminal_id,
                                "status": "closing",
                                "reason": reason,
                                "missing": "close-in-flight",
                                "proven": False,
                            }
                        evidence = dict(result2 or {})
                        error_type = error_type2
                        stop_confirmed = bool(evidence.get("runner_confirmed"))
                    else:
                        # 重发被拒：沿用**原调用**结果（``evidence``/``error_type``
                        # 保持第一次 ``call.run`` 的真实返回）。
                        self._note("close-stop-resend-refused", terminal_id)
            finally:
                self._close_op_lock(state).release()
        elif not stop_confirmed:
            # 锁等待超预算：如实按"未收敛"处理（不挂死、不假收敛）。
            self._note("close-op-lock-timeout", terminal_id)

        if error_type is not None and not stop_confirmed:
            self._mark_unproven(state, "close-worker-error", error_type)
            return {
                "terminal_id": terminal_id,
                "status": "cleanup-failed",
                "reason": reason,
                "missing": "close-worker-error",
                "error_type": error_type,
                "proven": False,
            }

        # 每轮重新评估外部证据（stop 已确认则不重复发）。
        evidence.update(self._launcher_status_evidence(state))
        evidence["process_exit"] = self._spawn_handle_exit_evidence(state)
        proven = bool(
            stop_confirmed
            and evidence.get("engine_converged")
            and (evidence.get("process_exit") or {}).get("exited")
        )
        outcome = {
            "terminal_id": terminal_id,
            "status": "exited" if proven else "cleanup-failed",
            "reason": str(reason),
            "proven": proven,
            "evidence": evidence,
        }
        if proven:
            self._finalize_exited(state, reason=reason, evidence=evidence)
        else:
            # F10：静态分类"缺哪一项证明"（不泄漏自由文本）。
            outcome["missing"] = self._missing_proof(evidence, stop_confirmed)
            self._mark_unproven(state, outcome["missing"], None)
        return outcome

    @staticmethod
    def _close_op_lock(state: _TerminalState) -> threading.Lock:
        """该终端的 close-operation 串行锁（惰性创建，兼容旧构造路径）。"""
        lock = getattr(state, "close_op_lock", None)
        if lock is None:
            lock = threading.Lock()
            state.close_op_lock = lock
        return lock

    @staticmethod
    def _should_resend_stop(
        state: _TerminalState, call: "_BoundedCall", evidence: Mapping[str, Any]
    ) -> bool:
        """是否允许本轮**重发** stop（F2）。

        条件（全部满足才重发，次数有界）：

        - 上一轮 stop **已结束**（不在途）——在途由单飞复用，不重发；
        - 上一轮**未确认**（``runner_confirmed`` 为假：``closing`` / 异常 / 非 exited）；
        - 未超过``MAX_STOP_RESENDS`` 上限（避免风暴）。
        """
        if call.in_flight:
            return False
        if evidence.get("runner_confirmed"):
            return False  # 已确认成功，不机械重复
        return state.stop_resends < MAX_STOP_RESENDS

    @staticmethod
    def _missing_proof(evidence: Mapping[str, Any], stop_confirmed: bool) -> str:
        """未收敛的**静态**分类（F10）：区分缺哪一项证明。

        纯枚举值，无自由文本、不含路径/异常消息；调用方据此判断重试或升级。
        """
        if not stop_confirmed:
            return "runner-stop-unconfirmed"
        if not evidence.get("engine_converged"):
            # launcher 引擎收尾未确证（含exit 6 / status 缺失或损坏）
            return "engine-cleanup-unproven"
        process_exit = evidence.get("process_exit")
        if not isinstance(process_exit, Mapping) or not process_exit.get("exited"):
            return "process-still-running"
        return "cleanup-unconfirmed"

    def _sync_state_record(self, state: _TerminalState) -> None:
        """把内存 ``state.record`` 与磁盘registry 同步（F8）。

        close/未证明路径只写磁盘会让内存副本**陈旧**（仍 ``running``），
        从而让 ``_lease_lookup`` 继续放行输入——准入门必须早于 RPC。
        """
        try:
            state.record = self._registry.get(state.terminal_id)
        except Exception as exc:  # noqa: BLE001 - 读失败保留原记录（不造值）
            self._note_error("state-record-sync-failed", exc)

    @staticmethod
    def _apply_closing(record: TerminalRecord, reason: str) -> TerminalRecord:
        """CLOSING → ``RuntimeState.EXITING``（计划 stopping 的合法映射）。"""
        if record.status not in (RuntimeState.EXITED, RuntimeState.LOST):
            record.status = RuntimeState.EXITING
            record.exit_reason = str(reason)
        return record

    def _request_stop(self, state: _TerminalState, *, reason: str) -> dict[str, Any]:
        """向 runner 发一次 stop 并等它自己的收尾报告（**只发一次**）。

        阻塞部分被 :class:`_BoundedCall` 追踪：重试复用同一在途/已完成调用，
        因此**不会**出现第二个 stop（迟到成功由缓存结果消费）。
        """
        evidence: dict[str, Any] = {"runner_confirmed": False, "engine_converged": False}
        client = state.client
        if client is None:
            evidence["runner_status"] = "not-attached"
            return evidence
        try:
            response = client.close(reason=str(reason))
        except Exception as exc:  # noqa: BLE001 - 只记类型名
            self._note_error("stop-request-failed", exc)
            response = {"status": "error", "error_type": type(exc).__name__}
        status = str(response.get("status") or "")
        evidence["runner_status"] = status
        evidence["runner_confirmed"] = status == "exited"
        if status == "exited":
            evidence["runner_exit"] = (response.get("describe") or {}).get("exit")
        return evidence

    def _launcher_status_evidence(self, state: _TerminalState) -> dict[str, Any]:
        """读 launcher-status（引擎收尾事实 + 身份交叉核验）。"""
        path = self.root / _LAUNCHER_STATUS_DIRNAME / f"{state.terminal_id}.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"engine_converged": False, "launcher_status": "absent"}
        except Exception as exc:  # noqa: BLE001 - 损坏状态文件不得当"已收尾"
            self._note_error("launcher-status-unreadable", exc)
            return {"engine_converged": False, "launcher_status": "corrupt"}
        engine = payload.get("engine") if isinstance(payload, Mapping) else None
        cleanup = engine.get("cleanup") if isinstance(engine, Mapping) else None
        converged = bool(isinstance(cleanup, Mapping) and cleanup.get("converged") is True)
        identity = payload.get("launcher_identity") if isinstance(payload, Mapping) else None
        identity_ok = self._identity_matches(
            identity,
            state.runner_pid,
            state.runner_filetime,
        )
        return {
            "engine_converged": converged and identity_ok,
            "launcher_status": str(payload.get("phase") or "unknown") if isinstance(payload, Mapping) else "unknown",
            "engine_retained": (cleanup or {}).get("retained") if isinstance(cleanup, Mapping) else None,
            "identity_match": identity_ok,
        }

    def _identity_matches(
        self, identity: Any, pid: int | None, filetime: int | None
    ) -> bool:
        """launcher-status 自报身份与 hello 自证身份**精确**一致才算交叉核验通过。"""
        if not isinstance(identity, Mapping):
            return False
        if pid is None or filetime is None:
            return False
        try:
            status_pid = int(identity.get("pid"))
            status_ft = int(identity.get("process_created_at_filetime"))
        except (TypeError, ValueError):
            return False
        return status_pid == int(pid) and status_ft == int(filetime)

    def _spawn_handle_exit_evidence(self, state: _TerminalState) -> dict[str, Any]:
        """经身份核验的进程退出证据（close 的第三项证明）。

        优先用**我们派生**的 ``Popen`` 句柄：它就是 ``CreateProcess`` 返回的那一个，
        对它判活是绑定证据（不是"再查一次 PID"，无PID 复用窗口）。

        **本实例不拥有该句柄时**（跨进程重启后的遗留记录）**不造证明**：退回到
        身份三态——只有 ``dead-confirmed``（pid+FILETIME 精确匹配且同 handle
        ``Wait`` 已退出）才算退出证据，并显式标注来源。其余一律"未证明"。
        """
        process = state.process
        if process is None:
            identity = self._identity_evidence(state.runner_pid, state.runner_filetime)
            if identity.get("status") == "dead-confirmed":
                return {
                    "exited": True,
                    "detail": "identity-retained-handle-dead",
                    "source": "identity-retained-handle",
                    "pid": identity.get("pid"),
                }
            return {
                "exited": False,
                "detail": "no-self-spawned-handle",
                "source": "none",
                "identity_status": str(identity.get("status") or "unattributable"),
            }
        poll = getattr(process, "poll", None)
        if not callable(poll):
            return {"exited": False, "detail": "handle-not-pollable", "source": "self-spawn-handle"}
        try:
            returncode = poll()
        except Exception as exc:  # noqa: BLE001
            self._note_error("spawn-handle-poll-failed", exc)
            return {"exited": False, "detail": "poll-failed", "source": "self-spawn-handle"}
        if returncode is None:
            return {
                "exited": False,
                "detail": "still-running",
                "returncode": None,
                "source": "self-spawn-handle",
            }
        return {
            "exited": True,
            "returncode": int(returncode),
            "spawn_pid": state.spawn_pid,
            "source": "self-spawn-handle",
        }

    def _finalize_exited(self, state: _TerminalState, *, reason: str, evidence: Mapping[str, Any]) -> None:
        """已证明终止：才更新记录、删秘密、回收 lease 资料。"""
        terminal_id = state.terminal_id
        exit_code = None
        runner_exit = evidence.get("runner_exit") if isinstance(evidence, Mapping) else None
        if isinstance(runner_exit, Mapping):
            raw = runner_exit.get("code")
            exit_code = int(raw) if isinstance(raw, int) else None
        if exit_code is None:
            process_exit = evidence.get("process_exit") if isinstance(evidence, Mapping) else None
            if isinstance(process_exit, Mapping) and isinstance(process_exit.get("returncode"), int):
                exit_code = int(process_exit["returncode"])
        try:
            self._registry.update(
                terminal_id,
                lambda record: self._apply_exited(record, reason=reason, exit_code=exit_code),
            )
        except Exception as exc:  # noqa: BLE001 - 记录写失败仍继续删秘密（已证明终止）
            self._note_error("exited-record-write-failed", exc)
        self._release_client(state)
        self._stop_heartbeat(state)
        # 只有**已证明终止**后才按 SecretStore 约束删除秘密（verified_exit=True）；
        # 断连/失联绝不构成删除理由。
        try:
            self._store().delete_secret(terminal_id, reason=str(reason), verified_exit=True)
        except Exception as exc:  # noqa: BLE001 - 删除失败保留文件并记类型名
            self._note_error("secret-delete-failed", exc)
        self._attachments().forget(terminal_id)
        self._note("terminal-exited", terminal_id)

    @staticmethod
    def _apply_exited(record: TerminalRecord, *, reason: str, exit_code: int | None) -> TerminalRecord:
        record.status = RuntimeState.EXITED
        record.exit_reason = str(reason)
        record.exit_code = exit_code
        record.detached = False
        return record

    # ------------------------------------------------------------ detach
    def detach(self, terminal_id: str) -> dict[str, Any]:
        """显式 runtime detach（保留 PTY/PID/端点/秘密，可重连）。

        真实宿主（ambient Job）**不支持** durable detach：runner 显式拒绝时
        **零状态变化** —— 不改记录、不停心跳、不删秘密、不做 breakaway/环境逃脱。
        """
        state = self._require_state(terminal_id)
        client = self._require_client(state)
        try:
            result = client.detach()
        except Exception as exc:  # noqa: BLE001
            self._note_error("detach-request-failed", exc)
            raise DetachRefused("detach-request-failed", error_type=type(exc).__name__) from None
        status = str(result.get("status") or "")
        durability = result.get("durability") or (result.get("describe") or {}).get("durability")
        if status != "detached":
            # 零状态变化：如实返回拒绝原因（含真实能力探测结果）。
            self._note("detach-refused", f"{terminal_id}:{status or 'unknown'}")
            return {
                "terminal_id": terminal_id,
                "detached": False,
                "status": status or "rejected",
                "durability": durability,
                "state_changed": False,
            }
        self._registry.update(
            terminal_id, lambda record: self._apply_detached(record)
        )
        self._sync_state_record(state)
        # detach 后 runtime 不再随服务停止：停心跳、保留秘密（重连闭环靠它）。
        self._stop_heartbeat(state)
        self._release_client(state)
        self._attachments().revoke_all(terminal_id)
        self._note("terminal-detached", terminal_id)
        return {
            "terminal_id": terminal_id,
            "detached": True,
            "status": status,
            "durability": durability,
            "state_changed": True,
            "mechanism": "runner-reported",
        }

    @staticmethod
    def _apply_detached(record: TerminalRecord) -> TerminalRecord:
        record.owner = "detached"
        record.detached = True
        record.detached_at = time.time()
        return record

    # ------------------------------------------------------------ reconcile
    def reconcile(self) -> dict[str, Any]:
        """崩溃/重启后核对 registry、秘密、端点与**真实身份**。

        分类严格分列（**不**冒充恢复）：

        - ``dead-confirmed``：同句柄 FILETIME + Wait 证明**我们认领的**身份已退出；
        - ``unattributable``：身份未知/错配或端点不可核验 → **零终止**、保诊断；
        - ``cleanup-unconfirmed``：曾发起停止但未获证明 → 保 owner 可重试；
        - ``alive``：仍在运行（``detached`` 核对后重连；``service`` 所有先等死期）。

        PID 查不到**不是** retained DEAD 证据（``UNKNOWN`` ≠ 已死）；不创建同 id
        替代 runner；不复活旧 lease；不因客户端断连删秘密。
        """
        buckets: dict[str, list[dict[str, Any]]] = {
            "dead-confirmed": [],
            "unattributable": [],
            "cleanup-unconfirmed": [],
            "alive": [],
            "already-terminal": [],
        }
        for record in self._safe_records():
            try:
                buckets[self._reconcile_one(record)].append(
                    {"terminal_id": record.terminal_id}
                )
            except Exception as exc:  # noqa: BLE001 - 单条失败不阻断其余核对
                self._note_error("reconcile-entry-failed", exc)
                buckets["unattributable"].append(
                    {"terminal_id": record.terminal_id, "error_type": type(exc).__name__}
                )
        total = sum(len(items) for items in buckets.values())
        return {
            "buckets": buckets,
            "counts": {name: len(items) for name, items in buckets.items()},
            "records": total,
            "identity_authority": "hello-self-report+same-handle-filetime-wait",
            "fresh_pid_absence_is_dead_evidence": False,
        }

    def _reconcile_one(self, record: TerminalRecord) -> str:
        if record.status in _TERMINAL_STATUSES:
            return "already-terminal"
        terminal_id = record.terminal_id
        with self._global_lock:
            state = self._states.get(terminal_id)
        if state is not None:
            return self._reconcile_live(state, record)
        return self._reconcile_persisted(record)

    # -- reconcile：进程内仍持有派生句柄 --------------------------------
    def _reconcile_live(self, state: _TerminalState, record: TerminalRecord) -> str:
        """本实例仍持有**自己派生**的句柄时的核对。

        **F1（核心纪律）**：我们派生的进程句柄退出**不是**整树终止证明。launcher
        可能以 ``exit 6``（引擎收尾未确证）退出，shim 场景下句柄 pid 还可能
        **不是** runner pid。因此写 ``exited`` / 删秘密**必须**以 **runner 身份
        三态 = dead-confirmed** 为门：

        - 身份 ``dead-confirmed``（pid + FILETIME 均精确匹配且已退出）→ 才补记
          ``exited`` 并删秘密；
        - 身份 ``alive`` → 记``cleanup-unconfirmed``，**保秘密 + 保记录**
          （launcher 走了但 runner 仍活着；删秘密不可逆）；
        - 身份 ``unattributable``（UNKNOWN / PID 或 FILETIME 不符）→ 同样
          **零终止**、保秘密 + 保记录。
        """
        terminal_id = state.terminal_id
        handle_exit = self._spawn_handle_exit_evidence(state)
        identity = self._identity_evidence(state.runner_pid, state.runner_filetime)
        identity_status = str(identity.get("status") or "")

        if handle_exit.get("exited") and identity_status == "dead-confirmed":
            self._mark_persisted(
                terminal_id,
                lambda item: self._apply_exited(
                    item, reason=str(item.exit_reason or "observed-exit"), exit_code=None
                ),
            )
            self._release_client(state)
            self._stop_heartbeat(state)
            self._delete_secret(terminal_id, "reconcile-observed-exit")
            return "dead-confirmed"

        if handle_exit.get("exited"):
            # 句柄退出但身份未确认终止 → 不写 exited、不删秘密（F1）。
            self._note(
                "reconcile-handle-exited-identity-not-dead",
                f"{terminal_id}:{identity.get('reason') or identity_status}",
            )
            if identity_status == "unattributable":
                return "unattributable"
            return "cleanup-unconfirmed"

        # 句柄说没退出：按记录状态分列，不终止、不冒充。
        if record.status is RuntimeState.CLEANUP_FAILED or record.status is RuntimeState.EXITING:
            # r3：若上一轮遗留 stop 因**端点暂时不可用**而未发出，则该记录仍需
            # 走可重复的端点恢复路径（此前在此直接返回，永不再attach）。
            if state.process is None and state.client is None:
                return self._retry_persisted_stop(record)
            return "cleanup-unconfirmed"
        if identity_status == "unattributable":
            self._note("reconcile-unattributable", f"{terminal_id}:{identity.get('reason')}")
            return "unattributable"
        if record.detached or record.owner == "detached":
            return self._reconnect(state, record)
        return "alive"

    # -- reconcile：只有持久记录（跨进程重启） --------------------------
    def _reconcile_persisted(self, record: TerminalRecord) -> str:
        terminal_id = record.terminal_id
        secret_present = self._secret_present(terminal_id)
        if not secret_present:
            self._note("reconcile-secret-missing", terminal_id)
            return "unattributable"
        evidence = self._identity_evidence(record.pid, record.process_created_at_filetime)
        if evidence["status"] == "dead-confirmed":
            self._mark_persisted(terminal_id, lambda item: self._apply_exited(
                item, reason=str(item.exit_reason or "observed-exit"), exit_code=None
            ))
            self._delete_secret(terminal_id, "reconcile-observed-exit")
            return "dead-confirmed"
        if evidence["status"] == "unattributable":
            # 身份未知/错配：**零终止**、保留记录与秘密（不冒充恢复、不删凭据）。
            self._note("reconcile-unattributable", f"{terminal_id}:{evidence['reason']}")
            return "unattributable"
        # 身份可核验且存活。
        if record.status is RuntimeState.CLEANUP_FAILED or record.status is RuntimeState.EXITING:
            # F3：旧managed / cleanup-failed 记录此前**没有任何**重发 stop 的入口，
            # 导致重启后永远无法收敛。这里给出受身份核验的**有界**重连+stop 入口。
            return self._retry_persisted_stop(record)
        if record.detached:
            return self._reconnect_persisted(record)
        return "alive"

    def _retry_persisted_stop(self, record: TerminalRecord) -> str:
        """遗留 managed/cleanup-failed 的**可重复**有界重连 + stop 重发。

        r3 要点（端点短暂拒绝后可恢复）：

        - 端点连接步骤是**可重复**的恢复路径，**不是**一次性闩锁：修前先把
          ``_persisted_stop_attempts`` 记死、``_states`` 落盘，``attach`` 失败后留下
          ``client=None``，而下次 ``_reconcile_live`` 对 cleanup-failed 直接返回
          ``cleanup-unconfirmed`` —— **永不再attach**，本记录永久失去收敛入口。
          现在改为：身份/秘密**每轮重核**，端点失败**局部真实 release** 连接并
          保留一个**可重试 state**，下一轮 reconcile 会再次尝试连接。
        - 成功仍**只凭原三项证据**（stop 确认 + 引擎收尾 + 经身份核验的进程退出）；
          **不**造process 句柄、**不**启动 managed 心跳（不靠续约复活旧 managed）。
        """
        terminal_id = record.terminal_id
        with self._global_lock:
            state = self._states.get(terminal_id)
        if state is None:
            state = _TerminalState(terminal_id=terminal_id, record=record)
            with self._global_lock:
                self._states[terminal_id] = state
        # 每轮用**当前**记录刷新身份，避免沿用陈旧值。
        state.record = record
        state.runner_pid = record.pid
        state.runner_filetime = record.process_created_at_filetime

        # 死期等待有界：让 runner 的 lease 宽限先过一点，再决定是否发 stop。
        if terminal_id not in self._persisted_stop_attempts:
            time.sleep(min(self.lease_grace + 0.2, 2.0))
        self._persisted_stop_attempts.add(terminal_id)

        if not self._attach_persisted_stop_client(state):
            # 端点这轮不可用：保留**可重试** state（不闩锁），下轮再来。
            return "cleanup-unconfirmed"
        outcome = self._close_state(state, reason=EXIT_REASON_SERVICE_SHUTDOWN,
                                    budget=self.stop_confirm)
        if outcome.get("status") == "exited":
            with self._global_lock:
                self._states.pop(terminal_id, None)
            return "dead-confirmed"
        self._note("reconcile-persisted-stop-unconfirmed", terminal_id)
        return "cleanup-unconfirmed"

    def _attach_persisted_stop_client(self, state: _TerminalState) -> bool:
        """有界端点连接（可重复）。失败时**局部真实 release**，不伪造连接。"""
        terminal_id = state.terminal_id
        if state.client is not None and state.attached:
            return True
        client = None
        try:
            client = self._client_factory()(
                terminal_id,
                data_root=self.root,
                connect_timeout=self.lease_grace,
                request_timeout_ms=max(2000, int(self.stop_confirm * 1000)),
                close_timeout_ms=int(self.stop_confirm * 1000) + 10_000,
                client_id=self._owner_client_id(terminal_id),
            )
            client.attach()
            described = client.describe()
        except Exception as exc:  # noqa: BLE001 - 端点不可核验 → 不动手
            self._note_error("persisted-stop-endpoint-refused", exc)
            if client is not None:
                # r3：局部**真实** release（不留下半开连接冒充可用）。
                try:
                    client.release_connection()
                except Exception:  # noqa: BLE001 - 释放失败不改变"不可用"事实
                    pass
            state.client = None
            state.attached = False
            return False
        if str(described.get("runner_state") or "") not in ("running", "closing", "exited"):
            self._note("persisted-stop-state-mismatch", terminal_id)
            try:
                client.release_connection()
            except Exception:  # noqa: BLE001
                pass
            state.client = None
            state.attached = False
            return False
        state.client = client
        state.attached = True
        # 刻意**不建心跳**：不通过续约复活旧 managed。
        return True

    def _identity_evidence(self, pid: int | None, filetime: int | None) -> dict[str, Any]:
        """身份三态核对：**同时**精确匹配 pid **与** raw FILETIME 才承认存活/已死。

        - ``dead-confirmed``：观测身份与我们**记录的身份**（pid + FILETIME 都精确
          相同）一致，且同句柄 ``Wait`` 显示已退出；
        - ``alive``：pid + FILETIME 均精确匹配且仍存活；
        - ``unattributable``：**缺 PID** / ``UNKNOWN`` / PID 不符 / FILETIME 不符
          / 观测身份缺失或字段**非整数/不可转换** → **零终止**（不动手）。

        r3 要点：

        - **缺 ``observed_pid`` 必须 unattributable**——FILETIME 单独相同**不足以**
          认定同一进程（PID 复用下不成立），此前"缺 pid 但 FT 匹配 + DEAD"会被误判
          ``dead-confirmed``；
        - PID / FILETIME **非整数或转换失败**（含观测身份整体缺失）一律归入
          **静态分类**（``pid-missing`` / ``pid-invalid`` / ``filetime-missing`` /
          ``filetime-invalid`` / ``identity-mismatch``）——**不抛异常**、**不伪造匹配**。
        """
        if pid is None or filetime is None:
            return {"status": "unattributable", "reason": "no-recorded-identity"}
        try:
            probe = self._probe(int(pid))
        except Exception as exc:  # noqa: BLE001
            self._note_error("identity-probe-failed", exc)
            return {"status": "unattributable", "reason": "probe-failed"}
        status_name = getattr(getattr(probe, "status", None), "value", None) or getattr(
            getattr(probe, "status", None), "name", None
        )
        status_text = str(status_name or "").lower()
        identity = getattr(probe, "identity", None)
        if identity is None:
            return {"status": "unattributable", "reason": "identity-missing"}
        observed_pid = getattr(identity, "pid", None)
        observed_ft = getattr(identity, "created_at_filetime", None)
        if status_text in ("", "none"):
            return {"status": "unattributable", "reason": "probe-status-unknown"}
        # r3：观测身份字段必须**存在且可转为整数**；否则静态分类，不抛、不猜。
        if observed_pid is None:
            return {"status": "unattributable", "reason": "pid-missing"}
        if observed_ft is None:
            return {"status": "unattributable", "reason": "filetime-missing"}
        # Exact identity fields: int() must not truncate floats or treat bool as PID.
        # Decimal strings are permitted for the raw64 cross-process representation.
        for value, reason in ((observed_pid, "pid-invalid"), (observed_ft, "filetime-invalid")):
            if isinstance(value, bool) or not (
                isinstance(value, int)
                or (isinstance(value, str) and value.isascii() and value.isdecimal())
            ):
                return {"status": "unattributable", "reason": reason}
        try:
            observed_pid_int = int(observed_pid)
        except (TypeError, ValueError, OverflowError):
            return {"status": "unattributable", "reason": "pid-invalid"}
        try:
            observed_ft_int = int(observed_ft)
        except (TypeError, ValueError, OverflowError):
            return {"status": "unattributable", "reason": "filetime-invalid"}
        if observed_ft_int != int(filetime):
            return {"status": "unattributable", "reason": "identity-mismatch"}
        # FILETIME 相同**不足以**认定同一进程——PID 也必须与记录一致。
        if observed_pid_int != int(pid):
            return {"status": "unattributable", "reason": "pid-mismatch"}
        if status_text == "alive":
            return {"status": "alive", "pid": int(pid), "filetime": observed_ft_int}
        if status_text == "dead":
            return {"status": "dead-confirmed", "pid": int(pid), "filetime": observed_ft_int}
        # UNKNOWN（含"PID 查不到"）**不是**已死证据。
        return {"status": "unattributable", "reason": "probe-unknown"}

    def _secret_present(self, terminal_id: str) -> bool:
        try:
            return bool(self._store().exists(terminal_id))
        except Exception as exc:  # noqa: BLE001
            self._note_error("secret-probe-failed", exc)
            return False

    def _mark_persisted(self, terminal_id: str, mutator: Callable[[TerminalRecord], TerminalRecord]) -> None:
        try:
            self._registry.update(terminal_id, mutator)
        except Exception as exc:  # noqa: BLE001
            self._note_error("reconcile-record-update-failed", exc)

    def _delete_secret(self, terminal_id: str, reason: str) -> None:
        """只在**已证明终止**后删除秘密（不使用 caller-responsibility 绕过）。"""
        try:
            self._store().delete_secret(terminal_id, reason=str(reason), verified_exit=True)
        except Exception as exc:  # noqa: BLE001
            self._note_error("secret-delete-failed", exc)

    # -- 重连（不创建替代 runner、不复活旧 lease） ----------------------
    def _reconnect(self, state: _TerminalState, record: TerminalRecord) -> str:
        """接回**既有** runner（绝不派生同id 替代进程）。

        F4：进入前**先真正回收旧资源**——若已有可用连接则**复用**，否则停掉旧心跳
        并释放旧连接。否则重复 reconcile 会让心跳线程与连接**无界增长**
        （审查实测 1→2→3→4 个同名心跳线程、release 恒为 0）。
        """
        terminal_id = state.terminal_id
        # F4：已有可用连接 → 复用（不新建、不叠加）。
        reusable = state.client is not None and state.attached
        if reusable:
            state.record = record
            if state.channel is None:
                state.channel = _ChannelProxy(self, terminal_id)
            if state.heartbeat is None:
                heartbeat = self._new_heartbeat(terminal_id)
                state.heartbeat = heartbeat
                heartbeat.start()
            self._note("reconnect-reused", terminal_id)
            return "alive"

        # 不可复用 → 先回收旧资源再建新连接（顺序：停心跳 → 释放连接）。
        self._stop_heartbeat(state)
        self._release_client(state)
        try:
            client = self._client_factory()(
                terminal_id,
                data_root=self.root,
                connect_timeout=self.lease_grace,
                request_timeout_ms=max(2000, int(self.stop_confirm * 1000)),
                close_timeout_ms=int(self.stop_confirm * 1000) + 10_000,
                client_id=self._owner_client_id(terminal_id),
            )
            client.attach()
            described = client.describe()
        except Exception as exc:  # noqa: BLE001 - 端点不可核验 → 不动手
            self._note_error("reconnect-failed", exc)
            return "unattributable"
        if str(described.get("runner_state") or "") not in ("running", "detached"):
            self._note("reconnect-state-mismatch", terminal_id)
            self._release_client(state)
            return "unattributable"
        state.client = client
        state.attached = True
        state.record = record
        # 跨进程恢复只重建控制桥，不派生 PTY，也不复活旧 attachment token。
        state.channel = _ChannelProxy(self, terminal_id)
        state.heartbeat = self._new_heartbeat(terminal_id)
        state.heartbeat.start()
        self._note("reconnected", terminal_id)
        return "alive"

    def _new_heartbeat(self, terminal_id: str) -> _Heartbeat:
        return _Heartbeat(
            client_factory=self._client_factory(),
            terminal_id=terminal_id,
            data_root=self.root,
            client_id=self._owner_client_id(terminal_id),
            interval=self.heartbeat_interval,
            grace=self.lease_grace,
        )

    def _reconnect_persisted(self, record: TerminalRecord) -> str:
        """跨进程重连：只**核对后接回既有 runner**，绝不派生同id 替代进程。"""
        terminal_id = record.terminal_id
        state = _TerminalState(terminal_id=terminal_id, record=record)
        state.runner_pid = record.pid
        state.runner_filetime = record.process_created_at_filetime
        with self._global_lock:
            self._states[terminal_id] = state
        outcome = self._reconnect(state, record)
        if outcome != "alive":
            with self._global_lock:
                self._states.pop(terminal_id, None)
        return outcome

    # ------------------------------------------------------------ shutdown
    def shutdown(
        self,
        *,
        budget: float | None = None,
        reason: str = EXIT_REASON_SERVICE_SHUTDOWN,
    ) -> dict[str, Any]:
        """服务停止：按**总预算**（含取锁与全部等待）尽力收敛 managed 终端。

        - **先关 create 准入**（F9）：``_admit_create`` 在同一 ``_admission_lock``
          内检查 ``_closing_down``，因此与并发 ``create`` **线性化**——要么 create
          先落盘并被本次 shutdown 收敛，要么被拒（不产生无人收敛的新终端）；
        - ``service`` 所有：显式关闭并等真实收尾确认；**detached** 不停止；
        - **F5**：只释放**已收敛 / kept-detached / 已终态**的连接；未收敛项**保留**
          连接与心跳引用（此处停止心跳线程但**不丢弃** worker 状态），使其后续
          仍可重试收敛；
        - **F7**：预算耗尽后**未处理**的记录全部列入未确认（``skipped``），
          ``secrets_retained`` 恒等于"确已保留"；
        - **F6**：``budget=0`` 不被抬高；``elapsed_within_budget`` 如实反映超时。
        """
        # r3：**入口即起deadline**（此前 ``_admission_lock`` 的等待发生在 ``started``
        # 之前 → 持锁 0.4s + budget=0.05 实测 elapsed 0.406 却上报 0.0/within=True）。
        started = time.monotonic()
        total = self.shutdown_budget if budget is None else max(0.0, float(budget))
        deadline = started + total

        # r3：准入关门请求**不得因取锁超时丢失**——先置全局 Event（无锁、立即生效，
        # 供 ``_admit`` 在准入临界区复查），再在预算内**有界**取锁做线性化。
        self._closing_down_event.set()
        admitted = self._admission_lock.acquire(timeout=max(0.0, deadline - time.monotonic()))
        if admitted:
            try:
                self._closing_down = True
            finally:
                self._admission_lock.release()
        else:
            # 取锁超时：Event 已保证 create 会被拒；此处如实继续收敛，不挂死。
            self._note("shutdown-admission-lock-timeout", None)
            self._closing_down = True

        outcomes: dict[str, str] = {}
        unconfirmed: list[str] = []
        skipped: list[str] = []
        exhausted = False

        records = self._safe_records()
        for index, record in enumerate(records):
            if time.monotonic() >= deadline:
                exhausted = True
                # F7：剩余记录**全部**显式列入未确认（不得静默丢失）。
                skipped = [item.terminal_id for item in records[index:]]
                break
            terminal_id = record.terminal_id
            if record.detached or record.owner == "detached":
                # detach 的 runtime 不随服务消亡：只停心跳、保留其余。
                self._stop_heartbeat_for(terminal_id, deadline=deadline)
                outcomes[terminal_id] = "kept-detached"
                continue
            if record.status in _TERMINAL_STATUSES:
                outcomes[terminal_id] = "already-terminal"
                continue
            remaining = max(0.0, deadline - time.monotonic())
            try:
                outcome = self._close_for_shutdown(
                    terminal_id, reason=reason, budget=min(self.stop_confirm, remaining)
                )
                # stop 返回时 launcher 的引擎收尾/进程 signaled 可能尚未落地。
                # 同一自持 owner 在总预算内重评证据，不能一次未确认就结束整个
                # graceful shutdown。_close_state 自带单飞与幂等停止纪律。
                while (
                    outcome.get("status") != "exited"
                    and terminal_id in self._states
                    and time.monotonic() < deadline
                ):
                    pause = min(0.05, max(0.0, deadline - time.monotonic()))
                    if pause:
                        time.sleep(pause)
                    remaining = max(0.0, deadline - time.monotonic())
                    if remaining <= 0:
                        exhausted = True
                        break
                    outcome = self._close_for_shutdown(
                        terminal_id, reason=reason, budget=min(self.stop_confirm, remaining)
                    )
                if outcome.get("status") != "exited" and time.monotonic() >= deadline:
                    exhausted = True
            except Exception as exc:  # noqa: BLE001
                self._note_error("shutdown-close-failed", exc)
                outcome = {"status": "cleanup-failed", "reason": type(exc).__name__}
            outcomes[terminal_id] = str(outcome.get("status") or "cleanup-failed")
            if outcome.get("status") != "exited":
                unconfirmed.append(terminal_id)

        # F5：**只**回收已收敛 / kept-detached / 已终态的连接；未收敛项保留。
        released: list[str] = []
        for terminal_id, status in outcomes.items():
            if status in ("exited", "kept-detached", "already-terminal"):
                self._stop_heartbeat_for(terminal_id, deadline=deadline)
                self._release_for(terminal_id)
                released.append(terminal_id)
        # 未收敛项：停心跳线程，但**保留** client 与 close worker 引用（可重试）。
        for terminal_id in unconfirmed:
            self._stop_heartbeat_for(terminal_id, deadline=deadline)

        # F7：skipped 计入未确认，且明确"未处理"。
        unconfirmed_all = sorted(set(unconfirmed) | set(skipped))
        with self._global_lock:
            self._shutdown_done = True
        elapsed = time.monotonic() - started
        return {
            "budget_seconds": total,
            "elapsed_seconds": round(elapsed, 3),
            # r3：如实反映是否在预算内完成（仅容忍 1ms 浮点噪声；**不得**用宽松
            # slack 把"超时"报成 within——MA 实测 0.406s 曾被报成 within=True）。
            "elapsed_within_budget": (not exhausted) and elapsed <= total + 0.001,
            "budget_exhausted": exhausted,
            "outcomes": outcomes,
            "exited": sorted(tid for tid, st in outcomes.items() if st == "exited"),
            "kept_detached": sorted(tid for tid, st in outcomes.items() if st == "kept-detached"),
            "released": sorted(released),
            "retained_for_retry": sorted(unconfirmed),
            "skipped": sorted(skipped),
            "unconfirmed": unconfirmed_all,
            # 恒等于"确有未收敛项 → 秘密确已保留"，不得假报 False。
            "secrets_retained": bool(unconfirmed_all),
        }

    def _close_for_shutdown(self, terminal_id: str, *, reason: str, budget: float) -> dict[str, Any]:
        with self._global_lock:
            state = self._states.get(terminal_id)
        if state is not None:
            return self._close_state(state, reason=reason, budget=budget)
        # 跨进程遗留记录：本实例无句柄 → 无法证明进程退出（**不**冒充）。
        self._note("shutdown-unattached", terminal_id)
        self._mark_persisted(terminal_id, lambda item: self._apply_unproven(item, "shutdown-unattached"))
        return {"status": "cleanup-failed", "reason": "shutdown-unattached", "proven": False}

    # ------------------------------------------------------------ 内部工具
    def _require_state(self, terminal_id: str) -> _TerminalState:
        with self._global_lock:
            state = self._states.get(terminal_id)
        if state is None:
            # 记录存在但本实例未持有（跨进程/重启后）→ 明确"未连接"，不猜。
            # 记录本身不存在时让registry 的 ``UnknownTerminalError`` 原样冒泡
            # （调用方需要区分"不存在"与"未连接"），但**不**附带内部细节。
            self._registry.get(terminal_id)
            raise TerminalNotAttached("not-attached")
        return state

    def _require_client(self, state: _TerminalState) -> Any:
        client = state.client
        if client is None or not state.attached:
            raise TerminalNotAttached("not-attached")
        return client

    def _known_states(self) -> list[str]:
        with self._global_lock:
            return list(self._states)

    def _stop_heartbeat(self, state: _TerminalState, *, deadline: float | None = None) -> None:
        """停心跳；``deadline`` 给定时**不超预算**地等线程收敛（F6）。

        F6：不无界取 ``state.lock``（等锁超时路径上再取锁会重新挂死）；
        属性读取在 CPython 下是原子的，足够安全。
        """
        heartbeat, state.heartbeat = state.heartbeat, None
        if heartbeat is None:
            return
        timeout = 2.0
        if deadline is not None:
            timeout = max(0.0, min(timeout, deadline - time.monotonic()))
        heartbeat.stop(timeout=timeout)

    def _stop_heartbeat_for(self, terminal_id: str, *, deadline: float | None = None) -> None:
        with self._global_lock:
            state = self._states.get(terminal_id)
        if state is not None:
            self._stop_heartbeat(state, deadline=deadline)

    def _release_client(self, state: _TerminalState) -> None:
        """只释放连接（**不**触碰 runtime、**不**删秘密）。"""
        client, state.client = state.client, None
        state.attached = False
        if client is None:
            return
        try:
            client.release_connection()
        except Exception as exc:  # noqa: BLE001
            self._note_error("release-connection-failed", exc)

    def _release_for(self, terminal_id: str) -> None:
        with self._global_lock:
            state = self._states.get(terminal_id)
        if state is not None:
            self._release_client(state)
