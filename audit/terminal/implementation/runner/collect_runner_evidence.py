"""runner 证据收集器（隔离真机重跑 + 汇总 + 残留/约束扫描）。

用法（仓库根）::

    E:/software/miniforge/python.exe audit/terminal/implementation/runner/collect_runner_evidence.py --label final

产物（全部写入 ``audit/terminal/implementation/runner/evidence/<label>/``）：

- ``pytest.log``：``tests/test_terminal_runner.py`` 全量输出（含退出码）；
- ``<test>.json``：测试写入的机器可读证据（由测试的 ``PAN_TERMINAL_RUNNER_EVIDENCE_DIR``）；
- ``ambient_constraint.json`` / ``dead_process_probe.json`` / ``response_whitelist.json``：三个只读复现探针；
- ``runner_runs.json``：环境、退出码、耗时、清理汇总、残留进程扫描；
- ``cleanup.json``：逐用例 cleanup 证据的应用性汇总。

本脚本只做只读查询与自建资源清理；**不杀任何非本脚本派生的进程**。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
AUDIT_DIR = Path(__file__).resolve().parent
RUNNER_TESTS = "tests/test_terminal_runner.py"
LEFT_OVER_MARKER = "packages.core.terminal.runner"


def _run(argv: list[str], *, timeout: float, cwd: Path = REPO_ROOT) -> dict:
    started = time.monotonic()
    completed = subprocess.run(
        argv, cwd=str(cwd), capture_output=True, text=True, timeout=timeout
    )
    return {
        "argv": argv,
        "returncode": completed.returncode,
        "seconds": round(time.monotonic() - started, 2),
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _git_head() -> str | None:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(REPO_ROOT),
            capture_output=True, text=True, timeout=15,
        )
        return completed.stdout.strip() or None
    except Exception:  # noqa: BLE001
        return None


def _scan_leftovers() -> dict:
    """只读扫描：命令行包含 runner 模块名的进程（不杀；期望 0）。"""
    script = (
        "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "
        f"'*{LEFT_OVER_MARKER}*' }} | Select-Object ProcessId,Name,CommandLine |"
        " ConvertTo-Json -Compress"
    )
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=30,
        )
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "error": type(exc).__name__}
    text = completed.stdout.strip()
    if completed.returncode != 0:
        return {"available": False, "returncode": completed.returncode}
    if not text:
        return {"available": True, "count": 0, "processes": []}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"available": True, "count": None, "raw": text[:400]}
    processes = parsed if isinstance(parsed, list) else [parsed]
    # 收集器自身的 PowerShell/查询进程可能命中；只统计 Python 进程。
    runners = [
        item
        for item in processes
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
            runner_trace = trace.get("runner") or {}
            if runner_trace.get("terminated") is True:
                summary["terminated"] += 1
            if trace.get("shell"):
                summary["shell_traces"] += 1
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="collect runner evidence")
    parser.add_argument("--label", default="final")
    args = parser.parse_args()
    evidence_dir = AUDIT_DIR / "evidence" / args.label
    evidence_dir.mkdir(parents=True, exist_ok=True)

    environment = {
        "collected_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "python": sys.version,
        "executable": sys.executable,
        "platform": sys.platform,
        "git_head": _git_head(),
        "label": args.label,
    }

    # ① 探针（只读复现：ambient 限制 / 死进程探针三态 / 响应白名单）
    probes: dict[str, dict] = {}
    probe_files = {
        "ambient_constraint": "probe_ambient_constraint.py",
        "dead_process_probe": "probe_dead_process_unknown.py",
        "response_whitelist": "probe_response_whitelist.py",
    }
    for name, filename in probe_files.items():
        result = _run(
            [sys.executable, str(AUDIT_DIR / "probes" / filename)],
            timeout=180,
        )
        (evidence_dir / f"{name}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        probes[name] = {
            "script": filename,
            "returncode": result["returncode"],
            "seconds": result["seconds"],
        }

    # ② 全量隔离真机测试（机器可读证据由测试直接写入 evidence_dir）
    env = dict(os.environ)
    env["PAN_TERMINAL_RUNNER_EVIDENCE_DIR"] = str(evidence_dir)
    started = time.monotonic()
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", RUNNER_TESTS, "-q", "-rA"],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=3600,
    )
    test_seconds = round(time.monotonic() - started, 2)
    (evidence_dir / "pytest.log").write_text(
        completed.stdout + ("\n[stderr]\n" + completed.stderr if completed.stderr else ""),
        encoding="utf-8",
    )

    # ③ 汇总
    report = {
        "environment": environment,
        "probes": probes,
        "pytest": {
            "returncode": completed.returncode,
            "seconds": test_seconds,
            "tail": "\n".join(completed.stdout.strip().splitlines()[-3:]),
        },
        "cleanup": _aggregate_cleanup(evidence_dir),
        "leftover_processes": _scan_leftovers(),
        "evidence_files": sorted(path.name for path in evidence_dir.iterdir()),
    }
    (evidence_dir / "runner_runs.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if completed.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
