"""P1 Terminal runner 隔离真机测试（Windows + 真实 ConPTY/Job/DPAPI/命名管道）。

覆盖（接口文档 §8）：
- 管理心跳保活 / 死期 2s 自停整树；
- 观察者（浏览器式）连接轮换不改变 runtime；
- detach：受 ambient Job 限制时显式拒绝；能力注入下同 PID/FILETIME + shell 变量保存 + 新 controller 重连；
- 根死孙活 → 整树清理；runner 硬死 → guard 内核清整树；
- startup/身份/认证拒绝（含 token 入 env 自检拒绝）；
- cleanup-failed 保 owner 重试收敛；
- 阻塞 write 不堵 watchdog/其它连接；
- 跨进程 DPAPI secret 重连；token 无 argv/env/日志泄漏；
- IPC 固化期限不过期执行 + terminal_id 严格绑定 + 输出日志驱逐 gap。

隔离：每个测试独立临时数据根（``<tmp>/terminals``）+ 独立 terminal_id + 无真实
模型调用/无外部服务；所有子进程清理均以 **同 handle 身份核验后终止**
（``probe_process`` + ``terminate_verified_process``），不按命令行广杀。

证据：设置 ``PAN_TERMINAL_RUNNER_EVIDENCE_DIR`` 时，测试把机器可读 JSON 写入该目录
（默认不写；审计证据由 ``audit/terminal/implementation/runner/collect_evidence.py`` 生成）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

import pytest

from packages.core.terminal import ipc, runner_client, secret_store, win_pipe
from packages.core.terminal import runner as runner_module
from packages.core.terminal.contracts import (
    CleanupReport,
    DetachReport,
    ExitInfo,
    ProcessStatus,
    RuntimeState,
    TerminateOutcome,
)

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="runner 依赖 Windows ConPTY / Job Object / DPAPI / 命名管道",
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BOOT_TIMEOUT = 30.0
DEFAULT_WAIT_EXIT = 30.0


# ══════════════════════════════════════════════════════════════════════════
# 证据与通用工具
# ══════════════════════════════════════════════════════════════════════════


def _evidence_dir() -> Path | None:
    value = os.environ.get("PAN_TERMINAL_RUNNER_EVIDENCE_DIR")
    if not value:
        return None
    path = Path(value)
    path.mkdir(parents=True, exist_ok=True)
    return path


def record_evidence(name: str, payload: dict[str, Any]) -> None:
    """把机器可读证据写入证据目录（未设置环境变量时 no-op）。"""
    directory = _evidence_dir()
    if directory is None:
        return
    (directory / f"{name}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )


def _wait_until(predicate: Callable[[], Any], timeout: float, interval: float = 0.05) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return None


def process_alive(pid: int) -> bool:
    return win_pipe.probe_process(int(pid)).status is ProcessStatus.ALIVE


def _pid_exists(pid: int) -> bool:
    """PID 是否仍存在（OpenProcess 失败且 ERROR_INVALID_PARAMETER(87) = 不存在）。

    进程完全退出并被回收后 ``probe_process`` 只能给 UNKNOWN（打不开 ≠ 已退出）；
    测试侧需要区分“已退出”与“探测失败”，因此在同用户自建子进程上用本原语。
    """
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = ctypes.c_void_p
    k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k32.CloseHandle.argtypes = (ctypes.c_void_p,)
    ctypes.set_last_error(0)
    handle = k32.OpenProcess(0x00100000, False, int(pid))  # QUERY_LIMITED_INFORMATION
    if not handle:
        return int(ctypes.get_last_error()) != 87  # 87=ERROR_INVALID_PARAMETER
    k32.CloseHandle(ctypes.c_void_p(handle))
    return True


def process_dead(pid: int, filetime: int | None = None) -> bool:
    """死亡判定：signaled=DEAD；打不开且 PID 不存在=已退出；ALIVE 且 FILETIME 不符=PI
    复用（原进程已退出）。"""
    probe = win_pipe.probe_process(int(pid))
    if probe.status is ProcessStatus.DEAD:
        return True
    if probe.status is ProcessStatus.ALIVE:
        if (
            filetime is not None
            and probe.identity is not None
            and int(probe.identity.created_at_filetime or -1) != int(filetime)
        ):
            return True  # PID 被复用：原身份已不存在
        return False
    return not _pid_exists(int(pid))


def wait_dead(pid: int, timeout: float, filetime: int | None = None) -> bool:
    return bool(
        _wait_until(lambda: process_dead(int(pid), filetime), timeout)
    )


def _read_until(
    client: "runner_client.RunnerClient",
    marker: str | bytes,
    *,
    timeout: float = 8.0,
    cursor: int = 0,
) -> tuple[bytes, int]:
    """从 cursor 续读原始流直到 marker 出现；返回（累计缓冲, next_cursor）。"""
    needle = marker if isinstance(marker, bytes) else marker.encode("utf-8")
    deadline = time.monotonic() + timeout
    buf = bytearray()
    current = int(cursor)
    while time.monotonic() < deadline:
        page = client.read(current)
        if page["gap"] is not None:
            current = page["next_cursor"]
            continue
        if page["data"]:
            buf += page["data"]
            current = page["next_cursor"]
        if needle in buf:
            return bytes(buf), current
        time.sleep(0.05)
    return bytes(buf), current


# ══════════════════════════════════════════════════════════════════════════
# 子进程骨架（runner 自证 + 内核核验 + 核验后清理）
# ══════════════════════════════════════════════════════════════════════════

WRAPPER_SOURCE = r'''
import os, sys, threading, time

sys.path.insert(0, os.environ["PAN_TERMINAL_RUNNER_REPO"])
from packages.core.terminal import win_pipe
from packages.core.terminal.contracts import ProcessProbe, ProcessStatus
from packages.core.terminal.runner import (
    RUNNER_EXIT_INTERNAL, DurabilityCapability, TerminalRunner,
)

terminal_id, secret_file = sys.argv[1], sys.argv[2]
mode = os.environ.get("PAN_TERMINAL_RUNNER_TEST_MODE", "normal")
kwargs = {}

if mode == "capable":
    kwargs["durability_probe"] = lambda: DurabilityCapability(True, False, "test-injected-capable")
elif mode == "fail-once-probe":
    state = {"calls": 0}
    def probe(pid):
        state["calls"] += 1
        if state["calls"] <= 1:
            return ProcessProbe(ProcessStatus.UNKNOWN, None, "test-injected-unknown")
        return win_pipe.default_identity_probe(pid)
    kwargs["identity_probe"] = probe
elif mode == "gated-write":
    # 受控阻塞写：gate 文件存在时 worker 阻塞（在 runner 进程内、真实 IPC/worker 路径），
    # 文件删除后委托真实 runtime.write。用于验证“阻塞 write 不堵 watchdog / 不叠加”。
    gate_path = os.environ["PAN_TERMINAL_RUNNER_GATE_FILE"]
    def _gated_write(data):
        deadline = time.time() + 90.0
        while os.path.exists(gate_path) and time.time() < deadline:
            time.sleep(0.02)
        return runner.runtime.write(data)
    kwargs["write_hook"] = _gated_write
elif mode == "fake-emulator":
    class FakeEmulator:
        """测试用权威仿真器替身（feed_at 可选扩展 + 慢 snapshot 注入）。"""
        def __init__(self, delay):
            self.delay = delay
            self._seq = 0
            self._lock = threading.Lock()
        @property
        def feed_lag(self):
            return False
        def feed(self, data):
            with self._lock:
                self._seq += len(data)
        def feed_at(self, seq, data):
            with self._lock:
                self._seq = int(seq) + len(data)
        def resize(self, rows, cols):
            pass
        def snapshot(self, *, timeout=2.0):
            if self.delay:
                time.sleep(self.delay)
            from packages.core.terminal.contracts import AppliedSnapshot, Fidelity, Recovery
            with self._lock:
                cursor = int(self._seq)
            return AppliedSnapshot(
                serialized_screen="FAKE-SCREEN:%d" % cursor, cursor=cursor, rows=24, cols=80,
                fidelity=Fidelity.FULL, recovery=Recovery.FULL, engine="fake-test",
            )
        def reset_baseline(self):
            return self.snapshot()
    kwargs["emulator"] = FakeEmulator(float(os.environ.get("PAN_TERMINAL_RUNNER_SNAPSHOT_DELAY", "0")))


def _dump_backend(backend):
    """测试取证：把真实 PTY 根进程身份写到文件（供父进程核验后代清理）。"""
    import json as _json
    identity = getattr(backend, "identity", None)
    payload = {
        "pid": getattr(backend, "pid", None),
        "filetime": getattr(identity, "created_at_filetime", None),
    }
    with open(os.environ["PAN_TERMINAL_RUNNER_PID_FILE"], "w", encoding="utf-8") as fh:
        _json.dump(payload, fh)


if mode == "startup-refuse-real":
    from packages.core.terminal.runner import RunnerBootstrapError

    class RefusingRunner(TerminalRunner):
        """真实 spawn 之后、发布 running 之前注入拒绝（真实 abort 清理路径）。"""
        def _build_runtime(self, backend):
            _dump_backend(backend)
            raise RunnerBootstrapError("test-injected post-spawn refusal")

    runner = RefusingRunner(terminal_id, secret_file, **kwargs)
elif mode == "startup-refuse-uncleaned":
    from packages.core.terminal.runner import RunnerBootstrapError

    class UncleanedRunner(TerminalRunner):
        """真实 spawn 后注入拒绝 + 清理双失败（退出码/状态文件须区分未证明）。"""
        def _build_runtime(self, backend):
            _dump_backend(backend)

            def _term_failure(force):
                raise RuntimeError("injected-terminate-failure")

            def _close_failure():
                raise RuntimeError("injected-close-failure")

            backend.terminate = _term_failure
            backend.close = _close_failure
            raise RunnerBootstrapError("test-injected post-spawn refusal")

    runner = UncleanedRunner(terminal_id, secret_file, **kwargs)
elif mode == "accept-fail":
    class AcceptFailRunner(TerminalRunner):
        """真实 PTY 运行中注入 accept 异常（O2：不得 exit 0）。"""
        def _accept_loop(self):
            _dump_backend(self._backend)

            def _accept_failure(*, timeout=None):
                raise RuntimeError("injected-accept-failure")

            self._pipe_server.accept = _accept_failure
            super()._accept_loop()

    runner = AcceptFailRunner(terminal_id, secret_file, **kwargs)
else:
    runner = TerminalRunner(terminal_id, secret_file, **kwargs)
try:
    code = runner.run()
except Exception as exc:
    print("runner wrapper error: %s" % type(exc).__name__, file=sys.stderr)
    code = RUNNER_EXIT_INTERNAL
sys.exit(code)
'''

GRANDCHILD_SOURCE = r'''
import ctypes, json, os, sys, time
from ctypes import wintypes

class FILETIME(ctypes.Structure):
    _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
creation, exit_t, kernel, user = FILETIME(), FILETIME(), FILETIME(), FILETIME()
k32.GetProcessTimes(
    ctypes.c_void_p(-1), ctypes.byref(creation), ctypes.byref(exit_t),
    ctypes.byref(kernel), ctypes.byref(user),
)
filetime = (int(creation.high) << 32) | int(creation.low)
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    json.dump({"pid": os.getpid(), "filetime": str(filetime)}, fh)
time.sleep(float(sys.argv[2]) if len(sys.argv) > 2 else 300.0)
'''

RECONNECT_SOURCE = r'''
import json, sys, time

sys.path.insert(0, sys.argv[5])
from packages.core.terminal import runner_client

report = {"attached": False}
client = runner_client.RunnerClient(
    sys.argv[2], data_root=sys.argv[1], connect_timeout=10.0, request_timeout_ms=5000
)
try:
    client.attach()
    report["attached"] = True
    report["peer_pid"] = client.peer_pid
    report["heartbeat"] = client.heartbeat()["status"]
    describe = client.describe()
    report["status"] = describe.get("status")
    report["pid"] = describe.get("pid")
    report["filetime"] = describe.get("process_created_at_filetime")
    report["runner_state"] = describe.get("runner_state")
    report["detached"] = describe.get("detached")
finally:
    client.release_connection()
with open(sys.argv[3], "w", encoding="utf-8") as fh:
    json.dump(report, fh)
'''


class RunnerHandle:
    """测试持有的 runner 子进程句柄：hello 自证身份 + 内核核验后的清理。"""

    def __init__(
        self,
        proc: subprocess.Popen,
        terminal_id: str,
        data_root: Path,
        store: secret_store.SecretStore,
        record: Any,
        payload: secret_store.SecretPayload | None,
    ) -> None:
        self.proc = proc
        self.terminal_id = terminal_id
        self.data_root = data_root
        self.store = store
        self.record = record
        self.payload = payload
        self.pid = int(record.pid)
        self.filetime = int(record.filetime)
        self.shell_pid: int | None = None
        self.shell_filetime: int | None = None
        self._stdout: list[str] = []
        self._stderr: list[str] = []
        self._drains = [
            threading.Thread(target=self._drain, args=(proc.stdout, self._stdout), daemon=True),
            threading.Thread(target=self._drain, args=(proc.stderr, self._stderr), daemon=True),
        ]
        for thread in self._drains:
            thread.start()

    @staticmethod
    def _drain(stream: Any, sink: list[str]) -> None:
        if stream is None:
            return
        try:
            for line in iter(stream.readline, ""):
                sink.append(line)
        except Exception:  # noqa: BLE001 - 管道关闭竞态
            pass

    # -- 观测 ---------------------------------------------------------
    @property
    def token(self) -> str:
        assert self.payload is not None
        return self.payload.token

    def stdout_text(self) -> str:
        return "".join(self._stdout)

    def stderr_text(self) -> str:
        return "".join(self._stderr)

    def alive(self) -> bool:
        return process_alive(self.pid)

    def wait_exit(self, timeout: float = DEFAULT_WAIT_EXIT) -> int:
        return int(self.proc.wait(timeout=timeout))

    def note_shell(self, describe: dict[str, Any]) -> None:
        self.shell_pid = int(describe["pid"])
        self.shell_filetime = int(describe["process_created_at_filetime"])

    # -- 清理（身份核验） ---------------------------------------------
    def kill_verified(self) -> dict[str, Any]:
        trace: dict[str, Any] = {"alive_before_cleanup": self.alive()}
        if trace["alive_before_cleanup"]:
            trace["runner"] = win_pipe.terminate_verified_process(self.pid, self.filetime)
        else:
            trace["runner"] = {"skipped": "not-alive"}
        # 直接子进程（wrapper/launcher）与自证 pid 不同时，同样核验后终止。
        if self.proc.pid and self.proc.pid != self.pid:
            probe = win_pipe.probe_process(int(self.proc.pid))
            if probe.status is ProcessStatus.ALIVE and probe.identity is not None:
                trace["launcher"] = win_pipe.terminate_verified_process(
                    int(self.proc.pid), probe.identity.created_at_filetime
                )
        if self.shell_pid is not None and process_alive(self.shell_pid):
            trace["shell"] = win_pipe.terminate_verified_process(
                self.shell_pid, self.shell_filetime
            )
        return trace

    def cleanup(self) -> dict[str, Any]:
        trace = self.kill_verified()
        try:
            self.proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            trace["proc_wait"] = "timeout"
        for thread in self._drains:
            thread.join(timeout=2)
        for stream in (self.proc.stdout, self.proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception:  # noqa: BLE001
                pass
        return trace


@pytest.fixture
def spawn_runner(tmp_path, request):
    """工厂 fixture：spawn runner（可选 wrapper 模式）+ 写 DPAPI 秘密 + 收尾清理。"""
    handles: list[RunnerHandle] = []

    def _spawn(
        *,
        terminal_id: str | None = None,
        mode: str | None = None,
        token: str | None = None,
        env_extra: dict[str, str] | None = None,
        write_secret: str = "auto",
        secret_timeout: float = DEFAULT_BOOT_TIMEOUT,
        snapshot_delay: float = 0.0,
    ) -> RunnerHandle:
        terminal_id = terminal_id or f"term_{os.urandom(6).hex()}"
        data_root = tmp_path / "terminals"
        store = secret_store.SecretStore(data_root)
        # P2 侧等价动作：先建 owner-only secrets 目录（从创建时生效），
        # 避免“目录在首轮 probe 之后才出现”的路径解析竞态。
        store.ensure_secrets_dir()
        secret_file = store.secret_path(terminal_id)
        if mode:
            wrapper = tmp_path / f"wrapper_{terminal_id}.py"
            wrapper.write_text(WRAPPER_SOURCE, encoding="utf-8")
            argv = [
                sys.executable,
                str(wrapper),
                terminal_id,
                str(secret_file),
            ]
        else:
            argv = runner_client.build_runner_argv(terminal_id, secret_file)
        env = dict(os.environ)
        env["PAN_TERMINAL_RUNNER_REPO"] = str(REPO_ROOT)
        if mode:
            env["PAN_TERMINAL_RUNNER_TEST_MODE"] = mode
        if snapshot_delay:
            env["PAN_TERMINAL_RUNNER_SNAPSHOT_DELAY"] = str(snapshot_delay)
        if env_extra:
            env.update(env_extra)
        proc = subprocess.Popen(
            argv,
            cwd=str(REPO_ROOT),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        try:
            record = store.wait_for_bootstrap_identity(terminal_id, timeout=secret_timeout)
        except Exception as exc:  # noqa: BLE001 - 记录路径诊断后再抛出（不掩盖）
            record_evidence(
                f"bootstrap_failure_{terminal_id}",
                {
                    "error_type": type(exc).__name__,
                    "secret_file": str(secret_file),
                    "parent_resolved": os.path.normcase(str(secret_file.parent.resolve())),
                    "secrets_dir_resolved": os.path.normcase(str(store.secrets_dir.resolve())),
                    "secrets_dir_exists": os.path.isdir(str(store.secrets_dir)),
                },
            )
            raise
        payload: secret_store.SecretPayload | None = None
        if write_secret == "auto":
            payload = runner_client.complete_bootstrap(store, terminal_id, token=token)
        elif write_secret == "bogus_identity":
            now = time.time()
            payload = secret_store.SecretPayload(
                terminal_id=terminal_id,
                pipe_name=win_pipe.pipe_name_for(terminal_id),
                token=token or ipc.generate_token(),
                runner_pid=int(record.pid) + 1,
                runner_filetime=int(record.filetime) + 1,
                created_at=now,
                updated_at=now,
            )
            store.write_secret(payload)
        elif write_secret == "none":
            payload = None
        else:  # pragma: no cover - 参数错误
            raise ValueError(f"unknown write_secret mode: {write_secret}")
        handle = RunnerHandle(proc, terminal_id, data_root, store, record, payload)
        handles.append(handle)
        return handle

    yield _spawn
    trace = {"spawned": len(handles), "handles": {}}
    for handle in handles:
        trace["handles"][handle.terminal_id] = handle.cleanup()
    record_evidence(f"cleanup_{request.node.name}", trace)


def _client(
    handle: RunnerHandle,
    *,
    client_id: str | None = None,
    close_timeout_ms: int = 15_000,
) -> runner_client.RunnerClient:
    client = runner_client.RunnerClient(
        handle.terminal_id,
        data_root=handle.data_root,
        connect_timeout=10.0,
        request_timeout_ms=8_000,
        close_timeout_ms=close_timeout_ms,
        client_id=client_id,
    )
    client.attach()
    return client


# ══════════════════════════════════════════════════════════════════════════
# 纯逻辑（桥接/能力声明）
# ══════════════════════════════════════════════════════════════════════════


class _FakeEmulator:
    def __init__(self) -> None:
        self.calls: list[Any] = []
        self.resizes: list[tuple[int, int]] = []
        self.feed_lag = False

    def feed(self, data: bytes) -> None:
        self.calls.append(("feed", bytes(data)))

    def feed_at(self, seq: int, data: bytes) -> None:
        self.calls.append(("feed_at", int(seq), bytes(data)))

    def resize(self, rows: int, cols: int) -> None:
        self.resizes.append((int(rows), int(cols)))

    def snapshot(self, *, timeout: float = 2.0):
        raise NotImplementedError

    def reset_baseline(self):
        raise NotImplementedError


class _PlainFeedEmulator:
    """只实现 base 协议 ``feed(data)``（没有可选扩展 ``feed_at``）。"""

    def __init__(self) -> None:
        self.calls: list[Any] = []
        self.feed_lag = False

    def feed(self, data: bytes) -> None:
        self.calls.append(("feed", bytes(data)))

    def resize(self, rows: int, cols: int) -> None:
        pass

    def snapshot(self, *, timeout: float = 2.0):
        raise NotImplementedError

    def reset_baseline(self):
        raise NotImplementedError


def test_emulator_bridge_gap_detection_and_feed_at_extension():
    """OutputConsumer(seq,data) 绝对偏移：连续=feed_at；缺口=粘滞降级（不丢事实）。"""
    emulator = _FakeEmulator()
    bridge = runner_module.RunnerEmulatorBridge(emulator)
    bridge(0, b"abc")
    bridge(3, b"def")
    assert not bridge.gap_seen
    assert bridge.fed_through == 6
    assert bridge.uses_feed_at
    assert emulator.calls == [("feed_at", 0, b"abc"), ("feed_at", 3, b"def")]
    bridge(10, b"xyz")  # 7→10 缺口
    assert bridge.gap_seen is True
    assert bridge.gaps == ((6, 10),)
    assert emulator.calls[-1] == ("feed_at", 10, b"xyz")
    bridge.resize(30, 100)
    assert emulator.resizes == [(30, 100)]

    # 无 ``feed_at`` 扩展：回退 base 协议 feed，但粘滞降级（禁止 full），缺口事实保留。
    plain = _PlainFeedEmulator()
    fallback = runner_module.RunnerEmulatorBridge(plain)
    fallback(0, b"aa")
    fallback(2, b"bb")
    assert not fallback.gap_seen and not fallback.uses_feed_at
    fallback(9, b"cc")
    assert fallback.gap_seen is True and fallback.gaps == ((4, 9),)
    assert plain.calls == [("feed", b"aa"), ("feed", b"bb"), ("feed", b"cc")]
    record_evidence(
        "bridge_unit",
        {
            "gap_first": [6, 10],
            "fallback_gap": [4, 9],
            "feed_at_used": bridge.uses_feed_at,
            "base_feed_used": not fallback.uses_feed_at,
        },
    )


def test_durability_capability_matches_ambient_job_reality():
    """durability 能力与宿主 ambient Job 事实一致；受限时 capable=False（fail-closed）。"""
    ambient = runner_module.detect_ambient_job()
    capability = runner_module.detect_durability_capability()
    assert capability.ambient_job is ambient
    assert capability.durable_capable is (not ambient)
    assert not capability.durable_capable or capability.detail
    record_evidence(
        "durability_capability",
        {
            "ambient_job": ambient,
            "durable_capable": capability.durable_capable,
            "detail": capability.detail,
        },
    )


# ══════════════════════════════════════════════════════════════════════════
# 真机：基础往返 / 输出日志 gap
# ══════════════════════════════════════════════════════════════════════════


def test_real_pty_roundtrip_read_input_and_close(spawn_runner):
    handle = spawn_runner()
    client = _client(handle)
    describe = client.describe()
    assert describe["runner_state"] == "running"
    assert describe["runtime_state"] == "running"
    assert int(describe["pid"]) != handle.pid  # shell ≠ runner
    handle.note_shell(describe)
    shell_pid = handle.shell_pid
    assert process_alive(shell_pid)
    assert client.heartbeat()["status"] == "ok"
    assert client.describe()["lease"]["owner"] == client.client_id

    marker = f"PAN_RT_{os.urandom(3).hex()}"
    written = client.input(f"echo {marker}\r".encode("utf-8"))
    assert written["status"] in ("done", "accepted-in-flight")
    text, _ = _read_until(client, marker)
    assert marker.encode() in text

    result = client.close()
    assert result["status"] == "exited"
    assert handle.wait_exit() == runner_module.RUNNER_EXIT_OK
    assert wait_dead(shell_pid, 8.0, handle.shell_filetime)
    client.release_connection()
    record_evidence(
        "roundtrip",
        {
            "runner_pid": handle.pid,
            "shell_pid": shell_pid,
            "marker": marker,
            "write_status": written["status"],
            "close_status": result["status"],
        },
    )


def test_output_log_eviction_publishes_gap_and_exact_totals(spawn_runner, tmp_path):
    handle = spawn_runner()
    client = _client(handle)
    client.heartbeat()
    blob = tmp_path / "blob.txt"
    blob.write_bytes(b"A" * 200_000 + b"TAIL-MARKER-" + b"B" * 100_000)
    client.input(f'type "{blob}"\r'.encode("utf-8"))
    page = _wait_until(lambda: None, 0.0)  # 保持类型一致性（无操作）
    deadline = time.monotonic() + 10.0
    totals = 0
    while time.monotonic() < deadline:
        describe = client.describe()
        totals = int(describe["total_bytes"])
        if totals > 262_144:
            break
        time.sleep(0.1)
    assert totals > 262_144, f"expected eviction (>256KiB), total_bytes={totals}"
    first_page = client.read(0)
    assert first_page["gap"] is not None, "cursor 0 必须报告 gap（窗口已驱逐）"
    assert int(first_page["first_retained_seq"]) > 0
    tail, _ = _read_until(
        client, b"TAIL-MARKER-", timeout=8.0, cursor=int(first_page["first_retained_seq"])
    )
    assert b"TAIL-MARKER-" in tail
    assert page is None
    record_evidence(
        "output_gap",
        {
            "total_bytes": totals,
            "gap": list(first_page["gap"]) if first_page["gap"] else None,
            "first_retained_seq": int(first_page["first_retained_seq"]),
            "retained": int(first_page["size"]),
        },
    )
    assert client.close()["status"] == "exited"
    assert handle.wait_exit() == runner_module.RUNNER_EXIT_OK


def test_snapshot_protocol_a_with_injected_emulator(spawn_runner):
    """协议 A：serialized + applied cursor（= 仿真器已消费的位置），从 cursor 续流。"""
    handle = spawn_runner(mode="fake-emulator")
    client = _client(handle)
    client.heartbeat()
    client.input(b"echo SNAPSHOT-READY\r")
    _read_until(client, "SNAPSHOT-READY")
    snap = client.snapshot()
    assert snap["status"] in ("running", "detached")
    assert snap["fidelity"] == "full" and snap["recovery"] == "full"
    assert snap["protocol"] == "A"
    assert snap["serialized_screen"].startswith("FAKE-SCREEN:")
    cursor = int(snap["cursor"])
    assert cursor > 0
    assert cursor <= int(client.describe()["total_bytes"])
    # 从 cursor 续原始流：不额外喂 pending_tail，不重放缺口
    page = client.read(cursor)
    assert page["gap"] is None
    assert client.close()["status"] == "exited"
    assert handle.wait_exit() == runner_module.RUNNER_EXIT_OK
    record_evidence(
        "snapshot_protocol_a",
        {"cursor": cursor, "serialized_prefix": snap["serialized_screen"][:32]},
    )


# ══════════════════════════════════════════════════════════════════════════
# lease：保活 / 死期自停 / 观察者轮换
# ══════════════════════════════════════════════════════════════════════════


def test_managed_lease_heartbeat_then_expiry_stops_tree(spawn_runner):
    handle = spawn_runner()
    client = _client(handle)
    handle.note_shell(client.describe())
    assert client.heartbeat()["status"] == "ok"
    stop = threading.Event()
    failures: list[str] = []

    def beat() -> None:
        while not stop.is_set():
            try:
                if client.heartbeat(timeout_ms=2000).get("status") != "ok":
                    failures.append("non-ok")
                    return
            except Exception as exc:  # noqa: BLE001 - 断连/超时都记录类型
                failures.append(type(exc).__name__)
                return
            stop.wait(0.4)

    thread = threading.Thread(target=beat, daemon=True)
    thread.start()
    time.sleep(3.2)  # 3× 死期：心跳必须保活
    assert not failures, failures
    assert handle.alive()
    assert process_alive(handle.shell_pid)
    stop.set()
    thread.join(timeout=3)
    released_at = time.monotonic()
    client.release_connection()  # 断连只释放连接
    assert handle.wait_exit(timeout=25) == runner_module.RUNNER_EXIT_OK
    stopped_after = time.monotonic() - released_at
    assert wait_dead(handle.shell_pid, 8.0, handle.shell_filetime)
    assert stopped_after >= 1.5, f"lease 死期不得早于 ~2s 触发：{stopped_after:.2f}s"
    record_evidence(
        "managed_lease",
        {
            "alive_after_3x_grace": True,
            "seconds_until_self_stop": round(stopped_after, 2),
            "exit_code": 0,
        },
    )


def test_observer_rotation_does_not_change_runtime(spawn_runner):
    handle = spawn_runner()
    owner = _client(handle, client_id=f"pan-owner-{os.urandom(2).hex()}")
    handle.note_shell(owner.describe())
    assert owner.heartbeat()["status"] == "ok"
    stop = threading.Event()

    def beat() -> None:
        while not stop.is_set():
            try:
                owner.heartbeat(timeout_ms=2000)
            except Exception:  # noqa: BLE001
                return
            stop.wait(0.3)

    thread = threading.Thread(target=beat, daemon=True)
    thread.start()
    try:
        for index in range(3):
            observer = _client(handle, client_id=f"observer-{index}")
            assert observer.register_observer()["status"] == "ok"
            described = observer.describe()
            assert described["runner_state"] == "running"
            assert int(described["pid"]) == handle.shell_pid
            observer.release_connection()  # 浏览器式轮换：断开
            time.sleep(0.2)
        assert owner.heartbeat(timeout_ms=2000)["status"] == "ok"
        assert handle.alive()
        assert process_alive(handle.shell_pid)
        latest = owner.describe()
        assert int(latest["pid"]) == handle.shell_pid
        assert int(latest["process_created_at_filetime"]) == handle.shell_filetime
        result = owner.close()
        assert result["status"] == "exited"
        assert handle.wait_exit() == runner_module.RUNNER_EXIT_OK
        record_evidence(
            "observer_rotation",
            {"rotations": 3, "shell_pid": handle.shell_pid, "runner_alive": True},
        )
    finally:
        stop.set()
        thread.join(timeout=3)


# ══════════════════════════════════════════════════════════════════════════
# detach / durability
# ══════════════════════════════════════════════════════════════════════════


def test_detach_refused_when_ambient_job_constrains_durability(spawn_runner):
    """真实 ambient Job 限制下：显式拒绝 detach（零状态变化），不假成功。"""
    if not runner_module.detect_ambient_job():
        pytest.skip("no ambient job in this environment: refusal path not applicable")
    handle = spawn_runner()
    client = _client(handle)
    client.heartbeat()
    describe = client.describe()
    assert describe["durability"]["capable"] is False
    assert describe["durability"]["ambient_job"] is True
    result = client.detach()
    assert result["status"] == "detach-refused"
    assert result["ok"] is False
    after = client.describe()
    assert after["runner_state"] == "running"
    assert after["detached"] is False
    handle.note_shell(after)
    assert process_alive(handle.shell_pid)
    assert client.close()["status"] == "exited"
    assert handle.wait_exit() == runner_module.RUNNER_EXIT_OK
    record_evidence(
        "detach_refused",
        {"status": result["status"], "durability": describe["durability"]},
    )


def test_detach_semantics_same_pty_reconnect_and_shell_variable(spawn_runner):
    """能力注入下 detach：同 PTY/PID/FILETIME、shell 变量保存、新 controller 重连。"""
    handle = spawn_runner(mode="capable")
    owner = _client(handle, client_id="pan-owner-detach")
    owner.heartbeat()
    handle.note_shell(owner.describe())
    shell_pid, shell_filetime = handle.shell_pid, handle.shell_filetime
    mark = f"MARK_{os.urandom(3).hex()}"
    owner.input(f"set PAN_DETACH_{mark}=7749\r".encode("ascii"))
    owner.input(f"echo %PAN_DETACH_{mark}%\r".encode("ascii"))
    text, _ = _read_until(owner, "7749")
    assert b"7749" in text

    result = owner.detach()
    assert result["status"] == "detached" and result["ok"] is True
    owner.release_connection()
    time.sleep(3.4)  # > 统一死期：detach 后不得被 lease 死期杀
    assert handle.alive(), "detached runner 不应随租约丢失退出"
    assert process_alive(shell_pid)

    controller = _client(handle, client_id="pan-new-controller")
    assert controller.heartbeat()["status"] == "ok"
    described = controller.describe()
    assert described["runner_state"] == "detached"
    assert described["detached"] is True
    assert int(described["pid"]) == shell_pid
    assert int(described["process_created_at_filetime"]) == shell_filetime
    controller.input(f"echo %PAN_DETACH_{mark}%\r".encode("ascii"))
    text2, _ = _read_until(controller, "7749")
    assert b"7749" in text2, "shell 变量必须在 detach + 重连后仍存在"
    assert controller.close()["status"] == "exited"
    assert handle.wait_exit() == runner_module.RUNNER_EXIT_OK
    assert wait_dead(shell_pid, 8.0, handle.shell_filetime)
    record_evidence(
        "detach_semantics",
        {
            "shell_pid": shell_pid,
            "same_filetime": True,
            "variable_preserved": True,
            "reconnect_status": "detached",
        },
    )


# ══════════════════════════════════════════════════════════════════════════
# 整树：根死孙活 / runner 硬死
# ══════════════════════════════════════════════════════════════════════════


def test_root_exit_with_live_grandchild_is_tree_cleaned(spawn_runner, tmp_path):
    handle = spawn_runner()
    client = _client(handle)
    client.heartbeat()
    handle.note_shell(client.describe())
    script = tmp_path / "grandchild.py"
    script.write_text(GRANDCHILD_SOURCE, encoding="utf-8")
    out_json = tmp_path / "grandchild.json"
    command = f'start /b "" "{sys.executable}" "{script}" "{out_json}" 300'
    client.input((command + "\r").encode("utf-8"))
    info = _wait_until(
        lambda: json.loads(out_json.read_text(encoding="utf-8"))
        if out_json.exists()
        else None,
        20.0,
    )
    assert info is not None, "grandchild 未写出身份"
    grandchild_pid = int(info["pid"])
    assert process_alive(grandchild_pid)
    assert int(info["filetime"]) > 0

    client.input(b"exit\r")  # 根 shell 退出
    seen = _wait_until(lambda: (client.describe().get("exit") or {}).get("seen"), 10.0)
    assert seen, "根 shell 必须被观测为已退出"
    assert process_alive(grandchild_pid), "本用例要求根死时孙进程仍活"

    result = client.close()
    assert result["status"] == "exited"
    assert handle.wait_exit() == runner_module.RUNNER_EXIT_OK
    assert wait_dead(grandchild_pid, 8.0, int(info["filetime"])), "整树清理必须覆盖根死后的孙进程"
    record_evidence(
        "root_dead_grandchild_cleaned",
        {
            "shell_pid": handle.shell_pid,
            "grandchild_pid": grandchild_pid,
            "root_exit_seen": True,
            "grandchild_dead": True,
        },
    )


def test_runner_hard_kill_closes_guard_and_cleans_tree(spawn_runner):
    handle = spawn_runner()
    client = _client(handle)
    client.heartbeat()
    handle.note_shell(client.describe())
    shell_pid = handle.shell_pid
    assert process_alive(shell_pid)
    trace = win_pipe.terminate_verified_process(handle.pid, handle.filetime)
    assert trace.get("terminated") is True, trace
    assert wait_dead(shell_pid, 10.0, handle.shell_filetime), "runner 硬死 → guard 句柄关闭 → 内核清整树"
    assert handle.wait_exit(timeout=20) != 0  # 非正常退出
    client.release_connection()
    record_evidence(
        "runner_hard_kill",
        {
            "runner_pid": handle.pid,
            "shell_pid": shell_pid,
            "shell_dead": True,
            "terminate_trace": trace,
        },
    )


# ══════════════════════════════════════════════════════════════════════════
# 启动 / 身份 / 认证拒绝 / 期限与绑定
# ══════════════════════════════════════════════════════════════════════════


def test_startup_refuses_identity_mismatch_without_listening(spawn_runner):
    handle = spawn_runner(write_secret="bogus_identity")
    code = handle.wait_exit(timeout=30)
    assert code == runner_module.RUNNER_EXIT_BOOTSTRAP_FAILED
    assert not handle.store.bootstrap_path(handle.terminal_id).exists(), "未完成 hello 必须被删除"
    started = time.monotonic()
    with pytest.raises(Exception):
        win_pipe.PipeClient(handle.terminal_id, connect_timeout=1.0).connect()
    assert time.monotonic() - started < 6.0
    record_evidence(
        "startup_identity_mismatch",
        {"exit_code": code, "hello_removed": True, "pipe_absent": True},
    )


def test_runner_refuses_token_in_environment(spawn_runner):
    """token 出现在 runner 自身环境变量 → 启动自检 fail-closed（不监听）。"""
    token = ipc.generate_token()
    handle = spawn_runner(token=token, env_extra={"PAN_LEAK_SENTINEL": token})
    code = handle.wait_exit(timeout=30)
    assert code == runner_module.RUNNER_EXIT_BOOTSTRAP_FAILED
    combined = handle.stdout_text() + handle.stderr_text()
    assert token not in combined, "失败路径不得把 token 写入日志"
    with pytest.raises(Exception):
        win_pipe.PipeClient(handle.terminal_id, connect_timeout=1.0).connect()
    record_evidence(
        "token_env_refusal",
        {"exit_code": code, "token_absent_from_output": True},
    )


def test_wrong_token_handshake_rejected_without_touching_runtime(spawn_runner):
    handle = spawn_runner()
    good = _client(handle)
    assert good.heartbeat()["status"] == "ok"
    describe = good.describe()
    handle.note_shell(describe)
    wrong = secret_store.SecretPayload(
        terminal_id=handle.terminal_id,
        pipe_name=win_pipe.pipe_name_for(handle.terminal_id),
        token=ipc.generate_token(),
        runner_pid=handle.pid,
        runner_filetime=handle.filetime,
        created_at=time.time(),
        updated_at=time.time(),
    )
    bad = runner_client.RunnerClient(
        handle.terminal_id, data_root=handle.data_root, secret=wrong, connect_timeout=10.0
    )
    with pytest.raises(runner_client.RunnerAttachError):
        bad.attach()
    bad.release_connection()
    after = good.describe()
    assert after["runner_state"] == "running"
    assert int(after["pid"]) == handle.shell_pid
    assert after["lease"]["owner"] == good.client_id
    assert process_alive(handle.shell_pid)
    assert good.close()["status"] == "exited"
    assert handle.wait_exit() == runner_module.RUNNER_EXIT_OK
    record_evidence(
        "wrong_token",
        {"handshake": "rejected", "runtime_untouched": True, "owner": good.client_id},
    )


def test_terminal_binding_and_expired_deadline_never_execute(spawn_runner):
    handle = spawn_runner(mode="fake-emulator", snapshot_delay=1.2)
    client = _client(handle)
    client.heartbeat()
    handle.note_shell(client.describe())

    # ① terminal_id 绑定：线上携带不同 id → terminal-mismatch、零 handler 调用。
    session = client.session
    saved = session.terminal_id
    session.terminal_id = "term_other"
    try:
        with pytest.raises(runner_client.RunnerClientError) as excinfo:
            client.call("read", {"cursor": "0", "max_bytes": 1}, timeout_ms=3000)
        assert "terminal-mismatch" in str(excinfo.value)
    finally:
        session.terminal_id = saved

    # ② 固化期限：慢 handler（1.2s）期间入队的 stop(timeout=1ms) 到达时已过期 →
    #    不执行（runtime 不动）。
    slow_result: list[Any] = []
    thread = threading.Thread(
        target=lambda: slow_result.append(client.snapshot(timeout_ms=5000)), daemon=True
    )
    thread.start()
    time.sleep(0.3)
    with pytest.raises((runner_client.RunnerClientError, ipc.RequestTimeout)) as expired:
        client.call("stop", {"reason": "close"}, timeout_ms=1, io_slack=6.0)
    assert "expired" in str(expired.value) or "RequestTimeout" in type(expired.value).__name__
    thread.join(timeout=10)
    after = client.describe()
    assert after["runner_state"] == "running", "过期的 stop 不得执行"
    assert process_alive(handle.shell_pid)
    assert slow_result and slow_result[0]["recovery"] == "full"
    assert client.close()["status"] == "exited"
    assert handle.wait_exit() == runner_module.RUNNER_EXIT_OK
    record_evidence(
        "binding_and_expired",
        {
            "terminal_mismatch_rejected": True,
            "expired_stop_not_executed": True,
            "runner_state_after": after["runner_state"],
        },
    )


# ══════════════════════════════════════════════════════════════════════════
# cleanup-failed 保 owner / 重试 / 阻塞 write
# ══════════════════════════════════════════════════════════════════════════


def test_cleanup_failed_keeps_owner_and_retry_converges(spawn_runner):
    """身份探针首查 UNKNOWN → close 拒绝并保 owner；恢复后重试收敛（同 worker 复用）。"""
    handle = spawn_runner(mode="fail-once-probe")
    client = _client(handle)
    client.heartbeat()
    handle.note_shell(client.describe())
    shell_pid = handle.shell_pid

    failed = client.close()
    assert failed["status"] == "cleanup-failed"
    assert failed["ok"] is False
    assert handle.alive(), "cleanup-failed 必须保留 owner（runner 不退出）"
    assert process_alive(shell_pid), "拒绝核验时不得终止仍在运行的树"
    describe = client.describe()
    assert describe["runner_state"] == "cleanup-failed"
    assert describe["runtime_state"] == "cleanup-failed"

    retried = client.close()
    assert retried["status"] == "exited"
    assert handle.wait_exit() == runner_module.RUNNER_EXIT_OK
    assert wait_dead(shell_pid, 8.0, handle.shell_filetime)
    record_evidence(
        "cleanup_failed_retry",
        {
            "first_status": failed["status"],
            "owner_retained": True,
            "tree_alive_after_refusal": True,
            "second_status": retried["status"],
        },
    )


def test_blocked_write_does_not_stall_watchdog_or_other_connections(spawn_runner, tmp_path):
    """受控阻塞写：worker 在途时心跳/其它连接不受阻；新 input 显式 busy 不叠加。

    真实 ConPTY 在本机不会因输入量阻塞（实测 2.7MB 输入被吸收，见
    ``audit/terminal/implementation/runner/README.md``），因此用测试注入的
    ``write_hook``（gate 文件）把“阻塞”放进真实 runner 进程的 write worker 内验证；
    生产路径不注入（``write_hook`` 默认 None = ``runtime.write``）。
    """
    gate = tmp_path / "write-gate"
    gate.write_text("blocked", encoding="utf-8")
    handle = spawn_runner(
        mode="gated-write", env_extra={"PAN_TERMINAL_RUNNER_GATE_FILE": str(gate)}
    )
    owner = _client(handle, client_id="pan-owner-blocked-write")
    owner.heartbeat()
    handle.note_shell(owner.describe())
    shell_pid = handle.shell_pid

    accepted = owner.input(b"echo GATED\r", timeout_ms=8000)
    assert accepted["status"] == "accepted-in-flight", accepted
    describe = owner.describe()
    assert describe["input_worker"]["in_flight"] is True, describe["input_worker"]
    busy = owner.input(b"echo SECOND\r", timeout_ms=3000)
    assert busy["status"] == "busy", "在途 write 期间不得启动第二个阻塞调用"
    alive_during_block = handle.alive()

    # 第二条连接：心跳与 describe 必须持续可用（看门狗/lease 不被阻塞 handler 拖死）。
    other = _client(handle, client_id="pan-second")
    latencies: list[float] = []
    for _ in range(6):
        started = time.monotonic()
        assert other.heartbeat(timeout_ms=3000)["status"] == "ok"
        latencies.append(time.monotonic() - started)
        assert other.describe()["runner_state"] == "running"
        time.sleep(0.25)
    # 阻塞 > 2s（死期）期间心跳持续 → runner 不得被误杀。
    assert handle.alive() and process_alive(shell_pid)
    assert max(latencies) < 1.0, f"心跳不得被阻塞写拖慢：{latencies}"

    gate.unlink()  # 释放 worker
    converged = _wait_until(
        lambda: not owner.describe()["input_worker"]["in_flight"], 15.0
    )
    assert converged, "释放 gate 后 write worker 必须收敛"
    follow_up = owner.input(b"echo AFTER\r", timeout_ms=8000)
    assert follow_up["status"] == "done", follow_up

    final = other.close()
    assert final["status"] == "exited"
    assert handle.wait_exit() == runner_module.RUNNER_EXIT_OK
    assert wait_dead(shell_pid, 8.0, handle.shell_filetime)
    record_evidence(
        "blocked_write",
        {
            "gate_injected": True,
            "first_input_status": accepted["status"],
            "in_flight_observed": True,
            "second_input_status": busy["status"],
            "alive_during_block": alive_during_block,
            "heartbeat_latencies": [round(value, 3) for value in latencies],
            "converged_after_release": True,
            "follow_up_status": follow_up["status"],
        },
    )


# ══════════════════════════════════════════════════════════════════════════
# 跨进程 secret 重连 / token 泄漏扫描
# ══════════════════════════════════════════════════════════════════════════


def test_new_process_reconnects_with_dpapi_secret(spawn_runner, tmp_path):
    handle = spawn_runner()
    owner = _client(handle)
    owner.heartbeat()
    handle.note_shell(owner.describe())
    script = tmp_path / "reconnect.py"
    script.write_text(RECONNECT_SOURCE, encoding="utf-8")
    report_path = tmp_path / "reconnect.json"
    env = dict(os.environ)
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            str(handle.data_root),
            handle.terminal_id,
            str(report_path),
            "",
            str(REPO_ROOT),
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["attached"] is True
    assert int(report["pid"]) == handle.shell_pid
    assert int(report["filetime"]) == handle.shell_filetime
    assert report["runner_state"] == "running"
    assert report["heartbeat"] == "ok"
    assert owner.close()["status"] == "exited"
    assert handle.wait_exit() == runner_module.RUNNER_EXIT_OK
    record_evidence("cross_process_reconnect", report)


def _windows_command_line(pid: int) -> str | None:
    """只读查询进程命令行（PowerShell Get-CimInstance）；不可用返回 None。"""
    script = (
        f"$p = Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}';"
        " if ($p) { [Console]::Out.Write($p.CommandLine) }"
    )
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except Exception:  # noqa: BLE001
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


def test_no_token_leakage_in_argv_env_logs_and_files(spawn_runner):
    token = ipc.generate_token()
    handle = spawn_runner(token=token)
    client = _client(handle)
    client.heartbeat()
    client.input(b"echo LEAK-CHECK\r")
    _read_until(client, "LEAK-CHECK")
    client.close()
    assert handle.wait_exit() == runner_module.RUNNER_EXIT_OK

    checks: dict[str, Any] = {}
    # ① spawn argv（本测试构造的参数）不得含 token。
    checks["spawn_argv"] = token not in " ".join(runner_client.build_runner_argv(
        handle.terminal_id, handle.store.secret_path(handle.terminal_id)
    ))
    # ② 进程命令行（OS 视角，只读查询；不可用时如实标 unverified）。
    command_line = _windows_command_line(handle.pid)
    checks["os_command_line"] = "unverified" if command_line is None else (token not in command_line)
    # ③ stdout/stderr。
    combined = handle.stdout_text() + handle.stderr_text()
    checks["stdout_stderr"] = token not in combined
    # ④ 数据根全部文件（含 DPAPI 密文、hello）。
    leaked_files = []
    data_root = handle.data_root
    if data_root.exists():
        for path in data_root.rglob("*"):
            if path.is_file():
                blob = path.read_bytes()
                if token.encode() in blob:
                    leaked_files.append(str(path))
    checks["files_without_token"] = leaked_files == []
    checks["secret_ciphertext_is_not_plaintext"] = leaked_files == []
    # ⑤ 本进程环境（token 从未进 env；runner 侧另有启动自检）。
    checks["parent_env"] = all(token not in str(value) for value in os.environ.values())
    assert all(
        value is not False for key, value in checks.items() if key != "os_command_line"
    ), checks
    record_evidence(
        "token_leakage_scan",
        {
            "checks": checks,
            "scanned_files": len(list(data_root.rglob("*"))) if data_root.exists() else 0,
            "command_line_available": command_line is not None,
        },
    )


# ══════════════════════════════════════════════════════════════════════════
# r2 返工门控（R1 close/lease 仲裁、R2 detach 仲裁、R3+O1 启动清理、O3/O4）
# 注入与真机分层：前 9 项为进程内注入（FakeRuntime/FakeBackend/替身），
# 后 3 项为真实子进程（真实 ConPTY/Job/DPAPI，单点注入故障）。
# ══════════════════════════════════════════════════════════════════════════


class _FakeLogStub:
    total_bytes = 0
    first_retained_seq = 0


class _GatedRuntime:
    """runner 命令端点使用的 PtyRuntime 最小替身（行为可门控、可观测并发）。"""

    def __init__(
        self,
        *,
        close_behavior: str = "ok",
        close_delay: float = 0.0,
        detach_delay: float = 0.0,
    ) -> None:
        self.state = RuntimeState.RUNNING
        self.identity = None
        self.rows, self.cols = 24, 80
        self.consumer_failed = False
        self.consumer_failure_count = 0
        self.log = _FakeLogStub()
        self.close_behavior = close_behavior
        self.close_delay = float(close_delay)
        self.detach_delay = float(detach_delay)
        self.close_calls: list[str] = []
        self.detach_calls = 0
        self._active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def poll_exit(self) -> ExitInfo:
        return ExitInfo()

    def close(self, *, reason: str = "explicit-close", **_kw) -> CleanupReport:
        with self._lock:
            self.close_calls.append(str(reason))
            self._active += 1
            self.max_active = max(self.max_active, self._active)
        try:
            if self.close_delay:
                time.sleep(self.close_delay)
            if self.close_behavior == "error":
                raise RuntimeError("injected-close-failure")
            if self.close_behavior == "not-ok":
                self.state = RuntimeState.CLEANUP_FAILED
                return CleanupReport(
                    terminal_id="term_gate",
                    requested_reason=str(reason),
                    terminate_result=TerminateOutcome.TIMED_OUT,
                    state_after=RuntimeState.CLEANUP_FAILED,
                    owner_retained=True,
                    error="injected not-ok cleanup",
                )
            self.state = RuntimeState.EXITED
            return CleanupReport(
                terminal_id="term_gate",
                requested_reason=str(reason),
                terminate_result=TerminateOutcome.RETURNED,
                state_after=RuntimeState.EXITED,
            )
        finally:
            with self._lock:
                self._active -= 1

    def detach(self) -> DetachReport:
        if self.detach_delay:
            time.sleep(self.detach_delay)
        self.detach_calls += 1
        return DetachReport(
            terminal_id="term_gate", detached_at=time.time(), durable_owner="runner"
        )

    def resize(self, rows: int, cols: int) -> bool:
        return True


def _make_runner(tmp_path, tid: str, **kwargs) -> Any:
    secret_file = tmp_path / "gate-terminals" / "secrets" / f"{tid}.secret"
    return runner_module.TerminalRunner(tid, secret_file, **kwargs)


def _wire_gate(runner: Any, runtime: _GatedRuntime, *, expired: bool = True) -> None:
    runner._runtime = runtime
    runner._runner_state = "running"
    if expired:
        runner._lease_established = True
        runner._last_heartbeat = time.monotonic() - 10.0


def test_r1_watchdog_consumes_late_close_success_after_lease_loss(tmp_path):
    """R1：显式 close 在途（未收敛）+ Pan 断开 → watchdog 追踪并**消费同一 worker**
    的迟到成功；不重复发起同一阻塞调用、不恢复输入门。"""
    runner = _make_runner(
        tmp_path, "term_gate_r1a", lease_grace_seconds=0.2, close_wait=0.3,
        lease_cleanup_retries=3,
    )
    runtime = _GatedRuntime(close_delay=1.0)
    _wire_gate(runner, runtime)
    first = runner.close(reason="explicit-close")
    assert first["status"] == "closing", first  # worker 在途（1.0s > close_wait 0.3s）

    thread = threading.Thread(target=runner._watchdog_loop, daemon=True)
    thread.start()
    thread.join(timeout=10.0)
    assert not thread.is_alive()
    assert runtime.close_calls == ["explicit-close"], "迟到成功必须被消费，不得重复发起"
    assert runtime.max_active == 1, "close worker 不得并发叠加"
    assert runner._exit_code == runner_module.RUNNER_EXIT_OK
    assert runner._shutdown_flag is True
    rejected = runner.input(b"echo should-not-run\r")
    assert rejected["status"] in ("closing", "rejected"), rejected
    record_evidence(
        "r2_r1_late_success",
        {
            "injection": "FakeRuntime(close_delay=1.0) + close_wait=0.3 + lease 过期",
            "first_close_status": first["status"],
            "close_calls": runtime.close_calls,
            "max_concurrent_close": runtime.max_active,
            "exit_code": runner._exit_code,
            "input_still_rejected": True,
        },
    )


def test_r1_watchdog_retries_failed_close_and_exits_nonzero(tmp_path):
    """R1：close worker 失败 + Pan 断开 → watchdog 有界重试（不叠加）；耗尽非零终态。"""
    runner = _make_runner(
        tmp_path, "term_gate_r1b", lease_grace_seconds=0.2, close_wait=0.3,
        lease_cleanup_retries=2,
    )
    runtime = _GatedRuntime(close_behavior="error")
    _wire_gate(runner, runtime)
    first = runner.close(reason="explicit-close")
    assert first["status"] == "cleanup-failed"

    thread = threading.Thread(target=runner._watchdog_loop, daemon=True)
    thread.start()
    thread.join(timeout=15.0)
    assert not thread.is_alive()
    assert runner._exit_code == runner_module.RUNNER_EXIT_CLEANUP_FAILED
    assert runner._shutdown_flag is True
    assert runtime.max_active == 1, "重试不得并发叠加同一阻塞调用"
    assert runtime.close_calls[0] == "explicit-close"
    assert all(call.startswith("lease-expired") for call in runtime.close_calls[1:])
    assert len(runtime.close_calls) <= 1 + (1 + runner._lease_cleanup_retries)
    assert runner._closing is True and runner._expiry_in_progress is True
    rejected = runner.input(b"echo nope\r")
    assert rejected["status"] in ("closing", "rejected")
    record_evidence(
        "r2_r1_retry_exhausted",
        {
            "injection": "FakeRuntime(close_behavior=error) + retries=2",
            "close_calls": runtime.close_calls,
            "max_concurrent_close": runtime.max_active,
            "exit_code": runner._exit_code,
            "input_still_rejected": True,
        },
    )


def test_r2_expiry_first_refuses_detach_zero_state(tmp_path):
    """R2：lease-expiry 先取得关闭权 → detach 明确拒绝、零状态变化；显式 close 仍可终止。"""
    runner = _make_runner(
        tmp_path, "term_gate_r2a", lease_grace_seconds=0.2, close_wait=1.0,
        durability_probe=lambda: runner_module.DurabilityCapability(True, False, "cap"),
    )
    runtime = _GatedRuntime(close_behavior="not-ok")
    _wire_gate(runner, runtime)
    epoch = runner._lease_epoch
    outcome: dict[str, str] = {}
    thread = threading.Thread(
        target=lambda: outcome.update(
            {"o": runner._lease_expired_close(epoch=epoch, established=True)}
        ),
        daemon=True,
    )
    thread.start()
    time.sleep(0.3)  # 让 expiry 先进入生命周期门（not-ok → 有界重试中）
    refused = runner.detach()
    thread.join(timeout=8.0)
    assert refused["status"] == "detach-refused" and refused["ok"] is False
    refusal_detail = json.loads(refused["detail"])
    assert refusal_detail["reason"] == "lease-close-in-progress"
    assert runner._detached is False and runtime.detach_calls == 0
    assert outcome.get("o") == "exhausted"
    # 用户显式 close（detached 未成功）仍可终止整树。
    runtime.close_behavior = "ok"
    final = runner.close(reason="explicit-close")
    assert final["status"] == "exited"
    record_evidence(
        "r2_r2_expiry_first",
        {
            "detach_status": refused["status"],
            "detach_reason": refusal_detail.get("reason"),
            "runtime_detach_calls": runtime.detach_calls,
            "expiry_outcome": outcome.get("o"),
            "explicit_close_after": final["status"],
        },
    )


def test_r2_detach_first_skips_late_lease_expiry(tmp_path):
    """R2：detach 先完成 → 迟到 expiry 在生命周期门内复核后跳过（不杀 detached）。"""
    runner = _make_runner(
        tmp_path, "term_gate_r2b", lease_grace_seconds=0.2, close_wait=1.0,
        durability_probe=lambda: runner_module.DurabilityCapability(True, False, "cap"),
    )
    runtime = _GatedRuntime(detach_delay=0.5)
    _wire_gate(runner, runtime)
    epoch = runner._lease_epoch
    result: dict[str, Any] = {}
    thread = threading.Thread(target=lambda: result.update(runner.detach()), daemon=True)
    thread.start()
    time.sleep(0.15)  # detach 已持生命周期锁（runtime.detach 阻塞 0.5s）期间
    outcome = runner._lease_expired_close(epoch=epoch, established=True)
    thread.join(timeout=5.0)
    assert result.get("status") == "detached" and result.get("ok") is True
    assert outcome == "skipped"
    assert runtime.close_calls == [], "detached 后的迟到 expiry 不得关闭整树"
    assert runner._detached is True
    assert runner._shutdown_flag is False
    record_evidence(
        "r2_r2_detach_first",
        {
            "detach_status": result.get("status"),
            "expiry_outcome": outcome,
            "close_calls": runtime.close_calls,
            "shutdown_flag": runner._shutdown_flag,
        },
    )


def test_r2_late_heartbeat_invalidates_stale_expiry(tmp_path):
    """R2：检测到 expiry 后、取得关闭权前发生续约（世代自增）→ 旧 expiry 作废；
    世代未变且仍未续约时仍会自停（双侧断言，防"永不触发"假绿）。"""
    runner = _make_runner(tmp_path, "term_gate_r2c", lease_grace_seconds=0.2)
    runtime = _GatedRuntime()
    _wire_gate(runner, runtime)
    epoch = runner._lease_epoch
    assert runner.owner_heartbeat("pan-new")["status"] == "ok"
    outcome = runner._lease_expired_close(epoch=epoch, established=True)
    assert outcome == "skipped"
    assert runtime.close_calls == []
    assert runner._shutdown_flag is False
    # 反向：世代未变 + 仍未续约 → 正常自停。
    runner._last_heartbeat = time.monotonic() - 10.0
    epoch2 = runner._lease_epoch
    outcome2 = runner._lease_expired_close(epoch=epoch2, established=True)
    assert outcome2 == "exited"
    assert runtime.close_calls == ["lease-expired"]
    record_evidence(
        "r2_r2_heartbeat_race",
        {
            "stale_expiry_outcome": outcome,
            "fresh_expiry_outcome": outcome2,
            "close_calls": runtime.close_calls,
        },
    )


class _FakeBackendCleanup:
    """启动失败清理替身：terminate/close 可注入失败（先双失败后恢复）。"""

    def __init__(self) -> None:
        self.fail_terminate = True
        self.fail_close = True
        self.closed = False
        self.calls: list[str] = []

    def terminate(self, force: bool) -> None:
        self.calls.append(f"terminate({force})")
        if self.fail_terminate:
            raise RuntimeError("injected-terminate-failure")

    def close(self) -> dict[str, Any]:
        self.calls.append("close")
        if self.fail_close:
            raise RuntimeError("injected-close-failure")
        self.closed = True
        return {"closed": True}


def test_startup_cleanup_owner_retained_and_retry_converges(tmp_path):
    """R3：启动失败清理双失败 → 保 owner/引用、报告未收敛；恢复后同 owner 重试收敛。"""
    runner = _make_runner(tmp_path, "term_gate_r3")
    backend = _FakeBackendCleanup()
    first = runner._abort_backend(backend)
    assert first["converged"] is False
    assert first["retained"] == ["backend"]
    assert runner._retained_backend is backend
    assert runner._startup_cleanup_converged() is False
    assert "injected-terminate-failure" not in json.dumps(first)
    assert first["errors"] == ["terminate:RuntimeError", "close:RuntimeError", "close:RuntimeError"]
    backend.fail_terminate = False
    backend.fail_close = False
    second = runner.retry_startup_cleanup()
    assert second["converged"] is True
    assert runner._retained_backend is None
    assert runner._startup_cleanup_converged() is True
    record_evidence(
        "r2_r3_owner_retry",
        {
            "first": first,
            "second_converged": second["converged"],
            "errors_are_type_only": True,
        },
    )


class _FakeCloseReport:
    def __init__(self, converged: bool, detail: str = "") -> None:
        self.converged = converged
        self.detail = detail


class _FakePipeServer:
    def __init__(self) -> None:
        self.fail = True
        self.calls = 0

    def close(self, *, timeout: float | None = None) -> _FakeCloseReport:
        self.calls += 1
        if self.fail:
            return _FakeCloseReport(False, "injected-not-converged")
        return _FakeCloseReport(True, "")


def test_pipe_cleanup_non_convergence_keeps_reference_and_retries(tmp_path):
    """O1：pipe CloseReport(converged=False) 不再被丢弃——保引用、如实报告、可重试。"""
    runner = _make_runner(tmp_path, "term_gate_pipe")
    server = _FakePipeServer()
    runner._pipe_server = server  # type: ignore[assignment]
    report = runner._close_pipe_server()
    assert report["converged"] is False
    assert report["attempts"] == 2
    assert report["detail"] == "injected-not-converged"
    assert runner._retained_pipe_server is server
    assert runner._startup_cleanup_converged() is False
    server.fail = False
    retried = runner.retry_pipe_cleanup()
    assert retried["converged"] is True
    assert runner._retained_pipe_server is None
    assert runner._startup_cleanup_converged() is True
    record_evidence(
        "r2_o1_pipe_cleanup",
        {
            "first": report,
            "retried_converged": retried["converged"],
            "total_close_calls": server.calls,
        },
    )


def test_connection_threads_reclaimed_and_join_uses_shared_deadline(tmp_path):
    """O3：连接线程对象及时回收；join 用共享总 deadline（非 N×2s），未收敛如实报告。"""
    runner = _make_runner(tmp_path, "term_gate_join")
    gate = threading.Event()

    def _blocked() -> None:
        gate.wait(8.0)

    threads = [threading.Thread(target=_blocked, daemon=True) for _ in range(4)]
    for thread in threads:
        thread.start()
    with runner._conn_lock:
        runner._conn_threads.extend(threads)
    started = time.monotonic()
    info = runner._join_connections(total_deadline=0.5)
    elapsed = time.monotonic() - started
    assert elapsed < 1.5, f"join 必须是共享总 deadline（实测 {elapsed:.2f}s）"
    assert info["remaining"] == 4 and info["joined"] == 0
    gate.set()
    for thread in threads:
        thread.join(timeout=3.0)
    assert runner._reap_connection_threads() == 4
    with runner._conn_lock:
        assert runner._conn_threads == []
    record_evidence(
        "r2_o3_join_deadline",
        {
            "elapsed_seconds": round(elapsed, 3),
            "remaining_reported": info["remaining"],
            "reaped": 4,
        },
    )


class _HugeNoteEmulator:
    """快照替身：engine/note 超大，驱动 detail 字段级缩减路径。"""

    feed_lag = False

    def feed(self, data: bytes) -> None:
        pass

    def feed_at(self, seq: int, data: bytes) -> None:
        pass

    def resize(self, rows: int, cols: int) -> None:
        pass

    def snapshot(self, *, timeout: float = 2.0):
        from packages.core.terminal.contracts import AppliedSnapshot, Fidelity, Recovery

        return AppliedSnapshot(
            serialized_screen="SCREEN",
            cursor=0,
            rows=24,
            cols=80,
            fidelity=Fidelity.FULL,
            recovery=Recovery.FULL,
            engine="E" * 20000,
            note="N" * 20000,
        )

    def reset_baseline(self):
        return self.snapshot()


def test_detail_generation_is_valid_bounded_json(tmp_path):
    """O4：detail 超界必须字段级缩减为**合法** JSON（不截断字符串），各处路径可解析。"""
    runner = _make_runner(tmp_path, "term_gate_detail")
    runner._runtime = _GatedRuntime()
    runner._runner_state = "running"

    original_describe = runner.describe()

    big = dict(original_describe)
    big["note"] = "N" * 20000
    big["durability"] = {"capable": False, "ambient_job": True, "detail": "D" * 30000}
    runner.describe = lambda: big  # type: ignore[method-assign]
    text = runner._detail_json()
    assert len(text) <= runner_module._DETAIL_BUDGET
    assert isinstance(json.loads(text), dict)

    detail = runner._detail_with({"note": "X" * 20000, "reconnect_hint": {"blob": "Z" * 10000}})
    assert len(detail) <= runner_module._DETAIL_BUDGET
    parsed2 = json.loads(detail)
    assert "note" in parsed2 or "reconnect_hint" in parsed2

    # 快照响应路径：超大 engine/note 也必须产出可解析 detail。
    runner.describe = original_describe  # type: ignore[method-assign]
    runner._emulator = _HugeNoteEmulator()
    payload = runner.snapshot(timeout_ms=100)
    assert payload["status"] in ("running", "degraded", "unavailable")
    assert isinstance(json.loads(payload["detail"]), dict)
    assert len(payload["detail"]) <= runner_module._DETAIL_BUDGET
    record_evidence(
        "r2_o4_detail_json",
        {
            "detail_len": len(text),
            "with_extra_len": len(detail),
            "snapshot_detail_len": len(payload["detail"]),
            "all_parseable": True,
        },
    )


# --------------------------------------------------------------------------
# r2 真机：启动失败/accept 失败的可观测性与真实后代清理证据
# --------------------------------------------------------------------------


def _read_status(handle: RunnerHandle) -> dict[str, Any]:
    path = handle.data_root / runner_module._STATUS_DIRNAME / f"{handle.terminal_id}.json"
    assert path.is_file(), f"状态文件缺失：{path}"
    return json.loads(path.read_text(encoding="utf-8"))


def test_startup_refuse_real_cleans_tree_and_reports_converged(spawn_runner, tmp_path):
    """R3 真机：真实 spawn 后注入拒绝 → 真实 abort 清理**证明收敛**（exit 4 + 状态文件），
    后代整树实测被清（保 guard 真实路径；非进程退出兜底）。"""
    pid_file = tmp_path / "spawned.json"
    handle = spawn_runner(
        mode="startup-refuse-real",
        env_extra={"PAN_TERMINAL_RUNNER_PID_FILE": str(pid_file)},
    )
    code = handle.wait_exit(timeout=30)
    assert code == runner_module.RUNNER_EXIT_BOOTSTRAP_FAILED
    status = _read_status(handle)
    assert status["phase"] == "bootstrap-failed"
    assert status["exit_code"] == runner_module.RUNNER_EXIT_BOOTSTRAP_FAILED
    assert status["cleanup"]["converged"] is True
    assert status["cleanup"]["retained"] == []
    assert status["cleanup"]["startup"]["converged"] is True
    spawned = json.loads(pid_file.read_text(encoding="utf-8"))
    assert spawned["pid"] and int(spawned["filetime"]) > 0
    assert wait_dead(int(spawned["pid"]), 8.0, int(spawned["filetime"])), (
        "真实 abort 清理必须覆盖 spawn 出的整树（保 guard、非进程退出兜底）"
    )
    assert "exit code=4" in handle.stderr_text()
    record_evidence(
        "r2_startup_real_converged",
        {
            "exit_code": code,
            "cleanup_converged": True,
            "spawned_pid_dead": True,
            "spawned_pid": spawned["pid"],
        },
    )


def test_startup_refuse_uncleaned_reports_unproven_and_retains_owner(spawn_runner, tmp_path):
    """R3 真机：清理双失败（注入）→ **区分未证明清理**：exit 6 + 状态文件 retained；
    不冒称内核兜底已实测（进程退出后的清理由本场景实测记录与措辞限定）。"""
    pid_file = tmp_path / "spawned.json"
    handle = spawn_runner(
        mode="startup-refuse-uncleaned",
        env_extra={"PAN_TERMINAL_RUNNER_PID_FILE": str(pid_file)},
    )
    code = handle.wait_exit(timeout=30)
    assert code == runner_module.RUNNER_EXIT_CLEANUP_UNPROVEN
    status = _read_status(handle)
    assert status["phase"] == "bootstrap-failed"
    assert status["exit_code"] == runner_module.RUNNER_EXIT_CLEANUP_UNPROVEN
    assert status["cleanup"]["converged"] is False
    assert "backend" in status["cleanup"]["retained"]
    assert status["cleanup"]["startup"]["converged"] is False
    serialized = json.dumps(status, ensure_ascii=False)
    assert "RuntimeError" in serialized and "injected-close-failure" not in serialized
    stderr = handle.stderr_text()
    assert "exit code=6" in stderr and "bootstrap-cleanup-unproven" in stderr
    spawned = json.loads(pid_file.read_text(encoding="utf-8"))
    assert spawned["pid"]
    started = time.monotonic()
    tree_dead = wait_dead(int(spawned["pid"]), 10.0, int(spawned["filetime"]))
    observed_seconds = round(time.monotonic() - started, 3)
    assert tree_dead, "进程退出应关闭 guard 句柄（本场景实测；不代表通用承诺）"
    record_evidence(
        "r2_startup_uncleaned",
        {
            "exit_code": code,
            "cleanup_converged": False,
            "retained": status["cleanup"]["retained"],
            "startup_report": status["cleanup"]["startup"],
            "tree_dead_after_exit": True,
            "seconds_to_dead_after_exit": observed_seconds,
            "note": "内核守卫随进程退出的清除为本场景实测；未泛化为通用保证",
        },
    )


def test_accept_failure_is_not_exit_zero_and_observable(spawn_runner, tmp_path):
    """O2 真机：accept 循环异常即使清理收敛也**非 exit 0**（7），原因公开且脱敏、
    状态文件区分清理收敛；真实整树被关闭。"""
    pid_file = tmp_path / "spawned.json"
    handle = spawn_runner(
        mode="accept-fail",
        env_extra={"PAN_TERMINAL_RUNNER_PID_FILE": str(pid_file)},
    )
    code = handle.wait_exit(timeout=45)
    assert code == runner_module.RUNNER_EXIT_ACCEPT_FAILED
    status = _read_status(handle)
    assert status["phase"] == "accept-failed"
    assert status["exit_code"] == runner_module.RUNNER_EXIT_ACCEPT_FAILED
    assert status["cleanup"]["converged"] is True
    stderr = handle.stderr_text()
    assert "exit code=7" in stderr and "accept-failed" in stderr
    spawned = json.loads(pid_file.read_text(encoding="utf-8"))
    assert wait_dead(int(spawned["pid"]), 8.0, int(spawned["filetime"]))
    record_evidence(
        "r2_accept_failed",
        {
            "exit_code": code,
            "cleanup_converged": True,
            "stderr_reason": "accept-failed",
            "spawned_pid_dead": True,
        },
    )
