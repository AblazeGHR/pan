"""r2 返工证据收集器（d48 先失败 + 修复后直连/uv 双复跑 + blob 锚定 + 残留扫描）。

用法（仓库根）::

    E:/software/miniforge/python.exe audit/terminal/implementation/runner/collect_r2_evidence.py

产物（``audit/terminal/implementation/runner/r2/``，全部 UTF-8）：

- ``pre_fix_gates.json``：d48 固定副本（git archive，只读）上的 9 项门控先失败证据；
- ``pytest_direct.txt`` / ``evidence_direct/``：直连 31 项全量 + 每用例 JSON；
- ``pytest_uv.txt`` / ``evidence_uv/``：uv 隔离环境 31 项全量；
- ``core137_uv_pytest.txt``：core129+broadcast8 + pyte 的 uv 隔离单列（不混合计数）；
- ``source_blobs.json``：runner.py / runner_client.py / 测试 / 文档 / 本文档的
  ``git hash-object``（工作树内容锚定）；
- ``summary.json``：环境、退出码、耗时、清理汇总、残留进程扫描。

只读查询与自建资源清理；不杀非自建进程、不改其他树。
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
R2_DIR = AUDIT_DIR / "r2"
RUNNER_TESTS = "tests/test_terminal_runner.py"
CORE_FILES = [
    "tests/test_terminal_driver.py",
    "tests/test_terminal_output.py",
    "tests/test_terminal_runtime.py",
    "tests/test_terminal_registry.py",
    "tests/test_terminal_lease.py",
    "tests/test_terminal_ownership.py",
    "tests/test_terminal_broadcast.py",
]
ANCHOR_FILES = [
    "packages/core/terminal/runner.py",
    "packages/core/terminal/runner_client.py",
    "tests/test_terminal_runner.py",
    "docs/design/PAN_TERMINAL_RUNNER_INTERFACES_20261003.md",
    "audit/terminal/implementation/runner/README.md",
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


def _write(name: str, text: str) -> None:
    (R2_DIR / name).write_text(text, encoding="utf-8")


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=30
    ).stdout.strip()


def _scan_leftovers() -> dict:
    script = (
        "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "
        "'*packages.core.terminal.runner*' } | Select-Object ProcessId,Name |"
        " ConvertTo-Json -Compress"
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


def _aggregate_cleanup(evidence_dir: Path) -> dict:
    summary = {
        "tests_with_cleanup_evidence": 0,
        "handles": 0,
        "alive_before_cleanup": 0,
        "terminated": 0,
        "shell_traces": 0,
    }
    for path in sorted(evidence_dir.glob("cleanup_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        summary["tests_with_cleanup_evidence"] += 1
        for trace in (payload.get("handles") or {}).values():
            summary["handles"] += 1
            if trace.get("alive_before_cleanup"):
                summary["alive_before_cleanup"] += 1
            if (trace.get("runner") or {}).get("terminated") is True:
                summary["terminated"] += 1
            if trace.get("shell"):
                summary["shell_traces"] += 1
    return summary


def main() -> int:
    R2_DIR.mkdir(parents=True, exist_ok=True)
    summary: dict = {
        "collected_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "python": sys.version,
        "executable": sys.executable,
        "git_head": _git("rev-parse", "HEAD"),
        "steps": {},
    }

    # ① d48 先失败（只读 git archive 固定副本）
    pre = _run(
        [sys.executable, str(R2_DIR / "pre_fix_gates.py"), str(R2_DIR / "pre_fix_gates.json")],
        timeout=600,
    )
    summary["steps"]["pre_fix"] = {
        "returncode": pre["returncode"], "seconds": pre["seconds"],
    }

    # ② 直连全量（31 项）+ 每用例 JSON
    env = dict(os.environ)
    env["PAN_TERMINAL_RUNNER_EVIDENCE_DIR"] = str(R2_DIR / "evidence_direct")
    direct = _run(
        [sys.executable, "-m", "pytest", RUNNER_TESTS, "-q", "-rA"], timeout=3600, env=env
    )
    _write("pytest_direct.txt", direct["stdout"] + ("\n[stderr]\n" + direct["stderr"] if direct["stderr"] else ""))
    summary["steps"]["pytest_direct"] = {
        "returncode": direct["returncode"],
        "seconds": direct["seconds"],
        "argv": direct["argv"],
        "tail": "\n".join(direct["stdout"].strip().splitlines()[-2:]),
        "cleanup": _aggregate_cleanup(R2_DIR / "evidence_direct"),
    }

    # ③ uv 隔离全量（31 项）
    env_uv = dict(os.environ)
    env_uv["PAN_TERMINAL_RUNNER_EVIDENCE_DIR"] = str(R2_DIR / "evidence_uv")
    uv_run = _run(
        UV_BASE + ["--", "python", "-m", "pytest", RUNNER_TESTS, "-q", "-rA"],
        timeout=3600, env=env_uv,
    )
    _write("pytest_uv.txt", uv_run["stdout"] + ("\n[stderr]\n" + uv_run["stderr"] if uv_run["stderr"] else ""))
    summary["steps"]["pytest_uv"] = {
        "returncode": uv_run["returncode"],
        "seconds": uv_run["seconds"],
        "argv": uv_run["argv"],
        "tail": "\n".join(uv_run["stdout"].strip().splitlines()[-2:]),
    }

    # ④ core129+broadcast8 + pyte 的 uv 隔离（单列，不混合计数）
    uv_core = _run(
        UV_BASE + ["--with", "pyte==0.8.2", "--", "python", "-m", "pytest", *CORE_FILES, "-q"],
        timeout=3600,
    )
    _write("core137_uv_pytest.txt", uv_core["stdout"] + ("\n[stderr]\n" + uv_core["stderr"] if uv_core["stderr"] else ""))
    summary["steps"]["core137_uv"] = {
        "returncode": uv_core["returncode"],
        "seconds": uv_core["seconds"],
        "argv": uv_core["argv"],
        "tail": "\n".join(uv_core["stdout"].strip().splitlines()[-1:]),
        "files": CORE_FILES,
    }

    # ⑤ 源文件 blob 锚定 + 残留扫描
    blobs = {}
    for name in ANCHOR_FILES:
        path = REPO_ROOT / name
        blobs[name] = {
            "git_hash_object": _git("hash-object", name) if path.is_file() else None,
            "exists": path.is_file(),
        }
    (R2_DIR / "source_blobs.json").write_text(
        json.dumps({"git_head": summary["git_head"], "blobs": blobs}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    summary["leftover_processes"] = _scan_leftovers()
    (R2_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    ok = (
        pre["returncode"] == 0
        and direct["returncode"] == 0
        and uv_run["returncode"] == 0
        and uv_core["returncode"] == 0
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
