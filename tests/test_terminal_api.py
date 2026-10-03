"""Terminal REST 入口测试（P2 REST/lifespan 第一批）。

分三层，与既有套件口径一致：

- **确定性层**（FastAPI 小 app + 注入 fake service，不起真实进程）：入口 gate
  （Origin/Host/Fetch/content-type）被拒请求**零 service 调用**、额外字段/身份伪造
  零作用、真整数与 cursor/uint64 精确、read/snapshot 出口投影（bytes→data_b64、
  游标十进制字符串、F5 bool-or-null、诊断白名单、128KiB 上限）、静态错误映射与
  异常文本零回显、close 重试可达、detach 拒绝不伪称 durable、非 Windows/非 loopback
  绑定零构造、启动不假 ready、reconcile 失败不 ready、4 槽有界与取消不提前释放、
  慢方法不阻塞事件循环、shutdown 超时保留引用并消费迟到结果。
- **接线层**：真实 ``packages.web.server.app`` 已注册终端路由（**不启动**完整
  Pan lifespan），合法请求得到入口 gate 的响应而非 404。
- **真实隔离 ASGI 链**（Windows + 真 ConPTY/Job/DPAPI/命名管道/headless 引擎，
  隔离临时数据根）：create → read → snapshot → close，同 handle 身份核验零残留。

纪律：不用 ``8768`` 监听、不启动完整 Pan/provider/账号；清理只对**自有**资源做
同 handle 身份核验（raw FILETIME + Wait），不按名广杀。
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest
from fastapi import FastAPI

from packages.core.terminal.contracts import (
    UnknownTerminalError,
)
from packages.core.terminal.service import (
    CapacityExceeded,
    CleanupUnconfirmed,
    DetachRefused,
    ServiceClosingDown,
    StartupFailed,
    TerminalNotAttached,
    TerminalService,
)
from packages.web import terminal_api

REPO_ROOT = Path(__file__).resolve().parents[1]
AUTHORITY = "127.0.0.1:8768"
ORIGIN = f"http://{AUTHORITY}"


# ══════════════════════════════════════════════════════════════════════════
# 测试替身
# ══════════════════════════════════════════════════════════════════════════


class _FakeRequest:
    """最小请求替身（仅 headers/method），用于无法经 httpx 表达的边界。"""

    def __init__(self, headers: dict[str, str], method: str = "GET") -> None:
        self.headers = dict(headers)
        self.method = method


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
    """TerminalService 替身：脚本化结果/错误/延迟/阻塞门 + 调用记录。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.calls: list[tuple[str, tuple, dict]] = []
        self.results: dict[str, Any] = {
            "list": [],
            "get": {},
            "create": {},
            "read": {},
            "snapshot": {},
            "close": {},
            "detach": {"terminal_id": "term_1", "detached": False, "status": "detach-refused",
                       "state_changed": False},
        }
        self.errors: dict[str, BaseException] = {}
        self.delays: dict[str, float] = {}
        self.gates: dict[str, _Gate] = {}
        self.reconcile_result: Any = {"buckets": {}, "counts": {}, "records": 0}
        self.reconcile_error: BaseException | None = None
        self.reconcile_gate: _Gate | None = None
        self.shutdown_result: Any = {"exited": [], "unconfirmed": [], "secrets_retained": False}
        self.shutdown_delay = 0.0
        self.shutdown_error: BaseException | None = None

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        with self._lock:
            self.calls.append((name, args, kwargs))

    #: 生命周期调用（lifespan 起停触发），与"请求触发的业务调用"分开统计。
    LIFECYCLE = frozenset({"reconcile", "shutdown"})

    def names(self) -> list[str]:
        """请求触发的业务调用名（排除 lifespan 起停的 reconcile/shutdown）。"""
        with self._lock:
            return [name for name, _args, _kwargs in self.calls if name not in self.LIFECYCLE]

    def _pre(self, name: str) -> None:
        delay = self.delays.get(name)
        if delay:
            time.sleep(float(delay))
        gate = self.gates.get(name)
        if gate is not None:
            gate.enter()
        error = self.errors.get(name)
        if error is not None:
            raise error

    # -- 公共方法 ------------------------------------------------------
    def list(self) -> Any:
        self._record("list")
        self._pre("list")
        return self.results["list"]

    def get(self, terminal_id: str) -> Any:
        self._record("get", terminal_id)
        self._pre("get")
        return self.results["get"]

    def create(self, **kwargs: Any) -> Any:
        self._record("create", **kwargs)
        self._pre("create")
        return self.results["create"]

    def read(self, terminal_id: str, cursor: int = 0, *, max_bytes: Any = None) -> Any:
        self._record("read", terminal_id, cursor, max_bytes=max_bytes)
        self._pre("read")
        return self.results["read"]

    def snapshot(self, terminal_id: str, *, timeout_ms: int = 5000) -> Any:
        self._record("snapshot", terminal_id, timeout_ms=timeout_ms)
        self._pre("snapshot")
        return self.results["snapshot"]

    def close(self, terminal_id: str, *, reason: str = "explicit-close") -> Any:
        self._record("close", terminal_id, reason=reason)
        self._pre("close")
        return self.results["close"]

    def detach(self, terminal_id: str) -> Any:
        self._record("detach", terminal_id)
        self._pre("detach")
        return self.results["detach"]

    def reconcile(self) -> Any:
        self._record("reconcile")
        if self.reconcile_gate is not None:
            self.reconcile_gate.enter()
        if self.reconcile_error is not None:
            raise self.reconcile_error
        return self.reconcile_result

    def shutdown(self, *, budget: Any = None) -> Any:
        self._record("shutdown", budget=budget)
        if self.shutdown_delay:
            time.sleep(float(self.shutdown_delay))
        if self.shutdown_error is not None:
            raise self.shutdown_error
        return self.shutdown_result


# ══════════════════════════════════════════════════════════════════════════
# 辅助
# ══════════════════════════════════════════════════════════════════════════


