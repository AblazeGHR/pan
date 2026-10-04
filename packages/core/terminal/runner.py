"""Pan Terminal runner（P1，布局 B：runner 出生持 PTY + guard）。

职责（冻结接口见 ``docs/design/PAN_TERMINAL_RUNNER_INTERFACES_20261003.md``）：

1. **bootstrap（argv 只有 ``--terminal-id`` / ``--secret-file``）**：写自身
   ``pid + raw64 FILETIME`` 自证（hello）→ 等 DPAPI 秘密（有界）→ 自身身份自检
   （fail-closed）→ token 环境泄漏自检 → ``FIRST_PIPE_INSTANCE`` 占名 →
   challenge/hello/ack HMAC 会话。
2. **原子 owned runtime**：``ConPtyBackend.spawn``（挂起创建 → assign → resume）
   → ``ownership.build_runtime``（fail-closed 工厂）→ ``runtime.start(gate=...)``。
   四要素缺一即拒绝 running（"没有证据不发布 running"）。
3. **lease**：Pan 所有者每 1s 心跳（``lease`` op，role=control）；统一死期 2s；
   丢失且未 detach → 整树自停。观察者/浏览器连接断开不改变 runtime、不续约。
4. **detach/durability**：宿主 ambient Job 可能杀 runner；能力不足时**显式拒绝**
   detach（不假成功）。detach 成功后保持同 PTY/PID/端点/秘密，新 controller
   （同用户）重连。
5. **worker 纪律**：write/close 不在 IPC/lease 循环内直接执行，交给可追踪 worker
   做有界等待；超时不重复启动同一阻塞调用、不释放 owner、关闭输入门、重试如实。
6. **快照桥接**：``AuthoritativeEmulator`` → ``OutputConsumer(seq, data)`` 的桥
   （缺口按绝对偏移检测；可选 ``feed_at`` 扩展；缺口/feed_lag 一律显式降级）。
   引擎未注入时快照 ``fidelity=unavailable``（不承诺完整恢复）。

边界（不得宣称）：

- 权威仿真器引擎本身不在本模块（并行 TA 的 ``emulator.py``）；本层只提供桥接与降级。
- 服务层/Web/MCP/registry 接线属 P2；本模块无 Web 依赖。
- ambient Job 下的 durable detach 未实证（本机宿主 Job 不可清除，见接口文档 §6）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import ipc, secret_store, win_pipe
from .contracts import (
    DEFAULT_EOF_GRACE_SECONDS,
    DEFAULT_LEASE_GRACE_SECONDS,
    DEFAULT_READ_SIZE,
    AppliedSnapshot,
    AuthoritativeEmulator,
    CleanupReport,
    DetachReport,
    DetachUnsupportedError,
    Fidelity,
    IllegalStateTransition,
    InvalidCursorError,
    LEASE_ROLE_CONTROL,
    LEASE_ROLE_OBSERVER,
    OwnershipMode,
    OwnershipPolicy,
    Recovery,
    RuntimeState,
)
from .ownership import build_runtime

__all__ = [
    "RUNNER_EXIT_OK",
    "RUNNER_EXIT_USAGE",
    "RUNNER_EXIT_CLEANUP_FAILED",
    "RUNNER_EXIT_BOOTSTRAP_FAILED",
    "RUNNER_EXIT_INTERNAL",
    "DEFAULT_SHELL_ARGV",
    "RunnerBootstrapError",
    "DurabilityCapability",
    "detect_ambient_job",
    "detect_durability_capability",
    "RunnerEmulatorBridge",
    "TerminalRunner",
    "main",
]

#: 正常停止（显式 close / 死期自停且清理收敛）。
RUNNER_EXIT_OK = 0
#: 命令行/用法错误。
RUNNER_EXIT_USAGE = 2
#: 死期清理有界重试后仍未收敛（退出即关闭 guard 句柄 = 内核兜底，不是"已确认清理"）。
RUNNER_EXIT_CLEANUP_FAILED = 3
#: bootstrap / 身份 / 管道 / 启动门禁失败，且自有资源清理已证明收敛。
RUNNER_EXIT_BOOTSTRAP_FAILED = 4
#: 未预期内部错误（类型名已脱敏记录）。
RUNNER_EXIT_INTERNAL = 5
#: 启动失败或收尾时清理**未能证明收敛**（owner/引用保留，跨进程见状态文件）。
RUNNER_EXIT_CLEANUP_UNPROVEN = 6
#: accept 循环异常退出（非正常 stop；清理状态见状态文件 ``cleanup.converged``）。
RUNNER_EXIT_ACCEPT_FAILED = 7

_EXIT_REASONS: dict[int, str] = {
    RUNNER_EXIT_OK: "ok",
    RUNNER_EXIT_USAGE: "usage",
    RUNNER_EXIT_CLEANUP_FAILED: "cleanup-failed",
    RUNNER_EXIT_BOOTSTRAP_FAILED: "bootstrap-failed",
    RUNNER_EXIT_INTERNAL: "internal-error",
    RUNNER_EXIT_CLEANUP_UNPROVEN: "cleanup-unproven",
    RUNNER_EXIT_ACCEPT_FAILED: "accept-failed",
}

#: 默认 shell（计划 §13：cmd.exe /q /d；可配置为 pwsh 由后续接线负责）。
DEFAULT_SHELL_ARGV: tuple[str, ...] = ("cmd.exe", "/q", "/d")

#: lease 死期默认值 = 计划 §13 统一口径 2s（心跳 1s）。
_LEASE_GRACE_SECONDS = DEFAULT_LEASE_GRACE_SECONDS
#: 首个 owner 心跳到达前的宽限：Pan 崩溃（未连接）同样按"丢失"自停。
_BOOTSTRAP_GRACE_SECONDS = 10.0
#: 等秘密文件出现的有界预算（与 secret_store 默认一致）。
_BOOTSTRAP_TIMEOUT_SECONDS = 10.0
#: input handler 等待 write worker 的上限：必须 < 死期（2s），否则阻塞 handler
#: 会把同一连接上的心跳拖过死期。
_INPUT_ACK_WAIT_SECONDS = 0.75
#: stop handler 等待 close worker 的上限（超时回 closing，不释放 owner）。
_CLOSE_WAIT_SECONDS = 8.0
#: 死期清理未收敛时的有界重试次数与间隔。
_LEASE_CLEANUP_RETRIES = 3
_LEASE_CLEANUP_RETRY_INTERVAL = 0.5
#: 关闭后留给连接写回响应的窗口（本地命名管道毫秒级；1.5s 是保险）。
_SHUTDOWN_GRACE_SECONDS = 1.5
#: 主循环收尾时等待 watchdog 线程退出的有界预算。
_WATCHDOG_JOIN_SECONDS = 3.0
#: ``run()`` 收尾（_finalize）的总预算：超过即按"未证明收敛"如实收场，不无限等锁。
_FINALIZE_BUDGET_SECONDS = 12.0
#: 启动失败路径清理的有界重试次数与单次等待（backend.close / pipe.close）。
_STARTUP_CLEANUP_ATTEMPTS = 2
_STARTUP_CLEANUP_TIMEOUT_SECONDS = 3.0
#: 跨进程可观测的运行期状态文件目录（位于 terminals root 下；无秘密）。
_STATUS_DIRNAME = "runner-status"
#: 每个连接的有界操作队列（入队固化期限，见 ipc.RequestScheduler）。
_CONNECTION_QUEUE = 8
#: 连接 serve 循环的接收超时（保证能周期性检查 shutdown）。
_RECV_POLL_SECONDS = 0.25
#: detail 摘要（描述 JSON）的有界预算（响应字段上限 4096）。
_DETAIL_BUDGET = 3800
#: 响应 payload 顶层允许字段（与 ``ipc._validate_response_payload`` 白名单一致）。
_RESPONSE_FIELDS = frozenset(
    {
        "data_b64",
        "seq",
        "size",
        "cursor",
        "next_cursor",
        "gap",
        "truncated",
        "rows",
        "cols",
        "status",
        "detail",
        "snapshot",
        "reason",
        "total_bytes",
        "first_retained_seq",
        "ok",
    }
)
#: detail 字段级缩减时按序丢弃的可选块（先丢最不关键的）。
_OPTIONAL_DETAIL_KEYS: tuple[str, ...] = (
    "observers",
    "connections",
    "input_worker",
    "close_worker",
    "consumer",
    "snapshot",
    "cleanup",
    "lease",
    "durability",
    "exit",
    "reconnect_hint",
    "events",
    "detail",
)
#: 机器判定字段（F5）：**任何缩减级别都必须保留**（缺失=unknown，不得因缩减丢失）。
_PRESERVE_DETAIL_KEYS: tuple[str, ...] = ("cursors_valid", "reset_unconfirmed")


def _ledger_int_or_none(value: Any) -> int | None:
    """F5：账本类整数（如 ``baseline_cursor``）的保守解析。

    只接受**合法 int（非 bool，且非负）**或**十进制字符串**：允许两侧空白，去空白后
    须为 ASCII 数字、可带一个正号 ``+``；其余（bool、浮点、空白、其他字符、全角数字、
    负数）一律 ``None``——**未知不造值**。负数在任一侧都代表非法绝对偏移 → ``None``。
    **超长 ASCII 数字**：CPython 整型字符串转换有位数上限（默认 4300 位），``int()``
    可能抛 ``ValueError``——此处捕获并回落 ``None``（观测面**不额外抛错**）。
    Python int 无界：合法大整数精确比较（禁浮点/截断）。
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return int(value) if value >= 0 else None
    if isinstance(value, str):
        text = value.strip()
        if text[:1] == "+":
            text = text[1:]
        if text and text.isascii() and text.isdigit():
            try:
                return int(text)
            except ValueError:  # 超长（默认 4300 位上限）：未知不造值、不抛
                return None
    return None


def _shrink_strings(value: Any, limit: int) -> Any:
    """递归缩短字符串（detail 字段级缩减用；绝不产生非法 JSON）。"""
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit]
    if isinstance(value, dict):
        return {key: _shrink_strings(item, limit) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_shrink_strings(item, limit) for item in value]
    return value


def _bounded_json(payload: Mapping[str, Any], budget: int = _DETAIL_BUDGET) -> str:
    """把字典渲染为**合法且有界**的 JSON（字段级缩减，禁止字符串截断）。

    缩减顺序：嵌套长字符串逐级缩短 → 按 ``_OPTIONAL_DETAIL_KEYS`` 丢弃可选项 →
    极简骨架。``_PRESERVE_DETAIL_KEYS``（机器判定字段）在**所有**缩减级别保留。
    任何一步的输出都可被 ``json.loads`` 解析。
    """
    def render(obj: Mapping[str, Any]) -> str:
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))

    candidate: dict[str, Any] = dict(payload)
    text = render(candidate)
    if len(text) <= budget:
        return text
    for limit in (512, 256, 128, 64, 32):
        candidate = _shrink_strings(candidate, limit)
        text = render(candidate)
        if len(text) <= budget:
            return text
    for key in _OPTIONAL_DETAIL_KEYS:
        candidate.pop(key, None)
        text = render(candidate)
        if len(text) <= budget:
            return text
    preserved = {
        key: candidate[key] for key in _PRESERVE_DETAIL_KEYS if key in candidate
    }
    minimal = {
        key: candidate.get(key)
        for key in ("schema_version", "terminal_id", "runner_state", "runtime_state", "detached")
        if key in candidate
    }
    minimal.update(preserved)
    minimal["truncated_fields"] = True
    text = render(_shrink_strings(minimal, 64))
    if len(text) <= budget:
        return text
    if preserved:
        return render(preserved)
    return render({"schema_version": 1, "truncated_fields": True})


class RunnerBootstrapError(RuntimeError):
    """bootstrap/管道/启动门禁失败（fail-closed；原因只允许静态串与类型名）。"""


