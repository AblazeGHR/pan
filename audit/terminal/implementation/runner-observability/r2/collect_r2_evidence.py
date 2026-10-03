"""F5 r2 收尾证据收集器（47 项 = 43 + 4 新门控；直连 + uv 各一次）。

用法（树根）::

    E:/software/miniforge/python.exe audit/terminal/implementation/runner-observability/r2/collect_r2_evidence.py

产物（本目录，UTF-8）：

- ``evidence/pre_fix_r2_tests.txt``（已存在：e822 上 4 项新门控失败原文）；
- ``r2_direct.txt`` / ``r2_uv.txt``：修复后 47 项直连与 uv 隔离各一次；
- ``evidence/direct/`` / ``evidence/uv/``：每用例 JSON；
- ``uv_env.json`` / ``source_blobs.json`` / ``summary.json``（含残留扫描）。

按 MA 口径：不重复 11 组合/48/42/core/全库/长稳；旧证据（上层 evidence/）零覆盖。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
R2_DIR = HERE.parent
AUDIT_DIR = R2_DIR.parent
REPO_ROOT = AUDIT_DIR
while not (REPO_ROOT / ".git").exists():
    REPO_ROOT = REPO_ROOT.parent
RUNNER_TESTS = "tests/test_terminal_runner.py"
ANCHOR_FILES = [
    "packages/core/terminal/runner.py",
    "tests/test_terminal_runner.py",
    "docs/design/PAN_TERMINAL_RUNNER_INTERFACES_20261003.md",
    "audit/terminal/implementation/runner-observability/r2/README.md",
]
UV_BASE = [
    "uv", "run", "--no-project", "--python", sys.executable,
    "--with-requirements", "minimal-requirements.txt",
    "--with", "pytest", "--with", "pytest-timeout",
]


def _run(argv: list[str], *, timeout: float, env: dict | None = None) -> dict:
    started = time.monotonic()
    completed = subprocess.run(
        argv, cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=timeout
    )
    return {
        "argv": argv,
        "returncode": completed.returncode,
        "seconds": round(time.monotonic() - started, 2),
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=30
    ).stdout.strip()


def _scan_leftovers() -> dict:
    script = (
        "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "
        "'*packages.core.terminal.runner*' -or $_.CommandLine -like "
        "'*test_terminal_runner*' } | Select-Object ProcessId,Name | ConvertTo-Json -Compress"
    )
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=30,
        )
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "error": type(exc).__name__}
    if completed.returncode != 0:
        return {"available": False, "returncode": completed.returncode}
    text = completed.stdout.strip()
    if not text:
        return {"available": True, "count": 0, "processes": []}
    parsed = json.loads(text)
    processes = parsed if isinstance(parsed, list) else [parsed]
    runners = [
        item for item in processes
        if isinstance(item, dict) and "python" in str(item.get("Name", "")).lower()
    ]
    return {"available": True, "count": len(runners), "processes": runners}


def _staged_blobs() -> dict:
    ls = subprocess.run(
        ["git", "ls-files", "-s", *ANCHOR_FILES],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=30,
    )
    blobs: dict[str, Any] = {}
    for line in ls.stdout.strip().splitlines():
        meta, path = line.split("\t", 1)
        parts = meta.split()
        blobs[path] = {"blob": parts[1], "mode": parts[0]}
    return blobs


def main() -> int:
    (R2_DIR / "evidence").mkdir(parents=True, exist_ok=True)
    summary: dict = {
        "collected_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "python": sys.version,
        "executable": sys.executable,
        "git_head": _git("rev-parse", "HEAD"),
        "pre_fix_evidence": (R2_DIR / "evidence/pre_fix_r2_tests.txt").is_file(),
        "steps": {},
    }

    env = dict(os.environ)
    env["PAN_TERMINAL_RUNNER_EVIDENCE_DIR"] = str(R2_DIR / "evidence" / "direct")
    direct = _run([sys.executable, "-m", "pytest", RUNNER_TESTS, "-q", "-rA"], timeout=3600, env=env)
    (R2_DIR / "r2_direct.txt").write_text(
        direct["stdout"] + ("\n[stderr]\n" + direct["stderr"] if direct["stderr"] else ""),
        encoding="utf-8",
    )
    summary["steps"]["pytest_direct"] = {
        "returncode": direct["returncode"],
        "seconds": direct["seconds"],
        "argv": direct["argv"],
        "tail": "\n".join(direct["stdout"].strip().splitlines()[-2:]),
    }

    env_uv = dict(os.environ)
    env_uv["PAN_TERMINAL_RUNNER_EVIDENCE_DIR"] = str(R2_DIR / "evidence" / "uv")
    uv_run = _run(
        UV_BASE + ["--", "python", "-m", "pytest", RUNNER_TESTS, "-q", "-rA"],
        timeout=3600, env=env_uv,
    )
    (R2_DIR / "r2_uv.txt").write_text(
        uv_run["stdout"] + ("\n[stderr]\n" + uv_run["stderr"] if uv_run["stderr"] else ""),
        encoding="utf-8",
    )
    summary["steps"]["pytest_uv"] = {
        "returncode": uv_run["returncode"],
        "seconds": uv_run["seconds"],
        "argv": uv_run["argv"],
        "tail": "\n".join(uv_run["stdout"].strip().splitlines()[-2:]),
    }

    uv_version = subprocess.run(["uv", "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
    (R2_DIR / "uv_env.json").write_text(
        json.dumps(
            {
                "uv_version": uv_version,
                "pytest_direct_argv": direct["argv"],
                "pytest_uv_argv": uv_run["argv"],
                "note": "47 项 = 43 + 4 新门控；不重复 11 组合/48/42/core/全库/长稳",
            },
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    (R2_DIR / "source_blobs.json").write_text(
        json.dumps(
            {
                "parent_head": summary["git_head"],
                "anchor_kind": "git staged blob id（与本次提交内容逐字节一致；staged 前为旧提交 blob）",
                "blobs": _staged_blobs(),
            },
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    summary["leftover_processes"] = _scan_leftovers()
    (R2_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0 if direct["returncode"] == 0 and uv_run["returncode"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
