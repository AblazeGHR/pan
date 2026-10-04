"""Pan Terminal WebSocket 桥（P2 WS 桥第一批）。

契约来源：``docs/design/PAN_TERMINAL_WS_BRIEF_20261004.md``（MA 已决任务书，
含 2026-10-04「拒绝编码校准」）与
``docs/design/PAN_TERMINAL_WS_INTERFACES_20261004.md``（本批成稿）。
下游实现依据：``packages/core/terminal/service.py``（已接受同步控制层）。

职责边界（本模块只做四件事）：

1. **握手前拒绝（MA 校准 A）**：accept **之前**严格复用 REST 的
   Origin/Host/Fetch/ready 门（WS 不要求 POST content-type），再校验
   terminal id 与 query cursor，然后 ``runtime.call(service.get)`` 确认对象
   合法。支持 ``websocket.http.response`` 扩展时用**标准 HTTP 状态** +
   静态 JSON 拒绝；无扩展时 accept 前 ``close`` 回退到 HTTP 403。
   **不向 HTTP 填 4400/4403/4404/1013**，也**不为编码而先 accept**。
2. **连接身份与所有权**：每连接由 server 生成唯一 ``connection_id``
   （**不用**浏览器自报 client_id），server 持有 ``LeaseToken``；连接默认
   observer，``claim`` 才升级为 control。浏览器永不得到 runner token/pipe/
   secret，也拿不到 ``revocation_id`` 或任何可重建凭据。
3. **单 sender + 有界队列**：reader 供输出、receiver 串行处理输入、sender 是
   **唯一**发送者；共用有界 outbound 队列（≤4MiB，**按 UTF-8 序列化线上字节**
   计且**含正在 send 的帧**，另 ≤256 项）。满或单次 send 超时 → 判 slow-client
   并断开（1013），**不**阻塞 runner reader、**不**默默丢字节。
4. **收尾纪律**：停 reader/receiver/sender 并等共享短 deadline，再在事件循环外
   ``service.release_attachment(server token)``（有界等待、保留迟到
   worker/result）。**只撤销连接**，绝不 ``close``/``detach``/停所有者心跳。

**禁止 import ``packages.web.server``**（避免循环依赖）：实际 server 只做
``app.include_router(terminal_ws.router)`` 与 lifespan 嵌套薄接入。

## 诚实边界（不夸大）

- 队列上界是**本层发送侧**纪律；**不**声称整个网络栈或 WS transport 的硬
  RSS 限额（transport 自身可能先缓存一帧）。
- 握手拒绝的 HTTP 状态/JSON **不保证**被浏览器原生 ``WebSocket`` 暴露
  （浏览器只看到连接失败）；分类可另用已有 REST 接口获取。
- 预算是**调用方侧有界等待**，**非** OS 硬 SLA：在途同步输入无法被 async
  取消停止，核心输入门/lease 纪律仍是最终依据。
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import json
import threading
import uuid
from contextlib import asynccontextmanager
from typing import Any, Mapping
from urllib.parse import urlsplit

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from packages.core.terminal.contracts import (
    LEASE_ROLE_CONTROL,
    LEASE_ROLE_OBSERVER,
    LeaseToken,
    NotControlLeaseError,
    StaleLeaseError,
    UnknownTerminalError,
)
from packages.core.terminal.service import (
    CapacityExceeded,
    CleanupUnconfirmed,
    DetachRefused,
    ServiceClosingDown,
    StartupFailed,
    TerminalNotAttached,
)
from packages.web import terminal_api
from packages.web.terminal_api import (
    COLS_MAX,
    COLS_MIN,
    ROWS_MAX,
    ROWS_MIN,
    SNAPSHOT_SCREEN_MAX,
    TIMEOUT_DEFAULT,
    TIMEOUT_MAX,
    TIMEOUT_MIN,
    Busy,
    GateRejected,
    TerminalRuntime,
    _bounded_field,
    _normalize_authority,
    _parse_uint64,
    normalize_origin,
    validate_terminal_id,
)

router = APIRouter(tags=["terminal-ws"])


# ══════════════════════════════════════════════════════════════════════════
# 常量（已决边界）
# ══════════════════════════════════════════════════════════════════════════

#: 每 app 最多同时存在的 WS 连接数（accept **前**预留；未收敛连接仍占预算）。
DEFAULT_MAX_CONNECTIONS = 32
#: 单条**接收**帧上限（先测 UTF-8 字节，再解析）。
MAX_INBOUND_FRAME_BYTES = 256 * 1024
#: ``input`` 解码后的裸字节上限。
MAX_INPUT_BYTES = 128 * 1024
#: outbound 队列字节上限：4MiB，**按 UTF-8 序列化线上字节**计，**含在途帧**。
MAX_OUTBOUND_BYTES = 4 * 1024 * 1024
#: outbound 队列项数上限。
MAX_OUTBOUND_ITEMS = 256
#: 单次 send 等待上限（超时即 slow-client）。
SEND_TIMEOUT_SECONDS = 2.0
#: 单连接收尾的共享短预算（停三个任务 + 释放 lease）。
CONNECTION_CLOSE_BUDGET = 2.0
#: 一轮 read 的最大字节数。
READ_CHUNK_BYTES = 32 * 1024
#: 空读后的最小等待（不忙循环）。
POLL_IDLE_SECONDS = 0.05
#: 协议版本。
PROTOCOL_VERSION = 1

#: 握手拒绝用的**标准** HTTP 状态（**不**使用 4400/4403/4404/1013 私有码）。
STATUS_FORBIDDEN = 403
STATUS_BAD_REQUEST = 400
STATUS_NOT_FOUND = 404
STATUS_UNAVAILABLE = 503
STATUS_CAPACITY = 429

#: 终态（可发 terminal-state 并正常关连接；**不**假 output_complete）。
TERMINAL_END_STATES = frozenset({"exited", "lost", "cleanup-failed"})

#: 握手拒绝的静态 code → 标准 HTTP 状态（MA 校准 A）。
_HANDSHAKE_STATUS: dict[str, int] = {
    "forbidden-origin": STATUS_FORBIDDEN,
    "forbidden-host": STATUS_FORBIDDEN,
    "forbidden-fetch-site": STATUS_FORBIDDEN,
    "invalid-terminal-id": STATUS_BAD_REQUEST,
    "invalid-cursor": STATUS_BAD_REQUEST,
    "unknown-terminal": STATUS_NOT_FOUND,
    "terminal-disabled": STATUS_UNAVAILABLE,
    "not-ready": STATUS_UNAVAILABLE,
    "terminal-unavailable": STATUS_UNAVAILABLE,
    "closing": STATUS_UNAVAILABLE,
    "connection-capacity-exceeded": STATUS_CAPACITY,
}


class ProtocolError(Exception):
    """入站命令的静态拒绝（只回静态 code，**不**回显请求/异常文本）。"""

    def __init__(self, code: str) -> None:
        self.code = str(code)
        super().__init__(self.code)


class SlowClient(Exception):
    """队列越界或单次 send 超时（明确 slow-client → 1013）。"""


# ══════════════════════════════════════════════════════════════════════════
# 入站命令白名单（op 与字段严格白名单）
# ══════════════════════════════════════════════════════════════════════════

#: op → 允许出现的字段集合（``v``/``type``/``terminal_id``/``op`` 之外）。
_COMMAND_FIELDS: dict[str, frozenset[str]] = {
    "claim": frozenset(),
    "release": frozenset(),
    "input": frozenset({"data_b64", "generation", "seq"}),
    "resize": frozenset({"rows", "cols", "generation"}),
    "ack": frozenset({"next_seq"}),
    "resume": frozenset({"cursor"}),
    "snapshot": frozenset({"timeout_ms"}),
    "ping": frozenset(),
}

_ENVELOPE_FIELDS = frozenset({"v", "type", "terminal_id", "op"})


def _event(name: str, terminal_id: str, **fields: Any) -> dict[str, Any]:
    """出站包络 ``{v:1,type:<静态>,terminal_id,...}``。

    ``terminal_id``/``type``/``v`` 以**连接绑定值**为准：即便 ``fields`` 里带了
    同名键（复用 REST 投影时可能带 ``terminal_id``）也**不**被覆盖。
    """
    payload: dict[str, Any] = {"v": PROTOCOL_VERSION, "type": name, "terminal_id": terminal_id}
    for key, value in fields.items():
        if key not in ("v", "type", "terminal_id"):
            payload[key] = value
    return payload


def _dec(value: Any) -> str:
    """游标/序号 → ASCII 十进制字符串（线上 uint64 一律字符串）。"""
    return str(int(value))


# ══════════════════════════════════════════════════════════════════════════
# 握手前 gate（复用 REST 入口检查；WS 无 POST content-type）
# ══════════════════════════════════════════════════════════════════════════


def check_ws_gate(websocket: WebSocket, runtime: TerminalRuntime) -> None:
    """**握手前**入口检查：与 REST ``check_gate`` 同一套 Origin/Host/Fetch/ready 门。

    与 REST 的唯一差别：``WebSocket`` **没有** ``method`` 属性，因此**不做**
    POST content-type 检查 —— 绝不把不存在的 ``WebSocket.method`` 当 POST 判定。
    转发头（``X-Forwarded-*``）与查询串里的 origin 一律不作为身份。
    """
    if not runtime.allowlist.config_valid:
        raise GateRejected(STATUS_FORBIDDEN, "forbidden-origin")
    origin_raw = websocket.headers.get("origin")
    if origin_raw is None:
        raise GateRejected(STATUS_FORBIDDEN, "forbidden-origin")
    normalized = normalize_origin(origin_raw)
    if normalized is None or normalized not in runtime.allowlist.origins:
        raise GateRejected(STATUS_FORBIDDEN, "forbidden-origin")
    authority = _normalize_authority(urlsplit(normalized).netloc)
    host_raw = websocket.headers.get("host")
    if authority is None or _normalize_authority(host_raw) != authority:
        raise GateRejected(STATUS_FORBIDDEN, "forbidden-host")
    fetch_site = websocket.headers.get("sec-fetch-site")
    if fetch_site is not None and fetch_site.strip().lower() not in ("same-origin", "same-site"):
        raise GateRejected(STATUS_FORBIDDEN, "forbidden-fetch-site")
    runtime.check_ready()


def parse_query_cursor(websocket: WebSocket) -> int:
    """初始游标取自 query ``cursor``（缺省 0；**严格**校验，前导零规范化）。"""
    raw = websocket.query_params.get("cursor")
    if raw is None:
        return 0
    value = _parse_uint64(raw)
    if value is None:
        raise GateRejected(STATUS_BAD_REQUEST, "invalid-cursor")
    return int(value)


async def send_handshake_denial(websocket: WebSocket, code: str) -> bool:
    """accept **前**拒绝；返回是否经 denial 扩展送达。

    支持 ``websocket.http.response`` 时发静态 JSON + **标准** HTTP 状态；不
    支持时回退 accept 前 ``close``（客户端观察为 HTTP 403）。任何情况下都
    **不**为编码而先 accept，也**不**向 HTTP 填 4400/4403/4404/1013。
    """
    status = _HANDSHAKE_STATUS.get(str(code), STATUS_FORBIDDEN)
    if "websocket.http.response" in websocket.scope.get("extensions", {}):
        await websocket.send_denial_response(
            JSONResponse({"ok": False, "error": {"code": str(code)}}, status_code=status)
        )
        return True
    await websocket.close()
    return False


# ══════════════════════════════════════════════════════════════════════════
# 出站队列（单 sender；有界；含在途帧；epoch 丢弃旧 output）
# ══════════════════════════════════════════════════════════════════════════


class _Frame:
    """一帧待发数据。

    ``marker`` 是 output 帧的 ``next_seq``（成功后用于推进**已发送**末尾）；
    ``epoch`` 非 ``None`` 表示该帧属于某个 stream epoch（resume 后旧 epoch
    的 output 必须丢弃，避免新旧混流）。
    """

    __slots__ = ("text", "size", "marker", "epoch")

    def __init__(self, text: str, *, marker: int | None = None, epoch: int | None = None) -> None:
        self.text = text
        self.size = len(text.encode("utf-8"))
        self.marker = marker
        self.epoch = epoch


class _Outbound:
    """单连接的有界 outbound 队列 + **唯一** sender。

    字节按 ``len(text.encode("utf-8"))``（**线上**序列化字节）计，且在 send
    完成前**保留在途帧的记账** —— 队列上界因此含正在发送的帧。
    """

    def __init__(
        self,
        websocket: WebSocket,
        *,
        on_sent: Any = None,
        max_bytes: int = MAX_OUTBOUND_BYTES,
        max_items: int = MAX_OUTBOUND_ITEMS,
        send_timeout: float = SEND_TIMEOUT_SECONDS,
    ) -> None:
        self._ws = websocket
        self._on_sent = on_sent
        self._max_bytes = int(max_bytes)
        self._max_items = int(max_items)
        self._send_timeout = float(send_timeout)
        self._queue: list[_Frame] = []
        self._queued_bytes = 0
        self._inflight_bytes = 0
        self.send_lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self.drained = asyncio.Event()
        self.drained.set()
        self._closed = False
        #: sender 因 slow-client 自行退出时置位（外层据此 close(1013)）。
        self.slow = False

    # -- 记账（测试可观测） ---------------------------------------------
    @property
    def queued_items(self) -> int:
        return len(self._queue)

    @property
    def queued_bytes(self) -> int:
        return self._queued_bytes

    @property
    def inflight_bytes(self) -> int:
        return self._inflight_bytes

    @property
    def total_bytes(self) -> int:
        """已排队 + 在途（本层发送侧真实占用）。"""
        return self._queued_bytes + self._inflight_bytes

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    @property
    def max_items(self) -> int:
        return self._max_items

    # -- 生产 ----------------------------------------------------------
    def enqueue(self, frame: _Frame) -> None:
        """非阻塞入队；越界即 :class:`SlowClient`（由调用方断开）。"""
        if self._closed:
            raise SlowClient("closed")
        if len(self._queue) + bool(self._inflight_bytes) + 1 > self._max_items:
            raise SlowClient("queue-items-exceeded")
        if self.total_bytes + frame.size > self._max_bytes:
            raise SlowClient("queue-bytes-exceeded")
        self._queue.append(frame)
        self.drained.clear()
        self._queued_bytes += frame.size
        self._wake.set()

    def close(self) -> None:
        self._closed = True
        self._wake.set()

    # -- 消费（唯一 sender） ---------------------------------------------
    async def sender_loop(self, epoch: Any = None) -> None:
        """**唯一**发送者：逐帧 send；单次等待有界；旧 epoch 的 output 丢弃。"""
        while True:
            while not self._queue and not self._closed:
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=0.2)
                except asyncio.TimeoutError:
                    continue
            if not self._queue:
                if self._closed:
                    return
                continue
            frame = self._queue.pop(0)
            self._queued_bytes -= frame.size
            # 在途记账保留到 send 完成（含在途帧的上界口径）。
            self._inflight_bytes = frame.size
            try:
                if frame.epoch is None:
                    await asyncio.wait_for(self._ws.send_text(frame.text), timeout=self._send_timeout)
                else:
                    async with self.send_lock:
                        if epoch is not None and frame.epoch != epoch():
                            continue
                        await asyncio.wait_for(self._ws.send_text(frame.text), timeout=self._send_timeout)
                        if frame.marker is not None and self._on_sent is not None:
                            self._on_sent(frame.marker)
            except asyncio.TimeoutError:
                self._closed = True
                self.slow = True
                raise SlowClient("send-timeout")
            except Exception as exc:  # noqa: BLE001 - 发送失败即停止本连接
                self._closed = True
                raise SlowClient("send-failed") from exc
            finally:
                self._inflight_bytes = 0
                if not self._queue:
                    self.drained.set()


# ══════════════════════════════════════════════════════════════════════════
# 单连接
# ══════════════════════════════════════════════════════════════════════════


class _Connection:
    """一个 WS 连接：server 持有 lease，浏览器只发命令、收事件。"""

    def __init__(
        self,
        websocket: WebSocket,
        terminal_id: str,
        runtime: TerminalRuntime,
        *,
        cursor: int,
    ) -> None:
        self.websocket = websocket
        self.terminal_id = str(terminal_id)
        self.runtime = runtime
        #: server 生成的连接身份（**不**采信浏览器自报 client_id）。
        self.connection_id = f"conn_{uuid.uuid4().hex}"
        self.cursor = int(cursor)
        #: 已被 sender **实际成功发送**的 output 末尾（ack 上界）。
        self.sent_end = self.cursor
        self.ack_watermark = self.cursor
        self._ack_watermark = self.cursor
        self._stream_epoch = 0
        self._token: LeaseToken | None = None
        self._paused = False
        self._closing = False
        self._terminal_sent = False
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self._close_task: asyncio.Task | None = None
        self.outbound = _Outbound(websocket, on_sent=self._on_output_sent)
        #: 在途 lease 操作（迟到 token 必须被真实回收，不能只丢 wrapper）。
        self._lease_ops: set[asyncio.Task] = set()
        self._late_tokens: list[LeaseToken] = []
        self._release_ops: dict[int, asyncio.Task] = {}
        self._manager: _Manager | None = None

    # -- 观测（只读事实） -----------------------------------------------
    @property
    def role(self) -> str:
        return self._token.role if self._token is not None else LEASE_ROLE_OBSERVER

    @property
    def control_generation(self) -> int | None:
        if self._token is not None and self._token.role == LEASE_ROLE_CONTROL:
            return int(self._token.generation)
        return None

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def stream_epoch(self) -> int:
        return self._stream_epoch

    @property
    def tasks(self) -> list[asyncio.Task]:
        return list(self._tasks)

    @property
    def lease_op_count(self) -> int:
        return len(self._lease_ops)

    def _on_output_sent(self, next_seq: int) -> None:
        """sender 实际发送成功后才推进 ``sent_end``（不是入队 frontier）。"""
        if int(next_seq) > self.sent_end:
            self.sent_end = int(next_seq)

    # -- 发送（唯一出口） ------------------------------------------------
    def send_event(self, name: str, *, marker: int | None = None, epoch: int | None = None, **fields: Any) -> None:
        self.outbound.enqueue(
            _Frame(
                json.dumps(_event(name, self.terminal_id, **fields), ensure_ascii=False),
                marker=marker,
                epoch=epoch,
            )
        )

    def send_error(self, code: str) -> None:
        self.send_event("error", code=str(code))

    # -- 生命周期 -------------------------------------------------------
    def start(self) -> None:
        self._tasks = [
            asyncio.create_task(_reader_loop(self), name=f"{self.connection_id}-reader"),
            asyncio.create_task(_receiver_loop(self), name=f"{self.connection_id}-receiver"),
            asyncio.create_task(
                self.outbound.sender_loop(lambda: self._stream_epoch),
                name=f"{self.connection_id}-sender",
            ),
        ]

    async def close(self) -> dict[str, Any]:
        """收尾：**单飞**、有界；未收敛项保留引用（不把 cancel 当清理证明）。"""
        if self._close_task is not None and not self._close_task.done():
            return await asyncio.shield(self._close_task)
        self._closing = True
        self._stop.set()
        self._close_task = asyncio.create_task(self._close_impl(), name=f"{self.connection_id}-close")
        return await asyncio.shield(self._close_task)

    def close_task(self) -> asyncio.Task | None:
        """本连接的收尾任务引用（未收敛时供管理器保留观测）。"""
        return self._close_task

    async def _close_impl(self) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + CONNECTION_CLOSE_BUDGET
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            remaining = max(0.0, deadline - loop.time())
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=remaining)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass  # cancelled task is settled; pending thread operations are tracked separately
            except Exception:  # noqa: BLE001 - 任务异常在此消费
                pass
        self.outbound.close()
        # 迟到 lease：在途 attach 完成后必须被**真实回收**。
        if self._lease_ops:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(
                    asyncio.shield(asyncio.gather(*self._lease_ops, return_exceptions=True)),
                    timeout=max(0.0, deadline - loop.time()),
                )
        if self._token is not None:
            self._retain_token(self._token)
            self._token = None
        for token in list(self._late_tokens):
            await self._release_token(token, deadline=deadline)
        released = not self._late_tokens and not self._lease_ops and not self._release_ops
        return {
            "connection_id": self.connection_id,
            "tasks_converged": all(task.done() for task in self._tasks) and not self._lease_ops,
            "lease_released": released,
            "late_tokens_pending": len(self._late_tokens),
        }

    async def _release_own_lease(self) -> bool:
        """释放本连接**实际**持有的 token（离事件循环、有界等待）。

        **只撤销连接**：绝不 ``close``/``detach``/停所有者心跳。
        """
        token = self._token
        if token is None:
            return True
        self._retain_token(token)
        self._token = None
        return await self._release_token(token)

    def _retain_token(self, token: LeaseToken) -> None:
        if not any(item is token for item in self._late_tokens):
            self._late_tokens.append(token)

    def _wake_cleanup(self) -> None:
        if self._closing and self._manager is not None:
            self._manager.retry_cleanup(self)

    async def _release_token(self, token: LeaseToken, *, deadline: float | None = None) -> bool:
        self._retain_token(token)
        service = self.runtime.service
        if service is None:
            return False
        key = id(token)
        task = self._release_ops.get(key)
        if task is None:
            task = asyncio.create_task(asyncio.to_thread(service.release_attachment, token))
            self._release_ops[key] = task
            def settled(done: asyncio.Task) -> None:
                self._release_ops.pop(key, None)
                if not done.cancelled() and done.exception() is None:
                    self._late_tokens[:] = [item for item in self._late_tokens if item is not token]
                    self._wake_cleanup()
            task.add_done_callback(settled)
        try:
            await asyncio.wait_for(
                asyncio.shield(task),
                timeout=CONNECTION_CLOSE_BUDGET if deadline is None else max(0.0, deadline - asyncio.get_running_loop().time()),
            )
            return True
        except asyncio.TimeoutError:
            return False
        except Exception:  # noqa: BLE001 - 释放失败不外泄文本
            return False

    async def _await_lease(self, coro_factory: Any) -> LeaseToken:
        """执行同步 lease 签发；连接已关闭时**真实回收**迟到 token。"""
        if self._closing:
            raise asyncio.CancelledError
        async def issue() -> LeaseToken:
            token = await self.runtime.call(coro_factory)
            self._retain_token(token)  # ownership precedes delivery to the cancellable caller
            return token
        task = asyncio.create_task(issue())
        self._lease_ops.add(task)
        def settled(done: asyncio.Task) -> None:
            self._lease_ops.discard(done)
            if not done.cancelled():
                done.exception()  # consume failure, retain successful token above
            self._wake_cleanup()
        task.add_done_callback(settled)
        token = await asyncio.shield(task)
        if self._closing:
            # claim/attach 线程完成前已断开：迟到发出的 token 必须被回收。
            raise asyncio.CancelledError
        self._late_tokens[:] = [item for item in self._late_tokens if item is not token]
        self._token = token
        return token


# ══════════════════════════════════════════════════════════════════════════
# 连接管理器（每 app；32 预算；未收敛仍占预算）
# ══════════════════════════════════════════════════════════════════════════


class _Manager:
    """每 app 一个：连接预算、准入、收尾聚合。"""

    def __init__(
        self, runtime: TerminalRuntime, *, max_connections: int = DEFAULT_MAX_CONNECTIONS
    ) -> None:
        self.runtime = runtime
        self.max_connections = int(max_connections)
        self._conns: dict[str, _Connection] = {}
        self._pending: dict[str, asyncio.Task] = {}
        self._owners: dict[str, _Connection] = {}
        self._reapers: dict[str, asyncio.Task] = {}
        self._closing = False
        self._lock = threading.Lock()

    # -- 预算（accept 前预留） -------------------------------------------
    def reserve(self) -> str | None:
        """accept **前**容量预留；返回预留键，满则 ``None``。

        未收敛连接**仍占预算**（不超发）；预留键必须由 :meth:`commit` 消费或
        由 :meth:`release` 归还，否则该额度一直被占用。
        """
        with self._lock:
            if self._closing or len(set(self._conns) | set(self._pending)) >= self.max_connections:
                return None
            key = f"pending:{uuid.uuid4().hex}"
            self._conns[key] = None  # type: ignore[assignment]
            return key

    def commit(self, reservation: str, conn: _Connection) -> bool:
        """把预留额度转交给真实连接。"""
        with self._lock:
            if self._closing or reservation not in self._conns:
                self._conns.pop(reservation, None)
                return False
            del self._conns[reservation]
            self._conns[conn.connection_id] = conn
            conn._manager = self
            return True

    def release(self, reservation: str) -> None:
        """归还预留额度（accept 失败/拒绝时必须调用）。"""
        with self._lock:
            self._conns.pop(reservation, None)

    def remove(self, conn: _Connection) -> None:
        with self._lock:
            self._conns.pop(conn.connection_id, None)

    def track_pending(self, conn: _Connection, task: asyncio.Task) -> None:
        with self._lock:
            self._pending[conn.connection_id] = task
            self._owners[conn.connection_id] = conn

    def clear_pending(self, conn: _Connection) -> None:
        with self._lock:
            self._pending.pop(conn.connection_id, None)
            self._owners.pop(conn.connection_id, None)

    async def finish_cleanup(self, conn: _Connection) -> None:
        report = await conn.close()
        if report["tasks_converged"] and report["lease_released"] and not report["late_tokens_pending"]:
            self.clear_pending(conn)
        else:
            self.track_pending(conn, conn.close_task())
        self.remove(conn)

    def retry_cleanup(self, conn: _Connection) -> None:
        existing = self._reapers.get(conn.connection_id)
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(self.finish_cleanup(conn))
        self._reapers[conn.connection_id] = task
        def settled(done: asyncio.Task) -> None:
            self._reapers.pop(conn.connection_id, None)
            if not done.cancelled():
                done.exception()
        task.add_done_callback(settled)

    @property
    def active(self) -> int:
        with self._lock:
            return sum(1 for value in self._conns.values() if value is not None)

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "reserved": len(set(self._conns) | set(self._pending)),
                "active": sum(1 for value in self._conns.values() if value is not None),
                "pending_cleanup": len(self._pending),
            }

    async def shutdown(self) -> None:
        """停准入并关闭全部连接（共享短预算）。"""
        with self._lock:
            self._closing = True
            conns = {value.connection_id: value for value in self._conns.values() if value is not None}
            conns.update(self._owners)
        for conn in conns.values():
            self.retry_cleanup(conn)
        pending = list(self._reapers.values())
        if pending:
            await asyncio.wait(pending, timeout=CONNECTION_CLOSE_BUDGET)


# ══════════════════════════════════════════════════════════════════════════
# 命令处理
# ══════════════════════════════════════════════════════════════════════════


def _validate_command(payload: Mapping[str, Any], conn: _Connection) -> tuple[str, dict[str, Any]]:
    """白名单校验：未知 op / 额外字段 / 版本 / terminal_id 覆盖一律静态 error。"""
    if type(payload.get("v")) is not int or payload.get("v") != PROTOCOL_VERSION:
        raise ProtocolError("unsupported-version")
    if payload.get("type") != "command":
        raise ProtocolError("unknown-type")
    declared = payload.get("terminal_id")
    if declared is not None and declared != conn.terminal_id:
        # 消息**不能**覆盖连接绑定的 terminal_id。
        raise ProtocolError("terminal-mismatch")
    op = payload.get("op")
    if not isinstance(op, str) or op not in _COMMAND_FIELDS:
        raise ProtocolError("unknown-op")
    fields = {k: v for k, v in payload.items() if k not in _ENVELOPE_FIELDS}
    if set(fields) - _COMMAND_FIELDS[op]:
        raise ProtocolError("unknown-field")
    return op, fields


def _require_control(conn: _Connection, fields: Mapping[str, Any]) -> LeaseToken:
    """输入权限门：**实际** control token + generation 精确一致，否则零写。"""
    token = conn._token
    if token is None or token.role != LEASE_ROLE_CONTROL:
        raise ProtocolError("not-control")
    raw = fields.get("generation")
    if not isinstance(raw, str):
        raise ProtocolError("invalid-generation")
    generation = _parse_uint64(raw)
    if generation is None:
        raise ProtocolError("invalid-generation")
    if int(generation) != int(token.generation):
        raise ProtocolError("stale-generation")
    return token


async def _detach_token(conn: _Connection, token: LeaseToken) -> None:
    if not await conn._release_token(token):
        raise ProtocolError("release-unconfirmed")


async def _cmd_claim(conn: _Connection) -> None:
    """升级本连接为 control；同连接已 control 则幂等返回当前 generation。"""
    service = conn.runtime.service
    assert service is not None
    current = conn._token
    if current is not None and current.role == LEASE_ROLE_CONTROL:
        holder = await conn.runtime.call(lambda: service.control_holder(conn.terminal_id))
        if isinstance(holder, Mapping) and holder.get("client_id") == conn.connection_id and holder.get("generation") == current.generation:
            conn.send_event(
                "claim-result", role=LEASE_ROLE_CONTROL, generation=_dec(current.generation), idempotent=True
            )
            return
    if current is not None:
        # 撤销并释放该连接之前的 observer token（不反复颁发）。
        await _detach_token(conn, current)
        conn._token = None
    token = await conn._await_lease(
        lambda: service.attach(conn.terminal_id, conn.connection_id, role=LEASE_ROLE_CONTROL)
    )
    conn.send_event("claim-result", role=LEASE_ROLE_CONTROL, generation=_dec(token.generation))


async def _cmd_release(conn: _Connection) -> None:
    """撤销当前 token，签发同 connection 的新 observer；连续 release 幂等。"""
    service = conn.runtime.service
    assert service is not None
    current = conn._token
    if current is not None and current.role == LEASE_ROLE_OBSERVER:
        # 已是 observer：幂等，**不**不断制造 token。
        conn.send_event(
            "release-result", role=LEASE_ROLE_OBSERVER, control_generation=None, idempotent=True
        )
        return
    if current is not None:
        await _detach_token(conn, current)
        conn._token = None
    token = await conn._await_lease(
        lambda: service.attach(conn.terminal_id, conn.connection_id, role=LEASE_ROLE_OBSERVER)
    )
    conn.send_event("release-result", role=LEASE_ROLE_OBSERVER, control_generation=None)


async def _cmd_input(conn: _Connection, fields: Mapping[str, Any]) -> None:
    service = conn.runtime.service
    assert service is not None
    token = _require_control(conn, fields)
    data_b64 = fields.get("data_b64")
    if not isinstance(data_b64, str):
        raise ProtocolError("invalid-field")
    try:
        payload = base64.b64decode(data_b64, validate=True)
    except (binascii.Error, ValueError):
        raise ProtocolError("invalid-base64") from None
    if len(payload) > MAX_INPUT_BYTES:
        raise ProtocolError("payload-too-large")
    seq_value: int | None = None
    if "seq" in fields:
        raw_seq = fields["seq"]
        if not isinstance(raw_seq, str):
            raise ProtocolError("invalid-field")
        parsed = _parse_uint64(raw_seq)
        if parsed is None:
            raise ProtocolError("invalid-field")
        seq_value = int(parsed)
    try:
        result = await conn.runtime.call(
            lambda: service.input(conn.terminal_id, token, payload, seq=seq_value)
        )
    except (NotControlLeaseError, StaleLeaseError):
        raise ProtocolError("stale-generation") from None
    # 只回白名单字段（**不**复制 service.channel 任意 Mapping）。
    conn.send_event(
        "input-result",
        accepted=result.get("accepted") is True,
        size=int(result.get("size") or 0),
        status=result.get("status"),
        seq=None if seq_value is None else _dec(seq_value),
    )


async def _cmd_resize(conn: _Connection, fields: Mapping[str, Any]) -> None:
    service = conn.runtime.service
    assert service is not None
    token = _require_control(conn, fields)
    rows, error = _bounded_field(fields, "rows", 0, ROWS_MIN, ROWS_MAX)
    if error or rows is None:
        raise ProtocolError("invalid-rows")
    cols, error = _bounded_field(fields, "cols", 0, COLS_MIN, COLS_MAX)
    if error or cols is None:
        raise ProtocolError("invalid-cols")
    try:
        result = await conn.runtime.call(
            lambda: service.resize(conn.terminal_id, token, int(rows), int(cols))
        )
    except (NotControlLeaseError, StaleLeaseError):
        raise ProtocolError("stale-generation") from None
    # PTY/engine 确认**分列**；不声称三方一致，也不用客户端 ack 假证明引擎。
    pty = result.get("pty") if isinstance(result.get("pty"), Mapping) else {}
    engine = result.get("engine") if isinstance(result.get("engine"), Mapping) else {}
    engine_confirmed = engine.get("confirmed") if isinstance(engine.get("confirmed"), bool) else None
    conn.send_event(
        "resize-result",
        accepted=result.get("accepted") is True,
        status=result.get("status"),
        rows=int(rows),
        cols=int(cols),
        pty_accepted=bool(pty.get("accepted")) if isinstance(pty, Mapping) else None,
        engine_confirmed=engine_confirmed,
    )


async def _cmd_ack(conn: _Connection, fields: Mapping[str, Any]) -> None:
    """ack 只记录**已发送**消费水位；不改 runner 日志、不控制 PTY 寿命、不 reset。"""
    raw = fields.get("next_seq")
    if not isinstance(raw, str):
        raise ProtocolError("invalid-field")
    value = _parse_uint64(raw)
    if value is None:
        raise ProtocolError("invalid-field")
    if int(value) < conn._ack_watermark:
        raise ProtocolError("ack-regressed")
    if int(value) > conn.sent_end:
        raise ProtocolError("ack-ahead-of-sent")
    conn._ack_watermark = int(value)
    conn.ack_watermark = conn._ack_watermark
    conn.send_event("ack-result", next_seq=_dec(conn._ack_watermark))


async def _cmd_resume(conn: _Connection, fields: Mapping[str, Any]) -> None:
    """显式从绝对偏移重拉；与已排队 output 线性化（换 epoch，旧 output 不混流）。"""
    raw = fields.get("cursor")
    if not isinstance(raw, str):
        raise ProtocolError("invalid-field")
    value = _parse_uint64(raw)
    if value is None:
        raise ProtocolError("invalid-field")
    async with conn.outbound.send_lock:
        conn._stream_epoch += 1
        conn.cursor = int(value)
        conn._paused = False
        conn.sent_end = conn.cursor  # new stream baseline, not an old stream's sent frontier
        conn._ack_watermark = conn.cursor
        conn.ack_watermark = conn.cursor
        conn.send_event("resume-result", cursor=_dec(conn.cursor))


async def _cmd_snapshot(conn: _Connection, fields: Mapping[str, Any]) -> None:
    """快照：复用 REST 128KiB 上限与机器字段投影；**不**自动改 raw 游标。"""
    service = conn.runtime.service
    assert service is not None
    timeout_ms, error = _bounded_field(fields, "timeout_ms", TIMEOUT_DEFAULT, TIMEOUT_MIN, TIMEOUT_MAX)
    if error or timeout_ms is None:
        raise ProtocolError("invalid-timeout")
    raw = await conn.runtime.call(
        lambda: service.snapshot(conn.terminal_id, timeout_ms=int(timeout_ms))
    )
    screen = terminal_api._raw_bytes(raw.get("serialized_screen"))
    if screen is not None and len(screen) > SNAPSHOT_SCREEN_MAX:
        raise ProtocolError("snapshot-too-large")
    # 投影里已含 ``terminal_id``；包络字段由 ``_event`` 统一填，这里不重复传。
    projection = terminal_api.project_snapshot(raw)
    projection.pop("terminal_id", None)
    conn.send_event("snapshot", **projection)


async def _dispatch(conn: _Connection, payload: Mapping[str, Any]) -> None:
    """执行一条命令（receiver 串行；权限门先于任何 service 调用）。"""
    op, fields = _validate_command(payload, conn)
    if op == "ping":
        # pong；**不是** runner owner-heartbeat（owner 续约归 service 独立心跳）。
        conn.send_event("pong")
    elif op == "ack":
        await _cmd_ack(conn, fields)
    elif op == "resume":
        await _cmd_resume(conn, fields)
    elif op == "snapshot":
        await _cmd_snapshot(conn, fields)
    elif op == "claim":
        await _cmd_claim(conn)
    elif op == "release":
        await _cmd_release(conn)
    elif op == "input":
        await _cmd_input(conn, fields)
    elif op == "resize":
        await _cmd_resize(conn, fields)
    else:  # pragma: no cover - 白名单已穷尽
        raise ProtocolError("unknown-op")


# ══════════════════════════════════════════════════════════════════════════
# reader / receiver
# ══════════════════════════════════════════════════════════════════════════


async def _reader_loop(conn: _Connection) -> None:
    """持续供输出：每轮从当前 cursor 读 ≤32KiB；gap → 暂停（等显式 resume）。"""
    runtime = conn.runtime
    service = runtime.service
    while conn._stop.is_set() is False:
        if conn._paused:
            await asyncio.sleep(POLL_IDLE_SECONDS)
            continue
        if service is None:
            return
        epoch, cursor = conn.stream_epoch, conn.cursor
        try:
            raw = await runtime.call(
                lambda: service.read(conn.terminal_id, cursor, max_bytes=READ_CHUNK_BYTES)
            )
        except Busy:
            # read 忙 → 暂缓重试，**不当 EOF**。
            await asyncio.sleep(POLL_IDLE_SECONDS)
            continue
        except UnknownTerminalError:
            conn.send_event("terminal-state", status="unknown-terminal")
            conn._terminal_sent = True
            return
        except (TerminalNotAttached, ServiceClosingDown, CapacityExceeded, StartupFailed,
                DetachRefused, CleanupUnconfirmed):
            conn.send_event("terminal-state", status="terminal-unavailable")
            conn._terminal_sent = True
            return
        if epoch != conn.stream_epoch:
            continue  # resume invalidates both queued frames and in-flight reads
        gap = raw.get("gap")
        if gap:
            # 原样报告缺口并**暂停**本连接 raw 读取（不补零/不自动 reset/不杀 PTY）。
            conn._paused = True
            conn.send_event("gap", from_seq=_dec(gap[0]), to_seq=_dec(gap[1]), fresh_view_required=True)
            if raw.get("status") in TERMINAL_END_STATES:
                # Already-ended owners cannot promise a fresh live snapshot.
                # Report the lost interval before terminal-state, never wait
                # forever on resume after the retained owner has been released.
                conn.send_event(
                    "terminal-state", status=raw["status"],
                    exit_code=raw.get("exit_code") if type(raw.get("exit_code")) is int else None,
                    output_complete=raw.get("output_complete") is True,
                    process_exit_seen=raw.get("process_exit_seen") is True,
                    reader_done=raw.get("reader_done") is True,
                )
                conn._terminal_sent = True
                return
            continue
        data = raw.get("data") or b""
        next_cursor = int(raw.get("next_cursor") or conn.cursor)
        if data:
            conn.send_event(
                "output",
                marker=next_cursor,
                epoch=conn._stream_epoch,
                seq=_dec(raw.get("seq") if raw.get("seq") is not None else conn.cursor),
                next_seq=_dec(next_cursor),
                data_b64=base64.b64encode(bytes(data)).decode("ascii"),
                size=len(data),
            )
            conn.cursor = next_cursor
            continue
        status = str(raw.get("status") or "")
        if status in TERMINAL_END_STATES:
            code = raw.get("exit_code")
            conn.send_event(
                "terminal-state", status=status,
                exit_code=code if type(code) is int else None,
                output_complete=raw.get("output_complete") is True,
                process_exit_seen=raw.get("process_exit_seen") is True,
                reader_done=raw.get("reader_done") is True,
            )
            conn._terminal_sent = True
            return
        await asyncio.sleep(POLL_IDLE_SECONDS)


async def _receiver_loop(conn: _Connection) -> None:
    """串行处理本连接输入；单帧 ≤256KiB（先测 UTF-8 字节）。"""
    runtime = conn.runtime
    while conn._stop.is_set() is False:
        message = await conn.websocket.receive()
        if message.get("type") == "websocket.disconnect":
            return
        text = message.get("text")
        if text is None:
            conn.send_error("binary-unsupported")
            continue
        if len(text.encode("utf-8")) > MAX_INBOUND_FRAME_BYTES:
            conn.send_error("frame-too-large")
            continue
        try:
            payload = json.loads(text)
        except (ValueError, RecursionError):
            conn.send_error("invalid-json")
            continue
        if not isinstance(payload, dict):
            conn.send_error("invalid-frame")
            continue
        try:
            await _dispatch(conn, payload)
        except ProtocolError as exc:
            conn.send_error(exc.code)
        except Busy:
            # 4 槽已满 → 暂缓重试（静态 code；**不当 EOF**、不排队）。
            conn.send_error("busy")
        except GateRejected as rejected:
            conn.send_error(rejected.code)
        except UnknownTerminalError:
            conn.send_error("unknown-terminal")
        except (StaleLeaseError, NotControlLeaseError):
            conn.send_error("stale-generation")
        except SlowClient:
            raise
        except Exception:  # noqa: BLE001 - 只回静态分类，不回显异常文本
            conn.send_error("internal-error")


# ══════════════════════════════════════════════════════════════════════════
# 路由：accept 前拒绝 → accept → 连接主流程
# ══════════════════════════════════════════════════════════════════════════


async def _serve(websocket: WebSocket, conn: _Connection, manager: _Manager) -> None:
    """accept 后的连接主流程：observer hello → 三任务 → 收尾。"""
    runtime = conn.runtime
    service = runtime.service
    try:
        if service is None:
            await websocket.close(code=1013)
            return
        await conn._await_lease(
            lambda: service.attach(conn.terminal_id, conn.connection_id, role=LEASE_ROLE_OBSERVER)
        )
        conn.send_event(
            "hello",
            connection_id=conn.connection_id,
            role=LEASE_ROLE_OBSERVER,
            control_generation=None,
            protocol={
                "version": PROTOCOL_VERSION,
                "encoding": "json-text",
                "cursor": "ascii-decimal-string",
                "max_inbound_frame_bytes": MAX_INBOUND_FRAME_BYTES,
                "max_input_bytes": MAX_INPUT_BYTES,
                "read_chunk_bytes": READ_CHUNK_BYTES,
            },
        )
        conn.start()
        # 任一任务结束（慢客户端/终态/断开）即进入收尾。
        done, pending = await asyncio.wait(conn._tasks, return_when=asyncio.FIRST_COMPLETED)
        # Queue admission can fail in the reader/receiver, not just in sender.
        # asyncio.wait does not propagate those exceptions. Consume them before
        # cleanup so an exhausted queue cannot silently drop the close frame.
        failed = any(
            not task.cancelled()
            and (error := task.exception()) is not None
            and not isinstance(error, WebSocketDisconnect)
            for task in done
        )
        if conn._terminal_sent and not failed and not conn.outbound.slow:
            # The reader finishing is not the sender finishing. Preserve FIFO
            # tail + terminal-state, including the in-flight frame, before 1000.
            try:
                await asyncio.wait_for(conn.outbound.drained.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                failed = True
            failed = failed or any(
                task.done() and not task.cancelled() and task.exception() is not None
                for task in conn._tasks
            )
        for task in pending:
            task.cancel()
        if conn.outbound.slow or failed:
            with contextlib.suppress(Exception):
                await websocket.close(code=1013)
        elif conn._terminal_sent:
            # 终态：正常关闭，**不**假 output_complete。
            with contextlib.suppress(Exception):
                await websocket.close(code=1000)
    except WebSocketDisconnect:
        pass
    except Exception:
        with contextlib.suppress(Exception):
            await websocket.close(code=1013)
    finally:
        # The owner task survives route cancellation and consumes late issuance results.
        manager.retry_cleanup(conn)
        await asyncio.shield(manager._reapers[conn.connection_id])


@router.websocket("/ws/terminal/{terminal_id}")
async def terminal_websocket(websocket: WebSocket, terminal_id: str) -> None:
    """``/ws/terminal/{terminal_id}``：连接身份 + 单 sender 输出流。

    拒绝路径**全部在 accept 前**：安全 gate（403）→ id/cursor（400）→ 对象
    存在性（404）→ ready/容量（503/429）。安全 gate 拒绝**零 service 调用**；
    合法来源允许一次 ``service.get``，但被拒连接**零 attach/读/写**。
    """
    runtime = getattr(websocket.app.state, "terminal_runtime", None)
    manager = getattr(websocket.app.state, "terminal_ws_manager", None)
    if not isinstance(runtime, TerminalRuntime) or not isinstance(manager, _Manager):
        with contextlib.suppress(Exception):
            await send_handshake_denial(websocket, "not-ready")
        return
    reservation: str | None = None
    try:
        check_ws_gate(websocket, runtime)
        cursor = parse_query_cursor(websocket)
        try:
            validate_terminal_id(terminal_id)
        except ValueError:
            raise GateRejected(STATUS_BAD_REQUEST, "invalid-terminal-id") from None
        # 合法来源：允许一次 service.get 确认对象存在（仍零 attach/读/写）。
        try:
            await runtime.call(lambda: runtime.service.get(terminal_id))
        except UnknownTerminalError:
            raise GateRejected(STATUS_NOT_FOUND, "unknown-terminal") from None
        # accept 前预留容量（满则 429）；拒绝路径必须归还预留。
        reservation = manager.reserve()
        if reservation is None:
            raise GateRejected(STATUS_CAPACITY, "connection-capacity-exceeded")
    except GateRejected as rejected:
        if reservation is not None:
            manager.release(reservation)
        await send_handshake_denial(websocket, rejected.code)
        return
    except Busy:
        if reservation is not None:
            manager.release(reservation)
        await send_handshake_denial(websocket, "not-ready")
        return
    except Exception:
        if reservation is not None:
            manager.release(reservation)
        await send_handshake_denial(websocket, "not-ready")
        return

    conn = _Connection(websocket, terminal_id, runtime, cursor=cursor)
    if not manager.commit(reservation, conn):  # type: ignore[arg-type]
        await send_handshake_denial(websocket, "connection-capacity-exceeded")
        return
    try:
        await websocket.accept()
    except Exception:
        manager.remove(conn)
        return
    await _serve(websocket, conn, manager)


# ══════════════════════════════════════════════════════════════════════════
# lifespan helper（内层；退出时先停 WS 准入/关连接）
# ══════════════════════════════════════════════════════════════════════════


async def start_ws(app: Any, *, runtime: TerminalRuntime | None = None) -> _Manager:
    """挂上本 app 的 WS 管理器（依赖外层 ``terminal_lifespan`` 已构造 runtime）。"""
    active = runtime if runtime is not None else getattr(app.state, "terminal_runtime", None)
    if not isinstance(active, TerminalRuntime):
        raise GateRejected(STATUS_UNAVAILABLE, "not-ready")
    manager = _Manager(active)
    app.state.terminal_ws_manager = manager
    return manager


async def stop_ws(app: Any, *, manager: Any = None) -> dict[str, Any]:
    """停 WS 准入并关闭全部连接（**共享 2s 预算**；与 REST 20s **单列**）。"""
    active = manager if manager is not None else getattr(app.state, "terminal_ws_manager", None)
    if not isinstance(active, _Manager):
        return {"status": "no-manager", "closed": 0, "pending_cleanup": 0}
    await active.shutdown()
    snapshot = active.snapshot()
    return {
        "status": "unconfirmed" if snapshot["reserved"] else "stopped",
        "retained_connections": int(snapshot["reserved"]),
        "pending_cleanup": int(snapshot["pending_cleanup"]),
        "budget_seconds": CONNECTION_CLOSE_BUDGET,
    }


@asynccontextmanager
async def websocket_lifespan(app: Any, *, runtime: TerminalRuntime | None = None):
    """真实 server 的**内层** lifespan：``yield`` 与 ``stop_ws`` 走 try/finally。

    退出时**先**停 WS 准入并关闭全部连接（共享 2s 预算），**再**由外层
    ``terminal_lifespan`` 走 REST 既有 20s 预算 —— 两预算**单列**。
    """
    manager = await start_ws(app, runtime=runtime)
    try:
        yield manager
    finally:
        await stop_ws(app, manager=manager)
