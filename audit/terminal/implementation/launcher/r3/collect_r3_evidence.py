"""Pan Terminal launcher r3 窄修证据收集器（ROUND2 审查 549f00bc 的 N1/N2）。

只跑**新增 + 紧邻 status/投影/公告定向集**（r3_ / r2_ / status / residual / startup /
close / illegal / sentinel / tmp 关键词），直连与 uv 隔离各一次。

**不跑**：46 全量里的 5 项真机跨进程用例（test_real_launcher_*，本轮与 N1/N2 无关）、
13 组合 / core / backend / IPC / emulator 全量 / 全库 / 浏览器 / provider / 长稳。

产物（UTF-8 .txt + JSON）：pytest_r3_{direct,uv}.txt、evidence_{direct,uv}/、
summary.json、uv_env.json。先失败证据与源证明见 evidence/（7552319b 只读副本）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]  # audit/terminal/implementation/launcher/r3 → 仓库根
BASE = ROOT / "audit/terminal/implementation/launcher/r3"
LAUNCHER_TESTS = "tests/test_terminal_launcher.py"
#: 定向集：新增 r3 + 紧邻 status/投影/公告相关（r2 全量 + r1 的相关项）。
TARGETED = "r3_ or r2_ or status or residual or startup or close or illegal or sentinel or tmp"

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


def _scan_leftovers() -> dict:
    try:
        import psutil
    except ImportError:
        return {"available": False}
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
    return {
        "available": True,
        "python_runner_leftovers": python_leftovers,
        "node_sidecar_leftovers": node_leftovers,
    }


def main() -> int:
    BASE.mkdir(parents=True, exist_ok=True)
    summary: dict = {
        "git_head": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(ROOT), capture_output=True, text=True
        ).stdout.strip(),
        "scope": "launcher 定向集（新增 r3 + 紧邻 status/投影/公告）；不含真机跨进程用例",
        "targeted_expression": TARGETED,
        "steps": {},
    }

    direct = _run(
        [sys.executable, "-m", "pytest", LAUNCHER_TESTS, "-q", "-o", "addopts=", "-k", TARGETED],
        env_extra={"PAN_TERMINAL_LAUNCHER_EVIDENCE_DIR": str(BASE / "evidence_direct")},
        timeout=900,
    )
    _write_log("pytest_r3_direct.txt", direct)
    summary["steps"]["r3_direct"] = {
        "returncode": direct["returncode"],
        "seconds": direct["seconds"],
        "tail": "\n".join(direct["stdout"].strip().splitlines()[-1:]),
    }

    uv_run = _run(
        UV_BASE + ["--", "python", "-m", "pytest", LAUNCHER_TESTS, "-q", "-o", "addopts=",
                   "-k", TARGETED],
        env_extra={"PAN_TERMINAL_LAUNCHER_EVIDENCE_DIR": str(BASE / "evidence_uv")},
        timeout=900,
    )
    _write_log("pytest_r3_uv.txt", uv_run)
    summary["steps"]["r3_uv"] = {
        "returncode": uv_run["returncode"],
        "seconds": uv_run["seconds"],
        "tail": "\n".join(uv_run["stdout"].strip().splitlines()[-1:]),
    }

    summary["leftover_processes"] = _scan_leftovers()
    summary["evidence_tmp_files"] = sorted(p.name for p in BASE.rglob("*.tmp"))
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
                "r3_direct_argv": [sys.executable, "-m", "pytest", LAUNCHER_TESTS,
                                   "-q", "-o", "addopts=", "-k", TARGETED],
                "r3_uv_argv": UV_BASE + ["--", "python", "-m", "pytest", LAUNCHER_TESTS,
                                         "-q", "-o", "addopts=", "-k", TARGETED],
                "note": "argv 记录；日志见 pytest_r3_{direct,uv}.txt（单 q 含汇总行）",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=1)[:2500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