def make_runtime(service: Any, **kwargs: Any) -> terminal_api.TerminalRuntime:
    params: dict[str, Any] = {
        "env": {},
        "platform": "win32",
        "host": "127.0.0.1",
        "port": 8768,
        "service_factory": lambda: service,
    }
    params.update(kwargs)
    return terminal_api.build_runtime(**params)


async def start_app(runtime: terminal_api.TerminalRuntime) -> FastAPI:
    app = FastAPI()
    app.include_router(terminal_api.router)
    await terminal_api.start_runtime(app, runtime=runtime)
    return app


async def wait_ready(runtime: terminal_api.TerminalRuntime, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while runtime.state != terminal_api.STATE_READY and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    assert runtime.state == terminal_api.STATE_READY, f"runtime not ready: {runtime.state!r}"


def make_client(
    app: FastAPI,
    *,
    authority: str = AUTHORITY,
    origin: str | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> httpx.AsyncClient:
    base = f"http://{authority}"
    hdrs = {"origin": base if origin is None else origin}
    if headers:
        hdrs.update(headers)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url=base,
        headers=hdrs,
        timeout=httpx.Timeout(timeout),
    )


def sample_view(**overrides: Any) -> dict[str, Any]:
    view = {
        "terminal_id": "term_1",
        "status": "running",
        "owner": "service",
        "rows": 24,
        "cols": 80,
        "pid": 1234,
        "process_created_at_filetime": "133400000000000000",
        "detached": False,
        "detached_at": None,
        "scope": {"workspace_id": "ws-1", "session_id": "sess-1"},
        "exit": {"code": None, "reason": None},
        "lease_grace_seconds": 2.0,
        "created_by": "web-local",
        "created_at": 1.0,
        "updated_at": 2.0,
        "attached": True,
        "authorization": "scope-is-metadata-not-permission",
        "heartbeat": {"client_id": "pan-owner-term_1", "beats": 3, "lost": False},
        # 非白名单：必须不出现在出口
        "pipe": r"\\.\pipe\pan-terminal-1",
        "token": "SECRET-TOKEN-XYZ",
        "internal_path": r"C:\Users\someone\.secret",
    }
    view.update(overrides)
    return view


# ══════════════════════════════════════════════════════════════════════════
# ① 入口 gate：被拒请求零 service 调用
# ══════════════════════════════════════════════════════════════════════════

_ORIGIN_REJECTIONS = [
    "null",
    "http://127.0.0.1:9999",
    "http://evil.127.0.0.1:8768",
    "https://127.0.0.1:8768",
    "http://127.0.0.1:8768/extra",
    "http://user@127.0.0.1:8768",
    "http://127.0.0.1:8768/?q=1",
    "http://*:8768",
    "http://localhost:8768 ",  # 前后空白
]


def test_gate_origin_boundaries_reject_with_zero_service_calls(tmp_path):
    """全部 Origin 边界：缺失/null/伪相似子域/错端口/错 scheme/带 path/通配 → 403 零调用。"""

    async def scenario() -> None:
        service = FakeService()
        runtime = make_runtime(service)
        app = await start_app(runtime)
        await wait_ready(runtime)
        async with make_client(app, origin=ORIGIN) as good:
            assert (await good.get("/api/terminals")).status_code == 200
        # 缺失 Origin
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url=f"http://{AUTHORITY}",
            timeout=httpx.Timeout(10.0),
        ) as bare:
            response = await bare.get("/api/terminals")
            assert response.status_code == 403
            assert response.json() == {"ok": False, "error": {"code": "forbidden-origin"}}
        for origin in _ORIGIN_REJECTIONS:
            async with make_client(app, origin=origin) as client:
                response = await client.get("/api/terminals")
                assert response.status_code == 403, (origin, response.status_code)
                assert response.json()["error"]["code"] == "forbidden-origin"
                post = await client.post("/api/terminals", json={})
                assert post.status_code == 403
        # 被拒请求零 service 调用（仅有正控那 1 次 list）
        assert service.names() == ["list"]

    asyncio.run(scenario())


def test_gate_allowlist_env_override_and_invalid_config_fails_closed(tmp_path):
    """PAN_TERMINAL_ALLOWED_ORIGINS 可替换；配置非法 fail-closed 不放行。"""

    async def scenario() -> None:
        # 替换为 9999：8768 默认域不再放行
        service = FakeService()
        runtime = make_runtime(service, env={"PAN_TERMINAL_ALLOWED_ORIGINS": "http://127.0.0.1:9999"})
        app = await start_app(runtime)
        await wait_ready(runtime)
        async with make_client(app, authority="127.0.0.1:9999", origin="http://127.0.0.1:9999") as client:
            assert (await client.get("/api/terminals")).status_code == 200
        async with make_client(app, authority=AUTHORITY, origin=ORIGIN) as client:
            assert (await client.get("/api/terminals")).status_code == 403

        # 非法配置（含 path / 非 URL / 通配 / null）→ 全部拒绝
        for bad in ("http://127.0.0.1:8768/path", "not-a-url", "http://*:8768", "null", "ftp://x:1"):
            bad_service = FakeService()
            bad_runtime = make_runtime(bad_service, env={"PAN_TERMINAL_ALLOWED_ORIGINS": bad})
            bad_app = await start_app(bad_runtime)
            await wait_ready(bad_runtime)
            async with make_client(bad_app, origin=ORIGIN) as client:
                response = await client.get("/api/terminals")
                assert response.status_code == 403, bad
                assert response.json()["error"]["code"] == "forbidden-origin"
            assert bad_service.names() == []

    asyncio.run(scenario())


