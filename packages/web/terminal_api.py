"""Pan Terminal REST 入口（P2 REST/lifespan 第一批）。

契约来源：``docs/design/PAN_TERMINAL_REST_INTERFACES_20261004.md``（本批成稿）
与 ``docs/design/PAN_TERMINAL_REST_BRIEF_20261004.md``（MA 已决任务书）。
下游实现依据：``packages/core/terminal/service.py``（已接受同步控制层）。

职责边界（本模块只做三件事）：

1. **入口安全 gate**（先于服务调用/实例创建）：Origin 精确 allowlist、
   Host 与允许 origin 的 authority 匹配、忽略 ``X-Forwarded-*``、
   ``Sec-Fetch-Site`` 只接受 ``same-origin`` / ``same-site``、POST 必须
   ``application/json``；非 loopback 绑定默认禁用（``PAN_TERMINAL_ALLOW_REMOTE=1``
   才解除），非 Windows 默认禁用。
2. **参数校验 + 出口字段投影**：拒绝额外字段、真整数边界、cursor/uint64 精确
   校验；响应只用白名单字段（不外泄未知键、token、pipe、路径、秘密）。
3. **生命周期/异步纪律**：所有同步 ``TerminalService`` 方法离开事件循环
   （``asyncio.to_thread``）；每实例最多 4 个在途请求（满则 429 ``busy``，不排队）；
   被拒请求零服务调用；HTTP 取消不取消底层线程、槽位直到真实完成才释放；
   ``shutdown`` 先关 REST 准入、再用 ``shield`` + 总预算收敛，超时如实报告
   ``unconfirmed`` 并保留 service/tasks 引用。

**禁止 import ``packages.web.server``**（避免循环依赖）：实际 server 只做
``app.include_router(terminal_api.router)`` 与 lifespan 起停薄接入。
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import os
import re
import sys
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from packages.core.terminal.contracts import UnknownTerminalError
from packages.core.terminal.service import (
    CapacityExceeded,
    CleanupUnconfirmed,
    DetachRefused,
    ServiceClosingDown,
    ServiceContext,
    StartupFailed,
    TerminalNotAttached,
    TerminalService,
)

router = APIRouter(prefix="/api/terminals", tags=["terminal"])

# ══════════════════════════════════════════════════════════════════════════
# 常量（已决边界）
# ══════════════════════════════════════════════════════════════════════════

DEFAULT_PAN_PORT = 8768
DEFAULT_PAN_HOST = "127.0.0.1"

#: 每实例在途请求上限（不设无界等待队列；满则 429 busy）。
DEFAULT_MAX_INFLIGHT = 4
#: shutdown 总预算（含取锁与全部等待）；与 service 默认同值。
DEFAULT_SHUTDOWN_BUDGET_SECONDS = 20.0

# 尺寸边界与 ``TerminalService.create`` 已接受的门**一致**（rows ≤ 500 / cols ≤ 1000）。
# r2 修正：任务书原写 rows 1..1000，与已接受核心不一致；此处统一到核心口径，
# 不维持 501..1000 的"假可用"（不改核心）。
ROWS_MIN = 1
ROWS_MAX = 500
ROWS_DEFAULT = 24
COLS_MIN = 1
COLS_MAX = 1000
COLS_DEFAULT = 80
STRING_MAX = 4096
MAX_BYTES_MIN = 1
MAX_BYTES_MAX = 131072
MAX_BYTES_DEFAULT = 65536
TIMEOUT_MIN = 1
TIMEOUT_MAX = 5000
TIMEOUT_DEFAULT = 5000
SNAPSHOT_SCREEN_MAX = 128 * 1024
UINT64_MAX = (1 << 64) - 1
MAX_JSON_BODY = 64 * 1024

#: 固定调用上下文：来自**通过入口检查的本地信任面**。
#: ``trusted_local=True`` 只表示"同用户本地受控网页"这一接线前提，
#: **不**伪称登录认证，也**不**代表 workspace 授权（scope 仅元数据）。
WEB_LOCAL_CONTEXT = ServiceContext(created_by="web-local", trusted_local=True)

# ══════════════════════════════════════════════════════════════════════════
# 错误分类（静态、可枚举；不含自由文本/路径/秘密/pipe）
# ══════════════════════════════════════════════════════════════════════════


def ok(result: Any) -> JSONResponse:
    """成功 envelope：``{ok:true,result:...}``。"""
    return JSONResponse({"ok": True, "result": result})


def fail(status_code: int, code: str) -> JSONResponse:
    """失败 envelope：``{ok:false,error:{code:<静态分类>}}``（无异常文本）。"""
    return JSONResponse({"ok": False, "error": {"code": str(code)}}, status_code=int(status_code))


class GateRejected(Exception):
    """入口 gate 拒绝（静态分类 + HTTP 状态）。抛出时**尚未**调用 service。"""

    def __init__(self, status_code: int, code: str) -> None:
        self.status_code = int(status_code)
        self.code = str(code)
        super().__init__(self.code)


class Busy(Exception):
    """在途请求已满（4 槽）；不排队、不等待。"""


class SnapshotTooLarge(Exception):
    """serialized_screen 超 128KiB：静态失败，**不**截断伪造状态。"""


def service_error(exc: BaseException) -> tuple[int, str]:
    """service 异常 → (HTTP 状态, 静态 code) 映射。

    未知异常一律 ``500 internal-error``（不回显任何异常文本）。
    """
    if isinstance(exc, UnknownTerminalError):
        return 404, "unknown-terminal"
    if isinstance(exc, CapacityExceeded):
        return 429, "capacity-exceeded"
    if isinstance(exc, ServiceClosingDown):
        return 503, "closing"
    if isinstance(exc, TerminalNotAttached):
        return 409, "not-attached"
    if isinstance(exc, DetachRefused):
        return 409, "detach-refused"
    if isinstance(exc, CleanupUnconfirmed):
        return 409, "cleanup-unconfirmed"
    if isinstance(exc, StartupFailed):
        # service 侧 ``invalid-size`` 是输入分类而非真实启动失败：如实归 422。
        if str(getattr(exc, "reason", "")) == "invalid-size":
            return 422, "invalid-size"
        return 502, "startup-failed"
    return 500, "internal-error"


# ══════════════════════════════════════════════════════════════════════════
# 严格校验（真整数、字符串、cursor/uint64；不 echo 原始输入）
# ══════════════════════════════════════════════════════════════════════════


def _is_real_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _valid_decimal(text: Any) -> bool:
    """ASCII 非负十进制字符串（无 ``+``/``-``/空白/浮点/十六进制/科学计数）。"""
    if not isinstance(text, str) or not text or not text.isascii():
        return False
    return all("0" <= ch <= "9" for ch in text)


def _canonical_digits(text: str) -> str | None:
    """去掉前导零后的 ASCII 十进制串（全零值规约为 ``"0"``）。"""
    stripped = text.lstrip("0")
    return stripped if stripped else "0"


def _parse_uint64(text: str) -> int | None:
    if not _valid_decimal(text):
        return None
    digits = _canonical_digits(text)
    if digits is None or len(digits) > 20:
        return None
    value = int(digits, 10)
    return value if value <= UINT64_MAX else None


def _parse_bounded_int_str(text: str, lo: int, hi: int) -> int | None:
    if not _valid_decimal(text):
        return None
    digits = _canonical_digits(text)
    if digits is None or len(digits) > 10:
        return None
    value = int(digits, 10)
    return value if lo <= value <= hi else None


def _bounded_field(
    obj: Mapping[str, Any], key: str, default: int, lo: int, hi: int
) -> tuple[int | None, str | None]:
    if key not in obj:
        return default, None
    value = obj[key]
    if not _is_real_int(value) or not (lo <= value <= hi):
        return None, "invalid-field"
    return int(value), None


def _optional_string_field(
    obj: Mapping[str, Any], key: str, *, max_len: int = STRING_MAX
) -> tuple[str | None, str | None]:
    """可选字符串（``null``/缺失 → ``None``）；超长或非字符串 → 静态错误。"""
    if key not in obj:
        return None, None
    value = obj[key]
    if value is None:
        return None, None
    if not isinstance(value, str) or len(value) > max_len:
        return None, "invalid-field"
    return value, None


#: 报告字段缺证哨兵（区分"缺字段"与"值为 None"）。
_MISSING = object()

#: 公共终端 id 形态（用于从服务报告里只提取**公共标识**，不复制自由文本）。
_PUBLIC_TERMINAL_ID_RE = re.compile(r"^term_[A-Za-z0-9_-]{1,64}$")


def _public_terminal_id(value: Any) -> str | None:
    """仅当形如公共 ``terminal_id`` 时返回，否则 ``None``（不透传自由文本）。"""
    return value if isinstance(value, str) and _PUBLIC_TERMINAL_ID_RE.match(value) else None


def validate_terminal_id(terminal_id: Any) -> str:
    """调用 service 之前完成 id 校验（权威校验器；非 Windows 走同形回退）。"""
    try:
        from packages.core.terminal.secret_store import SecretStore

        return SecretStore.validate_terminal_id(terminal_id)
    except ImportError:  # pragma: no cover - 非 Windows 开发回退（同正则）
        import re

        if not isinstance(terminal_id, str) or not re.match(r"^term_[A-Za-z0-9_-]{1,64}$", terminal_id):
            raise ValueError("invalid terminal id")
        return terminal_id


# ══════════════════════════════════════════════════════════════════════════
# Origin / Host / Fetch / content-type（入口安全；纯函数，可单测）
# ══════════════════════════════════════════════════════════════════════════


def _normalize_authority(netloc: Any) -> str | None:
    """authority → ``host[:port]``；畸形（含 ``ValueError``）一律 ``None``。"""
    if not isinstance(netloc, str):
        return None
    raw = netloc.strip().lower()
    if not raw or any(ch in raw for ch in "@/?#"):
        return None
    try:
        parts = urlsplit("//" + raw)
        host = parts.hostname
        port = parts.port
    except ValueError:
        # r2：``urlsplit``/``hostname``/``port`` 对畸形 authority（如 ``[broken``）抛
        # ``ValueError``；必须捕获归 ``None``，不得冒泡成未捕获 500。
        return None
    if not host:
        return None
    return f"{host}:{port}" if port is not None else host


def normalize_origin(value: Any) -> str | None:
    """规范化 origin（scheme + authority）；非法一律 ``None``（fail-closed）。

    只接受合法 http/https origin：**无 path**（含尾 ``/`` 一律拒绝）、无 query/fragment/
    userinfo/wildcard/null。畸形 URL/authority（``urlsplit`` 抛 ``ValueError``）→ ``None``。
    """
    if not isinstance(value, str):
        return None
    if value != value.strip():
        return None
    if not value or value.lower() == "null":
        return None
    if "*" in value:
        return None
    try:
        parts = urlsplit(value)
        host = parts.hostname
        port = parts.port  # 畸形端口（如 ``:abc``）在此抛 ValueError
    except ValueError:
        # r2：``http://[broken`` 之类畸形 origin 必须静态归 ``None``（→ 请求 403 /
        # 配置 fail-closed），而不是未捕获异常。
        return None
    if parts.scheme not in ("http", "https"):
        return None
    if not parts.netloc or not host:
        return None
    if parts.path:
        # 明确无 path：``"/"`` 也算 path（尾斜杠拒绝），匹配 brief。
        return None
    if parts.query or parts.fragment:
        return None
    if parts.username is not None or parts.password is not None:
        return None
    authority = _normalize_authority(parts.netloc)
    if authority is None:
        return None
    return f"{parts.scheme}://{authority}"


def _media_type(content_type: str | None) -> str:
    if not isinstance(content_type, str):
        return ""
    return content_type.split(";", 1)[0].strip().lower()


def resolve_port(env: Mapping[str, str]) -> int:
    raw = env.get("PAN_PORT")
    if raw:
        try:
            return int(str(raw).strip())
        except (TypeError, ValueError):
            pass
    return DEFAULT_PAN_PORT


def is_loopback_host(host: Any) -> bool:
    text = str(host or "").strip()
    if not text:
        return False
    if text.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(text).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class Allowlist:
    """Origin allowlist（精确匹配）与其 authority 集合。"""

    origins: frozenset[str]
    authorities: frozenset[str]
    config_valid: bool


def _allowlist_from(origins: set[str], *, config_valid: bool) -> Allowlist:
    authorities = set()
    for origin in origins:
        authority = _normalize_authority(urlsplit(origin).netloc)
        if authority:
            authorities.add(authority)
    return Allowlist(origins=frozenset(origins), authorities=frozenset(authorities), config_valid=config_valid)


def build_allowlist(env: Mapping[str, str], *, port: int) -> Allowlist:
    """解析 allowlist；配置非法即 **fail-closed**（空集合，不静默回退放行）。"""
    raw = env.get("PAN_TERMINAL_ALLOWED_ORIGINS")
    if raw is None or not str(raw).strip():
        return _allowlist_from(
            {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}, config_valid=True
        )
    entries: set[str] = set()
    for piece in str(raw).split(","):
        piece = piece.strip()
        if not piece:
            continue
        normalized = normalize_origin(piece)
        if normalized is None:
            # 任一条目非法 → 整份配置非法（不放行任何 origin）。
            return _allowlist_from(set(), config_valid=False)
        entries.add(normalized)
    if not entries:
        return _allowlist_from(set(), config_valid=False)
    return _allowlist_from(entries, config_valid=True)


# ══════════════════════════════════════════════════════════════════════════
# 有界在途槽位（每实例 4；不排队）
# ══════════════════════════════════════════════════════════════════════════


class _SlotLedger:
    """**请求槽**台账（满则拒绝，绝不扩容排队）。

    r2：按 **token 身份**记账——同一 token 重复 ``release`` 是 no-op（不偷减别的槽），
    且只有持有请求槽的任务才能释放（生命周期任务走别的路径，绝不扣请求槽）。
    """

    def __init__(self, limit: int) -> None:
        self._limit = max(1, int(limit))
        self._holders: set[object] = set()
        self._lock = threading.Lock()

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._holders)

    def acquire(self, token: object) -> bool:
        with self._lock:
            if len(self._holders) >= self._limit:
                return False
            self._holders.add(token)
            return True

    def release(self, token: object) -> bool:
        """仅当该 token 仍持有槽时释放一次；否则 no-op（幂等，不误减）。"""
        with self._lock:
            if token not in self._holders:
                return False
            self._holders.discard(token)
            return True


# ══════════════════════════════════════════════════════════════════════════
# Runtime（可独立测试；依赖工厂可注入）
# ══════════════════════════════════════════════════════════════════════════

STATE_DISABLED = "disabled"
STATE_STARTING = "starting"
STATE_READY = "ready"
STATE_FAILED = "failed"
STATE_CLOSING = "closing"
STATE_STOPPED = "stopped"


class TerminalRuntime:
    """Terminal REST 的运行期（每 app 一个；一个 TerminalService）。

    - ``service_factory`` 由真实 server（默认 ``TerminalService()``，尊重
      ``PAN_TERMINALS_DIR`` 既有默认）或测试注入；
    - 非 Windows / 非 loopback 绑定禁用时 **不构造** service（零 Windows 资源），
      routes 一律 503；
    - ``startup`` 只构造 service 并安排**一次** reconcile；启动中 503，reconcile
      成功才 ``ready``，失败保留 service 引用 + 静态失败状态（不假 ready）。
    """

    def __init__(
        self,
        *,
        enabled: bool,
        disabled_reason: str,
        allowlist: Allowlist,
        service_factory: Callable[[], Any] | None = None,
        max_inflight: int = DEFAULT_MAX_INFLIGHT,
        shutdown_budget: float = DEFAULT_SHUTDOWN_BUDGET_SECONDS,
    ) -> None:
        self.enabled = bool(enabled)
        self.disabled_reason = str(disabled_reason)
        self.allowlist = allowlist
        self._service_factory = service_factory or (lambda: TerminalService())
        self._ledger = _SlotLedger(max_inflight)
        self.shutdown_budget = max(0.0, float(shutdown_budget))

        self.service: Any | None = None
        self.state = STATE_STARTING if self.enabled else STATE_DISABLED
        self.failure: dict[str, Any] | None = None
        self.reconcile_report: dict[str, Any] | None = None

        self._tasks: set[asyncio.Task] = set()
        #: r2：请求任务 → 槽 token；只有这里的任务才允许扣请求槽。
        self._slot_token: dict[asyncio.Task, object] = {}
        self._tasks_lock = threading.Lock()
        self._reconcile_task: asyncio.Task | None = None
        self._shutdown_task: asyncio.Task | None = None
        self._shutdown_report: dict[str, Any] | None = None

    # ------------------------------------------------------------ 观测
    @property
    def inflight(self) -> int:
        """在途**请求**槽数（生命周期任务不占槽）。"""
        return self._ledger.count

    @property
    def tracked_tasks(self) -> int:
        with self._tasks_lock:
            return len(self._tasks)

    # ------------------------------------------------------------ gate
    def check_gate(self, request: Request) -> None:
        """入口 + 就绪检查；拒绝时尚未调用 service（零副作用）。"""
        if not self.allowlist.config_valid:
            raise GateRejected(403, "forbidden-origin")
        origin_raw = request.headers.get("origin")
        if origin_raw is None:
            raise GateRejected(403, "forbidden-origin")
        normalized = normalize_origin(origin_raw)
        if normalized is None or normalized not in self.allowlist.origins:
            raise GateRejected(403, "forbidden-origin")
        authority = _normalize_authority(urlsplit(normalized).netloc)
        host_raw = request.headers.get("host")
        if authority is None or _normalize_authority(host_raw) != authority:
            # Host 必须与允许 origin 的 authority 匹配；不读 X-Forwarded-*。
            raise GateRejected(403, "forbidden-host")
        fetch_site = request.headers.get("sec-fetch-site")
        if fetch_site is not None and fetch_site.strip().lower() not in ("same-origin", "same-site"):
            raise GateRejected(403, "forbidden-fetch-site")
        if request.method == "POST" and _media_type(request.headers.get("content-type")) != "application/json":
            raise GateRejected(403, "forbidden-content-type")
        self._check_ready()

    def _check_ready(self) -> None:
        if self.state == STATE_DISABLED:
            raise GateRejected(503, "terminal-disabled")
        if self.state in (STATE_CLOSING, STATE_STOPPED):
            raise GateRejected(503, "closing")
        if self.state == STATE_FAILED:
            # 与 create 的 502 ``startup-failed`` 区分：这里是"运行期不可用"。
            raise GateRejected(503, "terminal-unavailable")
        if self.state != STATE_READY or self.service is None:
            raise GateRejected(503, "not-ready")

    # ------------------------------------------------------------ 有界执行
    async def call(self, fn: Callable[[], Any]) -> Any:
        """在事件循环外执行同步 service 方法（4 槽有界、可追踪、取消不误报）。

        - **取槽与就绪复查在同一"无 await"区**（r2）：即使调用方已通过入口 gate，
          只要此间 ``shutdown`` 已关门（``closing``/``stopped``）就会被拒
          （``GateRejected``），**零业务执行**；
        - 槽位在**真实完成**（线程结束）后才释放，按 token **恰好一次**；HTTP
          取消/断开不取消底层线程，也不提前归还槽位；
        - 满槽 → :class:`Busy`（不排队、不等待）；
        - 线程异常原样冒泡（由调用方映射为静态 code），并保证异常被消费。
        """
        token = object()  # 占位 token：任务创建前先占槽（与就绪复查同区，无 await）
        if not self._ledger.acquire(token):
            raise Busy("busy")
        try:
            self._check_ready()
        except GateRejected:
            self._ledger.release(token)
            raise
        task = asyncio.create_task(asyncio.to_thread(fn), name="pan-terminal-rest")
        with self._tasks_lock:
            self._tasks.add(task)
            self._slot_token[task] = token
        try:
            result = await asyncio.shield(task)
        except asyncio.CancelledError:
            # 调用方取消（客户端断开）：保留 task 与槽位引用，完成时统一回收。
            task.add_done_callback(self._retire_request)
            raise
        except BaseException:
            self._retire_request(task)
            raise
        # 成功路径同样必须回收（``else`` 在 ``return`` 时不会执行）。
        self._retire_request(task)
        return result

    def _consume(self, task: asyncio.Task) -> None:
        """移出追踪集并**消费**结果/异常（迟到结果不裸泄漏）。"""
        with self._tasks_lock:
            self._tasks.discard(task)
        try:
            task.exception()
        except BaseException:  # noqa: BLE001 - 迟到异常在此被消费，不裸泄漏
            pass

    def _retire_request(self, task: asyncio.Task) -> None:
        """**请求**任务终结：消费 + 按 token **恰好一次**释放请求槽。"""
        self._consume(task)
        with self._tasks_lock:
            token = self._slot_token.pop(task, None)
        if token is not None:
            self._ledger.release(token)

    def _retire_lifecycle(self, task: asyncio.Task) -> None:
        """**生命周期**任务（reconcile/shutdown）终结：只消费，**绝不**扣请求槽。"""
        self._consume(task)

    # ------------------------------------------------------------ 生命周期
    async def startup(self) -> None:
        """构造 service（不派生进程）并安排一次 reconcile。"""
        if not self.enabled:
            self.state = STATE_DISABLED
            return
        try:
            self.service = self._service_factory()
        except Exception as exc:  # noqa: BLE001 - 静态失败状态，不假 ready
            self.failure = {"reason": "service-construction-failed", "error_type": type(exc).__name__}
            self.state = STATE_FAILED
            return
        self.state = STATE_STARTING
        self._reconcile_task = asyncio.create_task(self._run_reconcile(), name="pan-terminal-reconcile")
        with self._tasks_lock:
            self._tasks.add(self._reconcile_task)
        self._reconcile_task.add_done_callback(self._retire_lifecycle)

    async def _run_reconcile(self) -> None:
        try:
            report = await asyncio.to_thread(self.service.reconcile)
        except asyncio.CancelledError:
            if self.state == STATE_STARTING:
                self.failure = {"reason": "reconcile-cancelled"}
                self.state = STATE_FAILED
            raise
        except Exception as exc:  # noqa: BLE001 - 保留 service 引用 + 静态失败状态
            if self.state == STATE_STARTING:
                self.failure = {"reason": "reconcile-failed", "error_type": type(exc).__name__}
                self.state = STATE_FAILED
            return
        if self.state == STATE_STARTING:
            self.reconcile_report = report if isinstance(report, dict) else None
            self.state = STATE_READY

    def _ensure_shutdown_task(self, service: Any, budget: float) -> asyncio.Task:
        """单飞：在途的 shutdown task **复用**（不叠加）；已完成则消费后幂等重试。

        返回的 task 在创建时即挂 ``_retire_lifecycle`` 完成回调，因此无论谁在等待，
        它的结果/异常都会被消费（不会"永远缓存失败"）。
        """
        task = self._shutdown_task
        if task is not None and not task.done():
            return task
        if task is not None:
            self._consume(task)  # 消费上一轮迟到结果；随后允许同一 service 幂等重试
            self._shutdown_task = None
        task = asyncio.create_task(
            asyncio.to_thread(service.shutdown, budget=budget), name="pan-terminal-shutdown"
        )
        with self._tasks_lock:
            self._tasks.add(task)
        task.add_done_callback(self._retire_lifecycle)
        self._shutdown_task = task
        return task

    def _classify_shutdown(
        self, report: Any, *, timed_out: bool, error_type: str | None
    ) -> dict[str, Any]:
        """把**真实服务报告**归一为静态分类（不复制自由文本）。

        ``confirmed`` 只有四条件**同时**成立才可报：
        ① 报告是 Mapping 且 ``unconfirmed`` 是**空列表**；② ``secrets_retained`` 为真
        ``False``；③ ``budget_exhausted`` 不为 ``True``；④ 本层无在途请求、无未完成
        reconcile。未知/畸形/缺证/有残留 → ``unconfirmed``（**调用完成 ≠ 清理证明**）。
        """
        problems: list[str] = []
        unconfirmed: list[str] = []
        secrets_retained: bool | None = None
        budget_exhausted: bool | None = None
        if timed_out:
            problems.append("budget-timeout")
        if error_type is not None:
            problems.append("shutdown-error")
        if not isinstance(report, Mapping):
            problems.append("service-report-missing")
        else:
            raw_unconfirmed = report.get("unconfirmed", _MISSING)
            if isinstance(raw_unconfirmed, list):
                unconfirmed = sorted({item for item in map(_public_terminal_id, raw_unconfirmed) if item})
                if raw_unconfirmed:
                    problems.append("service-unconfirmed")
            else:
                problems.append("unconfirmed-evidence-missing")
            raw_secrets = report.get("secrets_retained", _MISSING)
            if raw_secrets is False:
                secrets_retained = False
            elif isinstance(raw_secrets, bool):
                secrets_retained = True
                problems.append("secrets-retained")
            else:
                problems.append("secrets-evidence-missing")
            raw_budget = report.get("budget_exhausted", _MISSING)
            if raw_budget is True:
                budget_exhausted = True
                problems.append("service-budget-exhausted")
            elif raw_budget is False:
                budget_exhausted = False
            else:
                # 缺证：既非 True 也非 False（缺失/None/非 bool）→ 不确认。
                budget_exhausted = None
                problems.append("budget-evidence-missing")
        if self.inflight > 0:
            problems.append("request-in-flight")
        if self._reconcile_task is not None and not self._reconcile_task.done():
            problems.append("reconcile-in-flight")
        confirmed = not problems
        return {
            "status": "confirmed" if confirmed else "unconfirmed",
            "confirmed": bool(confirmed),
            # 未确认时的公共 id（service 报告的未收敛终端）；无法确认则空列表。
            "unconfirmed": unconfirmed,
            "unconfirmed_count": len(unconfirmed),
            "problems": sorted(set(problems)),
            # 不得假报 False：只有 confirmed（含 secrets_retained 真 False）才是 False。
            "secrets_retained": False if confirmed else True,
            "budget_exhausted": budget_exhausted,
        }

    async def shutdown(self) -> dict[str, Any]:
        """先关 REST 准入，再在事件循环外按总预算收敛同一 service（单飞）。

        - 并发/超时重试**复用同一尚在途 task**（不叠加）；已结束但未证明时允许对
          同一 service 幂等重试，迟到结果被真实消费；
        - 返回值来自 :meth:`_classify_shutdown`（消费真实服务报告），**不再**把
          "调用完成"当作清理证明。
        """
        self.state = STATE_CLOSING
        service = self.service
        if service is None:
            self.state = STATE_STOPPED
            self._shutdown_report = {
                "status": "unconfirmed", "confirmed": False, "unconfirmed": [],
                "unconfirmed_count": 0, "problems": ["no-service"],
                "secrets_retained": True, "budget_exhausted": None,
            }
            return dict(self._shutdown_report)
        budget = self.shutdown_budget
        task = self._ensure_shutdown_task(service, budget)
        try:
            report = await asyncio.wait_for(asyncio.shield(task), timeout=budget)
        except asyncio.TimeoutError:
            # 超时：保留 service 与 task 引用（下一次调用复用同一 task）；不报成功。
            self.state = STATE_STOPPED
            self._shutdown_report = self._classify_shutdown(None, timed_out=True, error_type=None)
            return dict(self._shutdown_report)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 如实报告失败，保留 service 引用
            self.state = STATE_STOPPED
            self._shutdown_task = None  # 已结束：允许同一 service 幂等重试
            self._shutdown_report = self._classify_shutdown(
                None, timed_out=False, error_type=type(exc).__name__
            )
            return dict(self._shutdown_report)
        self.state = STATE_STOPPED
        self._shutdown_task = None
        self._shutdown_report = self._classify_shutdown(report, timed_out=False, error_type=None)
        return dict(self._shutdown_report)


# ══════════════════════════════════════════════════════════════════════════
# 出口投影（白名单字段；不泛化复制任意 Mapping）
# ══════════════════════════════════════════════════════════════════════════

_VIEW_KEYS = (
    "terminal_id",
    "status",
    "owner",
    "rows",
    "cols",
    "pid",
    "process_created_at_filetime",
    "detached",
    "detached_at",
    "lease_grace_seconds",
    "created_by",
    "created_at",
    "updated_at",
    "attached",
    "authorization",
)
_DIAG_BOOL_KEYS = (
    "engine_dead",
    "reset_unconfirmed",
    "cursors_valid",
    "reason_overflow",
    "feed_lag",
    "closing",
    "closed",
)
_DIAG_INT_KEYS = (
    "reset_count",
    "queue_bytes",
    "queue_ops",
    "max_queue_bytes",
    "max_queue_ops",
    "sidecar_pid",
)
_DIAG_STR_KEYS = (
    "producer_frontier",
    "expected_next",
    "applied_cursor",
    "baseline_cursor",
    "sidecar_filetime",
)
_DIAG_RANGE_KEYS = ("gap_ranges", "duplicate_ranges", "rejected_ranges")
_DIAG_MAP_KEYS = ("overflow", "counters")
_DIAG_LIST_MAX = 64
_DIAG_TEXT_MAX = 64


def _str_or_none(value: Any, *, limit: int = 64) -> str | None:
    return value[:limit] if isinstance(value, str) else None


def _bool_or_none(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def project_view(raw: Mapping[str, Any]) -> dict[str, Any]:
    """list/get/create/close 的公共视图投影（白名单键）。"""
    out: dict[str, Any] = {}
    for key in _VIEW_KEYS:
        if key in raw:
            out[key] = raw[key]
    scope = raw.get("scope")
    if isinstance(scope, Mapping):
        out["scope"] = {
            "workspace_id": scope.get("workspace_id"),
            "session_id": scope.get("session_id"),
        }
    exit_info = raw.get("exit")
    if isinstance(exit_info, Mapping):
        out["exit"] = {"code": exit_info.get("code"), "reason": exit_info.get("reason")}
    heartbeat = raw.get("heartbeat")
    if isinstance(heartbeat, Mapping):
        out["heartbeat"] = {
            "client_id": heartbeat.get("client_id"),
            "beats": heartbeat.get("beats"),
            "lost": _bool_or_none(heartbeat.get("lost")),
        }
    return out


def _raw_bytes(raw: Any) -> bytes | None:
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw)
    if isinstance(raw, str):
        return raw.encode("utf-8")
    raise ValueError("unencodable")


def _b64(raw: Any) -> str | None:
    payload = _raw_bytes(raw)
    return None if payload is None else base64.b64encode(payload).decode("ascii")


def _dec(value: Any) -> str | None:
    return str(int(value)) if _is_real_int(value) and int(value) >= 0 else None


def project_read(raw: Mapping[str, Any]) -> dict[str, Any]:
    """read 出口：bytes → data_b64，游标类字段十进制字符串，size 保持小整数。"""
    gap = raw.get("gap")
    gap_out = None
    if isinstance(gap, (list, tuple)) and len(gap) == 2 and all(_is_real_int(item) for item in gap):
        gap_out = [str(int(gap[0])), str(int(gap[1]))]
    return {
        "terminal_id": raw.get("terminal_id"),
        "data_b64": _b64(raw.get("data")),
        "seq": _dec(raw.get("seq")),
        "size": int(raw.get("size") or 0),
        "next_cursor": _dec(raw.get("next_cursor")),
        "total_bytes": _dec(raw.get("total_bytes")),
        "first_retained_seq": _dec(raw.get("first_retained_seq")),
        "gap": gap_out,
        "truncated": bool(raw.get("truncated")),
        "fresh_view_required": bool(raw.get("fresh_view_required")),
        "cursor_advanced": bool(raw.get("cursor_advanced")),
        "zero_fill": False,
        "status": _str_or_none(raw.get("status")),
    }


def project_engine(value: Any) -> str | None:
    """engine 只透出**公共标识字符串**；不泛化复制任意 Mapping。"""
    return value[:64] if isinstance(value, str) else None


def project_diagnostics(value: Any) -> dict[str, Any] | None:
    """诊断白名单投影：只透出已知公共事实；未知键与非匹配类型一律丢弃。"""
    if not isinstance(value, Mapping):
        return None
    out: dict[str, Any] = {}
    for key in _DIAG_BOOL_KEYS:
        if isinstance(value.get(key), bool):
            out[key] = value[key]
    for key in _DIAG_INT_KEYS:
        item = value.get(key)
        if _is_real_int(item):
            out[key] = int(item)
    for key in _DIAG_STR_KEYS:
        item = value.get(key)
        if isinstance(item, str):
            out[key] = item[:64]
    for key in _DIAG_RANGE_KEYS:
        item = value.get(key)
        if isinstance(item, (list, tuple)):
            out[key] = [
                [str(int(pair[0])), str(int(pair[1]))]
                for pair in item[:_DIAG_LIST_MAX]
                if isinstance(pair, (list, tuple)) and len(pair) == 2 and all(_is_real_int(x) for x in pair)
            ]
    reasons = value.get("reasons")
    if isinstance(reasons, (list, tuple)):
        out["reasons"] = [
            reason[:_DIAG_TEXT_MAX] for reason in reasons[:_DIAG_LIST_MAX] if isinstance(reason, str)
        ]
    for key in _DIAG_MAP_KEYS:
        item = value.get(key)
        if isinstance(item, Mapping):
            out[key] = {
                str(k)[:64]: int(v)
                for k, v in list(item.items())[:32]
                if isinstance(k, str) and _is_real_int(v)
            }
    return out


def project_snapshot(raw: Mapping[str, Any]) -> dict[str, Any]:
    """snapshot 出口：serialized_screen → 原字节 data_b64；不因 reasons 空升级。"""
    return {
        "terminal_id": raw.get("terminal_id"),
        "status": _str_or_none(raw.get("status")),
        "data_b64": _b64(raw.get("serialized_screen")),
        "cursor": _dec(raw.get("cursor")),
        "rows": int(raw.get("rows") or 0),
        "cols": int(raw.get("cols") or 0),
        "fidelity": _str_or_none(raw.get("fidelity")),
        "recovery": _str_or_none(raw.get("recovery")),
        "feed_lag": _bool_or_none(raw.get("feed_lag")),
        "note": _str_or_none(raw.get("note")),
        "engine": project_engine(raw.get("engine")),
        "cursors_valid": _bool_or_none(raw.get("cursors_valid")),
        "reset_unconfirmed": _bool_or_none(raw.get("reset_unconfirmed")),
        "applied_evicted": _bool_or_none(raw.get("applied_evicted")),
        "continuation_hint": _str_or_none(raw.get("continuation_hint")),
        "auto_reset_applied": False,
        "diagnostics": project_diagnostics(raw.get("diagnostics")),
    }


def project_detach(raw: Mapping[str, Any]) -> dict[str, Any]:
    """detach 出口投影（仅公共事实）。"""
    return {
        "terminal_id": raw.get("terminal_id"),
        "detached": bool(raw.get("detached")),
        "status": _str_or_none(raw.get("status")),
        "state_changed": bool(raw.get("state_changed")),
        "mechanism": _str_or_none(raw.get("mechanism")),
        "durability": raw.get("durability") if isinstance(raw.get("durability"), Mapping) else None,
    }


# ══════════════════════════════════════════════════════════════════════════
# 请求解析（手工解析：不触发 FastAPI 默认含 ``input`` 的 422 内容）
# ══════════════════════════════════════════════════════════════════════════


class BodyError(Exception):
    def __init__(self, code: str) -> None:
        self.code = str(code)
        super().__init__(self.code)


async def read_json_object(request: Request, *, allow_empty: bool) -> dict[str, Any]:
    """按 ``request.stream()`` **逐块**累计 body；超过 64 KiB **立即**拒绝。

    r2：不再先调用无界的 ``request.body()``（那会把任意大的 body 完整读进内存后才判上限）。
    坏 body（非法 UTF-8 / 非法 JSON / 深嵌套触发 ``RecursionError``）一律静态 422。
    """
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_JSON_BODY:
            raise BodyError("invalid-body")
        chunks.append(chunk)
    raw = b"".join(chunks)
    if not raw.strip():
        if allow_empty:
            return {}
        raise BodyError("invalid-body")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise BodyError("invalid-json") from None
    try:
        payload = json.loads(text)
    except RecursionError:
        # 深嵌套（如 ``[[[...]]]``）在 C 扫描器里可能抛 RecursionError（非 ValueError）。
        raise BodyError("invalid-json") from None
    except ValueError:
        raise BodyError("invalid-json") from None
    if not isinstance(payload, dict):
        raise BodyError("invalid-body")
    return payload


# ══════════════════════════════════════════════════════════════════════════
# 路由（顺序：gate → id/body 校验 → 有界 service 调用）
# ══════════════════════════════════════════════════════════════════════════


def _runtime(request: Request) -> TerminalRuntime:
    runtime = getattr(request.app.state, "terminal_runtime", None)
    if not isinstance(runtime, TerminalRuntime):
        raise GateRejected(503, "not-ready")
    return runtime


def _enter(request: Request) -> tuple[TerminalRuntime | None, JSONResponse | None]:
    """入口 gate（先于 id/body 校验与任何 service 调用）。"""
    try:
        runtime = _runtime(request)
        runtime.check_gate(request)
    except GateRejected as rejected:
        return None, fail(rejected.status_code, rejected.code)
    return runtime, None


async def _execute(runtime: TerminalRuntime, handler: Callable[[], Any]) -> JSONResponse:
    """有界执行 + 静态错误映射（不重跑 gate）。"""
    try:
        result = await runtime.call(handler)
    except Busy:
        return fail(429, "busy")
    except SnapshotTooLarge:
        return fail(502, "snapshot-too-large")
    except GateRejected as rejected:
        # r2：``call`` 内的关门复查（closing/unavailable…）按对应 503 返回，**不是** 500。
        return fail(rejected.status_code, rejected.code)
    except Exception as exc:  # noqa: BLE001 - 只映射静态分类
        status_code, code = service_error(exc)
        return fail(status_code, code)
    return ok(result)


def _id_or_error(terminal_id: str) -> JSONResponse | None:
    try:
        validate_terminal_id(terminal_id)
    except ValueError:
        return fail(422, "invalid-terminal-id")
    return None


async def _body_or_error(request: Request, *, allow_empty: bool) -> tuple[dict[str, Any] | None, JSONResponse | None]:
    try:
        return await read_json_object(request, allow_empty=allow_empty), None
    except BodyError as exc:
        return None, fail(422, exc.code)


@router.get("")
async def list_terminals(request: Request) -> JSONResponse:
    runtime, denied = _enter(request)
    if denied is not None:
        return denied
    assert runtime is not None
    return await _execute(runtime, lambda: [project_view(item) for item in runtime.service.list()])


@router.get("/{terminal_id}")
async def get_terminal(terminal_id: str, request: Request) -> JSONResponse:
    runtime, denied = _enter(request)
    if denied is not None:
        return denied
    invalid = _id_or_error(terminal_id)
    if invalid is not None:
        return invalid
    assert runtime is not None
    return await _execute(runtime, lambda: project_view(runtime.service.get(terminal_id)))


@router.post("")
async def create_terminal(request: Request) -> JSONResponse:
    runtime, denied = _enter(request)
    if denied is not None:
        return denied
    payload, bad_body = await _body_or_error(request, allow_empty=False)
    if bad_body is not None:
        return bad_body
    assert payload is not None
    allowed = {"rows", "cols", "cwd", "workspace_id", "session_id"}
    if set(payload) - allowed:
        # 拒绝额外字段（context/created_by/trusted_local/terminal_id/token/shell_argv…）
        return fail(422, "unknown-field")
    rows, error = _bounded_field(payload, "rows", ROWS_DEFAULT, ROWS_MIN, ROWS_MAX)
    if error:
        return fail(422, error)
    cols, error = _bounded_field(payload, "cols", COLS_DEFAULT, COLS_MIN, COLS_MAX)
    if error:
        return fail(422, error)
    cwd, error = _optional_string_field(payload, "cwd")
    if error:
        return fail(422, error)
    workspace_id, error = _optional_string_field(payload, "workspace_id")
    if error:
        return fail(422, error)
    session_id, error = _optional_string_field(payload, "session_id")
    if error:
        return fail(422, error)

    assert runtime is not None

    def handler() -> Any:
        return project_view(
            runtime.service.create(
                rows=int(rows),
                cols=int(cols),
                cwd=cwd,
                workspace_id=workspace_id,
                session_id=session_id,
                # 固定上下文：来自通过入口检查的本地信任面；不从 session_id 推断。
                context=WEB_LOCAL_CONTEXT,
            )
        )

    return await _execute(runtime, handler)


@router.get("/{terminal_id}/read")
async def read_terminal(
    terminal_id: str,
    request: Request,
    cursor: str | None = None,
    max_bytes: str | None = None,
) -> JSONResponse:
    runtime, denied = _enter(request)
    if denied is not None:
        return denied
    invalid = _id_or_error(terminal_id)
    if invalid is not None:
        return invalid
    cursor_value = 0 if cursor is None else _parse_uint64(cursor)
    if cursor_value is None:
        return fail(422, "invalid-cursor")
    if max_bytes is None:
        max_bytes_value = MAX_BYTES_DEFAULT
    else:
        max_bytes_value = _parse_bounded_int_str(max_bytes, MAX_BYTES_MIN, MAX_BYTES_MAX)
        if max_bytes_value is None:
            return fail(422, "invalid-max-bytes")
    assert runtime is not None
    return await _execute(
        runtime,
        lambda: project_read(runtime.service.read(terminal_id, cursor_value, max_bytes=max_bytes_value)),
    )


@router.post("/{terminal_id}/snapshot")
async def snapshot_terminal(terminal_id: str, request: Request) -> JSONResponse:
    runtime, denied = _enter(request)
    if denied is not None:
        return denied
    invalid = _id_or_error(terminal_id)
    if invalid is not None:
        return invalid
    payload, bad_body = await _body_or_error(request, allow_empty=True)
    if bad_body is not None:
        return bad_body
    assert payload is not None
    if set(payload) - {"timeout_ms"}:
        return fail(422, "unknown-field")
    timeout_ms, error = _bounded_field(payload, "timeout_ms", TIMEOUT_DEFAULT, TIMEOUT_MIN, TIMEOUT_MAX)
    if error:
        return fail(422, error)
    assert runtime is not None

    def handler() -> Any:
        raw = runtime.service.snapshot(terminal_id, timeout_ms=int(timeout_ms))
        screen = _raw_bytes(raw.get("serialized_screen"))
        if screen is not None and len(screen) > SNAPSHOT_SCREEN_MAX:
            raise SnapshotTooLarge("snapshot-too-large")
        return project_snapshot(raw)

    return await _execute(runtime, handler)


@router.post("/{terminal_id}/close")
async def close_terminal(terminal_id: str, request: Request) -> JSONResponse:
    runtime, denied = _enter(request)
    if denied is not None:
        return denied
    invalid = _id_or_error(terminal_id)
    if invalid is not None:
        return invalid
    payload, bad_body = await _body_or_error(request, allow_empty=True)
    if bad_body is not None:
        return bad_body
    if payload:
        return fail(422, "unknown-field")
    assert runtime is not None
    return await _execute(
        runtime,
        lambda: project_view(runtime.service.close(terminal_id, reason="explicit-close")),
    )


@router.post("/{terminal_id}/detach")
async def detach_terminal(terminal_id: str, request: Request) -> JSONResponse:
    runtime, denied = _enter(request)
    if denied is not None:
        return denied
    invalid = _id_or_error(terminal_id)
    if invalid is not None:
        return invalid
    payload, bad_body = await _body_or_error(request, allow_empty=True)
    if bad_body is not None:
        return bad_body
    if payload:
        return fail(422, "unknown-field")
    assert runtime is not None

    def handler() -> Any:
        raw = runtime.service.detach(terminal_id)
        if not isinstance(raw, Mapping) or raw.get("detached") is not True:
            # 拒绝即 409 detach-refused：不伪称 durable。
            raise DetachRefused("detach-refused")
        return project_detach(raw)

    return await _execute(runtime, handler)


# ══════════════════════════════════════════════════════════════════════════
# lifespan helper（真实 server 薄接入；测试可直接构造 runtime）
# ══════════════════════════════════════════════════════════════════════════


def build_runtime(
    *,
    env: Mapping[str, str] | None = None,
    host: str | None = None,
    port: int | None = None,
    platform: str | None = None,
    service_factory: Callable[[], Any] | None = None,
    max_inflight: int = DEFAULT_MAX_INFLIGHT,
    shutdown_budget: float = DEFAULT_SHUTDOWN_BUDGET_SECONDS,
) -> TerminalRuntime:
    """按环境解析并构造 runtime（**不**构造 service；由 startup 负责）。"""
    env = os.environ if env is None else env
    resolved_port = resolve_port(env) if port is None else int(port)
    resolved_host = (env.get("PAN_HOST") if host is None else host) or DEFAULT_PAN_HOST
    resolved_platform = sys.platform if platform is None else str(platform)
    allow_remote = str(env.get("PAN_TERMINAL_ALLOW_REMOTE") or "").strip() == "1"

    is_windows = resolved_platform.lower().startswith("win")
    loopback = is_loopback_host(resolved_host)
    enabled = is_windows and (loopback or allow_remote)
    if not is_windows:
        disabled_reason = "platform-not-windows"
    elif not loopback and not allow_remote:
        disabled_reason = "non-loopback-binding"
    else:
        disabled_reason = ""

    return TerminalRuntime(
        enabled=enabled,
        disabled_reason=disabled_reason,
        allowlist=build_allowlist(env, port=resolved_port),
        service_factory=service_factory,
        max_inflight=max_inflight,
        shutdown_budget=shutdown_budget,
    )


async def start_runtime(
    app: Any,
    *,
    runtime: TerminalRuntime | None = None,
    **kwargs: Any,
) -> TerminalRuntime:
    """lifespan 启动：构造 runtime、挂到 ``app.state``、安排一次 reconcile。"""
    active = runtime if runtime is not None else build_runtime(**kwargs)
    app.state.terminal_runtime = active
    await active.startup()
    return active


async def stop_runtime(app: Any, *, runtime: TerminalRuntime | None = None) -> dict[str, Any]:
    """lifespan 停止：先关 REST 准入，再按总预算收敛 service。"""
    active = runtime if runtime is not None else getattr(app.state, "terminal_runtime", None)
    if not isinstance(active, TerminalRuntime):
        return {
            "status": "unconfirmed", "confirmed": False, "unconfirmed": [],
            "unconfirmed_count": 0, "problems": ["no-runtime"],
            "secrets_retained": True, "budget_exhausted": None,
            "retained_service": False, "tracked_tasks": 0,
        }
    report = await active.shutdown()
    report["retained_service"] = active.service is not None
    report["tracked_tasks"] = active.tracked_tasks
    return report


@asynccontextmanager
async def terminal_lifespan(
    app: Any,
    *,
    runtime: TerminalRuntime | None = None,
    **kwargs: Any,
):
    """真实 server 用的**可独立测试** lifespan 片段。

    ``yield`` 与 ``stop_runtime`` 走 **try/finally**：即使 lifespan 体异常或被取消，
    也一定请求收尾（先关 REST 准入，再按总预算收敛同一 service）。等待 shield 与引用
    保留规则由 :meth:`TerminalRuntime.shutdown` 保持（不裸释放、不报成功）。
    """
    active = await start_runtime(app, runtime=runtime, **kwargs)
    try:
        yield active
    finally:
        await stop_runtime(app, runtime=active)
