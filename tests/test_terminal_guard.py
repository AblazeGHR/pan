"""Pan Terminal P1：Job Object 整树守卫（guard.py）真实内核验证。

覆盖：KILL_ON_JOB_CLOSE + 禁 breakaway 自证；assign/成员证明；**根死仍枚举
整个 Job**（不靠 psutil、不按 PID 扫描）；terminate_tree 先枚举 -> 
TerminateJobObject -> active==0 证明；resident 残留核对；句柄关闭后 fail-closed；
holder 进程被硬杀后 kill-on-close 收树（内核级，holder 无机会运行清理代码）。

全部为自建进程（ConPTY 子进程 / 树脚本 / holder 脚本），临时根在 pytest tmp_path；
有界等待；不接触任何既有服务。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from packages.core.terminal import identity
from packages.core.terminal.contracts import ProcessIdentity, ProcessStatus
from packages.core.terminal.guard import GuardQueryError, JobObjectGuard
from packages.core.terminal.spawn_win import spawn_conpty_suspended

PYTHON = sys.executable
REPO_ROOT = str(Path(__file__).resolve().parent.parent)

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="Job Object 仅 Windows"
)


# --------------------------------------------------------------------------- scripts
def write_grandchild(tmp_path: Path) -> str:
    return _write(
        tmp_path,
        "grandchild.py",
        "import time\n"
        "if __name__ == '__main__':\n"
        "    time.sleep(300)\n",
    )


def _write(tmp_path: Path, name: str, code: str) -> str:
    path = tmp_path / name
    path.write_text(code, encoding="utf-8")
    return str(path)


def write_tree_child(tmp_path: Path, grandchild: str) -> str:
    return _write(
        tmp_path,
        "tree_child.py",
        "import json, os, subprocess, sys, time\n"
        f"GRANDCHILD = r'{grandchild}'\n"
        "report = sys.argv[1]\n"
        "p = subprocess.Popen([sys.executable, GRANDCHILD])\n"
        "open(report, 'w').write(json.dumps({'child_pid': os.getpid(), 'grandchild_pid': p.pid}))\n"
        "time.sleep(300)\n",
    )


def wait_for_json(path: str, timeout: float = 12.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if os.path.exists(path):
            try:
                return json.loads(Path(path).read_text(encoding="utf-8"))
            except ValueError:
                pass
        time.sleep(0.05)
    raise AssertionError(f"报告文件未出现：{path}")


def dead_or_gone(pid: int, timeout: float = 8.0) -> bool:
    """进程已 signed 或对象已释放（PID 查无）。诊断用途。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        handle = identity.open_process_for_probe(pid)
        if not handle:
            return True
        try:
            if identity.wait_state(handle) is ProcessStatus.DEAD:
                return True
        finally:
            identity.close_handle_checked(handle)
        time.sleep(0.05)
    return False


def cleanup_attempt(attempt) -> None:
    """测试收尾：终止自建根（若仍活）-> guard 清扫 -> 分阶段释放（失败保留可重试）。"""
    try:
        if attempt.h_process and identity.wait_state(attempt.h_process) is not ProcessStatus.DEAD:
            attempt.terminate_raw()
            attempt.wait_dead(3.0)
    finally:
        attempt.cleanup()


# --------------------------------------------------------------------------- tests
def test_guard_flags_describe_and_breakaway_disabled():
    """Job 自证：KILL_ON_JOB_CLOSE 置位、breakaway 两个位均不置位。"""
    guard = JobObjectGuard()
    try:
        desc = guard.describe()
        assert desc["kind"] == "job-object"
        assert desc["os_level_guard"] is True
        assert desc["kill_on_close"] is True
        assert desc["breakaway_allowed"] is False
        flags = desc["limit_flags"]
        assert flags & 0x00002000  # KILL_ON_JOB_CLOSE
        assert not flags & 0x00000800  # BREAKAWAY_OK
        assert not flags & 0x00001000  # SILENT_BREAKAWAY_OK
        assert guard.active_processes() == 0
        assert guard.member_pids() == []
    finally:
        assert guard.close() is True


def test_membership_active_and_remaining(tmp_path):
    """原子 spawn 后：成员证明、active>=1、remaining 只报 Job 成员。"""
    guard = JobObjectGuard()
    attempt = spawn_conpty_suspended(
        [PYTHON, "-c", "import time; time.sleep(60)"],
        cwd=str(tmp_path),
        guard=guard,
    )
    try:
        assert attempt.evidence is not None and attempt.evidence.assigned is True
        members = guard.member_pids()
        assert attempt.pid in members
        assert (guard.active_processes() or 0) >= 1
        assert guard.is_member(attempt.h_process) is True
        # remaining 只报告 Job 成员：伪造 PID 不出现
        bogus = 99999999
        assert guard.remaining([attempt.pid, bogus]) == [attempt.pid]
    finally:
        cleanup_attempt(attempt)
        assert guard.close() is True