def test_gate_host_must_match_allowed_authority_and_forwarded_ignored():
    """Host 必须与允许 origin 的 authority 匹配；X-Forwarded-* 不得扩信任。"""

    async def scenario() -> None:
        service = FakeService()
        runtime = make_runtime(service)
        app = await start_app(runtime)
        await wait_ready(runtime)
        # Origin 合法但 Host 不同 authority
        async with make_client(app, authority="evil.local:8768", origin=ORIGIN) as client:
            response = await client.get("/api/terminals")
            assert response.status_code == 403
            assert response.json()["error"]["code"] == "forbidden-host"
        # 伪造转发头不扩信任（Host 仍是 evil）
        async with make_client(
            app, authority="evil.local:8768", origin=ORIGIN,
            headers={"x-forwarded-host": AUTHORITY, "x-forwarded-for": "127.0.0.1"},
        ) as client:
            response = await client.get("/api/terminals")
            assert response.status_code == 403
            assert response.json()["error"]["code"] == "forbidden-host"
        # Host 缺失 → 403（直接驱动 gate：httpx 总会补 Host）
        with pytest.raises(terminal_api.GateRejected) as excinfo:
            runtime.check_gate(_FakeRequest({"origin": ORIGIN}))
        assert excinfo.value.code == "forbidden-host"
        assert service.names() == []

    asyncio.run(scenario())


def test_gate_sec_fetch_site_and_post_content_type():
    """Sec-Fetch-Site 只接受 same-origin/same-site；POST 必须 application/json。"""

    async def scenario() -> None:
        service = FakeService()
        runtime = make_runtime(service)
        app = await start_app(runtime)
        await wait_ready(runtime)
        service.results["create"] = sample_view()
        for value, expected in (
            ("cross-site", 403),
            ("none", 403),
            ("weird", 403),
            ("same-origin", 200),
            ("same-site", 200),
            (None, 200),
        ):
            headers = {} if value is None else {"sec-fetch-site": value}
            async with make_client(app, origin=ORIGIN, headers=headers) as client:
                response = await client.get("/api/terminals")
                assert response.status_code == expected, (value, response.status_code)
                if expected == 403:
                    assert response.json()["error"]["code"] == "forbidden-fetch-site"
        # content-type：simple request 拒绝
        for ctype, expected in (("text/plain", 403), ("multipart/form-data; boundary=x", 403),
                                ("application/json", 200), ("application/json; charset=utf-8", 200)):
            async with make_client(app, origin=ORIGIN) as client:
                response = await client.post(
                    "/api/terminals", content=b"{}", headers={"content-type": ctype}
                )
                assert response.status_code == expected, (ctype, response.status_code)
                if expected == 403:
                    assert response.json()["error"]["code"] == "forbidden-content-type"

    asyncio.run(scenario())


def test_remote_binding_and_non_windows_disable_without_construction():
    """非 loopback 绑定默认禁用（需显式开关）；非 Windows 一律禁用且**零构造**。"""

    async def scenario() -> None:
        built: list[int] = []

        def factory() -> FakeService:
            built.append(1)
            return FakeService()

        for kwargs, expect_disabled in (
            ({"platform": "linux", "host": "127.0.0.1"}, True),
            ({"platform": "win32", "host": "0.0.0.0"}, True),
            ({"platform": "win32", "host": "127.0.0.1"}, False),
        ):
            runtime = terminal_api.build_runtime(env={}, service_factory=factory, **kwargs)
            app = await start_app(runtime)
            assert runtime.enabled is not expect_disabled
            if expect_disabled:
                assert runtime.service is None
            else:
                await wait_ready(runtime)
            async with make_client(app, origin=ORIGIN) as client:
                response = await client.get("/api/terminals")
                if expect_disabled:
                    assert response.status_code == 503
                    assert response.json()["error"]["code"] == "terminal-disabled"
                else:
                    assert response.status_code == 200
        # 显式开关解除 loopback 禁用
        runtime = terminal_api.build_runtime(
            env={"PAN_TERMINAL_ALLOW_REMOTE": "1"}, platform="win32", host="0.0.0.0",
            service_factory=FakeService,
        )
        assert runtime.enabled is True
        # 禁用路径从未构造 service
        assert runtime.service is None

    asyncio.run(scenario())


# ══════════════════════════════════════════════════════════════════════════
# ② 输入校验（身份伪造零作用 / 真整数 / 游标精确）
# ══════════════════════════════════════════════════════════════════════════

_FORBIDDEN_CREATE_FIELDS = ("context", "created_by", "trusted_local", "terminal_id", "token", "shell_argv")


def test_create_rejects_extra_fields_and_context_not_overridable():
    """额外/身份字段一律 422 零调用；合法 create 只能用固定本地上下文。"""

    async def scenario() -> None:
        service = FakeService()
        service.results["create"] = sample_view()
        runtime = make_runtime(service)
        app = await start_app(runtime)
        await wait_ready(runtime)
        async with make_client(app, origin=ORIGIN) as client:
            for field in _FORBIDDEN_CREATE_FIELDS:
                response = await client.post("/api/terminals", json={field: "trusted", "rows": 24})
                assert response.status_code == 422, field
                assert response.json()["error"]["code"] == "unknown-field"
                assert "trusted" not in response.text
            assert service.names() == []
            # 合法 create
            response = await client.post("/api/terminals", json={"rows": 30, "cols": 100, "cwd": "C:/tmp"})
            assert response.status_code == 200
            body = response.json()
            assert body["ok"] is True
            assert body["result"]["terminal_id"] == "term_1"
        _name, _args, kwargs = service.calls[-1]
        assert set(kwargs) == {"rows", "cols", "cwd", "workspace_id", "session_id", "context"}
        assert kwargs["rows"] == 30 and kwargs["cols"] == 100 and kwargs["cwd"] == "C:/tmp"
        assert kwargs["context"] is terminal_api.WEB_LOCAL_CONTEXT
        assert kwargs["context"].created_by == "web-local"
        assert kwargs["context"].trusted_local is True

    asyncio.run(scenario())


