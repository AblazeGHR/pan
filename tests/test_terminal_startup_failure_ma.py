"""终端引擎启动失败的**分类**回归 + 真实引擎开关冒烟。

现场故障链（修复前）：

    sidecar 依赖缺失 → HeadlessEmulator 构造失败（node 退出 ERR_MODULE_NOT_FOUND）
    → launcher 写 engine-startup-failed / exit 8 后立即退出
    → service 仍在 ``wait_for_bootstrap_identity`` 里空等到 ``startup_timeout``(30s)
    → 超时后按 ``bootstrap-unverified`` 记 ``cleanup-failed``
    → UI 把"从未启动"显示成 "clean up failed"，记录既不可删除也不可收敛。

本文件钉死修复后的口径：

1. 依赖缺失在 **spawn 前**就给出静态分类 ``sidecar-dependency-missing``；
2. launcher 的状态文件保留该静态类（不落自由文本）；
3. service 在派生句柄退出后**立即**分类，不空等预算；只有"引擎未创建且启动
   owner 已收敛"才记终态 ``exited``（无清理债），其余仍是 ``cleanup-failed``；
4. 真实引擎在 sidecar 依赖存在时可 create → echo → close，重复 3 次无残留。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from packages.core.terminal import win_pipe
from packages.core.terminal.contracts import ProcessStatus, RuntimeState
from packages.core.terminal.emulator import (
    EmulatorStartupError,
    HeadlessEmulator,
    _default_sidecar_path,
    _missing_sidecar_dependencies,
    _sidecar_dependencies,
)
from packages.core.terminal.launcher import TerminalLauncher
from packages.core.terminal.service import StartupFailed, TerminalService

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="终端生产路径依赖 Windows ConPTY / Job Object / DPAPI / 命名管道",
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SIDECAR_DIR = REPO_ROOT / "packages/core/terminal/emulator_sidecar"


def _sidecar_deps_present() -> bool:
    return (SIDECAR_DIR / "node_modules/@xterm/headless/package.json").is_file()


requires_sidecar = pytest.mark.skipif(
    not _sidecar_deps_present(),
    reason="sidecar 依赖未安装（需在 emulator_sidecar/ 执行 npm ci）",
)


# ══════════════════════════════════════════════════════════════════════════
# ① 依赖预检（不 spawn、不联网）
# ══════════════════════════════════════════════════════════════════════════


def test_dependency_check_follows_node_modules_lookup(tmp_path):
    """bare / scoped 包都按 Node 的 node_modules 逐级查找，不误报父目录安装。"""
    sidecar = tmp_path / "sidecar"
    sidecar.mkdir()
    (sidecar / "package.json").write_text(
        json.dumps({"dependencies": {"@xterm/headless": "6.0.0", "left-pad": "1.0.0"}}),
        encoding="utf-8",
    )
    assert _sidecar_dependencies(sidecar) == ("@xterm/headless", "left-pad")
    assert _missing_sidecar_dependencies(sidecar) == ("@xterm/headless", "left-pad")

    # 同级安装：两个都已就位
    for name in ("@xterm/headless", "left-pad"):
        target = sidecar / "node_modules" / Path(*name.split("/"))
        target.mkdir(parents=True)
        (target / "package.json").write_text("{}", encoding="utf-8")
    assert _missing_sidecar_dependencies(sidecar) == ()

    # 父目录 node_modules 也算可解析（Node 的向上查找）
    parent_install = tmp_path / "node_modules" / "@xterm" / "headless"
    parent_install.mkdir(parents=True)
    (parent_install / "package.json").write_text("{}", encoding="utf-8")
    (sidecar / "node_modules" / "@xterm" / "headless" / "package.json").unlink()
    assert _missing_sidecar_dependencies(sidecar) == ()


def test_missing_sidecar_dependency_fails_before_any_process(tmp_path):
    """依赖缺失 → spawn 前静态分类，不产生 pid/owner（什么也没有创建）。"""
    sidecar = tmp_path / "sidecar"
    sidecar.mkdir()
    (sidecar / "sidecar.mjs").write_text("// stub\n", encoding="utf-8")
    (sidecar / "package.json").write_text(
        json.dumps({"dependencies": {"@xterm/headless": "6.0.0"}}), encoding="utf-8"
    )
    with pytest.raises(EmulatorStartupError) as info:
        HeadlessEmulator(cols=80, rows=24, sidecar_path=sidecar / "sidecar.mjs")
    exc = info.value
    assert exc.residual.get("reason") == "sidecar-dependency-missing"
    assert exc.residual.get("pid") is None, "预检失败不得声称已经 spawn 过 sidecar"
    assert exc.owner is None, "没有进程就没有可重试 owner"


def test_default_sidecar_manifest_matches_expected_pins():
    """默认 sidecar 的依赖名来自它自己的 package.json（不靠硬编码漂移）。"""
    assert set(_sidecar_dependencies(_default_sidecar_path().parent)) == {
        "@xterm/headless",
        "@xterm/addon-serialize",
    }


# ══════════════════════════════════════════════════════════════════════════
# ② launcher 状态文件保留静态分类
# ══════════════════════════════════════════════════════════════════════════


def test_launcher_status_preserves_dependency_classification(tmp_path):
    def fail_factory():
        raise EmulatorStartupError(
            "sidecar 依赖未安装：@xterm/headless",
            owner=None,
            residual={"reason": "sidecar-dependency-missing"},
        )

    launcher = TerminalLauncher(
        "term_dep",
        tmp_path / "secrets" / "term_dep.secret",
        status_dir=tmp_path / "status",
        emulator_factory=fail_factory,
    )
    assert launcher.run() == 8
    status = json.loads((tmp_path / "status" / "term_dep.json").read_text(encoding="utf-8"))
    assert status["phase"] == "engine-startup-failed"
    assert status["exit_code"] == 8
    assert status["engine"]["created"] is False
    startup = status["engine"]["startup"]
    assert startup["converged"] is True and startup["owner"] == "none"
    assert startup["residual"]["reason_class"] == "sidecar-dependency-missing"
    # 自由文本不落盘：只保留存在性 + 静态分类
    assert status["reason"] == "engine-startup-failed"


# ══════════════════════════════════════════════════════════════════════════
# ③ service：bootstrap 前退出 → 立即分类（四条分支）
# ══════════════════════════════════════════════════════════════════════════

_STUB = r"""
import json, pathlib, sys
status_dir, terminal_id, payload, code = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
if payload:
    status_dir = pathlib.Path(status_dir)
    status_dir.mkdir(parents=True, exist_ok=True)
    data = json.loads(payload)
    data["terminal_id"] = terminal_id
    (status_dir / (terminal_id + ".json")).write_text(json.dumps(data), encoding="utf-8")
