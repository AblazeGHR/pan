"""r2 先失败后通过对照：旧实现（1daff2a6）的 close/构造失败无界性复现。

背景：r2 的 pytest 先失败日志使用的是修正前的 fake holder（非 detached，
holder 随 root 退出消失），不能作为"管道被存活后代持有 -> close 无界"的
有效现场。本脚本用**修正后的 detached holder 现场**对旧实现做确定性复现，
并对当前（修复后）实现验证有界收敛。

用法（从仓库根）：

    E:/software/miniforge/python.exe audit/terminal/implementation/emulator/evidence/r2/repro_p11_unbounded_close.py

- 旧实现通过 ``git show 1daff2a6:packages/core/terminal/emulator.py`` 动态加载
  （不修改工作树；旧模块的相对导入映射到当前 contracts/guard/identity）。
- 现场：fake sidecar（测试同款，detached holder 继承 stdout/stderr）：
  P11  close      = ready -> root 退出、holder（Job 成员）存活持管道；
  P1b  startup    = 不回 ready、root 存活 + holder 持管道。
- 断言（脚本退出码）：旧实现两个场景都必须"超过自身 timeout 仍阻塞"
  （由自建 holder 的身份核验终止解除；不作通过依据），当前实现必须有界。
- 所有终止仅针对自建进程：``identity.kill_verified``（同 handle raw FILETIME
  + Wait）；不扫描陌生 PID。
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(REPO_ROOT))

from packages.core.terminal import identity as identity_module  # noqa: E402
from packages.core.terminal.contracts import ProcessStatus  # noqa: E402

OLD_COMMIT = "1daff2a6"
RESULTS: dict = {"old": {}, "new": {}}


def _load_old_emulator():
    src = subprocess.run(
        ["git", "show", f"{OLD_COMMIT}:packages/core/terminal/emulator.py"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    tmp_dir = Path(tempfile.mkdtemp(prefix="pan-prefix-old-"))
    old_path = tmp_dir / "emulator.py"
    old_path.write_text(src, encoding="utf-8")
    from packages.core.terminal import contracts as contracts_mod
    from packages.core.terminal import guard as guard_mod
    from packages.core.terminal import identity as identity_mod

    pkg = types.ModuleType("pan_old_emulator_pkg")
    pkg.__path__ = [str(tmp_dir)]
    sys.modules["pan_old_emulator_pkg"] = pkg
    sys.modules["pan_old_emulator_pkg.contracts"] = contracts_mod
    sys.modules["pan_old_emulator_pkg.guard"] = guard_mod
    sys.modules["pan_old_emulator_pkg.identity"] = identity_mod
    spec = importlib.util.spec_from_file_location("pan_old_emulator_pkg.emulator", old_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod, tmp_dir


def _extract_fake_source() -> str:
    text = (REPO_ROOT / "tests" / "test_terminal_emulator.py").read_text(encoding="utf-8")
    match = re.search(r'FAKE_SIDECAR_SOURCE = r"""(.*?)"""', text, re.S)
    assert match, "FAKE_SIDECAR_SOURCE 未在测试文件中找到"
    return match.group(1)


def _wait_until(predicate, timeout: float, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + float(timeout)
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


def _same_handle_dead(proc) -> bool:
    handle = int(getattr(proc, "_handle", 0) or 0)
    if not handle:
        return False
    return identity_module.wait_state(handle) is ProcessStatus.DEAD


def _probe_retained(pid: int):
    handle = identity_module.open_process_for_probe(int(pid))
    if not handle:
        raise RuntimeError(f"OpenProcess(probe) failed for pid={pid}")
    probe = identity_module.probe_handle(int(handle), pid=int(pid))
    return int(handle), probe


def _cleanup_holder(holder_pid: int | None, holder_ft: int | None) -> None:
    if holder_pid and holder_ft:
        identity_module.kill_verified(holder_pid, int(holder_ft), wait_timeout=3.0)


def _cleanup_proc(proc) -> None:
    handle = int(getattr(proc, "_handle", 0) or 0)
    ft = identity_module.read_creation_filetime(handle) if handle else None
    if ft is not None and proc.poll() is None:
        identity_module.kill_verified(int(proc.pid), int(ft), wait_timeout=3.0)


def run_p11(emulator_cls, fake: Path, tmp: Path) -> dict:
    """ready-exit-child：root 退出、holder 存活持 stdout → close(timeout=3)。"""
    pid_file = tmp / "holder.pid"
    os.environ["FAKE_SIDECAR_MODE"] = "ready-exit-child"
    os.environ["FAKE_CHILD_PID_FILE"] = str(pid_file)
    try:
        emu = emulator_cls(sidecar_path=str(fake), startup_timeout=20.0)
    finally:
        os.environ.pop("FAKE_SIDECAR_MODE", None)
        os.environ.pop("FAKE_CHILD_PID_FILE", None)
    proc = emu._proc
    out: dict = {}
    try:
        out["root_dead"] = _wait_until(lambda: _same_handle_dead(proc), 8.0)
        out["holder_pid_file"] = _wait_until(pid_file.exists, 8.0)
        holder_pid = int(pid_file.read_text(encoding="utf-8").strip()) if pid_file.exists() else None
        holder_ft = None
        if holder_pid:
            handle, probe = _probe_retained(holder_pid)
            holder_ft = int(probe.identity.created_at_filetime) if probe.identity else None
            out["holder_alive"] = probe.status is ProcessStatus.ALIVE
            out["holder_in_job"] = holder_pid in emu._guard.member_pids()
            identity_module.close_handle_checked(handle)
        reports: list = []
        thread = threading.Thread(
            target=lambda: reports.append(emu.close(timeout=3.0)), daemon=True
        )
        t0 = time.monotonic()
        thread.start()
        thread.join(timeout=12.0)
        blocked = thread.is_alive()
        out["blocked_beyond_budget"] = blocked
        out["blocked_for_sec"] = round(time.monotonic() - t0, 3)
        if blocked:
            _cleanup_holder(holder_pid, holder_ft)
            thread.join(timeout=15.0)
        out["total_sec"] = round(time.monotonic() - t0, 3)
        if reports:
            report = reports[0]
            out["closed"] = report.closed
            out["job_verified"] = getattr(report, "job_verified", None)
            out["process_exited"] = report.process_exited
        out["_holder_pid"] = holder_pid
        out["_holder_ft"] = holder_ft
        return out
    finally:
        _cleanup_holder(out.get("_holder_pid"), out.get("_holder_ft"))
        _cleanup_proc(proc)


def run_p1b(emulator_cls, fake: Path, tmp: Path) -> dict:
    """no-ready-child：构造超时 + root 存活 + holder 持管道 → 失败清理与 owner。"""
    pid_file = tmp / "holder.pid"
    os.environ["FAKE_SIDECAR_MODE"] = "no-ready-child"
    os.environ["FAKE_CHILD_PID_FILE"] = str(pid_file)
    captured: list = []
    original_popen = subprocess.Popen

    def spy_popen(*args, **kwargs):
        proc = original_popen(*args, **kwargs)
        captured.append(proc)
        return proc

    outcome: dict = {}
    module = sys.modules[emulator_cls.__module__]
    emulator_subprocess = getattr(module, "subprocess", subprocess)
    emulator_subprocess.Popen = spy_popen
    try:
        def ctor():
            try:
                emulator_cls(sidecar_path=str(fake), startup_timeout=2.0)
            except Exception as exc:  # noqa: BLE001
                outcome["exc"] = exc

        thread = threading.Thread(target=ctor, daemon=True)
        t0 = time.monotonic()
        thread.start()
        thread.join(timeout=15.0)
        blocked = thread.is_alive()
        out: dict = {"blocked_beyond_budget": blocked}
        if blocked:
            for proc in captured:
                _cleanup_proc(proc)
            if pid_file.exists():
                try:
                    holder_pid = int(pid_file.read_text(encoding="utf-8").strip())
                    handle, probe = _probe_retained(holder_pid)
                    ft = probe.identity.created_at_filetime if probe.identity else None
                    _cleanup_holder(holder_pid, int(ft) if ft else None)
                    identity_module.close_handle_checked(handle)
                except Exception:  # noqa: BLE001
                    pass
            thread.join(timeout=15.0)
        out["total_sec"] = round(time.monotonic() - t0, 3)
        exc = outcome.get("exc")
        out["exception"] = f"{type(exc).__name__}" if exc else None
        out["owner_present"] = exc is not None and getattr(exc, "owner", None) is not None
        out["residual_present"] = exc is not None and isinstance(
            getattr(exc, "residual", None), dict
        )
        owner = getattr(exc, "owner", None) if exc is not None else None
        if owner is not None and hasattr(owner, "retry_cleanup"):
            retry = owner.retry_cleanup(timeout=10.0)
            out["retry_cleanup_closed"] = retry.get("closed")
        for proc in captured:
            out.setdefault("root_dead", True)
            out["root_dead"] = out["root_dead"] and (
                _same_handle_dead(proc) or proc.poll() is not None
            )
        return out
    finally:
        emulator_subprocess.Popen = original_popen
        os.environ.pop("FAKE_SIDECAR_MODE", None)
        os.environ.pop("FAKE_CHILD_PID_FILE", None)
        if pid_file.exists():
            try:
                holder_pid = int(pid_file.read_text(encoding="utf-8").strip())
                handle, probe = _probe_retained(holder_pid)
                ft = probe.identity.created_at_filetime if probe.identity else None
                _cleanup_holder(holder_pid, int(ft) if ft else None)
                identity_module.close_handle_checked(handle)
            except Exception:  # noqa: BLE001
                pass


def main() -> int:
    import shutil as _shutil

    tmp_root = Path(tempfile.mkdtemp(prefix="pan-r2-repro-"))
    old_tmp: Path | None = None
    try:
        fake = tmp_root / "fake_sidecar.cjs"
        fake.write_text(_extract_fake_source().strip() + "\n", encoding="utf-8")

        old_mod, old_tmp = _load_old_emulator()
        from packages.core.terminal.emulator import HeadlessEmulator as NewEmulator

        # 旧实现 P11
        sys.stderr.write("[1/4] old impl P11 (expect blocked beyond budget)\n")
        RESULTS["old"]["p11"] = run_p11(old_mod.HeadlessEmulator, fake, tmp_root)
        RESULTS["old"]["p11"].pop("_holder_pid", None)
        RESULTS["old"]["p11"].pop("_holder_ft", None)

        # 旧实现 P1b
        sys.stderr.write("[2/4] old impl P1b startup (expect blocked / no owner)\n")
        RESULTS["old"]["p1b"] = run_p1b(old_mod.HeadlessEmulator, fake, tmp_root)

        # 新实现 P11
        sys.stderr.write("[3/4] new impl P11 (expect bounded + verified)\n")
        RESULTS["new"]["p11"] = run_p11(NewEmulator, fake, tmp_root)
        RESULTS["new"]["p11"].pop("_holder_pid", None)
        RESULTS["new"]["p11"].pop("_holder_ft", None)

        # 新实现 P1b
        sys.stderr.write("[4/4] new impl P1b startup (expect bounded + owner retry)\n")
        RESULTS["new"]["p1b"] = run_p1b(NewEmulator, fake, tmp_root)
    finally:
        for path in (old_tmp, tmp_root):
            if path is not None:
                _shutil.rmtree(path, ignore_errors=True)

    ok = (
        RESULTS["old"]["p11"].get("blocked_beyond_budget") is True
        and RESULTS["old"]["p1b"].get("blocked_beyond_budget") is True
        and RESULTS["new"]["p11"].get("blocked_beyond_budget") is False
        and RESULTS["new"]["p11"].get("closed") is True
        and RESULTS["new"]["p11"].get("job_verified") is True
        and RESULTS["new"]["p1b"].get("blocked_beyond_budget") is False
        and RESULTS["new"]["p1b"].get("owner_present") is True
        and RESULTS["new"]["p1b"].get("retry_cleanup_closed") is True
    )
    payload = {"ok": ok, "old_commit": OLD_COMMIT, "results": RESULTS}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
