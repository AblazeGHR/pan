"""Pan Terminal 公共核心的冻结契约（平台无关，P0）。

本模块是 Windows backend / runner / service 各层共用的**接口基线**，只包含：

- 平台无关的数据类型、枚举与异常；
- ``PtyBackend`` / ``TreeGuard`` / ``ScreenObserver`` / ``StartupOwnershipGate`` /
  ``AuthoritativeEmulator`` / ``DetachHandler`` / ``AutomationDriver`` 等 Protocol；
- 工程默认参数常量（与实施计划 §13 对齐）。

硬约束：

1. **导入无副作用**：不导入 ``ctypes`` / ``win32`` / ``pyte`` / ``psutil`` 等平台或
   可选依赖，不创建目录、不探测环境；纯逻辑模块因此在任何平台可导入、可测试。
2. **仿真器只声明协议不实现**：``AuthoritativeEmulator`` 与其 ``AppliedSnapshot``
   由 runner 侧（TA-B ``emulator.py``）实现；本模块不提供任何仿真器。
3. 所有“声明式字段”（如所有权证据的 bool、``lifecycle_owner``）都是**数据**：
   它们不构成 OS 级保障，真实性由产生证据的 spawn/宿主实现负责（见
   ``ProcessOwnershipEvidence`` 与 ``StartupOwnershipGate`` 文档）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterable, Protocol, runtime_checkable

TERMINAL_ID_PREFIX = "term_"

#: 输出日志（客户端游标窗口）默认上界，见实施计划 §13。
DEFAULT_OUTPUT_LOG_BYTES = 256 * 1024
#: PTY 单次读取块大小，见实施计划 §13。
DEFAULT_READ_SIZE = 64 * 1024
#: 进程退出后等待通道 EOF 的有界宽限，见实施计划 §13。
DEFAULT_EOF_GRACE_SECONDS = 8.0
#: lease 统一死期（心跳 1s，容忍 1 次丢失），见实施计划 §13。
DEFAULT_LEASE_GRACE_SECONDS = 2.0
#: 停止/关闭确认（runner 整树退出）有界等待默认值，见实施计划 §13。
DEFAULT_STOP_CONFIRM_SECONDS = 5.0
#: 单次句柄关闭（取消读/释放）的有界等待：阻塞调用不得卡死 close 主线程。
DEFAULT_HANDLE_CLOSE_TIMEOUT = 2.0
#: 已接纳输入清空的有界等待（close 与输入操作门同步），见 ``PtyRuntime.close``。
DEFAULT_INPUT_DRAIN_TIMEOUT = 2.0
#: 输出消费者错误诊断保留的不同异常类型数上界（只记类型名，不记消息）。
DEFAULT_CONSUMER_ERROR_TYPES = 8

LEASE_ROLE_CONTROL = "control"
LEASE_ROLE_OBSERVER = "observer"


# --------------------------------------------------------------------------
# 枚举
# --------------------------------------------------------------------------


class RuntimeState(str, Enum):
    """终端 runtime 生命周期状态。合法迁移由 ``PtyRuntime`` 强制。"""

    CREATED = "created"
    STARTING = "starting"
    RUNNING = "running"
    EXITING = "exiting"
    EXITED = "exited"
    CLEANUP_FAILED = "cleanup-failed"
    LOST = "lost"


class DrainStopReason(str, Enum):
    """reader drain 循环的结束原因分类。

    只有 ``EOF`` 才允许 ``channel_eof`` / ``output_complete``；``CANCELLED`` 是
    我们自己关掉通道的结果（既不是对端 EOF，也不算通道错误）。
    """

    UNKNOWN = "unknown"
    EOF = "eof"
    CANCELLED = "cancelled"
    CHANNEL_ERROR = "channel-error"
    EOF_TIMEOUT = "eof-timeout"
    STOP_REQUESTED = "stop-requested"


class IdentityCheck(str, Enum):
    """清理前的身份核验结论（r2 三态语义）。

    - ``not-required``：没有记录身份（无核验对象）；
    - ``verified``：存活且与记录身份匹配；
    - ``dead-confirmed``：后端提供**明确已退出证据**（retained handle signaled /
      Job 层面证据）——允许继续整树清理，不代表身份“匹配”；
    - ``mismatch`` / ``probe-failed`` / ``unknown``：**fail-closed**，拒绝任何
      终止/interrupt/取消，保 owner 可重试。``unknown`` 特别覆盖“探针返回无结论”
      与“有记录身份却无探针”：None 只表示未知，**不能证明 dead**。
    """

    NOT_REQUIRED = "not-required"
    VERIFIED = "verified"
    CONFIRMED_DEAD = "dead-confirmed"
    MISMATCH = "mismatch"
    PROBE_FAILED = "probe-failed"
    UNKNOWN = "unknown"


class ProcessStatus(str, Enum):
    """探针对单个 PID 的显式存活判定（r2 契约）。

    ``DEAD`` 必须来自真实证据（retained handle ``WaitForSingleObject`` 已
    signaled、Job 层面确认等），不得用“查不到/打不开”冒充；查不到一律
    ``UNKNOWN``。
    """

    ALIVE = "alive"
    DEAD = "dead"
    UNKNOWN = "unknown"


class TerminateOutcome(str, Enum):
    """终止动作的结果（与清理整体结果分开报告）。"""

    NOT_ATTEMPTED = "not-attempted"
    RETURNED = "returned"
    ERROR = "error"
    TIMED_OUT = "timed-out"
    REFUSED_IDENTITY_MISMATCH = "refused-identity-mismatch"
    REFUSED_IDENTITY_UNKNOWN = "refused-identity-unknown"


class OwnershipMode(str, Enum):
    """默认寿命语义；``lifecycle_owner`` 只是声明性数据，核心不按其分支。"""

    SERVICE = "service"
    DETACHED = "detached"
    EXTERNAL = "external"


class Fidelity(str, Enum):
    """快照/观察引擎保真度声明。只有 ``FULL`` 允许宣称“完整”。"""

    FULL = "full"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class Recovery(str, Enum):
    """快照恢复能力声明；``feed_lag`` 后必须显式降级（通常为 ``DEGRADED``）。"""

    FULL = "full"
    PARTIAL = "partial"
    DEGRADED = "degraded"
    NONE = "none"


# --------------------------------------------------------------------------
# 异常（集中冻结，便于各层稳定捕获）
# --------------------------------------------------------------------------


class BackendUnavailableError(RuntimeError):
    """平台后端不可用（缺依赖/平台不支持）。创建终端时解释，不阻断整个 Pan。"""


class InvalidCursorError(ValueError):
    """输出游标非法（负数，或超过已产生的字节数）。"""


class IllegalStateTransition(RuntimeError):
    """状态机非法迁移/在错误状态下调用操作。"""


class UnknownTerminalError(KeyError):
    """terminal_id 不存在（registry 或 attachment 层）。"""


class StaleLeaseError(RuntimeError):
    """lease 已被撤销，或控制权世代已过期。"""


class NotControlLeaseError(RuntimeError):
    """非控制权 token 试图写入/改尺寸/转交控制权。"""


class OwnershipPolicyRequired(RuntimeError):
    """未显式给出所有权策略：工厂没有默认值。"""


class UnownedTreeRejected(RuntimeError):
    """声明了树守卫能力却未提供守卫实现，且未显式确认接受“无 OS 级守卫”。"""


class OwnershipGateError(RuntimeError):
    """启动所有权证据不完整：拒绝进入 running。"""


class DetachUnsupportedError(RuntimeError):
    """无真实宿主能力（``DetachHandler``）时不允许伪造 detach。"""


class ExternalOwnershipNotYetValidated(RuntimeError):
    """外部所有者的默认寿命/崩溃/重连语义未定义，不得仅凭声明放行。"""


class RegistryError(RuntimeError):
    """registry 持久化层错误基类。"""


class RegistryLockTimeout(RegistryError, TimeoutError):
    """跨进程 registry 锁等待超时。"""


class RegistryCorruptError(RegistryError):
    """registry 记录文件存在但无法解析；不静默丢弃。"""


class TerminalExistsError(ValueError):
    """create 的目标 terminal_id 已存在。"""


# --------------------------------------------------------------------------
# 数据类
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ProcessIdentity:
    """进程身份 = PID + 创建时间。

    ``created_at_filetime`` 是 Windows 100ns FILETIME，精确相等比较（无容差）；
    只有 ``created_at`` 时可给容差。两者都缺 -> ``matches`` 恒为 False
    （fail-closed：不可验证即视为不匹配，拒绝对不可验证 PID 下手）。
    """

    pid: int | None
    created_at_filetime: int | None = None
    created_at: float | None = None
    image: str | None = None

    def matches(self, other: "ProcessIdentity | None", *, tolerance: float = 0.0) -> bool:
        if other is None or self.pid is None or other.pid is None:
            return False
        if self.pid != other.pid:
            return False
        if self.created_at_filetime is not None and other.created_at_filetime is not None:
            return int(self.created_at_filetime) == int(other.created_at_filetime)
        if self.created_at is not None and other.created_at is not None:
            return abs(float(self.created_at) - float(other.created_at)) <= tolerance
        return False

    def as_dict(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "created_at_filetime": self.created_at_filetime,
            "created_at": self.created_at,
            "image": self.image,
        }


@dataclass(frozen=True)
class ProcessProbe:
    """探针的显式三态结果（r2 契约；``IdentityProbe`` 的返回类型）。

    - ``ALIVE``：必须附带 ``identity`` 供比对；没有可比对身份 -> 调用方按
      ``unknown`` 处理（fail-closed）；
    - ``DEAD``：明确已退出证据——**只能**来自 spawn/入组时记录的**同一**
      retained handle（同 handle ``WaitForSingleObject`` 已 signaled）或 Job
      对象层面的证据；**不得**用对现查陌生 PID（如重新 ``OpenProcess``）的
      signaled 结果放行——PID 复用会让“陌生进程已退出”冒充“我们的进程已退出”；
    - ``UNKNOWN``：不可探测/无结论——**不等于 dead**。
    """

    status: ProcessStatus
    identity: ProcessIdentity | None = None
    detail: str = ""


@dataclass(frozen=True)
class ProcessOwnershipEvidence:
    """启动所有权证据：四要素缺一不可。

    **注意**：这些 bool 字段是 backend 在真实 spawn 时如实填写的声明，
    本身**不是** OS 级保障；门禁（``StartupOwnershipGate.verify``）只校验
    证据的完整性，证据真实性必须由挂起式 spawn/原子入组等真实原语支撑。
    """

    assigned: bool
    atomic_with_spawn: bool
    identity: ProcessIdentity | None
    handle_bound_for_cleanup: bool
    guard: str
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "assigned": self.assigned,
            "atomic_with_spawn": self.atomic_with_spawn,
            "identity": self.identity.as_dict() if self.identity else None,
            "handle_bound_for_cleanup": self.handle_bound_for_cleanup,
            "guard": self.guard,
            "detail": self.detail,
        }


@dataclass
class ExitInfo:
    """退出事实。以下字段是**不同事实**，必须分开判断，不能互相充当证据：

    - ``process_exit_seen`` / ``code``：根进程已退出及其退出码；
    - ``reader_done``：reader 线程结束（任何原因，含错误/取消/超时）；
    - ``channel_eof``：``read()`` 报告**对端**真实通道 EOF（自取消不算）；
    - ``output_complete``：**只有**真实 EOF 结账时为真。
    """

    code: int | None = None
    process_exit_seen: bool = False
    reader_done: bool = False
    channel_eof: bool = False
    output_complete: bool = False
    drain_stop_reason: DrainStopReason = DrainStopReason.UNKNOWN
    reason: str = "unknown"
    observed_at: float = 0.0
    channel_error: str | None = None
    bytes_at_stop: int = 0

    @property
    def termination_complete(self) -> bool:
        """输出是否“干净地结束”：只有对端真实 EOF 才算。"""
        return self.output_complete

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "process_exit_seen": self.process_exit_seen,
            "reader_done": self.reader_done,
            "channel_eof": self.channel_eof,
            "output_complete": self.output_complete,
            "drain_stop_reason": self.drain_stop_reason.value,
            "reason": self.reason,
            "observed_at": self.observed_at,
            "channel_error": self.channel_error,
            "bytes_at_stop": self.bytes_at_stop,
        }


@dataclass
class CleanupReport:
    """``close()`` 的结果。失败必须保留 owner（``owner_retained``）以便重试。"""

    terminal_id: str
    requested_reason: str
    interrupt_sent: bool = False
    interrupt_error: str | None = None
    terminate_result: TerminateOutcome = TerminateOutcome.NOT_ATTEMPTED
    terminate_error: str | None = None
    tree_owned_pids: tuple[int, ...] = ()
    tree_remaining_pids: tuple[int, ...] = ()
    backend_closed: bool = False
    reader_joined: bool = False
    reader_converged: bool = False
    reader_cancelled: bool = False
    cancel_kind: str = "none"
    drain_stop_reason: DrainStopReason = DrainStopReason.UNKNOWN
    identity_check: IdentityCheck = IdentityCheck.NOT_REQUIRED
    state_after: RuntimeState = RuntimeState.EXITED
    owner_retained: bool = False
    error: str | None = None
    seconds: float = 0.0

    @property
    def ok(self) -> bool:
        """清理是否完整成功（与“输出是否完整”无关）。"""
        return (
            self.terminate_result is TerminateOutcome.RETURNED
            and not self.tree_remaining_pids
            and self.state_after is RuntimeState.EXITED
            and self.error is None
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "terminal_id": self.terminal_id,
            "requested_reason": self.requested_reason,
            "interrupt_sent": self.interrupt_sent,
            "interrupt_error": self.interrupt_error,
            "terminate_result": self.terminate_result.value,
            "terminate_error": self.terminate_error,
            "tree_owned_pids": list(self.tree_owned_pids),
            "tree_remaining_pids": list(self.tree_remaining_pids),
            "backend_closed": self.backend_closed,
            "reader_joined": self.reader_joined,
            "reader_converged": self.reader_converged,
            "reader_cancelled": self.reader_cancelled,
            "cancel_kind": self.cancel_kind,
            "drain_stop_reason": self.drain_stop_reason.value,
            "identity_check": self.identity_check.value,
            "state_after": self.state_after.value,
            "owner_retained": self.owner_retained,
            "error": self.error,
            "seconds": self.seconds,
            "ok": self.ok,
        }


@dataclass(frozen=True)
class OutputChunk:
    """一段输出。``seq`` 是这段首字节在**流中的绝对偏移**。"""

    seq: int
    data: bytes


@dataclass(frozen=True)
class OutputPage:
    """一次带游标的读取结果。

    - ``chunks`` 首块序号是 ``first_seq``；``next_cursor`` 用于下次继续读；
    - ``gap`` 非空表示请求游标到保留窗口之间的字节**已被丢弃**，客户端必须
      走快照恢复，不能把这段当作空、也不能补零。
    """

    chunks: tuple[OutputChunk, ...]
    first_seq: int
    next_cursor: int
    gap: tuple[int, int] | None
    truncated: bool


@dataclass(frozen=True)
class LeaseToken:
    """一次附件（attachment）的授权凭证。

    ``revocation_id`` 是不透明随机串，由 ``AttachmentRegistry`` 生成；真正区分
    “同一个控制权”的是它。``client_id``/``role`` 是自报字段，不能作为授权依据。
    """

    terminal_id: str
    client_id: str
    generation: int
    role: str
    rows: int
    cols: int
    revocation_id: str = ""

    @property
    def is_control(self) -> bool:
        return self.role == LEASE_ROLE_CONTROL


@dataclass(frozen=True)
class TerminalScope:
    """可选的 Workspace / Agent Session 关联（**仅元数据，不是权限**）。"""

    workspace_id: str | None = None
    session_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"workspace_id": self.workspace_id, "session_id": self.session_id}


@dataclass
class TerminalRecord:
    """终端注册表的持久化记录（JSON 无任何秘密/token 字段）。

    运行期对象（``PtyRuntime``/连接）不在此记录中；本记录只承载事实与提示。
    """

    terminal_id: str
    owner: str = "service"  # "service" | "detached"
    status: RuntimeState = RuntimeState.CREATED
    created_at: float = 0.0
    updated_at: float = 0.0
    rows: int = 0
    cols: int = 0
    pid: int | None = None
    process_created_at_filetime: int | None = None
    pipe: str | None = None
    detached: bool = False
    detached_at: float | None = None
    scope: TerminalScope = field(default_factory=TerminalScope)
    exit_code: int | None = None
    exit_reason: str | None = None
    lease_grace_seconds: float | None = None
    created_by: str | None = None

    archived: bool = False
    cleanup_pending: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "terminal_id": self.terminal_id,
            "owner": self.owner,
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "rows": self.rows,
            "cols": self.cols,
            "pid": self.pid,
            "process_created_at_filetime": self.process_created_at_filetime,
            "pipe": self.pipe,
            "detached": self.detached,
            "detached_at": self.detached_at,
            "scope": self.scope.as_dict(),
            "exit": {"code": self.exit_code, "reason": self.exit_reason},
            "lease_grace_seconds": self.lease_grace_seconds,
            "created_by": self.created_by,
            "archived": self.archived,
            "cleanup_pending": self.cleanup_pending,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TerminalRecord":
        scope = data.get("scope") or {}
        exit_info = data.get("exit") or {}
        return cls(
            terminal_id=str(data.get("terminal_id") or ""),
            owner=str(data.get("owner") or "service"),
            status=RuntimeState(str(data.get("status") or RuntimeState.CREATED.value)),
            created_at=float(data.get("created_at") or 0.0),
            updated_at=float(data.get("updated_at") or 0.0),
            rows=int(data.get("rows") or 0),
            cols=int(data.get("cols") or 0),
            pid=data.get("pid"),
            process_created_at_filetime=data.get("process_created_at_filetime"),
            pipe=data.get("pipe"),
            detached=bool(data.get("detached", False)),
            detached_at=data.get("detached_at"),
            scope=TerminalScope(
                workspace_id=scope.get("workspace_id"),
                session_id=scope.get("session_id"),
            ),
            exit_code=exit_info.get("code"),
            exit_reason=exit_info.get("reason"),
            lease_grace_seconds=data.get("lease_grace_seconds"),
            created_by=data.get("created_by"),
            archived=data.get("archived") is True,
            cleanup_pending=data.get("cleanup_pending") is True,
        )


@dataclass(frozen=True)
class ScreenSnapshot:
    """屏幕快照（观察者层）。

    ``fidelity`` 必须显式声明，不能让调用方误以为是权威渲染状态。
    """

    rows: int
    cols: int
    lines: tuple[str, ...]
    cursor: tuple[int, int]
    alternate_screen: bool | None
    private_modes: frozenset[int]
    raw_mode_bits: frozenset[int]
    saved_cursor: bool
    scrollback_lines: int
    engine: str
    fidelity: Fidelity
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "rows": self.rows,
            "cols": self.cols,
            "lines": list(self.lines),
            "cursor": list(self.cursor),
            "alternate_screen": self.alternate_screen,
            "private_modes": sorted(self.private_modes),
            "raw_mode_bits": sorted(self.raw_mode_bits),
            "saved_cursor": self.saved_cursor,
            "scrollback_lines": self.scrollback_lines,
            "engine": self.engine,
            "fidelity": self.fidelity.value,
            "note": self.note,
        }


@dataclass(frozen=True)
class WaitOutcome:
    """``wait_for`` 的结果：命中/超时必须可区分（避免“超时后最后一次文本恰好
    满足”被当作命中）。"""

    matched: bool
    timed_out: bool
    screen: ScreenSnapshot | None
    last_text: str
    observed_bytes: int


@dataclass(frozen=True)
class AppliedSnapshot:
    """权威仿真器在 barrier 上返回的原子快照。

    ``cursor`` 是**仿真器确认已解析应用**的绝对字节位置（applied_seq），
    **不是** producer 的 ``total_bytes``：reader 入队不等于 sidecar 已解析。
    barrier 有界超时或 feed 滞后（``feed_lag``）时必须按 ``recovery`` 降级，
    不得用未应用的字节数冒充。
    """

    serialized_screen: str
    cursor: int
    rows: int
    cols: int
    fidelity: Fidelity
    recovery: Recovery
    feed_lag: bool = False
    engine: str = ""
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "serialized_screen": self.serialized_screen,
            "cursor": self.cursor,
            "rows": self.rows,
            "cols": self.cols,
            "fidelity": self.fidelity.value,
            "recovery": self.recovery.value,
            "feed_lag": self.feed_lag,
            "engine": self.engine,
            "note": self.note,
        }


@dataclass(frozen=True)
class DetachReport:
    """显式 runtime detach 的结果。只有注入真实宿主能力（``DetachHandler``）后
    才可能产生；核心不伪造 ``detached`` 事实。"""

    terminal_id: str
    detached_at: float
    durable_owner: str
    reconnect_hint: dict[str, Any] = field(default_factory=dict)
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "terminal_id": self.terminal_id,
            "detached_at": self.detached_at,
            "durable_owner": self.durable_owner,
            "reconnect_hint": dict(self.reconnect_hint),
            "note": self.note,
        }


# --------------------------------------------------------------------------
# 类型别名
# --------------------------------------------------------------------------

#: 身份探针：按 PID 取显式三态结论。不可探测/无结论必须返回 ``ProcessProbe``
#: (status=UNKNOWN) 或裸 ``None``（兼容旧探针，按 UNKNOWN 处理）——**不得**
#: 用 None 表示“已退出”。为兼容过渡期，返回裸 ``ProcessIdentity`` 按
#: ALIVE+该身份处理；返回裸 ``ProcessStatus`` 按对应状态处理。
IdentityProbe = Callable[[int], "ProcessProbe | ProcessIdentity | ProcessStatus | None"]
#: 输出消费回调（P1 用于喂权威仿真器）。
#: 第一参数是**该块首字节的绝对偏移**（与 OutputLog 同源）：消费者据此检测
#: gap（失败块/跳块的绝对位置）并显式降级，禁止用它冒充连续供料。
#: 必须快速返回，不得阻塞 reader。
OutputConsumer = Callable[[int, bytes], None]


# --------------------------------------------------------------------------
# Protocol（平台实现由后续 TA 提供）
# --------------------------------------------------------------------------


@runtime_checkable
class PtyBackend(Protocol):
    """最小 PTY 后端（Windows 生产实现 = ConPtyBackend）。

    约定：

    - ``read`` 阻塞直到有数据、或通道结束；通道结束时抛 ``EOFError``；
    - ``read`` 返回**原始字节**，不做任何终端语义处理；
    - ``exit_code`` 在进程尚未退出时返回 ``None``；
    - ``terminate`` 只请求终止，不确认整树退出；整树由 ``TreeGuard`` 负责；
    - ``close`` 关闭句柄；在 pywinpty 上它同时会终止进程，因此**只允许**在
      终止与整树都确认后进行（见 ``PtyRuntime.close``）。
    - **最终句柄串行安全（r3 协议约束，不改方法签名）**：同一句柄上的
      ``read``/``write``/``resize``/``terminate``/``close`` 并发调用必须由后端
      内部串行化（自锁或文档化的事件序列），不得交错破坏句柄状态；``close``
      与在途 ``write``/``terminate`` 尤其不得竞态。runtime 侧只保证“关门后不再
      发起新输入 + 有界等待已接纳输入收敛”，句柄级并发安全由后端负责。
    """

    pid: int | None

    def read(self, size: int) -> bytes: ...

    def write(self, data: bytes) -> int: ...

    def resize(self, rows: int, cols: int) -> None: ...

    def alive(self) -> bool: ...

    def exit_code(self) -> int | None: ...

    def terminate(self, force: bool) -> None: ...

    def close(self) -> None: ...


@runtime_checkable
class TreeGuard(Protocol):
    """整树所有权守卫（Windows 生产实现 = Job Object）。

    契约（r2 修订）：

    - 顺序：**先** ``owned_pids`` 快照所有权，**再** ``terminate_tree`` 终止，
      **最后**用 ``remaining`` 核对残留；反过来（先杀再查）在根 PID 消失后
      必然返回空集。
    - ``owned_pids`` 失败=所有权未知：调用方必须 fail-closed（不得 EXITED、
      不得取消 reader）。
    - ``terminate_tree`` **即使快照为空也必须被调用**：真实 guard 可依赖 Job
      身份（而不是 root PID 扫描）——根已死仍能枚举整个 Job 的孙进程；
      ``timeout`` 是有界终止预算。
    - ``terminate_tree`` 返回 ``(owned_after, remaining_after)`` 是**权威结果**，
      调用方应采用（可能比之前的快照更完整）；``remaining_after`` 非空表示树
      未确认死亡。
    - ``remaining`` 抛异常=残留未知：调用方必须 fail-closed。
    - **每个操作必须自身有界（r3 协议约束）**：``owned_pids`` / ``remaining``
      必须是快速查询，``terminate_tree`` 必须遵守 ``timeout`` 预算并及时返回；
      runtime 侧另有外层有界等待兜底（超时按“未知”fail-closed、未完成的调用
      跨重试复用不重叠），但不代替 guard 自身的 deadline 纪律。

    ``describe()`` 必须如实标注 ``os_level_guard``：psutil 兜底方案返回 False，
    它只用于测试观察，不是整树所有权证明。
    """

    def describe(self) -> dict[str, Any]: ...

    def owned_pids(self, root_pid: int | None) -> list[int]: ...

    def remaining(self, pids: Iterable[int]) -> list[int]: ...

    def terminate_tree(
        self, root_pid: int | None, *, timeout: float
    ) -> tuple[list[int], list[int]]: ...


@runtime_checkable
class StartupOwnershipGate(Protocol):
    """启动所有权门禁。``verify()`` 不通过（抛 ``OwnershipGateError``）时
    runtime 不得进入 ``running``。"""

    def verify(self) -> None: ...


class ScreenObserver(Protocol):
    """自动化观察者（业务状态机 driver 的输入）。

    ``snapshot`` 必须是**仿真器状态**，不是尾部文本；``fidelity`` 必须显式声明。
    """

    def feed(self, data: bytes) -> None: ...

    def snapshot(self) -> ScreenSnapshot: ...

    def resize(self, rows: int, cols: int) -> None: ...

    def wait_for(
        self,
        predicate: Callable[[str], bool],
        timeout: float,
        *,
        quiet_ms: float = 0.0,
    ) -> WaitOutcome: ...


class AuthoritativeEmulator(Protocol):
    """权威仿真器宿主协议（**只声明，不实现**；实现属 runner 侧）。

    feed / resize / snapshot 必须走同一有序命令通道；``snapshot`` 在 barrier
    上原子返回 ``AppliedSnapshot``（cursor = 已解析位置）。feed 队列有界，
    打满置位 ``feed_lag`` 并按恢复语义降级，禁止阻塞 PTY reader。
    """

    @property
    def feed_lag(self) -> bool: ...

    def feed(self, data: bytes) -> None: ...

    def resize(self, rows: int, cols: int) -> None: ...

    def snapshot(self, *, timeout: float = 2.0) -> AppliedSnapshot: ...

    def reset_baseline(self) -> AppliedSnapshot: ...


class DetachHandler(Protocol):
    """真实宿主能力：把 runtime 的所有权移交给独立宿主的实现。

    公共核心**不提供任何默认实现**；没有注入本能力时 ``PtyRuntime.detach()``
    必须失败（``DetachUnsupportedError``），不允许伪造 detached 事实。
    """

    def request_detach(self, runtime: "PtyRuntime") -> DetachReport: ...


@runtime_checkable
class TerminalChannel(Protocol):
    """attachment lease 可操作的目标（``PtyRuntime`` 满足此协议）。

    刻意不暴露 read/exit 等能力：lease 只能写与改尺寸。
    """

    def write(self, data: bytes) -> int: ...

    def resize(self, rows: int, cols: int) -> bool: ...


class AutomationDriver(Protocol):
    """业务状态机接口。公共核心只提供 runtime/observer/lease，业务菜单留在 driver。"""

    driver_id: str

    def run(self, context: "AutomationContext") -> dict[str, Any]: ...


#: attachment registry 的终端解析器：不存在/未授权时返回 None。
TerminalLookup = Callable[[str], TerminalChannel | None]


@dataclass(frozen=True)
class OwnershipPolicy:
    """所有权策略：**声明**谁持有树守卫，核心不按布局分支。

    - ``mode``：默认寿命语义（``SERVICE`` 随 Pan 生死；``DETACHED`` durable；
      ``EXTERNAL`` 语义未定义）；
    - ``lifecycle_owner``：``"pan-service" | "runner" | "external-host"`` 等，
      仅声明性数据；
    - ``tree_guard``：注入的 ``TreeGuard`` 实现（Job Object / 进程组 / 其它）；
    - ``detach_handler``：真实宿主能力；声明 ``detached`` 或 ``DETACHED`` 模式的
      策略必须提供它，否则 ``build_runtime`` fail-closed。
    """

    mode: OwnershipMode
    lifecycle_owner: str
    tree_guard_kind: str
    tree_guard: TreeGuard | None = None
    lease_grace_seconds: float | None = None
    detached: bool = False
    detach_handler: DetachHandler | None = None
    notes: str = ""

    def describe(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "lifecycle_owner": self.lifecycle_owner,
            "tree_guard_kind": self.tree_guard_kind,
            "tree_guard": getattr(self.tree_guard, "describe", lambda: None)()
            if self.tree_guard is not None
            else None,
            "lease_grace_seconds": self.lease_grace_seconds,
            "detached": self.detached,
            "detach_handler": bool(self.detach_handler is not None),
            "notes": self.notes,
        }

    def on_service_shutdown(self, runtime: "PtyRuntime") -> CleanupReport:
        """默认寿命策略：SERVICE 随 Pan 结束；非 service 不由 Pan 在此终止。

        非 service 被拒**不是**产品决定未定，而是本层没有可用的内核级守卫 /
        外部所有者实现：静默按 service 终止会违背“detach 后不随 Pan 终止”的
        既定语义，所以宁可 fail-closed。
        """
        if self.detached or self.mode is not OwnershipMode.SERVICE:
            raise DetachUnsupportedError(
                "非 service 所有权不能按 service 语义在服务关闭时终止："
                "缺少内核级守卫/宿主实现（见 ownership.py 与接口文档）。"
            )
        return runtime.close(reason="service-shutdown")

    def reconnect_hint(self, terminal_id: str) -> dict[str, Any]:
        return {
            "terminal_id": terminal_id,
            "mode": self.mode.value,
            "lifecycle_owner": self.lifecycle_owner,
            "detached": self.detached,
            "reconnect_supported": bool(
                self.mode is OwnershipMode.DETACHED and self.detach_handler is not None
            ),
        }
