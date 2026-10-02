"""Pan Terminal 公共核心（P0，接口冻结）。

模块布局与职责：

- ``contracts``：平台无关数据、枚举、异常与 Protocol（``PtyBackend`` /
  ``TreeGuard`` / ``ScreenObserver`` / ``AuthoritativeEmulator`` 等）；
- ``output``：有界输出日志（绝对字节偏移 / gap）；
- ``runtime``：``PtyRuntime``（drain、状态机、清理契约、detach 门）；
- ``ownership``：所有权策略门禁与 fail-closed 工厂 ``build_runtime``；
- ``registry``：持久化 ``TerminalRegistry``（原子写 + 跨进程锁）；
- ``attachments``：control lease（撤销不可复活）；
- ``observer``：pyte 自动化观察者（``fidelity=partial``，延迟导入）；
- ``driver``：``AutomationContext``（业务 driver 的受限作用面）。

**导入无副作用**：不导入 ``pyte`` / ``ctypes`` / ``psutil`` 等平台或可选依赖，
不创建目录、不探测环境；pyte 与 Windows 相关依赖全部延迟到构造函数中。

Windows 生产 backend（``backend.py`` / ``spawn_win.py`` / ``guard.py`` /
``identity.py`` / ``ipc.py`` / ``runner.py`` / ``emulator.py`` 等）与
服务层（``service.py``）由后续 TA 按 ``docs/design/PAN_TERMINAL_CORE_INTERFACES_20261003.md``
冻结的签名实现，本包当前不含它们。
"""

from __future__ import annotations

from .attachments import AttachmentRegistry
from .contracts import (
    DEFAULT_EOF_GRACE_SECONDS,
    DEFAULT_LEASE_GRACE_SECONDS,
    DEFAULT_OUTPUT_LOG_BYTES,
    DEFAULT_READ_SIZE,
    DEFAULT_STOP_CONFIRM_SECONDS,
    LEASE_ROLE_CONTROL,
    LEASE_ROLE_OBSERVER,
    TERMINAL_ID_PREFIX,
    AppliedSnapshot,
    AuthoritativeEmulator,
    AutomationDriver,
    BackendUnavailableError,
    CleanupReport,
    DetachHandler,
    DetachReport,
    DetachUnsupportedError,
    DrainStopReason,
    ExitInfo,
    ExternalOwnershipNotYetValidated,
    Fidelity,
    IdentityCheck,
    IdentityProbe,
    IllegalStateTransition,
    InvalidCursorError,
    LeaseToken,
    NotControlLeaseError,
    OutputChunk,
    OutputConsumer,
    OutputPage,
    OwnershipGateError,
    OwnershipMode,
    OwnershipPolicy,
    OwnershipPolicyRequired,
    ProcessIdentity,
    ProcessOwnershipEvidence,
    PtyBackend,
    Recovery,
    RegistryCorruptError,
    RegistryError,
    RegistryLockTimeout,
    RuntimeState,
    ScreenObserver,
    ScreenSnapshot,
    StaleLeaseError,
    StartupOwnershipGate,
    TerminalChannel,
    TerminalExistsError,
    TerminalLookup,
    TerminalRecord,
    TerminalScope,
    TerminateOutcome,
    TreeGuard,
    UnknownTerminalError,
    UnownedTreeRejected,
    WaitOutcome,
)
from .driver import AutomationContext
from .observer import ALT_SCREEN_DEC_MODES, PyteScreenObserver
from .output import OutputLog
from .ownership import NullTreeGuard, UnverifiedOwnershipGate, build_runtime
from .registry import TerminalRegistry
from .runtime import PtyRuntime

__all__ = [
    # contracts: 常量
    "TERMINAL_ID_PREFIX",
    "DEFAULT_OUTPUT_LOG_BYTES",
    "DEFAULT_READ_SIZE",
    "DEFAULT_EOF_GRACE_SECONDS",
    "DEFAULT_LEASE_GRACE_SECONDS",
    "DEFAULT_STOP_CONFIRM_SECONDS",
    "LEASE_ROLE_CONTROL",
    "LEASE_ROLE_OBSERVER",
    # contracts: 枚举
    "RuntimeState",
    "DrainStopReason",
    "IdentityCheck",
    "TerminateOutcome",
    "OwnershipMode",
    "Fidelity",
    "Recovery",
    # contracts: 异常
    "BackendUnavailableError",
    "InvalidCursorError",
    "IllegalStateTransition",
    "UnknownTerminalError",
    "StaleLeaseError",
    "NotControlLeaseError",
    "OwnershipPolicyRequired",
    "UnownedTreeRejected",
    "OwnershipGateError",
    "DetachUnsupportedError",
    "ExternalOwnershipNotYetValidated",
    "RegistryError",
    "RegistryLockTimeout",
    "RegistryCorruptError",
    "TerminalExistsError",
    # contracts: 数据
    "ProcessIdentity",
    "ProcessOwnershipEvidence",
    "ExitInfo",
    "CleanupReport",
    "OutputChunk",
    "OutputPage",
    "LeaseToken",
    "TerminalScope",
    "TerminalRecord",
    "ScreenSnapshot",
    "WaitOutcome",
    "AppliedSnapshot",
    "DetachReport",
    "OwnershipPolicy",
    # contracts: 类型别名
    "IdentityProbe",
    "OutputConsumer",
    "TerminalLookup",
    # contracts: Protocol
    "PtyBackend",
    "TreeGuard",
    "StartupOwnershipGate",
    "ScreenObserver",
    "AuthoritativeEmulator",
    "DetachHandler",
    "TerminalChannel",
    "AutomationDriver",
    # 实现
    "OutputLog",
    "PtyRuntime",
    "build_runtime",
    "UnverifiedOwnershipGate",
    "NullTreeGuard",
    "TerminalRegistry",
    "AttachmentRegistry",
    "PyteScreenObserver",
    "ALT_SCREEN_DEC_MODES",
    "AutomationContext",
]
