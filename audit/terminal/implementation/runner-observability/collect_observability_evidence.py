"""F5 观测性证据收集器（先失败已另行取证；本脚本跑修复后 43 项双环境 + 锚定 + 扫描）。

用法（树根）::

    E:/software/miniforge/python.exe audit/terminal/implementation/runner-observability/collect_observability_evidence.py

产物（本目录，全部 UTF-8）：

- ``evidence/pre_fix_f5_tests.txt``（已存在：旧代码上 5 项 F5 门控失败原文）；
- ``evidence/post_fix_direct.txt`` / ``evidence/post_fix_uv.txt``：修复后 43 项
  （38 原 + 5 F5）直连与 uv 隔离各一次；
- ``evidence/direct/`` / ``evidence/uv/``：每用例 JSON；
- ``uv_env.json`` / ``source_blobs.json`` / ``summary.json``（含残留扫描）。

按 MA 口径：不跑 11 组合/42/core/全库/浏览器/provider/长稳/账号网络服务。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
AUDIT_DIR = HERE.parent
REPO_ROOT = AUDIT_DIR
while not (REPO_ROOT / ".git").exists():
    REPO_ROOT = REPO_ROOT.parent
EVIDENCE_DIR = AUDIT_DIR / "evidence"
RUNNER_TESTS = "tests/test_terminal_runner.py"
ANCHOR_FILES = [
    "packages/core/terminal/runner.py",
    "tests/test_terminal_runner.py",
    "docs/design/PAN_TERMINAL_RUNNER_INTERFACES_20261003.md",
    "audit/terminal/implementation/runner-observability/README.md",
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
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    summary: dict = {
        "collected_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "python": sys.version,
        "executable": sys.executable,
        "git_head": _git("rev-parse", "HEAD"),
        "pre_fix_evidence": (EVIDENCE_DIR / "pre_fix_f5_tests.txt").is_file(),
        "steps": {},
    }

    env = dict(os.environ)
    env["PAN_TERMINAL_RUNNER_EVIDENCE_DIR"] = str(EVIDENCE_DIR / "direct")
    direct = _run([sys.executable, "-m", "pytest", RUNNER_TESTS, "-q", "-rA"], timeout=3600, env=env)
    (EVIDENCE_DIR / "post_fix_direct.txt").write_text(
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
    env_uv["PAN_TERMINAL_RUNNER_EVIDENCE_DIR"] = str(EVIDENCE_DIR / "uv")
    uv_run = _run(
        UV_BASE + ["--", "python", "-m", "pytest", RUNNER_TESTS, "-q", "-rA"],
        timeout=3600, env=env_uv,
    )
    (EVIDENCE_DIR / "post_fix_uv.txt").write_text(
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
    (AUDIT_DIR / "uv_env.json").write_text(
        json.dumps(
            {
                "uv_version": uv_version,
                "pytest_direct_argv": direct["argv"],
                "pytest_uv_argv": uv_run["argv"],
                "note": "43 项 = 38 原 + 5 F5；不跑 11 组合/42/core/全库",
            },
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    (AUDIT_DIR / "source_blobs.json").write_text(
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
    (AUDIT_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0 if direct["returncode"] == 0 and uv_run["returncode"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
