"""只读探针：``probe_process`` 三态在“引用保留 / 完全回收”两类死进程上的真实行为。

为什么需要这个探针：测试侧死亡判定若只认 ``status == DEAD``，会把“打不开”
（UNKNOWN）误判为“仍存活”。本机实测两类语义：

1. **引用保留**：进程已终止但仍有内核引用（句柄 / Job 记账）→
   ``WaitForSingleObject`` signaled → ``DEAD``；
2. **完全回收**：进程对象已释放（例如 runner 硬死后 guard Job 销毁其 ConPTY
   子进程）→ ``OpenProcess`` 失败 → ``UNKNOWN``（**不是** dead）——
   此时必须叠加 PID 存在性 / FILETIME 才能断言“已退出”。

不杀任何非自建进程；自建 runner/子进程在 finally 中以同 handle 核验后清理。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_here = Path(__file__).resolve()
REPO_ROOT = _here
while not (REPO_ROOT / "packages").is_dir():
    REPO_ROOT = REPO_ROOT.parent
sys.path.insert(0, str(REPO_ROOT))

from packages.core.terminal import runner_client, secret_store, win_pipe  # noqa: E402


def _probe(pid: int) -> dict:
    probe = win_pipe.probe_process(int(pid))
    return {"status": probe.status.value, "detail": probe.detail}


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
        return int(ctypes.get_last_error()) != 87  # 87=ERROR_INVALID_PARAMETER
    k32.CloseHandle(ctypes.c_void_p(handle))
    return True


def main() -> int:
    payload: dict = {}

    # ① 引用保留（Popen 句柄未关闭）
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    pid = proc.pid
    proc.wait(timeout=30)
    time.sleep(0.2)
    payload["with_popen_handle"] = _probe(pid)
    proc.__exit__(None, None, None)

    # ② Popen 句柄关闭（本机仍在宿主 Job 记账中）
    time.sleep(0.5)
    payload["after_popen_handle_release"] = _probe(pid)
    payload["pid_exists_after_release"] = _pid_exists(pid)

    # ③ 完全回收：runner 硬死 → guard Job 销毁 → ConPTY shell 的进程对象被释放
    root = Path(tempfile.mkdtemp(prefix="runner-probe-"))
    tid = "term_" + os.urandom(6).hex()
    store = secret_store.SecretStore(root)
    store.ensure_secrets_dir()
    secret_file = store.secret_path(tid)
    env = dict(os.environ)
    env["PAN_TERMINAL_RUNNER_REPO"] = str(REPO_ROOT)
    runner_proc = subprocess.Popen(
        runner_client.build_runner_argv(tid, secret_file),
        cwd=str(REPO_ROOT),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    runner_pid = runner_identity = shell_pid = None
    payload["guard_scenario"] = {}
    try:
        record = store.wait_for_bootstrap_identity(tid, timeout=30)
        runner_pid, runner_identity = int(record.pid), int(record.filetime)
        runner_client.complete_bootstrap(store, tid)
        client = runner_client.RunnerClient(
            tid, data_root=root, connect_timeout=10, request_timeout_ms=8000
        )
        client.attach()
        client.heartbeat()
        shell_pid = int(client.describe()["pid"])
        client.release_connection()
        payload["guard_scenario"] = {
            "runner_pid": runner_pid,
            "shell_pid": shell_pid,
            "shell_before_kill": _probe(shell_pid),
        }
        trace = win_pipe.terminate_verified_process(runner_pid, runner_identity)
        payload["guard_scenario"]["runner_kill_terminated"] = bool(trace.get("terminated"))
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline and _pid_exists(shell_pid):
            time.sleep(0.2)
        payload["guard_scenario"]["shell_after_kill"] = _probe(shell_pid)
        payload["guard_scenario"]["shell_pid_exists_after_kill"] = _pid_exists(shell_pid)
        payload["guard_scenario"]["runner_exit_code"] = runner_proc.wait(timeout=30)
    finally:
        if runner_pid is not None and _pid_exists(runner_pid):
            win_pipe.terminate_verified_process(runner_pid, runner_identity)
        if shell_pid is not None and _pid_exists(shell_pid):
            probe = win_pipe.probe_process(shell_pid)
            win_pipe.terminate_verified_process(
                shell_pid,
                probe.identity.created_at_filetime if probe.identity else None,
            )
        try:
            runner_proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            pass

    payload["conclusion"] = (
        "死亡判定不得只看 probe 三态：引用保留=DEAD；完全回收=UNKNOWN；"
        "测试侧以 PID 存在性 + FILETIME 兜底"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
