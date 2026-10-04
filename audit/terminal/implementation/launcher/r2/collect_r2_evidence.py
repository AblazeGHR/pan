"""Pan Terminal launcher r2 窄修证据收集器（审查 5681ef8e 的 F2–F7）。

跑：r2 后的 launcher 套件（tests/test_terminal_launcher.py）直连与 uv 隔离各一次
（46 项 = 17 原有 + 12 新增函数/29 新用例）。**不跑** 13 组合 / core / 全库 / 长稳
（MA 令：旧全量与相邻套件不重复）。

产物（UTF-8 .txt + JSON）：pytest_r2_{direct,uv}.txt、evidence_{direct,uv}/、
summary.json、uv_env.json。先失败证据与源证明见 evidence/（5c 固定只读副本）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]  # audit/terminal/implementation/launcher/r2 → 仓库根
BASE = ROOT / "audit/terminal/implementation/launcher/r2"
LAUNCHER_TESTS = "tests/test_terminal_launcher.py"

UV_BASE = [
    "uv", "run", "--no-project", "--python", sys.executable,
    "--with-requirements", "minimal-requirements.txt",
    "--with", "pytest", "--with", "pytest-timeout",
]


def _run(argv: list[str], *, env_extra: dict[str, str], timeout: float) -> dict:
    env = dict(os.environ)
    env.update(env_extra)
    started = time.monotonic()
    completed = subprocess.run(
        argv, cwd=str(ROOT), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=timeout,
    )
    return {
        "argv": argv,
        "returncode": int(completed.returncode),
        "seconds": round(time.monotonic() - started, 3),
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _write_log(name: str, run: dict) -> None:
    text = run["stdout"]
    if run["stderr"]:
        text += "\n[stderr]\n" + run["stderr"]
    (BASE / name).write_text(text, encoding="utf-8")


def _aggregate_cleanup(directory: Path) -> dict:
    sessions = 0
    alive_before = 0
    nonzero_exits: list[dict] = []
    leftovers: list[dict] = []
    tmp_leftovers: list[str] = []
    if not directory.is_dir():
        return {"sessions": 0}
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict) and path.name.endswith(".tmp"):
            tmp_leftovers.append(path.name)
        if not isinstance(data, dict) or "alive_before_cleanup" not in data:
            continue
        sessions += 1
        if data.get("alive_before_cleanup"):
            alive_before += 1
        exit_code = data.get("exit_code")
        if exit_code not in (0, None):
            nonzero_exits.append({"file": path.name, "exit_code": exit_code})
        if data.get("sidecar_leftover") or data.get("shell_alive"):
            leftovers.append({"file": path.name,
                              "sidecar_leftover": data.get("sidecar_leftover"),
                              "shell_alive": data.get("shell_alive")})
    return {
        "sessions": sessions,
        "alive_before_cleanup": alive_before,
        "nonzero_exit_sessions": nonzero_exits,
        "resource_leftovers": leftovers,
    }


def _scan_tmp_and_leftovers() -> dict:
    """只读扫描：残留进程 + status 目录里的 .tmp 残留（F4 口径）。"""
    try:
        import psutil
    except ImportError:
        processes = {"available": False}
    else:
        python_leftovers: list[int] = []
        node_leftovers: list[int] = []
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                name = (proc.info.get("name") or "").lower()
                cmdline = " ".join(str(part) for part in (proc.info.get("cmdline") or []))
                if "python" in name and (
                    "packages.core.terminal.launcher" in cmdline
                    or "packages.core.terminal.runner" in cmdline
                ):
                    python_leftovers.append(int(proc.info["pid"]))
                if "node" in name and "emulator_sidecar" in cmdline:
                    node_leftovers.append(int(proc.info["pid"]))
            except Exception:  # noqa: BLE001
                continue
        processes = {
            "available": True,
            "python_runner_leftovers": python_leftovers,
            "node_sidecar_leftovers": node_leftovers,
        }
    status_dir = ROOT / "audit/terminal/implementation/launcher/r2"
    tmp_files = sorted(p.name for p in status_dir.rglob("*.tmp")) if status_dir.is_dir() else []
    return {"processes": processes, "evidence_tmp_files": tmp_files}


def main() -> int:
    BASE.mkdir(parents=True, exist_ok=True)
    summary: dict = {
        "git_head": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(ROOT), capture_output=True, text=True
        ).stdout.strip(),
        "scope": "launcher 套件 r2（17 原有 + 29 新增用例）；不跑 13 组合/core/全库/长稳",
        "steps": {},
    }

    direct = _run(
        [sys.executable, "-m", "pytest", LAUNCHER_TESTS, "-q", "-o", "addopts=", "-rA"],
        env_extra={"PAN_TERMINAL_LAUNCHER_EVIDENCE_DIR": str(BASE / "evidence_direct")},
        timeout=1800,
    )
    _write_log("pytest_r2_direct.txt", direct)
    summary["steps"]["r2_direct"] = {
        "returncode": direct["returncode"],
        "seconds": direct["seconds"],
        "tail": "\n".join(direct["stdout"].strip().splitlines()[-2:]),
        "cleanup": _aggregate_cleanup(BASE / "evidence_direct"),
    }

    uv_run = _run(
        UV_BASE + ["--", "python", "-m", "pytest", LAUNCHER_TESTS, "-q", "-o", "addopts=", "-rA"],
        env_extra={"PAN_TERMINAL_LAUNCHER_EVIDENCE_DIR": str(BASE / "evidence_uv")},
        timeout=1800,
    )
    _write_log("pytest_r2_uv.txt", uv_run)
    summary["steps"]["r2_uv"] = {
        "returncode": uv_run["returncode"],
        "seconds": uv_run["seconds"],
        "tail": "\n".join(uv_run["stdout"].strip().splitlines()[-2:]),
        "cleanup": _aggregate_cleanup(BASE / "evidence_uv"),
    }

    summary["residual_scan"] = _scan_tmp_and_leftovers()
    (BASE / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    uv_version = subprocess.run(
        ["uv", "--version"], capture_output=True, text=True, timeout=60
    ).stdout.strip()
    (BASE / "uv_env.json").write_text(
        json.dumps(
            {
                "uv_version": uv_version,
                "r2_direct_argv": [sys.executable, "-m", "pytest", LAUNCHER_TESTS,
                                   "-q", "-o", "addopts=", "-rA"],
                "r2_uv_argv": UV_BASE + ["--", "python", "-m", "pytest", LAUNCHER_TESTS,
                                         "-q", "-o", "addopts=", "-rA"],
                "note": "argv 记录；日志见 pytest_r2_{direct,uv}.txt（单 q 含汇总行）",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=1)[:3000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
