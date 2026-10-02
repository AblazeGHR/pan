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
#: bootstrap / 身份 / 管道 / 启动门禁失败（fail-closed，未发布 running）。
RUNNER_EXIT_BOOTSTRAP_FAILED = 4
#: 未预期内部错误（类型名已脱敏记录）。
RUNNER_EXIT_INTERNAL = 5

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

    def reset(self, fn: Callable[[], Any] | None = None) -> None:
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
    def started_at(self) -> float:
        return self._started_at

    @property
    def finished_at(self) -> float | None:
        return self._finished_at

    def run(self, timeout: float) -> tuple[bool, Any, str | None]:
        """返回 ``(finished, result, error_type)``；``finished=False`` = 仍在执行。"""
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
        self._events: list[tuple[float, str, str]] = []

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

    def _abort_backend(self, backend: Any) -> None:
        """启动失败路径：只清理自有后代（尽力而为，失败如实记录）。"""
        if backend is None:
            return
        try:
            backend.terminate(True)
        except Exception as exc:  # noqa: BLE001
            self._note("abort-terminate-failed", type(exc).__name__)
        try:
            backend.close()
        except Exception as exc:  # noqa: BLE001 - 保留 owner 由进程退出兜底
            self._note("abort-close-failed", type(exc).__name__)

    def _close_pipe_server(self) -> None:
        server = self._pipe_server
        self._pipe_server = None
        if server is None:
            return
        try:
            server.close(timeout=2.0)
        except Exception as exc:  # noqa: BLE001
            self._note("pipe-close-failed", type(exc).__name__)

    # ------------------------------------------------------------ 主流程
    def run(self) -> int:
        """同步主流程；返回退出码（见模块常量）。"""
        self._runner_state = "bootstrapping"
        try:
            self._bootstrap()
            self._prepare()
        except RunnerBootstrapError as exc:
            self._note("bootstrap-refused", type(exc).__name__)
            self._cleanup_hello_on_failure()
            self._runner_state = "bootstrap-failed"
            return RUNNER_EXIT_BOOTSTRAP_FAILED
        except Exception as exc:  # noqa: BLE001 - 未预期错误也要 fail-closed
            self._note("bootstrap-error", type(exc).__name__)
            self._cleanup_hello_on_failure()
            self._runner_state = "bootstrap-failed"
            return RUNNER_EXIT_BOOTSTRAP_FAILED

        watchdog = threading.Thread(
            target=self._watchdog_loop, name="runner-lease-watchdog", daemon=True
        )
        watchdog.start()
        try:
            self._accept_loop()
        finally:
            self._watchdog_stop.set()
            self._close_pipe_server()
            self._join_connections()
            self._finalize()
        return int(self._exit_code if self._exit_code is not None else RUNNER_EXIT_OK)

    def _accept_loop(self) -> None:
        server = self._pipe_server
        while not self._shutdown_due():
            try:
                connection = server.accept(timeout=self._accept_timeout)
            except win_pipe.PipeCancelled:  # pragma: no cover - 本层不主动取消 accept
                continue
            except Exception as exc:  # noqa: BLE001 - 管道层失败：有界退出
                self._note("accept-failed", type(exc).__name__)
                break
            if connection is None:
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

    def _join_connections(self) -> None:
        with self._conn_lock:
            threads = list(self._conn_threads)
        for thread in threads:
            thread.join(timeout=2.0)

    def _finalize(self) -> None:
        """主循环退出后的收尾：未完成清理时再尝试一次（有界，如实记录）。"""
        runtime = self._runtime
        if runtime is None:
            return
        if runtime.state in (RuntimeState.EXITED, RuntimeState.LOST):
            if self._exit_code is None:
                self._exit_code = RUNNER_EXIT_OK
            return
        worker = self._close_worker
        if worker is not None and worker.in_flight:
            finished, result, error_type = worker.run(2.0)  # 有界等待同一 worker（不重叠）
            if self._exit_code is not None:
                return
            if finished and error_type is None and isinstance(result, CleanupReport) and result.ok:
                self._exit_code = RUNNER_EXIT_OK
            else:
                self._exit_code = RUNNER_EXIT_CLEANUP_FAILED
            return
        result = self.close(reason="runner-shutdown")
        if self._exit_code is None:
            self._exit_code = (
                RUNNER_EXIT_OK if result.get("status") == "exited" else RUNNER_EXIT_CLEANUP_FAILED
            )

    # ------------------------------------------------------------ 关闭协调
    def _shutdown_due(self) -> bool:
        with self._shutdown_lock:
            if not self._shutdown_flag:
                return False
            deadline = self._shutdown_deadline
        return deadline is None or time.monotonic() >= deadline

    def _request_shutdown(self, exit_code: int) -> None:
        """请求主循环退出；留出响应写回窗口（不立刻切断连接）。"""
        with self._shutdown_lock:
            if self._exit_code is None or exit_code != RUNNER_EXIT_OK:
                self._exit_code = int(exit_code)
            self._shutdown_flag = True
            self._shutdown_deadline = time.monotonic() + _SHUTDOWN_GRACE_SECONDS

    # ------------------------------------------------------------ watchdog
    def _watchdog_loop(self) -> None:
        """lease 看门狗：只读时间戳 + 触发 close worker（与 handler 互不拖死）。"""
        while not self._watchdog_stop.wait(0.05):
            if self._shutdown_flag or self._closing or self._detached:
                continue
            if self._runtime is None:
                continue
            now = time.monotonic()
            with self._lease_lock:
                established = self._lease_established
                last = self._last_heartbeat
                started = self._lease_started_at
            if established:
                if last is not None and (now - last) >= self._lease_grace:
                    self._lease_expired_close()
                    return
            elif (now - started) >= self._bootstrap_grace:
                self._note("owner-absent")
                self._lease_expired_close()
                return

    def _lease_expired_close(self) -> None:
        """死期自停：close worker 有界重试；未收敛则以退出（内核 guard）兜底。"""
        result = self.close(reason="lease-expired")
        if result.get("status") == "exited":
            return
        for attempt in range(1, self._lease_cleanup_retries + 1):
            if self._watchdog_stop.wait(_LEASE_CLEANUP_RETRY_INTERVAL):
                return
            result = self.close(reason=f"lease-expired-retry{attempt}")
            if result.get("status") == "exited":
                return
        self._note("lease-cleanup-exhausted")
        self._runner_state = "cleanup-failed"
        self._request_shutdown(RUNNER_EXIT_CLEANUP_FAILED)

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
            text = json.dumps(self.describe(), ensure_ascii=False, separators=(",", ":"))
        except Exception as exc:  # noqa: BLE001 - 序列化失败不允许拖死响应
            return json.dumps({"status": "detail-error", "error_type": type(exc).__name__})
        if len(text) <= _DETAIL_BUDGET:
            return text
        minimal = {
            "schema_version": 1,
            "terminal_id": self.terminal_id,
            "runner_state": self._runner_state,
            "detached": bool(self._detached),
            "truncated": True,
        }
        return json.dumps(minimal, ensure_ascii=False, separators=(",", ":"))

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
            detail_value = json.dumps(base, ensure_ascii=False, separators=(",", ":"))
        if detail_value is None:
            detail_value = self._detail_json()
        out["detail"] = detail_value[:_DETAIL_BUDGET]
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
                    }
                ),
            },
        )

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
        """已认证 Pan 所有者的租约续约/接管（唯一续约路径）。"""
        client_id = str(client_id)
        with self._lease_lock:
            if self._closing or self._runner_state in ("exited",):
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
            if self._closing or runtime is None or runtime.state is not RuntimeState.RUNNING:
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

    def close(self, *, reason: str = "explicit-close") -> dict[str, Any]:
        reason = str(reason or "explicit-close")[:64]
        with self._lifecycle_lock:
            if self._runner_state == "exited":
                return self._payload("exited", ok=True)
            self._closing = True
            runtime = self._runtime
            if runtime is None:
                self._runner_state = "exited"
                self._request_shutdown(RUNNER_EXIT_OK)
                return self._payload("exited", ok=True)
            worker = self._close_worker
            if worker is None or worker.finished:
                self._close_worker = worker = _TrackedCall(
                    lambda reason=reason: runtime.close(reason=reason)
                )
            finished, result, error_type = worker.run(self._close_wait)
        if not finished:
            self._runner_state = "closing"
            return self._payload("closing", ok=False)
        if error_type is not None:
            self._runner_state = "cleanup-failed"
            self._note("close-worker-error", error_type)
            return self._payload("cleanup-failed", ok=False)
        report: CleanupReport = result
        self._last_cleanup = report
        if report.ok:
            self._runner_state = "exited"
            self._note("exited")
            self._request_shutdown(RUNNER_EXIT_OK)
            return self._payload("exited", ok=True)
        # 失败不释放 owner：runtime 保 cleanup-failed、句柄/进程证据仍在，可重试。
        self._runner_state = "cleanup-failed"
        self._note("cleanup-failed")
        return self._payload("cleanup-failed", ok=False)

    # ---- detail 辅助
    def _detail_with(self, extra: Mapping[str, Any]) -> str:
        """在 describe 摘要之上叠加字段（仍是单个 JSON 字符串，≤4096）。"""
        try:
            base = json.loads(self._detail_json())
        except Exception:  # noqa: BLE001
            base = {"schema_version": 1}
        base.update(dict(extra))
        text = json.dumps(base, ensure_ascii=False, separators=(",", ":"))
        if len(text) <= _DETAIL_BUDGET:
            return text
        small = {"schema_version": 1, "truncated": True}
        small.update(dict(extra))
        return json.dumps(small, ensure_ascii=False, separators=(",", ":"))


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
        return runner.run()
    except Exception as exc:  # noqa: BLE001 - 顶层兜底：只输出类型名
        print(f"pan-terminal-runner: internal error ({type(exc).__name__})", file=sys.stderr)
        return RUNNER_EXIT_INTERNAL


if __name__ == "__main__":  # pragma: no cover - 由 -m 执行
    raise SystemExit(main())
