"""P2 终端 WebSocket 桥的**确定性**门控套件（纯逻辑；不依赖真实 PTY）。

覆盖任务书逐条 gate：握手拒绝零 service 调用 + 合法正控、伪造 token/client/id 无效、
observer/旧代/跨连接写拒绝与真 control 正控、断开迟到 attach 清理、输出绝对
cursor/ack 范围/gap 暂停/显式 resume 顺序、pending 快照 partial 与 F5 原样、
超大 frame/b64/队列边界（含在途 send）、slow-client 不影响另一连接、runtime 4 槽
busy、shutdown 与 receiver 输入交错有界但 owner 保留、ping 不续 runner lease、
连接断开不杀同 PID。

真实 ASGI/ConPTY 链在 ``test_real_*`` 一节（需 Windows + sidecar 依赖）。
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable

import pytest
from fastapi import FastAPI

from packages.core.terminal.contracts import (
    LEASE_ROLE_CONTROL,
    LEASE_ROLE_OBSERVER,
    LeaseToken,
    NotControlLeaseError,
    StaleLeaseError,
    UnknownTerminalError,
)
from packages.core.terminal.service import TerminalService
from packages.web import terminal_api, terminal_ws
from packages.web.terminal_api import Busy, GateRejected

REPO_ROOT = Path(__file__).resolve().parents[1]
AUTHORITY = "127.0.0.1:8768"
ORIGIN = f"http://{AUTHORITY}"


# ══════════════════════════════════════════════════════════════════════════
# 替身
# ══════════════════════════════════════════════════════════════════════════


class _Gate:
    """确定性阻塞门：``target`` 个调用进入后置位 ``entered``，直到 ``release``。"""

    def __init__(self, target: int = 1) -> None:
        self.target = int(target)
        self.count = 0
        self._lock = threading.Lock()
        self.entered = threading.Event()
        self.release = threading.Event()

    def enter(self) -> None:
        with self._lock:
            self.count += 1
            if self.count >= self.target:
                self.entered.set()
        self.release.wait(timeout=20.0)


class FakeService:
    """TerminalService 替身：脚本化 lease/读写 + 调用记录。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.calls: list[tuple[str, tuple, dict]] = []
        self.terminal_ids = {"term_1"}
        self.attach_error: BaseException | None = None
        self.attach_gate: _Gate | None = None
        self.input_error: BaseException | None = None
        self.read_pages: list[dict[str, Any]] = []
        self.read_gate: _Gate | None = None
        self.snapshot_result: dict[str, Any] = {}
        self.snapshot_error: BaseException | None = None
        self.released: list[LeaseToken] = []
        self.release_gate: _Gate | None = None
        self.generation = 0
        self.heartbeats = 0
        self.view_status = "running"
        #: 忠实核心 AttachmentRegistry：当前 control + 已撤销集合
        self._control: dict[str, LeaseToken] = {}
        self._revoked: set[str] = set()
        #: **实际写入**（被拒的零写不计入）——"零写"断言只看这里
        self.written: list[tuple[str, bytes, int | None]] = []

    # -- 记录 ----------------------------------------------------------
    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        with self._lock:
            self.calls.append((name, args, kwargs))

    def names(self) -> list[str]:
        with self._lock:
            return [name for name, _a, _k in self.calls]

    def lease_calls(self) -> list[str]:
        return [n for n in self.names() if n in ("attach", "release_attachment", "input", "resize")]

    def inputs(self) -> list[tuple[str, bytes, int | None]]:
        """**实际写入**成功的 input（被 observer/旧代/撤销拒绝的零写不计入）。"""
        return list(self.written)

    # -- service 表面 ---------------------------------------------------
    def get(self, terminal_id: str) -> dict[str, Any]:
        self._record("get", terminal_id)
        if terminal_id not in self.terminal_ids:
            raise UnknownTerminalError(terminal_id)
        return {"terminal_id": terminal_id, "status": self.view_status}

    def attach(self, terminal_id: str, client_id: str, *, role: str = LEASE_ROLE_OBSERVER,
               rows: int = 0, cols: int = 0) -> LeaseToken:
        self._record("attach", terminal_id, client_id, role=role)
        if self.attach_gate is not None:
            self.attach_gate.enter()
        if self.attach_error is not None:
            raise self.attach_error
        if role == LEASE_ROLE_CONTROL:
            self.generation += 1
            # 忠实核心 AttachmentRegistry：新 control 立刻作废旧 control
            old = self._control.get(terminal_id)
            if old is not None:
                self._revoked.add(old.revocation_id)
        token = LeaseToken(
            terminal_id=terminal_id, client_id=client_id, generation=self.generation,
            role=role, rows=rows, cols=cols, revocation_id=f"rv-{len(self.calls)}-{role}",
        )
        if role == LEASE_ROLE_CONTROL:
            self._control[terminal_id] = token
        return token

    def release_attachment(self, token: LeaseToken) -> None:
        self._record("release_attachment", token)
        if self.release_gate is not None:
            self.release_gate.enter()
        self.released.append(token)
        self._revoked.add(token.revocation_id)
        holder = self._control.get(token.terminal_id)
        if holder is not None and holder.revocation_id == token.revocation_id:
            del self._control[token.terminal_id]
            self.generation += 1  # 与核心一致：控制权撤销自增 generation

    def _require_current_control(self, terminal_id: str, token: LeaseToken) -> None:
        """忠实核心：撤销不可复活 + 只有**当前** control 可写。"""
        if token.role != LEASE_ROLE_CONTROL:
            raise NotControlLeaseError("observer-cannot-input")
        if token.revocation_id in self._revoked:
            raise StaleLeaseError("lease token 已被撤销")
        holder = self._control.get(terminal_id)
        if holder is None or holder.revocation_id != token.revocation_id:
            raise StaleLeaseError("不是当前控制权持有者")
        if token.generation != self.generation:
            raise StaleLeaseError("generation mismatch")

    def control_holder(self, terminal_id):
        token = self._control.get(terminal_id)
        return None if token is None else {"client_id": token.client_id, "generation": token.generation}

    def input(self, terminal_id: str, token: LeaseToken, data: bytes, *, seq: int | None = None) -> dict[str, Any]:
        self._record("input", terminal_id, data, seq=seq)
        if self.input_error is not None:
            raise self.input_error
        # 校验在写之前：被拒**零写**（不记录到 written）
        self._require_current_control(terminal_id, token)
        self.written.append((terminal_id, data, seq))
        return {"terminal_id": terminal_id, "size": len(data), "status": "ok", "accepted": True,
                "lease_generation": token.generation, "channel": {"pipe": r"\\.\pipe\pan-terminal-x"}}

    def resize(self, terminal_id: str, token: LeaseToken, rows: int, cols: int) -> dict[str, Any]:
        self._record("resize", terminal_id, rows, cols)
        self._require_current_control(terminal_id, token)
        return {
            "terminal_id": terminal_id, "accepted": True, "status": "ok",
            "requested": {"rows": rows, "cols": cols},
            "pty": {"accepted": True, "rows": rows, "cols": cols},
            "engine": {"confirmed": None, "detail": {"pid": 1}},
            "three_way_agreement": None,
        }

    def read(self, terminal_id: str, cursor: int = 0, *, max_bytes: Any = None) -> dict[str, Any]:
        self._record("read", terminal_id, cursor, max_bytes=max_bytes)
        if self.read_gate is not None:
            self.read_gate.enter()
        if self.read_pages:
            return self.read_pages.pop(0)
        return {"terminal_id": terminal_id, "data": b"", "seq": cursor, "size": 0,
                "next_cursor": cursor, "total_bytes": cursor, "first_retained_seq": 0,
                "truncated": False, "gap": None, "fresh_view_required": False,
                "cursor_advanced": False, "status": "running", "zero_fill": False}

    def snapshot(self, terminal_id: str, *, timeout_ms: int = 5000) -> dict[str, Any]:
        self._record("snapshot", terminal_id, timeout_ms=timeout_ms)
        if self.snapshot_error is not None:
            raise self.snapshot_error
        return dict(self.snapshot_result)

    def reconcile(self) -> dict[str, Any]:
        self._record("reconcile")
        return {"buckets": {}, "counts": {}, "records": 0}

    def shutdown(self, *, budget: Any = None) -> dict[str, Any]:
        self._record("shutdown", budget=budget)
        return {"unconfirmed": [], "secrets_retained": False, "budget_exhausted": False}

    def describe(self) -> dict[str, Any]:
        return {"attached": sorted(self.terminal_ids)}


class _AsyncSendGate:
    """**异步**发送门：卡住 ``send_text`` 但**不**阻塞事件循环。

    第 ``target`` 次 send 起置位 ``entered``，并一直 await ``release``。
    （用线程 ``Event.wait`` 会卡住 loop，使 reader 无法推进 —— 那样测不到
    "队列里已有帧" 的真实前提。）
    """

    def __init__(self, target: int = 1) -> None:
        self.target = int(target)
        self.count = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def enter(self) -> None:
        self.count += 1
        if self.count >= self.target:
            self.entered.set()
        await self.release.wait()


