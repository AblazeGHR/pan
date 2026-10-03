"""组合 r2 审查门控公共设施（round2；独立实现）。

- 进程内 emulator 探针（F1/F2）；
- 真机会话：复用**旧审查树只读**的独立 launcher（本审查自产，非被审 tests 内嵌），
  以本树 repo（3a）装配：真实 ConPTY + 真实 IPC + 真实 HeadlessEmulator；
- 证据写 round2/gates/；硬看门狗。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

_here = Path(__file__).resolve()
REPO_ROOT = _here
for _p in _here.parents:
    if (_p / "packages" / "core" / "terminal").is_dir():
        REPO_ROOT = _p
        break
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

ROUND2_GATES = _here.parent
HARD_TIMEOUT = float(os.environ.get("CR2_GATE_TIMEOUT", "300"))

# 旧审查树（冻结、只读）中的独立 launcher（本审查此前自产，非被审代码）
OLD_REVIEW_TREE = Path("D:/project/pan-worktrees/terminal-composition-review-20261003")
LAUNCHER = OLD_REVIEW_TREE / "audit/terminal/implementation/composition-review/gates/probe_launcher.py"

from packages.core.terminal import runner_client, secret_store, win_pipe  # noqa: E402
from packages.core.terminal.emulator import EmulatorStartupError, HeadlessEmulator  # noqa: E402


def start_hard_watchdog(label: str, seconds: float | None = None) -> threading.Timer:
    timer = threading.Timer(seconds or HARD_TIMEOUT, _fire, args=(label, seconds or HARD_TIMEOUT))
    timer.daemon = True
    timer.start()
    return timer


def _fire(label: str, seconds: float) -> None:
    print(f"[gate:{label}] HARD TIMEOUT after {seconds}s -> os._exit(97)", flush=True)
    os._exit(97)


def write_evidence(name: str, payload: dict) -> Path:
    payload = dict(payload)
    payload.setdefault("generated_at", time.strftime("%Y-%m-%d %H:%M:%S"))
    payload.setdefault("injection_only", True)
    payload.setdefault(
        "note",
        "组合 r2 审查独立门控（round2；对照 docs/design/"
        "PAN_TERMINAL_COMPOSITION_SECURITY_REVIEW_20261003_ROUND2.md）",
    )
    path = ROUND2_GATES / f"{name}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"[gate] evidence -> {path}", flush=True)
    return path


def wait_until(predicate, timeout: float, interval: float = 0.05):
    deadline = time.monotonic() + float(timeout)
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return predicate()


def process_dead(pid: int, filetime: int | None = None) -> bool:
    """同 handle probe 优先；UNKNOWN 用 OpenProcess 兜底（与组合套件判定口径一致）。"""
    import ctypes
    from ctypes import wintypes

    from packages.core.terminal.contracts import ProcessStatus

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
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = ctypes.c_void_p
    k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k32.CloseHandle.argtypes = (ctypes.c_void_p,)
    ctypes.set_last_error(0)
    handle = k32.OpenProcess(0x00100000, False, int(pid))
    if not handle:
        return int(ctypes.get_last_error()) == 87  # ERROR_INVALID_PARAMETER → 不存在
    k32.CloseHandle(ctypes.c_void_p(handle))
    return False


def wait_dead(pid: int, timeout: float, filetime: int | None = None) -> bool:
    return bool(wait_until(lambda: process_dead(int(pid), filetime), timeout))


class Control:
    def __init__(self, directory: Path) -> None:
        self.request = directory / "control-request.json"
        self.result = directory / "control-result.json"
        self._seq = 0
        self._lock = threading.Lock()

    def call(self, cmd: str, *, wait: float = 30.0, **args):
        with self._lock:
            self._seq += 1
            seq = self._seq
            if self.result.exists():
                self.result.unlink()
            tmp = self.request.with_name("control-request.tmp")
            tmp.write_text(json.dumps({"seq": seq, "cmd": cmd, "args": args}), encoding="utf-8")
            os.replace(tmp, self.request)
            deadline = time.monotonic() + wait
            while time.monotonic() < deadline:
                if self.result.is_file():
                    try:
                        payload = json.loads(self.result.read_text(encoding="utf-8"))
                    except Exception:  # noqa: BLE001
                        time.sleep(0.02)
                        continue
                    if payload.get("seq") == seq:
                        if not payload.get("ok"):
                            raise RuntimeError(f"control {cmd}: {payload.get('error')}")
                        return payload.get("result")
                time.sleep(0.02)
        raise TimeoutError(f"control {cmd} timeout")


class Session:
    """真机会话（自有 launcher；本树 repo 装配；同 handle 身份核验清理）。"""

    def __init__(self, tmp: Path, mode_args: list[str] | None = None) -> None:
        self.tid = f"term_{os.urandom(6).hex()}"
        self.data_root = tmp / f"terminals_{self.tid}"
        self.store = secret_store.SecretStore(self.data_root)
        self.store.ensure_secrets_dir()
        self.secret_file = self.store.secret_path(self.tid)
        self.report_path = tmp / f"report_{self.tid}.json"
        self.control_dir = tmp / f"control_{self.tid}"
        self.control_dir.mkdir()
        self.control = Control(self.control_dir)
        env = dict(os.environ)
        env["COMP_REVIEW_REPO"] = str(REPO_ROOT)
        env["COMP_HARD_TIMEOUT"] = "220"
        argv = [
            sys.executable,
            str(LAUNCHER),
            self.tid,
            str(self.secret_file),
            str(self.report_path),
            str(self.control_dir),
        ] + (mode_args or [])
        self.proc = subprocess.Popen(
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
        record = self.store.wait_for_bootstrap_identity(self.tid, timeout=60.0)
        self.runner_pid = int(record.pid)
        self.runner_filetime = int(record.filetime)
        self.payload = runner_client.complete_bootstrap(self.store, self.tid)
        self.heartbeat_stop = threading.Event()
        self.heartbeat_latencies: list[float] = []
        self.heartbeat_client = self.client("cr2-keeper")
        assert self.heartbeat_client.heartbeat()["status"] == "ok"
        self._hb_thread = threading.Thread(target=self._hb_loop, daemon=True)
        self._hb_thread.start()
        self.shell_pid: int | None = None
        self.shell_filetime: int | None = None
        self.sidecar_pid: int | None = None
        self.sidecar_filetime: int | None = None

    def _hb_loop(self) -> None:
        while not self.heartbeat_stop.is_set():
            t0 = time.monotonic()
            try:
                if self.heartbeat_client.heartbeat(timeout_ms=3000).get("status") != "ok":
                    return
            except Exception:  # noqa: BLE001
                return
            self.heartbeat_latencies.append(round(time.monotonic() - t0, 3))
            self.heartbeat_stop.wait(0.4)

    def client(self, client_id: str):
        client = runner_client.RunnerClient(
            self.tid, data_root=self.data_root, connect_timeout=15.0,
            request_timeout_ms=15_000, close_timeout_ms=25_000, client_id=client_id,
        )
        client.attach()
        return client

    def report(self) -> dict:
        if not self.report_path.is_file():
            return {}
        try:
            return json.loads(self.report_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}

    def wait_report(self, phase: str = "finished", timeout: float = 90.0) -> dict:
        data = wait_until(lambda: self.report() if self.report().get("phase") == phase else None, timeout)
        return data or {}

    def note_shell(self) -> None:
        describe = self.control.call("describe")
        self.shell_pid = int(describe["pid"])
        self.shell_filetime = int(describe["process_created_at_filetime"])

    def note_sidecar(self) -> None:
        diag = self.control.call("diagnostics")
        if diag.get("sidecar_pid"):
            self.sidecar_pid = int(diag["sidecar_pid"])
            self.sidecar_filetime = int(diag["sidecar_filetime"])

    def cleanup(self) -> dict:
        trace: dict = {}
        self.heartbeat_stop.set()
        self._hb_thread.join(timeout=3.0)
        try:
            self.heartbeat_client.release_connection()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.proc.wait(timeout=4.0)
        except subprocess.TimeoutExpired:
            pass
        for name, pid, ft in (
            ("runner", self.runner_pid, self.runner_filetime),
            ("shell", self.shell_pid, self.shell_filetime),
            ("sidecar", self.sidecar_pid, self.sidecar_filetime),
        ):
            if pid and not process_dead(pid, ft):
                try:
                    trace[name] = win_pipe.terminate_verified_process(pid, ft)
                except Exception as exc:  # noqa: BLE001
                    trace[name] = {"error": type(exc).__name__}
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            trace["launcher_wait"] = "timeout"
        for stream in (self.proc.stdout, self.proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception:  # noqa: BLE001
                pass
        return trace