def test_create_size_validation_real_ints_and_bounds():
    """rows/cols 必须是真整数（非 bool）且 1..1000；字符串/浮点/null 一律 422。"""

    async def scenario() -> None:
        service = FakeService()
        service.results["create"] = sample_view()
        runtime = make_runtime(service)
        app = await start_app(runtime)
        await wait_ready(runtime)
        bad_values = [True, False, 1.5, "24", 0, 1001, -1, None, [24]]
        async with make_client(app, origin=ORIGIN) as client:
            for value in bad_values:
                response = await client.post("/api/terminals", json={"rows": value})
                assert response.status_code == 422, value
                assert response.json()["error"]["code"] in ("invalid-field", "unknown-field")
                response = await client.post("/api/terminals", json={"cols": value})
                assert response.status_code == 422, value
            assert service.names() == []
            assert (await client.post("/api/terminals", json={"rows": 1, "cols": 1000})).status_code == 200
            # cwd 超长 / 非字符串
            assert (await client.post("/api/terminals", json={"cwd": "x" * 4097})).status_code == 422
            assert (await client.post("/api/terminals", json={"cwd": 123})).status_code == 422
            assert (await client.post("/api/terminals", json={"workspace_id": 5})).status_code == 422

    asyncio.run(scenario())


def test_read_cursor_and_max_bytes_precision():
    """cursor 为 ASCII 非负十进制字符串（uint64）；max_bytes 真整数 1..131072。"""

    async def scenario() -> None:
        service = FakeService()
        service.results["read"] = {
            "terminal_id": "term_1", "data": b"", "seq": 0, "size": 0, "next_cursor": 0,
            "total_bytes": 0, "first_retained_seq": 0, "gap": None, "truncated": False,
            "fresh_view_required": False, "cursor_advanced": False, "status": "running",
        }
        runtime = make_runtime(service)
        app = await start_app(runtime)
        await wait_ready(runtime)
        bad_cursors = ["abc", "-1", "+1", "1.0", "1e3", "0x10", " 1", "", "18446744073709551616", "１２"]
        async with make_client(app, origin=ORIGIN) as client:
            for value in bad_cursors:
                response = await client.get(f"/api/terminals/term_1/read?cursor={_encode(value)}")
                assert response.status_code == 422, value
                assert response.json()["error"]["code"] == "invalid-cursor", value
            assert (await client.get("/api/terminals/term_1/read")).status_code == 200
            assert (await client.get("/api/terminals/term_1/read?cursor=007")).status_code == 200
            assert (await client.get("/api/terminals/term_1/read?cursor=18446744073709551615")).status_code == 200
            for value in ["0", "131073", "65536.0", "true", "abc", ""]:
                response = await client.get(f"/api/terminals/term_1/read?max_bytes={_encode(value)}")
                assert response.status_code == 422, value
                assert response.json()["error"]["code"] == "invalid-max-bytes", value
            assert (await client.get("/api/terminals/term_1/read?max_bytes=1")).status_code == 200
            assert (await client.get("/api/terminals/term_1/read?max_bytes=131072")).status_code == 200
        # 校验 service 收到的是真整数（007 → 7）
        read_calls = [call for call in service.calls if call[0] == "read"]
        cursors = [call[1][1] for call in read_calls]
        assert 7 in cursors and 0 in cursors
        max_bytes_seen = {call[2]["max_bytes"] for call in read_calls}
        assert max_bytes_seen == {65536, 1, 131072}

    asyncio.run(scenario())


def _encode(value: str) -> str:
    import urllib.parse

    return urllib.parse.quote(value, safe="")


def test_snapshot_timeout_validation_real_ints():
    async def scenario() -> None:
        service = FakeService()
        service.results["snapshot"] = {"terminal_id": "term_1", "status": "running",
                                       "serialized_screen": "x", "cursor": 1, "rows": 24, "cols": 80}
        runtime = make_runtime(service)
        app = await start_app(runtime)
        await wait_ready(runtime)
        async with make_client(app, origin=ORIGIN) as client:
            for value in (True, 1.0, "1", 0, 5001, -1, None):
                response = await client.post("/api/terminals/term_1/snapshot", json={"timeout_ms": value})
                assert response.status_code == 422, value
            assert service.names().count("snapshot") == 0
            assert (await client.post("/api/terminals/term_1/snapshot", json={"timeout_ms": 1})).status_code == 200
            assert (await client.post("/api/terminals/term_1/snapshot", json={"timeout_ms": 5000})).status_code == 200
            assert (await client.post("/api/terminals/term_1/snapshot", json={})).status_code == 200
            assert (await client.post("/api/terminals/term_1/snapshot", content=b"",
                                      headers={"content-type": "application/json"})).status_code == 200
            # 额外字段
            assert (await client.post("/api/terminals/term_1/snapshot", json={"rows": 1})).status_code == 422

    asyncio.run(scenario())


def test_unknown_and_malformed_terminal_id():
    """非法 id 422（不触 service）；合法但不存在 404。"""

    async def scenario() -> None:
        service = FakeService()
        service.errors["get"] = UnknownTerminalError("term_missing")
        runtime = make_runtime(service)
        app = await start_app(runtime)
        await wait_ready(runtime)
        async with make_client(app, origin=ORIGIN) as client:
            for bad in ("bad", "bad id", "term_", "term_" + "a" * 65, "TERM_abc", "term_a b"):
                response = await client.get(f"/api/terminals/{bad}")
                assert response.status_code == 422, bad
                assert response.json()["error"]["code"] == "invalid-terminal-id"
            assert service.names() == []
            response = await client.get("/api/terminals/term_missing")
            assert response.status_code == 404
            assert response.json()["error"]["code"] == "unknown-terminal"

    asyncio.run(scenario())


# ══════════════════════════════════════════════════════════════════════════
# ③ 出口投影
# ══════════════════════════════════════════════════════════════════════════