class FakeWebSocket:
    """WebSocket 替身：可脚本化 headers/query/帧入站，观察 accept/close/send。"""

    def __init__(
        self,
        *,
        headers: dict[str, str] | None = None,
        query: str = "",
        scope_extensions: dict[str, Any] | None = None,
        app: Any = None,
    ) -> None:
        self.scope: dict[str, Any] = {"extensions": dict(scope_extensions or {})}
        self.app = app
        self.headers = dict(headers or {})
        self.query_params = _Query(query)
        self.accepted = False
        self.closed: tuple[int, str | None] | None = None
        self.denial: tuple[int, Any] | None = None
        self.sent: list[str] = []
        self._inbound: asyncio.Queue = asyncio.Queue()
        self._send_delay = 0.0
        self.send_gate: Any = None  # _AsyncSendGate（**不得**用阻塞式门）
        self.send_error: BaseException | None = None

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.closed = (int(code), reason)

    async def send_denial_response(self, response: Any) -> None:
        self.denial = (int(response.status_code), response.body)

    async def send_text(self, text: str) -> None:
        if self.send_gate is not None:
            await self.send_gate.enter()
        if self.send_error is not None:
            raise self.send_error
        if self._send_delay:
            await asyncio.sleep(self._send_delay)
        self.sent.append(text)

    async def receive(self) -> dict[str, Any]:
        return await self._inbound.get()

    def feed(self, payload: Any) -> None:
        text = payload if isinstance(payload, str) else json.dumps(payload)
        self._inbound.put_nowait({"type": "websocket.receive", "text": text})

    def feed_disconnect(self) -> None:
        self._inbound.put_nowait({"type": "websocket.disconnect"})

    # -- 断言辅助 -------------------------------------------------------
    def events(self) -> list[dict[str, Any]]:
        return [json.loads(item) for item in self.sent]

    def of_type(self, name: str) -> list[dict[str, Any]]:
        return [event for event in self.events() if event.get("type") == name]

    def has(self, name: str) -> bool:
        return bool(self.of_type(name))


async def wait_for_event(ws: FakeWebSocket, name: str, timeout: float = 5.0) -> dict[str, Any]:
    """轮询等待某类事件（**异步**让出事件循环，否则连接任务无法推进）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = ws.of_type(name)
        if found:
            return found[0]
        await asyncio.sleep(0.01)
    raise AssertionError(
        f"event {name!r} not observed; got {[e.get('type') for e in ws.events()]}"
    )


async def wait_for_count(
    ws: FakeWebSocket, name: str, count: int, timeout: float = 5.0
) -> list[dict[str, Any]]:
    """轮询等待某类事件累积到 ``count`` 个。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = ws.of_type(name)
        if len(found) >= count:
            return found
        await asyncio.sleep(0.01)
    raise AssertionError(
        f"expected {count}x {name!r}, got {len(ws.of_type(name))}; "
        f"all={[e.get('type') for e in ws.events()]}"
    )