@dataclass(frozen=True)
class DurabilityCapability:
    """durable detach 能力声明（受宿主 ambient Job 限制时 capable=False）。"""

    durable_capable: bool
    ambient_job: bool
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "capable": self.durable_capable,
            "ambient_job": self.ambient_job,
            "detail": self.detail,
        }


def detect_ambient_job() -> bool:
    """自身是否处于**宿主** ambient Job。

    本进程从不把自己 assign 进自有 guard Job（guard 只罩 PTY 子进程），因此
    ``IsProcessInJob(GetCurrentProcess(), NULL)`` 为真即宿主 Job。宿主 Job 可能带
    ``KILL_ON_JOB_CLOSE``：Pan 退出 → Job 句柄关闭 → runner 被杀 → detach 的
    durable 承诺不成立；进程内无法清除（无 Job 句柄可查询/改标志）。

    非 Windows：恒为 False（无 Job 模型；durable 由 detect_durability_capability
    fail-closed 拒绝）。
    """
    if sys.platform != "win32":
        return False
    import ctypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    result = ctypes.c_int(0)
    if not k32.IsProcessInJob(ctypes.c_void_p(-1), None, ctypes.byref(result)):
        # 查询失败 = 能力未知：按"处于 Job"（保守）处理，durable 判定 fail-closed。
        return True
    return bool(result.value)


def detect_durability_capability() -> DurabilityCapability:
    """durable detach 能力：非 Windows 不承诺；Windows 按 ambient Job 判定。"""
    if sys.platform != "win32":
        return DurabilityCapability(
            durable_capable=False,
            ambient_job=False,
            detail="non-windows: durable detach 未验证（fail-closed）",
        )
    ambient = detect_ambient_job()
    if ambient:
        return DurabilityCapability(
            durable_capable=False,
            ambient_job=True,
            detail=(
                "host ambient Job present: cannot prove runner survives the job's close "
                "(KILL_ON_JOB_CLOSE may kill the tree); detach refused (fail-closed)"
            ),
        )
    return DurabilityCapability(
        durable_capable=True,
        ambient_job=False,
        detail="no ambient job detected: runner is not owned by a kill-on-close host job",
    )


class RunnerEmulatorBridge:
    """``OutputConsumer(seq, data)`` → ``AuthoritativeEmulator`` 的桥（冻结语义）。

    - 第一参数是**绝对字节偏移**；不连续即缺口：粘滞 ``gap_seen`` + 有界 gap 记录
      （缺口事实不丢失）；
    - 仿真器提供可选扩展 ``feed_at(seq, data)`` 时按真实偏移投递（不改 contracts）；
      否则退回 base 协议 ``feed(data)`` 并**永久降级**（快照不允许 full）；
    - feed 与 resize 共用本桥的内部锁（同一有序通道）；``snapshot`` **不**持锁
      （barrier 不许阻塞 reader；跨进程一致性由仿真器自己的命令通道保证）。
    """

    MAX_GAPS = 8

    def __init__(self, emulator: AuthoritativeEmulator) -> None:
        self._emulator = emulator
        candidate = getattr(emulator, "feed_at", None)
        self._feed_at: Callable[[int, bytes], None] | None = (
            candidate if callable(candidate) else None
        )
        self._lock = threading.Lock()
        self._expected: int | None = None
        self._gaps: list[tuple[int, int]] = []
        self._gap_seen = False
        self._fed_through = 0

    def __call__(self, seq: int, data: bytes) -> None:
        if not data:
            return
        with self._lock:
            expected = self._expected
            if expected is not None and int(seq) != expected:
                self._gap_seen = True
                if len(self._gaps) < self.MAX_GAPS:
                    self._gaps.append((int(expected), int(seq)))
            if expected is None:
                self._expected = int(seq)
            if self._feed_at is not None:
                self._feed_at(int(seq), bytes(data))
            else:
                self._emulator.feed(bytes(data))
            self._expected = int(seq) + len(data)
            self._fed_through = int(seq) + len(data)

    def resize(self, rows: int, cols: int) -> None:
        """resize 与 feed 走同一把桥锁（同一有序通道；不绕过仿真器）。"""
        with self._lock:
            self._emulator.resize(int(rows), int(cols))

    @property
    def gap_seen(self) -> bool:
        return self._gap_seen

    @property
    def gaps(self) -> tuple[tuple[int, int], ...]:
        with self._lock:
            return tuple(self._gaps)

    @property
    def fed_through(self) -> int:
        with self._lock:
            return self._fed_through

    @property
    def uses_feed_at(self) -> bool:
        return self._feed_at is not None


class _TrackedCall:
    """可追踪的有界调用（write/close worker 复用语义）。

    与 ``runtime._BoundedCall`` 同构但**只记异常类型名**（防异常文本带出秘密）：

    - 首次 ``run`` 启动 daemon worker；超时返回 ``finished=False``，worker
      **保留**——下次 ``run`` 继续等待同一 worker（**不重复启动同一阻塞调用**）；
    - 已完成的成功结果可重复消费；失败/成功完成后可 ``reset`` 绑定新一轮
      （仍不重叠）。
    """

    def __init__(self, fn: Callable[[], Any]) -> None:
        self._fn = fn
        self._worker: threading.Thread | None = None
        self._done = threading.Event()
        self._result: Any = None
        self._error_type: str | None = None
        self._started_at = 0.0
        self._finished_at: float | None = None
        self._start_lock = threading.Lock()

    def reset(self, fn: Callable[[], Any] | None = None) -> None:
        with self._start_lock:
            if self._worker is not None and self._worker.is_alive():
                raise RuntimeError("cannot reset a tracked call while its worker is still running")
            if fn is not None:
                self._fn = fn
            self._worker = None
            self._done = threading.Event()
            self._result = None
            self._error_type = None
            self._started_at = 0.0
            self._finished_at = None

    @property
    def started(self) -> bool:
        return self._worker is not None

    @property
    def finished(self) -> bool:
        return self._worker is not None and self._done.is_set()

    @property
    def in_flight(self) -> bool:
        return self._worker is not None and not self._done.is_set()

    @property
    def succeeded(self) -> bool:
        return self.finished and self._error_type is None

    @property
    def error_type(self) -> str | None:
        return self._error_type

    @property
    def result(self) -> Any:
        """已完成 worker 的结果（未完成返回 None；只读，供"消费迟到成功"）。"""
        return self._result if self.finished else None

    @property
    def started_at(self) -> float:
        return self._started_at

    @property
    def finished_at(self) -> float | None:
        return self._finished_at

    def run(self, timeout: float) -> tuple[bool, Any, str | None]:
        """返回 ``(finished, result, error_type)``；``finished=False`` = 仍在执行。

        并发调用安全：启动段在内部锁内完成（同一 worker 只启动一次），等待段
        可多线程同时等同一 ``Event``（close 的 lifecycle 锁已串行化调用方）。
        """
        with self._start_lock:
            if not self.started:
                self._started_at = time.monotonic()

                def _work() -> None:
                    try:
                        self._result = self._fn()
                    except Exception as exc:  # noqa: BLE001 - 失败必须如实记录
                        self._error_type = type(exc).__name__
                    finally:
                        self._finished_at = time.monotonic()
                        self._done.set()

                self._worker = threading.Thread(
                    target=_work, name="runner-tracked-call", daemon=True
                )
                self._worker.start()
        if not self._done.wait(max(0.0, timeout)):
            return False, None, None
        return True, self._result, self._error_type


class _RunnerDetachHandler:
    """布局 B 的真实宿主能力：runner 自身就是 durable 宿主。

    只产出 ``DetachReport``（含重连提示）；**不触碰 PTY**——语义由
    ``runtime.detach()`` 固定的"保留原 PTY/PID、只置 durable 标志"承担。
    """

    def __init__(self, runner: "TerminalRunner") -> None:
        self._runner = runner

    def request_detach(self, runtime: Any) -> DetachReport:
        now = time.time()
        identity = getattr(runtime, "identity", None)
        hint = {
            "pid": identity.pid if identity is not None else None,
            "process_created_at_filetime": (
                str(identity.created_at_filetime)
                if identity is not None and identity.created_at_filetime is not None
                else None
            ),
            "pipe": win_pipe.pipe_name_for(self._runner.terminal_id),
            "reconnect": "read DPAPI secret, connect pipe, lease(control) heartbeat",
        }
        return DetachReport(
            terminal_id=self._runner.terminal_id,
            detached_at=now,
            durable_owner="runner",
            reconnect_hint=hint,
            note=(
                "layout B durable runner: PTY/PID/pipe/secret preserved; "
                "lease expiry no longer stops the runtime"
            ),
        )