def test_list_and_get_whitelist_projection_hides_unknown_and_secret_fields():
    async def scenario() -> None:
        service = FakeService()
        service.results["list"] = [sample_view()]
        service.results["get"] = sample_view()
        runtime = make_runtime(service)
        app = await start_app(runtime)
        await wait_ready(runtime)
        async with make_client(app, origin=ORIGIN) as client:
            listing = (await client.get("/api/terminals")).json()["result"]
            detail = (await client.get("/api/terminals/term_1")).json()["result"]
        for result in (listing[0], detail):
            assert result["terminal_id"] == "term_1"
            assert result["scope"] == {"workspace_id": "ws-1", "session_id": "sess-1"}
            assert "pipe" not in result and "token" not in result and "internal_path" not in result
            assert "pan-terminal-" not in json.dumps(result)
            assert "SECRET-TOKEN-XYZ" not in json.dumps(result)

    asyncio.run(scenario())


def test_read_projection_decimal_strings_and_base64():
    async def scenario() -> None:
        service = FakeService()
        payload = b"\x00\x01ab\xff" + "汉字".encode("utf-8")
        service.results["read"] = {
            "terminal_id": "term_1", "data": payload, "seq": 5, "size": len(payload),
            "next_cursor": 9, "total_bytes": 1234, "first_retained_seq": 3, "gap": [1, 2],
            "truncated": True, "fresh_view_required": True, "cursor_advanced": True, "status": "running",
        }
        runtime = make_runtime(service)
        app = await start_app(runtime)
        await wait_ready(runtime)
        async with make_client(app, origin=ORIGIN) as client:
            result = (await client.get("/api/terminals/term_1/read?cursor=5")).json()["result"]
        assert base64.b64decode(result["data_b64"]) == payload
        for key in ("seq", "next_cursor", "total_bytes", "first_retained_seq"):
            assert isinstance(result[key], str) and result[key].isdigit(), key
        assert result["seq"] == "5" and result["next_cursor"] == "9" and result["total_bytes"] == "1234"
        assert result["first_retained_seq"] == "3"
        assert result["gap"] == ["1", "2"]
        assert isinstance(result["size"], int) and result["size"] == len(payload)
        assert result["zero_fill"] is False
        # gap=None 时输出 null
        service.results["read"]["gap"] = None
        async with make_client(app, origin=ORIGIN) as client:
            result = (await client.get("/api/terminals/term_1/read")).json()["result"]
        assert result["gap"] is None

    asyncio.run(scenario())


def test_snapshot_projection_b64_bool_or_null_whitelist_and_no_upgrade():
    async def scenario() -> None:
        service = FakeService()
        service.results["snapshot"] = {
            "terminal_id": "term_1",
            "status": "running",
            "serialized_screen": "prompt> 汉字",
            "cursor": 12,
            "rows": 24,
            "cols": 80,
            "fidelity": "partial",
            "recovery": "degraded",
            "feed_lag": True,
            "note": "unverified-sequence",
            "engine": {"name": "xterm", "pid": 999},  # 非字符串：只允许公共标识
            "cursors_valid": None,
            "reset_unconfirmed": "true",  # 字符串不是 bool → 必须 None
            "applied_evicted": True,
            "continuation_hint": "fresh-view-required",
            "diagnostics": {
                # 已知公共事实
                "engine_dead": False, "reset_count": 2, "applied_cursor": "12",
                "gap_ranges": [[1, 2]], "reasons": ["unverified-sequence"], "counters": {"feed": 3},
                # 未知键 / 自由文本 / 路径：必须丢弃
                "engine_error": "boom at C:/Users/secret/.secret",
                "node_binary": "C:/node/node.exe",
                "token": "SECRET-TOKEN-XYZ",
                "unknown_key": {"deep": 1},
            },
        }
        runtime = make_runtime(service)
        app = await start_app(runtime)
        await wait_ready(runtime)
        async with make_client(app, origin=ORIGIN) as client:
            result = (await client.post("/api/terminals/term_1/snapshot", json={"timeout_ms": 800})).json()["result"]
        assert base64.b64decode(result["data_b64"]).decode("utf-8") == "prompt> 汉字"
        assert result["cursor"] == "12"
        assert result["cursors_valid"] is None
        assert result["reset_unconfirmed"] is None
        assert result["fidelity"] == "partial" and result["recovery"] == "degraded"
        assert result["applied_evicted"] is True
        assert result["continuation_hint"] == "fresh-view-required"
        assert result["auto_reset_applied"] is False
        assert result["engine"] is None
        diag = result["diagnostics"]
        assert diag["engine_dead"] is False and diag["reset_count"] == 2
        assert diag["applied_cursor"] == "12" and diag["gap_ranges"] == [["1", "2"]]
        assert diag["reasons"] == ["unverified-sequence"]
        for leaked in ("engine_error", "node_binary", "token", "unknown_key"):
            assert leaked not in diag
        assert "SECRET-TOKEN-XYZ" not in json.dumps(result)
        assert "node.exe" not in json.dumps(result)
        # serialized_screen 为 None → data_b64 null；engine 字符串透传
        service.results["snapshot"].update({"serialized_screen": None, "engine": "xterm-headless+serialize"})
        async with make_client(app, origin=ORIGIN) as client:
            result = (await client.post("/api/terminals/term_1/snapshot", json={})).json()["result"]
        assert result["data_b64"] is None
        assert result["engine"] == "xterm-headless+serialize"

    asyncio.run(scenario())


def test_snapshot_too_large_is_static_and_not_truncated():
    async def scenario() -> None:
        service = FakeService()
        runtime = make_runtime(service)
        app = await start_app(runtime)
        await wait_ready(runtime)
        async with make_client(app, origin=ORIGIN) as client:
            service.results["snapshot"] = {"terminal_id": "term_1", "status": "running",
                                           "serialized_screen": "a" * (128 * 1024)}
            assert (await client.post("/api/terminals/term_1/snapshot", json={})).status_code == 200
            service.results["snapshot"] = {"terminal_id": "term_1", "status": "running",
                                           "serialized_screen": "a" * (128 * 1024 + 1)}
            response = await client.post("/api/terminals/term_1/snapshot", json={})
            assert response.status_code == 502
            assert response.json() == {"ok": False, "error": {"code": "snapshot-too-large"}}

    asyncio.run(scenario())