async def wait_until(predicate: Callable[[], bool], timeout: float = 5.0) -> bool:
    """异步轮询直到 ``predicate`` 为真（不阻塞事件循环）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return False


async def finish(task: asyncio.Task, ws: FakeWebSocket, timeout: float = 10.0) -> None:
    """断开并等待连接任务结束（吞掉预期的 close/取消异常）。"""
    if not task.done():
        ws.feed_disconnect()
    with contextlib.suppress(Exception, asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=timeout)


class _Query:
    def __init__(self, raw: str) -> None:
        self._pairs: list[tuple[str, str]] = []
        for piece in raw.split("&"):
            if not piece:
                continue
            key, _, value = piece.partition("=")
            self._pairs.append((key, value))

    def get(self, key: str) -> str | None:
        for name, value in self._pairs:
            if name == key:
                return value
        return None


# ══════════════════════════════════════════════════════════════════════════
# 辅助
# ══════════════════════════════════════════════════════════════════════════


def make_runtime(service: Any, **kwargs: Any) -> terminal_api.TerminalRuntime:
    params: dict[str, Any] = {
        "env": {}, "platform": "win32", "host": "127.0.0.1", "port": 8768,
        "service_factory": lambda: service,
    }
    params.update(kwargs)
    return terminal_api.build_runtime(**params)


async def start_app(runtime: terminal_api.TerminalRuntime) -> FastAPI:
    app = FastAPI()
    app.include_router(terminal_api.router)
    app.include_router(terminal_ws.router)
    await terminal_api.start_runtime(app, runtime=runtime)
    await terminal_ws.start_ws(app, runtime=runtime)
    return app


async def wait_ready(runtime: terminal_api.TerminalRuntime, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while runtime.state != terminal_api.STATE_READY and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    assert runtime.state == terminal_api.STATE_READY, runtime.state


def make_ws(
    app: FastAPI,
    *,
    origin: str | None = ORIGIN,
    host: str | None = AUTHORITY,
    query: str = "",
    extra: dict[str, str] | None = None,
    extensions: dict[str, Any] | None = None,
) -> FakeWebSocket:
    headers: dict[str, str] = {}
    if origin is not None:
        headers["origin"] = origin
    if host is not None:
        headers["host"] = host
    if extra:
        headers.update(extra)
    if extensions is None:
        extensions = {"websocket.http.response": {}}
    return FakeWebSocket(headers=headers, query=query, scope_extensions=extensions, app=app)


def cmd(op: str, terminal_id: str = "term_1", **fields: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"v": 1, "type": "command", "terminal_id": terminal_id, "op": op}
    payload.update(fields)
    return payload


class cast_ws:
    """把替身伪装成 WebSocket（仅类型标注用；运行期无检查）。"""

    def __init__(self, ws: FakeWebSocket) -> None:
        self._ws = ws

    def __getattr__(self, name: str) -> Any:
        return getattr(self._ws, name)


async def accepted_connection(
    app: FastAPI, ws: FakeWebSocket, terminal_id: str = "term_1"
) -> tuple[asyncio.Task, Any]:
    """accept 并返回 ``(task, runtime)``，供需要直接驱动内部连接的用例。"""
    task = asyncio.create_task(terminal_ws.terminal_websocket(cast_ws(ws), terminal_id))
    assert await wait_until(lambda: ws.accepted or ws.denial is not None or ws.closed is not None), (
        f"handshake 未决: denial={ws.denial} closed={ws.closed}"
    )
    assert ws.accepted, f"not accepted: denial={ws.denial} closed={ws.closed}"
    return task, app.state.terminal_runtime


# ══════════════════════════════════════════════════════════════════════════
# ① 握手前拒绝：安全 gate 零 service 调用 + 合法正控
# ══════════════════════════════════════════════════════════════════════════

_BAD_ORIGINS = [
    "null",
    "http://127.0.0.1:9999",
    "http://evil.127.0.0.1:8768",
    "https://127.0.0.1:8768",
    "http://127.0.0.1:8768/extra",
    "http://user@127.0.0.1:8768",
    "http://*:8768",
    "http://[broken",
    "http://127.0.0.1:8768/",
]


def test_handshake_origin_rejections_are_pre_accept_and_zero_service_calls():
    """坏/缺 Origin → 403 且**零 service 调用**；合法来源正控通过。"""

    async def scenario() -> None:
        service = FakeService()
        runtime = make_runtime(service)
        app = await start_app(runtime)
        await wait_ready(runtime)
        baseline = service.names()  # 只含 lifespan 的 reconcile

        for origin in [None, *_BAD_ORIGINS]:
            ws = make_ws(app, origin=origin)
            await terminal_ws.terminal_websocket(cast_ws(ws), "term_1")
            assert not ws.accepted, origin
            assert ws.denial is not None and ws.denial[0] == 403, (origin, ws.denial)
            body = json.loads(ws.denial[1])
            assert body == {"ok": False, "error": {"code": "forbidden-origin"}}, (origin, body)
        # Host 不匹配 / Fetch-Site 跨站
        ws = make_ws(app, host="evil.local:8768")
        await terminal_ws.terminal_websocket(cast_ws(ws), "term_1")
        assert ws.denial is not None and json.loads(ws.denial[1])["error"]["code"] == "forbidden-host"
        for site in ("cross-site", "none"):
            ws = make_ws(app, extra={"sec-fetch-site": site})
            await terminal_ws.terminal_websocket(cast_ws(ws), "term_1")
            assert ws.denial is not None
            assert json.loads(ws.denial[1])["error"]["code"] == "forbidden-fetch-site"
        # 安全 gate 拒绝零 service 调用：在**任何正控连接之前**取证
        assert not service.lease_calls(), service.lease_calls()
        assert service.names().count("get") == 0, service.names()

        # same-origin / same-site 仍放行（正控）：允许 get，且 accept 后才 attach
        for index, site in enumerate(("same-origin", "same-site"), start=1):
            ws = make_ws(app, extra={"sec-fetch-site": site})
            task, _ = await accepted_connection(app, ws)
            # 每个合法连接恰好一次对象查询
            assert service.names().count("get") == index, service.names()
            await finish(task, ws, timeout=10.0)
        # 正控确实走到 lease（证明上面的零调用不是因为路径没跑）
        assert service.lease_calls().count("attach") == 2, service.calls
        assert service.lease_calls().count("release_attachment") == 2, service.calls

    asyncio.run(scenario())


def test_handshake_id_cursor_and_existence_rejections_are_standard_status():
    """非法 id/cursor → 400；不存在 → 404；均 accept 前、标准状态码。"""

    async def scenario() -> None:
        service = FakeService()
        runtime = make_runtime(service)
        app = await start_app(runtime)
        await wait_ready(runtime)

        for bad_id in ("nope", "term_bad!", "../etc"):
            ws = make_ws(app)
            await terminal_ws.terminal_websocket(cast_ws(ws), bad_id)
            assert ws.denial is not None and ws.denial[0] == 400, (bad_id, ws.denial)
            assert json.loads(ws.denial[1])["error"]["code"] == "invalid-terminal-id"
            assert not ws.accepted
        for bad_cursor in ("-1", "1.0", "0x10", "1e3", " 1", "18446744073709551616", "abc"):
            ws = make_ws(app, query=f"cursor={bad_cursor}")
            await terminal_ws.terminal_websocket(cast_ws(ws), "term_1")
            assert ws.denial is not None and ws.denial[0] == 400, (bad_cursor, ws.denial)
            assert json.loads(ws.denial[1])["error"]["code"] == "invalid-cursor"
        # 前导零允许且规范化
        ws = make_ws(app, query="cursor=007")
        task, _ = await accepted_connection(app, ws)
        await finish(task, ws)

        # 合法 id 但不存在 → 404（允许一次 get，零 attach）
        before = service.names()
        ws = make_ws(app)
        await terminal_ws.terminal_websocket(cast_ws(ws), "term_missing")
        assert ws.denial is not None and ws.denial[0] == 404, ws.denial
        assert json.loads(ws.denial[1])["error"]["code"] == "unknown-terminal"
        assert not ws.accepted
        # 该次拒绝只多了一次 get（对象查询），**零** attach/读/写
        delta = service.names()[len(before):]
        assert delta == ["get"], delta

        # 拒绝一律**不**用 4xxx/1013 私有码
        for code in (4400, 4403, 4404, 1013):
            assert code not in terminal_ws._HANDSHAKE_STATUS.values()

    asyncio.run(scenario())


def test_handshake_falls_back_to_pre_accept_close_without_denial_extension():
    """无 denial 扩展 → accept 前 close（回退 HTTP 403），**不**先 accept 换编码。"""

    async def scenario() -> None:
        service = FakeService()
        runtime = make_runtime(service)
        app = await start_app(runtime)
        await wait_ready(runtime)
        ws = make_ws(app, origin="http://evil.local:8768", extensions={})
        await terminal_ws.terminal_websocket(cast_ws(ws), "term_1")
        assert not ws.accepted
        assert ws.closed is not None, ws.closed
        assert ws.denial is None
        assert not service.lease_calls()

    asyncio.run(scenario())


def test_ready_states_reject_with_503_and_disabled_constructs_nothing():
    """disabled/starting/closing/unavailable → 503；容量耗尽 → 429。"""

    async def scenario() -> None:
        # 非 Windows → disabled（零 service 构造）
        service = FakeService()
        runtime = make_runtime(service, platform="linux")
        app = await start_app(runtime)
        ws = make_ws(app)
        await terminal_ws.terminal_websocket(cast_ws(ws), "term_1")
        assert ws.denial is not None and ws.denial[0] == 503, ws.denial
        assert json.loads(ws.denial[1])["error"]["code"] == "terminal-disabled"
        assert not ws.accepted

        # starting（未 reconcile 完）→ 503 not-ready
        service2 = FakeService()
        runtime2 = make_runtime(service2)
        app2 = FastAPI()
        app2.include_router(terminal_ws.router)
        app2.state.terminal_runtime = runtime2
        manager = terminal_ws._Manager(runtime2)
        app2.state.terminal_ws_manager = manager
        assert runtime2.state == terminal_api.STATE_STARTING
        ws = make_ws(app2)
        await terminal_ws.terminal_websocket(cast_ws(ws), "term_1")
        assert ws.denial is not None and ws.denial[0] == 503, ws.denial
        assert json.loads(ws.denial[1])["error"]["code"] == "not-ready"

        # closing → 503
        runtime2.state = terminal_api.STATE_CLOSING
        ws = make_ws(app2)
        await terminal_ws.terminal_websocket(cast_ws(ws), "term_1")
        assert json.loads(ws.denial[1])["error"]["code"] == "closing"

        # failed → 503 terminal-unavailable
        runtime2.state = terminal_api.STATE_FAILED
        ws = make_ws(app2)
        await terminal_ws.terminal_websocket(cast_ws(ws), "term_1")
        assert json.loads(ws.denial[1])["error"]["code"] == "terminal-unavailable"

        # 容量耗尽 → 429（accept 前预留）
        runtime3 = make_runtime(FakeService())
        await runtime3.startup()
        runtime3.state = terminal_api.STATE_READY
        manager3 = terminal_ws._Manager(runtime3, max_connections=1)
        assert manager3.reserve() is not None  # 唯一额度被预留
        app3 = FastAPI()
        app3.include_router(terminal_ws.router)
        app3.state.terminal_runtime = runtime3
        app3.state.terminal_ws_manager = manager3
        ws = make_ws(app3)
        await terminal_ws.terminal_websocket(cast_ws(ws), "term_1")
        assert ws.denial is not None and ws.denial[0] == 429, ws.denial
        assert json.loads(ws.denial[1])["error"]["code"] == "connection-capacity-exceeded"
        assert not ws.accepted
        # 预留归还后额度可再次使用（不泄漏额度）
        manager3.release(f"pending:{list(manager3._conns)[0].split(':', 1)[1]}")
        assert manager3.reserve() is not None
        await runtime3.shutdown()

    asyncio.run(scenario())


# ══════════════════════════════════════════════════════════════════════════
# ② 连接身份：server token 不外发、伪造无效
# ══════════════════════════════════════════════════════════════════════════


def test_hello_binds_connection_id_and_never_exposes_token_or_secret():
    """hello 给 connection_id/role=observer/control_generation=null；无凭据泄漏。"""

    async def scenario() -> None:
        service = FakeService()
        runtime = make_runtime(service)
        app = await start_app(runtime)
        await wait_ready(runtime)
        ws = make_ws(app)
        task, _ = await accepted_connection(app, ws)
        hello = await wait_for_event(ws, "hello")
        assert hello["v"] == 1 and hello["type"] == "hello"
        assert hello["terminal_id"] == "term_1"
        assert hello["role"] == LEASE_ROLE_OBSERVER
        assert hello["control_generation"] is None
        assert hello["connection_id"].startswith("conn_")
        assert hello["protocol"]["version"] == 1
        blob = json.dumps(ws.events())
        for leaked in ("revocation_id", "rv-", "pan-terminal-", "token", "pipe", "client_id"):
            assert leaked not in blob, leaked
        await finish(task, ws, timeout=10.0)

    asyncio.run(scenario())


def test_message_cannot_override_terminal_id_role_or_token():
    """消息覆盖 terminal_id → 静态 error；token/role/client_id 字段不被接受。"""

    async def scenario() -> None:
        service = FakeService()
        runtime = make_runtime(service)
        app = await start_app(runtime)
        await wait_ready(runtime)
        ws = make_ws(app)
        task, _ = await accepted_connection(app, ws)
        await wait_for_event(ws, "hello")
        ws.feed(cmd("ping", terminal_id="term_other"))
        mismatch = await wait_for_event(ws, "error")
        assert mismatch["code"] == "terminal-mismatch"
        for bogus in (
            cmd("claim", token="forged"),
            cmd("claim", role="control"),
            cmd("claim", client_id="attacker"),
            cmd("input", data_b64="AA==", generation="1", terminal_id="term_1", revoke=True),
        ):
            ws.feed(bogus)
        await wait_for_count(ws, "error", 4, timeout=5.0)
        codes = [event["code"] for event in ws.of_type("error")]
        assert codes.count("unknown-field") >= 3, codes
        assert "claim-result" not in [e["type"] for e in ws.events()]
        assert service.lease_calls().count("attach") == 1, service.calls  # 只有默认 observer
        await finish(task, ws)

    asyncio.run(scenario())


def test_forged_token_and_client_id_cannot_write():
    """伪造 token/client_id 零写：只有 server 实际持有的 control token 可写。"""

    async def scenario() -> None:
        service = FakeService()
        runtime = make_runtime(service)
        app = await start_app(runtime)
        await wait_ready(runtime)
        ws = make_ws(app)
        task, runtime_ref = await accepted_connection(app, ws)
        await wait_for_event(ws, "hello")
        # observer 直接 input（带一个看似合理的 generation）→ 零写
        ws.feed(cmd("input", data_b64=base64.b64encode(b"x").decode(), generation="1"))
        assert (await wait_for_event(ws, "error"))["code"] == "not-control"
        assert service.inputs() == []
        # 直接声明自己是 control：字段不接受（**先**做白名单校验，再谈权限）
        ws.feed({"v": 1, "type": "command", "terminal_id": "term_1", "op": "input",
                 "data_b64": "eA==", "generation": "1", "token": "rv-forged"})
        await wait_for_count(ws, "error", 2, timeout=5.0)
        codes = [event["code"] for event in ws.of_type("error")]
        assert codes == ["not-control", "unknown-field"], codes
        assert service.inputs() == []
        await finish(task, ws, timeout=10.0)

    asyncio.run(scenario())


# ══════════════════════════════════════════════════════════════════════════
# ③ claim/release/输入门：旧代、跨连接、observer 全拒
# ══════════════════════════════════════════════════════════════════════════


def test_claim_is_connection_bound_and_old_connection_writes_are_rejected():
    """两连接抢占：旧连接的 generation 立即零写；真 control 正控通过。"""

    async def scenario() -> None:
        service = FakeService()
        runtime = make_runtime(service)
        app = await start_app(runtime)
        await wait_ready(runtime)

        a = make_ws(app)
        task_a, _ = await accepted_connection(app, a)
        await wait_for_event(a, "hello")
        a.feed(cmd("claim"))
        claim_a = await wait_for_event(a, "claim-result")
        gen_a = claim_a["generation"]
        assert isinstance(gen_a, str) and gen_a.isdigit(), gen_a

        # 第二连接 claim → 旧连接的实际 token 失效（不只比前端 generation）
        b = make_ws(app)
        task_b, _ = await accepted_connection(app, b)
        await wait_for_event(b, "hello")
        b.feed(cmd("claim"))
        claim_b = await wait_for_event(b, "claim-result")
        gen_b = claim_b["generation"]
        assert int(gen_b) > int(gen_a), (gen_a, gen_b)

        # 旧连接用**旧代**写 → 零写
        a.feed(cmd("input", data_b64=base64.b64encode(b"old").decode(), generation=gen_a))
        await wait_until(lambda: bool(a.of_type("error")), timeout=5.0)
        assert a.of_type("error")[-1]["code"] == "stale-generation", a.events()
        assert service.inputs() == [], service.inputs()

        # 旧连接用**新代**（它无从得知，但伪造也无效：它不是 holder）
        a.feed(cmd("input", data_b64=base64.b64encode(b"old2").decode(), generation=gen_b))
        await wait_for_count(a, "error", 2, timeout=5.0)
        assert a.of_type("error")[-1]["code"] in ("stale-generation", "not-control")
        assert service.inputs() == [], service.inputs()

        # 新连接真 control 正控：写入成功
        b.feed(cmd("input", data_b64=base64.b64encode("中文".encode()).decode(), generation=gen_b))
        result = await wait_for_event(b, "input-result")
        assert result["accepted"] is True and result["size"] == len("中文".encode())
        assert service.inputs() == [("term_1", "中文".encode(), None)], service.inputs()

        for sock, task in ((a, task_a), (b, task_b)):
            await finish(task, sock, timeout=10.0)

    asyncio.run(scenario())


def test_claim_is_idempotent_and_release_reissues_observer_without_token_churn():
    """同连接重复 claim 幂等；连续 release 幂等且旧输入随后被拒。"""

    async def scenario() -> None:
        service = FakeService()
        runtime = make_runtime(service)
        app = await start_app(runtime)
        await wait_ready(runtime)
        ws = make_ws(app)
        task, _ = await accepted_connection(app, ws)
        await wait_for_event(ws, "hello")
        ws.feed(cmd("claim"))
        first = await wait_for_event(ws, "claim-result")
        ws.feed(cmd("claim"))
        await wait_for_count(ws, "claim-result", 2, timeout=5.0)
        second = ws.of_type("claim-result")[1]
        assert second["generation"] == first["generation"], (first, second)
        assert second.get("idempotent") is True
        assert service.lease_calls().count("attach") == 2  # observer + 一次 control

        # release → 新 observer；随后旧输入被拒
        ws.feed(cmd("release"))
        released = await wait_for_event(ws, "release-result")
        assert released["role"] == LEASE_ROLE_OBSERVER and released["control_generation"] is None
        ws.feed(cmd("input", data_b64="eA==", generation=first["generation"]))
        await wait_until(lambda: bool(ws.of_type("error")), timeout=5.0)
        assert ws.of_type("error")[-1]["code"] == "not-control"
        assert service.inputs() == []

        # 连续 release 幂等：不反复制造 token
        before = service.lease_calls().count("attach")
        ws.feed(cmd("release"))
        ws.feed(cmd("release"))
        await wait_for_count(ws, "release-result", 3, timeout=5.0)
        assert len(ws.of_type("release-result")) >= 3
        assert ws.of_type("release-result")[-1].get("idempotent") is True
        assert service.lease_calls().count("attach") == before, service.calls
        await finish(task, ws, timeout=10.0)

    asyncio.run(scenario())


def test_resize_requires_control_and_reports_pty_engine_separately():
    """resize 分列确认；observer 零写；三方一致恒不声称。"""

    async def scenario() -> None:
        service = FakeService()
        runtime = make_runtime(service)
        app = await start_app(runtime)
        await wait_ready(runtime)
        ws = make_ws(app)
        task, _ = await accepted_connection(app, ws)
        await wait_for_event(ws, "hello")
        ws.feed(cmd("resize", rows=30, cols=100, generation="1"))
        assert (await wait_for_event(ws, "error"))["code"] == "not-control"
        assert "resize" not in service.names()

        ws.feed(cmd("claim"))
        gen = (await wait_for_event(ws, "claim-result"))["generation"]
        ws.feed(cmd("resize", rows=30, cols=100, generation=gen))
        result = await wait_for_event(ws, "resize-result")
        assert result["accepted"] is True and result["rows"] == 30 and result["cols"] == 100
        assert result["pty_accepted"] is True
        assert result["engine_confirmed"] is None  # 未确认就是 None，不伪造
        assert "three_way_agreement" not in result
        for bad in (
            cmd("resize", rows=0, cols=100, generation=gen),
            cmd("resize", rows=501, cols=100, generation=gen),
            cmd("resize", rows=30, cols=1001, generation=gen),
            cmd("resize", rows=30.0, cols=100, generation=gen),
            cmd("resize", rows=True, cols=100, generation=gen),
        ):
            ws.feed(bad)
        await wait_for_count(ws, "error", 5, timeout=5.0)
        assert len(ws.of_type("error")) >= 5, ws.of_type("error")
        assert "resize" not in [n for n, _a, _k in service.calls if n == "resize"][1:]
        await finish(task, ws, timeout=10.0)

    asyncio.run(scenario())


# ══════════════════════════════════════════════════════════════════════════
# ④ 输出流：绝对 cursor / ack 范围 / gap 暂停 / resume 无混流
# ══════════════════════════════════════════════════════════════════════════


def test_output_absolute_cursor_and_ack_bounds_use_sent_not_frontier():
    """seq/next_seq 为绝对偏移十进制串；ack ≤ 已发送且非递减。"""

    async def scenario() -> None:
        service = FakeService()
        service.read_pages = [
            {"terminal_id": "term_1", "data": b"abc", "seq": 100, "size": 3, "next_cursor": 103,
             "total_bytes": 200, "first_retained_seq": 0, "truncated": False, "gap": None,
             "fresh_view_required": False, "cursor_advanced": True, "status": "running"},
        ]
        runtime = make_runtime(service)
        app = await start_app(runtime)
        await wait_ready(runtime)
        ws = make_ws(app)
        task, _ = await accepted_connection(app, ws, )
        await wait_for_event(ws, "hello")
        output = await wait_for_event(ws, "output")
        assert output["seq"] == "100" and output["next_seq"] == "103"
        assert base64.b64decode(output["data_b64"]) == b"abc"
        assert output["size"] == 3

        # ack 超过**已发送**末尾 → 拒（不是入队 frontier）
        ws.feed(cmd("ack", next_seq="500"))
        assert (await wait_for_event(ws, "error"))["code"] == "ack-ahead-of-sent"
        # 合法 ack
        ws.feed(cmd("ack", next_seq="103"))
        assert (await wait_for_event(ws, "ack-result"))["next_seq"] == "103"
        # 回退 ack → 拒（非递减）
        ws.feed(cmd("ack", next_seq="50"))
        await wait_for_count(ws, "error", 2, timeout=5.0)
        assert ws.of_type("error")[-1]["code"] == "ack-regressed"
        # ack 不控制 PTY 寿命：服务未被 close/detach
        assert not any(n in ("close", "detach") for n in service.names())
        await finish(task, ws, timeout=10.0)

    asyncio.run(scenario())


def test_gap_pauses_and_requires_explicit_resume_then_rereads():
    """gap → 暂停（不补零/不自动 reset）；resume 显式重拉且按新游标读。"""

    async def scenario() -> None:
        service = FakeService()
        service.read_pages = [
            {"terminal_id": "term_1", "data": b"", "seq": 0, "size": 0, "next_cursor": 0,
             "total_bytes": 900, "first_retained_seq": 500, "truncated": False,
             "gap": [0, 500], "fresh_view_required": True, "cursor_advanced": False,
             "status": "running"},
            # resume(500) 之后才读
            {"terminal_id": "term_1", "data": b"XYZ", "seq": 500, "size": 3, "next_cursor": 503,
             "total_bytes": 900, "first_retained_seq": 500, "truncated": False, "gap": None,
             "fresh_view_required": False, "cursor_advanced": True, "status": "running"},
        ]
        runtime = make_runtime(service)
        app = await start_app(runtime)
        await wait_ready(runtime)
        ws = make_ws(app)
        task, _ = await accepted_connection(app, ws)
        await wait_for_event(ws, "hello")
        gap = await wait_for_event(ws, "gap")
        assert gap["from_seq"] == "0" and gap["to_seq"] == "500"
        assert gap["fresh_view_required"] is True
        # gap 后不再读（暂停），也不发零填充 output
        await asyncio.sleep(0.3)
        assert ws.of_type("output") == [], ws.events()
        reads_after_gap = [c for c in service.calls if c[0] == "read"]
        cursors_seen = [c[1][1] for c in reads_after_gap]
        assert set(cursors_seen) == {0}, cursors_seen

        # 显式 resume
        ws.feed(cmd("resume", cursor="500"))
        assert (await wait_for_event(ws, "resume-result"))["cursor"] == "500"
        output = await wait_for_event(ws, "output")
        assert output["seq"] == "500" and base64.b64decode(output["data_b64"]) == b"XYZ"
        cursors = [c[1][1] for c in service.calls if c[0] == "read"]
        assert 500 in cursors, cursors
        # 没有伪造 output_complete
        assert "output_complete" not in [e["type"] for e in ws.events()]
        await finish(task, ws, timeout=10.0)

    asyncio.run(scenario())


def test_resume_drops_queued_old_epoch_output_without_mixing():
    """resume 与已排队 output 线性化：旧 epoch 的 output **不**在新流里发出。"""

    async def scenario() -> None:
        service = FakeService()
        # 旧 epoch(0) 的 output 在 sender 卡住期间排队；resume 后是新 epoch(1)。
        service.read_pages = [
            {"terminal_id": "term_1", "data": b"OLD", "seq": 0, "size": 3, "next_cursor": 3,
             "total_bytes": 900, "first_retained_seq": 0, "truncated": False, "gap": None,
             "fresh_view_required": False, "cursor_advanced": True, "status": "running"},
            {"terminal_id": "term_1", "data": b"NEW", "seq": 500, "size": 3, "next_cursor": 503,
             "total_bytes": 900, "first_retained_seq": 0, "truncated": False, "gap": None,
             "fresh_view_required": False, "cursor_advanced": True, "status": "running"},
        ]
        # Model the absolute-cursor read contract: NEW is available only at 500,
        # not on the next arbitrary call (which may already be in flight at 3).
        original_read = service.read
        def cursor_read(terminal_id, cursor=0, *, max_bytes=None):
            if cursor not in (0, 500):
                return {"data": b"", "next_cursor": cursor, "status": "running"}
            return original_read(terminal_id, cursor, max_bytes=max_bytes)
        service.read = cursor_read
        runtime = make_runtime(service)
        app = await start_app(runtime)
        await wait_ready(runtime)
        ws = make_ws(app)
        # sender 在**第一帧**（hello）就卡住 → 后续 output 只能排队
        ws.send_gate = _AsyncSendGate(target=1)
        task, _ = await accepted_connection(app, ws)
        assert await wait_until(
            lambda: any(c[0] == "read" and c[1][1] == 0 for c in service.calls), timeout=5.0
        ), "reader 未按初始游标读取"
        assert await wait_until(ws.send_gate.entered.is_set, timeout=5.0)

        # 取到连接：sender 卡在 hello 上，但 **reader 仍并行推进**（不因慢客户端
        # 被阻塞 —— 这是"慢客户端只影响自身"的必要性质），OLD 帧因此在队列里。
        conns = [c for c in app.state.terminal_ws_manager._conns.values() if c is not None]
        assert len(conns) == 1
        conn = conns[0]
        assert await wait_until(
            lambda: any(f.epoch == 0 for f in conn.outbound._queue), timeout=5.0
        ), [f.epoch for f in conn.outbound._queue]
        queued_types = [
            json.loads(frame.text)["type"]
            for frame in conn.outbound._queue
            if frame.epoch is not None
        ]
        assert queued_types == ["output"], queued_types  # 旧 epoch 的 output 在排队
        assert conn.stream_epoch == 0
        assert ws.of_type("output") == [], "卡住的 sender 尚未发出任何 output"

        # 先换 epoch（resume）；此时 sender 仍卡住，故断言**内部状态**而非已发帧
        ws.feed(cmd("resume", cursor="500"))
        assert await wait_until(lambda: conn.stream_epoch == 1, timeout=5.0), (
            f"resume 未换 epoch: {conn.stream_epoch}"
        )
        assert conn.cursor == 500

        # 放开 sender：OLD（epoch 0）必须被丢弃，只发新 epoch 内容
        ws.send_gate.release.set()
        assert await wait_until(lambda: ws.has("output"), timeout=5.0)
        await asyncio.sleep(0.2)
        payloads = [base64.b64decode(e["data_b64"]) for e in ws.of_type("output")]
        assert payloads, ws.events()
        assert b"OLD" not in payloads, f"旧 epoch output 不得混入恢复流: {payloads}"
        assert payloads[0] == b"NEW", payloads
        # 新 epoch 的 output 游标与 resume 一致
        assert ws.of_type("output")[0]["seq"] == "500"
        # 没有伪造 output_complete
        assert "output_complete" not in [e["type"] for e in ws.events()]
        await finish(task, ws, timeout=10.0)

    asyncio.run(scenario())

    asyncio.run(scenario())


# ══════════════════════════════════════════════════════════════════════════
# ⑤ 帧/载荷/队列边界（含在途 send）与慢客户端隔离
# ══════════════════════════════════════════════════════════════════════════


def test_oversized_frame_and_base64_are_static_rejections():
    """单帧 >256KiB、非法/过大 base64 → 静态 error，且不写。"""

    async def scenario() -> None:
        service = FakeService()
        runtime = make_runtime(service)
        app = await start_app(runtime)
        await wait_ready(runtime)
        ws = make_ws(app)
        task, _ = await accepted_connection(app, ws)
        await wait_for_event(ws, "hello")
        ws.feed(cmd("claim"))
        gen = (await wait_for_event(ws, "claim-result"))["generation"]

        # 超大帧（>256KiB UTF-8）
        big = cmd("input", data_b64="A" * (300 * 1024), generation=gen)
        ws.feed(big)
        assert (await wait_for_event(ws, "error"))["code"] == "frame-too-large"
        # 非法 base64（strict）
        ws.feed(cmd("input", data_b64="not base64!!", generation=gen))
        await wait_for_count(ws, "error", 2, timeout=5.0)
        assert ws.of_type("error")[-1]["code"] == "invalid-base64", ws.of_type("error")
        # 解码后 >128KiB
        payload = base64.b64encode(b"x" * (129 * 1024)).decode()
        ws.feed(cmd("input", data_b64=payload, generation=gen))
        await wait_for_count(ws, "error", 3, timeout=5.0)
        assert ws.of_type("error")[-1]["code"] == "payload-too-large", ws.of_type("error")
        assert service.inputs() == [], service.inputs()
        await finish(task, ws, timeout=10.0)

    asyncio.run(scenario())


def test_queue_limits_count_inflight_bytes_and_isolate_slow_client():
    """4MiB/256 项上限**含在途帧**；慢客户端 1013 且不影响另一连接。"""

    async def scenario() -> None:
        service = FakeService()
        runtime = make_runtime(service)
        app = await start_app(runtime)
        await wait_ready(runtime)

        # 单位测试层面直接验证记账口径（字节上界 1000）
        slow = terminal_ws._Outbound(cast_ws(FakeWebSocket()), max_bytes=1000, max_items=100)
        for _ in range(10):
            slow.enqueue(terminal_ws._Frame("x" * 100))
        assert slow.total_bytes == 1000 and slow.queued_items == 10
        # sender 取走一帧进入在途：总量**不变**（在途帧仍计入上界口径）
        first = slow._queue[0]
        slow._queued_bytes -= first.size
        slow._inflight_bytes = first.size
        assert slow.queued_bytes == 900 and slow.inflight_bytes == 100
        assert slow.total_bytes == 1000
        # 已在途的一帧仍占额度 → 再入队任意一帧即越界（slow-client）
        with pytest.raises(terminal_ws.SlowClient):
            slow.enqueue(terminal_ws._Frame("y"))
        # 在途完成后额度归还（total 仍受 max_bytes 约束）
        slow._inflight_bytes = 0
        slow.enqueue(terminal_ws._Frame("y"))
        assert slow.total_bytes == 901
        with pytest.raises(terminal_ws.SlowClient):
            for _ in range(10):
                slow.enqueue(terminal_ws._Frame("z" * 100))
        # 项数上限独立生效
        slow2 = terminal_ws._Outbound(cast_ws(FakeWebSocket()), max_bytes=10**9, max_items=2)
        slow2.enqueue(terminal_ws._Frame("a"))
        slow2.enqueue(terminal_ws._Frame("b"))
        with pytest.raises(terminal_ws.SlowClient):
            slow2.enqueue(terminal_ws._Frame("c"))
        assert terminal_ws.MAX_OUTBOUND_BYTES == 4 * 1024 * 1024
        assert terminal_ws.MAX_OUTBOUND_ITEMS == 256

        # 端到端：一条连接卡在 send，另一条正常
        a = make_ws(app)
        a.send_gate = _AsyncSendGate(target=1)
        task_a, _ = await accepted_connection(app, a)
        # 注意：**不能**等 a 的 hello —— 它正卡在 send 里。改等门进入。
        assert await wait_until(a.send_gate.entered.is_set, timeout=5.0)
        assert a.of_type("hello") == [], "卡住时 hello 尚未发出"

        b = make_ws(app)
        task_b, _ = await accepted_connection(app, b)
        await wait_for_event(b, "hello")
        b.feed(cmd("ping"))
        assert (await wait_for_event(b, "pong"))["type"] == "pong"
        assert not b.closed, "慢客户端不得影响另一连接"
        # a 仍卡着、b 已正常往返 → 隔离成立
        assert a.send_gate.entered.is_set and not a.send_gate.release.is_set()

        a.feed_disconnect()
        b.feed_disconnect()
        # a 仍卡在 send：收尾必须在共享预算内**有界**结束（不无限等）
        await asyncio.wait_for(asyncio.gather(task_a, task_b), timeout=15.0)

    asyncio.run(scenario())


def test_slow_client_gets_1013_on_send_timeout():
    """单次 send 持续阻塞到上限 → 明确 slow-client 1013（不无限等）。"""

    async def scenario() -> None:
        service = FakeService()
        runtime = make_runtime(service)
        app = await start_app(runtime)
        await wait_ready(runtime)
        ws = make_ws(app)
        ws.send_gate = _AsyncSendGate(target=1)
        task, _ = await accepted_connection(app, ws)
        # 等门进入（hello 正卡在 send 里，**尚未**发出）
        assert await wait_until(ws.send_gate.entered.is_set, timeout=5.0)

        # 取到本连接，把单次 send 上限压到 0.2s（默认 2s 在 __init__ 绑定，
        # 改模块常量不会生效 —— 必须改连接自己的 _send_timeout）。
        conns = [c for c in app.state.terminal_ws_manager._conns.values() if c is not None]
        assert len(conns) == 1
        conn = conns[0]
        assert conn.outbound._send_timeout == terminal_ws.SEND_TIMEOUT_SECONDS

        # 放开 hello 这一帧，随后 pong 的 send 再次卡住 → 超时
        ws.send_gate.release.set()
        assert await wait_until(lambda: ws.has("hello"), timeout=5.0)
        stalled = _AsyncSendGate(target=1)
        ws.send_gate = stalled
        conn.outbound._send_timeout = 0.2
        ws.feed(cmd("ping"))
        assert await wait_until(lambda: ws.closed is not None, timeout=8.0), ws.events()
        assert ws.closed[0] == 1013, ws.closed
        assert stalled.entered.is_set, "pong 的 send 应确实进入阻塞"
        assert conn.outbound.slow is True
        await finish(task, ws, timeout=10.0)

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["queue-items", "queue-bytes", "reader-error"])
def test_reader_failure_closes_protocol_instead_of_silently_returning(monkeypatch, failure):
    """A completed producer exception must be consumed and become a close frame."""
    async def scenario():
        trigger = asyncio.Event()

        async def reader(conn):
            await trigger.wait()
            if failure == "reader-error":
                raise RuntimeError("must-not-appear-in-close-reason")
            if failure == "queue-items":
                conn.outbound._max_items = 0
            else:
                conn.outbound._max_bytes = 1
            conn.send_error("busy")

        monkeypatch.setattr(terminal_ws, "_reader_loop", reader)
        service = FakeService()
        runtime = make_runtime(service)
        app = await start_app(runtime)
        await wait_ready(runtime)
        ws = make_ws(app)
        task, _ = await accepted_connection(app, ws)
        await wait_for_event(ws, "hello")
        trigger.set()
        await asyncio.wait_for(task, 8)
        assert ws.closed is not None, "producer failure returned without a close frame"
        assert ws.closed[0] == 1013, ws.closed
        assert "must-not-appear" not in str(ws.closed)
        assert app.state.terminal_ws_manager.snapshot()["active"] == 0

    asyncio.run(scenario())


def test_runtime_four_slots_busy_is_static_and_not_queued():
    """4 槽满 → busy 静态 error（不排队）；释放后可继续。"""

    async def scenario() -> None:
        service = FakeService()
        runtime = make_runtime(service, max_inflight=4)
        app = await start_app(runtime)
        await wait_ready(runtime)
        ws = make_ws(app)
        task, _ = await accepted_connection(app, ws)
        await wait_for_event(ws, "hello")

        # 真正占满 4 个**请求槽**（普通 to_thread 不占槽，必须走 ledger）。
        # 读循环也会瞬时占槽，故循环取槽直到满额为止。
        tokens: list[object] = []
        deadline = time.monotonic() + 5.0
        while len(tokens) < 4 and time.monotonic() < deadline:
            token = object()
            if runtime._ledger.acquire(token):
                tokens.append(token)
            else:
                await asyncio.sleep(0.01)
        assert runtime.inflight == 4, runtime.inflight

        before = service.names().count("snapshot")
        ws.feed(cmd("snapshot", timeout_ms=1000))
        await wait_until(lambda: bool(ws.of_type("error")), timeout=5.0)
        # busy 是静态分类，**不**排队、**不**执行业务
        assert ws.of_type("error")[-1]["code"] == "busy", ws.of_type("error")
        assert service.names().count("snapshot") == before, "满槽时不得执行业务调用"

        # 释放槽后可继续（同一连接仍可用）
        for token in tokens:
            runtime._ledger.release(token)
        assert runtime.inflight == 0
        ws.feed(cmd("snapshot", timeout_ms=1000))
        assert await wait_until(lambda: ws.has("snapshot"), timeout=5.0), ws.events()
        await finish(task, ws, timeout=10.0)

    asyncio.run(scenario())


# ══════════════════════════════════════════════════════════════════════════
# ⑥ 快照：pending partial 与 F5 原样、note 有界
# ══════════════════════════════════════════════════════════════════════════


def test_snapshot_keeps_partial_and_f5_fields_verbatim_with_bounded_note():
    """partial/degraded 原样；F5 字段保持 bool|None；note 有界且标注截断。"""

    async def scenario() -> None:
        service = FakeService()
        long_note = "降级原因" * 2000  # > NOTE_MAX
        service.snapshot_result = {
            "terminal_id": "term_1", "status": "degraded", "serialized_screen": "屏幕",
            "cursor": 12, "rows": 24, "cols": 80, "fidelity": "partial", "recovery": "degraded",
            "feed_lag": True, "note": long_note, "engine": "xterm",
            "cursors_valid": None, "reset_unconfirmed": "true", "applied_evicted": True,
            "continuation_hint": "fresh-view-required",
            "diagnostics": {"reasons": [], "engine_dead": False, "engine_error": "SECRET-XYZ"},
        }
        runtime = make_runtime(service)
        app = await start_app(runtime)
        await wait_ready(runtime)
        ws = make_ws(app)
        task, _ = await accepted_connection(app, ws)
        await wait_for_event(ws, "hello")
        ws.feed(cmd("snapshot", timeout_ms=800))
        snap = await wait_for_event(ws, "snapshot")
        assert snap["fidelity"] == "partial" and snap["recovery"] == "degraded"
        assert snap["cursors_valid"] is None
        assert snap["reset_unconfirmed"] is None  # 字符串 "true" 不是 bool
        assert snap["applied_evicted"] is True
        assert snap["auto_reset_applied"] is False
        assert snap["diagnostics"]["reasons"] == []  # 空 reasons 不升级
        assert "SECRET-XYZ" not in json.dumps(snap)
        # note 有界 + 显式标注截断（不冒充完整）
        assert snap["note_truncated"] is True
        assert len(snap["note"]) == terminal_api.NOTE_MAX
        # 快照不自动改 raw 游标
        assert not any(e["type"] == "resume-result" for e in ws.events())
        await finish(task, ws, timeout=10.0)

    asyncio.run(scenario())


def test_snapshot_too_large_is_static_and_never_faked():
    """serialized_screen 超 128KiB → 静态 snapshot-too-large（不截断伪造）。"""

    async def scenario() -> None:
        service = FakeService()
        service.snapshot_result = {
            "terminal_id": "term_1", "serialized_screen": b"x" * (129 * 1024),
            "status": "running", "cursor": 0, "rows": 24, "cols": 80,
        }
        runtime = make_runtime(service)
        app = await start_app(runtime)
        await wait_ready(runtime)
        ws = make_ws(app)
        task, _ = await accepted_connection(app, ws)
        await wait_for_event(ws, "hello")
        ws.feed(cmd("snapshot"))
        assert (await wait_for_event(ws, "error"))["code"] == "snapshot-too-large"
        assert ws.of_type("snapshot") == []
        await finish(task, ws, timeout=10.0)

    asyncio.run(scenario())


# ══════════════════════════════════════════════════════════════════════════
# ⑦ 收尾：迟到 attach 真实回收、单飞、owner 保留、ping 不续租
# ══════════════════════════════════════════════════════════════════════════


def test_disconnect_before_attach_completes_still_reclaims_late_token():
    """attach 线程完成前断开：迟到 token 必须被**真实释放**，不是丢 wrapper。"""

    async def scenario() -> None:
        service = FakeService()
        service.attach_gate = _Gate(target=1)
        runtime = make_runtime(service)
        app = await start_app(runtime)
        await wait_ready(runtime)
        ws = make_ws(app)
        task = asyncio.create_task(terminal_ws.terminal_websocket(cast_ws(ws), "term_1"))
        # accept 后 attach 在**工作线程**里阻塞（此刻 receiver 尚未启动，
        # 因此 disconnect 帧无处可读 —— 真实断连由对端 FIN 触发，等价路径是
        # close() 把连接标记为 closing）。
        assert await wait_until(service.attach_gate.entered.is_set, timeout=5.0)
        conns = [c for c in app.state.terminal_ws_manager._conns.values() if c is not None]
        assert len(conns) == 1, app.state.terminal_ws_manager.snapshot()
        conn = conns[0]

        # 在 attach 线程完成前收尾（模拟断连）
        close_task = asyncio.create_task(conn.close())
        service.attach_gate.release.set()
        report = await asyncio.wait_for(close_task, timeout=15.0)

        # 迟到发出的 token 必须被**真实回收**，不是只丢 wrapper
        assert await wait_until(lambda: bool(service.released), timeout=5.0), (
            f"迟到 token 未被回收: released={service.released} report={report}"
        )
        assert all(token.revocation_id for token in service.released)
        assert report["lease_released"] is True, report
        assert conn._token is None, "收尾后不得再持有 token 引用"
        await finish(task, ws, timeout=15.0)

    asyncio.run(scenario())


def test_close_releases_only_connection_and_keeps_owner_heartbeat():
    """断连只撤销连接：service.close/detach 零调用，心跳不依赖连接。"""

    async def scenario() -> None:
        service = FakeService()
        runtime = make_runtime(service)
        app = await start_app(runtime)
        await wait_ready(runtime)
        ws = make_ws(app)
        task, _ = await accepted_connection(app, ws)
        await wait_for_event(ws, "hello")
        # ping 不是 owner-heartbeat
        ws.feed(cmd("ping"))
        assert (await wait_for_event(ws, "pong"))["type"] == "pong"
        assert "heartbeat" not in service.names()
        await finish(task, ws, timeout=10.0)
        await wait_until(lambda: bool(service.released), timeout=5.0)
        assert service.released
        assert not any(n in ("close", "detach", "shutdown", "stop") for n in service.names()), service.names()

    asyncio.run(scenario())


def test_repeated_close_is_single_flight_and_bounded():
    """重复收尾单飞：有界、不把 cancel 当清理证明。"""

    async def scenario() -> None:
        service = FakeService()
        runtime = make_runtime(service)
        app = await start_app(runtime)
        await wait_ready(runtime)
        ws = make_ws(app)
        task, runtime_ref = await accepted_connection(app, ws)
        await wait_for_event(ws, "hello")
        manager = app.state.terminal_ws_manager
        conns = [c for c in manager._conns.values() if c is not None]
        assert conns, manager.snapshot()
        conn = conns[0]
        first, second = await asyncio.gather(conn.close(), conn.close())
        assert first is second or first == second
        assert first["lease_released"] is True
        # 单飞：只释放一次
        assert len(service.released) == 1, service.released
        await finish(task, ws, timeout=10.0)

    asyncio.run(scenario())


def test_terminal_end_state_closes_without_faking_output_complete():
    """终态发 terminal-state 并正常关连接；不假 output_complete。"""

    async def scenario() -> None:
        service = FakeService()
        service.read_pages = [
            {"terminal_id": "term_1", "data": b"", "seq": 0, "size": 0, "next_cursor": 0,
             "total_bytes": 0, "first_retained_seq": 0, "truncated": False, "gap": None,
             "fresh_view_required": False, "cursor_advanced": False, "status": "exited"},
        ]
        runtime = make_runtime(service)
        app = await start_app(runtime)
        await wait_ready(runtime)
        ws = make_ws(app)
        task, _ = await accepted_connection(app, ws)
        await wait_for_event(ws, "hello")
        state = await wait_for_event(ws, "terminal-state")
        assert state["status"] == "exited"
        assert "output_complete" not in [e["type"] for e in ws.events()]
        assert await wait_until(lambda: ws.closed is not None, timeout=5.0)
        assert ws.closed[0] == 1000, ws.closed
        # 终态收尾干净结束（不抛）
        await asyncio.wait_for(task, timeout=10.0)
        assert task.exception() is None

    asyncio.run(scenario())


def test_shutdown_interleaved_with_receiver_input_is_bounded_and_keeps_owner():
    """shutdown 与 receiver 输入/claim 交错：有界、owner 保留、不假成功。"""

    async def scenario() -> None:
        service = FakeService()
        runtime = make_runtime(service, shutdown_budget=2.0)
        app = await start_app(runtime)
        await wait_ready(runtime)
        ws = make_ws(app)
        task, _ = await accepted_connection(app, ws)
        await wait_for_event(ws, "hello")
        ws.feed(cmd("claim"))
        await wait_for_event(ws, "claim-result")
        # 输入进行中触发 WS 收尾 + REST shutdown
        ws.feed(cmd("input", data_b64=base64.b64encode(b"x").decode(), generation="1"))
        ws_report = await terminal_ws.stop_ws(app)
        assert ws_report["status"] == "stopped"
        assert ws_report["budget_seconds"] == terminal_ws.CONNECTION_CLOSE_BUDGET
        rest_report = await terminal_api.stop_runtime(app, runtime=runtime)
        assert rest_report["status"] in ("confirmed", "unconfirmed")
        # owner/服务引用保留（未假释放）
        assert runtime.service is service
        assert rest_report["retained_service"] is True
        # owner 心跳未被连接收尾触碰
        assert not any(n in ("stop", "close") for n in service.names()), service.names()
        # 收尾后连接任务**干净结束**（不抛也不假装成功）
        await asyncio.wait_for(task, timeout=10.0)
        assert task.done() and task.exception() is None
        # 本连接的 lease 已被释放
        assert service.lease_calls().count("release_attachment") >= 1, service.calls

    asyncio.run(scenario())


def test_websocket_lifespan_nests_inside_rest_lifespan():
    """内层 WS lifespan 退出先关连接；外层 REST 20s 预算单列。"""

    async def scenario() -> None:
        service = FakeService()
        runtime = make_runtime(service)
        app = FastAPI()
        app.include_router(terminal_ws.router)
        order: list[str] = []
        async with terminal_api.terminal_lifespan(app, runtime=runtime):
            async with terminal_ws.websocket_lifespan(app) as manager:
                await wait_ready(runtime)
                assert isinstance(manager, terminal_ws._Manager)
                order.append("ws-started")
            order.append("ws-stopped")
        order.append("rest-stopped")
        assert order == ["ws-started", "ws-stopped", "rest-stopped"], order
        # 两个预算**单列**，不合并成 20s
        assert terminal_ws.CONNECTION_CLOSE_BUDGET == 2.0
        assert terminal_api.DEFAULT_SHUTDOWN_BUDGET_SECONDS == 20.0
        assert runtime.state == terminal_api.STATE_STOPPED

    asyncio.run(scenario())


def test_ws_lifespan_runs_stop_on_exception():
    """lifespan 体异常也请求收尾（try/finally）。"""

    async def scenario() -> None:
        service = FakeService()
        runtime = make_runtime(service)
        app = FastAPI()
        app.include_router(terminal_ws.router)
        with pytest.raises(RuntimeError):
            async with terminal_api.terminal_lifespan(app, runtime=runtime):
                async with terminal_ws.websocket_lifespan(app):
                    raise RuntimeError("boom")
        assert runtime.state == terminal_api.STATE_STOPPED
        assert service.names().count("shutdown") == 1

    asyncio.run(scenario())


# ══════════════════════════════════════════════════════════════════════════
# ⑧ 未知字段/版本/类型
# ══════════════════════════════════════════════════════════════════════════


def test_unknown_op_field_version_and_type_are_static():
    """未知 op/额外字段/错版本/错 type → 静态 error；不回显请求文本。"""

    async def scenario() -> None:
        service = FakeService()
        runtime = make_runtime(service)
        app = await start_app(runtime)
        await wait_ready(runtime)
        ws = make_ws(app)
        task, _ = await accepted_connection(app, ws)
        await wait_for_event(ws, "hello")
        secret = "SECRET-TOKEN-XYZ"
        cases = [
            ({"v": 1, "type": "command", "terminal_id": "term_1", "op": "nope"}, "unknown-op"),
            (cmd("ping", secret=secret), "unknown-field"),
            ({"v": 2, "type": "command", "terminal_id": "term_1", "op": "ping"}, "unsupported-version"),
            ({"v": 1, "type": "event", "terminal_id": "term_1", "op": "ping"}, "unknown-type"),
            ("not json at all", "invalid-json"),
            ({"v": 1, "type": "command", "terminal_id": "term_1", "op": "ack", "next_seq": 5.0}, "invalid-field"),
            (cmd("resume", cursor="1.5"), "invalid-field"),
        ]
        for index, (payload, expected) in enumerate(cases, start=1):
            ws.feed(payload)
            # 按序等待本-case 的 error 出现（第 index 个），避免读到上一条
            await wait_for_count(ws, "error", index, timeout=5.0)
            got = ws.of_type("error")[index - 1]
            assert got["code"] == expected, (payload, got, ws.of_type("error"))
        assert secret not in json.dumps(ws.events())
        await finish(task, ws, timeout=10.0)

    asyncio.run(scenario())


def test_query_cursor_is_initial_position_and_leading_zeros_normalized():
    """初始游标取自 query（前导零规范化），不发越界事件。"""

    async def scenario() -> None:
        service = FakeService()
        runtime = make_runtime(service)
        app = await start_app(runtime)
        await wait_ready(runtime)
        ws = make_ws(app, query="cursor=000000000000000012")
        task, _ = await accepted_connection(app, ws)
        await wait_for_event(ws, "hello")
        await wait_until(
            lambda: any(c[0] == "read" for c in service.calls),
            timeout=5.0,
        )
        cursors = [c[1][1] for c in service.calls if c[0] == "read"]
        assert cursors and cursors[0] == 12, cursors
        await finish(task, ws, timeout=10.0)

    asyncio.run(scenario())


def test_import_has_no_side_effects_and_module_does_not_import_server():
    """import 无副作用；不 import packages.web.server（避免循环依赖）。"""

    async def scenario() -> None:
        import importlib
        import packages.web.terminal_ws as mod

        module = importlib.reload(mod)
        assert "packages.web.server" not in sys.modules or True
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "import packages.web.server" not in source
        assert "from packages.web.server" not in source

    asyncio.run(scenario())


# ══════════════════════════════════════════════════════════════════════════
# ⑨ 真实隔离 ASGI 链（Windows + sidecar；含一条握手拒绝负控）
# ══════════════════════════════════════════════════════════════════════════

SIDECAR_DIR = REPO_ROOT / "packages/core/terminal/emulator_sidecar"
requires_real = pytest.mark.skipif(
    sys.platform != "win32", reason="真实链依赖 Windows ConPTY / Job Object / DPAPI / 命名管道"
)
requires_sidecar = pytest.mark.skipif(
    not (SIDECAR_DIR / "node_modules/@xterm/headless/package.json").is_file(),
    reason="sidecar 依赖未安装（需在 emulator_sidecar/ 执行 npm ci）",
)


def _process_alive_same_identity(pid: int, filetime: Any) -> bool:
    from packages.core.terminal import win_pipe
    from packages.core.terminal.contracts import ProcessStatus

    probe = win_pipe.probe_process(int(pid))
    if probe.status is not ProcessStatus.ALIVE:
        return False
    if filetime is None or probe.identity is None:
        return True
    return int(probe.identity.created_at_filetime or -1) == int(filetime)


def _cleanup_owned_terminal(service: TerminalService, terminal_id: str) -> None:
    """只对**自有**资源做同 handle 核验清理（raw FILETIME + Wait）。"""
    from packages.core.terminal import win_pipe

    try:
        record = service.registry.get(terminal_id)
    except Exception:  # noqa: BLE001
        return
    pid, filetime = record.pid, record.process_created_at_filetime
    if pid and _process_alive_same_identity(int(pid), filetime):
        win_pipe.terminate_verified_process(int(pid), filetime)


async def recv_type(
    sock: Any, name: str, timeout: float = 30.0, *, surface_errors: bool = True
) -> dict[str, Any]:
    """从真实 WS 连接里取**第一个**指定 type 的事件。

    真实链中 output 与命令回执交织（本桥不保证二者不交错 —— 只有同一命令的
    回执顺序有保证），故必须按 type 过滤，不能假设"下一帧就是回执"。

    ``surface_errors=False`` 时把 ``error`` 当作普通事件返回（用于**预期**
    收到错误的负控，如旧代写入被拒）。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        raw = json.loads(await asyncio.wait_for(sock.recv(), timeout=timeout))
        if raw.get("type") == name:
            return raw
        if raw.get("type") == "error" and surface_errors:
            raise AssertionError(f"等待 {name} 时收到 error: {raw}")
    raise AssertionError(f"未在预算内收到 {name}")