sys.exit(code)
"""


def _stub_spawn(root: Path, *, payload: dict[str, Any] | None, code: int):
    def spawn(terminal_id, *, secret_file, cwd, shell_argv, root):
        return subprocess.Popen(
            [sys.executable, "-c", _STUB, str(root / "launcher-status"), terminal_id,
             json.dumps(payload) if payload else "", str(int(code))],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    return spawn


def _startup_failure_status(reason_class: str, *, code: int = 8) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "phase": "engine-startup-failed",
        "exit_code": code,
        "reason": "engine-startup-failed",
        "engine": {
            "created": False,
            "cleanup": {"converged": False, "outcome": "not-started"},
            "startup": {
                "converged": True, "owner": "none", "attempts": 0,
                "residual": {"reason_class": reason_class},
            },
        },
    }


def test_service_fails_fast_and_keeps_no_cleanup_debt(tmp_path):
    """引擎从未创建 + 启动 owner 收敛 → 立即 StartupFailed，记录可删除。"""
    service = TerminalService(
        tmp_path / "terminals",
        spawn_launcher=_stub_spawn(
            tmp_path, payload=_startup_failure_status("sidecar-dependency-missing"), code=8
        ),
        startup_timeout=30.0,
    )
    started = time.monotonic()
    with pytest.raises(StartupFailed) as info:
        service.create(rows=24, cols=80)
    elapsed = time.monotonic() - started
    assert elapsed < 5.0, f"launcher 已退出，不应空等 startup_timeout：{elapsed:.2f}s"
    assert info.value.reason == "sidecar-dependency-missing"

    record = service.registry.list()[-1]
    assert record.status is RuntimeState.EXITED
    assert record.cleanup_pending is False, "没有创建过资源，不得留下清理债"
    assert record.exit_code == 8
    assert record.exit_reason == "sidecar-dependency-missing"
    assert service._store().exists(record.terminal_id) is False
    assert service.remove(record.terminal_id)["removed"] is True


def test_service_keeps_cleanup_failed_when_engine_cleanup_unproven(tmp_path):
    """启动 owner 未收敛（exit 6）→ 不得降级成成功，仍是 cleanup-failed。"""
    payload = {
        "schema_version": 1,
        "phase": "cleanup-unproven",
        "exit_code": 6,
        "reason": "engine-startup-cleanup-unproven",
        "engine": {
            "created": False,
            "cleanup": {"converged": False},
            "startup": {"converged": False, "residual": {"reason_class": "engine-startup-failed"}},
        },
    }
    service = TerminalService(
        tmp_path / "terminals",
        spawn_launcher=_stub_spawn(tmp_path, payload=payload, code=6),
        startup_timeout=30.0,
    )
    started = time.monotonic()
    with pytest.raises(StartupFailed) as info:
        service.create()
    assert time.monotonic() - started < 5.0
    assert info.value.reason == "bootstrap-unverified"
    record = service.registry.list()[-1]
    assert record.status is RuntimeState.CLEANUP_FAILED
    assert record.exit_reason == "bootstrap-unverified"


def test_service_keeps_cleanup_failed_when_launcher_dies_without_status(tmp_path):
    """句柄非零退出但没有状态文件 → 无法证明"没有资源"，保持 cleanup-failed。"""
    service = TerminalService(
        tmp_path / "terminals",
        spawn_launcher=_stub_spawn(tmp_path, payload=None, code=9),
        startup_timeout=30.0,
    )
    started = time.monotonic()
    with pytest.raises(StartupFailed) as info:
        service.create()
    assert time.monotonic() - started < 5.0, "已退出的句柄不应继续空等"
    assert info.value.reason == "bootstrap-unverified"
    assert service.registry.list()[-1].status is RuntimeState.CLEANUP_FAILED


def test_service_keeps_waiting_when_shim_exits_zero_without_status(tmp_path):
    """零退出且无状态文件可能是 Python shim 先退出（真 launcher 仍在）→ 不抢跑。"""
    service = TerminalService(
        tmp_path / "terminals",
        spawn_launcher=_stub_spawn(tmp_path, payload=None, code=0),
        startup_timeout=1.0,
    )
    started = time.monotonic()
    with pytest.raises(StartupFailed) as info:
        service.create()
    elapsed = time.monotonic() - started
    assert elapsed >= 1.0, f"零退出无状态时必须保持等待到预算：{elapsed:.2f}s"
    assert info.value.reason == "bootstrap-unverified"
    assert info.value.error_type == "SecretNotFoundError"
    assert service.registry.list()[-1].status is RuntimeState.CLEANUP_FAILED


# ══════════════════════════════════════════════════════════════════════════
# ④ 真实引擎冒烟：启动 / 执行命令 / 关闭 / 重复 3 次 / 无残留
# ══════════════════════════════════════════════════════════════════════════


def _read_until(service: TerminalService, terminal_id: str, needle: bytes, *, timeout: float) -> bool:
    cursor = 0
    deadline = time.monotonic() + float(timeout)
    while time.monotonic() < deadline:
        try:
            page = service.read(terminal_id, cursor)
        except Exception:  # noqa: BLE001 - 启动窗口内的读失败重试
            time.sleep(0.1)
            continue
        data = page.get("data") or b""
        if needle in data:
            return True
        cursor = int(page.get("next_cursor") or cursor)
        time.sleep(0.05)
    return False


def _process_dead(pid: int, filetime: int | None) -> bool:
    probe = win_pipe.probe_process(int(pid))
    if probe.status is ProcessStatus.DEAD:
        return True
    if probe.status is ProcessStatus.ALIVE and filetime is not None and probe.identity is not None:
        return int(probe.identity.created_at_filetime or -1) != int(filetime)
    return False


def _node_children(pid: int) -> dict[int, float]:
    """launcher 直系 node 子进程 → create_time（同 pid 复用不算残留）。"""
    try:
        import psutil
    except ImportError:  # pragma: no cover
        return {}
    found: dict[int, float] = {}
    for proc in psutil.process_iter(["pid", "name", "ppid", "create_time"]):
        try:
            if "node" in (proc.info.get("name") or "").lower() and int(proc.info.get("ppid") or -1) == pid:
                found[int(proc.info["pid"])] = float(proc.info["create_time"])
        except Exception:  # noqa: BLE001
            continue
    return found


def _same_process_alive(pid: int, created: float) -> bool:
    """pid 仍存在且创建时间一致才算残留（区分退出/pid 复用）。"""
    try:
        import psutil
    except ImportError:  # pragma: no cover
        return False
    try:
        process = psutil.Process(int(pid))
        return abs(float(process.create_time()) - float(created)) < 1.0
    except Exception:  # noqa: BLE001 - 进程已消失即不残留
        return False


@requires_sidecar
def test_real_terminal_start_echo_close_three_times(tmp_path):
    """真实生产路径：create → echo 看到输出 → close 收敛；重复 3 次无残留。"""
    service = TerminalService(tmp_path / "terminals", log_stderr=False)
    try:
        for round_index in range(3):
            view = service.create(rows=24, cols=80)
            terminal_id = view["terminal_id"]
            assert view["status"] == RuntimeState.RUNNING.value
            pid = int(view["pid"])
            filetime = int(view["process_created_at_filetime"])
            sidecars = _node_children(pid)
            assert sidecars, f"第 {round_index + 1} 次：引擎 sidecar 应是 launcher 直系子进程"

            token = service.attach(terminal_id, f"browser-{round_index}", role="control")
            marker = f"PAN-TERM-{round_index}-{os.urandom(3).hex()}"
            service.input(terminal_id, token, f"echo {marker}\r".encode("utf-8"))
            assert _read_until(service, terminal_id, marker.encode("utf-8"), timeout=25.0), (
                f"第 {round_index + 1} 次：终端输出未命中 {marker}"
            )

            service.close(terminal_id)
            record = service.get(terminal_id)
            assert record["status"] == RuntimeState.EXITED.value, record
            assert service._store().exists(terminal_id) is False, "已证明终止后应删秘密"
            assert _process_dead(pid, filetime), "launcher/runner 应已退出"
            deadline = time.monotonic() + 12.0
            alive = {pid: created for pid, created in sidecars.items()
                     if _same_process_alive(pid, created)}
            while alive and time.monotonic() < deadline:
                time.sleep(0.1)
                alive = {pid: created for pid, created in alive.items()
                         if _same_process_alive(pid, created)}
            assert not alive, f"第 {round_index + 1} 次：sidecar 残留 {sorted(alive)}"
    finally:
        service.shutdown(budget=20.0)