# ══════════════════════════════════════════════════════════════════════════
# ④ 静态错误映射与零回显
# ══════════════════════════════════════════════════════════════════════════


def test_service_error_mapping_is_static():
    async def scenario() -> None:
        cases: list[tuple[BaseException, int, str, str]] = [
            (UnknownTerminalError("term_x"), 404, "unknown-terminal", "get"),
            (CapacityExceeded("capacity-exceeded"), 429, "capacity-exceeded", "create"),
            (TerminalNotAttached("not-attached"), 409, "not-attached", "read"),
            (ServiceClosingDown("service-closing-down"), 503, "closing", "create"),
            (StartupFailed("startup-failed"), 502, "startup-failed", "create"),
            (StartupFailed("invalid-size"), 422, "invalid-size", "create"),
            (CleanupUnconfirmed("runner-stop-unconfirmed"), 409, "cleanup-unconfirmed", "close"),
            (DetachRefused("detach-refused"), 409, "detach-refused", "detach"),
        ]
        for error, status, code, method in cases:
            service = FakeService()
            service.errors[method] = error
            runtime = make_runtime(service)
            app = await start_app(runtime)
            await wait_ready(runtime)
            async with make_client(app, origin=ORIGIN) as client:
                if method == "get":
                    response = await client.get("/api/terminals/term_x")
                elif method == "read":
                    response = await client.get("/api/terminals/term_x/read")
                elif method == "close":
                    response = await client.post("/api/terminals/term_x/close", json={})
                elif method == "detach":
                    response = await client.post("/api/terminals/term_x/detach", json={})
                else:
                    response = await client.post("/api/terminals", json={})
            assert response.status_code == status, (error, response.status_code)
            assert response.json() == {"ok": False, "error": {"code": code}}

    asyncio.run(scenario())


def test_service_exception_text_never_echoed():
    """未知异常 → 500 internal-error；异常文本/路径/pipe 不出现。"""

    async def scenario() -> None:
        service = FakeService()
        service.errors["list"] = RuntimeError(
            r"SECRET-TOKEN-XYZ at C:\Users\me\AppData\.secret \\.\pipe\pan-terminal-1"
        )
        runtime = make_runtime(service)
        app = await start_app(runtime)
        await wait_ready(runtime)
        async with make_client(app, origin=ORIGIN) as client:
            response = await client.get("/api/terminals")
        assert response.status_code == 500
        assert response.json() == {"ok": False, "error": {"code": "internal-error"}}
        for leaked in ("SECRET-TOKEN-XYZ", "pan-terminal-", ".secret", "Users", "RuntimeError"):
            assert leaked not in response.text

    asyncio.run(scenario())


def test_close_unconfirmed_is_409_without_fake_exit_and_retry_reachable():
    async def scenario() -> None:
        service = FakeService()
        service.errors["close"] = CleanupUnconfirmed("runner-stop-unconfirmed")
        runtime = make_runtime(service)
        app = await start_app(runtime)
        await wait_ready(runtime)
        async with make_client(app, origin=ORIGIN) as client:
            response = await client.post("/api/terminals/term_1/close", json={})
            assert response.status_code == 409
            assert response.json() == {"ok": False, "error": {"code": "cleanup-unconfirmed"}}
            assert "exited" not in response.text
            # 重试可达：证据齐备后同一路由成功
            service.errors.pop("close")
            service.results["close"] = sample_view(status="exited", owner="service")
            retry = await client.post("/api/terminals/term_1/close", json={})
            assert retry.status_code == 200
            assert retry.json()["result"]["status"] == "exited"
        close_calls = [call for call in service.calls if call[0] == "close"]
        assert [call[2]["reason"] for call in close_calls] == ["explicit-close", "explicit-close"]

    asyncio.run(scenario())


def test_detach_refused_is_409_not_fake_durable():
    async def scenario() -> None:
        service = FakeService()
        service.results["detach"] = {"terminal_id": "term_1", "detached": False,
                                     "status": "detach-refused", "state_changed": False}
        runtime = make_runtime(service)
        app = await start_app(runtime)
        await wait_ready(runtime)
        async with make_client(app, origin=ORIGIN) as client:
            response = await client.post("/api/terminals/term_1/detach", json={})
            assert response.status_code == 409
            assert response.json() == {"ok": False, "error": {"code": "detach-refused"}}
            assert "mechanism" not in response.text
            service.results["detach"] = {"terminal_id": "term_1", "detached": True, "status": "detached",
                                         "state_changed": True, "mechanism": "runner-reported",
                                         "durability": {"capable": True}}
            ok_response = await client.post("/api/terminals/term_1/detach", json={})
            assert ok_response.status_code == 200
            assert ok_response.json()["result"]["detached"] is True

    asyncio.run(scenario())


# ══════════════════════════════════════════════════════════════════════════
# ⑤ 异步纪律（离开事件循环 / 4 槽有界 / 取消 / shutdown / 启动）
# ══════════════════════════════════════════════════════════════════════════


def test_slow_service_method_does_not_block_event_loop():
    async def scenario() -> None:
        service = FakeService()
        service.delays["list"] = 0.4
        runtime = make_runtime(service)
        app = await start_app(runtime)
        await wait_ready(runtime)
        ticks = 0

        async def ticker() -> None:
            nonlocal ticks
            while True:
                await asyncio.sleep(0.02)
                ticks += 1

        tick_task = asyncio.create_task(ticker())
        async with make_client(app, origin=ORIGIN) as client:
            response = await client.get("/api/terminals")
        tick_task.cancel()
        try:
            await tick_task
        except asyncio.CancelledError:
            pass
        assert response.status_code == 200
        # 0.4s 慢方法期间事件循环仍在推进（远多于 1 次）
        assert ticks >= 8, ticks

    asyncio.run(scenario())


