"""r3 窄修证据收集器（a704 先失败 + 修复后直连/uv 双复跑 + blob 锚定 + 残留扫描）。

用法（仓库根）::

    E:/software/miniforge/python.exe audit/terminal/implementation/runner/collect_r3_evidence.py

产物（``audit/terminal/implementation/runner/r3/``，全部 UTF-8）：

- ``pre_fix_gates.json``：a704 固定副本（git archive，只读）上的 N1/N2/N3/N4 先失败证据；
- ``pytest_direct.txt`` / ``evidence_direct/``：直连 38 项全量 + 每用例 JSON；
- ``pytest_uv.txt`` / ``evidence_uv/``：uv 隔离 38 项全量（不再跑 core129/8）；
- ``uv_env.json``：uv 版本与三条命令行（N/A 的 core 命令不列）；
- ``source_blobs.json``：runner.py / 测试 / 文档 / 本目录 README 的暂存 blob id 锚定；
- ``summary.json``：环境、退出码、耗时、清理汇总、残留扫描。

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
R3_DIR = AUDIT_DIR / "r3"
RUNNER_TESTS = "tests/test_terminal_runner.py"
ANCHOR_FILES = [
    "packages/core/terminal/runner.py",
    "packages/core/terminal/runner_client.py",
    "tests/test_terminal_runner.py",
    "docs/design/PAN_TERMINAL_RUNNER_INTERFACES_20261003.md",
    "audit/terminal/implementation/runner/r3/README.md",
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
    return summary


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
    R3_DIR.mkdir(parents=True, exist_ok=True)
    summary: dict = {
        "collected_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "python": sys.version,
        "executable": sys.executable,
        "git_head": _git("rev-parse", "HEAD"),
        "steps": {},
    }

    pre = _run(
        [sys.executable, str(R3_DIR / "pre_fix_gates.py"), str(R3_DIR / "pre_fix_gates.json")],
        timeout=600,
    )
    summary["steps"]["pre_fix"] = {"returncode": pre["returncode"], "seconds": pre["seconds"]}

    env = dict(os.environ)
    env["PAN_TERMINAL_RUNNER_EVIDENCE_DIR"] = str(R3_DIR / "evidence_direct")
    direct = _run([sys.executable, "-m", "pytest", RUNNER_TESTS, "-q", "-rA"], timeout=3600, env=env)
    (R3_DIR / "pytest_direct.txt").write_text(
        direct["stdout"] + ("\n[stderr]\n" + direct["stderr"] if direct["stderr"] else ""),
        encoding="utf-8",
    )
    summary["steps"]["pytest_direct"] = {
        "returncode": direct["returncode"],
        "seconds": direct["seconds"],
        "argv": direct["argv"],
        "tail": "\n".join(direct["stdout"].strip().splitlines()[-2:]),
        "cleanup": _aggregate_cleanup(R3_DIR / "evidence_direct"),
    }

    env_uv = dict(os.environ)
    env_uv["PAN_TERMINAL_RUNNER_EVIDENCE_DIR"] = str(R3_DIR / "evidence_uv")
    uv_run = _run(
        UV_BASE + ["--", "python", "-m", "pytest", RUNNER_TESTS, "-q", "-rA"],
        timeout=3600, env=env_uv,
    )
    (R3_DIR / "pytest_uv.txt").write_text(
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
    (R3_DIR / "uv_env.json").write_text(
        json.dumps(
            {
                "uv_version": uv_version,
                "pytest_direct_argv": direct["argv"],
                "pytest_uv_argv": uv_run["argv"],
                "note": "argv 由收集器实际执行值记录（source-anchored）；不再运行 core129/8",
            },
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )

    (R3_DIR / "source_blobs.json").write_text(
        json.dumps(
            {
                "parent_head": summary["git_head"],
                "anchor_kind": "git staged blob id（与本次提交内容逐字节一致）",
                "blobs": _staged_blobs(),
            },
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    summary["leftover_processes"] = _scan_leftovers()
    (R3_DIR / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    ok = pre["returncode"] == 0 and direct["returncode"] == 0 and uv_run["returncode"] == 0
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
