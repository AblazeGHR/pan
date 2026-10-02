"""attachment / control lease（进程内多客户端单 writer，平台无关，P0；r2）。

契约要点（契约报告 §2.5 / M8 / M14 与 MA 审查 r2）：

1. 一个终端可有多个观察连接，同一时刻**只有一个输入控制权**；
2. **撤销不可复活**：``detach(token)`` 把 token 的 ``revocation_id`` 登记为已撤销
   并（若是控制权）自增 generation；此后该 token 与同代副本的
   ``send``/``resize``/``transfer`` 全部被拒，不存在 setdefault 式复活路径；
3. **校验与操作在同一临界区**（r2：每终端独立锁）：attach/transfer/detach/
   send/resize 在该终端的锁内“校验 -> 写终端”原子，单 writer 顺序有保证；
   **一个终端的慢写不阻塞其它终端**的撤销/操作，也不引入额外线程；
4. **已发出 lease 校验**（r2）：``validate`` 只接受本 registry 实际发出过、且未被
   撤销的 lease（当前控制权或已登记的观察者 token）；伪造随机
   ``revocation_id`` 的 token 一律 ``NotControlLeaseError``。已知边界：复制
   **已发出但仍有效** token 的 ``revocation_id`` 会被接受（M14.19 如实记录）——
   授权必须由 Pan 入口在先完成；
5. 观察者不携带权限：控制权转交/撤销**不会**误伤观察者；撤销一个观察者 token
   也不影响同一 client 的其它 observer token（按 ``revocation_id`` 精确撤销，
   不按自报 ``client_id`` 批量失效）；
6. ``forget(terminal_id)``：终态 terminal 可安全回收 lease 资料（防无界增长）；
   之后该 terminal 的旧 token 一律 ``StaleLeaseError``（不可复活）、
   ``control_holder`` 为 ``None``、新 attach 拒绝。每个终态 terminal 只保留
   一个常量级状态对象（含锁），不再随 token 数增长。

**区别于 ``PtyRuntime.detach()``**：此处 ``detach(token)`` 是**连接级**撤销
（浏览器断开只释放连接，不改变进程寿命）；runtime detach 是**宿主级**所有权
移交，需要真实宿主能力。两者不可互相冒充。

**明确未实现（不在本层范围）**：真实身份认证与网络授权（把 token 绑定到登录
用户/连接/ACL）、跨进程 token 保密。
"""

from __future__ import annotations

import threading
import uuid

from .contracts import (
    LEASE_ROLE_CONTROL,
    LEASE_ROLE_OBSERVER,
    LeaseToken,
    NotControlLeaseError,
    StaleLeaseError,
    TerminalLookup,
    UnknownTerminalError,
)


class _TerminalLeaseState:
    """单个终端的 lease 状态（含独立锁；forget 后保留为常量级墓碑）。"""

    __slots__ = ("lock", "generation", "control", "observers", "revoked", "forgotten")

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.generation = 0
        self.control: LeaseToken | None = None
        self.observers: dict[str, set[str]] = {}
        self.revoked: set[str] = set()
        self.forgotten = False