def test_four_slots_bounded_busy_rejected_and_released_on_completion():
    async def scenario() -> None:
        service = FakeService()
        gate = _Gate(target=4)
        service.gates["list"] = gate
        service.results["list"] = []
        runtime = make_runtime(service, max_inflight=4)
        app = await start_app(runtime)
        await wait_ready(runtime)

        async def one() -> int:
            async with make_client(app, origin=ORIGIN, timeout=30.0) as client:
                return (await client.get("/api/terminals")).status_code

        tasks = [asyncio.create_task(one()) for _ in range(4)]
        await asyncio.to_thread(gate.entered.wait, 10.0)
        assert gate.entered.is_set(), "4 个在途请求应全部进入 service"
        async with make_client(app, origin=ORIGIN) as client:
            busy = await client.get("/api/terminals")
        assert busy.status_code == 429
        assert busy.json() == {"ok": False, "error": {"code": "busy"}}
        assert runtime.inflight == 4
        gate.release.set()
        statuses = await asyncio.gather(*tasks)
        assert statuses == [200, 200, 200, 200]
        assert runtime.inflight == 0
        # 释放后可继续
        async with make_client(app, origin=ORIGIN) as client:
            assert (await client.get("/api/terminals")).status_code == 200

    asyncio.run(scenario())


def test_cancelled_request_does_not_release_slot_before_thread_finishes():
    async def scenario() -> None:
        service = FakeService()
        gate = _Gate(target=4)
        service.gates["list"] = gate
        runtime = make_runtime(service, max_inflight=4)
        app = await start_app(runtime)
        await wait_ready(runtime)
        tasks = [asyncio.create_task(runtime.call(lambda: service.list())) for _ in range(4)]
        await asyncio.to_thread(gate.entered.wait, 10.0)
        assert runtime.inflight == 4
        tasks[0].cancel()
        await asyncio.sleep(0.1)
        # 取消不提前释放槽位（底层线程仍在跑）
        assert runtime.inflight == 4
        with pytest.raises(terminal_api.Busy):
            await runtime.call(lambda: "x")
        gate.release.set()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        assert isinstance(results[0], asyncio.CancelledError)
        deadline = time.monotonic() + 5.0
        while runtime.inflight and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        assert runtime.inflight == 0
        assert await runtime.call(lambda: "ok") == "ok"

    asyncio.run(scenario())


def test_reconcile_failure_not_ready_and_service_reference_kept():
    async def scenario() -> None:
        service = FakeService()
        service.reconcile_error = RuntimeError("boom C:/secret")
        runtime = make_runtime(service)
        app = await start_app(runtime)
        deadline = time.monotonic() + 5.0
        while runtime.state != terminal_api.STATE_FAILED and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        assert runtime.state == terminal_api.STATE_FAILED
        assert runtime.failure is not None and runtime.failure["error_type"] == "RuntimeError"
        assert runtime.service is service
        async with make_client(app, origin=ORIGIN) as client:
            response = await client.get("/api/terminals")
        assert response.status_code == 503
        assert response.json() == {"ok": False, "error": {"code": "terminal-unavailable"}}
        assert "boom" not in response.text and "secret" not in response.text

    asyncio.run(scenario())


def test_starting_state_503_until_reconcile_completes():
    async def scenario() -> None:
        service = FakeService()
        gate = _Gate(target=1)
        service.reconcile_gate = gate
        runtime = make_runtime(service)
        app = await start_app(runtime)
        assert runtime.state == terminal_api.STATE_STARTING
        async with make_client(app, origin=ORIGIN) as client:
            response = await client.get("/api/terminals")
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "not-ready"
        gate.release.set()
        await wait_ready(runtime)
        async with make_client(app, origin=ORIGIN) as client:
            assert (await client.get("/api/terminals")).status_code == 200

    asyncio.run(scenario())


def test_shutdown_timeout_keeps_references_and_consumes_late_result():
    async def scenario() -> None:
        service = FakeService()
        service.shutdown_delay = 1.0
        runtime = make_runtime(service, shutdown_budget=0.2)
        app = await start_app(runtime)
        await wait_ready(runtime)
        report = await terminal_api.stop_runtime(app, runtime=runtime)
        assert report["status"] == "unconfirmed"
        assert report["unconfirmed"] is True
        assert report["retained_service"] is True
        assert runtime.service is service
        # 超时后 REST 准入已关
        async with make_client(app, origin=ORIGIN) as client:
            response = await client.get("/api/terminals")
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "closing"
        # 迟到结果被消费（任务最终退役、异常不裸泄漏）
        deadline = time.monotonic() + 5.0
        while runtime.tracked_tasks and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        assert runtime.tracked_tasks == 0
        shutdown_calls = [call for call in service.calls if call[0] == "shutdown"]
        assert len(shutdown_calls) == 1
        assert shutdown_calls[0][2]["budget"] == pytest.approx(0.2)

    asyncio.run(scenario())


def test_shutdown_error_reported_not_faked_success():
    async def scenario() -> None:
        service = FakeService()
        service.shutdown_error = RuntimeError("shutdown SECRET-TOKEN-XYZ")
        runtime = make_runtime(service, shutdown_budget=1.0)
        app = await start_app(runtime)
        await wait_ready(runtime)
        report = await terminal_api.stop_runtime(app, runtime=runtime)
        assert report["status"] == "failed"
        assert report["unconfirmed"] is True
        assert report["error_type"] == "RuntimeError"
        assert "SECRET-TOKEN-XYZ" not in json.dumps(report)
        assert runtime.service is service

    asyncio.run(scenario())