def test_root_death_still_enumerates_whole_job(tmp_path):
    """根死后仍枚举孙进程（Job 身份，不靠根 PID 扫描），terminate_tree 清整树。"""
    grandchild = write_grandchild(tmp_path)
    tree_child = write_tree_child(tmp_path, grandchild)
    report_path = str(tmp_path / "tree_report.json")
    guard = JobObjectGuard()
    attempt = spawn_conpty_suspended(
        [PYTHON, tree_child, report_path], cwd=str(tmp_path), guard=guard
    )
    try:
        report = wait_for_json(report_path)
        root_pid = report["child_pid"]
        grandchild_pid = report["grandchild_pid"]
        assert root_pid == attempt.pid
        assert grandchild_pid in guard.member_pids()

        # 只杀根（经 kill_verified：同 handle FILETIME 精确匹配）
        kill = identity.kill_verified(
            root_pid,
            ProcessIdentity(pid=root_pid, created_at_filetime=attempt.identity.created_at_filetime),
        )
        assert kill.killed is True
        assert dead_or_gone(root_pid, timeout=5.0) is True

        members_after_root_death = guard.member_pids()
        assert grandchild_pid in members_after_root_death, "根死后必须仍能枚举孙进程"
        assert root_pid not in members_after_root_death

        owned, remaining = guard.terminate_tree(root_pid, timeout=5.0)
        assert grandchild_pid in owned
        assert remaining == []
        assert (guard.active_processes() or 0) == 0
        assert dead_or_gone(grandchild_pid, timeout=5.0) is True
        assert guard.remaining(owned) == []
    finally:
        cleanup_attempt(attempt)
        assert guard.close() is True


def test_terminate_tree_idle_and_repeatable(tmp_path):
    """无孙树场景：terminate_tree 返回 (owned, []) 且 active=0；close 幂等。"""
    guard = JobObjectGuard()
    attempt = spawn_conpty_suspended(
        [PYTHON, "-c", "import time; time.sleep(60)"],
        cwd=str(tmp_path),
        guard=guard,
    )
    try:
        owned, remaining = guard.terminate_tree(attempt.pid, timeout=5.0)
        assert attempt.pid in owned
        assert remaining == []
        assert guard.active_processes() == 0
        assert attempt.wait_dead(5.0) is True
    finally:
        attempt.staged_release(require_dead=True)
        assert guard.close() is True
        assert guard.close() is True  # 幂等


def test_guard_query_fail_closed_after_close():
    """句柄关闭后查询 -> GuardQueryError（所有权未知，fail-closed）。"""
    guard = JobObjectGuard()
    assert guard.close() is True
    with pytest.raises(GuardQueryError):
        guard.member_pids()
    with pytest.raises(GuardQueryError):
        guard.owned_pids(1234)
    with pytest.raises(GuardQueryError):
        guard.remaining([1234])
    with pytest.raises(GuardQueryError):
        guard.terminate_tree(1234, timeout=0.1)
    assert guard.active_processes() is None


def test_holder_hard_kill_kills_tree(tmp_path):
    """holder 被硬杀（内核级 kill-on-close）：整树消失，holder 无清理代码机会。

    holder 复刻布局 B：出生即持有 ConPTY + 自持 guard；测试用 ``kill_verified``
    按 holder 的 pid+FILETIME 精确终止 holder 进程（仅自建进程）。
    """
    grandchild = write_grandchild(tmp_path)
    tree_child = write_tree_child(tmp_path, grandchild)
    holder_script = _write(
        tmp_path,
        "holder.py",
        "import json, os, sys, time\n"
        f"sys.path.insert(0, r'{REPO_ROOT}')\n"
        "from packages.core.terminal.backend import ConPtyBackend\n"
        "from packages.core.terminal import identity\n"
        "holder_report, child_report = sys.argv[1], sys.argv[2]\n"
        f"b = ConPtyBackend.spawn([sys.executable, r'{tree_child}', child_report], rows=30, cols=100)\n"
        "own_ft = identity.read_creation_filetime(identity.kernel32().GetCurrentProcess())\n"
        "deadline = time.time() + 10\n"
        "while time.time() < deadline and not os.path.exists(child_report):\n"
        "    time.sleep(0.05)\n"
        "tree = json.loads(open(child_report).read()) if os.path.exists(child_report) else None\n"
        "open(holder_report, 'w').write(json.dumps({'holder_pid': os.getpid(), 'holder_filetime': own_ft, 'tree': tree}))\n"
        "time.sleep(300)\n",
    )
    holder_report = str(tmp_path / "holder_report.json")
    child_report = str(tmp_path / "holder_child_report.json")
    holder = subprocess.Popen([PYTHON, holder_script, holder_report, child_report])
    try:
        report = wait_for_json(holder_report)
        holder_pid = report["holder_pid"]
        holder_ft = report["holder_filetime"]
        tree = report["tree"]
        assert tree, "holder 应已建立孙树"

        # 独立复核 holder 身份（同 handle FILETIME 精确相等）
        handle = identity.open_process_for_probe(holder_pid)
        assert handle
        try:
            probe = identity.probe_handle(handle, pid=holder_pid)
            assert probe.status is ProcessStatus.ALIVE
            assert probe.identity is not None
            assert probe.identity.created_at_filetime == holder_ft
        finally:
            identity.close_handle_checked(handle)

        for pid in (tree["child_pid"], tree["grandchild_pid"]):
            assert dead_or_gone(pid, timeout=1.0) is False, "硬杀前整树应在"

        kill = identity.kill_verified(
            holder_pid,
            ProcessIdentity(pid=holder_pid, created_at_filetime=holder_ft),
        )
        assert kill.killed is True, kill.as_dict()

        survivors_after = []
        for pid in (tree["child_pid"], tree["grandchild_pid"]):
            if not dead_or_gone(pid, timeout=8.0):
                survivors_after.append(pid)
        assert survivors_after == [], f"kill-on-close 未收干净：{survivors_after}"
        holder.wait(timeout=10)
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.wait(timeout=5)
