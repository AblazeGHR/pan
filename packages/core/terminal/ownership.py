"""所有权策略、启动门禁与 fail-closed 工厂（平台无关，P0）。

- ``OwnershipPolicy`` / ``OwnershipMode`` 定义在 ``contracts.py``（跨层共享数据）；
- 本模块只包含：``UnverifiedOwnershipGate``（四要素校验）、``NullTreeGuard``
  （显式“无 OS 级守卫”的测试/降级实现，必须显式确认）与 ``build_runtime`` 工厂。

**不把 bool 声明当真实 OS 保障**：门禁校验的是所有权证据的**完整性**；
``assigned`` / ``atomic_with_spawn`` 等字段的真实性必须由挂起式 spawn、原子
入组、同一 handle 绑定等真实原语产生（Windows backend 层，TA-B）。本层不做、
也无法做 OS 级证明。
"""

from __future__ import annotations

from typing import Any

from .contracts import (
    DEFAULT_EOF_GRACE_SECONDS,
    DEFAULT_OUTPUT_LOG_BYTES,
    DEFAULT_READ_SIZE,
    DetachUnsupportedError,
    ExternalOwnershipNotYetValidated,
    IdentityProbe,
    OutputConsumer,
    OwnershipGateError,
    OwnershipMode,
    OwnershipPolicy,
    OwnershipPolicyRequired,
    ProcessIdentity,
    ProcessOwnershipEvidence,
    UnownedTreeRejected,
)
from .runtime import PtyRuntime


class UnverifiedOwnershipGate:
    """按四要素校验启动所有权证据（fail-closed）。

    对应生产层要求：

    1. **spawn 后 assign 窗口**：``atomic_with_spawn`` 必须为真（挂起创建 ->
       赋值 -> 恢复），否则子进程可在窗口内派生逃逸；
    2. **assign 失败必须拒绝 running**：``assigned`` 为假即拒绝；
    3. **清理身份查验 + 同一 handle 终止**：``identity`` 非空且
       ``handle_bound_for_cleanup`` 为真。
    """

    def __init__(self, evidence: ProcessOwnershipEvidence) -> None:
        self.evidence = evidence

    def verify(self) -> None:
        ev = self.evidence
        if not ev.assigned:
            raise OwnershipGateError(
                "spawn 后 assign 失败或未赋值：拒绝进入 running（必须先终止再重试）"
            )
        if not ev.atomic_with_spawn:
            raise OwnershipGateError(
                "所有权赋值未与 spawn 原子化：存在 spawn->assign 逃逸窗口，拒绝进入 running"
            )
        if ev.identity is None:
            raise OwnershipGateError(
                "缺少进程身份（PID + 创建时间）：无法在清理时核验，拒绝进入 running"
            )
        if not ev.handle_bound_for_cleanup:
            raise OwnershipGateError(
                "清理未绑定赋值时的同一 handle：按 PID 重新打开可能在 PID 复用时误杀，"
                "拒绝进入 running"
            )


class NullTreeGuard:
    """显式“无 OS 级树守卫”。

    只在调用方显式 ``acknowledge_unowned_tree=True`` 时可用；用于测试与
    “确实没有内核级守卫”的诚实标注，**不是**生产默认，也**不是**整树所有权证明。
    """

    def describe(self) -> dict[str, Any]:
        return {
            "kind": "none",
            "os_level_guard": False,
            "note": "显式无 OS 级守卫：仅测试/降级标注，不构成整树所有权证明",
        }

    def owned_pids(self, root_pid: int | None) -> list[int]:
        return []

    def remaining(self, pids: Any) -> list[int]:
        return list(pids)

    def terminate_tree(
        self, root_pid: int | None, *, timeout: float
    ) -> tuple[list[int], list[int]]:
        return [], []


def build_runtime(
    terminal_id: str,
    backend: Any,
    *,
    ownership: OwnershipPolicy | None,
    acknowledge_unowned_tree: bool = False,
    require_startup_gate: bool | None = None,
    output_cap: int = DEFAULT_OUTPUT_LOG_BYTES,
    read_size: int = DEFAULT_READ_SIZE,
    eof_grace: float = DEFAULT_EOF_GRACE_SECONDS,
    identity: ProcessIdentity | None = None,
    identity_probe: IdentityProbe | None = None,
    output_consumer: OutputConsumer | None = None,
) -> PtyRuntime:
    """fail-closed 工厂：没有明确所有权策略就不产出 runtime。

    校验顺序（任一条不过就抛错，绝不静默降级）：

    1. ``ownership`` 必须有值（无默认布局）；
    2. ``mode=EXTERNAL`` -> 拒绝（语义未定义，不得仅凭声明放行）；
    3. 声明 ``DETACHED``/``detached`` 但未注入 ``detach_handler`` -> 拒绝
       （``DetachUnsupportedError``：不允许伪造 detach）；
    4. 声明了树守卫能力却给了 ``None`` 实现 -> 必须显式
       ``acknowledge_unowned_tree=True``；
    5. 有树守卫时默认要求启动所有权证据（``require_startup_gate`` 缺省为真）。
    """
    if ownership is None:
        raise OwnershipPolicyRequired(
            "必须显式传入 OwnershipPolicy：公共契约不预设「服务持 Job」或「runner 布局」"
        )
    if ownership.mode is OwnershipMode.EXTERNAL:
        raise ExternalOwnershipNotYetValidated(
            "外部所有者的默认寿命/崩溃/重连语义尚未定义（探针只覆盖 runner 自持布局）："
            "不得仅凭「外部进程持有」就放行。"
        )
    detach_expected = ownership.mode is OwnershipMode.DETACHED or ownership.detached
    if detach_expected and ownership.detach_handler is None:
        raise DetachUnsupportedError(
            "策略声明 detached/DETACHED 但未注入真实宿主能力（detach_handler）："
            "公共核心不伪造 durable 所有权（见接口文档 §detach）。"
        )
    if ownership.tree_guard is None and not acknowledge_unowned_tree:
        raise UnownedTreeRejected(
            f"策略声明 tree_guard_kind={ownership.tree_guard_kind!r} 但未提供实现；"
            "若确实没有内核级守卫，必须显式 acknowledge_unowned_tree=True 并在证据中标注"
        )
    gate_required = (
        ownership.tree_guard is not None if require_startup_gate is None else require_startup_gate
    )
    return PtyRuntime(
        terminal_id,
        backend,
        output_cap=output_cap,
        read_size=read_size,
        terminator=ownership.tree_guard,
        eof_grace=eof_grace,
        identity=identity,
        identity_probe=identity_probe,
        require_startup_gate=gate_required,
        ownership=ownership,
        output_consumer=output_consumer,
    )