class TerminalRunner:
    """runner 进程主体（见模块 docstring 与接口文档 §7.1）。"""

    def __init__(
        self,
        terminal_id: str,
        secret_file: str | Path,
        *,
        rows: int = 24,
        cols: int = 80,
        shell_argv: Sequence[str] | None = None,
        cwd: str | None = None,
        lease_grace_seconds: float = _LEASE_GRACE_SECONDS,
        bootstrap_grace_seconds: float = _BOOTSTRAP_GRACE_SECONDS,
        bootstrap_timeout_seconds: float = _BOOTSTRAP_TIMEOUT_SECONDS,
        input_ack_wait: float = _INPUT_ACK_WAIT_SECONDS,
        close_wait: float = _CLOSE_WAIT_SECONDS,
        lease_cleanup_retries: int = _LEASE_CLEANUP_RETRIES,
        emulator: AuthoritativeEmulator | None = None,
        durability_probe: Callable[[], DurabilityCapability] | None = None,
        identity_probe: Callable[[int], Any] | None = None,
        write_hook: Callable[[bytes], int] | None = None,
        accept_timeout: float = 0.5,
        diagnostics: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.terminal_id = str(terminal_id)
        self._secret_file = Path(secret_file)
        self._rows = int(rows)
        self._cols = int(cols)
        self._shell_argv = tuple(shell_argv) if shell_argv is not None else DEFAULT_SHELL_ARGV
        self._cwd = cwd
        self._lease_grace = max(0.1, float(lease_grace_seconds))
        self._bootstrap_grace = max(0.1, float(bootstrap_grace_seconds))
        self._bootstrap_timeout = max(0.1, float(bootstrap_timeout_seconds))
        self._input_ack_wait = max(0.0, float(input_ack_wait))
        self._close_wait = max(0.0, float(close_wait))
        self._lease_cleanup_retries = max(0, int(lease_cleanup_retries))
        self._natural_exit_ready_at: float | None = None
        self._emulator = emulator
        self._bridge = RunnerEmulatorBridge(emulator) if emulator is not None else None
        #: 生产必须为 backend.probe（装配约束）；注入仅测试/诊断用。
        self._identity_probe_override = identity_probe
        #: 写路径注入（**仅测试/诊断**）：默认 None = ``runtime.write``；生产不得注入。
        self._write_hook = write_hook
        self._durability_probe = durability_probe or detect_durability_capability
        self._accept_timeout = max(0.05, float(accept_timeout))
        self._diagnostics = diagnostics

        self._store: secret_store.SecretStore | None = None
        self._token: str | None = None
        self._own_identity = None
        self._secret_payload: secret_store.SecretPayload | None = None
        self._hello_written = False
        self._secret_verified = False

        self._backend: Any = None
        self._runtime: Any = None
        self._pipe_server: win_pipe.PipeServer | None = None

        #: runner 级状态（与 runtime 状态分开公布）。
        self._runner_state = "init"
        self._exit_code: int | None = None
        self._closing = False
        self._detached = False
        self._last_cleanup: CleanupReport | None = None

        # lease 状态（只有已认证 control 心跳能改）。
        # RLock：命令 payload 构建（describe）在同一线程可重入，避免自锁死。
        self._lease_lock = threading.RLock()
        self._owner_client_id: str | None = None
        self._owner_generation: int = 0
        self._last_heartbeat: float | None = None
        self._lease_established = False
        self._lease_started_at = 0.0

        # worker（write/close）与连接登记。
        self._worker_lock = threading.RLock()
        self._write_worker: _TrackedCall | None = None
        self._write_completed = 0
        self._last_input_status = "none"
        self._close_worker: _TrackedCall | None = None
        self._lifecycle_lock = threading.RLock()
        self._conn_lock = threading.RLock()
        self._conn_threads: list[threading.Thread] = []
        self._conn_accepted = 0
        self._conn_active = 0
        self._observer_registrations = 0

        # 关闭/退出协调。
        self._shutdown_lock = threading.Lock()
        self._shutdown_flag = False
        self._shutdown_deadline: float | None = None
        self._watchdog_stop = threading.Event()
        self._watchdog: threading.Thread | None = None
        self._events: list[tuple[float, str, str]] = []
        #: 生命周期仲裁（R2）：lease-expiry 一旦取得关闭权，detach 明确拒绝。
        self._expiry_in_progress = False
        self._expiry_reason: str | None = None
        #: 启动失败清理的可重试 owner（不收敛时保留引用）与报告（O1/R3）。
        self._retained_backend: Any = None
        self._retained_pipe_server: Any = None
        self._startup_close_call: _TrackedCall | None = None
        self._startup_cleanup: dict[str, Any] = {}
        self._pipe_cleanup: dict[str, Any] = {}
        #: 跨进程可观测的状态目录（terminals root/runner-status；无秘密）。
        self._status_dir = self._secret_file.parent.parent / _STATUS_DIRNAME
        #: 状态写串行化 + 唯一自有 tmp（N3）。
        self._status_lock = threading.RLock()
        #: 收尾（N4）结果记录：outcome / lock_acquired / worker 追踪。
        self._finalize_report: dict[str, Any] | None = None
        #: accept 循环异常退出的事实（脱敏类型名）。
        self._accept_failure: str | None = None
        self._exit_reason: str = "ok"
        #: lease 世代：每次心跳/接管自增 —— 旧 expiry 不得误杀新 lease。
        self._lease_epoch = 0

    # ------------------------------------------------------------------ 诊断
    def _note(self, event: str, detail: str = "") -> None:
        """有界、脱敏（只允许静态串/类型名）的运行事件。"""
        entry = (time.time(), str(event)[:48], str(detail)[:64])
        self._events.append(entry)
        if len(self._events) > 64:
            del self._events[:-64]
        if self._diagnostics is not None:
            try:
                self._diagnostics({"event": entry[1], "detail": entry[2], "at": entry[0]})
            except Exception:  # noqa: BLE001 - 诊断回调不得影响主流程
                pass

    @property
    def events(self) -> tuple[tuple[float, str, str], ...]:
        return tuple(self._events)

    # ------------------------------------------------------------ 只读属性
    @property
    def runner_state(self) -> str:
        return self._runner_state

    @property
    def exit_reason(self) -> str:
        """退出原因（静态串；用于 stderr/状态文件，不含秘密）。"""
        return self._exit_reason

    @property
    def status_path(self) -> Path:
        """跨进程可观测状态文件路径（<root>/runner-status/<terminal_id>.json）。"""
        return self._status_path()

    @property
    def detached(self) -> bool:
        return self._detached

    @property
    def runtime(self) -> Any:
        return self._runtime

    @property
    def token(self) -> str | None:
        """仅供同进程装配（测试断言 token 不落盘/不上线）；不得写日志。"""
        return self._token

    # ------------------------------------------------------------ bootstrap
    @staticmethod
    def _assert_token_not_in_env(token: str) -> None:
        """token 不得出现在环境变量（key/value 任一）：fail-closed。"""
        for key, value in os.environ.items():
            if token in key or token in value:
                raise RunnerBootstrapError("token found in process environment (refusing to start)")

    def _bootstrap(self) -> None:
        """hello → 秘密 → 自检 → 泄漏自检（失败抛 ``RunnerBootstrapError``）。"""
        store = secret_store.SecretStore(self._secret_file.parent.parent)
        expected_name = f"{self.terminal_id}{secret_store.SECRET_FILE_SUFFIX}"
        if self._secret_file.name != expected_name:
            raise RunnerBootstrapError("secret file name does not match terminal id")
        if self._secret_file.parent.name != secret_store.SECRETS_DIRNAME:
            raise RunnerBootstrapError("secret file is not under the canonical secrets directory")
        self._store = store
        store.write_bootstrap_identity(self.terminal_id)
        self._hello_written = True
        self._note("hello-written")
        payload = store.wait_for_secret(self.terminal_id, timeout=self._bootstrap_timeout)
        self._note("secret-read")
        own = win_pipe.current_process_identity()
        self._own_identity = own
        # 自身身份自检：秘密里的 pid + raw FILETIME 必须与自身一致。
        store.verify_runner_identity(self.terminal_id, own)
        self._secret_verified = True
        self._assert_token_not_in_env(payload.token)
        self._token = payload.token
        self._secret_payload = payload
        self._note("identity-verified")

    def _cleanup_hello_on_failure(self) -> None:
        store, verified = self._store, self._secret_verified
        if store is None or not self._hello_written or verified:
            return
        try:
            store.delete_bootstrap_identity(self.terminal_id)
        except Exception as exc:  # noqa: BLE001 - 清理失败只记录类型名
            self._note("hello-cleanup-failed", type(exc).__name__)

    # ------------------------------------------------------------ 启动装配
    def _spawn_backend(self) -> Any:
        from .backend import ConPtyBackend  # 延迟导入：非 Windows 不阻塞模块导入

        return ConPtyBackend.spawn(
            self._shell_argv,
            cwd=self._cwd,
            rows=self._rows,
            cols=self._cols,
        )

    def _build_runtime(self, backend: Any) -> Any:
        policy = OwnershipPolicy(
            mode=OwnershipMode.SERVICE,
            lifecycle_owner="runner",
            tree_guard_kind="job-object",
            tree_guard=backend.guard,
            lease_grace_seconds=self._lease_grace,
            detached=False,
            detach_handler=_RunnerDetachHandler(self),
            notes="布局 B：runner 自持 PTY+guard；Pan 只持 IPC 租约",
        )
        identity_probe = self._identity_probe_override or backend.probe
        return build_runtime(
            self.terminal_id,
            backend,
            ownership=policy,
            identity=backend.identity,
            identity_probe=identity_probe,
            output_consumer=self._bridge,
            read_size=DEFAULT_READ_SIZE,
            eof_grace=DEFAULT_EOF_GRACE_SECONDS,
        )

    def _prepare(self) -> None:
        """FIRST 管道 → 原子 owned runtime（失败抛 ``RunnerBootstrapError``）。"""
        server = win_pipe.PipeServer(
            self.terminal_id,
            max_instances=4,
            max_active_connections=4,
            max_frame_bytes=ipc.DEFAULT_MAX_FRAME_BYTES,
        )
        try:
            server.create()  # FILE_FLAG_FIRST_PIPE_INSTANCE：占名即拒绝
        except Exception as exc:  # noqa: BLE001 - 统一 fail-closed
            raise RunnerBootstrapError(f"pipe create refused: {type(exc).__name__}") from exc
        self._pipe_server = server
        self._note("pipe-ready")

        backend = None
        try:
            backend = self._spawn_backend()
            self._note("pty-spawned")
            runtime = self._build_runtime(backend)
            gate = backend.gate
            if gate is None:
                raise RunnerBootstrapError("spawn evidence missing: refuse to publish running")
            runtime.start(rows=self._rows, cols=self._cols, gate=gate)
        except RunnerBootstrapError:
            self._abort_backend(backend)
            self._close_pipe_server()
            raise
        except Exception as exc:  # noqa: BLE001
            self._abort_backend(backend)
            self._close_pipe_server()
            raise RunnerBootstrapError(
                f"atomic owned runtime refused: {type(exc).__name__}"
            ) from exc
        self._backend = backend
        self._runtime = runtime
        self._runner_state = "running"
        self._lease_started_at = time.monotonic()
        self._note("running")

    def _abort_backend(self, backend: Any) -> dict[str, Any]:
        """启动失败路径：有界清理自有后代；**不收敛时保留引用**（R3）。

        返回可审计报告（写入状态文件）；``backend.close`` 的有界 worker 跨重试
        复用（不重叠），必要时可经 :meth:`retry_startup_cleanup` 在同一进程内重试。
        不冒称"内核退出兜底已验证"——未收敛即如实标注 ``converged=False``。
        """
        report: dict[str, Any] = {
            "attempted": False,
            "terminate": None,
            "close": None,
            "close_attempts": 0,
            "converged": backend is None,
            "retained": [] if backend is None else ["backend"],
            "errors": [],
        }
        if backend is None:
            self._startup_cleanup = report
            return report
        self._retained_backend = backend  # 保引用：不收敛时仍可重试/如实报告
        report["attempted"] = True
        for _ in range(_STARTUP_CLEANUP_ATTEMPTS):
            try:
                backend.terminate(True)
                report["terminate"] = "returned"
                break
            except Exception as exc:  # noqa: BLE001 - 只记类型名
                report["terminate"] = f"error:{type(exc).__name__}"
                if f"terminate:{type(exc).__name__}" not in report["errors"]:
                    report["errors"].append(f"terminate:{type(exc).__name__}")
        for _ in range(_STARTUP_CLEANUP_ATTEMPTS):
            report["close_attempts"] += 1
            call = self._startup_close_call
            if call is None or call.finished:
                # 上一轮已结束（失败也算）：允许新一轮；未完成则复用同一 worker。
                call = _TrackedCall(backend.close)
                self._startup_close_call = call
            finished, _result, error_type = call.run(_STARTUP_CLEANUP_TIMEOUT_SECONDS)
            if not finished:
                report["close"] = "timeout"
                report["errors"].append("close:timeout")
                break
            if error_type is not None:
                report["close"] = f"error:{error_type}"
                report["errors"].append(f"close:{error_type}")
                continue
            closed_attr = getattr(backend, "closed", None)
            if closed_attr is None or bool(closed_attr):
                report["close"] = "returned"
                break
            report["close"] = "not-closed"
        report["converged"] = report["close"] == "returned"
        if report["converged"]:
            self._retained_backend = None
            report["retained"] = []
        self._startup_cleanup = report
        return report

    def retry_startup_cleanup(self) -> dict[str, Any]:
        """同一进程内重试启动失败清理（诊断/测试入口；runner 即将退出）。

        未收敛的 backend 引用一直被持有（不丢 owner），本方法对同一引用重试；
        ``backend.close`` worker 未完成时复用、已完成失败时另起（不重叠）。
        """
        backend = self._retained_backend
        if backend is None:
            return dict(self._startup_cleanup or {"converged": True, "retained": []})
        if (
            self._startup_close_call is not None
            and self._startup_close_call.finished
            and not self._startup_close_call.succeeded
        ):
            self._startup_close_call = None  # 上一轮失败：允许新一轮（不重叠）
        return self._abort_backend(backend)

    def _close_pipe_server(self, *, attempts: int = _STARTUP_CLEANUP_ATTEMPTS) -> dict[str, Any]:
        """关闭 pipe server；**不收敛时保留引用**并如实报告（O1：不再零记录丢弃）。"""
        server = self._pipe_server
        report: dict[str, Any] = {
            "attempted": server is not None,
            "converged": server is None,
            "attempts": 0,
            "detail": None,
            "errors": [],
        }
        if server is None:
            self._pipe_cleanup = report
            return report
        self._pipe_server = None
        last_report: Any = None
        for _ in range(max(1, int(attempts))):
            report["attempts"] += 1
            try:
                last_report = server.close(timeout=2.0)
            except Exception as exc:  # noqa: BLE001 - 只记类型名
                last_report = None
                report["errors"].append(type(exc).__name__)
                continue
            if bool(getattr(last_report, "converged", False)):
                report["converged"] = True
                break
        if report["converged"]:
            self._retained_pipe_server = None
        else:
            self._retained_pipe_server = server  # 保引用：可重试收敛
            report["detail"] = getattr(last_report, "detail", None)
            self._note("pipe-close-not-converged")
        self._pipe_cleanup = report
        return report

    def retry_pipe_cleanup(self, *, attempts: int = 2) -> dict[str, Any]:
        """同一进程内重试 pipe server 关闭（诊断/测试入口；不丢引用）。"""
        server = self._retained_pipe_server
        if server is None:
            return dict(self._pipe_cleanup or {"converged": True})
        report = dict(self._pipe_cleanup or {})
        for _ in range(max(1, int(attempts))):
            report["attempts"] = int(report.get("attempts", 0)) + 1
            try:
                last_report = server.close(timeout=2.0)
            except Exception as exc:  # noqa: BLE001
                report.setdefault("errors", []).append(type(exc).__name__)
                continue
            if bool(getattr(last_report, "converged", False)):
                report["converged"] = True
                report["detail"] = getattr(last_report, "detail", None)
                self._retained_pipe_server = None
                break
        self._pipe_cleanup = report
        if report.get("converged"):
            self._note("pipe-close-converged-retry")
        return report

    def _startup_cleanup_converged(self) -> bool:
        """启动失败清理是否已被证明收敛（backend + pipe 两部分都算证据）。"""
        backend_ok = bool(self._startup_cleanup.get("converged", self._retained_backend is None))
        pipe_ok = bool(self._pipe_cleanup.get("converged", self._retained_pipe_server is None))
        return backend_ok and pipe_ok

    # ------------------------------------------------------------ 状态文件
    def _status_path(self) -> Path:
        """状态文件路径（N2）：**先校验 terminal_id**并确认收敛在自有 status 目录内。

        非法 id（路径穿越/分隔符/非 ``term_`` 形状）→ ``ValueError``，调用方只做脱敏
        诊断、**不写任何派生文件**（bootstrap 失败路径同样适用，不逃逸 root）。
        """
        terminal_id = secret_store.SecretStore.validate_terminal_id(self.terminal_id)
        path = self._status_dir / f"{terminal_id}.json"
        try:
            root = self._status_dir.resolve()
            parent = Path(path).parent.resolve()
        except OSError as exc:
            raise ValueError(f"status path unresolvable: {type(exc).__name__}") from exc
        if parent != root:
            raise ValueError("status path escapes the runner status directory")
        return path

    def _status_identity(self) -> dict[str, Any]:
        """N3：绑定 runner 自身**真实** pid + raw FILETIME（字符串，跨 JS 不浮点）。

        来源 = bootstrap 时经内核读取的自身身份（``current_process_identity``）；
        未读取到（如秘密文件路径校验失败等最早失败）→ 明确 ``unknown``，**不造身份**。
        状态文件**不是存活权威**：消费者必须与 bootstrap 记录 / DPAPI 秘密交叉核验。
        """
        identity = self._own_identity
        if identity is None or identity.pid is None:
            return {
                "pid": None,
                "process_created_at_filetime": None,
                "verified": False,
                "source": "unknown",
                "authority": (
                    "not a liveness authority: identity unknown; cross-check the bootstrap "
                    "record and the DPAPI secret before using this file"
                ),
            }
        filetime = (
            str(int(identity.created_at_filetime))
            if identity.created_at_filetime is not None
            else None
        )
        return {
            "pid": int(identity.pid),
            "process_created_at_filetime": filetime,
            "verified": bool(self._secret_verified),
            "source": "current-process",
            "authority": (
                "not a liveness authority: cross-check with the bootstrap record / DPAPI "
                "secret (pid + raw FILETIME) before trusting this file"
            ),
        }

    def _status_cleanup(self) -> dict[str, Any]:
        cleanup: dict[str, Any] = {
            "converged": self._startup_cleanup_converged(),
            "startup": dict(self._startup_cleanup) if self._startup_cleanup else None,
            "pipe": dict(self._pipe_cleanup) if self._pipe_cleanup else None,
            "retained": [
                name
                for name, value in (
                    ("backend", self._retained_backend),
                    ("pipe", self._retained_pipe_server),
                )
                if value is not None
            ],
        }
        report = self._last_cleanup
        if report is not None:
            cleanup["close_ok"] = bool(report.ok)
            cleanup["terminate_result"] = report.terminate_result.value
            cleanup["state_after"] = report.state_after.value
            cleanup["tree_remaining"] = len(report.tree_remaining_pids)
            cleanup["owner_retained"] = bool(report.owner_retained)
        if self._finalize_report is not None:
            cleanup["finalize"] = dict(self._finalize_report)
        return cleanup

    def _prior_record_identity_mismatch(self, path: Path) -> bool:
        """N3：写前尽力辨认旧残留记录的 runner 身份（不同 → 标记，不可当"当前"）。"""
        try:
            if not path.is_file():
                return False
            existing = json.loads(path.read_text(encoding="utf-8"))
            prior = (existing or {}).get("runner_identity") or {}
            prior_pid = prior.get("pid")
            prior_filetime = prior.get("process_created_at_filetime")
            current = self._status_identity()
            if prior_pid is None and prior_filetime is None:
                # 旧记录没有身份块（d48/a704 形状）：无法证明同源，按不同处理。
                return True
            return bool(
                str(prior_pid) != str(current.get("pid"))
                or str(prior_filetime) != str(current.get("process_created_at_filetime"))
            )
        except Exception:  # noqa: BLE001 - 辨认失败按"不可证明同源"
            return True

    def _write_status(
        self,
        phase: str,
        *,
        exit_code: int | None = None,
        reason: str | None = None,
        cleanup: Mapping[str, Any] | None = None,
    ) -> None:
        """跨进程可观测的脱敏状态（N2/N3）：串行、唯一自有 tmp、原子 replace。

        - 路径先经 ``_status_path`` 校验（非法 id 零写，只记脱敏诊断）；
        - 失败只清**自有** tmp，cleanup 结果不吞（note 如实记录）；
        - 始终不抛（状态可观测性不影响生命周期）。
        """
        with self._status_lock:
            try:
                path = self._status_path()
            except (ValueError, OSError) as exc:
                # 非法 id / 不可解析路径：只脱敏诊断，不写任何派生文件。
                self._note("status-path-rejected", type(exc).__name__)
                return
            payload = {
                "schema_version": 1,
                "terminal_id": self.terminal_id,
                "phase": str(phase)[:32],
                "exit_code": int(exit_code) if exit_code is not None else None,
                "reason": str(reason or self._exit_reason)[:64],
                "runner_pid": os.getpid(),
                "runner_identity": self._status_identity(),
                "prior_record_identity_mismatch": self._prior_record_identity_mismatch(path),
                "cleanup": dict(cleanup if cleanup is not None else self._status_cleanup()),
                "runtime_exit": self._runtime_exit_facts(),
                "updated_at": round(time.time(), 3),
            }
            tmp = path.with_name(
                f"{path.name}.{os.getpid()}.{threading.get_ident()}.{os.urandom(4).hex()}.tmp"
            )
            try:
                self._status_dir.mkdir(parents=True, exist_ok=True)
                tmp.write_text(
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8",
                )
                os.replace(tmp, path)
            except Exception as exc:  # noqa: BLE001 - 状态可观测性失败不影响生命周期
                self._note("status-write-failed", type(exc).__name__)
            finally:
                # 只清理**自己**的 tmp；清理失败如实记录，不吞。
                if tmp.exists():
                    try:
                        tmp.unlink()
                    except OSError as exc:  # noqa: BLE001
                        self._note("status-tmp-cleanup-failed", type(exc).__name__)

    # ------------------------------------------------------------ 主流程
    def run(self) -> int:
        """同步主流程；返回退出码（见模块常量与状态文件）。"""
        self._runner_state = "bootstrapping"
        try:
            self._bootstrap()
            self._prepare()
        except RunnerBootstrapError as exc:
            self._note("bootstrap-refused", type(exc).__name__)
            self._cleanup_hello_on_failure()
            self._runner_state = "bootstrap-failed"
            return self._bootstrap_failed_exit("bootstrap-refused")
        except Exception as exc:  # noqa: BLE001 - 未预期错误也要 fail-closed
            self._note("bootstrap-error", type(exc).__name__)
            self._cleanup_hello_on_failure()
            self._runner_state = "bootstrap-failed"
            return self._bootstrap_failed_exit("bootstrap-error")

        watchdog = threading.Thread(
            target=self._watchdog_loop, name="runner-lease-watchdog", daemon=True
        )
        self._watchdog = watchdog
        watchdog.start()
        try:
            self._accept_loop()
        finally:
            self._watchdog_stop.set()
            self._close_pipe_server()
            join_info = self._join_connections()
            self._note("connections-joined", f"{join_info['joined']}+{join_info['remaining']}")
            watchdog.join(timeout=_WATCHDOG_JOIN_SECONDS)
            if watchdog.is_alive():
                # 看门狗深陷 close 等待链：不无限 join；退出码由 _finalize 依实际
                # 清理状态决定（这里只如实记录，不单独改写退出码）。
                self._note("watchdog-not-converged")
            self._finalize()
        self._set_exit_code(RUNNER_EXIT_OK)
        self._write_status(
            self._exit_status_phase(),
            exit_code=int(self._exit_code),
            reason=self._exit_reason,
        )
        self._announce_exit(int(self._exit_code))
        return int(self._exit_code)

    def _note_exit_reason(self, reason: str) -> None:
        """记录退出原因：**首个原因优先**（后续成功/失败不覆盖已确立的异常原因）。"""
        if self._exit_reason in ("", "ok"):
            self._exit_reason = str(reason)

    def _announce_exit(self, code: int) -> None:
        """公开脱敏退出原因（静态串；细节见状态文件）。非零才输出。"""
        if int(code) == RUNNER_EXIT_OK:
            return
        reason = self._exit_reason or _EXIT_REASONS.get(int(code), "unknown")
        try:
            print(f"pan-terminal-runner: exit code={int(code)} reason={reason}", file=sys.stderr)
        except Exception:  # noqa: BLE001 - 通告失败不影响退出
            pass

    def _bootstrap_failed_exit(self, reason: str) -> int:
        """启动失败：区分"已清理"与"未证明清理"（退出码 + 状态文件，R3）。"""
        converged = self._startup_cleanup_converged()
        code = RUNNER_EXIT_BOOTSTRAP_FAILED if converged else RUNNER_EXIT_CLEANUP_UNPROVEN
        self._exit_reason = "bootstrap-failed" if converged else "bootstrap-cleanup-unproven"
        self._note(reason, "converged" if converged else "cleanup-unproven")
        self._write_status("bootstrap-failed", exit_code=code, reason=self._exit_reason)
        self._announce_exit(code)
        return code

    def _exit_status_phase(self) -> str:
        code = int(self._exit_code if self._exit_code is not None else RUNNER_EXIT_OK)
        return {
            RUNNER_EXIT_OK: "exited",
            RUNNER_EXIT_CLEANUP_FAILED: "cleanup-failed",
            RUNNER_EXIT_BOOTSTRAP_FAILED: "bootstrap-failed",
            RUNNER_EXIT_CLEANUP_UNPROVEN: "cleanup-unproven",
            RUNNER_EXIT_ACCEPT_FAILED: "accept-failed",
            RUNNER_EXIT_INTERNAL: "internal",
        }.get(code, "exit")

    def _accept_loop(self) -> None:
        server = self._pipe_server
        while not self._shutdown_due():
            try:
                connection = server.accept(timeout=self._accept_timeout)
            except win_pipe.PipeCancelled:  # pragma: no cover - 本层不主动取消 accept
                continue
            except Exception as exc:  # noqa: BLE001 - 管道层失败：有界退出（非正常 stop）
                self._accept_failure = type(exc).__name__
                self._note("accept-failed", self._accept_failure)
                self._exit_reason = "accept-failed"
                # O2：accept 异常即使后续清理收敛也**不是**正常 exit 0。
                self._set_exit_code(RUNNER_EXIT_ACCEPT_FAILED)
                self._request_shutdown(RUNNER_EXIT_ACCEPT_FAILED)
                break
            if connection is None:
                self._reap_connection_threads()
                continue
            thread = threading.Thread(
                target=self._serve_connection,
                args=(connection,),
                name=f"runner-conn-{self.terminal_id}",
                daemon=True,
            )
            with self._conn_lock:
                self._conn_threads.append(thread)
            thread.start()

    def _reap_connection_threads(self) -> int:
        """回收已结束的连接线程对象（O3：列表不随连接数无界增长）。"""
        with self._conn_lock:
            before = len(self._conn_threads)
            self._conn_threads = [t for t in self._conn_threads if t.is_alive()]
            return before - len(self._conn_threads)

    def _join_connections(self, total_deadline: float = 3.0) -> dict[str, Any]:
        """在**共享总 deadline** 内 join 连接线程（非 N×2s），如实报告未收敛数。"""
        deadline = time.monotonic() + max(0.0, float(total_deadline))
        with self._conn_lock:
            threads = list(self._conn_threads)
        joined = 0
        remaining = 0
        for thread in threads:
            budget = deadline - time.monotonic()
            if budget <= 0.0:
                remaining += 1
                continue
            thread.join(timeout=budget)
            if thread.is_alive():
                remaining += 1
            else:
                joined += 1
        self._reap_connection_threads()
        return {
            "threads": len(threads),
            "joined": joined,
            "remaining": remaining,
            "deadline_seconds": float(total_deadline),
        }

    def _record_finalize(self, outcome: str, **fields: Any) -> None:
        """记录收尾结果（N4：非零未收敛与可追踪 worker 状态必须可见）。"""
        record: dict[str, Any] = {"outcome": str(outcome)}
        record.update(fields)
        worker = self._close_worker
        if worker is None:
            record["close_worker"] = {"present": False}
        else:
            record["close_worker"] = {
                "present": True,
                "in_flight": bool(worker.in_flight),
                "finished": bool(worker.finished),
                "error_type": worker.error_type,
            }
        self._finalize_report = record

    def _finalize(self) -> None:
        """主循环退出后的收尾（N4）。

        - 12s 预算是**从入口计时**的**总**预算：包含 lifecycle 锁的有界等待、
          close worker 等待与全部 join（不是"不含锁等待的收尾预算"）；
        - 有界取锁：锁忙（被其它关闭链持有）→ 不假 success、不丢 owner、
          不裸关 in-flight 句柄，记录非零未收敛 + 可追踪 worker；随后仍可重试；
        - 剩余预算逐段传递（`min(close_wait, remaining)`、worker `min(3s, remaining)`）；
          预算耗尽立即返回，**不静默延长/多次最低预算重试**；
        - 在途 close worker 复用原 worker（不重叠）。
        预算是调用方侧的有界等待，**不是 OS 原语硬 SLA**（锁获取/GIL/调度只保证
        "尽力在预算内返回"）。
        """
        started = time.monotonic()
        deadline = started + float(_FINALIZE_BUDGET_SECONDS)
        runtime = self._runtime
        if runtime is None:
            return
        if runtime.state is RuntimeState.EXITED:
            self._record_finalize("already-exited")
            self._set_exit_code(RUNNER_EXIT_OK)
            return
        if runtime.state is RuntimeState.LOST:
            # LOST = 未知所有权：绝不当成功（旧代码把 LOST 映射为 exit 0 是死代码缺陷）。
            self._note("runtime-lost")
            self._record_finalize("runtime-lost")
            self._set_exit_code(RUNNER_EXIT_CLEANUP_UNPROVEN)
            return
        worker = self._close_worker
        if worker is not None and worker.in_flight:
            budget = min(3.0, max(0.0, deadline - time.monotonic()))
            finished, result, error_type = worker.run(budget)  # 同一 worker（不重叠）
            if finished and error_type is None and isinstance(result, CleanupReport) and result.ok:
                self._record_finalize("worker-converged", waited_seconds=round(budget, 3))
                self._set_exit_code(RUNNER_EXIT_OK)
            else:
                self._record_finalize(
                    "worker-not-converged", waited_seconds=round(budget, 3),
                    finished=bool(finished), error_type=error_type,
                )
                self._set_exit_code(RUNNER_EXIT_CLEANUP_FAILED)
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0.05:
            self._note("finalize-budget-exhausted")
            self._record_finalize("budget-exhausted", remaining_seconds=round(remaining, 3))
            self._set_exit_code(RUNNER_EXIT_CLEANUP_UNPROVEN)
            return
        # 有界取锁：等锁时间计入总预算；锁忙不假 success、不丢 owner、不裸关句柄。
        lock_acquired = bool(self._lifecycle_lock.acquire(timeout=remaining))
        if not lock_acquired:
            self._note("finalize-lock-busy")
            self._record_finalize(
                "lock-busy", remaining_seconds=round(deadline - time.monotonic(), 3)
            )
            self._set_exit_code(RUNNER_EXIT_CLEANUP_FAILED)
            return
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0.05:
                self._note("finalize-budget-exhausted-after-lock")
                self._record_finalize(
                    "budget-exhausted-after-lock",
                    remaining_seconds=round(remaining, 3),
                )
                self._set_exit_code(RUNNER_EXIT_CLEANUP_UNPROVEN)
                return
            result = self.close(reason="runner-shutdown", wait=min(self._close_wait, remaining))
            status = result.get("status")
            if status == "exited":
                self._record_finalize(
                    "close-converged", waited_seconds=round(remaining, 3),
                )
                self._set_exit_code(RUNNER_EXIT_OK)
            else:
                self._record_finalize(
                    "close-not-converged", waited_seconds=round(remaining, 3), status=status,
                )
                self._set_exit_code(RUNNER_EXIT_CLEANUP_FAILED)
        finally:
            self._lifecycle_lock.release()

    # ------------------------------------------------------------ 关闭协调
    def _shutdown_due(self) -> bool:
        with self._shutdown_lock:
            if not self._shutdown_flag:
                return False
            deadline = self._shutdown_deadline
        return deadline is None or time.monotonic() >= deadline

    def _set_exit_code(self, code: int) -> None:
        """设置退出码：首个非零码优先（正常 0 不覆盖已记录的异常/未证明码）。"""
        code = int(code)
        with self._shutdown_lock:
            if self._exit_code is None or (
                self._exit_code == RUNNER_EXIT_OK and code != RUNNER_EXIT_OK
            ):
                self._exit_code = code

    def _request_shutdown(self, exit_code: int) -> None:
        """请求主循环退出；留出响应写回窗口（不立刻切断连接）。"""
        self._set_exit_code(exit_code)
        with self._shutdown_lock:
            self._shutdown_flag = True
            self._shutdown_deadline = time.monotonic() + _SHUTDOWN_GRACE_SECONDS

    # ------------------------------------------------------------ watchdog
    def _lease_snapshot(self) -> tuple[bool, float | None, int, float]:
        """读取 lease 状态快照（established, last_heartbeat, epoch, started_at）。"""
        with self._lease_lock:
            return (
                bool(self._lease_established),
                self._last_heartbeat,
                int(self._lease_epoch),
                float(self._lease_started_at),
            )

    def _lease_still_expired(self, epoch: int, *, established: bool) -> bool:
        """仲裁复核：检测时刻的 expiry 是否仍然成立（世代未变 + 仍未续约）。

        任何已处理的续约/接管都会自增 ``_lease_epoch``，因此"迟到 expiry"在取得
        关闭权之前会被本复核作废——旧 expiry 不得误杀新 lease。
        """
        with self._lease_lock:
            if int(self._lease_epoch) != int(epoch):
                return False
            if not self._lease_established:
                return not established
            last = self._last_heartbeat
        return last is not None and (time.monotonic() - last) >= self._lease_grace

    def _acquire_lease_close_right(self, epoch: int, *, established: bool) -> str:
        """**N1 线性化点**：在 lease 锁内原子完成"expiry 复核 + 取得不可撤销关闭权"。

        与 ``owner_heartbeat`` 共用同一把 ``_lease_lock``，因此两条指令流严格线性化：

        - hb 先被接受（世代自增）→ 本方法返回 ``renewed``（旧 expiry 作废）；
        - 本方法先取得关闭权（``_expiry_in_progress=True``）→ hb 在锁内看到该位，
          只能返回 ``closing``（**不得假 ok**）；
        - 关闭权取得后不可撤销（关闭链继续；失败仍走 R1 同 worker 消费/重试）。

        返回 ``granted`` / ``renewed`` / ``already``。
        """
        with self._lease_lock:
            if self._expiry_in_progress:
                return "already"
            if int(self._lease_epoch) != int(epoch):
                return "renewed"
            if bool(self._lease_established) != bool(established):
                return "renewed"
            now = time.monotonic()
            if established:
                last = self._last_heartbeat
                if last is None or (now - last) < self._lease_grace:
                    return "renewed"
            elif (now - float(self._lease_started_at)) < self._bootstrap_grace:
                return "renewed"
            self._expiry_in_progress = True
            return "granted"

    def _runtime_exit_facts(self) -> dict[str, Any] | None:
        if self._runtime is None:
            return None
        try:
            info = self._runtime.poll_exit()
            return {
                "seen": info.process_exit_seen is True,
                "code": info.code,
                "reader_done": info.reader_done is True,
                "output_complete": getattr(info, "output_complete", None),
            }
        except Exception:
            return None

    def _natural_exit_tick(self, *, now: float | None = None) -> bool:
        """Retained root death + finished drain, not pipe EOF or owner lease loss.

        Allow five seconds for active readers to fetch tail output. The service
        may finish sooner after consuming the final empty page. Cleanup always
        uses the existing single-flight runtime close and whole-Job proof.
        """
        facts = self._runtime_exit_facts()
        if not facts or facts["seen"] is not True or facts["reader_done"] is not True:
            return False
        code = facts["code"]
        if type(code) is not int or not 0 <= code <= 0xFFFFFFFF:
            return False
        now = time.monotonic() if now is None else now
        if self._natural_exit_ready_at is None:
            self._natural_exit_ready_at = now
        if now - self._natural_exit_ready_at < 5.0:
            return False
        result = self.close(reason="natural-exit", wait=0.25)
        if result.get("status") == "exited":
            return True
        # A blocked worker is retained and reused; never start overlapping closes.
        if now - self._natural_exit_ready_at >= 17.0:
            self._note("natural-cleanup-exhausted")
            self._runner_state = "cleanup-failed"
            self._exit_reason = "natural-cleanup-exhausted"
            self._request_shutdown(RUNNER_EXIT_CLEANUP_FAILED)
            return True
        return False

    def _watchdog_loop(self) -> None:
        """lease 看门狗：只读时间戳 + 触发 close worker（与 handler 互不拖死）。

        R1：**不再因 ``_closing`` 跳过**——显式 close 未收敛/失败后若 Pan 断开，
        watchdog 仍负责消费/追踪**同一** close worker 的有界结果并重试；
        R2：detach 由同一生命周期门仲裁（在 ``close(source="lease")`` 内复核）。
        """
        while not self._watchdog_stop.wait(0.05):
            if self._shutdown_flag:
                return
            if self._runtime is None:
                continue
            if self._natural_exit_tick():
                return
            established, last, epoch, started = self._lease_snapshot()
            now = time.monotonic()
            if established:
                expired = last is not None and (now - last) >= self._lease_grace
            else:
                expired = (now - started) >= self._bootstrap_grace
                if expired:
                    self._note("owner-absent")
            if not expired or self._detached:
                continue
            outcome = self._lease_expired_close(epoch=epoch, established=established)
            if outcome in ("exited", "exhausted"):
                return
            # "skipped"（迟到 detach/迟到心跳）：继续看门，等下一次判定。

    def _lease_expired_close(self, *, epoch: int, established: bool) -> str:
        """死期自停（R1/R2/N1 仲裁）：返回 ``exited`` / ``skipped`` / ``exhausted``。

        - N1：**先在 lease 锁内原子取得不可撤销关闭权**（复核与置位同临界区），
          与 hb 线性化：hb 先 → `renewed` 作废；本方法先 → hb 只能 `closing`；
        - 与 detach 在同一生命周期门仲裁：expiry 先取得关闭权 → detach 拒绝；
          detach 先完成 → `close(source="lease")` 锁内复核后跳过（不杀 detached）；
        - 显式 close 在途/失败时：等待并消费**同一** close worker（不叠加），
          未收敛则有界重试（重试只在 worker 已结束时另起，不重叠）。
        """
        grant = self._acquire_lease_close_right(epoch, established=established)
        if grant != "granted":
            return "skipped"  # renewed（hb 先）/ already（本进程仅 watchdog 单线程）
        result = self.close(
            reason="lease-expired",
            source="lease",
            lease_epoch=epoch,
            lease_established=established,
        )
        status = result.get("status")
        if status == "exited":
            return "exited"
        if status in ("lease-skipped-detached", "lease-skipped-renewed"):
            return "skipped"
        for attempt in range(1, self._lease_cleanup_retries + 1):
            if self._watchdog_stop.wait(_LEASE_CLEANUP_RETRY_INTERVAL):
                return "skipped"
            if self._detached:
                return "skipped"
            if not self._lease_still_expired(epoch, established=established):
                return "skipped"  # 防御性复核（关闭权取得后 hb 已不可续约）
            result = self.close(
                reason=f"lease-expired-retry{attempt}",
                source="lease",
                lease_epoch=epoch,
                lease_established=established,
            )
            status = result.get("status")
            if status == "exited":
                return "exited"
            if status in ("lease-skipped-detached", "lease-skipped-renewed"):
                return "skipped"
        self._note("lease-cleanup-exhausted")
        self._runner_state = "cleanup-failed"
        self._exit_reason = "lease-cleanup-exhausted"
        self._request_shutdown(RUNNER_EXIT_CLEANUP_FAILED)
        return "exhausted"

    # ------------------------------------------------------------ 连接服务
    def _serve_connection(self, connection: Any) -> None:
        """每连接一个线程：认证 → RequestScheduler（固化期限）→ handler。"""
        token = self._token
        if token is None:  # pragma: no cover - run() 保证 token 已就绪
            connection.close(timeout=1.0)
            return
        session = ipc.IpcSession(
            connection,
            role="server",
            token=token,
            terminal_id=self.terminal_id,
            local_identity=self._own_identity,
            identity_probe=win_pipe.default_identity_probe,
            server_id="runner",
        )
        with self._conn_lock:
            self._conn_active += 1
        try:
            try:
                session.handshake()
            except Exception as exc:  # noqa: BLE001 - 认证失败：连接关闭、runtime 不动
                self._note("auth-rejected", type(exc).__name__)
                return
            with self._conn_lock:
                self._conn_accepted += 1
            self._note("authenticated")
            scheduler = ipc.RequestScheduler(max_operations=_CONNECTION_QUEUE)
            while True:
                with self._shutdown_lock:
                    deadline = self._shutdown_deadline if self._shutdown_flag else None
                if deadline is not None and time.monotonic() >= deadline:
                    break
                remaining = _RECV_POLL_SECONDS if deadline is None else min(
                    _RECV_POLL_SECONDS, max(0.0, deadline - time.monotonic())
                )
                try:
                    message = session.recv_message(timeout=remaining)
                except (ipc.TransportClosedError, EOFError, OSError):
                    break
                except ipc.TransportTimeout:
                    continue  # 有界读超时：连接保留，继续等下一帧
                except ipc.ProtocolError:
                    self._note("protocol-violation")
                    break
                if message is None:
                    continue
                if message.get("type") != ipc.MessageType.REQUEST.value:
                    continue
                outcome = scheduler.submit(message)
                if outcome is not ipc.SubmitOutcome.ACCEPTED:
                    reason = (
                        "expired"
                        if outcome is ipc.SubmitOutcome.REJECTED_EXPIRED
                        else "queue-full"
                    )
                    session.transport.send_frame(
                        ipc.build_error(reason, request_id=message.get("request_id"), secrets_=(token,))
                    )
                    continue
                claimed = scheduler.claim()
                if claimed is None:
                    continue
                try:
                    response = session.run_handler(claimed, self.handle)
                except Exception as exc:  # noqa: BLE001 - 分发/构造失败不拖死连接
                    self._note("dispatch-error", type(exc).__name__)
                    response = ipc.build_error(
                        "handler-error", request_id=claimed.request_id, secrets_=(token,)
                    )
                session.transport.send_frame(response)
                payload = response.get("payload") or {}
                if (
                    (claimed.get("payload") or {}).get("op") == ipc.OP_STOP
                    and payload.get("status") == "exited"
                ):
                    break  # 已停：本连接先结束，保证响应已写回
        finally:
            with self._conn_lock:
                self._conn_active -= 1
            try:
                connection.close(timeout=2.0)
            except Exception as exc:  # noqa: BLE001
                self._note("connection-close-failed", type(exc).__name__)

    # ------------------------------------------------------------ IPC 分发
    def handle(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """唯一 handler 入口（``IpcSession.run_handler`` 之内，认证/绑定已校验）。"""
        payload = request.get("payload") or {}
        op = payload.get("op")
        if op == ipc.OP_READ:
            return self._op_read(payload)
        if op == ipc.OP_INPUT:
            return self._op_input(payload)
        if op == ipc.OP_RESIZE:
            return self._op_resize(payload)
        if op == ipc.OP_SNAPSHOT:
            return self._op_snapshot(payload)
        if op == ipc.OP_LEASE:
            return self._op_lease(payload)
        if op == ipc.OP_STOP:
            return self._op_stop(payload)
        return self._payload("unsupported-op")  # pragma: no cover - schema 已拒绝

    # ------------------------------------------------------------ 命令实现
    def describe(self) -> dict[str, Any]:
        runtime = self._runtime
        try:
            capability = self._durability_probe()
        except Exception as exc:  # noqa: BLE001 - 能力未知 = 不可 durable
            capability = DurabilityCapability(
                durable_capable=False,
                ambient_job=True,
                detail=f"durability probe failed: {type(exc).__name__}",
            )
        with self._lease_lock:
            lease = {
                "established": self._lease_established,
                "owner": self._owner_client_id,
                "generation": self._owner_generation,
                "age_ms": None
                if self._last_heartbeat is None
                else int(max(0.0, time.monotonic() - self._last_heartbeat) * 1000),
                "grace_ms": int(self._lease_grace * 1000),
            }
        with self._worker_lock:
            write_worker = self._write_worker
            input_worker = {
                "in_flight": bool(write_worker is not None and write_worker.in_flight),
                "completed": self._write_completed,
                "last_status": self._last_input_status,
            }
            close_worker = {
                "in_flight": bool(self._close_worker is not None and self._close_worker.in_flight),
                "finished": bool(self._close_worker is not None and self._close_worker.finished),
                "error_type": (
                    self._close_worker.error_type if self._close_worker is not None else None
                ),
            }
        with self._conn_lock:
            connections = {"accepted": self._conn_accepted, "active": self._conn_active}
        identity = getattr(runtime, "identity", None) if runtime is not None else None
        exit_info = None
        cleanup = None
        consumer = {"failed": False, "count": 0, "gap_seen": False}
        state = self._runner_state
        if runtime is not None:
            try:
                info = runtime.poll_exit()
                exit_info = {
                    "seen": bool(info.process_exit_seen),
                    "code": info.code,
                    "reason": info.reason,
                    "reader_done": bool(info.reader_done),
                    "output_complete": info.output_complete,
                    "bytes_at_stop": info.bytes_at_stop,
                    "drain_stop_reason": info.drain_stop_reason.value,
                }
            except Exception as exc:  # noqa: BLE001 - 句柄释放后探针可能不可用
                exit_info = {"seen": None, "probe_error": type(exc).__name__}
            state = self._runner_state
            consumer = {
                "failed": bool(runtime.consumer_failed),
                "count": int(runtime.consumer_failure_count),
                "gap_seen": bool(self._bridge.gap_seen) if self._bridge is not None else False,
            }
            if self._last_cleanup is not None:
                report = self._last_cleanup
                cleanup = {
                    "state_after": report.state_after.value,
                    "ok": bool(report.ok),
                    "seconds": report.seconds,
                    "terminate_result": report.terminate_result.value,
                    "remaining": len(report.tree_remaining_pids),
                }
        snapshot_meta = snapshot_capability(self._emulator, self._bridge)
        lifecycle = {
            "closing": bool(self._closing),
            "finalize_outcome": (self._finalize_report or {}).get("outcome"),
            "expiry_in_progress": bool(self._expiry_in_progress),
            "expiry_reason": self._expiry_reason,
            "exit_reason": self._exit_reason,
            "accept_failure": self._accept_failure,
            "startup_cleanup_converged": self._startup_cleanup_converged(),
        }
        return {
            "schema_version": 1,
            "terminal_id": self.terminal_id,
            "runner_state": state,
            "runtime_state": runtime.state.value if runtime is not None else None,
            "pid": identity.pid if identity is not None else None,
            "process_created_at_filetime": (
                str(identity.created_at_filetime)
                if identity is not None and identity.created_at_filetime is not None
                else None
            ),
            "rows": int(runtime.rows) if runtime is not None else int(self._rows),
            "cols": int(runtime.cols) if runtime is not None else int(self._cols),
            "detached": bool(self._detached),
            "durability": capability.as_dict(),
            "lease": lease,
            "lifecycle": lifecycle,
            "exit": exit_info,
            "cleanup": cleanup,
            "consumer": consumer,
            "snapshot": snapshot_meta,
            "input_worker": input_worker,
            "close_worker": close_worker,
            "connections": connections,
            "observers": self._observer_registrations,
        }

    def _detail_json(self) -> str:
        try:
            payload = self.describe()
        except Exception as exc:  # noqa: BLE001 - 序列化失败不允许拖死响应
            payload = {"schema_version": 1, "status": "detail-error", "error_type": type(exc).__name__}
        return _bounded_json(payload, _DETAIL_BUDGET)

    def _payload(
        self,
        status: str,
        *,
        ok: bool | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """构造响应 payload：**只允许冻结白名单字段**；未知字段自动折进 ``detail``。

        防御性设计：ipc 响应白名单是冻结的，误把新字段放顶层会让 ``build_response``
        抛 ``MalformedFrameError``（整条请求变 handler-error）。这里提前收敛，
        保证字段变化只能表现为 detail 内的信息，不会破坏帧协议。
        """
        out: dict[str, Any] = {"status": status}
        if ok is not None:
            out["ok"] = bool(ok)
        detail_value: str | None = None
        folded: dict[str, Any] = {}
        if extra:
            for key, value in extra.items():
                if key in _RESPONSE_FIELDS:
                    if key == "detail":
                        detail_value = value if isinstance(value, str) else None
                    else:
                        out[key] = value
                else:
                    folded[key] = value
        if folded:
            base: dict[str, Any] = {}
            if detail_value:
                try:
                    parsed = json.loads(detail_value)
                    if isinstance(parsed, dict):
                        base = parsed
                except json.JSONDecodeError:
                    base = {}
            base.update(folded)
            detail_value = _bounded_json(base, _DETAIL_BUDGET)
        if detail_value is None:
            detail_value = self._detail_json()
        # detail 必须是**合法且有界**的 JSON：来源均已过 _bounded_json（字段级缩减）。
        out["detail"] = detail_value
        return out

    # ---- read / describe
    def _op_read(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        runtime = self._runtime
        if runtime is None:
            return self._payload("starting", ok=False)
        try:
            cursor = ipc.payload_int(payload, "cursor", default=0)
            max_bytes = ipc.payload_int(
                payload, "max_bytes", default=ipc.DEFAULT_MAX_PAYLOAD_BYTES
            )
        except ipc.ProtocolError:
            return self._payload("invalid-request", ok=False)
        try:
            page = runtime.read_from(cursor, max_bytes=int(max_bytes))
        except InvalidCursorError:
            return self._payload("invalid-cursor", ok=False)
        data = b"".join(chunk.data for chunk in page.chunks)
        seq = page.chunks[0].seq if page.chunks else int(cursor)
        return self._payload(
            self._runner_state,
            ok=True,
            extra={
                "data_b64": ipc.encode_payload_bytes(data),
                "seq": int(seq),
                "size": len(data),
                "cursor": int(cursor),
                "next_cursor": int(page.next_cursor),
                "gap": list(page.gap) if page.gap is not None else None,
                "truncated": bool(page.truncated),
                "total_bytes": int(runtime.log.total_bytes),
                "first_retained_seq": int(runtime.log.first_retained_seq),
            },
        )

    def read(self, cursor: int, *, max_bytes: int | None = None) -> dict[str, Any]:
        """进程内命令（与线上 ``read`` 同一实现）。"""
        normalized: dict[str, Any] = {"op": ipc.OP_READ}
        normalized["cursor"] = ipc.encode_exact_int(int(cursor))
        if max_bytes is None:
            normalized["max_bytes"] = ipc.DEFAULT_MAX_PAYLOAD_BYTES
        else:
            normalized["max_bytes"] = int(max_bytes)
        return self._op_read(normalized)

    # ---- input（有界 worker）
    def _op_input(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        try:
            data = ipc.parse_b64_bytes(payload.get("data_b64"))
            seq = (
                ipc.payload_int(payload, "seq")
                if "seq" in payload
                else None
            )
        except ipc.ProtocolError:
            return self._payload("invalid-request", ok=False)
        return self.input(data, seq=seq)

    def input(self, data: bytes, *, seq: int | None = None) -> dict[str, Any]:
        """输入：交给可追踪 write worker，有界等待；不叠加、不重复启动。"""
        runtime = self._runtime
        if runtime is None:
            return self._payload("starting", ok=False)
        data = bytes(data)
        if not data:
            return self._payload("noop", ok=True, extra={"size": 0})
        with self._worker_lock:
            if self._closing or self._runner_state in ("closing", "exited", "cleanup-failed"):
                worker = None
                refusal = "closing"
            else:
                worker = self._write_worker
                if worker is not None and worker.in_flight:
                    # 同一时刻只有一个在途 write：不排队、不重复启动同一阻塞调用。
                    worker = None
                    refusal = "busy"
                else:
                    if worker is None:
                        worker = _TrackedCall(self._write_call(data))
                        self._write_worker = worker
                    else:
                        # 上一轮已结束：绑定新数据开始新一轮（仍不重叠）。
                        worker.reset(self._write_call(data))
                    self._last_input_status = "in-flight"
                    refusal = None
        if worker is None:
            return self._payload(refusal, ok=False)
        finished, result, error_type = worker.run(self._input_ack_wait)
        if not finished:
            return self._payload("accepted-in-flight", ok=False, extra={"size": 0})
        with self._worker_lock:
            self._write_completed += 1
            if error_type is not None:
                self._last_input_status = f"error:{error_type}"
                status, written = "write-failed", 0
            else:
                status, written = result
                self._last_input_status = str(status)
        extra: dict[str, Any] = {"size": int(written)}
        if seq is not None:
            extra["seq"] = int(seq)
        return self._payload(str(status), ok=(status == "done"), extra=extra)

    def _write_call(self, data: bytes) -> Callable[[], tuple[str, int]]:
        hook = self._write_hook

        def _call() -> tuple[str, int]:
            try:
                if hook is not None:  # 测试/诊断注入：阻塞行为在此线程内发生
                    written = hook(data)
                else:
                    written = self._runtime.write(data)
                return ("done", int(written))
            except Exception as exc:  # noqa: BLE001 - 结果类型化，不落消息
                return (f"rejected:{type(exc).__name__}", 0)

        return _call

    # ---- resize（同一有序通道；不假称已同步）
    def _op_resize(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        try:
            rows = ipc.payload_int(payload, "rows")
            cols = ipc.payload_int(payload, "cols")
        except ipc.ProtocolError:
            return self._payload("invalid-request", ok=False)
        return self.resize(rows, cols)

    def resize(self, rows: int, cols: int) -> dict[str, Any]:
        runtime = self._runtime
        if runtime is None:
            return self._payload("starting", ok=False)
        rows, cols = int(rows), int(cols)
        if not runtime.resize(rows, cols):
            return self._payload("rejected", ok=False, extra={"rows": rows, "cols": cols})
        emulator_result = "none"
        if self._bridge is not None:
            try:
                self._bridge.resize(rows, cols)
                emulator_result = "ok"
            except Exception as exc:  # noqa: BLE001 - 只记类型名
                emulator_result = f"error:{type(exc).__name__}"
                self._note("emulator-resize-failed", type(exc).__name__)
        return self._payload(
            "ok",
            ok=True,
            extra={
                "rows": rows,
                "cols": cols,
                "detail": self._detail_with(
                    {"pty_resize": "ok", "emulator_resize": emulator_result}
                ),
            },
        )

    # ---- snapshot（协议 A；无引擎显式 unavailable）
    def _op_snapshot(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        try:
            timeout_ms = ipc.payload_int(payload, "timeout_ms", default=5000)
        except ipc.ProtocolError:
            return self._payload("invalid-request", ok=False)
        return self.snapshot(timeout_ms=int(timeout_ms))

    def snapshot(self, *, timeout_ms: int = 5000) -> dict[str, Any]:
        runtime = self._runtime
        if runtime is None:
            return self._payload("starting", ok=False)
        timeout = min(max(int(timeout_ms) / 1000.0, 0.001), 60.0)
        if self._emulator is None:
            return self._payload(
                "unavailable",
                ok=False,
                extra={
                    "detail": self._detail_with(
                        {
                            "fidelity": Fidelity.UNAVAILABLE.value,
                            "recovery": Recovery.NONE.value,
                            "engine": "none",
                            "note": (
                                "authoritative emulator not injected: no snapshot; "
                                "fresh view required (cursor is not an applied position)"
                            ),
                            **self._emulator_snapshot_fields(None),
                        }
                    )
                },
            )
        try:
            snap = self._emulator.snapshot(timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - barrier 超时/失败：显式降级
            self._note("snapshot-failed", type(exc).__name__)
            return self._payload(
                "degraded",
                ok=False,
                extra={
                    "detail": self._detail_with(
                        {
                            "fidelity": Fidelity.PARTIAL.value,
                            "recovery": Recovery.DEGRADED.value,
                            "engine": "injected",
                            "note": f"snapshot barrier failed: {type(exc).__name__}",
                            **self._emulator_snapshot_fields(None),
                        }
                    )
                },
            )
        snap = self._downgrade_snapshot(snap)
        serialized = snap.serialized_screen.encode("utf-8")
        if len(serialized) > ipc.DEFAULT_MAX_PAYLOAD_BYTES:
            return self._payload("degraded", ok=False, extra={"detail": self._detail_with({
                "fidelity": snap.fidelity.value,
                "recovery": Recovery.DEGRADED.value,
                "engine": snap.engine,
                "note": "serialized screen exceeds wire payload budget",
                # 本响应未交付可续流载荷：一致性不可证明 → 保守 unknown。
                **self._emulator_snapshot_fields(None),
            })})
        return self._payload(
            self._runner_state,
            ok=True,
            extra={
                "data_b64": ipc.encode_payload_bytes(serialized),
                "size": len(serialized),
                "cursor": int(snap.cursor),
                "rows": _clamp_dimension(snap.rows, runtime.rows),
                "cols": _clamp_dimension(snap.cols, runtime.cols),
                "detail": self._detail_with(
                    {
                        "fidelity": snap.fidelity.value,
                        "recovery": snap.recovery.value,
                        "feed_lag": bool(snap.feed_lag),
                        "engine": snap.engine,
                        "note": snap.note,
                        "protocol": "A",
                        **self._emulator_snapshot_fields(snap),
                    }
                ),
            },
        )

    def _emulator_snapshot_fields(self, snap: AppliedSnapshot | None) -> dict[str, Any]:
        """F5 机器确认字段（detail 内）：``cursors_valid`` / ``reset_unconfirmed``。

        **保守近似（非"一致可证明"）**：本函数以"轻量来源当前值 + 本次载荷属性"做
        保守近似；runner 没有与快照历史世代的原子绑定（来源值在快照之后读取），
        因此任何不能支持的 True 一律回落 ``None``（unknown）。

        - **严格类型**：两个来源只接受**真正 bool**（``isinstance(v, bool)``）；真值型
          非 bool（str/int/...）→ ``None``（unknown），**不做 bool 强转**。
        - 来源契约：``cursors_valid=True`` 的来源须**自含 reset 未确认判定**（真实引擎
          的 ``cursors_valid`` 属性在 reset 未确认时为 False）；文档 §4.4 写明。
        - **baseline 交叉（同一次 diagnostics 读取）**：取 ``baseline_cursor``（仅合法
          int 非 bool 或**十进制字符串**参与比较，未知/非法**不造值**）；若
          ``baseline > snap.cursor`` 说明快照早于最近一次已确认 reset → ``None``。
        - **来源需无 IO、有界短临界区**；runner **不隔离慢源**，且客户端 ``timeout_ms``
          **不包含**确认面额外延迟（doc §4.4 写明属调用方契约）。
        - 读取失败/异常 → ``None``（不默认有效）；任何异常只记类型名，异常文本不进入
          响应（防泄漏）。
        """
        fields: dict[str, Any] = {"cursors_valid": None, "reset_unconfirmed": None}
        emulator = self._emulator
        if emulator is None:
            return fields
        raw_valid: Any = None
        try:
            raw_valid = getattr(emulator, "cursors_valid", None)
        except Exception as exc:  # noqa: BLE001 - 探测失败 = unknown
            raw_valid = None
            self._note("cursors-valid-probe-failed", type(exc).__name__)
        raw_reset: Any = None
        raw_baseline: Any = None
        try:
            diagnostics_fn = getattr(emulator, "diagnostics", None)
            diagnostics = diagnostics_fn() if callable(diagnostics_fn) else None
            if isinstance(diagnostics, Mapping):
                raw_reset = diagnostics.get("reset_unconfirmed")
                raw_baseline = diagnostics.get("baseline_cursor")
        except Exception as exc:  # noqa: BLE001 - 探测失败 = unknown
            raw_reset = None
            raw_baseline = None
            self._note("reset-probe-failed", type(exc).__name__)

        valid_flag = raw_valid if isinstance(raw_valid, bool) else None
        reset_unconfirmed = raw_reset if isinstance(raw_reset, bool) else None
        baseline_cursor = _ledger_int_or_none(raw_baseline)
        fields["reset_unconfirmed"] = reset_unconfirmed
        if valid_flag is None:
            return fields
        if valid_flag is False:
            fields["cursors_valid"] = False
            return fields
        consistent = bool(
            snap is not None
            and snap.recovery in (Recovery.FULL, Recovery.PARTIAL)
            and not bool(snap.feed_lag)
            and reset_unconfirmed is not True
        )
        if consistent and baseline_cursor is not None and snap is not None:
            if baseline_cursor > int(snap.cursor):
                # 快照早于最近一次已确认 reset 基线（stale）→ 保守 unknown。
                consistent = False
        fields["cursors_valid"] = True if consistent else None
        return fields

    def _downgrade_snapshot(self, snap: AppliedSnapshot) -> AppliedSnapshot:
        """粘滞降级：缺口/feed_lag/消费者失败 → 禁止 full（诚实性约束）。"""
        lag = bool(snap.feed_lag)
        reasons: list[str] = []
        if lag:
            reasons.append("feed_lag")
        if self._bridge is not None and self._bridge.gap_seen:
            lag = True
            reasons.append("consumer-gap")
        if self._runtime is not None and self._runtime.consumer_failed:
            lag = True
            reasons.append("consumer-failed")
        emulator_lag = bool(getattr(self._emulator, "feed_lag", False))
        if emulator_lag:
            lag = True
            reasons.append("emulator-feed-lag")
        if not reasons:
            return snap
        recovery = snap.recovery
        if recovery in (Recovery.FULL, Recovery.PARTIAL):
            recovery = Recovery.DEGRADED
        note = snap.note
        addition = "degraded: " + ",".join(reasons)
        note = f"{note} | {addition}" if note else addition
        return replace(snap, recovery=recovery, feed_lag=lag, note=note[:512])

    # ---- lease（owner 心跳 / observer 登记）
    def _op_lease(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        try:
            client_id = str(payload.get("client_id"))
            role = payload.get("role")
            generation = (
                ipc.payload_int(payload, "generation") if "generation" in payload else None
            )
        except ipc.ProtocolError:
            return self._payload("invalid-request", ok=False)
        if role == LEASE_ROLE_CONTROL:
            return self.owner_heartbeat(client_id, generation=generation)
        if role == LEASE_ROLE_OBSERVER:
            return self.register_observer(client_id)
        return self._payload("invalid-request", ok=False)

    def owner_heartbeat(self, client_id: str, *, generation: int | None = None) -> dict[str, Any]:
        """已认证 Pan 所有者的租约续约/接管（唯一续约路径）。

        **N1 线性化**：与 expiry 的关闭权取得共用同一把 ``_lease_lock``——
        若关闭权已被取得（``_expiry_in_progress``，或在关闭/已退出），本方法在锁内
        直接返回 ``closing``（**不假 ok**）；否则续约/接管与世代自增在同一临界区完成，
        此后任何"旧 expiry"的取得尝试都会因世代变化而作废。两条指令流因此严格线性化。
        """
        client_id = str(client_id)
        with self._lease_lock:
            if (
                self._expiry_in_progress
                or self._closing
                or self._runner_state in ("exited",)
            ):
                status = "closing"
            else:
                previous = self._owner_client_id
                self._owner_client_id = client_id
                if previous != client_id:
                    if generation is not None:
                        self._owner_generation = int(generation)
                    else:
                        self._owner_generation = self._owner_generation + 1
                    self._note("owner-changed")
                elif generation is not None:
                    self._owner_generation = int(generation)
                self._last_heartbeat = time.monotonic()
                self._lease_established = True
                # 世代自增：任何已处理的（续约/接管）心跳都让旧 expiry 判定作废。
                self._lease_epoch += 1
                status = "ok"
        if status == "closing":
            return self._payload("closing", ok=False)
        return self._payload("ok", ok=True)

    def register_observer(self, client_id: str) -> dict[str, Any]:
        """observer 连接登记：**不**续约、**不**改变 runtime（浏览器连接不入 runtime）。"""
        with self._conn_lock:
            self._observer_registrations += 1
        return self._payload("ok", ok=True, extra={"reason": "observer-registered"})

    # ---- detach（能力不足显式拒绝）
    def detach(self, *, reason: str = "detach") -> dict[str, Any]:
        with self._lifecycle_lock:
            runtime = self._runtime
            if self._detached:
                return self._payload("already-detached", ok=True)
            # R2 仲裁：lease-expiry 已取得关闭权（或正在关闭）时，detach 明确拒绝
            # （零状态变化；不假装成功，也不与关闭路径竞态）。
            if self._expiry_in_progress:
                self._note("detach-refused-lease-close")
                return self._payload(
                    "detach-refused",
                    ok=False,
                    extra={
                        "detail": self._detail_with(
                            {
                                "detach": "refused",
                                "reason": "lease-close-in-progress",
                                "expiry_reason": self._expiry_reason,
                            }
                        )
                    },
                )
            if self._closing:
                return self._payload(
                    "detach-refused",
                    ok=False,
                    extra={
                        "detail": self._detail_with(
                            {"detach": "refused", "reason": "close-in-progress"}
                        )
                    },
                )
            if runtime is None or runtime.state is not RuntimeState.RUNNING:
                return self._payload("rejected", ok=False)
            try:
                capability = self._durability_probe()
            except Exception as exc:  # noqa: BLE001
                capability = DurabilityCapability(
                    durable_capable=False,
                    ambient_job=True,
                    detail=f"durability probe failed: {type(exc).__name__}",
                )
            if not capability.durable_capable:
                self._note("detach-refused")
                return self._payload(
                    "detach-refused",
                    ok=False,
                    extra={
                        "detail": self._detail_with(
                            {
                                "detach": "refused",
                                "durability": capability.as_dict(),
                            }
                        )
                    },
                )
            try:
                report: DetachReport = runtime.detach()
            except DetachUnsupportedError:
                return self._payload("detach-refused", ok=False)
            except IllegalStateTransition:
                return self._payload("rejected", ok=False)
            self._detached = True
            self._runner_state = "detached"
            self._note("detached")
        return self._payload(
            "detached",
            ok=True,
            extra={
                "reason": "detached",
                "detail": self._detail_with(
                    {
                        "detach": "detached",
                        "detached_at": report.detached_at,
                        "durable_owner": report.durable_owner,
                        "reconnect_hint": report.reconnect_hint,
                    }
                ),
            },
        )

    # ---- close（有界 worker；失败保 owner 可重试）
    def _op_stop(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        reason = payload.get("reason") or "explicit-close"
        if reason == "detach":
            return self.detach(reason=str(reason))
        return self.close(reason=str(reason))

    def close(
        self,
        *,
        reason: str = "explicit-close",
        source: str = "explicit",
        lease_epoch: int | None = None,
        lease_established: bool | None = None,
        wait: float | None = None,
    ) -> dict[str, Any]:
        """生命周期关闭（唯一 close worker；失败保 owner 可重试）。

        ``source``：``"explicit"``（用户/服务显式关闭，**detached 也照常终止**）或
        ``"lease"``（死期自停；在同一生命周期门内复核 detach 与 lease 世代，
        见 ``_lease_expired_close``）。``lease_epoch``/``lease_established`` 是
        expiry 检测时刻的世代快照（仅 lease 来源使用）。``wait`` 覆盖本次有界
        等待（默认 ``close_wait``；收尾预算不足时用于收窄）。
        """
        reason = str(reason or "explicit-close")[:64]
        budget = self._close_wait if wait is None else max(0.0, float(wait))
        with self._lifecycle_lock:
            if source == "lease":
                # R2 仲裁点：与 detach 共用同一把生命周期锁。
                if self._detached:
                    return self._payload("lease-skipped-detached", ok=True)
                if (
                    not self._expiry_in_progress
                    and lease_epoch is not None
                    and not self._lease_still_expired(
                        int(lease_epoch), established=bool(lease_established)
                    )
                ):
                    # 兼容直接调用：关闭权尚未取得且世代已更新 → 作废（N1 后
                    # ``_lease_expired_close`` 已在 lease 锁内原子取得关闭权，
                    # 此处不会触发）。
                    return self._payload("lease-skipped-renewed", ok=True)
                self._expiry_in_progress = True
                self._expiry_reason = reason
            if self._runner_state == "exited":
                return self._payload("exited", ok=True)
            self._closing = True
            runtime = self._runtime
            if runtime is None:
                self._runner_state = "exited"
                self._note_exit_reason("exited")
                self._request_shutdown(RUNNER_EXIT_OK)
                return self._payload("exited", ok=True)
            worker = self._close_worker
            late_success = (
                worker is not None
                and worker.finished
                and worker.succeeded
                and isinstance(worker.result, CleanupReport)
                and worker.result.ok
            )
            if late_success:
                # 消费迟到成功：worker 已完成且收敛，不重复发起同一阻塞调用
                # （R1："追踪同一 close worker 结果"）。
                finished, result, error_type = True, worker.result, None
            else:
                if worker is None or worker.finished:
                    self._close_worker = worker = _TrackedCall(
                        lambda reason=reason: runtime.close(reason=reason)
                    )
                finished, result, error_type = worker.run(budget)
        if not finished:
            self._runner_state = "closing"
            return self._payload("closing", ok=False)
        if error_type is not None:
            self._runner_state = "cleanup-failed"
            self._note_exit_reason("cleanup-failed")
            self._note("close-worker-error", error_type)
            return self._payload("cleanup-failed", ok=False)
        if not isinstance(result, CleanupReport):
            # 后端返回非法结果：按清理未证明处理（fail-closed，不假设成功）。
            self._runner_state = "cleanup-failed"
            self._note_exit_reason("cleanup-unproven")
            self._note("close-result-invalid")
            return self._payload("cleanup-failed", ok=False)
        report: CleanupReport = result
        self._last_cleanup = report
        if report.ok:
            self._runner_state = "exited"
            self._note_exit_reason("exited")
            self._note("exited")
            self._request_shutdown(RUNNER_EXIT_OK)
            return self._payload("exited", ok=True)
        # 失败不释放 owner：runtime 保 cleanup-failed、句柄/进程证据仍在，可重试。
        self._runner_state = "cleanup-failed"
        self._note_exit_reason("cleanup-failed")
        self._note("cleanup-failed")
        return self._payload("cleanup-failed", ok=False)

    # ---- detail 辅助
    def _detail_with(self, extra: Mapping[str, Any]) -> str:
        """在 describe 摘要之上叠加字段（**合法且有界** JSON：字段级缩减）。"""
        try:
            parsed = json.loads(self._detail_json())
            base: dict[str, Any] = parsed if isinstance(parsed, dict) else {"schema_version": 1}
        except Exception:  # noqa: BLE001
            base = {"schema_version": 1}
        base.update(dict(extra))
        return _bounded_json(base, _DETAIL_BUDGET)


def _clamp_dimension(value: Any, fallback: int) -> int:
    """把尺寸收敛到 IPC 白名单区间（[1,512]）；越界回退到 runtime 实际尺寸。"""
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = 0
    if 1 <= number <= 512:
        return number
    fallback_number = int(fallback) if fallback else 0
    return fallback_number if 1 <= fallback_number <= 512 else 24


def snapshot_capability(
    emulator: AuthoritativeEmulator | None, bridge: RunnerEmulatorBridge | None
) -> dict[str, Any]:
    """describe 的 snapshot 段：如实报告引擎与降级位（不承诺完整恢复）。"""
    if emulator is None:
        return {
            "engine": "none",
            "fidelity": Fidelity.UNAVAILABLE.value,
            "recovery": Recovery.NONE.value,
            "feed_lag": False,
        }
    feed_lag = bool(getattr(emulator, "feed_lag", False)) or bool(
        bridge is not None and bridge.gap_seen
    )
    recovery = Recovery.DEGRADED.value if feed_lag else Recovery.PARTIAL.value
    return {
        "engine": "injected",
        "fidelity": Fidelity.PARTIAL.value,
        "recovery": recovery,
        "feed_lag": feed_lag,
        "feed_at": bool(bridge is not None and bridge.uses_feed_at),
    }


def _parse_argv(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m packages.core.terminal.runner",
        description="Pan Terminal runner（布局 B：runner 出生持 PTY + guard）",
    )
    parser.add_argument("--terminal-id", required=True, help="term_... 终端标识")
    parser.add_argument("--secret-file", required=True, help="<root>/secrets/<terminal_id>.secret")
    parser.add_argument("--rows", type=int, default=24)
    parser.add_argument("--cols", type=int, default=80)
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Sequence[str] | None = None) -> int:
    """命令行入口：argv 只有 id 与 secret 路径（无 token）。"""
    try:
        args = _parse_argv(argv)
    except SystemExit as exc:  # argparse 已输出用法
        return int(exc.code or RUNNER_EXIT_USAGE)
    runner = TerminalRunner(
        args.terminal_id,
        args.secret_file,
        rows=int(args.rows),
        cols=int(args.cols),
    )
    try:
        code = runner.run()
    except Exception as exc:  # noqa: BLE001 - 顶层兜底：只输出类型名
        print(f"pan-terminal-runner: internal error ({type(exc).__name__})", file=sys.stderr)
        return RUNNER_EXIT_INTERNAL
    return int(code)


if __name__ == "__main__":  # pragma: no cover - 由 -m 执行
    raise SystemExit(main())