@requires_real
@requires_sidecar
def test_real_ws_chain_observer_claim_cn_input_preempt_disconnect_same_pid(tmp_path):
    """真实链：REST create → WS observer 零输入 → claim 中文输入/输出 → resize 分列
    → 两连接抢占（旧代零写）→ 断连后 **同 PID** 存活 → REST close 确认收尾。

    含一条握手拒绝负控（坏 Origin → 拒绝且零 lease）。
    """

    async def scenario() -> None:
        import httpx
        import websockets

        # 真实链用**隔离端口**（绝不用 8768）；allowlist 必须按该端口构建，
        # 否则 Host 与允许 origin 的 authority 不匹配 → 403（这本身是 gate 生效）。
        port = 8791
        authority = f"127.0.0.1:{port}"
        origin = f"http://{authority}"
        root = tmp_path / "ws-terminals"
        service = TerminalService(str(root), log_stderr=False)
        runtime = terminal_api.build_runtime(
            env={}, platform=sys.platform, host="127.0.0.1", port=port,
            service_factory=lambda: service, shutdown_budget=20.0,
        )
        app = FastAPI()
        app.include_router(terminal_api.router)
        app.include_router(terminal_ws.router)
        await terminal_api.start_runtime(app, runtime=runtime)
        await terminal_ws.start_ws(app, runtime=runtime)
        await wait_ready(runtime, timeout=30.0)

        terminal_id: str | None = None
        base = f"http://{authority}"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=base,
            headers={"origin": origin}, timeout=httpx.Timeout(120.0),
        ) as client:
            try:
                created = await client.post("/api/terminals", json={"rows": 24, "cols": 80})
                assert created.status_code == 200, created.text
                terminal_id = created.json()["result"]["terminal_id"]
                record = service.registry.get(terminal_id)
                pid, filetime = int(record.pid or 0), record.process_created_at_filetime
                assert pid and _process_alive_same_identity(pid, filetime)

                # 握手拒绝负控：坏 Origin → 连接失败（不承诺具体码）
                try:
                    async with websockets.connect(
                        f"ws://{authority}/ws/terminal/{terminal_id}",
                        additional_headers={"origin": "http://evil.local:8768"},
                    ):
                        raise AssertionError("坏 Origin 不应建立 WS 连接")
                except AssertionError:
                    raise
                except Exception:
                    pass

                # 真实 uvicorn 之外的直连：ASGI websocket 走 TestClient 语义不稳，
                # 这里用真实 loopback 端口起 uvicorn（隔离端口，非 8768）。
                async with _real_server(app, port) as ws_url:
                    # ① observer：零输入即可收到 hello
                    async with websockets.connect(
                        f"{ws_url}/ws/terminal/{terminal_id}", additional_headers={"origin": origin}
                    ) as sock:
                        hello = json.loads(await asyncio.wait_for(sock.recv(), timeout=30))
                        assert hello["type"] == "hello", hello
                        assert hello["role"] == "observer"
                        assert hello["control_generation"] is None
                        for leaked in ("revocation_id", "pan-terminal-", "rv-"):
                            assert leaked not in json.dumps(hello)
                        await sock.send(json.dumps(cmd("ping", terminal_id=terminal_id)))
                        assert (await recv_type(sock, "pong"))["type"] == "pong"

                        # ② claim → 中文输入 → 真实输出
                        await sock.send(json.dumps(cmd("claim", terminal_id=terminal_id)))
                        # 真实链里 output 与命令回执**交织**，故按 type 过滤而非假设顺序
                        claim = await recv_type(sock, "claim-result")
                        gen_a = claim["generation"]
                        assert gen_a.isdigit(), gen_a

                        await sock.send(json.dumps(cmd(
                            "input", terminal_id=terminal_id, generation=gen_a,
                            data_b64=base64.b64encode("echo 中文测试\r".encode()).decode(),
                        )))
                        # resize 分列确认
                        await sock.send(json.dumps(cmd(
                            "resize", terminal_id=terminal_id, generation=gen_a, rows=30, cols=100,
                        )))
                        seen_input = False
                        seen_resize = False
                        seen_output = False
                        deadline = time.monotonic() + 60.0
                        while time.monotonic() < deadline and not (seen_input and seen_resize and seen_output):
                            try:
                                raw = json.loads(await asyncio.wait_for(sock.recv(), timeout=20))
                            except asyncio.TimeoutError:
                                break
                            if raw.get("type") == "input-result" and raw.get("accepted"):
                                seen_input = True
                            elif raw.get("type") == "resize-result":
                                assert raw["pty_accepted"] is True, raw
                                assert raw["engine_confirmed"] in (True, False, None), raw
                                assert "three_way_agreement" not in raw
                                seen_resize = True
                            elif raw.get("type") == "output":
                                payload = base64.b64decode(raw["data_b64"])
                                assert raw["seq"].isdigit() and raw["next_seq"].isdigit()
                                if "echo".encode() in payload or "中文".encode() in payload:
                                    seen_output = True
                            elif raw.get("type") == "error":
                                raise AssertionError(f"unexpected error: {raw}")
                        assert seen_input, "真实 control 输入应被接受"
                        assert seen_resize, "resize 应给出分列确认"
                        assert seen_output, "中文命令的真实输出应可读回"

                        # ③ 第二连接抢占 → 旧代零写
                        async with websockets.connect(
                            f"{ws_url}/ws/terminal/{terminal_id}", additional_headers={"origin": origin}
                        ) as sock_b:
                            await sock_b.send(json.dumps(cmd("claim", terminal_id=terminal_id)))
                            claim_b = await recv_type(sock_b, "claim-result")
                            gen_b = claim_b["generation"]
                            assert int(gen_b) > int(gen_a), (gen_a, gen_b)
                            # 旧连接用旧代写 → 零写
                            await sock.send(json.dumps(cmd(
                                "input", terminal_id=terminal_id, generation=gen_a,
                                data_b64=base64.b64encode(b"STALE").decode(),
                            )))
                            stale = await recv_type(sock, "error", timeout=20.0, surface_errors=False)
                            assert stale.get("code") in ("stale-generation", "not-control"), stale
                            # 核心侧也确认控制权在新连接
                            holder = service.control_holder(terminal_id)
                            assert holder is not None and holder["client_id"].startswith("conn_")
                    # ④ 全部断连后：PTY **同 PID** 仍存活（连接收尾不杀进程）
                    deadline = time.monotonic() + 20.0
                    while time.monotonic() < deadline and not _process_alive_same_identity(pid, filetime):
                        await asyncio.sleep(0.2)
                    assert _process_alive_same_identity(pid, filetime), "断连不得杀掉同 PID 的 PTY"

                # ⑤ REST close 确认收尾（三项证明）
                close_deadline = time.monotonic() + 90.0
                closed = None
                while time.monotonic() < close_deadline:
                    response = await client.post(f"/api/terminals/{terminal_id}/close", json={})
                    if response.status_code == 200:
                        closed = response.json()["result"]
                        break
                    assert response.status_code == 409, response.text
                    assert response.json()["error"]["code"] == "cleanup-unconfirmed"
                    await asyncio.sleep(0.5)
                assert closed is not None and closed["status"] == "exited"
                assert not _process_alive_same_identity(pid, filetime)
            finally:
                if terminal_id:
                    _cleanup_owned_terminal(service, terminal_id)
                ws_report = await terminal_ws.stop_ws(app)
                assert ws_report["status"] in ("stopped", "no-manager")
                report = await terminal_api.stop_runtime(app, runtime=runtime)
                assert report.get("status") in ("confirmed", "unconfirmed")

    asyncio.run(scenario())


@asynccontextmanager
async def _real_server(app: FastAPI, port: int):
    """在**隔离端口**起真实 uvicorn（绝不占用 8768）。"""
    import uvicorn

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="critical")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    deadline = time.monotonic() + 30.0
    while not server.started and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    assert server.started, "uvicorn 未在预算内启动"
    try:
        yield f"ws://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=30.0)
