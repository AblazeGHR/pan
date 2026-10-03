"""P1 Terminal 生产模块组合验证：真实 ConPtyBackend + Runner + IPC + HeadlessEmulator。

装配（测试专属 launcher，见 ``LAUNCHER_SOURCE``）：

- 单进程内：``TerminalRunner(terminal_id, secret_file, emulator=HeadlessEmulator(...))``；
  runner 内部经 ``ConPtyBackend.spawn`` 起真实 ConPTY shell，IPC 走真实命名管道 +
  DPAPI 秘密 + HMAC 会话；**不注入 identity_probe**（必须装配 retained-handle
  ``backend.probe``）。
- 自建数据根（secrets/runner-status）+ 自建命名管道；不启动既有 Pan 服务、不触账号/
  provider/浏览器；全程 headless↔headless（不声明浏览器渲染兼容）。
- launcher 额外提供**文件控制通道**（test-only）：对 launcher 进程内的同一
  ``HeadlessEmulator`` 实例调用 ``snapshot/resize_wait/diagnostics/reset_baseline/
  restore_screen/test_stall`` 等确认面（runner IPC 不暴露这些方法）。
- **emulator 生命周期归组合方**：runner 不关闭注入的 emulator；launcher 在
  ``runner.run()`` 返回后显式 ``emulator.close()`` 并记录 ``EmulatorCloseReport``；
  runner 硬死路径由 emulator 自持 Job guard 的内核兜底覆盖（本套件实测记录）。

证据：``PAN_TERMINAL_COMPOSITION_EVIDENCE_DIR`` 设置时写 JSON（UTF-8）；清理只对
**自有**资源做同 handle 身份核验（raw FILETIME + Wait），不按名广杀。

要求（本轮 MA 口径）：无浏览器持续供料；驱逐后协议 A applied cursor 恢复 + 续流单次
消费；UTF8/CSI/OSC 边界；引擎 partial/lag/reset_unconfirmed 不假 full；resize_wait
只确认引擎、PTY/引擎尺寸分列、失败/过期不假三方一致；启动引擎失败 owner 保留；关闭
顺序 / 管道断连不杀 runtime / 心跳独立连接与慢 snapshot；runner 结束 sidecar+PTY 整树
无残留；write/close 可追踪 worker 不阻塞事件循环；detach 受限拒绝零变化。
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

from packages.core.terminal import runner_client, secret_store, win_pipe
from packages.core.terminal import runner as runner_module
from packages.core.terminal.contracts import ProcessStatus
from packages.core.terminal.emulator import EmulatorStartupError, HeadlessEmulator

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="组合验证依赖 Windows ConPTY / Job Object / DPAPI / 命名管道",
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SIDECAR_DIR = REPO_ROOT / "packages/core/terminal/emulator_sidecar"
LAUNCHER_HARD_TIMEOUT = 240.0


def _sidecar_deps_present() -> bool:
    return (SIDECAR_DIR / "node_modules/@xterm/headless/package.json").is_file()


requires_sidecar = pytest.mark.skipif(
    not _sidecar_deps_present(),
    reason="sidecar 依赖未安装（需在 emulator_sidecar/ 执行 npm ci）",
)


# ══════════════════════════════════════════════════════════════════════════
# 证据 / 工具
# ══════════════════════════════════════════════════════════════════════════


def _evidence_dir() -> Path | None:
    value = os.environ.get("PAN_TERMINAL_COMPOSITION_EVIDENCE_DIR")
    if not value:
        return None
    path = Path(value)
    path.mkdir(parents=True, exist_ok=True)
    return path


def record_evidence(name: str, payload: dict[str, Any]) -> None:
    directory = _evidence_dir()
    if directory is None:
        return
    (directory / f"{name}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )


def _wait_until(predicate: Callable[[], Any], timeout: float, interval: float = 0.05) -> Any:
    deadline = time.monotonic() + float(timeout)
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return predicate()


def _pid_exists(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = ctypes.c_void_p
    k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k32.CloseHandle.argtypes = (ctypes.c_void_p,)
    ctypes.set_last_error(0)
    handle = k32.OpenProcess(0x00100000, False, int(pid))
    if not handle:
        return int(ctypes.get_last_error()) != 87  # ERROR_INVALID_PARAMETER
    k32.CloseHandle(ctypes.c_void_p(handle))
    return True


def process_dead(pid: int, filetime: int | None = None) -> bool:
    probe = win_pipe.probe_process(int(pid))
    if probe.status is ProcessStatus.DEAD:
        return True
    if probe.status is ProcessStatus.ALIVE:
        if (
            filetime is not None
            and probe.identity is not None
            and int(probe.identity.created_at_filetime or -1) != int(filetime)
        ):
            return True
        return False
    return not _pid_exists(int(pid))


def wait_dead(pid: int, timeout: float, filetime: int | None = None) -> bool:
    return bool(_wait_until(lambda: process_dead(int(pid), filetime), timeout))


def _enum_str(value: Any) -> str:
    text = str(value)
    return text.rsplit(".", 1)[-1].lower()


_FIDELITY_RANK = {"unavailable": 0, "partial": 1, "full": 2}
_RECOVERY_RANK = {"none": 0, "degraded": 1, "partial": 2, "full": 3}


def _read_until(
    client: "runner_client.RunnerClient",
    marker: bytes,
    *,
    timeout: float = 20.0,
    cursor: int = 0,
) -> tuple[bytes, int]:
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
        if marker in buf:
            return bytes(buf), current
        time.sleep(0.05)
    return bytes(buf), current


def _wait_total_stable(
    client: "runner_client.RunnerClient", *, timeout: float = 25.0, interval: float = 0.15,
    stable_polls: int = 4,
) -> int:
    """等生产端 total_bytes 连续若干轮不变（快照/续流范围才有确定边界）。"""
    deadline = time.monotonic() + timeout
    last = -1
    stable = 0
    while time.monotonic() < deadline:
        total = int(client.describe()["total_bytes"])
        if total == last:
            stable += 1
            if stable >= stable_polls:
                return total
        else:
            stable = 0
            last = total
        time.sleep(interval)
    return last


def _read_range(
    client: "runner_client.RunnerClient", cursor: int, end: int
) -> bytes:
    """单遍读取 [cursor, end)：每个字节恰好消费一次（next_cursor 严格推进）。"""
    buf = bytearray()
    current = int(cursor)
    while current < int(end):
        page = client.read(current)
        if page["gap"] is not None:
            raise AssertionError(f"read gap at {current}: {page['gap']}")
        if not page["data"]:
            break
        assert page["next_cursor"] > current
        buf += page["data"]
        current = int(page["next_cursor"])
    return bytes(buf)


def _wait_engine_caught_up(
    session: "CompositionSession",
    client: "runner_client.RunnerClient",
    total: int,
    *,
    timeout: float = 180.0,
    poll: float = 0.25,
) -> dict[str, Any]:
    """轮询**本地** diagnostics 等引擎 applied cursor 追平，再取一次线上快照。

    组合实测发现：applier 严格单飞 + 真实 ConPTY 小读块（~百字节/op）→ 引擎吞吐
    受每 op 往返限制；快照控制 op 会排在积压 feeds 之后。因此等待追平用本地
    diagnostics（不占 op 队列），线上快照只在追平后取一次。
    """
    deadline = time.monotonic() + float(timeout)
    applied = -1
    while time.monotonic() < deadline:
        diagnostics = session.control("diagnostics")
        applied = int(diagnostics.get("applied_cursor", -1))
        if applied >= int(total):
            break
        time.sleep(poll)
    snap = client.snapshot(timeout_ms=8000)
    assert int(snap.get("cursor", -1)) >= int(total), (
        f"引擎未追平：applied={applied} snapshot_cursor={snap.get('cursor')} total={total}"
    )
    return snap


# ══════════════════════════════════════════════════════════════════════════
# launcher（测试专属组合装配；非生产模块）
# ══════════════════════════════════════════════════════════════════════════

LAUNCHER_SOURCE = r'''
import json, os, sys, threading, time

sys.path.insert(0, os.environ["PAN_TERMINAL_COMPOSITION_REPO"])
from packages.core.terminal import win_pipe
from packages.core.terminal.emulator import HeadlessEmulator
from packages.core.terminal.runner import TerminalRunner

terminal_id, secret_file, report_path, control_dir, mode = sys.argv[1:6]

def hard_exit():
    time.sleep(float(os.environ.get("PAN_COMP_HARD_TIMEOUT", "240")))
    os._exit(97)

threading.Thread(target=hard_exit, daemon=True).start()

def dump(payload):
    tmp = report_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, default=str)
    os.replace(tmp, report_path)

own = win_pipe.current_process_identity()
report = {
    "mode": mode,
    "phase": "starting",
    "pid": os.getpid(),
    "identity": {"pid": own.pid, "filetime": str(own.created_at_filetime)},
}
dump(report)

kwargs = {"cols": 80, "rows": 24}
if mode == "compose-tight":
    kwargs.update(control_timeout=0.2, snapshot_timeout=0.5, max_queue_bytes=4096)
elif mode == "compose-fake-slowreset":
    kwargs.update(sidecar_path=os.environ["PAN_COMP_FAKE_SIDECAR"], control_timeout=0.3)

emulator = HeadlessEmulator(**kwargs)
runner = TerminalRunner(terminal_id, secret_file, emulator=emulator)
report.update({"phase": "emulator-ready", "emulator": emulator.diagnostics()})
dump(report)

control_request = os.path.join(control_dir, "control-request.json")
control_result = os.path.join(control_dir, "control-result.json")

def write_result(payload):
    tmp = control_result + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, default=str)
    os.replace(tmp, control_result)

def execute(cmd, args):
    if cmd == "describe":
        return runner.describe()
    if cmd == "snapshot":
        return emulator.snapshot(timeout=float(args.get("timeout", 2.0))).as_dict()
    if cmd == "diagnostics":
        return emulator.diagnostics()
    if cmd == "resize_wait":
        return emulator.resize_wait(
            int(args["rows"]), int(args["cols"]), timeout=float(args.get("timeout", 2.0))
        )
    if cmd == "reset_baseline":
        return emulator.reset_baseline().as_dict()
    if cmd == "restore_screen":
        return emulator.restore_screen(str(args["serialized"]))
    if cmd == "test_stall":
        return emulator._test_stall(int(args["ms"]))
    if cmd == "producer_frontier":
        return int(emulator.producer_frontier)
    if cmd == "engine_alive":
        return bool(emulator.engine_alive)
    raise ValueError("unknown control command: %s" % cmd)

def control_loop():
    while True:
        try:
            if os.path.exists(control_request):
                with open(control_request, "r", encoding="utf-8") as fh:
                    request = json.load(fh)
                os.unlink(control_request)
                try:
                    result = execute(request["cmd"], request.get("args") or {})
                    write_result({"seq": request["seq"], "cmd": request["cmd"], "ok": True, "result": result})
                except Exception as exc:
                    write_result({"seq": request["seq"], "cmd": request["cmd"], "ok": False,
                                  "error": "%s: %s" % (type(exc).__name__, exc)})
        except FileNotFoundError:
            pass
        except Exception as exc:
            try:
                write_result({"seq": -1, "cmd": "control-loop", "ok": False,
                              "error": type(exc).__name__})
            except Exception:
                pass
        time.sleep(0.02)

threading.Thread(target=control_loop, daemon=True).start()

code = 5
runner_error = None
try:
    code = runner.run()
except Exception as exc:
    runner_error = type(exc).__name__
    code = 5

# 组合方负责 emulator 生命周期（runner 不关闭注入的 emulator）。
close_report = None
try:
    close_report = emulator.close(timeout=8.0).as_dict()
except Exception as exc:
    close_report = {"closed": False, "error": type(exc).__name__}

report.update({
    "phase": "finished",
    "exit_code": code,
    "runner_error": runner_error,
    "emulator_close": close_report,
    "emulator_diag": emulator.diagnostics(),
})
dump(report)
sys.exit(code)
'''

#: 测试专属 stub sidecar（仅用于确定性构造 reset_unconfirmed 状态；非真实引擎）。
FAKE_SIDECAR_SOURCE = r'''
"use strict";
const state = { applied: 0, rows: 24, cols: 80 };

function send(header, payload) {
  const body = payload || Buffer.alloc(0);
  const head = Buffer.from(JSON.stringify(header), "utf8");
  const total = head.length + body.length;
  const out = Buffer.alloc(8 + total);
  out.writeUInt32LE(total, 0);
  out.writeUInt32LE(head.length, 4);
  head.copy(out, 8);
  body.copy(out, 8 + head.length);
  process.stdout.write(out);
}

let buffer = Buffer.alloc(0);
process.stdin.on("data", (data) => {
  buffer = Buffer.concat([buffer, data]);
  while (true) {
    if (buffer.length < 8) return;
    const total = buffer.readUInt32LE(0);
    const hlen = buffer.readUInt32LE(4);
    if (buffer.length < 8 + total) return;
    const header = JSON.parse(buffer.slice(8, 8 + hlen).toString("utf8"));
    const payload = buffer.slice(8 + hlen, 8 + total);
    buffer = buffer.slice(8 + total);
    handle(header, payload);
  }
});

function handle(header, payload) {
  const op = header.op;
  const type = header.type;
  if (type === "hello") {
    state.cols = header.cols || state.cols;
    state.rows = header.rows || state.rows;
    send({ type: "ready", op: op, engine: "fake-sidecar-test/1.0",
           node_version: process.version, pid: process.pid,
           cols: state.cols, rows: state.rows });
    return;
  }
  if (type === "feed") {
    state.applied = Number(header.abs_end);
    send({ type: "applied", op: op, applied_bytes: String(state.applied),
           processed_frontier: String(state.applied), pending_tail_bytes: 0,
           parser_dirty: false });
    return;
  }
  if (type === "resize") {
    state.rows = header.rows; state.cols = header.cols;
    send({ type: "applied", op: op, rows: state.rows, cols: state.cols,
           applied_bytes: String(state.applied) });
    return;
  }
  if (type === "snapshot" || type === "barrier") {
    const body = Buffer.from("FAKE-SCREEN:" + state.applied, "utf8");
    send({ type: "snapshot", op: op, applied_bytes: String(state.applied),
           rows: state.rows, cols: state.cols, fidelity: "full", recovery: "full",
           reasons: [] }, body);
    return;
  }
  if (type === "reset") {
    // 确定性迟到 ack：> control_timeout（0.3s）后回填账本（模拟在途未确认）
    setTimeout(() => {
      send({ type: "applied", op: op, applied_bytes: String(state.applied),
             baseline_frontier: String(state.applied) });
    }, 1000);
    return;
  }
  if (type === "restore") {
    send({ type: "applied", op: op, applied_bytes: String(state.applied) });
    return;
  }
  if (type === "shutdown") {
    send({ type: "applied", op: op, applied_bytes: String(state.applied) });
    setTimeout(() => process.exit(0), 10);
    return;
  }
  if (type === "test_stall") {
    setTimeout(() => send({ type: "applied", op: op, applied_bytes: String(state.applied) }),
               Math.min(Number(header.ms) || 0, 10000));
    return;
  }
  if (type === "test_inject_error") {
    send({ type: "error", op: op, code: "injected", detail: "fake" });
    return;
  }
  send({ type: "error", op: op, code: "unknown-op", detail: String(type) });
}
'''


# ══════════════════════════════════════════════════════════════════════════
# 会话（launcher 子进程 + 控制通道 + 真实 IPC 客户端）
# ══════════════════════════════════════════════════════════════════════════


class _HeartbeatKeeper:
    """独立连接上的持续心跳（模拟真实 Pan 的 1s 租约续约，长步骤期间保活）。"""

    def __init__(self, session: "CompositionSession", *, interval: float = 0.35) -> None:
        self._session = session
        self._interval = float(interval)
        self._stop = threading.Event()
        self.client = session.client("pan-heartbeat-keeper")
        assert self.client.heartbeat()["status"] == "ok"
        self.latencies: list[float] = []
        self.errors: list[str] = []
        self._thread = threading.Thread(target=self._loop, name="compose-heartbeat", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                if self.client.heartbeat(timeout_ms=3000).get("status") != "ok":
                    self.errors.append("non-ok")
                    return
            except Exception as exc:  # noqa: BLE001 - 断连/超时类型记录
                self.errors.append(type(exc).__name__)
                return
            self.latencies.append(round(time.monotonic() - started, 3))
            self._stop.wait(self._interval)

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=3.0)
        try:
            self.client.release_connection()
        except Exception:  # noqa: BLE001
            pass


class CompositionSession:
    def __init__(
        self,
        proc: subprocess.Popen,
        terminal_id: str,
        data_root: Path,
        store: secret_store.SecretStore,
        report_path: Path,
        control_dir: Path,
    ) -> None:
        self.proc = proc
        self.terminal_id = terminal_id
        self.data_root = data_root
        self.store = store
        self.report_path = report_path
        self.control_dir = control_dir
        self.control_request = control_dir / "control-request.json"
        self.control_result = control_dir / "control-result.json"
        self.runner_pid: int | None = None
        self.runner_filetime: int | None = None
        self.payload: secret_store.SecretPayload | None = None
        self.shell_pid: int | None = None
        self.shell_filetime: int | None = None
        self.sidecar_pid: int | None = None
        self.sidecar_filetime: int | None = None
        self._seq = 0
        self._control_lock = threading.Lock()
        self._keeper: _HeartbeatKeeper | None = None
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
        except Exception:  # noqa: BLE001
            pass

    # -- 报告 -----------------------------------------------------------
    def report(self) -> dict[str, Any]:
        if not self.report_path.is_file():
            return {}
        try:
            return json.loads(self.report_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}

    def wait_report(self, phase: str = "finished", *, timeout: float = 90.0) -> dict[str, Any]:
        data = _wait_until(lambda: self.report() if self.report().get("phase") == phase else None, timeout)
        assert data, f"launcher 未到达 phase={phase}: {self.report()} stderr={self.stderr_text()[-400:]}"
        return data

    def stderr_text(self) -> str:
        return "".join(self._stderr)

    def stdout_text(self) -> str:
        return "".join(self._stdout)

    # -- 控制通道 --------------------------------------------------------
    def control(self, cmd: str, *, wait: float = 30.0, **args: Any) -> Any:
        with self._control_lock:
            self._seq += 1
            seq = self._seq
            if self.control_result.exists():
                self.control_result.unlink()
            tmp = self.control_request.with_name("control-request.tmp")
            tmp.write_text(
                json.dumps({"seq": seq, "cmd": cmd, "args": args}, ensure_ascii=False),
                encoding="utf-8",
            )
            os.replace(tmp, self.control_request)
            deadline = time.monotonic() + float(wait)
            while time.monotonic() < deadline:
                if self.control_result.is_file():
                    try:
                        payload = json.loads(self.control_result.read_text(encoding="utf-8"))
                    except Exception:  # noqa: BLE001
                        time.sleep(0.02)
                        continue
                    if payload.get("seq") == seq:
                        if not payload.get("ok"):
                            raise RuntimeError(f"control {cmd} failed: {payload.get('error')}")
                        return payload.get("result")
                time.sleep(0.02)
        raise TimeoutError(f"control {cmd} timed out after {wait}s")

    # -- 身份 ------------------------------------------------------------
    def note_shell(self, describe: dict[str, Any]) -> None:
        self.shell_pid = int(describe["pid"])
        self.shell_filetime = int(describe["process_created_at_filetime"])

    def note_sidecar(self, diagnostics: dict[str, Any]) -> None:
        pid = diagnostics.get("sidecar_pid")
        filetime = diagnostics.get("sidecar_filetime")
        if pid and filetime:
            self.sidecar_pid = int(pid)
            self.sidecar_filetime = int(filetime)

    # -- 客户端 ----------------------------------------------------------
    def keepalive(self, *, interval: float = 0.35) -> _HeartbeatKeeper:
        """启动独立连接上的持续心跳（长步骤期间维持 managed lease）。"""
        if self._keeper is None:
            self._keeper = _HeartbeatKeeper(self, interval=interval)
        return self._keeper

    def client(self, client_id: str | None = None) -> "runner_client.RunnerClient":
        client = runner_client.RunnerClient(
            self.terminal_id,
            data_root=self.data_root,
            connect_timeout=15.0,
            request_timeout_ms=15_000,
            close_timeout_ms=25_000,
            client_id=client_id,
        )
        client.attach()
        return client

    # -- 清理（仅自有资源，同 handle 身份核验） ---------------------------
    def cleanup(self) -> dict[str, Any]:
        if self._keeper is not None:
            self._keeper.stop()
        # 正常路径：launcher 写完 finished 报告后立即 exit；先等其退出窗口收敛，
        # 避免把"正在退出"误记成存活（硬死路径进程已消亡，立即返回）。
        try:
            self.proc.wait(timeout=4.0)
        except subprocess.TimeoutExpired:
            pass
        trace: dict[str, Any] = {"alive_before_cleanup": bool(self.runner_pid and process_dead(self.runner_pid, self.runner_filetime) is False)}
        if self.runner_pid and not process_dead(self.runner_pid, self.runner_filetime):
            trace["runner"] = win_pipe.terminate_verified_process(self.runner_pid, self.runner_filetime)
        if self.sidecar_pid and not process_dead(self.sidecar_pid, self.sidecar_filetime):
            trace["sidecar"] = win_pipe.terminate_verified_process(self.sidecar_pid, self.sidecar_filetime)
        if self.shell_pid and not process_dead(self.shell_pid, self.shell_filetime):
            trace["shell"] = win_pipe.terminate_verified_process(self.shell_pid, self.shell_filetime)
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            trace["launcher_wait"] = "timeout"
        for thread in self._drains:
            thread.join(timeout=2)
        for stream in (self.proc.stdout, self.proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception:  # noqa: BLE001
                pass
        trace["exit_code"] = self.proc.returncode
        return trace


@pytest.fixture
def composed(tmp_path, request):
    sessions: list[CompositionSession] = []

    def _spawn(
        *,
        mode: str = "compose",
        terminal_id: str | None = None,
        env_extra: dict[str, str] | None = None,
        boot_timeout: float = 60.0,
    ) -> CompositionSession:
        tid = terminal_id or f"term_{os.urandom(6).hex()}"
        data_root = tmp_path / f"terminals_{tid}"
        store = secret_store.SecretStore(data_root)
        store.ensure_secrets_dir()
        secret_file = store.secret_path(tid)
        launcher = tmp_path / f"composition_launcher_{tid}.py"
        launcher.write_text(LAUNCHER_SOURCE, encoding="utf-8")
        control_dir = tmp_path / f"control_{tid}"
        control_dir.mkdir()
        report_path = tmp_path / f"report_{tid}.json"
        env = dict(os.environ)
        env["PAN_TERMINAL_COMPOSITION_REPO"] = str(REPO_ROOT)
        env["PAN_COMP_HARD_TIMEOUT"] = str(int(LAUNCHER_HARD_TIMEOUT))
        if env_extra:
            env.update(env_extra)
        proc = subprocess.Popen(
            [
                sys.executable,
                str(launcher),
                tid,
                str(secret_file),
                str(report_path),
                str(control_dir),
                mode,
            ],
            cwd=str(REPO_ROOT),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        session = CompositionSession(proc, tid, data_root, store, report_path, control_dir)
        sessions.append(session)
        record = store.wait_for_bootstrap_identity(tid, timeout=boot_timeout)
        session.runner_pid = int(record.pid)
        session.runner_filetime = int(record.filetime)
        session.payload = runner_client.complete_bootstrap(store, tid)
        # 真实 Pan 语义：持续心跳（独立连接）维持 managed lease；组合用例的长步骤
        # （大输出/停滞/慢快照）不得因"测试没有心跳"触发死期自停。
        session.keepalive()
        return session

    yield _spawn
    trace: dict[str, Any] = {}
    for session in sessions:
        trace[session.terminal_id] = session.cleanup()
    record_evidence(f"composition_cleanup_{request.node.name}", trace)


# ══════════════════════════════════════════════════════════════════════════
# 组合用例
# ══════════════════════════════════════════════════════════════════════════


@requires_sidecar
def test_composition_browserless_feed_eviction_protocol_a_single_consumption(
    composed, tmp_path
):
    """T17/T7 组合：全程无浏览器持续供料；>256KiB 驱逐后协议 A（applied cursor）
    恢复 + 续流单次消费（headless↔headless 对拍）。"""
    session = composed()
    client = session.client("pan-compose-owner")
    assert client.heartbeat()["status"] == "ok"
    describe = session.control("describe")
    session.note_shell(describe)
    assert describe["consumer"]["failed"] is False

    # 大输出（> 日志窗口）触发驱逐；仿真器全量消费（引擎吞吐受限 → 等待追平）。
    blob = tmp_path / "big.txt"
    content = b"".join(
        (b"PAN-COMP-PAD-%05d " % index) + b"x" * 60 + b"\r\n" for index in range(3800)
    )
    assert len(content) > 262144
    blob.write_bytes(content)
    submit_at = time.monotonic()  # F2 容量口径：单一 monotonic 从首次提交起算（不扣 probe 窗口）
    client.input(f'type "{blob}"\r'.encode("utf-8"))
    total = _wait_until(
        lambda: client.describe()["total_bytes"] if client.describe()["total_bytes"] > 262144 else None,
        60.0,
    )
    assert total, f"未触发驱逐：total={client.describe()['total_bytes']}"

    marker = f"COMP-END-{os.urandom(3).hex()}"
    client.input(f"echo {marker}\r".encode("utf-8"))
    _read_until(client, marker.encode(), timeout=20)
    total_now = client.describe()["total_bytes"]
    backlog = session.control("diagnostics")
    snap = _wait_engine_caught_up(session, client, total_now)
    catch_up_seconds = round(time.monotonic() - submit_at, 3)
    caught_diag = session.control("diagnostics")
    assert int(snap["cursor"]) == int(total_now), (
        f"applied cursor 未追平：cursor={snap['cursor']} total={total_now}"
    )
    assert marker in snap["serialized_screen"], "驱逐前的屏幕状态必须仍在权威快照中"
    page = client.read(int(snap["cursor"]))
    assert page["gap"] is None, "applied cursor 不得出现 gap（日志驱逐不影响权威位置）"
    assert page["data"] == b""

    # 续流单次消费：新尾部从 cursor 起只出现一次。
    tail_marker = f"COMP-TAIL-{os.urandom(3).hex()}"
    client.input(f"echo {tail_marker}\r".encode("utf-8"))
    _read_until(client, tail_marker.encode(), timeout=20, cursor=int(snap["cursor"]))
    total_stable = _wait_total_stable(client)
    tail_bytes = _read_range(client, int(snap["cursor"]), total_stable)
    # 单次消费（结构性）：[cursor, total) 每个字节恰好一次、cursor 严格推进、无 gap。
    # 注：marker 在原始流中可合法出现多次（cmd 回显 + 命令输出 + ConPTY OSC 标题），
    # 不能用计数判"双消费"；双消费由下方 headless↔headless 对拍兜底。
    assert len(tail_bytes) == int(total_stable) - int(snap["cursor"])
    assert tail_marker.encode() in tail_bytes
    page_tail = client.read(int(snap["cursor"]))
    assert page_tail["seq"] == int(snap["cursor"])

    # headless↔headless 对拍：checker 从 serialized 重建 + **只喂一次**同一字节范围。
    checker = HeadlessEmulator(
        cols=int(snap["cols"]), rows=int(snap["rows"]), start_cursor=int(snap["cursor"])
    )
    try:
        assert checker.restore_screen(snap["serialized_screen"])
        checker.feed_at(int(snap["cursor"]), tail_bytes)
        checker_snap = checker.snapshot(timeout=30.0)
        live = _wait_engine_caught_up(session, client, total_stable, timeout=120.0)
        assert checker_snap.serialized_screen == live["serialized_screen"], (
            "协议 A 对拍不一致（单次消费/状态重建失败）"
        )
        fidelity, recovery = _enum_str(live["fidelity"]), _enum_str(live["recovery"])
        assert fidelity in _FIDELITY_RANK and recovery in _RECOVERY_RANK
    finally:
        checker.close(timeout=10.0)

    stop = client.close()
    assert stop["status"] == "exited"
    final = session.wait_report("finished")
    assert final["exit_code"] == runner_module.RUNNER_EXIT_OK
    assert final["emulator_close"]["closed"] is True
    record_evidence(
        "compose_browserless_protocol_a",
        {
            "total_bytes": int(total_now),
            "eviction_triggered": True,
            "cursor_at_snapshot": int(snap["cursor"]),
            "tail_single_consumption": True,
            "checker_serialized_equal": True,
            "fidelity": fidelity,
            "recovery": recovery,
            "capacity_observation": {
                "measure": "单一 monotonic：首次提交 → applied==total（不扣除任何 probe 窗口）",
                "catch_up_seconds": catch_up_seconds,
                "producer_feed_ops": backlog.get("counters", {}).get("feed_ops"),
                "engine_feed_batches": caught_diag.get("counters", {}).get("feed_batches"),
                "engine_feed_batched_ops": caught_diag.get("counters", {}).get("feed_batched_ops"),
                "max_feed_batch_bytes": caught_diag.get("counters", {}).get("max_feed_batch_bytes"),
                "pre_catch_up": {
                    "applied_cursor": backlog.get("applied_cursor"),
                    "queue_ops": backlog.get("queue_ops"),
                    "queue_bytes": backlog.get("queue_bytes"),
                },
                "note": (
                    "同机探针（本机 80×24+scrollback=1000）合并批上限 64KiB（emulator r4 F2）；"
                    "producer feed op 数与实际引擎帧数分列，不外推跨机硬 SLA"
                ),
            },
            "emulator_close": final["emulator_close"],
        },
    )


@requires_sidecar
def test_composition_utf8_osc_boundaries_no_fake_full(composed, tmp_path):
    """UTF-8 跨读块 + OSC（未验证序列）边界：屏幕标记完好；运行器不得把引擎的
    partial/降级状态升级为 full（runner ≤ engine 且 full 仅当 reasons 为空）。"""
    session = composed()
    client = session.client("pan-compose-owner")
    client.heartbeat()
    emitter = tmp_path / "emit_boundary.py"
    emitter.write_text(
        "import sys\n"
        "sys.stdout.buffer.write(('中' * 40000).encode('utf-8'))\n"
        "sys.stdout.buffer.write(b'\\x1b]0;COMP-OSC-TITLE\\x07')\n"
        "sys.stdout.buffer.write(b'COMP-MARK-OSC\\r\\n')\n"
        "sys.stdout.flush()\n",
        encoding="utf-8",
    )
    client.input(f'"{sys.executable}" "{emitter}"\r'.encode("utf-8"))
    text, _ = _read_until(client, b"COMP-MARK-OSC", timeout=40)
    assert b"COMP-MARK-OSC" in text

    total_now = int(client.describe()["total_bytes"])
    runner_snap = _wait_engine_caught_up(session, client, total_now, timeout=180.0)
    engine_snap = session.control("snapshot", wait=10.0)
    diagnostics = session.control("diagnostics")
    fidelity, recovery = _enum_str(runner_snap["fidelity"]), _enum_str(runner_snap["recovery"])
    # 不得升级引擎状态（runner 用自己的降级位，但不允许优于引擎自报）
    assert _FIDELITY_RANK[fidelity] <= _FIDELITY_RANK[_enum_str(engine_snap["fidelity"])]
    assert _RECOVERY_RANK[recovery] <= _RECOVERY_RANK[_enum_str(engine_snap["recovery"])]
    # full 仅当引擎 reasons 为空（诚实性）
    if fidelity == "full" and recovery == "full":
        assert diagnostics["reasons"] == [], f"有 reasons 却报 full：{diagnostics['reasons']}"
    # F5 机器字段（真实 engine↔runner↔IPC）：存在且 bool-or-null（unknown）；不解析 note
    for field in ("cursors_valid", "reset_unconfirmed"):
        assert field in runner_snap, f"F5 字段缺失：{field}"
        assert runner_snap[field] is None or isinstance(runner_snap[field], bool), (
            f"{field} 必须是 bool 或 null：{runner_snap[field]!r}"
        )
    # 原因分层：node 未验证序列原因仅在 note；Python diagnostics.reasons 为空不得升级
    note_text = str(runner_snap.get("note") or "")
    node_reason_in_note = "reasons=" in note_text
    if node_reason_in_note and diagnostics["reasons"] == []:
        assert recovery != "full", "仅有 node note 原因（python reasons 空）时不得报 full"
    # UTF-8 内容完好（标记与中文字符均在屏幕状态里）
    assert "COMP-MARK-OSC" in runner_snap["serialized_screen"]
    assert "中" in runner_snap["serialized_screen"]
    assert client.describe()["consumer"]["failed"] is False
    assert client.close()["status"] == "exited"
    final = session.wait_report("finished")
    assert final["exit_code"] == runner_module.RUNNER_EXIT_OK
    record_evidence(
        "compose_utf8_osc_boundary",
        {
            "runner_fidelity": fidelity,
            "runner_recovery": recovery,
            "engine_fidelity": _enum_str(engine_snap["fidelity"]),
            "engine_recovery": _enum_str(engine_snap["recovery"]),
            "engine_reasons": diagnostics["reasons"][:12],
            "no_upgrade": True,
            "marker_intact": True,
            "utf8_intact": True,
            "f5_fields": {
                "cursors_valid": runner_snap.get("cursors_valid"),
                "reset_unconfirmed": runner_snap.get("reset_unconfirmed"),
            },
            "node_reason_layer": {
                "node_reason_in_note": node_reason_in_note,
                "python_reasons": diagnostics["reasons"][:8],
            },
        },
    )


@requires_sidecar
def test_composition_resize_pty_and_engine_recorded_separately(composed):
    """T3 组合：resize_wait 只确认**引擎**；PTY 与引擎实际尺寸分别记录；
    失败/过期拒绝不得假三方一致。"""
    session = composed()
    client = session.client("pan-compose-owner")
    client.heartbeat()
    result = client.resize(30, 100)
    assert result["status"] == "ok", result
    detail = result["describe"]
    assert "pty_resize" in detail and "emulator_resize" in detail
    assert detail["pty_resize"] == "ok"
    # PTY 实际尺寸（runtime 侧）与请求一致
    describe = client.describe()
    assert int(describe["rows"]) == 30 and int(describe["cols"]) == 100
    # 引擎确认面（唯一可声称引擎一致的口径）
    assert session.control("resize_wait", rows=30, cols=100, wait=15.0, timeout=8.0) is True
    diag = session.control("diagnostics")
    assert [int(v) for v in diag["applied_resize"]] == [30, 100]

    # 过期拒绝（排队中过期不执行）：PTY 与引擎都不得被假改
    assert session.control("test_stall", ms=1500) is True
    assert session.control("resize_wait", rows=20, cols=90, wait=5.0, timeout=0.2) is False
    diag2 = session.control("diagnostics")
    assert [int(v) for v in diag2["applied_resize"]] == [30, 100], "过期 resize 不得被执行"
    describe2 = client.describe()
    assert int(describe2["rows"]) == 30 and int(describe2["cols"]) == 100
    time.sleep(1.6)  # 让 stall 收敛，避免影响后续 close
    assert client.close()["status"] == "exited"
    final = session.wait_report("finished")
    assert final["exit_code"] == runner_module.RUNNER_EXIT_OK
    record_evidence(
        "compose_resize_separation",
        {
            "runner_detail": detail,
            "pty_actual": [30, 100],
            "engine_confirmed": True,
            "engine_applied": [30, 100],
            "expired_refused": True,
            "no_three_way_claim_on_refusal": True,
        },
    )


@requires_sidecar
def test_composition_feed_lag_no_fake_full(composed, tmp_path):
    """T19 组合：引擎 feed 队列打满（停滞 + 小队列）→ feed_lag 粘滞 →
    运行器快照显式降级，不假 full。"""
    session = composed(mode="compose-tight")
    client = session.client("pan-compose-owner")
    client.heartbeat()
    client.input(b"echo READY\r")
    _read_until(client, b"READY", timeout=20)
    assert session.control("test_stall", ms=3000) is True
    blob = tmp_path / "lag.txt"
    blob.write_bytes(b"L" * 40000)
    client.input(f'type "{blob}"\r'.encode("utf-8"))
    lagged = _wait_until(
        lambda: session.control("diagnostics")["feed_lag"], 20.0, interval=0.2
    )
    assert lagged, "未触发 feed_lag（引擎队列未打满）"
    snap = client.snapshot(timeout_ms=1500)
    fidelity, recovery = _enum_str(snap["fidelity"]), _enum_str(snap["recovery"])
    assert recovery != "full", "feed_lag 时不得宣称 full 恢复"
    engine_snap = session.control("snapshot", wait=10.0)
    assert _RECOVERY_RANK[recovery] <= _RECOVERY_RANK[_enum_str(engine_snap["recovery"])]
    assert client.close()["status"] == "exited"
    final = session.wait_report("finished")
    assert final["exit_code"] == runner_module.RUNNER_EXIT_OK
    record_evidence(
        "compose_feed_lag",
        {
            "feed_lag": True,
            "runner_fidelity": fidelity,
            "runner_recovery": recovery,
            "engine_recovery": _enum_str(engine_snap["recovery"]),
            "no_fake_full": True,
        },
    )


@requires_sidecar
def test_composition_reset_unconfirmed_no_fake_full(composed, tmp_path):
    """reset_unconfirmed（stub sidecar 确定性迟到 ack；真实 runner/IPC/emulator
    Python 层）→ cursors_valid=false、快照降级、不假 full；迟到 ack 回填一次。"""
    fake = tmp_path / "fake_sidecar.cjs"
    fake.write_text(FAKE_SIDECAR_SOURCE, encoding="utf-8")
    session = composed(
        mode="compose-fake-slowreset",
        env_extra={"PAN_COMP_FAKE_SIDECAR": str(fake)},
    )
    client = session.client("pan-compose-owner")
    client.heartbeat()
    client.input(b"echo BEFORE-RESET\r")
    _read_until(client, b"BEFORE-RESET", timeout=20)

    reset_snap = session.control("reset_baseline", wait=15.0)
    assert "unconfirmed" in str(reset_snap.get("note", "")).lower(), reset_snap
    diag = session.control("diagnostics")
    assert diag["reset_unconfirmed"] is True
    assert diag["cursors_valid"] is False
    # 未确认窗口内的 runner 快照：control op 排在在途 reset 之后，长超时会等迟到 ack 回填；
    # 用短超时（200ms < stub 的 1.0s ack 延迟）在窗口内取保守字段。
    runner_snap = client.snapshot(timeout_ms=200)
    fidelity, recovery = _enum_str(runner_snap["fidelity"]), _enum_str(runner_snap["recovery"])
    assert recovery != "full", "reset 未确认期间不得声称 full"
    # F5 真路径：reset 未确认 → reset_unconfirmed=true；引擎 cursors_valid 属性在
    # 未确认期本身为 False → 透出 False（最强 fail-closed；unknown 亦可接受但不出现）
    assert runner_snap.get("reset_unconfirmed") is True, runner_snap.get("reset_unconfirmed")
    assert runner_snap.get("cursors_valid") is False, runner_snap.get("cursors_valid")
    # 迟到 ack：账本一次回填、禁止解除；reset 后仍不假 full（reset 不恢复历史）
    _wait_until(
        lambda: session.control("diagnostics")["reset_unconfirmed"] is False, 8.0, interval=0.2
    )
    diag2 = session.control("diagnostics")
    assert diag2["reset_unconfirmed"] is False
    assert int(diag2["reset_count"]) == 1, "迟到 ack 只允许回填一次"
    after = client.snapshot(timeout_ms=4000)
    assert _enum_str(after["recovery"]) != "full", "reset 之后不得假 full"
    # 迟到 ack 回填后：reset_unconfirmed=false；baseline==applied（stub 回填）→ cursors_valid=true
    assert after.get("reset_unconfirmed") is False, after.get("reset_unconfirmed")
    assert after.get("cursors_valid") is True, after.get("cursors_valid")
    assert client.close()["status"] == "exited"
    final = session.wait_report("finished")
    assert final["exit_code"] == runner_module.RUNNER_EXIT_OK
    record_evidence(
        "compose_reset_unconfirmed",
        {
            "injected_sidecar": True,
            "reset_snapshot_note": reset_snap.get("note"),
            "reset_unconfirmed": True,
            "cursors_valid_false": True,
            "runner_recovery_during": recovery,
            "reset_count_after_ack": int(diag2["reset_count"]),
            "recovery_after_reset": _enum_str(after["recovery"]),
            "no_fake_full": True,
        },
    )


def test_composition_startup_engine_failure_keeps_owner(tmp_path):
    """启动引擎失败三态（真实 node 进程）：
    ① spawn 前校验失败 → EmulatorStartupError，无资源（owner=None 合理）；
    ② spawn 后 **ready 前 EOF** → **必须** EmulatorStartupError 带可重试 owner
       （emulator r4 F1 修复；组合发现闭环：retry 收敛、无残留）；
    ③ spawn 后 `fatal` 帧 → EmulatorStartupError 带 owner，可重试收敛、无残留。"""
    # ① spawn 前：脚本不存在
    missing = tmp_path / "missing_sidecar.mjs"
    with pytest.raises(EmulatorStartupError) as excinfo:
        HeadlessEmulator(sidecar_path=str(missing), startup_timeout=10.0)
    pre = excinfo.value
    assert pre.owner is None, "spawn 前失败不得伪造 owner"

    # ② spawn 后 ready 前 EOF（注入 stub 立即退出）→ r4 F1 修复：与 fatal/timeout 同路径
    #    fail-closed；必须带可重试 owner、retry 收敛、无残留（不再构造 dead engine）
    exit_only = tmp_path / "exit_only_sidecar.cjs"
    exit_only.write_text("process.exit(3);\n", encoding="utf-8")
    with pytest.raises(EmulatorStartupError) as excinfo_eof:
        HeadlessEmulator(sidecar_path=str(exit_only), startup_timeout=10.0)
    eof_error = excinfo_eof.value
    assert eof_error.owner is not None, "ready 前 EOF 必须带可重试 owner（r4 F1 修复）"
    eof_residual = dict(eof_error.residual or {})
    eof_report = eof_error.owner.retry_cleanup(timeout=10.0)
    assert eof_report.get("closed") is True, eof_report
    eof_dead = True
    eof_pid = eof_residual.get("pid")
    if eof_pid:
        eof_filetime = eof_residual.get("filetime") or eof_residual.get("created_at_filetime")
        eof_dead = wait_dead(int(eof_pid), 8.0, int(eof_filetime) if eof_filetime else None)
    assert eof_dead, "ready 前 EOF 失败不得留下 node 残留"

    # ③ spawn 后 fatal 帧（真实 node 已入 Job）→ 必须带 owner 并可重试收敛
    fatal = tmp_path / "fatal_sidecar.cjs"
    fatal.write_text(
        "function send(h){const b=Buffer.from(JSON.stringify(h),'utf8');"
        "const o=Buffer.alloc(8+b.length);o.writeUInt32LE(b.length,0);o.writeUInt32LE(b.length,4);"
        "b.copy(o,8);process.stdout.write(o);}\n"
        "send({type:'fatal',code:'injected-dependency-broken',detail:'test'});\n"
        "setTimeout(()=>process.exit(4),50);\n",
        encoding="utf-8",
    )
    started = time.monotonic()
    with pytest.raises(EmulatorStartupError) as excinfo2:
        HeadlessEmulator(sidecar_path=str(fatal), startup_timeout=10.0)
    elapsed = round(time.monotonic() - started, 3)
    error = excinfo2.value
    assert error.owner is not None, "spawn 后 fatal 失败必须携带可重试 owner"
    residual = dict(error.residual or {})
    report = error.owner.retry_cleanup(timeout=10.0)
    assert report.get("closed") is True, report
    pid = residual.get("pid")
    dead = True
    if pid:
        filetime = residual.get("filetime") or residual.get("created_at_filetime")
        dead = wait_dead(int(pid), 8.0, int(filetime) if filetime else None)
    assert dead, "构造失败不得留下 node 残留"
    record_evidence(
        "compose_startup_failure_owner",
        {
            "pre_spawn_error": type(pre).__name__,
            "pre_spawn_owner_none": True,
            "post_spawn_eof": {
                "error": type(eof_error).__name__,
                "has_owner": True,
                "retry_cleanup": eof_report,
                "no_residue": bool(eof_dead),
                "fix": "emulator r4 F1：ready 前 EOF 与 fatal/timeout 同路径 fail-closed",
            },
            "post_spawn_fatal": {
                "error": type(error).__name__,
                "seconds": elapsed,
                "has_owner": True,
                "retry_cleanup": report,
                "residual": residual,
                "no_residue": bool(dead),
            },
        },
    )


@requires_sidecar
def test_composition_pipe_disconnect_and_slow_snapshot_do_not_kill_runtime(composed):
    """管道断连只释放连接（心跳在另一连接上保持）；引擎停滞时慢 snapshot 由独立
    连接心跳并行承担，runtime 不被误杀。"""
    session = composed(mode="compose-tight")
    owner = session.client("pan-compose-owner")
    other = session.client("pan-compose-second")
    assert owner.heartbeat()["status"] == "ok"
    session.note_shell(session.control("describe"))
    pid_before = int(session.control("describe")["pid"])

    observer = session.client("pan-compose-observer")
    assert observer.register_observer()["status"] == "ok"
    observer.release_connection()  # 断连 = 只释放连接
    time.sleep(0.6)
    assert int(session.control("describe")["pid"]) == pid_before, "断连不得杀 runtime"
    assert owner.heartbeat()["status"] == "ok"

    # 慢 snapshot（引擎被 stall）：在 owner 连接上发起；other 连接心跳不受影响。
    assert session.control("test_stall", ms=1500) is True
    snapshot_result: dict[str, Any] = {}

    def _slow_snapshot() -> None:
        try:
            snapshot_result.update(owner.snapshot(timeout_ms=500))
        except Exception as exc:  # noqa: BLE001
            snapshot_result["error"] = type(exc).__name__

    thread = threading.Thread(target=_slow_snapshot, daemon=True)
    thread.start()
    latencies: list[float] = []
    for _ in range(5):
        started = time.monotonic()
        heartbeat = other.heartbeat(timeout_ms=3000)
        latencies.append(round(time.monotonic() - started, 3))
        assert heartbeat["status"] == "ok"
        time.sleep(0.12)
    thread.join(timeout=10.0)
    assert not thread.is_alive()
    assert snapshot_result.get("recovery") != "full", snapshot_result
    assert max(latencies) < 0.8, f"独立连接心跳被慢 snapshot 拖慢：{latencies}"
    assert int(session.control("describe")["pid"]) == pid_before
    time.sleep(1.4)
    assert other.close()["status"] == "exited"
    final = session.wait_report("finished")
    assert final["exit_code"] == runner_module.RUNNER_EXIT_OK
    record_evidence(
        "compose_disconnect_slow_snapshot",
        {
            "disconnect_kept_pid": True,
            "snapshot_recovery": snapshot_result.get("recovery"),
            "snapshot_status": snapshot_result.get("status"),
            "heartbeat_latencies": latencies,
            "runtime_alive_after": True,
        },
    )


@requires_sidecar
def test_composition_write_and_close_workers_do_not_block_loop(composed):
    """write/close 由可追踪 worker 承担：大输入期间独立连接心跳稳定；close 返回
    exited 且 emulator 由组合方显式关闭（顺序可核）。"""
    session = composed()
    owner = session.client("pan-compose-owner")
    other = session.client("pan-compose-second")
    assert owner.heartbeat()["status"] == "ok"
    session.note_shell(session.control("describe"))

    statuses: list[str] = []
    for _ in range(3):
        result = owner.input(b"X" * 96_000, timeout_ms=8000)
        statuses.append(str(result["status"]))
    latencies: list[float] = []
    for _ in range(5):
        started = time.monotonic()
        assert other.heartbeat(timeout_ms=3000)["status"] == "ok"
        latencies.append(round(time.monotonic() - started, 3))
        time.sleep(0.1)
    assert max(latencies) < 1.0, f"心跳被写 worker 拖慢：{latencies}"
    assert set(statuses) <= {"done", "accepted-in-flight", "busy"}, statuses

    stopped = owner.close()
    assert stopped["status"] == "exited", stopped
    final = session.wait_report("finished")
    assert final["exit_code"] == runner_module.RUNNER_EXIT_OK
    assert final["emulator_close"]["closed"] is True
    record_evidence(
        "compose_write_close_workers",
        {
            "input_statuses": statuses,
            "heartbeat_latencies": latencies,
            "close_status": stopped["status"],
            "emulator_close": final["emulator_close"],
        },
    )


@requires_sidecar
def test_composition_clean_stop_leaves_no_sidecar_or_tree_residue(composed):
    """正常停止：runner exit 0 → emulator.close 收敛 → sidecar 与 PTY 整树均无残留。"""
    session = composed()
    client = session.client("pan-compose-owner")
    client.heartbeat()
    session.note_shell(session.control("describe"))
    session.note_sidecar(session.control("diagnostics"))
    assert session.sidecar_pid and session.shell_pid
    assert client.close()["status"] == "exited"
    final = session.wait_report("finished")
    assert final["exit_code"] == runner_module.RUNNER_EXIT_OK
    close_report = final["emulator_close"]
    assert close_report["closed"] is True, close_report
    assert wait_dead(session.shell_pid, 8.0, session.shell_filetime)
    assert wait_dead(session.sidecar_pid, 10.0, session.sidecar_filetime)
    record_evidence(
        "compose_clean_stop_no_residue",
        {
            "exit_code": final["exit_code"],
            "emulator_close": close_report,
            "shell_dead": True,
            "sidecar_dead": True,
            "sidecar_pid": session.sidecar_pid,
        },
    )


@requires_sidecar
def test_composition_runner_hard_kill_leaves_no_sidecar_or_tree(composed):
    """runner 硬死：emulator 自持 Job guard 随进程消亡 → sidecar 与 PTY 整树内核清理。"""
    session = composed()
    client = session.client("pan-compose-owner")
    client.heartbeat()
    session.note_shell(session.control("describe"))
    session.note_sidecar(session.control("diagnostics"))
    shell_pid, shell_filetime = session.shell_pid, session.shell_filetime
    sidecar_pid, sidecar_filetime = session.sidecar_pid, session.sidecar_filetime
    trace = win_pipe.terminate_verified_process(session.runner_pid, session.runner_filetime)
    assert trace.get("terminated") is True, trace
    assert wait_dead(shell_pid, 10.0, shell_filetime), "PTY 整树必须随 runner 消亡"
    assert wait_dead(sidecar_pid, 12.0, sidecar_filetime), "sidecar 必须随 runner 消亡"
    assert session.proc.wait(timeout=20) != 0
    client.release_connection()
    record_evidence(
        "compose_hard_kill_no_residue",
        {
            "runner_terminate": trace,
            "shell_dead": True,
            "sidecar_dead": True,
            "kernel_guard_note": "本场景实测（emulator 自持 guard 随进程消亡）；不泛化",
        },
    )


@requires_sidecar
def test_composition_detach_refused_under_constraint_zero_change(composed):
    """detach 只验证受限环境下的显式拒绝（零状态变化）；不做环境逃脱实验。"""
    if not runner_module.detect_ambient_job():
        pytest.skip("no ambient job in this environment: refusal path not applicable")
    session = composed()
    client = session.client("pan-compose-owner")
    client.heartbeat()
    before = session.control("describe")
    assert before["durability"]["capable"] is False
    result = client.detach()
    assert result["status"] == "detach-refused" and result["ok"] is False
    after = session.control("describe")
    assert after["runner_state"] == "running" and after["detached"] is False
    assert int(after["pid"]) == int(before["pid"])
    session.note_shell(after)
    assert not process_dead(session.shell_pid, session.shell_filetime)
    assert client.close()["status"] == "exited"
    final = session.wait_report("finished")
    assert final["exit_code"] == runner_module.RUNNER_EXIT_OK
    record_evidence(
        "compose_detach_refused",
        {
            "durability": before["durability"],
            "detach_status": result["status"],
            "zero_state_change": True,
        },
    )


# ══════════════════════════════════════════════════════════════════════════
# composition r2 新增门控（修正后真实验收）：驱逐 gap/fresh-view、引擎宿主收尾预算
# ══════════════════════════════════════════════════════════════════════════


@requires_sidecar
def test_composition_r2_eviction_gap_fresh_view_and_catch_up(composed, tmp_path):
    """协议 A 驱逐边界：applied < first_retained 时 `read(applied)` **显式 gap** →
    fresh-view 语义（**不自动 reset、不杀 PTY**）；追平后重取快照无 gap。
    宿主 = 测试 launcher（非生产 launcher）。"""
    session = composed()
    client = session.client("pan-compose-owner")
    client.heartbeat()
    describe0 = session.control("describe")
    session.note_shell(describe0)

    big = b"".join((b"GAP-PAD-%05d " % index) + b"x" * 40 + b"\r\n" for index in range(3200))
    first = tmp_path / "gap1.txt"
    second = tmp_path / "gap2.txt"
    first.write_bytes(big)
    second.write_bytes(big)

    # 停滞引擎（3s）期间生产 >2×256KiB：applied 落在驱逐窗口之前（真实边界）。
    assert session.control("test_stall", ms=3000) is True
    client.input(f'type "{first}"\r'.encode("utf-8"))
    client.input(f'type "{second}"\r'.encode("utf-8"))

    window: dict[str, int] = {}

    def _capture_window():
        total = int(client.describe()["total_bytes"])
        if total <= 262144 + 65536:
            return None
        applied = int(session.control("diagnostics")["applied_cursor"])
        retained = int(client.describe()["first_retained_seq"])
        if applied < retained:
            window.update({"total": total, "applied": applied, "first_retained": retained})
            return dict(window)
        return None

    assert _wait_until(_capture_window, 30.0, interval=0.05), (
        f"未捕获 applied < first_retained 的驱逐窗口：{window}"
    )
    applied = int(window["applied"])
    page = client.read(applied)
    assert page["gap"] is not None, "applied 被驱逐时必须返回显式 gap（不得假续流）"
    assert int(page["gap"][0]) == applied
    assert page["data"], "gap 页应携带窗口内可用数据（从 first_retained 起）"
    # fresh-view 语义：不自动 reset、不杀 PTY
    mid_diag = session.control("diagnostics")
    assert int(mid_diag["reset_count"]) == 0, "fresh-view 不得自动调用 reset_baseline"
    assert int(session.control("describe")["pid"]) == int(describe0["pid"])
    assert not process_dead(session.shell_pid, session.shell_filetime)

    # 追平后重取：cursor == total、read(cursor) 无 gap。
    total_stable = _wait_total_stable(client, timeout=60.0)
    snap = _wait_engine_caught_up(session, client, total_stable, timeout=180.0)
    assert int(snap["cursor"]) == int(total_stable)
    page2 = client.read(int(snap["cursor"]))
    assert page2["gap"] is None and page2["data"] == b""
    assert int(session.control("diagnostics")["reset_count"]) == 0

    assert client.close()["status"] == "exited"
    final = session.wait_report("finished")
    assert final["exit_code"] == runner_module.RUNNER_EXIT_OK
    record_evidence(
        "compose_r2_eviction_gap_fresh_view",
        {
            "window": {k: int(v) for k, v in window.items()},
            "gap_returned": True,
            "gap_start_equals_applied": True,
            "no_auto_reset": True,
            "pty_alive_during_downgrade": True,
            "catch_up_refetch_no_gap": True,
            "note": "fresh-view 是显示层策略；不自动 reset_baseline、不结束 PTY",
        },
    )


def test_composition_r2_engine_close_budget_owner_retryable():
    """引擎宿主（测试宿主语义）收尾预算与 owner 重试：`close(timeout=0)` 不收敛 →
    `closed=false` 且有界报告 → 有界重试收敛。不称生产 launcher、不泛化 Job 兜底布局。"""
    emulator = HeadlessEmulator(cols=80, rows=24)
    try:
        first = emulator.close(timeout=0.0)
        second = emulator.close(timeout=15.0)
    finally:
        final_report = emulator.close(timeout=15.0)
    assert first.closed is False, first.as_dict()
    assert first.process_exited is False and first.stderr_joined is False
    assert second.closed is True, second.as_dict()
    assert final_report.closed is True
    record_evidence(
        "compose_r2_engine_close_budget",
        {
            "close_zero_closed": bool(first.closed),
            "close_zero_detail": first.detail,
            "retry_closed": bool(second.closed),
            "idempotent_final": bool(final_report.closed),
            "note": "测试宿主显式收尾；生产 launcher 未批准/未实现；Job 兜底布局不泛化",
        },
    )
