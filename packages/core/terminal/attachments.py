"""attachment / control lease（进程内多客户端单 writer，平台无关，P0）。

契约要点（对应契约报告 §2.5 / M8 / M14）：

1. 一个终端可有多个观察连接，同一时刻**只有一个输入控制权**；
2. **撤销不可复活**：``detach(token)`` 把 token 的 ``revocation_id`` 记入 revoked
   集合并（若是控制权）自增 generation；此后该 token 与同代副本的
   ``send``/``resize``/``transfer`` 全部被拒，不存在 setdefault 式复活路径；
3. **校验与操作在同一临界区**：attach/transfer/detach/send/resize 共用一把可重入
   锁，“校验 -> 写终端”原子，单 writer 顺序有保证；
4. ``role``/``client_id`` 是自报字段：控制权操作要求 token 与 registry 记录的
   控制权**同一 revocation_id**；伪造随机 id 的 token 被 ``NotControlLeaseError``
   拒绝；
5. 观察者不携带权限：其有效性只由“是否被撤销”决定；控制权转交/撤销**不会**误伤
   观察者，撤销一个观察者也不影响其它观察者。

**区别于 ``PtyRuntime.detach()``**：此处 ``detach(token)`` 是**连接级**撤销
（浏览器断开只释放连接，不改变进程寿命）；runtime detach 是**宿主级**所有权
移交，需要真实宿主能力。两者不可互相冒充。

**明确未实现（不在本层范围）**：真实身份认证与网络授权（把 token 绑定到登录
用户/连接/ACL）、跨进程 token 保密。已知边界：复制 ``revocation_id`` 的 token
会被接受（契约 M14.19 如实记录）——授权必须由 Pan 入口在**先**完成，本 registry
只解决进程内单 writer + 撤销语义。
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


class AttachmentRegistry:
    """控制权 lease 注册表。依赖一个 ``TerminalLookup``（``terminal_id`` ->
    可写目标；不存在/未授权返回 ``None``）。"""

    def __init__(self, lookup: TerminalLookup) -> None:
        self._lookup = lookup
        self._gen: dict[str, int] = {}
        self._control: dict[str, LeaseToken] = {}
        self._observers: dict[str, set[str]] = {}
        self._revoked: set[str] = set()
        # attach/transfer/detach/send/resize 共用一个锁：校验与操作原子。
        self._lock = threading.RLock()

    # -- 内部：仅在持锁时调用 -------------------------------------------
    def _new_token(
        self, terminal_id: str, client_id: str, role: str, rows: int, cols: int
    ) -> LeaseToken:
        generation = self._gen.get(terminal_id, 0)
        if role == LEASE_ROLE_CONTROL:
            generation += 1
            self._gen[terminal_id] = generation
            previous = self._control.get(terminal_id)
            if previous is not None:
                # 旧控制权立刻作废（转交/抢占），防止迟到消息继续操作终端。
                self._revoked.add(previous.revocation_id)
        return LeaseToken(
            terminal_id=terminal_id,
            client_id=client_id,
            generation=generation,
            role=role,
            rows=rows,
            cols=cols,
            revocation_id=uuid.uuid4().hex,
        )

    def _resolve_locked(
        self, token: LeaseToken, *, require_control: bool
    ):
        """校验 token 并返回可写目标。校验与调用方随后的操作在同一临界区。"""
        target = self._lookup(token.terminal_id)
        if target is None:
            # 不存在/未授权终端：不知道 ID 即无权限。
            raise UnknownTerminalError(token.terminal_id)
        if not token.revocation_id or token.revocation_id in self._revoked:
            raise StaleLeaseError("lease token 已被撤销")
        if not require_control:
            return target
        # 先判权限类别：观察者 token 的失败原因稳定为“无权限”，与世代无关。
        if token.role != LEASE_ROLE_CONTROL:
            raise NotControlLeaseError("observer lease 不能写入/改尺寸/转交控制权")
        current = self._gen.get(token.terminal_id, 0)
        if token.generation != current:
            raise StaleLeaseError(f"lease generation {token.generation} != current {current}")
        holder = self._control.get(token.terminal_id)
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
        with self._lock:
            token = self._new_token(terminal_id, client_id, role, rows, cols)
            if role == LEASE_ROLE_CONTROL:
                self._control[terminal_id] = token
            else:
                self._observers.setdefault(terminal_id, set()).add(client_id)
            return token

    def transfer_control(self, token: LeaseToken, *, to_client: str) -> LeaseToken:
        with self._lock:
            self._resolve_locked(token, require_control=True)
            new_token = self._new_token(
                token.terminal_id, to_client, LEASE_ROLE_CONTROL, token.rows, token.cols
            )
            self._control[token.terminal_id] = new_token
            return new_token

    def detach(self, token: LeaseToken) -> None:
        """撤销该 token（连接级）。

        撤销后其 ``send``/``resize``/``transfer`` 一律被拒，且**不可复活**。
        这与 ``PtyRuntime.detach()``（需要真实宿主能力的 durable 所有权移交）
        是完全不同的语义：本方法从不触碰 PTY 进程寿命。
        """
        with self._lock:
            tid = token.terminal_id
            self._revoked.add(token.revocation_id)
            holder = self._control.get(tid)
            if holder is not None and holder.revocation_id == token.revocation_id:
                del self._control[tid]
                # 控制权撤销同时自增 generation：任何同一代的副本/迟到消息全部失效。
                self._gen[tid] = self._gen.get(tid, 0) + 1
            else:
                self._observers.get(tid, set()).discard(token.client_id)

    def is_revoked(self, token: LeaseToken) -> bool:
        with self._lock:
            return (not token.revocation_id) or token.revocation_id in self._revoked

    def validate(self, token: LeaseToken) -> None:
        """观察者级校验（存在性 + 撤销）；不要求控制权。"""
        with self._lock:
            self._resolve_locked(token, require_control=False)

    def send(self, token: LeaseToken, data: bytes) -> int:
        with self._lock:
            target = self._resolve_locked(token, require_control=True)
            # 校验与写入同一临界区：单 writer 顺序有保证。
            return target.write(data)

    def resize(self, token: LeaseToken, rows: int, cols: int) -> bool:
        with self._lock:
            target = self._resolve_locked(token, require_control=True)
            return target.resize(rows, cols)

    def control_holder(self, terminal_id: str) -> LeaseToken | None:
        with self._lock:
            return self._control.get(terminal_id)