class AttachmentRegistry:
    """控制权 lease 注册表。依赖一个 ``TerminalLookup``（``terminal_id`` ->
    可写目标；不存在/未授权返回 ``None``）。"""

    def __init__(self, lookup: TerminalLookup) -> None:
        self._lookup = lookup
        self._states: dict[str, _TerminalLeaseState] = {}
        self._states_lock = threading.Lock()

    # -- 内部 -----------------------------------------------------------
    def _state_for(self, terminal_id: str) -> _TerminalLeaseState:
        with self._states_lock:
            state = self._states.get(terminal_id)
            if state is None:
                state = _TerminalLeaseState()
                self._states[terminal_id] = state
            return state

    def _new_token_locked(
        self,
        state: _TerminalLeaseState,
        terminal_id: str,
        client_id: str,
        role: str,
        rows: int,
        cols: int,
    ) -> LeaseToken:
        generation = state.generation
        if role == LEASE_ROLE_CONTROL:
            generation += 1
            state.generation = generation
            previous = state.control
            if previous is not None:
                # 旧控制权立刻作废（转交/抢占），防止迟到消息继续操作终端。
                state.revoked.add(previous.revocation_id)
        return LeaseToken(
            terminal_id=terminal_id,
            client_id=client_id,
            generation=generation,
            role=role,
            rows=rows,
            cols=cols,
            revocation_id=uuid.uuid4().hex,
        )

    def _require_issued_locked(self, state: _TerminalLeaseState, token: LeaseToken) -> None:
        """确认 token 是本 registry 实际发出过、且未被撤销的 lease。"""
        if token.role == LEASE_ROLE_CONTROL:
            holder = state.control
            if holder is not None and holder.revocation_id == token.revocation_id:
                return
        else:
            issued = state.observers.get(token.client_id)
            if issued and token.revocation_id in issued:
                return
        raise NotControlLeaseError("token 不是本 registry 发出的有效 lease")

    def _resolve_locked(self, state: _TerminalLeaseState, token: LeaseToken, *, require_control: bool):
        """校验 token 并返回可写目标（调用方在同一临界区内完成操作）。"""
        target = self._lookup(token.terminal_id)
        if target is None:
            # 不存在/未授权终端：不知道 ID 即无权限。
            raise UnknownTerminalError(token.terminal_id)
        if state.forgotten:
            raise StaleLeaseError("terminal 已终结（forget）：旧 lease 一律失效")
        if not token.revocation_id or token.revocation_id in state.revoked:
            raise StaleLeaseError("lease token 已被撤销")
        if not require_control:
            self._require_issued_locked(state, token)
            return target
        # 先判权限类别：观察者 token 的失败原因稳定为“无权限”，与世代无关。
        if token.role != LEASE_ROLE_CONTROL:
            raise NotControlLeaseError("observer lease 不能写入/改尺寸/转交控制权")
        if token.generation != state.generation:
            raise StaleLeaseError(f"lease generation {token.generation} != current {state.generation}")
        holder = state.control
        if holder is None or holder.revocation_id != token.revocation_id:
            raise NotControlLeaseError(
                "token 不是当前控制权持有者（已被撤销、已转交或为伪造 token）"
            )
        return target

    # -- 公开 API -------------------------------------------------------
    def attach(
        self,
        terminal_id: str,
        client_id: str,
        *,
        role: str = LEASE_ROLE_OBSERVER,
        rows: int = 0,
        cols: int = 0,
    ) -> LeaseToken:
        if role not in (LEASE_ROLE_CONTROL, LEASE_ROLE_OBSERVER):
            raise ValueError(f"invalid lease role: {role!r}")
        if self._lookup(terminal_id) is None:
            raise UnknownTerminalError(terminal_id)
        state = self._state_for(terminal_id)
        with state.lock:
            if state.forgotten:
                raise StaleLeaseError("terminal 已终结（forget）：不再签发 lease")
            token = self._new_token_locked(state, terminal_id, client_id, role, rows, cols)
            if role == LEASE_ROLE_CONTROL:
                state.control = token
            else:
                state.observers.setdefault(client_id, set()).add(token.revocation_id)
            return token

    def transfer_control(self, token: LeaseToken, *, to_client: str) -> LeaseToken:
        state = self._state_for(token.terminal_id)
        with state.lock:
            self._resolve_locked(state, token, require_control=True)
            new_token = self._new_token_locked(
                state, token.terminal_id, to_client, LEASE_ROLE_CONTROL, token.rows, token.cols
            )
            state.control = new_token
            return new_token

    def detach(self, token: LeaseToken) -> None:
        """撤销该 token（连接级）。

        按 ``revocation_id`` 精确撤销：只影响该 token（同一 client 的其它
        observer token 不受影响）。撤销后其 ``send``/``resize``/``transfer``
        一律被拒，且**不可复活**。这与 ``PtyRuntime.detach()``（需要真实宿主
        能力的 durable 所有权移交）是完全不同的语义：本方法从不触碰 PTY
        进程寿命。
        """
        state = self._state_for(token.terminal_id)
        with state.lock:
            state.revoked.add(token.revocation_id)
            holder = state.control
            if holder is not None and holder.revocation_id == token.revocation_id:
                state.control = None
                # 控制权撤销同时自增 generation：任何同一代的副本/迟到消息全部失效。
                state.generation += 1
            else:
                issued = state.observers.get(token.client_id)
                if issued is not None and token.revocation_id in issued:
                    issued.discard(token.revocation_id)
                    if not issued:
                        del state.observers[token.client_id]

    def forget(self, terminal_id: str) -> None:
        """终态 terminal 的 lease 资料回收（可不调用；调用后不可复活）。

        之后：旧 token 一律 ``StaleLeaseError``、``control_holder`` 为 ``None``、
        新 attach 拒绝。状态对象（含锁）保留为常量级墓碑，不随 token 数增长。
        """
        state = self._state_for(terminal_id)
        with state.lock:
            state.forgotten = True
            state.control = None
            state.observers.clear()
            state.revoked.clear()
            state.generation += 1

    def is_revoked(self, token: LeaseToken) -> bool:
        state = self._state_for(token.terminal_id)
        with state.lock:
            return (
                state.forgotten
                or (not token.revocation_id)
                or token.revocation_id in state.revoked
            )

    def validate(self, token: LeaseToken) -> None:
        """校验 lease 有效性（存在性 + 撤销 + 实际发出性）；不要求控制权。"""
        state = self._state_for(token.terminal_id)
        with state.lock:
            self._resolve_locked(state, token, require_control=False)

    def send(self, token: LeaseToken, data: bytes) -> int:
        state = self._state_for(token.terminal_id)
        with state.lock:
            target = self._resolve_locked(state, token, require_control=True)
            # 校验与写入同一临界区：单 writer 顺序有保证。
            return target.write(data)

    def resize(self, token: LeaseToken, rows: int, cols: int) -> bool:
        state = self._state_for(token.terminal_id)
        with state.lock:
            target = self._resolve_locked(state, token, require_control=True)
            return target.resize(rows, cols)

    def control_holder(self, terminal_id: str) -> LeaseToken | None:
        state = self._state_for(terminal_id)
        with state.lock:
            return state.control