def test_import_has_no_side_effects_and_disabled_constructs_nothing(tmp_path):
    """导入不建目录/起线程；禁用 runtime 不构造 service（全新解释器验证导入纯净）。"""

    import subprocess

    probe_root = tmp_path / "probe-root"
    script = (
        "import os, threading\n"
        "before = threading.active_count()\n"
        "import packages.web.terminal_api as api\n"
        "assert threading.active_count() == before, 'import created threads'\n"
        "assert not os.path.exists(os.environ['PAN_TERMINALS_DIR']), 'import created dirs'\n"
        "print('clean')\n"
    )
    env = dict(os.environ)
    env["PAN_TERMINALS_DIR"] = str(probe_root)
    env["PYTHONPATH"] = str(REPO_ROOT)
    proc = subprocess.run(
        [sys.executable, "-c", script], cwd=str(REPO_ROOT), env=env,
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert "clean" in proc.stdout


# ══════════════════════════════════════════════════════════════════════════
# ⑥ 接线层：真实 server app 已注册终端路由（不启动完整 lifespan）
# ══════════════════════════════════════════════════════════════════════════


def test_real_pan_app_registers_terminal_routes_without_full_lifespan():
    """真实 ``packages.web.server.app`` 已注册路由（未启动 lifespan → 503，而非 404）。"""

    async def scenario() -> None:
        import packages.web.server as server

        # 路由注册走 include_router（本 FastAPI 版本为惰性包含），以真实请求验证可达。
        assert getattr(server, "terminal_api", None) is terminal_api
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app), base_url=f"http://{AUTHORITY}",
            headers={"origin": ORIGIN}, timeout=httpx.Timeout(30.0),
        ) as client:
            response = await client.get("/api/terminals")
            assert response.status_code != 404, "终端路由未注册到 Pan app"
            assert response.status_code == 503
            assert response.json() == {"ok": False, "error": {"code": "not-ready"}}
            # 入口 gate 对无 Origin 仍拒绝（fail-closed）
            bare = await client.get("/api/terminals", headers={"origin": ""})
            assert bare.status_code in (403, 503)

    asyncio.run(scenario())


# ══════════════════════════════════════════════════════════════════════════
# ⑦ 真实隔离 ASGI 链（Windows + sidecar）
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


@requires_real
@requires_sidecar
def test_real_isolated_asgi_chain_create_read_snapshot_close(tmp_path):
    """真实隔离 ASGI 链：create → read/snapshot → close，同 handle 身份清理零残留。"""

    async def scenario() -> None:
        root = tmp_path / "rest-terminals"
        service = TerminalService(str(root), log_stderr=False)
        runtime = terminal_api.build_runtime(
            env={}, platform=sys.platform, host="127.0.0.1", port=8768,
            service_factory=lambda: service, shutdown_budget=20.0,
        )
        app = await start_app(runtime)
        await wait_ready(runtime, timeout=30.0)

        terminal_id: str | None = None
        client = make_client(app, origin=ORIGIN, timeout=120.0)
        try:
            async with client:
                # 负控：错 Origin 被拒（真实 app 的 gate 生效）
                async with make_client(app, origin="http://evil.local:8768", timeout=30.0) as bad:
                    assert (await bad.get("/api/terminals")).status_code == 403

                created = await client.post("/api/terminals", json={"rows": 24, "cols": 80})
                assert created.status_code == 200, created.text
                envelope = created.json()
                assert envelope["ok"] is True
                result = envelope["result"]
                terminal_id = result["terminal_id"]
                assert result["status"] == "running"
                assert result["pid"], "记录应带 hello 自证的 runner pid"
                # 秘密材料不外泄
                assert "pipe" not in result and "pan-terminal-" not in created.text

                # read：默认 shell 初始提示作为真实输出（不新增旁路伪造生产能力）
                deadline = time.monotonic() + 40.0
                data = b""
                cursor = 0
                while time.monotonic() < deadline and not data:
                    page = await client.get(f"/api/terminals/{terminal_id}/read?cursor={cursor}")
                    assert page.status_code == 200, page.text
                    page_result = page.json()["result"]
                    data = base64.b64decode(page_result["data_b64"])
                    cursor = int(page_result["next_cursor"])
                    if not data:
                        await asyncio.sleep(0.2)
                assert data, "真实 shell 初始提示应可经 REST read 取回"

                # snapshot：协议 A 投影
                snap_deadline = time.monotonic() + 30.0
                snapshot = None
                while time.monotonic() < snap_deadline:
                    snap_response = await client.post(
                        f"/api/terminals/{terminal_id}/snapshot", json={"timeout_ms": 5000}
                    )
                    assert snap_response.status_code == 200, snap_response.text
                    snapshot = snap_response.json()["result"]
                    if snapshot["data_b64"]:
                        break
                    await asyncio.sleep(0.3)
                assert snapshot is not None
                assert snapshot["status"] in ("running", "degraded", "starting", "exiting", "unavailable")
                if snapshot["data_b64"]:
                    decoded = base64.b64decode(snapshot["data_b64"]).decode("utf-8", errors="replace")
                    assert isinstance(decoded, str)
                for field in ("cursors_valid", "reset_unconfirmed", "applied_evicted"):
                    assert snapshot[field] is None or isinstance(snapshot[field], bool)
                assert snapshot["continuation_hint"] in (
                    "continue-stream", "partial-view", "fresh-view-required", "retry-after-start"
                )

                # close：三项证明齐备才 exited（未确证时有界重试；不伪报成功）
                close_deadline = time.monotonic() + 60.0
                closed = None
                while time.monotonic() < close_deadline:
                    response = await client.post(f"/api/terminals/{terminal_id}/close", json={})
                    if response.status_code == 200:
                        closed = response.json()["result"]
                        break
                    assert response.status_code == 409, response.text
                    assert response.json()["error"]["code"] == "cleanup-unconfirmed"
                    await asyncio.sleep(0.5)
                assert closed is not None, "close 应在预算内返回确定结果"
                assert closed["status"] == "exited"
                assert service._store().exists(terminal_id) is False, "已证明终止后应删秘密"
                # 同 handle 身份核验：进程确实已不在
                record = service.registry.get(terminal_id)
                assert not _process_alive_same_identity(int(record.pid or 0), record.process_created_at_filetime)
        finally:
            if terminal_id:
                _cleanup_owned_terminal(service, terminal_id)
            report = await terminal_api.stop_runtime(app, runtime=runtime)
            assert report.get("status") in ("confirmed", "no-service", "unconfirmed")

    asyncio.run(scenario())
